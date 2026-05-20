# app_web/public/routes.py

import os
import json
import urllib.parse
import time
import hashlib
from threading import Lock

from flask import (
    render_template,
    abort,
    request,
    redirect,
    jsonify,
    make_response,
    session,
    url_for,
    current_app,
    g,
)
from flask_login import current_user
from sqlalchemy import func
from . import public_bp
from config_app import db

from utils.audit_logger import log_action
from utils.distributed_backplane import bp_get, bp_set
from utils.timezone import iso_utc_z, utc_now_naive

# Límite de paginación pública
PUBLIC_MAX_PAGE = 50
PUBLIC_LIVE_PING_MAX_BODY_BYTES = int(os.getenv("PUBLIC_LIVE_PING_MAX_BODY_BYTES", "4096"))
PUBLIC_LIVE_PING_PATH_MAX_LEN = int(os.getenv("PUBLIC_LIVE_PING_PATH_MAX_LEN", "180"))
PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN = int(os.getenv("PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN", "90"))
PUBLIC_LIVE_ALLOWED_EVENT_TYPES = {
    "heartbeat",
    "pageview",
    "cta_click",
    "form_start",
    "form_submit",
}
_PUBLIC_LIVE_RL_LOCK = Lock()
_PUBLIC_LIVE_RL_LOCAL: dict[str, tuple[int, float]] = {}


def _safe_page(value, default=1):
    """
    Convierte a int, fuerza mínimo 1 y máximo PUBLIC_MAX_PAGE.
    """
    try:
        page = int(value)
    except Exception:
        page = default
    if page < 1:
        page = 1
    if page > PUBLIC_MAX_PAGE:
        page = PUBLIC_MAX_PAGE
    return page


# 🔌 SWITCH GENERAL: WEB PÚBLICA HABILITADA / DESHABILITADA
# ❌ DESACTIVADA TEMPORALMENTE (no accesible al público)
PUBLIC_SITE_ENABLED = True
# Para reactivar en el futuro, cambia a True


def _json_no_cache(payload: dict, status: int = 200):
    """JSON response con headers anti-cache para refresco silencioso."""
    resp = make_response(jsonify(payload), status)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _public_client_ip() -> str:
    raw = (
        (request.headers.get("CF-Connecting-IP") or "").strip()
        or (request.headers.get("X-Real-IP") or "").strip()
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or (request.remote_addr or "").strip()
        or "0.0.0.0"
    )
    return raw[:64]


def _public_live_rate_limited(ip: str) -> bool:
    minute_key = utc_now_naive().strftime("%Y%m%d%H%M")
    key = f"public_live_rl:{minute_key}:{ip}"
    limit = max(10, int(PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN or 90))
    timeout = 75
    count = None
    try:
        count = int(bp_get(key, default=0, context="public_live_rl_get") or 0) + 1
        bp_set(key, count, timeout=timeout, context="public_live_rl_set")
    except Exception:
        count = None

    if count is None:
        now = time.time()
        with _PUBLIC_LIVE_RL_LOCK:
            val = _PUBLIC_LIVE_RL_LOCAL.get(key)
            if val and val[1] > now:
                c = int(val[0]) + 1
            else:
                c = 1
            _PUBLIC_LIVE_RL_LOCAL[key] = (c, now + float(timeout))
            count = c
    return int(count) > limit


def _public_external_url(endpoint: str, **values) -> str:
    base_raw = (
        (current_app.config.get("PUBLIC_BASE_URL") or "")
        or (os.getenv("PUBLIC_BASE_URL") or "")
        or "https://www.domesticadelcibao.com"
    ).strip()
    if base_raw:
        parsed = urllib.parse.urlparse(base_raw)
        if parsed.scheme and parsed.netloc:
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
            rel = url_for(endpoint, _external=False, **values).lstrip("/")
            return urllib.parse.urljoin(base.rstrip("/") + "/", rel)
    return url_for(endpoint, _external=True, **values)


def _catalogo_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _catalogo_public_alias(candidata, ficha_web=None) -> str:
    raw = (getattr(ficha_web, "nombre_publico", None) or "").strip()
    if raw:
        return raw
    raw = (getattr(candidata, "nombre_completo", None) or "").strip()
    if not raw:
        codigo = (getattr(candidata, "codigo", None) or "").strip()
        return f"Perfil {codigo}" if codigo else f"Perfil #{int(getattr(candidata, 'fila', 0) or 0)}"
    parts = [p for p in raw.split() if p]
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[1][0]}."


def _catalogo_safe_text(value, fallback="No especificado") -> str:
    if value is None:
        return fallback
    text_value = str(value).strip()
    return text_value if text_value else fallback


def _catalogo_public_payload(candidata, ficha_web=None):
    ciudad = _catalogo_safe_text(getattr(ficha_web, "ciudad_publica", None), fallback="").strip() if ficha_web else ""
    sector_general = _catalogo_safe_text(getattr(ficha_web, "sector_publico", None), fallback="").strip() if ficha_web else ""
    if not ciudad:
        ciudad = "Zona disponible bajo coordinacion"
    if not sector_general:
        sector_general = "Informacion general disponible con la agencia"

    modalidad = _catalogo_safe_text(
        getattr(ficha_web, "modalidad_publica", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not modalidad:
        modalidad = _catalogo_safe_text(getattr(candidata, "modalidad_trabajo_preferida", None), fallback="Por definir")

    experiencia = _catalogo_safe_text(
        getattr(ficha_web, "experiencia_resumen", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not experiencia:
        experiencia = _catalogo_safe_text(getattr(candidata, "anos_experiencia", None), fallback="")
    if not experiencia:
        experiencia = _catalogo_safe_text(getattr(candidata, "empleo_anterior", None), fallback="Experiencia no especificada")[:240]

    experiencia_detallada = _catalogo_safe_text(
        getattr(ficha_web, "experiencia_detallada", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not experiencia_detallada:
        experiencia_detallada = "Experiencia detallada no especificada."

    entrevista_publica = _catalogo_safe_text(
        getattr(ficha_web, "entrevista_publica_resumen", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not entrevista_publica:
        entrevista_publica = "Entrevista pública pendiente de preparar por la agencia."

    especialidades = _catalogo_safe_text(
        getattr(ficha_web, "tags_publicos", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not especialidades:
        especialidades = _catalogo_safe_text(getattr(candidata, "areas_experiencia", None), fallback="No especificadas")

    disponibilidad = "Disponible de inmediato" if bool(getattr(ficha_web, "disponible_inmediato", False)) else ""
    if not disponibilidad:
        disponibilidad = _catalogo_safe_text(getattr(candidata, "disponibilidad_inicio", None), fallback="A coordinar")
    sueldo_publico = _catalogo_safe_text(
        getattr(ficha_web, "sueldo_texto_publico", None) if ficha_web else None,
        fallback="",
    ).strip() or None
    estado_publico = _catalogo_safe_text(
        getattr(ficha_web, "estado_publico", None) if ficha_web else None,
        fallback="disponible",
    ).strip().lower()
    foto_publica = _catalogo_safe_text(
        getattr(ficha_web, "foto_publica_url", None) if ficha_web else None,
        fallback="",
    ).strip() or None
    verificacion = "Perfil evaluado por la agencia"
    return {
        "id": int(getattr(candidata, "fila", 0) or 0),
        "codigo": (getattr(candidata, "codigo", None) or "").strip() or None,
        "nombre_publico": _catalogo_public_alias(candidata, ficha_web=ficha_web),
        "edad": _catalogo_safe_text(
            getattr(ficha_web, "edad_publica", None) if ficha_web else getattr(candidata, "edad", None),
            fallback="No especificada",
        ),
        "ciudad": ciudad,
        "sector_general": sector_general,
        "modalidad": modalidad,
        "estado_publico": estado_publico,
        "sueldo_texto_publico": sueldo_publico,
        "experiencia_resumen": experiencia,
        "experiencia_detallada": experiencia_detallada,
        "entrevista_publica_resumen": entrevista_publica,
        "especialidades": especialidades,
        "foto_publica": foto_publica,
        "disponibilidad": disponibilidad,
        "verificacion_general": verificacion,
    }


def _resolver_catalogo_publico_por_token(token: str):
    from models import CatalogoPrivado

    token_hash = _catalogo_token_hash(token)
    catalogo = CatalogoPrivado.query.filter_by(token_hash=token_hash).first()
    if not catalogo:
        return None, "invalid"
    now = utc_now_naive()
    if not bool(catalogo.is_active):
        return catalogo, "expired"
    if catalogo.expires_at and catalogo.expires_at <= now:
        return catalogo, "expired"
    catalogo.last_seen_at = now
    db.session.commit()
    return catalogo, "ok"


def _resolve_share_state(code: str):
    from clientes import routes as clientes_routes

    alias = clientes_routes.resolve_public_share_alias(code)
    if alias is None:
        return None, "invalid", "invalid", None

    link_type = (getattr(alias, "link_type", "") or "").strip().lower()
    token = str(getattr(alias, "token", "") or "")
    token_hash = clientes_routes._public_link_token_hash_storage(token)

    if link_type == "existente":
        if clientes_routes._public_link_usage_by_hash(token_hash) is not None:
            return alias, "used", link_type, token
        cliente, fail_reason, _meta = clientes_routes._resolve_public_link_token(token)
        if cliente is not None:
            return alias, "ready", link_type, token
        if fail_reason == "expired":
            return alias, "expired", link_type, token
        return alias, "invalid", link_type, token

    if link_type == "nuevo":
        if clientes_routes._public_new_link_usage_by_hash(token_hash) is not None:
            return alias, "used", link_type, token
        ok, fail_reason, _meta = clientes_routes._resolve_public_new_link_token(token)
        if ok:
            return alias, "ready", link_type, token
        if fail_reason == "expired":
            return alias, "expired", link_type, token
        return alias, "invalid", link_type, token

    return alias, "invalid", "invalid", token


@public_bp.route("/solicitud/<code>", methods=["GET"])
def solicitud_share_landing(code: str):
    alias, share_state, link_type, _token = _resolve_share_state(code)
    code_clean = (code or "").strip().upper()
    og_url = _public_external_url("public.solicitud_share_landing", code=code_clean)
    continue_url = _public_external_url("public.solicitud_share_continue", code=code_clean)

    if alias is None:
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    return render_template(
        "clientes/public_share_landing.html",
        share_code=code_clean,
        share_state=share_state,
        link_type=link_type,
        continue_url=continue_url,
        og_url=og_url,
        canonical_url=og_url,
    ), 200


@public_bp.route("/solicitud/<code>/continuar", methods=["GET", "POST"])
def solicitud_share_continue(code: str):
    alias, share_state, link_type, token = _resolve_share_state(code)
    code_clean = (code or "").strip().upper()
    og_url = _public_external_url("public.solicitud_share_landing", code=code_clean)
    sent_state = (request.args.get("estado") or "").strip().lower()

    if alias is None:
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    if share_state == "used":
        # UX: tras envío exitoso por alias, mostrar primero confirmación de éxito
        # (si existe estado de sesión válido), y "used" solo en reingresos posteriores.
        if sent_state == "enviado" and token:
            g.public_share_code = code_clean
            g.public_share_url_override = og_url
            from clientes import routes as clientes_routes
            if link_type == "existente":
                return clientes_routes.solicitud_publica(token)
            if link_type == "nuevo":
                return clientes_routes.solicitud_publica_nueva_token(token)
        return render_template(
            "clientes/public_link_used.html",
            status_code=410,
            og_url=og_url,
            canonical_url=og_url,
        ), 410

    if share_state == "expired":
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="expired",
            status_code=410,
            og_url=og_url,
            canonical_url=og_url,
        ), 410

    if share_state != "ready":
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    g.public_share_code = code_clean
    g.public_share_url_override = og_url

    from clientes import routes as clientes_routes

    if link_type == "existente":
        return clientes_routes.solicitud_publica(token)
    if link_type == "nuevo":
        return clientes_routes.solicitud_publica_nueva_token(token)

    return render_template(
        "clientes/public_link_invalid.html",
        reason_key="invalid",
        status_code=404,
        og_url=og_url,
        canonical_url=og_url,
    ), 404


@public_bp.route('/ping', methods=['GET'])
def public_ping():
    """Endpoint liviano para saber si el servidor está vivo (público)."""
    return _json_no_cache({
        'ok': True,
        'public_enabled': bool(PUBLIC_SITE_ENABLED),
        'server_time': iso_utc_z(),
    })


@public_bp.route('/live/ping', methods=['POST'])
def public_live_ping():
    content_length = int(request.content_length or 0)
    if content_length > int(PUBLIC_LIVE_PING_MAX_BODY_BYTES):
        return _json_no_cache({"ok": False, "error": "payload_too_large"}, status=413)

    ip = _public_client_ip()
    if _public_live_rate_limited(ip):
        return _json_no_cache({"ok": False, "error": "rate_limited"}, status=429)

    if not request.is_json:
        return _json_no_cache({"ok": False, "error": "invalid_json"}, status=400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_no_cache({"ok": False, "error": "invalid_json"}, status=400)

    event_type = str(payload.get('event_type') or '').strip().lower()[:32]
    if event_type not in PUBLIC_LIVE_ALLOWED_EVENT_TYPES:
        return _json_no_cache({"ok": False, "error": "invalid_event_type"}, status=400)

    current_path = str(payload.get('current_path') or "").strip()
    if (not current_path) or (not current_path.startswith("/")):
        return _json_no_cache({"ok": False, "error": "invalid_current_path"}, status=400)
    current_path = current_path[:int(PUBLIC_LIVE_PING_PATH_MAX_LEN)]

    page_title = (payload.get('page_title') or '').strip()[:160]
    ua = (request.headers.get("User-Agent") or "").strip().lower()
    is_bot = any(tok in ua for tok in ("bot", "crawler", "spider", "curl", "python-requests"))

    # Muestreo liviano para no generar ruido ni costo de escritura innecesario.
    if (event_type == "heartbeat") and is_bot:
        return _json_no_cache({'ok': True, 'sampled': False})

    minute_key = utc_now_naive().strftime("%Y%m%d%H%M")
    route_key = f"public_live:{minute_key}:{current_path}"
    count = 0
    try:
        count = int(bp_get(route_key, default=0, context="public_live_route_count_get") or 0) + 1
        bp_set(route_key, count, timeout=180, context="public_live_route_count_set")
    except Exception:
        count = 0

    # Persistencia en auditoria con dedupe por minuto/ruta/evento.
    dedupe_key = f"public_live_log:{minute_key}:{event_type}:{current_path}"
    should_log = (event_type != "heartbeat")
    try:
        if should_log or not bp_get(dedupe_key, default=0, context="public_live_dedupe_get"):
            bp_set(dedupe_key, 1, timeout=180, context="public_live_dedupe_set")
            should_log = True
    except Exception:
        pass

    if should_log:
        log_action(
            action_type="PUBLIC_LIVE_EVENT",
            entity_type="public_route",
            entity_id=current_path[:64] or "home",
            summary=f"Visita publica {current_path}"[:255],
            metadata={
                "scope": "public",
                "event_type": event_type,
                "page_title": page_title,
                "route_hits_minute": count,
            },
            success=True,
        )
    return _json_no_cache({'ok': True, 'sampled': True, 'route_hits_minute': count})


@public_bp.route("/")
def index():
    """
    Raíz del sitio.

    - Si la web pública está deshabilitada:
        👉 redirige al login interno (/login).
    - Si la web pública está habilitada:
        👉 muestra el landing público normal.
    """
    if not PUBLIC_SITE_ENABLED:
        # 🔴 Cambia "/login" por "/home" o la ruta que uses como inicio de sesión
        return redirect("/login")

    return render_template("public/index.html")


@public_bp.route("/servicios")
def servicios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/servicios.html")


@public_bp.route("/sobre-nosotros")
def sobre_nosotros():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/sobre_nosotros.html")


@public_bp.route("/contacto")
def contacto():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/contacto.html")


@public_bp.route("/faq")
def faq():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/faq.html")


@public_bp.route("/como-funciona")
def como_funciona():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/como_funciona.html")


@public_bp.route("/beneficios")
def beneficios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/beneficios.html")


@public_bp.route("/candidatas")
def candidatas_publico():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    # Compatibilidad de coexistencia:
    # Si hay sesión interna de staff, usar el flujo legacy de banco/listado.
    try:
        role = (str(session.get("role") or "").strip().lower())
        if role in ("owner", "admin", "secretaria"):
            from core import legacy_handlers as legacy_h
            return legacy_h.list_candidatas()
    except Exception:
        pass
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = (getattr(current_user, "role", None) or getattr(current_user, "rol", None) or "").strip().lower()
            if role in ("owner", "admin", "secretaria"):
                from core import legacy_handlers as legacy_h
                return legacy_h.list_candidatas()
    except Exception:
        pass
    return render_template("public/candidatas.html")


@public_bp.route("/politicas")
def politicas_publicas():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("politicas.html")


@public_bp.route("/privacidad")
def privacidad_publica():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("privacidad.html")


@public_bp.route("/gracias")
def gracias():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/gracias.html")


@public_bp.route("/catalogo/<token>", methods=["GET"])
def catalogo_privado_listado(token: str):
    from models import CatalogoPrivadoItem, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    if status == "expired":
        return render_template("catalogo_privado/catalogo_expirado.html"), 410

    items = (
        CatalogoPrivadoItem.query.filter_by(catalogo_id=catalogo.id, is_visible=True)
        .order_by(CatalogoPrivadoItem.orden.asc().nullslast(), CatalogoPrivadoItem.id.asc())
        .all()
    )
    candidata_ids = [int(item.candidata_id) for item in items if item.candidata_id]
    fichas = {}
    if candidata_ids:
        rows = CandidataWeb.query.filter(CandidataWeb.candidata_id.in_(candidata_ids)).all()
        fichas = {int(r.candidata_id): r for r in rows}
    candidatas = [
        _catalogo_public_payload(item.candidata, ficha_web=fichas.get(int(item.candidata_id)))
        for item in items
        if item.candidata
    ]
    return render_template(
        "catalogo_privado/catalogo_listado.html",
        catalogo=catalogo,
        candidatas=candidatas,
        token=token,
    )


@public_bp.route("/catalogo/<token>/candidata/<codigo_o_id>", methods=["GET"])
def catalogo_privado_candidata_detalle(token: str, codigo_o_id: str):
    from models import Candidata, CatalogoPrivadoItem, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    if status == "expired":
        return render_template("catalogo_privado/catalogo_expirado.html"), 410

    candidata_q = Candidata.query
    raw = (codigo_o_id or "").strip()
    if raw.isdigit():
        candidata = candidata_q.filter(Candidata.fila == int(raw)).first()
    else:
        candidata = candidata_q.filter(func.lower(Candidata.codigo) == raw.lower()).first()
    if not candidata:
        return render_template("catalogo_privado/catalogo_invalido.html"), 404

    exists_item = CatalogoPrivadoItem.query.filter_by(
        catalogo_id=catalogo.id,
        candidata_id=int(candidata.fila),
        is_visible=True,
    ).first()
    if not exists_item:
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    ficha_web = CandidataWeb.query.filter_by(candidata_id=int(candidata.fila)).first()

    return render_template(
        "catalogo_privado/catalogo_candidata_detalle.html",
        catalogo=catalogo,
        candidata=_catalogo_public_payload(candidata, ficha_web=ficha_web),
        token=token,
    )
