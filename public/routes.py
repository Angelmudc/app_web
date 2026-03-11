# app_web/public/routes.py

from flask import render_template, abort, request, redirect, jsonify, make_response, session
from flask_login import current_user
from . import public_bp

from config_app import cache
from utils.audit_logger import log_action
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
        count = int(cache.get(route_key) or 0) + 1
        cache.set(route_key, count, timeout=180)
    except Exception:
        count = 0

    # Persistencia en auditoria con dedupe por minuto/ruta/evento.
    dedupe_key = f"public_live_log:{minute_key}:{event_type}:{current_path}"
    should_log = (event_type != "heartbeat")
    try:
        if should_log or not cache.get(dedupe_key):
            cache.set(dedupe_key, 1, timeout=180)
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


@public_bp.route("/gracias")
def gracias():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/gracias.html")
