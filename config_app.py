import os
import re
import json
from pathlib import Path
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
import gspread
import cloudinary
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from flask_migrate import Migrate

# ─── 1) Instancias globales de SQLAlchemy y Cache ─────────────────────
db = SQLAlchemy()
cache = Cache()

# ─── 2) Google Service Account ────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

# Render inyecta tu archivo secreto en /etc/secrets/<filename>
svc_file = os.getenv("SERVICE_ACCOUNT_FILE", "").strip()
if not svc_file:
    raise RuntimeError("❌ Define SERVICE_ACCOUNT_FILE como variable de entorno en Render (por ejemplo: /etc/secrets/service_account.json)")

# Como Render monta el Secret File en /etc/secrets/<filename>,
# podemos usar directamente esa ruta absoluta:
cred_path = Path(svc_file)
if not cred_path.exists():
    raise RuntimeError(f"❌ No encuentro el Secret File de Google: {cred_path}")

credentials = Credentials.from_service_account_file(str(cred_path), scopes=SCOPES)

# Cliente de Google Sheets y Drive
gspread_client = gspread.authorize(credentials)
sheets_service = build("sheets", "v4", credentials=credentials)
sheets = sheets_service  # alias para importar en app.py

# ─── 3) ID de la hoja de cálculo ───────────────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
if not SPREADSHEET_ID:
    raise RuntimeError("❌ Define SPREADSHEET_ID como Environment Variable en Render")

# ─── 4) Configuración de Cloudinary ───────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "").strip(),
    api_key=os.getenv("CLOUDINARY_API_KEY", "").strip(),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "").strip()
)

# ─── 5) Normalización de cédula ───────────────────────────────────────
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    return digits if CEDULA_PATTERN.fullmatch(digits) else None

# ─── 6) Factory de la aplicación Flask ─────────────────────────────────
def create_app():
    app = Flask(__name__, instance_relative_config=False)

    # Secreto de Flask
    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev')

    # Base de datos (render usará DATABASE_URL)
    db_url = os.getenv('DATABASE_URL', '').strip()
    if not db_url:
        raise RuntimeError("❌ Define DATABASE_URL como Environment Variable en Render")
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Caché
    app.config['CACHE_TYPE'] = 'simple'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 120
    cache.init_app(app)

    # Inicializar extensiones
    db.init_app(app)
    Migrate(app, db)

    # Carga opcional de config_entrevistas.json (dentro del repo)
    cfg_path = Path(app.root_path) / 'config' / 'config_entrevistas.json'
    entrevistas_cfg = {}
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding='utf-8') as f:
                entrevistas_cfg = json.load(f)
            app.logger.info("✅ Configuraciones de entrevistas cargadas.")
        except Exception as e:
            app.logger.error(f"❌ Error al leer config_entrevistas.json: {e}")

    app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg
    return app

# ─── 7) Instancia principal ────────────────────────────────────────────
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
