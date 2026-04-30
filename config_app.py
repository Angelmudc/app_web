# config_app.py
# -*- coding: utf-8 -*-

import os
import re
import json
import secrets
import click
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlsplit

from flask import Flask, request, redirect, url_for, abort, session, render_template, g, jsonify
from datetime import timedelta
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException

from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_migrate import Migrate
from flask_login import LoginManager, logout_user
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf import CSRFProtect

from sqlalchemy import text, func, inspect as sa_inspect
from sqlalchemy.pool import NullPool
from utils.compat_engine import format_compat_result
from utils.funciones_formatter import format_funciones, format_funciones_display
from utils.timezone import (
    format_rd_datetime,
    now_rd,
    rd_today,
    to_rd,
    utc_now_naive,
)
from utils.secrets_manager import get_secret
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    # Carga .env local sin sobrescribir variables ya definidas por el entorno (prod/panel).
    _DOTENV_PATH = Path(__file__).resolve().parent / ".env"
    if _DOTENV_PATH.exists():
        load_dotenv(dotenv_path=_DOTENV_PATH, override=False)

# Instancias globales
# ─────────────────────────────────────────────────────────────
db = SQLAlchemy()
cache = Cache()
migrate = Migrate()
csrf = CSRFProtect()

# ─────────────────────────────────────────────────────────────
# Utilidad: normalizar cédula (devuelve 11 dígitos sin guiones)
# ─────────────────────────────────────────────────────────────
CEDULA_PATTERN = re.compile(r"^\d{11}$")


def normalize_cedula(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    return digits if CEDULA_PATTERN.fullmatch(digits) else None


# ─────────────────────────────────────────────────────────────
# Utilidades DB: normalizar DATABASE_URL y asegurar SSL
# ─────────────────────────────────────────────────────────────
def _normalize_db_url(url: str) -> str:
    """
    - Acepta 'postgres://...' y lo convierte a 'postgresql+psycopg2://...'
    - Asegura 'sslmode=require' en la querystring (para Render/Supabase/Neon/etc).
    """
    if not url:
        raise RuntimeError("DATABASE_URL no configurada.")

    url = url.strip()

    # Permite SQLite para testing local/pytest sin forzar parámetros de Postgres.
    if url.startswith("sqlite:"):
        return url

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    # Asegurar sslmode=require en URL
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    return url


def _is_true(v: str) -> bool:
    return (str(v or "").strip().lower() in ("1", "true", "yes", "on"))


def _detect_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()


def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # ✅ Permite que las rutas funcionen con y sin slash final.
    app.url_map.strict_slashes = False

    env = _detect_env()
    prod = env in ("prod", "production")

    # Detectar si estamos corriendo en Render (para decidir cookies Secure sin romper local)
    IS_RENDER = bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_SERVICE_NAME")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("RENDER_INTERNAL_HOSTNAME")
    )

    # Permite forzar manualmente cookie secure (por si usas otro hosting)
    FORCE_COOKIE_SECURE = _is_true(os.getenv("FORCE_COOKIE_SECURE", ""))

    def _env_str(name: str, default: str) -> str:
        return (os.getenv(name) or default).strip()

    def _split_csv(raw: str) -> list[str]:
        parts = []
        for item in (raw or "").split(","):
            val = item.strip()
            if val:
                parts.append(val)
        return parts

    def _normalize_origin(raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        scheme = (parsed.scheme or "").strip().lower()
        netloc = (parsed.netloc or "").strip().lower()
        if scheme not in {"http", "https"} or not netloc:
            return ""
        return f"{scheme}://{netloc}"

    def _public_base_url() -> str:
        """
        Origen público canónico para links compartibles.
        En producción exige HTTPS para reducir riesgo de phishing/spoofing.
        """
        fallback = "https://www.domesticadelcibao.com"
        raw = _env_str("PUBLIC_BASE_URL", fallback)
        normalized = _normalize_origin(raw)
        if not normalized:
            return fallback
        parsed = urlsplit(normalized)
        if prod and (parsed.scheme or "").lower() != "https":
            return fallback
        return normalized

    def _default_cors_origins() -> set[str]:
        out = set()
        public_origin = _normalize_origin(_env_str("PUBLIC_BASE_URL", "https://www.domesticadelcibao.com"))
        if public_origin:
            out.add(public_origin)
        if not prod:
            out.update(
                {
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:8080",
                    "http://127.0.0.1:8080",
                }
            )
        return out

    # ─────────────────────────────────────────────────────────
    # Seguridad de sesión/cookies
    # ─────────────────────────────────────────────────────────
    # OJO:
    # En Render, FLASK_RUN_HOST casi nunca existe.
    # Por eso detectamos "localhost" de forma robusta.
    def _is_local_request_host() -> bool:
        try:
            host = (request.host or "").split(":")[0].strip().lower()
            return host in ("127.0.0.1", "localhost")
        except Exception:
            # fallback seguro
            return False

    # Si NO hay request context (en startup), usamos variable opcional
    is_localhost_flag = (os.getenv("IS_LOCALHOST", "").strip().lower() in ("1", "true", "yes", "on"))

    default_secret = "dev-only-secret-change-me"
    app.config["SECRET_KEY"] = (
        get_secret("FLASK_SECRET_KEY", required=prod)
        or default_secret
    )

    # Cookies secure:
    # - En prod: True (pero SOLO si estás bajo HTTPS real)
    # - En local: False (para no romper CSRF/cookies)
    # Nota: ProxyFix + request.is_secure te ayuda a detectar HTTPS real detrás de proxy.
    def _cookie_secure() -> bool:
        if FORCE_COOKIE_SECURE:
            return True

        if not prod:
            return False

        # En produccion aplicamos postura fail-closed y usamos override local por host.
        if _is_true(os.getenv("ALLOW_INSECURE_PROD_COOKIES", "")):
            return False
        return True

    app.config.update(
        {
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": _env_str("SESSION_COOKIE_SAMESITE", "Lax"),
            "SESSION_COOKIE_DOMAIN": None,
            "SESSION_COOKIE_SECURE": _cookie_secure(),
            "SESSION_PERMANENT": False,
            "SESSION_COOKIE_MAX_AGE": None,
            "SESSION_REFRESH_EACH_REQUEST": False,
            "REMEMBER_COOKIE_REFRESH_EACH_REQUEST": False,
            "REMEMBER_COOKIE_HTTPONLY": True,
            # Evita que el remember cookie se "muera" instantáneo (0s puede crear comportamientos raros)
            "REMEMBER_COOKIE_DURATION": timedelta(days=int(os.getenv("REMEMBER_DAYS", "7"))),
            "PREFERRED_URL_SCHEME": "https" if prod else "http",

            # Sesión (Flask espera timedelta, no int)
            "PERMANENT_SESSION_LIFETIME": timedelta(
                seconds=int(os.getenv("SESSION_TTL_SECONDS", "2592000"))
            ),

            "SESSION_COOKIE_NAME": _env_str("SESSION_COOKIE_NAME", "app_web_session"),
            "REMEMBER_COOKIE_SAMESITE": _env_str("REMEMBER_COOKIE_SAMESITE", "Lax"),
            "REMEMBER_COOKIE_SECURE": _cookie_secure(),
            # Dominio público canónico para metadatos compartibles (OG/Twitter/links públicos).
            "PUBLIC_BASE_URL": _public_base_url(),
            # Canal de soporte por WhatsApp (sin hardcode en rutas/templates).
            "SUPPORT_WHATSAPP_NUMBER": _env_str("SUPPORT_WHATSAPP_NUMBER", "18094296892"),
            "SUPPORT_WHATSAPP_DISPLAY": _env_str("SUPPORT_WHATSAPP_DISPLAY", "+1 809 429 6892"),
            # Feature flag rollout: navegación parcial clientes (infra, sin activar comportamiento aún).
            "CLIENTES_PARTIAL_NAV_ENABLED": _is_true(os.getenv("CLIENTES_PARTIAL_NAV_ENABLED", "0")),
            # CSV de rutas piloto exactas (ej: "/clientes/informacion,/clientes/planes").
            "CLIENTES_PARTIAL_NAV_PILOT_ROUTES": _env_str("CLIENTES_PARTIAL_NAV_PILOT_ROUTES", ""),
            # Lista derivada del CSV para inspección/diagnóstico de config.
            "CLIENTES_PARTIAL_NAV_PILOT_ROUTES_LIST": _split_csv(
                _env_str("CLIENTES_PARTIAL_NAV_PILOT_ROUTES", "")
            ),
            # Realtime clientes: en producción apagado por defecto para evitar SSE largo sobre workers sync.
            "CLIENTES_LIVE_SSE_ENABLED": _is_true(
                os.getenv("CLIENTES_LIVE_SSE_ENABLED", "0" if prod else "1")
            ),
            # Realtime admin: en producción apagado por defecto para evitar SSE largo sobre workers sync.
            "ADMIN_LIVE_SSE_ENABLED": _is_true(
                os.getenv("ADMIN_LIVE_SSE_ENABLED", "0" if prod else "1")
            ),
        }
    )

    configured_cors = _split_csv(os.getenv("CORS_ALLOWED_ORIGINS", ""))
    cors_allowed_origins = {
        origin for origin in (_normalize_origin(x) for x in configured_cors) if origin
    }
    if not cors_allowed_origins:
        cors_allowed_origins = _default_cors_origins()

    cors_allowed_methods = {
        m.upper()
        for m in _split_csv(os.getenv("CORS_ALLOWED_METHODS", "GET,POST,PUT,PATCH,DELETE,OPTIONS"))
        if m
    }
    if not cors_allowed_methods:
        cors_allowed_methods = {"GET", "POST", "OPTIONS"}

    cors_allowed_headers = {
        h.lower()
        for h in _split_csv(
            os.getenv(
                "CORS_ALLOWED_HEADERS",
                "Content-Type,Authorization,X-CSRFToken,X-CSRF-Token,X-Requested-With",
            )
        )
        if h
    }
    if not cors_allowed_headers:
        cors_allowed_headers = {"content-type"}

    app.config["CORS_ALLOWED_ORIGINS"] = sorted(cors_allowed_origins)
    app.config["CORS_ALLOWED_METHODS"] = sorted(cors_allowed_methods)
    app.config["CORS_ALLOWED_HEADERS"] = sorted(cors_allowed_headers)
    app.config["CORS_ALLOW_CREDENTIALS"] = _is_true(os.getenv("CORS_ALLOW_CREDENTIALS", "1"))
    try:
        cors_max_age = max(60, int((os.getenv("CORS_MAX_AGE_SECONDS") or "600").strip()))
    except Exception:
        cors_max_age = 600
    app.config["CORS_MAX_AGE_SECONDS"] = cors_max_age

    # ✅ Limitar tamaño de requests (evita payloads gigantes)
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024)))  # 4MB total request
    try:
        app_max_file_mb = float((os.getenv("APP_MAX_FILE_MB") or "3").strip())
    except Exception:
        app_max_file_mb = 3.0
    if app_max_file_mb <= 0:
        app_max_file_mb = 3.0
    app.config["APP_MAX_FILE_MB"] = app_max_file_mb
    app.config["APP_MAX_FILE_BYTES"] = int(app_max_file_mb * 1024 * 1024)

    # ─────────────────────────────────────────────────────────
    # CSRF
    # ─────────────────────────────────────────────────────────
    # En local con http, SSL_STRICT debe ser False.
    # En prod, normalmente sí.
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["WTF_CSRF_SSL_STRICT"] = (prod and _cookie_secure() and IS_RENDER)
    app.config["WTF_CSRF_TIME_LIMIT"] = int(os.getenv("WTF_CSRF_TIME_LIMIT", "28800"))  # 8 horas
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-Token"]
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    csrf.init_app(app)

    # ─────────────────────────────────────────────────────────
    # ProxyFix (Render / reverse proxy)
    # ─────────────────────────────────────────────────────────
    # Esto es clave para request.is_secure, host, ip, etc.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # ─────────────────────────────────────────────────────────
    # Override LOCAL (evita loops de login cuando en tu .env hay vars de Render)
    # Si estás entrando por http://localhost o http://127.0.0.1, el navegador
    # IGNORA cookies con atributo Secure. Eso rompe la sesión y te devuelve a /login.
    #
    # Esto mantiene seguridad "tipo empresa" en producción/HTTPS, pero garantiza
    # que en local funcione aunque tengas APP_ENV=production o variables RENDER en .env.
    # ─────────────────────────────────────────────────────────
    @app.before_request
    def _local_cookie_override():
        # Si el usuario fuerza Secure, respetarlo
        if FORCE_COOKIE_SECURE:
            return

        # Si estamos en localhost, no usar cookies Secure y no exigir CSRF por SSL
        if _is_local_request_host() or is_localhost_flag:
            app.config["SESSION_COOKIE_SECURE"] = False
            app.config["REMEMBER_COOKIE_SECURE"] = False
            app.config["WTF_CSRF_SSL_STRICT"] = False

    def _origin_allowed(origin: str) -> bool:
        normalized = _normalize_origin(origin)
        return bool(normalized and normalized in cors_allowed_origins)

    def _append_vary(resp, value: str):
        existing = (resp.headers.get("Vary") or "").strip()
        values = [x.strip() for x in existing.split(",") if x.strip()]
        if value not in values:
            values.append(value)
        resp.headers["Vary"] = ", ".join(values)

    def _apply_cors_headers(resp, origin: str, requested_headers: Optional[List[str]] = None):
        normalized = _normalize_origin(origin)
        if not normalized:
            return resp
        resp.headers["Access-Control-Allow-Origin"] = normalized
        _append_vary(resp, "Origin")
        if bool(app.config.get("CORS_ALLOW_CREDENTIALS")):
            resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = ", ".join(sorted(cors_allowed_methods))
        if requested_headers:
            resp.headers["Access-Control-Allow-Headers"] = ", ".join(requested_headers)
        else:
            resp.headers["Access-Control-Allow-Headers"] = ", ".join(sorted(h for h in cors_allowed_headers))
        resp.headers["Access-Control-Max-Age"] = str(int(app.config.get("CORS_MAX_AGE_SECONDS", 600)))
        resp.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
        return resp

    @app.before_request
    def _cors_preflight_guard():
        origin = (request.headers.get("Origin") or "").strip()
        if not origin:
            return None

        if request.method != "OPTIONS":
            return None

        requested_method = (request.headers.get("Access-Control-Request-Method") or "").strip().upper()
        if not requested_method:
            return None

        if not _origin_allowed(origin):
            abort(403, description="Origen CORS no permitido.")

        if requested_method not in cors_allowed_methods:
            abort(405, description="Metodo CORS no permitido.")

        requested_header_values = []
        req_headers = (request.headers.get("Access-Control-Request-Headers") or "").strip()
        for header in req_headers.split(","):
            h = header.strip()
            if not h:
                continue
            if h.lower() not in cors_allowed_headers:
                abort(400, description="Header CORS no permitido.")
            requested_header_values.append(h)

        resp = app.make_default_options_response()
        resp.status_code = 204
        return _apply_cors_headers(resp, origin=origin, requested_headers=requested_header_values)

    # ─────────────────────────────────────────────────────────
    # Base de datos (Postgres en prod, SQLite aislada para tests)
    # ─────────────────────────────────────────────────────────
    raw_db_url = (
        get_secret("DATABASE_URL", required=prod)
        or ""
    ).strip()
    if env in ("test", "testing"):
        # En tests siempre usar SQLite aislada para evitar tocar BD real.
        if not raw_db_url.startswith("sqlite:"):
            raw_db_url = "sqlite:///:memory:"
    db_url = _normalize_db_url(raw_db_url)
    is_sqlite = db_url.startswith("sqlite:")

    pool_mode = (os.getenv("DB_POOL_MODE", "") or "").lower()
    use_null_pool = pool_mode in ("pgbouncer", "nullpool", "off")

    if is_sqlite:
        engine_opts = {
            "pool_pre_ping": True,
        }
    else:
        connect_args = {
            "sslmode": "require",
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "8")),
            "keepalives": int(os.getenv("DB_KEEPALIVES", "1")),
            "keepalives_idle": int(os.getenv("DB_KEEPALIVES_IDLE", "30")),
            "keepalives_interval": int(os.getenv("DB_KEEPALIVES_INTERVAL", "10")),
            "keepalives_count": int(os.getenv("DB_KEEPALIVES_COUNT", "3")),
            "application_name": os.getenv("DB_APPNAME", "app_web"),
        }

        engine_opts = {
            "pool_pre_ping": True,
            "pool_reset_on_return": "rollback",
            "connect_args": connect_args,
        }

        if use_null_pool:
            engine_opts["poolclass"] = NullPool
        else:
            engine_opts.update(
                {
                    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "300")),
                    "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
                    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
                    "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
                }
            )

    app.config.update(
        {
            "SQLALCHEMY_DATABASE_URI": db_url,
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SQLALCHEMY_ENGINE_OPTIONS": engine_opts,

            "TEMPLATES_AUTO_RELOAD": not prod,
            "JSON_SORT_KEYS": False,
        }
    )

    cache_type_env = (os.getenv("CACHE_TYPE") or "").strip().lower()
    redis_url = (
        (os.getenv("BACKPLANE_REDIS_URL") or "").strip()
        or (os.getenv("REDIS_URL") or "").strip()
        or (os.getenv("CACHE_REDIS_URL") or "").strip()
    )
    autodetected_enabled = bool(redis_url or cache_type_env in {"redis", "rediscache"})

    explicit_enabled_raw = (
        os.getenv("ENTERPRISE_BACKPLANE_ENABLED")
        if os.getenv("ENTERPRISE_BACKPLANE_ENABLED") is not None
        else os.getenv("DISTRIBUTED_BACKPLANE_ENABLED")
    )
    explicit_enabled = None
    if explicit_enabled_raw is not None and str(explicit_enabled_raw).strip() != "":
        explicit_enabled = _is_true(str(explicit_enabled_raw))

    distributed_backplane_enabled = bool(autodetected_enabled if explicit_enabled is None else explicit_enabled)

    required_raw = (
        os.getenv("ENTERPRISE_BACKPLANE_REQUIRED")
        if os.getenv("ENTERPRISE_BACKPLANE_REQUIRED") is not None
        else os.getenv("DISTRIBUTED_BACKPLANE_REQUIRED", "0")
    )
    distributed_backplane_required = _is_true(str(required_raw))

    strict_runtime_raw = (
        os.getenv("ENTERPRISE_BACKPLANE_STRICT_RUNTIME")
        if os.getenv("ENTERPRISE_BACKPLANE_STRICT_RUNTIME") is not None
        else os.getenv("DISTRIBUTED_BACKPLANE_STRICT_RUNTIME", "0")
    )
    strict_runtime_requested = _is_true(str(strict_runtime_raw))

    if distributed_backplane_enabled and not redis_url:
        distributed_backplane_enabled = False
        app.logger.warning(
            "Backplane distribuido solicitado pero no hay URL Redis; se usa modo degradado local."
        )
    if distributed_backplane_required and not distributed_backplane_enabled:
        raise RuntimeError(
            "Backplane requerido (ENTERPRISE_BACKPLANE_REQUIRED/DISTRIBUTED_BACKPLANE_REQUIRED=1) sin Redis disponible."
        )

    strict_runtime = bool(strict_runtime_requested and distributed_backplane_enabled)

    cache_config = {
        "CACHE_DEFAULT_TIMEOUT": int(os.getenv("CACHE_DEFAULT_TIMEOUT", "120")),
    }
    if distributed_backplane_enabled:
        cache_config.update(
            {
                "CACHE_TYPE": "RedisCache",
                "CACHE_REDIS_URL": redis_url,
                "CACHE_KEY_PREFIX": (os.getenv("CACHE_KEY_PREFIX") or "app_web:").strip(),
            }
        )
    else:
        cache_config.update({"CACHE_TYPE": "simple"})

    app.config.update(cache_config)
    app.config["DISTRIBUTED_BACKPLANE_ENABLED"] = bool(distributed_backplane_enabled)
    app.config["ENTERPRISE_BACKPLANE_ENABLED"] = bool(distributed_backplane_enabled)
    app.config["DISTRIBUTED_BACKPLANE_REQUIRED"] = bool(distributed_backplane_required)
    app.config["DISTRIBUTED_BACKPLANE_STRICT_RUNTIME"] = bool(strict_runtime)
    app.config["DISTRIBUTED_BACKPLANE_MODE"] = "redis" if distributed_backplane_enabled else "disabled"
    app.config["DISTRIBUTED_BACKPLANE_HEALTHY_AT_STARTUP"] = False

    # ─────────────────────────────────────────────────────────
    # Inicializar extensiones
    # ─────────────────────────────────────────────────────────
    db.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)

    if distributed_backplane_enabled:
        from utils.distributed_backplane import bp_healthcheck

        backplane_ok = bool(bp_healthcheck(strict=distributed_backplane_required, app_obj=app))
        if not backplane_ok and distributed_backplane_required:
            raise RuntimeError("Redis backplane requerido no disponible.")
        app.config["DISTRIBUTED_BACKPLANE_HEALTHY_AT_STARTUP"] = backplane_ok
        if not backplane_ok:
            app.config["DISTRIBUTED_BACKPLANE_MODE"] = "degraded_unavailable"

    # Importar modelos para Alembic/Migrate
    try:
        import models  # noqa: F401
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────
    # Seguridad extra (anti brute-force + headers suaves)
    # ─────────────────────────────────────────────────────────
    from utils.security_layer import init_security
    init_security(app, cache)


    @app.after_request
    def _set_security_headers(resp):
        # No sobreescribir si ya existe (por capas: security_layer también setea)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-XSS-Protection", "0")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        try:
            resp.headers.pop("Server", None)
            resp.headers.pop("X-Powered-By", None)
        except Exception:
            pass

        # CSP se maneja en utils/security_layer.py (una sola fuente de verdad)

        # HSTS SOLO si estás en prod y bajo HTTPS real (ProxyFix ayuda)
        # Si tu sitio abre por HTTP (preview/local), no lo fuerces.
        try:
            if prod and request.is_secure:
                resp.headers.setdefault(
                    "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                )
        except Exception:
            pass

        # Cross-origin isolation opcional
        enable_xoi = _is_true(os.getenv("ENABLE_CROSS_ORIGIN_ISOLATION", ""))
        if prod and enable_xoi:
            resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
            resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
            resp.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")

        try:
            origin = (request.headers.get("Origin") or "").strip()
            if origin and _origin_allowed(origin):
                _apply_cors_headers(resp, origin=origin)
        except Exception:
            pass

        return resp

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_entity_too_large(_err):
        msg = "El archivo o la solicitud excede el límite permitido. Reduce el tamaño e intenta de nuevo."
        wants_json = False
        try:
            wants_json = bool(request.accept_mimetypes.best == "application/json")
        except Exception:
            wants_json = False
        if wants_json:
            return {"error": msg}, 413

        try:
            from flask import flash
            flash(msg, "danger")
            return redirect(request.url)
        except Exception:
            return msg, 413

    @app.before_request
    def _assign_request_id():
        try:
            rid = (request.headers.get("X-Request-ID") or "").strip()[:120]
            if rid:
                request.request_id = rid
        except Exception:
            pass

    def _raw_error_event(error_type: str, err: str, code: int = 500):
        try:
            from utils.enterprise_layer import log_error_event

            log_error_event(
                error_type=(error_type or "SERVER_ERROR")[:60],
                exc=(err or "Unhandled error"),
                route=(request.path or "")[:255] or None,
                request_id=(getattr(request, "request_id", None) or "")[:120] or None,
                status_code=int(code or 500),
            )
        except Exception:
            pass

    def _http_status_code_from_err(err, default: int = 500) -> int:
        try:
            raw = getattr(err, "code", default)
            return int(raw)
        except Exception:
            return int(default)

    @app.errorhandler(Exception)
    def _handle_unexpected_error(err):
        if isinstance(err, HTTPException):
            code = int(getattr(err, "code", 500) or 500)
            if code < 500:
                return err
        _raw_error_event(
            error_type="SERVER_ERROR",
            err=(str(err) or "Unhandled error"),
            code=_http_status_code_from_err(err, 500),
        )
        try:
            g._error_event_logged = True
        except Exception:
            pass
        wants_json = False
        try:
            best = (request.accept_mimetypes.best or "").strip().lower()
            wants_json = (best == "application/json") or request.path.endswith(".json")
        except Exception:
            wants_json = False
        if wants_json:
            return {"error": "Ha ocurrido un error interno."}, 500
        return render_template("errors/500.html"), 500

    @app.after_request
    def _capture_http_5xx(resp):
        try:
            code = int(getattr(resp, "status_code", 0) or 0)
            if code >= 500 and not bool(getattr(g, "_error_event_logged", False)):
                _raw_error_event(
                    error_type="SERVER_ERROR",
                    err=f"HTTP {code}",
                    code=code,
                )
                g._error_event_logged = True
        except Exception:
            pass
        return resp

    @app.after_request
    def _capture_security_http_events(resp):
        try:
            code = int(getattr(resp, "status_code", 0) or 0)
            path = (request.path or "").strip()
            if (not path) or path.startswith("/static/"):
                return resp

            from utils.audit_logger import log_security_event

            if code == 403 and not bool(getattr(g, "_authz_denied_logged", False)):
                log_security_event(
                    event="AUTHZ_DENIED",
                    status="fail",
                    entity_type="security",
                    summary="Acceso denegado",
                    reason="http_403",
                    metadata={"path": path, "method": request.method, "status_code": 403},
                )
                g._authz_denied_logged = True

            is_validation_code = code in {400, 422}
            if is_validation_code and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                log_security_event(
                    event="VALIDATION_FAILED",
                    status="fail",
                    entity_type="validation",
                    summary="Validación de request fallida",
                    reason=f"http_{code}",
                    metadata={"path": path, "method": request.method, "status_code": code},
                )
        except Exception:
            pass
        return resp

    # ─────────────────────────────────────────────────────────
    # Helpers globales para templates
    # ─────────────────────────────────────────────────────────
    app.jinja_env.globals["now"] = now_rd
    app.jinja_env.globals["today_rd"] = rd_today
    app.jinja_env.globals["current_year"] = now_rd().year
    app.jinja_env.globals["rd_dt"] = format_rd_datetime
    app.jinja_env.globals["to_rd"] = to_rd
    app.jinja_env.filters["rd_datetime"] = format_rd_datetime
    app.jinja_env.globals["format_funciones"] = format_funciones
    app.jinja_env.filters["funciones_fmt"] = format_funciones
    app.jinja_env.globals["format_funciones_display"] = format_funciones_display
    app.jinja_env.filters["format_funciones_display"] = format_funciones_display
    app.jinja_env.globals["format_compat_result"] = format_compat_result
    app.jinja_env.filters["compat_fmt"] = format_compat_result
    app.jinja_env.globals["new_idempotency_key"] = lambda: secrets.token_urlsafe(24)

    # ─────────────────────────────────────────────────────────
    # Login manager (staff en BD + clientes)
    # ─────────────────────────────────────────────────────────
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.session_protection = "strong"

    @login_manager.unauthorized_handler
    def unauthorized_callback():
        next_url = request.full_path if request.full_path else request.path
        if request.path.startswith("/clientes"):
            return redirect(url_for("clientes.login", next=next_url))
        return redirect(url_for("admin.login", next=next_url))

    from utils.staff_auth import (
        BREAKGLASS_USER_ID,
        build_breakglass_user,
        clear_breakglass_session,
        is_breakglass_session_valid,
    )

    @login_manager.user_loader
    def load_user(user_id):
        try:
            from models import Cliente, StaffUser
        except Exception:
            return None

        uid = (str(user_id or "").strip())
        if not uid:
            return None

        if uid == BREAKGLASS_USER_ID:
            if is_breakglass_session_valid(session):
                return build_breakglass_user()
            try:
                logout_user()
            except Exception:
                pass
            try:
                clear_breakglass_session(session)
                session.clear()
            except Exception:
                pass
            return None

        # Staff en BD: se restaura solo si la sesión pertenece al panel admin.
        if uid.startswith("staff:"):
            if not bool(session.get("is_admin_session")):
                return None
            raw = uid.split(":", 1)[1].strip()
            if raw.isdigit():
                return StaffUser.query.get(int(raw))
            return None

        # Compatibilidad por si alguna sesión previa guardó solo el id numérico.
        if bool(session.get("is_admin_session")) and uid.isdigit():
            su = StaffUser.query.get(int(uid))
            if su is not None:
                return su

        try:
            if uid.isdigit():
                return Cliente.query.get(int(uid))
            return None
        except Exception:
            return None

    def _staff_password_min_len() -> int:
        try:
            return max(8, int((os.getenv("STAFF_PASSWORD_MIN_LEN") or "8").strip()))
        except Exception:
            return 8

    def _normalize_staff_role(role_raw: str) -> str:
        role = (role_raw or "").strip().lower()
        if role in {"owner", "admin", "secretaria"}:
            return role
        default_role = (os.getenv("ADMIN_DEFAULT_ROLE") or "secretaria").strip().lower()
        return default_role if default_role in {"owner", "admin", "secretaria"} else "secretaria"

    def _create_staff_user(username: str, role: str, password: str, email: str = "", is_active: bool = True):
        from models import StaffUser

        username_clean = (username or "").strip()
        email_clean = (email or "").strip().lower() or None
        role_clean = _normalize_staff_role(role)
        min_len = _staff_password_min_len()

        normalized_password = StaffUser.normalize_password(password)

        if not username_clean:
            raise click.ClickException("Debes indicar --username.")
        if not normalized_password or len(normalized_password) < min_len:
            raise click.ClickException(f"La contraseña debe tener al menos {min_len} caracteres.")

        exists_username = StaffUser.query.filter(
            func.lower(StaffUser.username) == username_clean.lower()
        ).first()
        if exists_username:
            raise click.ClickException("Ese username ya existe.")

        if email_clean:
            exists_email = StaffUser.query.filter(
                func.lower(StaffUser.email) == email_clean
            ).first()
            if exists_email:
                raise click.ClickException("Ese email ya existe.")

        u = StaffUser(username=username_clean, email=email_clean, role=role_clean, is_active=bool(is_active))
        u.set_password(normalized_password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Staff creado: username={u.username} role={u.role} id={u.id} active={u.is_active}")

    @app.cli.command("create-staff")
    @click.option("--username", required=True, help="Usuario interno (único).")
    @click.option("--role", required=False, default=lambda: os.getenv("ADMIN_DEFAULT_ROLE", "secretaria"), help="Rol: owner|admin|secretaria.")
    @click.option("--password", required=True, help="Contraseña inicial.")
    @click.option("--email", required=False, default="", help="Email opcional (único).")
    def create_staff_command(username: str, role: str, password: str, email: str):
        """Crea un usuario interno en staff_users."""
        _create_staff_user(username=username, role=role, password=password, email=email)

    @app.cli.command("create-secretaria")
    @click.option("--username", required=True, help="Usuario interno (único).")
    @click.option("--password", required=True, help="Contraseña inicial.")
    @click.option("--email", required=False, default="", help="Email opcional (único).")
    def create_secretaria_command(username: str, password: str, email: str):
        """Atajo para crear staff con rol secretaria."""
        _create_staff_user(username=username, role="secretaria", password=password, email=email)

    @app.cli.command("create-owner")
    @click.option("--username", required=True, help="Usuario interno (único).")
    @click.option("--password", required=True, help="Contraseña inicial.")
    @click.option("--email", required=False, default="", help="Email opcional (único).")
    def create_owner_command(username: str, password: str, email: str):
        """Atajo para crear staff con rol owner."""
        _create_staff_user(username=username, role="owner", password=password, email=email)

    @app.cli.command("create-emergency-admin")
    @click.option("--username", required=True, help="Username del admin de emergencia.")
    @click.option("--email", required=False, default="", help="Email opcional.")
    @click.option("--password", required=True, help="Contraseña.")
    @click.option("--inactive/--active", default=True, help="Por defecto se crea inactivo.")
    def create_emergency_admin_command(username: str, email: str, password: str, inactive: bool):
        """Crea un admin de emergencia en staff_users (inactivo por defecto)."""
        _create_staff_user(
            username=username,
            role="admin",
            password=password,
            email=email,
            is_active=(not bool(inactive)),
        )

    @app.cli.command("set-staff-active")
    @click.option("--username", required=True, help="Username o email del staff.")
    @click.option("--active", required=True, type=int, help="1=activo, 0=inactivo.")
    def set_staff_active_command(username: str, active: int):
        """Activa o desactiva un usuario staff por username/email."""
        from models import StaffUser

        ident = (username or "").strip().lower()
        if not ident:
            raise click.ClickException("Debes indicar --username.")

        user = StaffUser.query.filter(
            (func.lower(StaffUser.username) == ident) | (func.lower(StaffUser.email) == ident)
        ).first()
        if not user:
            raise click.ClickException("No se encontró usuario staff con ese username/email.")

        user.is_active = bool(int(active) == 1)
        db.session.commit()
        click.echo(f"Actualizado: id={user.id} username={user.username} active={user.is_active}")

    @app.cli.command("audit-staff-passwords")
    def audit_staff_passwords_command():
        """Audita hashes de staff sin modificar datos."""
        from models import StaffUser

        users = StaffUser.query.order_by(StaffUser.id.asc()).all()
        total = len(users)
        suspicious = []

        def _reason_for_hash(raw_hash: str) -> str:
            h = (raw_hash or "").strip()
            if not h:
                return "empty_hash"
            if h == "DISABLED_RESET_REQUIRED":
                return "disabled_reset_required"
            if "$" not in h:
                return "not_kdf_format"
            if h.startswith(("pbkdf2:", "scrypt:", "argon2:")):
                return ""
            return "unknown_scheme"

        for u in users:
            reason = _reason_for_hash(getattr(u, "password_hash", ""))
            if reason:
                suspicious.append(
                    {
                        "id": int(u.id),
                        "username": (u.username or ""),
                        "role": (u.role or ""),
                        "active": bool(u.is_active),
                        "reason": reason,
                    }
                )

        dup_rows = (
            db.session.query(func.lower(StaffUser.username).label("u"), func.count(StaffUser.id).label("n"))
            .group_by(func.lower(StaffUser.username))
            .having(func.count(StaffUser.id) > 1)
            .all()
        )
        dup_usernames = [{"username_norm": str(r.u), "count": int(r.n)} for r in dup_rows]

        click.echo(f"staff_total={total}")
        click.echo(f"suspicious_hashes={len(suspicious)}")
        for row in suspicious:
            click.echo(
                f"- id={row['id']} username={row['username']} role={row['role']} "
                f"active={int(row['active'])} reason={row['reason']}"
            )
        click.echo(f"case_insensitive_username_duplicates={len(dup_usernames)}")
        for row in dup_usernames:
            click.echo(f"- username_norm={row['username_norm']} count={row['count']}")

    from utils.outbox_relay import outbox_relay_cli
    app.cli.add_command(outbox_relay_cli)

    @app.cli.group("operational-snapshots")
    def operational_snapshots_group():
        """Snapshots operativos O2 (retención mínima y tendencias básicas)."""

    @operational_snapshots_group.command("capture")
    @click.option("--once", is_flag=True, default=False, help="Compatibilidad operativa; en CLI siempre es una ejecución.")
    @click.option("--window-minutes", default=15, show_default=True, type=int, help="Ventana O1 para capturar métricas.")
    @click.option("--skip-cleanup", is_flag=True, default=False, help="No ejecutar limpieza por retención después de capturar.")
    def operational_snapshots_capture_command(once: bool, window_minutes: int, skip_cleanup: bool):
        """Captura un snapshot operativo puntual para cron/scheduler externo."""
        from utils.enterprise_layer import operational_snapshot_capture

        out = operational_snapshot_capture(
            window_minutes=max(5, min(int(window_minutes), 120)),
            cleanup=(not bool(skip_cleanup)),
        )
        click.echo(
            f"snapshot_id={int(out.get('snapshot_id', 0))} "
            f"captured_at={out.get('captured_at')} "
            f"window_minutes={int(out.get('window_minutes', 15))} "
            f"pruned={int(out.get('pruned', 0))} "
            f"once={int(bool(once))}"
        )

    @operational_snapshots_group.command("cleanup")
    @click.option("--retention-hours", default=0, show_default=True, type=int, help="Horas a retener; 0 usa la política O2.")
    def operational_snapshots_cleanup_command(retention_hours: int):
        """Limpia snapshots operativos fuera de retención."""
        from utils.enterprise_layer import cleanup_operational_snapshots

        hours = int(retention_hours) if int(retention_hours or 0) > 0 else None
        deleted = cleanup_operational_snapshots(retention_hours=hours)
        click.echo(f"deleted={int(deleted)}")

    def _seed_testing_staff_users() -> None:
        if env not in ("test", "testing"):
            return
        from models import StaffUser

        seed = [
            ("Owner", "owner", "admin123"),
            ("Cruz", "admin", "8998"),
            ("Karla", "secretaria", "9989"),
            ("Anyi", "secretaria", "0931"),
        ]
        for username, role, password in seed:
            exists = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
            if exists:
                continue
            u = StaffUser(username=username, role=role, is_active=True)
            u.set_password(password)
            db.session.add(u)
        db.session.commit()

    if env in ("test", "testing"):
        with app.app_context():
            from models import StaffUser, StaffAuditLog, StaffPresenceState, OperationalMetricSnapshot, TrustedDevice
            StaffUser.__table__.create(bind=db.engine, checkfirst=True)
            StaffAuditLog.__table__.create(bind=db.engine, checkfirst=True)
            StaffPresenceState.__table__.create(bind=db.engine, checkfirst=True)
            OperationalMetricSnapshot.__table__.create(bind=db.engine, checkfirst=True)
            TrustedDevice.__table__.create(bind=db.engine, checkfirst=True)
            try:
                col_names = {
                    c.get("name")
                    for c in sa_inspect(db.engine).get_columns("staff_users")
                    if c.get("name")
                }
                if "mfa_enabled" not in col_names:
                    db.session.execute(text("ALTER TABLE staff_users ADD COLUMN mfa_enabled BOOLEAN DEFAULT 0 NOT NULL"))
                if "mfa_secret" not in col_names:
                    db.session.execute(text("ALTER TABLE staff_users ADD COLUMN mfa_secret VARCHAR(512)"))
                if "mfa_last_timestep" not in col_names:
                    db.session.execute(text("ALTER TABLE staff_users ADD COLUMN mfa_last_timestep INTEGER"))
                trusted_col_names = {
                    c.get("name")
                    for c in sa_inspect(db.engine).get_columns("trusted_devices")
                    if c.get("name")
                }
                if "device_token_hash" not in trusted_col_names:
                    db.session.execute(text("ALTER TABLE trusted_devices ADD COLUMN device_token_hash VARCHAR(64)"))
                db.session.execute(
                    text(
                        "UPDATE trusted_devices "
                        "SET device_token_hash = device_fingerprint "
                        "WHERE device_token_hash IS NULL"
                    )
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
            _seed_testing_staff_users()

        @app.route("/_test/error", methods=["GET"])
        def _test_error_route():
            raise RuntimeError("forced_test_error")

    from utils.audit_logger import log_staff_post_fallback

    @app.after_request
    def _staff_audit_post_fallback(resp):
        return log_staff_post_fallback(resp)

    @app.route("/healthz", methods=["GET"])
    def public_healthz():
        return jsonify({"ok": True, "status": "ok"}), 200

    # ─────────────────────────────────────────────────────────
    # Blueprints
    # ─────────────────────────────────────────────────────────
    from admin.routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")

    from webadmin import webadmin_bp
    app.register_blueprint(webadmin_bp, url_prefix="/webadmin")

    from clientes import clientes_bp
    app.register_blueprint(clientes_bp, url_prefix="/clientes")

    from public import public_bp
    app.register_blueprint(public_bp)  # "/"

    from contratos import contratos_bp
    app.register_blueprint(contratos_bp)

    from reclutamiento_publico import reclutamiento_publico_bp
    app.register_blueprint(reclutamiento_publico_bp)

    from registro.routes import registro_bp
    app.register_blueprint(registro_bp, url_prefix="/registro")

    from reclutas import reclutas_bp
    app.register_blueprint(reclutas_bp)  # ya trae url_prefix="/reclutas"

    # ─────────────────────────────────────────────────────────
    # Config de entrevistas (si existe)
    # ─────────────────────────────────────────────────────────
    try:
        cfg_path = Path(app.root_path) / "config" / "config_entrevistas.json"
        with open(cfg_path, encoding="utf-8") as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config["ENTREVISTAS_CONFIG"] = entrevistas_cfg

    @app.teardown_appcontext
    def _shutdown_session(exception=None):
        db.session.remove()

    return app
