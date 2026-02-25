# utils/security_layer.py
# -*- coding: utf-8 -*-

import os
import time
from flask import request, abort, make_response


def _is_true(v: str) -> bool:
    return (str(v or "").strip().lower() in ("1", "true", "yes", "on"))


def _should_trust_xff() -> bool:
    """
    Solo confiar en X-Forwarded-For / X-Real-IP cuando estás detrás de proxy/reverse proxy
    (Render / Nginx / Cloudflare).

    Habilitar:
      TRUST_XFF=1
    """
    return _is_true(os.getenv("TRUST_XFF", ""))


def _get_client_ip() -> str:
    """
    Saca la IP del cliente de forma segura.

    - Si TRUST_XFF=1: intenta X-Real-IP y luego X-Forwarded-For (primera IP).
    - Si no: usa request.remote_addr.

    Nota:
    - XFF puede traer múltiples IPs, tomamos la primera (IP original).
    """
    ip = ""

    if _should_trust_xff():
        # Algunos proxies mandan X-Real-IP
        x_real = (request.headers.get("X-Real-IP") or "").strip()
        if x_real:
            ip = x_real

        if not ip:
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
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
    prod = env in ("prod", "production")

    # Ventana + limites
    LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))   # 5 min
    LOGIN_MAX_ATTEMPTS   = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))      # 10 intentos
    LOGIN_BLOCK_SECONDS  = int(os.getenv("LOGIN_BLOCK_SECONDS", "900"))    # 15 min

    # Protege logins (incluye variaciones con slash final)
    LOGIN_PATHS = {
        "/login", "/login/",
        "/admin/login", "/admin/login/",
        "/clientes/login", "/clientes/login/",
    }

    # Opcional: no tocar health checks internos (Render/monitoreo)
    # Si tu app usa estos endpoints, no los bloqueamos nunca.
    HEALTH_PATHS = {
        "/health", "/healthz", "/ping", "/_health", "/_healthz"
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

        # ─────────────────────────────────────────────────────
        # CSP (Content-Security-Policy)
        #
        # Objetivo:
        # - En DEV/local: NO romper diseños (CDNs) por default.
        # - En PROD: CSP fuerte permitiendo solo lo que tu app usa.
        #
        # Control por env:
        #   CSP_MODE = off | report | enforce
        #     - off: no setea CSP
        #     - report: setea Content-Security-Policy-Report-Only
        #     - enforce: setea Content-Security-Policy
        #
        # Default:
        #   - development: off
        #   - production: enforce
        # ─────────────────────────────────────────────────────

        csp_mode = (os.getenv("CSP_MODE") or ("enforce" if prod else "off")).strip().lower()

        if csp_mode in ("report", "enforce"):
            # Si en tu HTML usas CDNs (Bootstrap, FontAwesome, Select2, DataTables, etc.)
            # se deben permitir aquí.
            csp = (
                "default-src 'self'; "
                "base-uri 'self'; "
                "object-src 'none'; "
                "frame-ancestors 'none'; "
                "img-src 'self' data: blob: https:; "
                "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://use.fontawesome.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://code.jquery.com https://cdn.datatables.net; "
                "connect-src 'self' https:; "
                "form-action 'self'"
            )

            # En producción bajo HTTPS, podemos forzar upgrade.
            if prod and request.is_secure:
                csp += "; upgrade-insecure-requests"

            # Evita duplicados si otra capa lo setea
            try:
                resp.headers.pop("Content-Security-Policy", None)
                resp.headers.pop("Content-Security-Policy-Report-Only", None)
            except Exception:
                pass

            if csp_mode == "report":
                resp.headers["Content-Security-Policy-Report-Only"] = csp
            else:
                resp.headers["Content-Security-Policy"] = csp

        # HSTS solo cuando esté en producción real con HTTPS
        if prod and request.is_secure:
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        # Evita que el navegador "adivine" tipos
        resp.headers.setdefault("X-Download-Options", "noopen")

        # Cache-control defensivo para páginas autenticadas (admin/clientes)
        # Esto ayuda a evitar que el navegador muestre páginas del panel desde cache.
        try:
            p = (request.path or "")
            if p.startswith("/admin") or p.startswith("/clientes"):
                resp.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                resp.headers.setdefault("Pragma", "no-cache")
                resp.headers.setdefault("Expires", "0")
        except Exception:
            pass

        return resp

    @app.errorhandler(429)
    def _too_many_requests(e):
        return make_response(
            "Demasiados intentos seguidos. Espera un momento y vuelve a intentar.",
            429
        )

    def _looks_like_login_attempt() -> bool:
        """
        Evita contar intentos por errores de CSRF o requests raros.
        Solo cuenta si el POST realmente trae campos típicos de login.
        """
        f = request.form
        keys = set(k.lower() for k in f.keys())

        # Campos comunes
        has_user = any(k in keys for k in ("usuario", "username", "email"))
        has_pass = any(k in keys for k in ("clave", "password", "pass"))

        # Si viene al menos user o pass, asumimos intento real.
        return bool(has_user or has_pass)

    @app.before_request
    def _anti_bruteforce_login():
        # Permitir estáticos y health checks
        path = (request.path or "")
        if path.startswith("/static/") or path in HEALTH_PATHS:
            return

        # No aplicar a preflight o métodos no relevantes
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return

        # Solo POST típicamente
        if request.method != "POST":
            return

        # Solo en rutas de login
        if path not in LOGIN_PATHS:
            return

        # Solo contar si parece login real (evita bloquear por CSRF fallido)
        if not _looks_like_login_attempt():
            return

        ip = _get_client_ip()
        if not ip:
            return

        raw_user = (
            request.form.get("usuario")
            or request.form.get("username")
            or request.form.get("email")
            or ""
        ).strip()[:80].lower()

        # Por IP
        block_key = f"login:block:{path}:{ip}"
        attempts_key = f"login:attempts:{path}:{ip}"

        # Por IP+usuario (si aplica)
        block_key_u = f"login:block:{path}:{ip}:{raw_user}" if raw_user else None
        attempts_key_u = f"login:attempts:{path}:{ip}:{raw_user}" if raw_user else None

        # bloqueado
        if _cache_get(cache, block_key):
            abort(429)
        if block_key_u and _cache_get(cache, block_key_u):
            abort(429)

        # suma intento
        attempts = int(_cache_get(cache, attempts_key) or 0) + 1
        _cache_set(cache, attempts_key, attempts, timeout=LOGIN_WINDOW_SECONDS)

        # Variante por usuario (si existe)
        attempts_u = 0
        if attempts_key_u:
            attempts_u = int(_cache_get(cache, attempts_key_u) or 0) + 1
            _cache_set(cache, attempts_key_u, attempts_u, timeout=LOGIN_WINDOW_SECONDS)

        # Bloquea si se pasó
        if attempts > LOGIN_MAX_ATTEMPTS or (attempts_key_u and attempts_u > LOGIN_MAX_ATTEMPTS):
            _cache_set(
                cache,
                block_key,
                int(time.time()) + LOGIN_BLOCK_SECONDS,
                timeout=LOGIN_BLOCK_SECONDS
            )
            if block_key_u:
                _cache_set(
                    cache,
                    block_key_u,
                    int(time.time()) + LOGIN_BLOCK_SECONDS,
                    timeout=LOGIN_BLOCK_SECONDS
                )
            abort(429)

    def clear_login_attempts(ip: str, path: str = "/admin/login", username: str = ""):
        """
        Limpia contadores de login para un (ip, path, username).
        Útil al autenticarse correctamente para evitar "castigos" posteriores.
        """
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