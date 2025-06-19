import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
import gspread
import cloudinary
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from flask_migrate import Migrate

# 1) Carga .env local (sólo para desarrollo)
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path, override=True)

# 2) Instancias globales
db = SQLAlchemy()
cache = Cache()

# 3) Scopes de Google
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

# ─── 4) Leer SERVICE_ACCOUNT_FILE y resolver ruta ─────────────────
svc_file = os.getenv("SERVICE_ACCOUNT_FILE", "").strip()
if not svc_file:
    raise RuntimeError("❌ Debes definir SERVICE_ACCOUNT_FILE en las Environment Variables de Render")

# Si la ruta no es absoluta, la tomamos relativa al proyecto
path_obj = Path(svc_file)
if not path_obj.is_absolute():
    path_obj = Path(__file__).parent / svc_file

if not path_obj.exists():
    raise RuntimeError(f"❌ No encuentro el archivo de credenciales: {path_obj}")

# 5) Cargar credenciales desde fichero
credentials = Credentials.from_service_account_file(str(path_obj), scopes=SCOPES)

# 6) Cliente de Google Sheets y alias
gspread_client = gspread.authorize(credentials)
sheets = build("sheets", "v4", credentials=credentials)

# 7) SPREADSHEET_ID
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
if not SPREADSHEET_ID:
    raise RuntimeError("❌ Debes definir SPREADSHEET_ID en las Environment Variables de Render")

# 8) Configuración de Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)

# 9) Normalización de cédula
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# 10) Factory de la aplicación Flask
def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # Clave secreta
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev')

    # Base de datos
    db_url = os.getenv('DATABASE_URL', '').strip()
    if not db_url:
        raise RuntimeError("❌ Debes definir DATABASE_URL en las Environment Variables de Render")
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Caché
    app.config['CACHE_TYPE'] = 'simple'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 120
    cache.init_app(app)

    # Inicializar extensiones
    db.init_app(app)
    Migrate(app, db)

    # Carga de configuración adicional (entrevistas)
    try:
        cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
        app.logger.info("✅ Config entrevistas cargada.")
    except Exception as e:
        app.logger.error(f"❌ No pude cargar config_entrevistas.json: {e}")
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    return app

# 11) Instancia principal
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
