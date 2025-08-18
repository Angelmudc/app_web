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
from werkzeug.middleware.proxy_fix import ProxyFix  # ← agregado para HTTPS en Render
import cloudinary
from flask_wtf import CSRFProtect

# 1) Carga .env
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path, override=True)

# 2) Instancias globales
db      = SQLAlchemy()
cache   = Cache()
migrate = Migrate()
csrf    = CSRFProtect()     # instancia global de CSRF

# 3) Usuarios en memoria (ejemplo)
USUARIOS = {
    "angel":    {"pwd_hash": generate_password_hash("0000"), "role": "admin"},
    "divina":   {"pwd_hash": generate_password_hash("67890"), "role": "admin"},
    "darielis": {"pwd_hash": generate_password_hash("3333"), "role": "secretaria"},
}

# 4) Normalización de cédula
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# 5) Factory de la app
def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # ── Clave secreta para CSRF y sesiones ───────────────────────
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'cambia_esta_clave_a_una_muy_segura')
    app.config['WTF_CSRF_ENABLED'] = True
    csrf.init_app(app)

    # ── ProxyFix para HTTPS detrás de Render ────────────────────
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # ── Registro de blueprints ─────────────────────────────────
    from admin.routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from clientes.routes import clientes_bp
    app.register_blueprint(clientes_bp)

    # ── Configuración de base de datos y cache ────────────────
    db_url = os.getenv('DATABASE_URL', '').strip()
    if not db_url:
        raise RuntimeError("❌ Debes definir DATABASE_URL")
    app.config.update({
        'SQLALCHEMY_DATABASE_URI':    db_url,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'CACHE_TYPE':                 'simple',
        'CACHE_DEFAULT_TIMEOUT':      120,
        # ← Aquí activamos pool_pre_ping y forzamos SSL
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'pool_pre_ping': True,
            'connect_args': {
                'sslmode': 'require'
            }
        }
    })

    # Inicializar extensiones
    db.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)

    # ── Helpers globales para plantillas ──────────────────────
    from datetime import datetime as _dt
    app.jinja_env.globals['now'] = _dt.utcnow
    app.jinja_env.globals['current_year'] = _dt.utcnow().year

    # ── Flask-Login ─────────────────────────────────────────
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized_callback():
        if request.path.startswith('/clientes'):
            return redirect(url_for('clientes.login', next=request.url))
        return redirect(url_for('admin.login', next=request.url))

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
        except:
            return None

    # ── Carga config entrevistas (opcional) ───────────────────
    try:
        cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    return app

# 6) Configuración de Cloudinary (puede quedar al final)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)
