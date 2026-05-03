# app_web/public/routes.py

import os
import urllib.parse

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
from . import public_bp

from utils.audit_logger import log_action
from utils.distributed_backplane import bp_get, bp_set
from utils.timezone import iso_utc_z, utc_now_naive

# Límite de paginación pública
PUBLIC_MAX_PAGE = 50
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
    payload = request.get_json(silent=True) or {}
    current_path = (payload.get('current_path') or request.path or '').strip()[:255]
    event_type = (payload.get('event_type') or 'heartbeat').strip().lower()[:32]
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
