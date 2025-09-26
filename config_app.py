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
import cloudinary

from sqlalchemy import text
from sqlalchemy.pool import NullPool

# ─────────────────────────────────────────────────────────────
# Carga .env (siempre desde la raíz del proyecto)
# ─────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path, override=True)

# ─────────────────────────────────────────────────────────────
# Instancias globales
# ─────────────────────────────────────────────────────────────
db      = SQLAlchemy()
cache   = Cache()
migrate = Migrate()
csrf    = CSRFProtect()

# ─────────────────────────────────────────────────────────────
# Usuarios en memoria (para login admin/secretaria)
# ─────────────────────────────────────────────────────────────
from werkzeug.security import generate_password_hash, check_password_hash
USUARIOS = {
    "angel":    {"pwd_hash": generate_password_hash("0000"),  "role": "admin"},
    "divina":   {"pwd_hash": generate_password_hash("67890"),  "role": "admin"},
    "darielis": {"pwd_hash": generate_password_hash("3333"),   "role": "secretaria"},
}

# ─────────────────────────────────────────────────────────────
# Utilidad: normalizar cédula (opcional)
# ─────────────────────────────────────────────────────────────
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
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

    # CSRF y sesiones
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'cambia_esta_clave_a_una_muy_segura')
    app.config['WTF_CSRF_ENABLED'] = True
    csrf.init_app(app)

    # Si deployas detrás de proxy (Render, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # Blueprints
    from admin.routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    from clientes.routes import clientes_bp
    app.register_blueprint(clientes_bp)

    # ── Base de datos remota (sin BD local)
    raw_db_url = os.getenv('DATABASE_URL', '')
    db_url = _normalize_db_url(raw_db_url)

    # ¿Usas PgBouncer (transaction pooling)? → usa NullPool para evitar reciclar SSL roto
    # Define en .env: DB_POOL_MODE=pgbouncer   (o déjalo vacío para pool normal)
    pool_mode = os.getenv("DB_POOL_MODE", "").lower()  # "", "pgbouncer"
    use_null_pool = pool_mode in ("pgbouncer", "nullpool", "off")

    # Engine options
    connect_args = {
        # SSL ya va en la URL; esto refuerza en psycopg2
        "sslmode": "require",
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "8")),
        # keepalives para evitar “decryption failed or bad record mac” por sesiones viejas
        "keepalives": int(os.getenv("DB_KEEPALIVES", "1")),
        "keepalives_idle": int(os.getenv("DB_KEEPALIVES_IDLE", "30")),
        "keepalives_interval": int(os.getenv("DB_KEEPALIVES_INTERVAL", "10")),
        "keepalives_count": int(os.getenv("DB_KEEPALIVES_COUNT", "3")),
        "application_name": os.getenv("DB_APPNAME", "app_web"),
    }

    engine_opts = {
        "pool_pre_ping": True,          # verifica conexión antes de usarla
        "pool_reset_on_return": "rollback",
        "connect_args": connect_args,
    }

    if use_null_pool:
        # Un socket por operación, sin reusar → adiós sockets “viejos” con SSL roto
        engine_opts["poolclass"] = NullPool
    else:
        # Pool normal, pero reciclando a menudo para evitar sesiones envejecidas
        engine_opts.update({
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "300")),  # 5 minutos
            "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
            "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
        })

    app.config.update({
        "SQLALCHEMY_DATABASE_URI": db_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": engine_opts,
        "CACHE_TYPE": "simple",
        "CACHE_DEFAULT_TIMEOUT": 120,
    })

    # Inicializar extensiones
    db.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)

    # Helpers globales para templates
    from datetime import datetime as _dt
    app.jinja_env.globals['now'] = _dt.utcnow
    app.jinja_env.globals['current_year'] = _dt.utcnow().year

    # ── Login manager (usuarios en memoria + clientes)
    from flask_login import LoginManager
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized_callback():
        if request.path.startswith('/clientes'):
            return redirect(url_for('clientes.login', next=request.url))
        return redirect(url_for('admin.login', next=request.url))

    from werkzeug.security import check_password_hash
    class User(UserMixin):
        def __init__(self, username, role):
            self.id   = username
            self.role = role
        def check_password(self, password):
            return check_password_hash(USUARIOS[self.id]['pwd_hash'], password)

    @login_manager.user_loader
    def load_user(user_id):
        data = USUARIOS.get(user_id)
        if data:
            return User(user_id, data['role'])
        try:
            from models import Cliente
            return Cliente.query.get(int(user_id))
        except Exception:
            return None

    # Config de entrevistas (si existe)
    try:
        cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    # ── Ping preventivo por request (SQLAlchemy 2.x requiere text())
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

# ─────────────────────────────────────────────────────────────
# Cloudinary (si lo usas para imágenes; si no, quedan vacíos)
# ─────────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)
