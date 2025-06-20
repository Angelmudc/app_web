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
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
import cloudinary

# Usuarios en memoria
USUARIOS = {
    "angel":    {"pwd_hash": generate_password_hash("12345"), "role": "admin"},
    "divina":   {"pwd_hash": generate_password_hash("67890"), "role": "admin"},
    "caty":     {"pwd_hash": generate_password_hash("11111"), "role": "secretaria"},
    "darielis": {"pwd_hash": generate_password_hash("22222"), "role": "secretaria"},
}

# 1) Carga .env
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path, override=True)

# 2) Instancias
db    = SQLAlchemy()
cache = Cache()
migrate = Migrate()

# 3) Scopes Google
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

# 4) Credenciales desde ENV
clave_json = os.getenv("CLAVE1_JSON", "").strip()
if not clave_json:
    raise RuntimeError("❌ Debes definir CLAVE1_JSON")
info = json.loads(clave_json)
credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
gspread_client = gspread.authorize(credentials)
sheets_service  = build("sheets", "v4", credentials=credentials)
SPREADSHEET_ID  = os.getenv("SPREADSHEET_ID", "").strip()
if not SPREADSHEET_ID:
    raise RuntimeError("❌ Debes definir SPREADSHEET_ID")

# 5) Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)

# 6) Normalización cédula
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# 7) Factory
def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev')

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

    # Cargar config entrevistas
    try:
        cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
    except Exception:
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    # **No definimos aquí la ruta “/”**. Todas las rutas las pones en app.py

    return app

# 8) Instancia principal
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
