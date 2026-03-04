# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import os
from datetime import timedelta

from flask import session, request, redirect, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from config_app import create_app, db, csrf, cache, USUARIOS
from core.routes import candidatas_bp, procesos_bp, entrevistas_bp, archivos_bp


app = create_app()


app.jinja_env.globals['has_endpoint'] = lambda name: name in app.view_functions


def url_for_safe(endpoint: str, **values):
    return url_for(endpoint, **values) if endpoint in app.view_functions else None


app.jinja_env.globals['url_for_safe'] = url_for_safe


@app.before_request
def force_session_expire():
    # Mantiene la sesión no permanente (como en la versión monolítica)
    session.permanent = False


IS_PROD = (
    (os.getenv("FLASK_ENV", "").strip().lower() == "production")
    or (os.getenv("ENV", "").strip().lower() == "production")
)

app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
app.config.setdefault("SESSION_COOKIE_SECURE", bool(IS_PROD))
app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
app.config.setdefault("REMEMBER_COOKIE_SECURE", bool(IS_PROD))
app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=8))
app.config.setdefault("SESSION_PERMANENT", False)
app.config.setdefault("WTF_CSRF_TIME_LIMIT", 60 * 60 * 8)

# Refuerzo explícito para despliegues detrás de Render/Cloudflare
if not isinstance(app.wsgi_app, ProxyFix):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


@app.after_request
def security_headers(resp):
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if IS_PROD and request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    except Exception:
        pass
    return resp


def _is_authenticated_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            return True
    except Exception:
        pass
    return bool(session.get("usuario") and session.get("role"))


def _is_admin_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = (getattr(current_user, "role", None) or getattr(current_user, "rol", None) or "").strip().lower()
            if role == "admin" or bool(getattr(current_user, "is_admin", False)):
                return True
    except Exception:
        pass
    return (str(session.get("role") or "").strip().lower() == "admin")


def _is_staff_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = (getattr(current_user, "role", None) or getattr(current_user, "rol", None) or "").strip().lower()
            if role in ("admin", "secretaria"):
                return True
    except Exception:
        pass
    return (str(session.get("role") or "").strip().lower() in ("admin", "secretaria"))


@app.before_request
def _protect_sensitive_routes():
    path = (request.path or "").strip()

    if not path or path.startswith("/static/"):
        return None

    # Nunca interceptar logins/logout/health
    if path in {"/login", "/logout", "/admin/login", "/clientes/login", "/health", "/healthz", "/ping"}:
        return None

    if path.startswith("/admin/"):
        if not _is_staff_any():
            return redirect(url_for("admin.login", next=request.full_path or path))
        return None

    if path.startswith("/clientes/"):
        if not _is_authenticated_any():
            return redirect(url_for("clientes.login", next=request.full_path or path))
        return None

    sensitive_prefixes = (
        "/gestionar_archivos",
        "/subir_fotos",
        "/report",
        "/pagos",
        "/editar",
    )
    if any(path.startswith(p) for p in sensitive_prefixes):
        if not _is_authenticated_any():
            return redirect(url_for("login", next=request.full_path or path))
    return None


def _register_endpoint_aliases(app_obj, blueprint_name: str):
    """Crea alias legacy sin prefijo para conservar url_for('endpoint') existentes."""
    prefix = f"{blueprint_name}."
    rules = list(app_obj.url_map.iter_rules())

    for rule in rules:
        if not rule.endpoint.startswith(prefix):
            continue

        bare_endpoint = rule.endpoint[len(prefix):]
        if bare_endpoint in app_obj.url_map._rules_by_endpoint:
            continue

        view_func = app_obj.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        app_obj.add_url_rule(
            rule.rule,
            endpoint=bare_endpoint,
            view_func=view_func,
            methods=methods,
            defaults=rule.defaults,
            strict_slashes=rule.strict_slashes,
        )


def _register_endpoint_alias(app_obj, source_endpoint: str, alias_endpoint: str):
    """Crea alias explícito entre endpoints, incluyendo endpoints legacy con namespace."""
    if alias_endpoint in app_obj.url_map._rules_by_endpoint:
        return

    source_rules = list(app_obj.url_map._rules_by_endpoint.get(source_endpoint, []))
    if not source_rules:
        return

    view_func = app_obj.view_functions.get(source_endpoint)
    if view_func is None:
        return

    for rule in source_rules:
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        app_obj.add_url_rule(
            rule.rule,
            endpoint=alias_endpoint,
            view_func=view_func,
            methods=methods,
            defaults=rule.defaults,
            strict_slashes=rule.strict_slashes,
        )


# Blueprints internos modularizados
app.register_blueprint(candidatas_bp)
app.register_blueprint(procesos_bp)
app.register_blueprint(entrevistas_bp)
app.register_blueprint(archivos_bp)

# Compatibilidad de endpoint names preexistentes
for bp_name in ("candidatas_routes", "procesos_routes", "entrevistas_routes", "archivos_routes"):
    _register_endpoint_aliases(app, bp_name)

# Compatibilidad legacy explícita para templates/flujo de archivos
_register_endpoint_alias(app, "subir_fotos", "subir_fotos.subir_fotos")
_register_endpoint_alias(app, "ver_imagen", "subir_fotos.ver_imagen")


# Login routing por blueprint (mismo comportamiento esperado)
try:
    lm = app.extensions.get("login_manager")
    if lm is not None:
        lm.login_view = "login"
        if not getattr(lm, "blueprint_login_views", None):
            lm.blueprint_login_views = {}
        lm.blueprint_login_views["clientes"] = "clientes.login"
        lm.blueprint_login_views["admin"] = "admin.login"

        @lm.unauthorized_handler
        def _unauthorized_callback():
            bp = (request.blueprint or "").strip()
            next_url = request.full_path if request.full_path else request.path
            if bp == "clientes":
                return redirect(url_for("clientes.login", next=next_url))
            if bp == "admin":
                return redirect(url_for("admin.login", next=next_url))
            return redirect(url_for("login", next=next_url))
except Exception:
    pass


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
