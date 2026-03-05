# config_app.py
# -*- coding: utf-8 -*-

import os
import re
import json
import click
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

from flask import Flask, request, redirect, url_for, abort, session
from datetime import timedelta

from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf import CSRFProtect

from sqlalchemy import text, func
from sqlalchemy.pool import NullPool
from utils.compat_engine import format_compat_result
from utils.funciones_formatter import format_funciones, format_funciones_display

# ─────────────────────────────────────────────────────────────
# Carga .env (siempre desde la raíz del proyecto)
# ─────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path, override=True)

# ─────────────────────────────────────────────────────────────
# Instancias globales
# ─────────────────────────────────────────────────────────────
db = SQLAlchemy()
cache = Cache()
migrate = Migrate()
csrf = CSRFProtect()

# ─────────────────────────────────────────────────────────────
# Usuarios en memoria (para login admin/secretaria)
# ─────────────────────────────────────────────────────────────

USUARIOS = {
    "Cruz":   {"pwd_hash": generate_password_hash("8998", method="pbkdf2:sha256"), "role": "admin"},
    "Karla":  {"pwd_hash": generate_password_hash("9989", method="pbkdf2:sha256"), "role": "secretaria"},
    "Anyi": {"pwd_hash": generate_password_hash("0931", method="pbkdf2:sha256"), "role": "secretaria"},
}

# Helper: get admin user record by username, case-insensitive
def _get_admin_user_record(raw_username: str):
    """Devuelve (key_canonica, data) para USUARIOS, tolerando mayúsc/minúsc.

    Esto evita loops de login cuando el username se guarda con otro casing.
    """
    u = (raw_username or "").strip()
    if not u:
        return None, None

    # 1) Exact match
    if u in USUARIOS:
        return u, USUARIOS[u]

    # 2) Case-insensitive match
    ul = u.lower()
    for k, v in USUARIOS.items():
        if str(k).lower() == ul:
            return k, v

    return None, None

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
        raise RuntimeError("❌ Debes definir DATABASE_URL en tu .env (URL REMOTA).")

    url = url.strip()

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

    default_secret = "cambia_esta_clave_a_una_muy_segura"
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", default_secret)
    if app.config["SECRET_KEY"] == default_secret and prod:
        raise RuntimeError("SECRET_KEY no configurada. Define FLASK_SECRET_KEY en .env")

    # Cookies secure:
    # - En prod: True (pero SOLO si estás bajo HTTPS real)
    # - En local: False (para no romper CSRF/cookies)
    # Nota: ProxyFix + request.is_secure te ayuda a detectar HTTPS real detrás de proxy.
    def _cookie_secure() -> bool:
        # En desarrollo local (aunque APP_ENV diga "production"), NO usar cookies Secure
        # porque en HTTP el navegador las ignora y la sesión no se guarda.
        if FORCE_COOKIE_SECURE:
            return True

        if not prod:
            return False

        # Si NO estamos en Render (o un entorno HTTPS real), asumimos que es local/dev
        # y desactivamos Secure para no romper login/sesión.
        if not IS_RENDER:
            return False

        # En Render, normalmente hay HTTPS real.
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
        }
    )

    # ✅ Limitar tamaño de requests (evita payloads gigantes)
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024)))  # 4MB

    # ─────────────────────────────────────────────────────────
    # CSRF
    # ─────────────────────────────────────────────────────────
    # En local con http, SSL_STRICT debe ser False.
    # En prod, normalmente sí.
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["WTF_CSRF_SSL_STRICT"] = (prod and _cookie_secure() and IS_RENDER)
    app.config["WTF_CSRF_TIME_LIMIT"] = int(os.getenv("WTF_CSRF_TIME_LIMIT", "7200"))  # 2 horas
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

    # ─────────────────────────────────────────────────────────
    # Base de datos remota
    # ─────────────────────────────────────────────────────────
    raw_db_url = os.getenv("DATABASE_URL", "")
    db_url = _normalize_db_url(raw_db_url)

    pool_mode = (os.getenv("DB_POOL_MODE", "") or "").lower()
    use_null_pool = pool_mode in ("pgbouncer", "nullpool", "off")

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

            # Cache (simple)
            "CACHE_TYPE": "simple",
            "CACHE_DEFAULT_TIMEOUT": 120,

            "TEMPLATES_AUTO_RELOAD": not prod,
            "JSON_SORT_KEYS": False,
        }
    )

    # ─────────────────────────────────────────────────────────
    # Inicializar extensiones
    # ─────────────────────────────────────────────────────────
    db.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)

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
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )

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

        return resp

    # ─────────────────────────────────────────────────────────
    # Helpers globales para templates
    # ─────────────────────────────────────────────────────────
    from datetime import datetime as _dt
    app.jinja_env.globals["now"] = _dt.utcnow
    app.jinja_env.globals["current_year"] = _dt.utcnow().year
    app.jinja_env.globals["format_funciones"] = format_funciones
    app.jinja_env.filters["funciones_fmt"] = format_funciones
    app.jinja_env.globals["format_funciones_display"] = format_funciones_display
    app.jinja_env.filters["format_funciones_display"] = format_funciones_display
    app.jinja_env.globals["format_compat_result"] = format_compat_result
    app.jinja_env.filters["compat_fmt"] = format_compat_result

    # ─────────────────────────────────────────────────────────
    # Login manager (staff en BD + legacy en memoria + clientes)
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

    class User(UserMixin):
        def __init__(self, username, role):
            self.id = username
            self.role = role

        def check_password(self, password):
            key, data = _get_admin_user_record(self.id)
            if not data:
                return False
            return check_password_hash(data["pwd_hash"], password)

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

        key, data = _get_admin_user_record(uid)
        if data:
            return User(key, data["role"])

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
        if role in {"admin", "secretaria"}:
            return role
        default_role = (os.getenv("ADMIN_DEFAULT_ROLE") or "secretaria").strip().lower()
        return default_role if default_role in {"admin", "secretaria"} else "secretaria"

    def _create_staff_user(username: str, role: str, password: str, email: str = "", is_active: bool = True):
        from models import StaffUser

        username_clean = (username or "").strip()
        email_clean = (email or "").strip().lower() or None
        role_clean = _normalize_staff_role(role)
        min_len = _staff_password_min_len()

        if not username_clean:
            raise click.ClickException("Debes indicar --username.")
        if not password or len(password) < min_len:
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
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Staff creado: username={u.username} role={u.role} id={u.id} active={u.is_active}")

    @app.cli.command("create-staff")
    @click.option("--username", required=True, help="Usuario interno (único).")
    @click.option("--role", required=False, default=lambda: os.getenv("ADMIN_DEFAULT_ROLE", "secretaria"), help="Rol: admin|secretaria.")
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

    # ─────────────────────────────────────────────────────────
    # Ping preventivo por request (evita conexiones muertas)
    # ─────────────────────────────────────────────────────────
    @app.before_request
    def _ensure_db_connection():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db.session.remove()

    @app.teardown_appcontext
    def _shutdown_session(exception=None):
        db.session.remove()

    return app
