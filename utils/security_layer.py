# utils/security_layer.py
import os
import time
from flask import request, abort, make_response

def _should_trust_xff() -> bool:
    """
    Solo confiar en X-Forwarded-For cuando estás detrás de un proxy/reverse proxy
    (por ejemplo Render / Nginx). Para habilitarlo:
      TRUST_XFF=1
    """
    return (os.getenv("TRUST_XFF", "").strip() == "0")

def _get_client_ip() -> str:
    """
    Saca la IP del cliente de forma segura.
    - Solo usa X-Forwarded-For si TRUST_XFF=0 (para evitar spoofing).
    - Si no: request.remote_addr.
    """
    ip = ""
    if _should_trust_xff():
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            ip = xff.split(",")[0].strip()

    if not ip:
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

    # Protege logins (incluye el login principal si existe)
    LOGIN_PATHS = {
        "/login",
        "/admin/login",
        "/clientes/login",
    }

    @app.after_request
    def _security_headers(resp):
        # No sobreescribir si ya existe (seguridad por capas)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        resp.headers.setdefault("X-Frame-Options", "DENY")

        # CSP “suave” (no rompe CDNs). No pisar si ya existe.
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
        resp.headers.setdefault("Content-Security-Policy", csp)

        # HSTS SOLO en producción (requiere HTTPS). No pisar si ya existe.
        if prod:
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

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

        # Intento de identificar usuario (si el form lo envía). Mantener genérico.
        raw_user = (
            request.form.get("usuario")
            or request.form.get("username")
            or request.form.get("email")
            or ""
        ).strip()[:80].lower()

        # Por IP
        block_key = f"login:block:{path}:{ip}"
        attempts_key = f"login:attempts:{path}:{ip}"

        # Por IP+usuario (si aplica) — dificulta ataques dirigidos
        block_key_u = f"login:block:{path}:{ip}:{raw_user}" if raw_user else None
        attempts_key_u = f"login:attempts:{path}:{ip}:{raw_user}" if raw_user else None

        # bloqueado
        if _cache_get(cache, block_key):
            abort(429)
        if block_key_u and _cache_get(cache, block_key_u):
            abort(429)

        # suma intento
        attempts = _cache_get(cache, attempts_key) or 0
        attempts = int(attempts) + 1
        _cache_set(cache, attempts_key, attempts, timeout=LOGIN_WINDOW_SECONDS)

        # Variante por usuario (si existe)
        if attempts_key_u:
            attempts_u = _cache_get(cache, attempts_key_u) or 0
            attempts_u = int(attempts_u) + 1
            _cache_set(cache, attempts_key_u, attempts_u, timeout=LOGIN_WINDOW_SECONDS)
        else:
            attempts_u = 0

        # Bloquea si se pasó (por IP o por IP+usuario)
        if attempts > LOGIN_MAX_ATTEMPTS or (attempts_key_u and attempts_u > LOGIN_MAX_ATTEMPTS):
            _cache_set(cache, block_key, int(time.time()) + LOGIN_BLOCK_SECONDS, timeout=LOGIN_BLOCK_SECONDS)
            if block_key_u:
                _cache_set(cache, block_key_u, int(time.time()) + LOGIN_BLOCK_SECONDS, timeout=LOGIN_BLOCK_SECONDS)
            abort(429)

    def clear_login_attempts(ip: str, path: str = "/admin/login", username: str = ""):
        if not ip:
            return
        ip = ip.strip()[:64]
        username = (username or "").strip()[:80].lower()

        _cache_delete(cache, f"login:attempts:{path}:{ip}")
        _cache_delete(cache, f"login:block:{path}:{ip}")

        if username:
            _cache_delete(cache, f"login:attempts:{path}:{ip}:{username}")
            _cache_delete(cache, f"login:block:{path}:{ip}:{username}")

    app.extensions["clear_login_attempts"] = clear_login_attempts