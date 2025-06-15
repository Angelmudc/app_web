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

# ─── 1) Carga de variables de entorno ────────────────────────────────
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path, override=True)

# ─── 2) Instancia global de SQLAlchemy y Cache ───────────────────────
db = SQLAlchemy()
cache = Cache()

# ─── 3) Verificación de credenciales de Google ───────────────────────
sa_path = Path(os.getenv("SERVICE_ACCOUNT_FILE", ""))
if not sa_path.exists():
    raise RuntimeError(f"❌ No encuentro credenciales de servicio en {sa_path}")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]
credentials = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
gspread_client = gspread.authorize(credentials)
sheets_service = build("sheets", "v4", credentials=credentials)

# Alias para compatibilidad con import en app.py
sheets = sheets_service

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
if not SPREADSHEET_ID:
    raise RuntimeError("❌ Falta SPREADSHEET_ID en el .env")

# ─── 4) Configuración de Cloudinary ──────────────────────────────────
cloudinary.config(
    cloud_name  = os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key     = os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret  = os.getenv("CLOUDINARY_API_SECRET", "")
)

# ─── 5) Normalización de cédula ──────────────────────────────────────
CEDULA_PATTERN = re.compile(r'^\d{11}$')

def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# ─── 6) Factory de la aplicación Flask ───────────────────────────────
def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # Clave secreta para sesiones
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev')

    # URL de la base de datos
    db_url = os.getenv('DATABASE_URL', '').strip()
    if not db_url:
        raise RuntimeError("❌ Falta DATABASE_URL en tu .env")
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Configuración de caché
    app.config['CACHE_TYPE'] = 'simple'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 120
    cache.init_app(app)

    # Inicializa extensiones
    db.init_app(app)

    # ─── Carga de ENTREVISTAS_CONFIG ───────────────────────────────
    try:
        cfg_path = os.path.join(app.root_path, 'config', 'config_entrevistas.json')
        with open(cfg_path, encoding='utf-8') as f:
            entrevistas_cfg = json.load(f)
        app.logger.info("✅ Configuración de entrevistas cargada con éxito.")
    except Exception as e:
        app.logger.error(f"❌ Error cargando config_entrevistas.json: {e}")
        entrevistas_cfg = {}
    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

    return app

# ─── 7) Instancia para ejecución directa ─────────────────────────────
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
