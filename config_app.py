import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import cloudinary

# Usuarios en memoria
USUARIOS = {
    "angel":    {"pwd_hash": generate_password_hash("12345"), "role": "admin"},
    "divina":   {"pwd_hash": generate_password_hash("67890"), "role": "admin"},
    "kathy":     {"pwd_hash": generate_password_hash("11111"), "role": "secretaria"},
    "darielis": {"pwd_hash": generate_password_hash("22222"), "role": "secretaria"},
}

# 1) Carga .env
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path, override=True)

# 2) Instancias globales
db      = SQLAlchemy()
cache   = Cache()
migrate = Migrate()

# 3) Normalización de cédula
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# 4) Factory de la app
def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev')

    # Base de datos
    db_url = os.getenv('DATABASE_URL', '').strip()
    if not db_url:
        raise RuntimeError("❌ Debes definir DATABASE_URL")
    app.config.update({
        'SQLALCHEMY_DATABASE_URI': db_url,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': 120,
    })

    # Inicializar extensiones
    cache.init_app(app)
    db.init_app(app)
    migrate.init_app(app, db)

    # ── Flask-Login ─────────────────────────
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    class User(UserMixin):
        def __init__(self, username, role):
            self.id   = username
            self.role = role
        def check_password(self, password):
            return check_password_hash(USUARIOS[self.id]['pwd_hash'], password)

    @login_manager.user_loader
    def load_user(user_id):
        data = USUARIOS.get(user_id)
        return User(user_id, data['role']) if data else None

    # Cargar config entrevistas (opcional)
    try:
        cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    # **Rutas definidas en app.py**

    return app

# 5) Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)

# 6) Instancia principal
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
