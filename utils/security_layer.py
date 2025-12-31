# utils/security_layer.py
import os
import time
from flask import request, abort, make_response

def _get_client_ip() -> str:
    """
    Saca la IP real.
    - Si hay proxy / Render: usa X-Forwarded-For (primer IP).
    - Si no: request.remote_addr.
    """
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        ip = xff.split(",")[0].strip()
    else:
        ip = (request.remote_addr or "").strip()
    return ip[:64]

def _cache_get(cache, key):
    try:
        return cache.get(key)
    except Exception:
        return None

def _cache_set(cache, key, value, timeout):
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception:
        return False

def _cache_delete(cache, key):
    try:
        cache.delete(key)
        return True
    except Exception:
        return False

def init_security(app, cache):
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "production")).lower()
    prod = env in ("prod", "production")

    LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))   # 5 min
    LOGIN_MAX_ATTEMPTS   = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))      # 10 intentos
    LOGIN_BLOCK_SECONDS  = int(os.getenv("LOGIN_BLOCK_SECONDS", "900"))    # 15 min

    # OJO: NO incluimos "/login" porque tu login principal ya tiene su propio bloqueo en app.py
    LOGIN_PATHS = {
        "/admin/login",
        "/clientes/login",
    }

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        resp.headers["X-Frame-Options"] = "DENY"

        # CSP “suave” (no rompe CDNs)
        csp = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "script-src 'self' 'unsafe-inline' https:; "
            "font-src 'self' data: https:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        resp.headers["Content-Security-Policy"] = csp

        if prod:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return resp

    @app.errorhandler(429)
    def _too_many_requests(e):
        return make_response(
            "Demasiados intentos seguidos. Espera un momento y vuelve a intentar.",
            429
        )

    @app.before_request
    def _anti_bruteforce_login():
        if request.method != "POST":
            return

        path = request.path or ""
        if path not in LOGIN_PATHS:
            return

        ip = _get_client_ip()
        if not ip:
            return

        block_key = f"login:block:{path}:{ip}"
        attempts_key = f"login:attempts:{path}:{ip}"

        # bloqueado
        if _cache_get(cache, block_key):
            abort(429)

        # suma intento
        attempts = _cache_get(cache, attempts_key) or 0
        attempts = int(attempts) + 1
        _cache_set(cache, attempts_key, attempts, timeout=LOGIN_WINDOW_SECONDS)

        # bloquea si se pasó
        if attempts > LOGIN_MAX_ATTEMPTS:
            _cache_set(cache, block_key, int(time.time()) + LOGIN_BLOCK_SECONDS, timeout=LOGIN_BLOCK_SECONDS)
            abort(429)

    def clear_login_attempts(ip: str, path: str = "/admin/login"):
        if not ip:
            return
        ip = ip.strip()[:64]
        _cache_delete(cache, f"login:attempts:{path}:{ip}")
        _cache_delete(cache, f"login:block:{path}:{ip}")

    app.extensions["clear_login_attempts"] = clear_login_attempts