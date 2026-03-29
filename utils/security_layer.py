# utils/security_layer.py
# -*- coding: utf-8 -*-

import os
import time
from flask import request, abort, make_response, session
from utils.distributed_backplane import bp_delete, bp_get, bp_incr, bp_set

try:
    from flask_login import current_user
except Exception:
    current_user = None


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

    - Si TRUST_XFF=1: intenta en orden:
      1) CF-Connecting-IP
      2) X-Real-IP
      3) X-Forwarded-For (primera IP)
    - Si no: usa request.remote_addr.

    Nota:
    - XFF puede traer múltiples IPs, tomamos la primera (IP original).
    """
    ip = ""

    if _should_trust_xff():
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            ip = cf_ip

        # Algunos proxies mandan X-Real-IP
        x_real = (request.headers.get("X-Real-IP") or "").strip()
        if (not ip) and x_real:
            ip = x_real

        if not ip:
            xff = (request.headers.get("X-Forwarded-For") or "").strip()
            if xff:
                ip = xff.split(",")[0].strip()

    if not ip:
        ip = (request.remote_addr or "").strip()

    return ip[:64]


def _cache_get(cache, key):
    return bp_get(key, default=None, context="security_cache_get")


def _cache_set(cache, key, value, timeout):
    return bp_set(key, value, timeout=timeout, context="security_cache_set")


def _cache_delete(cache, key):
    return bp_delete(key, context="security_cache_delete")


def init_security(app, cache):
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
    prod = env in ("prod", "production")

    def _env_int(name: str, default: int, min_value: int = 1) -> int:
        try:
            return max(min_value, int((os.getenv(name) or str(default)).strip()))
        except Exception:
            return max(min_value, int(default))

    def _log_auth_throttle(event: str, *, ip: str, path: str, username: str, reason: str, metadata=None):
        try:
            from utils.audit_logger import log_auth_event

            log_auth_event(
                event=event,
                status="fail",
                user_identifier=(username or None),
                reason=reason,
                metadata={
                    "ip": ip,
                    "path": path,
                    **(metadata or {}),
                },
            )
        except Exception:
            return

    def _login_window_seconds() -> int:
        return _env_int("LOGIN_WINDOW_SECONDS", 3600)

    def _login_block_seconds() -> int:
        return _env_int("LOGIN_BLOCK_SECONDS", 900)

    def _login_delay_threshold() -> int:
        return _env_int("LOGIN_DELAY_THRESHOLD", 5)

    def _login_block_threshold() -> int:
        fallback_raw = (os.getenv("LOGIN_MAX_ATTEMPTS") or "10").strip() or "10"
        try:
            fallback = int(fallback_raw)
        except Exception:
            fallback = 10
        return _env_int("LOGIN_BLOCK_THRESHOLD", fallback)

    def _login_rate_ip_1m() -> int:
        return _env_int("LOGIN_RATE_IP_1M", 5)

    def _login_rate_ip_1h() -> int:
        return _env_int("LOGIN_RATE_IP_1H", 20)

    def _login_rate_user_1m() -> int:
        return _env_int("LOGIN_RATE_USER_1M", 5)

    def _login_rate_user_1h() -> int:
        return _env_int("LOGIN_RATE_USER_1H", 20)

    def _login_delay_ms_base() -> int:
        return _env_int("LOGIN_DELAY_MS_BASE", 800, min_value=0)

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
    # Endpoints internos de realtime/polling del panel.
    REALTIME_MONITOREO_PATHS = {
        "/admin/monitoreo/presence/ping",
        "/admin/monitoreo/stream",
        "/admin/monitoreo/logs.json",
        "/admin/monitoreo/summary.json",
        "/admin/monitoreo/productividad.json",
        "/admin/monitoreo/presence.json",
        "/admin/seguridad/locks/ping",
        "/admin/solicitudes/live",
        "/clientes/live/ping",
        "/clientes/solicitudes/live",
        "/live/ping",
    }

    # Anti-scraping / anti-bots
    SCRAPE_GLOBAL_WINDOW_SECONDS = int(os.getenv("SCRAPE_GLOBAL_WINDOW_SECONDS", "300"))
    SCRAPE_GLOBAL_MAX_REQ = int(os.getenv("SCRAPE_GLOBAL_MAX_REQ", "300"))
    SCRAPE_STRICT_WINDOW_SECONDS = int(os.getenv("SCRAPE_STRICT_WINDOW_SECONDS", "60"))
    SCRAPE_LIST_MAX_REQ = int(os.getenv("SCRAPE_LIST_MAX_REQ", "30"))
    SCRAPE_LIST_MAX_REQ_USER = int(os.getenv("SCRAPE_LIST_MAX_REQ_USER", "60"))
    SCRAPE_ADMIN_MAX_REQ = int(os.getenv("SCRAPE_ADMIN_MAX_REQ", "60"))
    SCRAPE_CLIENTES_MAX_REQ = int(os.getenv("SCRAPE_CLIENTES_MAX_REQ", "60"))
    SCRAPE_UPLOAD_MAX_REQ = int(os.getenv("SCRAPE_UPLOAD_MAX_REQ", "30"))
    SCRAPE_REPORTS_MAX_REQ = int(os.getenv("SCRAPE_REPORTS_MAX_REQ", "40"))
    SCRAPE_404_WINDOW_SECONDS = int(os.getenv("SCRAPE_404_WINDOW_SECONDS", "120"))
    SCRAPE_404_MAX = int(os.getenv("SCRAPE_404_MAX", "25"))
    SCRAPE_BLOCK_SECONDS = int(os.getenv("SCRAPE_BLOCK_SECONDS", "600"))
    SCRAPE_SLOWDOWN = _is_true(os.getenv("SCRAPE_SLOWDOWN", "1"))
    SCRAPE_SLOWDOWN_MS = int(os.getenv("SCRAPE_SLOWDOWN_MS", "150"))
    PUBLIC_SENSITIVE_WINDOW_SECONDS = int(os.getenv("PUBLIC_SENSITIVE_WINDOW_SECONDS", "60"))
    PUBLIC_SENSITIVE_MAX_REQ = int(os.getenv("PUBLIC_SENSITIVE_MAX_REQ", "10"))
    STAFF_WORK_WINDOW_SECONDS = int(os.getenv("STAFF_WORK_WINDOW_SECONDS", "60"))
    STAFF_WORK_MAX_REQ = int(os.getenv("STAFF_WORK_MAX_REQ", "300"))
    STAFF_REALTIME_WINDOW_SECONDS = int(os.getenv("STAFF_REALTIME_WINDOW_SECONDS", "60"))
    STAFF_REALTIME_MAX_REQ = int(os.getenv("STAFF_REALTIME_MAX_REQ", "900"))
    STAFF_LIST_MAX_REQ_USER = int(os.getenv("STAFF_LIST_MAX_REQ_USER", "300"))
    AUTH_WORK_WINDOW_SECONDS = int(os.getenv("AUTH_WORK_WINDOW_SECONDS", "60"))
    AUTH_WORK_MAX_REQ = int(os.getenv("AUTH_WORK_MAX_REQ", "180"))
    ADMIN_CRITICAL_WINDOW_SECONDS = int(os.getenv("ADMIN_CRITICAL_WINDOW_SECONDS", "60"))
    ADMIN_CRITICAL_MAX_REQ = int(os.getenv("ADMIN_CRITICAL_MAX_REQ", "60"))
    def _operational_rate_limits_enabled() -> bool:
        """
        Activo por defecto en producción.
        En desarrollo/testing, requiere activar explícitamente por ENV.
        """
        raw = os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS")
        if raw is not None and str(raw).strip() != "":
            return _is_true(raw)
        run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
        return run_env in ("prod", "production")

    BOT_UA_PATTERNS = (
        "curl",
        "python-requests",
        "python-urllib",
        "httpx",
        "wget",
        "scrapy",
        "aiohttp",
        "go-http-client",
        "libwww-perl",
    )

    def _is_exempt_path(path: str) -> bool:
        if not path:
            return True
        if path.startswith("/static/"):
            return True
        if path in HEALTH_PATHS:
            return True
        return False

    def _normalize_path(path: str) -> str:
        p = (path or "").strip()
        if not p:
            return ""
        if p != "/":
            p = p.rstrip("/")
        return p

    NORMALIZED_LOGIN_PATHS = {_normalize_path(p) for p in LOGIN_PATHS}

    def _is_realtime_monitoreo_path(path: str) -> bool:
        p = _normalize_path(path)
        if not p:
            return False
        if p in REALTIME_MONITOREO_PATHS:
            return True
        if p.startswith("/admin/monitoreo/candidatas/") and (
            p.endswith("/stream") or p.endswith("/logs.json")
        ):
            return True
        return p in REALTIME_MONITOREO_PATHS

    def _normalize_role(value: str) -> str:
        role = (value or "").strip().lower()
        if role in ("secretaria", "secretary", "secre", "secretaría"):
            return "secretaria"
        if role in ("owner", "admin"):
            return role
        if role in ("cliente",):
            return "cliente"
        return role

    def _current_user_key() -> str:
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                uid = (
                    current_user.get_id()
                    or getattr(current_user, "id", None)
                    or getattr(current_user, "pk", None)
                    or getattr(current_user, "email", None)
                    or getattr(current_user, "username", None)
                )
                if uid:
                    return str(uid).strip()[:80]
        except Exception:
            pass

        # Compatibilidad legacy por sesión
        raw = (session.get("usuario") or "").strip()
        return raw[:80] if raw else ""

    def _current_role() -> str:
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                role = (
                    getattr(current_user, "role", None)
                    or getattr(current_user, "rol", None)
                    or ""
                )
                out = _normalize_role(str(role))
                if out:
                    return out
        except Exception:
            pass
        return _normalize_role(str(session.get("role") or ""))

    def _is_authenticated_any() -> bool:
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                return True
        except Exception:
            pass
        return bool((session.get("usuario") or "").strip())

    def _is_staff_authenticated() -> bool:
        role = _current_role()
        if role not in {"owner", "admin", "secretaria"}:
            return False
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                if hasattr(current_user, "is_active") and not bool(current_user.is_active):
                    return False
        except Exception:
            pass
        return True

    def _is_admin_critical_path(path: str, method: str) -> bool:
        m = (method or "").upper()
        p = _normalize_path(path)
        if m == "DELETE":
            return True
        if m not in {"POST", "PUT", "PATCH"}:
            return False
        if not p.startswith("/admin/"):
            return False
        critical_tokens = ("/eliminar", "/delete", "/borrar", "/cancelar")
        return any(tok in p for tok in critical_tokens)

    def _is_likely_bot_ua() -> bool:
        ua = (request.headers.get("User-Agent") or "").strip().lower()
        if not ua:
            return True
        return any(p in ua for p in BOT_UA_PATTERNS)

    def _bucket_inc(key: str, window_seconds: int) -> int:
        return int(
            bp_incr(
                key,
                delta=1,
                timeout=max(1, int(window_seconds)),
                context="security_bucket_incr",
            ) or 0
        )

    def _match_scrape_group(path: str, endpoint: str) -> str:
        ep = (endpoint or "").strip()
        p = (path or "").strip()

        if p == "/candidatas_db" or ep.endswith("list_candidatas_db"):
            return "list_json"
        if p.startswith("/admin/"):
            return "admin"
        if p.startswith("/clientes/"):
            return "clientes"
        if p.startswith("/gestionar_archivos") or p.startswith("/subir_fotos"):
            return "upload"
        if p.startswith("/reporte") or p.startswith("/report") or p.startswith("/pagos") or "/editar" in p:
            return "reports"
        return ""

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
        msg = getattr(e, "description", None) or (
            "Estas realizando demasiadas acciones muy rapido. "
            "Espera unos segundos e intenta nuevamente."
        )
        return make_response(
            msg,
            429
        )

    @app.before_request
    def _anti_scrape_guard():
        if not _operational_rate_limits_enabled():
            return
        if bool(app.config.get("TESTING")):
            return

        path = (request.path or "")
        path = _normalize_path(path)
        if _is_exempt_path(path):
            return

        if request.method in ("OPTIONS", "HEAD"):
            return

        ip = _get_client_ip() or "0.0.0.0"
        endpoint = (request.endpoint or "")
        user_key = _current_user_key()
        is_auth = _is_authenticated_any()
        is_staff = _is_staff_authenticated()
        is_realtime = _is_realtime_monitoreo_path(path)
        is_bot = (not is_staff) and _is_likely_bot_ua()

        # Bloqueo temporal por comportamiento malicioso (ej. exceso de 404)
        if (not is_staff) and _cache_get(cache, f"scrape:block:{ip}"):
            abort(429, description="IP temporalmente bloqueada por actividad sospechosa. Intenta más tarde.")

        bot_factor = 0.5 if is_bot else 1.0

        if path in NORMALIZED_LOGIN_PATHS:
            # Los límites de login se manejan en _anti_bruteforce_login.
            return

        principal = f"user:{user_key}" if (is_auth and user_key) else f"ip:{ip}"

        # 1) Global por actor (IP para público, usuario para autenticados)
        if is_staff and is_realtime:
            global_window = max(10, STAFF_REALTIME_WINDOW_SECONDS)
            global_max = max(120, int(STAFF_REALTIME_MAX_REQ * (0.8 if is_bot else 1.0)))
            global_scope = "staff_realtime"
            global_msg = (
                "Hay demasiadas actualizaciones en tiempo real en este momento. "
                "Espera unos segundos e intenta nuevamente."
            )
        elif is_staff:
            global_window = max(10, STAFF_WORK_WINDOW_SECONDS)
            global_max = max(120, int(STAFF_WORK_MAX_REQ * (0.8 if is_bot else 1.0)))
            global_scope = "staff_work"
            global_msg = (
                "Estas realizando demasiadas acciones muy rapido. "
                "Espera unos segundos e intenta nuevamente."
            )
        elif is_auth:
            global_window = max(10, AUTH_WORK_WINDOW_SECONDS)
            global_max = max(60, int(AUTH_WORK_MAX_REQ * bot_factor))
            global_scope = "auth_work"
            global_msg = "Exceso de solicitudes para la sesion actual. Espera unos segundos e intenta nuevamente."
        else:
            global_window = max(10, SCRAPE_GLOBAL_WINDOW_SECONDS)
            global_max = max(10, int(SCRAPE_GLOBAL_MAX_REQ * bot_factor))
            global_scope = "public"
            global_msg = "Límite global de solicitudes excedido. Reduce la frecuencia e intenta luego."

        gcount = _bucket_inc(f"scrape:global:{global_scope}:{principal}", global_window)
        if gcount > global_max:
            abort(429, description=global_msg)

        # 2) Límites estrictos por grupo de rutas
        group = _match_scrape_group(path, endpoint)
        if group:
            if is_staff:
                if is_realtime:
                    group_limit = max(120, STAFF_REALTIME_MAX_REQ)
                    group_window = max(10, STAFF_REALTIME_WINDOW_SECONDS)
                else:
                    staff_limits = {
                        "list_json": STAFF_LIST_MAX_REQ_USER,
                        "admin": STAFF_WORK_MAX_REQ,
                        "clientes": STAFF_WORK_MAX_REQ,
                        "upload": STAFF_WORK_MAX_REQ,
                        "reports": STAFF_WORK_MAX_REQ,
                    }
                    group_limit = max(80, int(staff_limits.get(group, STAFF_WORK_MAX_REQ)))
                    group_window = max(10, STAFF_WORK_WINDOW_SECONDS)

                group_count = _bucket_inc(
                    f"scrape:{group}:staff:{principal}",
                    group_window,
                )
                if group_count > group_limit:
                    abort(
                        429,
                        description=(
                            "Estas realizando demasiadas acciones muy rapido. "
                            "Espera unos segundos e intenta nuevamente."
                        ),
                    )

                if _is_admin_critical_path(path, request.method):
                    critical_count = _bucket_inc(
                        f"scrape:admin_critical:{principal}",
                        max(10, ADMIN_CRITICAL_WINDOW_SECONDS),
                    )
                    if critical_count > max(10, ADMIN_CRITICAL_MAX_REQ):
                        abort(
                            429,
                            description=(
                                "Demasiadas acciones administrativas sensibles en poco tiempo. "
                                "Espera unos segundos e intenta nuevamente."
                            ),
                        )
            elif is_auth:
                auth_limits = {
                    "list_json": AUTH_WORK_MAX_REQ,
                    "admin": AUTH_WORK_MAX_REQ,
                    "clientes": AUTH_WORK_MAX_REQ,
                    "upload": max(30, AUTH_WORK_MAX_REQ // 2),
                    "reports": max(30, AUTH_WORK_MAX_REQ // 2),
                }
                auth_limit = max(30, int(auth_limits.get(group, AUTH_WORK_MAX_REQ) * bot_factor))
                auth_count = _bucket_inc(
                    f"scrape:{group}:auth:{principal}",
                    max(10, AUTH_WORK_WINDOW_SECONDS),
                )
                if auth_count > auth_limit:
                    abort(
                        429,
                        description="Exceso de solicitudes para la sesion actual. Espera unos segundos e intenta nuevamente.",
                    )
            else:
                limits = {
                    "list_json": SCRAPE_LIST_MAX_REQ,
                    "admin": SCRAPE_ADMIN_MAX_REQ,
                    "clientes": SCRAPE_CLIENTES_MAX_REQ,
                    "upload": SCRAPE_UPLOAD_MAX_REQ,
                    "reports": SCRAPE_REPORTS_MAX_REQ,
                }
                ip_limit = max(5, int(limits.get(group, SCRAPE_REPORTS_MAX_REQ) * bot_factor))
                ip_count = _bucket_inc(f"scrape:{group}:ip:{ip}", SCRAPE_STRICT_WINDOW_SECONDS)

                if ip_count > ip_limit:
                    abort(
                        429,
                        description=(
                            "Estas realizando demasiadas acciones muy rapido. "
                            "Espera unos segundos e intenta nuevamente."
                        ),
                    )

                # Caso especial pedido: endpoint JSON grande limitado también por usuario autenticado
                if group == "list_json" and user_key:
                    user_limit = max(10, int(SCRAPE_LIST_MAX_REQ_USER * bot_factor))
                    ucount = _bucket_inc(
                        f"scrape:{group}:user:{user_key}",
                        SCRAPE_STRICT_WINDOW_SECONDS
                    )
                    if ucount > user_limit:
                        abort(429, description="Demasiadas solicitudes para este usuario en listados JSON.")

                # 3) Slowdown progresivo cerca del límite (solo tráfico público/no autenticado)
                if SCRAPE_SLOWDOWN:
                    ratio = (ip_count / float(max(1, ip_limit)))
                    if ratio >= 0.8:
                        ms = min(1000, max(50, SCRAPE_SLOWDOWN_MS))
                        if ratio >= 0.95:
                            ms = min(1200, int(ms * 2))
                        time.sleep(ms / 1000.0)

    @app.after_request
    def _track_404_and_block(resp):
        try:
            path = (request.path or "")
            if _is_exempt_path(path):
                return resp

            if int(getattr(resp, "status_code", 200)) != 404:
                return resp

            ip = _get_client_ip() or "0.0.0.0"
            c404 = _bucket_inc(f"scrape:404:{ip}", SCRAPE_404_WINDOW_SECONDS)
            if c404 > SCRAPE_404_MAX:
                _cache_set(
                    cache,
                    f"scrape:block:{ip}",
                    int(time.time()) + SCRAPE_BLOCK_SECONDS,
                    timeout=SCRAPE_BLOCK_SECONDS,
                )
        except Exception:
            pass
        return resp

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
        if not _operational_rate_limits_enabled():
            return
        if bool(app.config.get("TESTING")):
            return

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

        # Bloqueos por IP y por usuario global (sin depender del otro)
        block_key_ip = f"login:block:ip:{path}:{ip}"
        block_key_user = f"login:block:user:{path}:{raw_user}" if raw_user else None
        block_key_ip_user = f"login:block:ip_user:{path}:{ip}:{raw_user}" if raw_user else None

        # Contadores de fallos
        attempts_key_ip = f"login:fail:ip:{path}:{ip}"
        attempts_key_user = f"login:fail:user:{path}:{raw_user}" if raw_user else None
        attempts_key_ip_user = f"login:fail:ip_user:{path}:{ip}:{raw_user}" if raw_user else None

        # Rate limit por IP
        ip_1m = _bucket_inc(f"login:req:ip:1m:{path}:{ip}", 60)
        ip_1h = _bucket_inc(f"login:req:ip:1h:{path}:{ip}", 3600)
        if ip_1m > _login_rate_ip_1m() or ip_1h > _login_rate_ip_1h():
            _log_auth_throttle(
                "AUTH_LOGIN_RATE_LIMITED",
                ip=ip,
                path=path,
                username=raw_user,
                reason="ip_rate_limit_exceeded",
                metadata={"ip_1m": ip_1m, "ip_1h": ip_1h},
            )
            abort(
                429,
                description=(
                    "Demasiados intentos de acceso en poco tiempo. "
                    "Espera un momento y vuelve a intentar."
                ),
            )

        # Rate limit por usuario (si viene identificador)
        if raw_user:
            user_1m = _bucket_inc(f"login:req:user:1m:{path}:{raw_user}", 60)
            user_1h = _bucket_inc(f"login:req:user:1h:{path}:{raw_user}", 3600)
            if user_1m > _login_rate_user_1m() or user_1h > _login_rate_user_1h():
                _log_auth_throttle(
                    "AUTH_LOGIN_RATE_LIMITED",
                    ip=ip,
                    path=path,
                    username=raw_user,
                    reason="user_rate_limit_exceeded",
                    metadata={"user_1m": user_1m, "user_1h": user_1h},
                )
                abort(
                    429,
                    description=(
                        "Demasiados intentos de acceso en poco tiempo. "
                        "Espera un momento y vuelve a intentar."
                    ),
                )

        # bloqueado
        if _cache_get(cache, block_key_ip):
            _log_auth_throttle(
                "AUTH_LOGIN_BLOCKED",
                ip=ip,
                path=path,
                username=raw_user,
                reason="ip_temporarily_blocked",
            )
            abort(429)
        if block_key_user and _cache_get(cache, block_key_user):
            _log_auth_throttle(
                "AUTH_LOGIN_BLOCKED",
                ip=ip,
                path=path,
                username=raw_user,
                reason="user_temporarily_blocked",
            )
            abort(429)
        if block_key_ip_user and _cache_get(cache, block_key_ip_user):
            _log_auth_throttle(
                "AUTH_LOGIN_BLOCKED",
                ip=ip,
                path=path,
                username=raw_user,
                reason="ip_user_temporarily_blocked",
            )
            abort(429)

        # suma intento fallido potencial (se limpia al autenticar)
        attempts_ip = int(_cache_get(cache, attempts_key_ip) or 0) + 1
        _cache_set(cache, attempts_key_ip, attempts_ip, timeout=_login_window_seconds())

        attempts_user = 0
        if attempts_key_user:
            attempts_user = int(_cache_get(cache, attempts_key_user) or 0) + 1
            _cache_set(cache, attempts_key_user, attempts_user, timeout=_login_window_seconds())

        attempts_ip_user = 0
        if attempts_key_ip_user:
            attempts_ip_user = int(_cache_get(cache, attempts_key_ip_user) or 0) + 1
            _cache_set(cache, attempts_key_ip_user, attempts_ip_user, timeout=_login_window_seconds())

        max_fail = max(attempts_ip, attempts_user, attempts_ip_user)

        # Delay progresivo a partir de 5 fallos.
        delay_threshold = _login_delay_threshold()
        if max_fail >= delay_threshold:
            over = max_fail - delay_threshold + 1
            delay_ms = min(5000, max(100, int(_login_delay_ms_base() * over)))
            time.sleep(delay_ms / 1000.0)

        # Bloqueo temporal a partir de 10 fallos (IP o usuario)
        if max_fail >= _login_block_threshold():
            block_seconds = _login_block_seconds()
            _cache_set(
                cache,
                block_key_ip,
                int(time.time()) + block_seconds,
                timeout=block_seconds
            )
            if block_key_user:
                _cache_set(
                    cache,
                    block_key_user,
                    int(time.time()) + block_seconds,
                    timeout=block_seconds
                )
            if block_key_ip_user:
                _cache_set(
                    cache,
                    block_key_ip_user,
                    int(time.time()) + block_seconds,
                    timeout=block_seconds
                )
            _log_auth_throttle(
                "AUTH_LOGIN_BLOCKED",
                ip=ip,
                path=path,
                username=raw_user,
                reason="login_fail_threshold_reached",
                metadata={"max_fail": max_fail, "block_seconds": block_seconds},
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

        _cache_delete(cache, f"login:fail:ip:{path}:{ip}")
        _cache_delete(cache, f"login:block:ip:{path}:{ip}")

        if username:
            _cache_delete(cache, f"login:fail:user:{path}:{username}")
            _cache_delete(cache, f"login:block:user:{path}:{username}")
            _cache_delete(cache, f"login:fail:ip_user:{path}:{ip}:{username}")
            _cache_delete(cache, f"login:block:ip_user:{path}:{ip}:{username}")

    app.extensions["clear_login_attempts"] = clear_login_attempts
