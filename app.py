# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import os
from datetime import timedelta

from flask import session, request, redirect, url_for

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


@app.after_request
def security_headers(resp):
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    except Exception:
        pass
    return resp


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


# Blueprints internos modularizados
app.register_blueprint(candidatas_bp)
app.register_blueprint(procesos_bp)
app.register_blueprint(entrevistas_bp)
app.register_blueprint(archivos_bp)

# Compatibilidad de endpoint names preexistentes
for bp_name in ("candidatas_routes", "procesos_routes", "entrevistas_routes", "archivos_routes"):
    _register_endpoint_aliases(app, bp_name)


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
