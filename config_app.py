# config_app.py
import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv

from flask import Flask, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf import CSRFProtect

from sqlalchemy import text
from sqlalchemy.pool import NullPool

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
    "Cruz": {"pwd_hash": generate_password_hash("8998"), "role": "admin"},
    "vanina": {"pwd_hash": generate_password_hash("2424"), "role": "secretaria"},
}

# ─────────────────────────────────────────────────────────────
# Utilidad: normalizar cédula (devuelve 11 dígitos sin guiones)
# ─────────────────────────────────────────────────────────────
CEDULA_PATTERN = re.compile(r"^\d{11}$")


def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits if CEDULA_PATTERN.fullmatch(digits) else None


# ─────────────────────────────────────────────────────────────
# Utilidades DB: normalizar DATABASE_URL y asegurar SSL
# ─────────────────────────────────────────────────────────────
def _normalize_db_url(url: str) -> str:
    """
    - Acepta 'postgres://...' y lo convierte a 'postgresql+psycopg2://...'
    - Asegura 'sslmode=require' en la querystring.
    """
    if not url:
        raise RuntimeError("❌ Debes definir DATABASE_URL en tu .env (URL REMOTA).")

    url = url.strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


# ─────────────────────────────────────────────────────────────
# Factory de la app
# ─────────────────────────────────────────────────────────────
def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # ── Seguridad de sesión/cookies
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "production")).lower()
    prod = env in ("prod", "production")

    default_secret = "cambia_esta_clave_a_una_muy_segura"
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", default_secret)
    if app.config["SECRET_KEY"] == default_secret and prod:
        raise RuntimeError("SECRET_KEY no configurada. Define FLASK_SECRET_KEY en .env")

    app.config.update(
        {
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": prod,  # True si usas HTTPS
            "REMEMBER_COOKIE_HTTPONLY": True,
            "REMEMBER_COOKIE_SECURE": prod,
            "PERMANENT_SESSION_LIFETIME": int(
                os.getenv("SESSION_TTL_SECONDS", "2592000")
            ),  # 30 días
        }
    )

    # ✅ Limitar tamaño de requests (evita payloads gigantes)
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.getenv("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024))
    )  # 4MB

    # CSRF
    app.config["WTF_CSRF_ENABLED"] = True
    csrf.init_app(app)

    # Si deployas detrás de proxy (Render, Fly, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # ── Base de datos remota (sin BD local)
    raw_db_url = os.getenv("DATABASE_URL", "")
    db_url = _normalize_db_url(raw_db_url)

    # ¿Usas PgBouncer (transaction pooling)? → usa NullPool para evitar reciclar SSL roto
    pool_mode = os.getenv("DB_POOL_MODE", "").lower()  # "", "pgbouncer", "nullpool"
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
            "CACHE_TYPE": "simple",
            "CACHE_DEFAULT_TIMEOUT": 120,
            "TEMPLATES_AUTO_RELOAD": not prod,
            "JSON_SORT_KEYS": False,
        }
    )

    # Inicializar extensiones
    db.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)

    # ── Capa de seguridad extra (headers + anti brute-force login)
    # Requiere: utils/security_layer.py
    from utils.security_layer import init_security

    init_security(app, cache)

    # ── Headers de seguridad globales (CSP unificado para tus 3 base.html)
    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://code.jquery.com https://cdn.datatables.net; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "upgrade-insecure-requests; "
    )

    @app.after_request
    def _set_security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
        )

        # CSP (enforcing)
        resp.headers["Content-Security-Policy"] = csp

        # HSTS SOLO en producción (requiere HTTPS)
        if prod:
            resp.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Buenas prácticas cross-origin
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"

        return resp

    # Helpers globales para templates
    from datetime import datetime as _dt

    app.jinja_env.globals["now"] = _dt.utcnow
    app.jinja_env.globals["current_year"] = _dt.utcnow().year

    # ── Login manager (usuarios en memoria + clientes)
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized_callback():
        if request.path.startswith("/clientes"):
            return redirect(url_for("clientes.login", next=request.url))
        return redirect(url_for("admin.login", next=request.url))

    class User(UserMixin):
        def __init__(self, username, role):
            self.id = username
            self.role = role

        def check_password(self, password):
            return check_password_hash(USUARIOS[self.id]["pwd_hash"], password)

    @login_manager.user_loader
    def load_user(user_id):
        data = USUARIOS.get(user_id)
        if data:
            return User(user_id, data["role"])
        try:
            from models import Cliente

            return Cliente.query.get(int(user_id))
        except Exception:
            return None

    # ── Blueprints
    from admin.routes import admin_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")

    from clientes import clientes_bp

    app.register_blueprint(clientes_bp)

    from public import public_bp

    app.register_blueprint(public_bp)  # sin prefix: responde en "/"

    # Config de entrevistas (si existe)
    try:
        cfg_path = Path(app.root_path) / "config" / "config_entrevistas.json"
        with open(cfg_path, encoding="utf-8") as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config["ENTREVISTAS_CONFIG"] = entrevistas_cfg

    # ── Ping preventivo por request (evita conexiones muertas)
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
