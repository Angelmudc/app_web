import os
import warnings
import urllib3
from dotenv import load_dotenv
import psycopg2
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import AuthorizedSession
from datetime import datetime
from requests.exceptions import SSLError

# ——— Supresión de warnings SSL inseguro ———
warnings.filterwarnings("ignore", message="Unverified HTTPS request")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 1) Carga variables de entorno
load_dotenv()

# 2) Parámetros de Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
if not SPREADSHEET_ID:
    raise ValueError("La variable de entorno SPREADSHEET_ID no está definida.")
SHEET_NAME = "Nueva hoja"

# 3) Ruta a credenciales de servicio
base_dir = os.path.dirname(__file__)
service_account_path = os.path.join(base_dir, 'service_account.json')
if not os.path.isfile(service_account_path):
    raise FileNotFoundError("No se encontró 'service_account.json' en la raíz del proyecto.")

# 4) Autenticación con Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
creds = Credentials.from_service_account_file(service_account_path, scopes=SCOPES)

# Creamos una sesión autorizada y deshabilitamos la verificación SSL
authed_session = AuthorizedSession(creds)
authed_session.verify = False

# Inicializamos gspread con esa sesión
gc = gspread.Client(auth=creds, session=authed_session)
worksheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

# 5) Leer filas (desde fila 3), con reintento si falla por SSL
try:
    all_vals = worksheet.get_all_values()
except SSLError:
    print("⚠️ Hubo un error SSL, reintentando sin verificación...")
    authed_session.verify = False
    all_vals = worksheet.get_all_values()

rows = all_vals[2:]

# 6) Función de parseo de fechas (DD/MM/YYYY y DD/MM/YYYY HH:MM:SS)
def parse_date(s):
    s = s.strip()
    if not s:
        return None
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

# 7) Configuración de PostgreSQL
db_url = os.getenv('NEW_DATABASE_URL')
if not db_url:
    raise ValueError("La variable de entorno NEW_DATABASE_URL no está definida.")

# 8) Conexión y migración
conn = psycopg2.connect(db_url)
try:
    with conn:
        with conn.cursor() as cur:
            # Forzamos el estilo DMY para interpretar bien tus fechas
            cur.execute("SET datestyle = 'ISO, DMY';")

            for r in rows:
                # Asegurar 32 columnas (A→AF)
                r += [''] * (32 - len(r))
                (
                    marca, nombre, edad, tel, dirc, modalidad, rutas, emp_ant,
                    anos_exp, areas, sabe_planchar, contactos_lab, contactos_fam,
                    acepta_pct, cedula, codigo, medio, insc, monto_ins, fecha_ins,
                    fecha_pago, inicio, monto_tot, porcentaje, calificacion,
                    entrevista, depu, perfil, ced1, ced2, ref_lab, ref_fam
                ) = r

                # Parseo de fechas a objetos datetime
                fecha_ins_p  = parse_date(fecha_ins)
                fecha_pago_p = parse_date(fecha_pago)
                inicio_p     = parse_date(inicio)

                # Inserción en la base de datos
                cur.execute("""
                    INSERT INTO candidata(
                        marca_temporal, nombre_completo, edad, numero_telefono,
                        direccion_completa, modalidad_trabajo, rutas_cercanas,
                        empleo_anterior, anos_experiencia, areas_experiencia,
                        monto_inscripcion, fecha_inscripcion, fecha_pago, inicio,
                        monto_total, porcentaje, calificacion, entrevista,
                        depuracion, perfil, cedula1, cedula2,
                        referencias_laborales, referencias_familiares,
                        acepta_porcentaje, cedula, codigo, medio_inscripcion,
                        inscripcion
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s
                    )
                """, (
                    marca or None,
                    nombre or None,
                    int(edad) if edad.isdigit() else None,
                    tel or None,
                    dirc or None,
                    modalidad or None,
                    rutas or None,
                    emp_ant or None,
                    int(anos_exp) if anos_exp.isdigit() else None,
                    areas or None,
                    float(monto_ins) if monto_ins.replace('.', '', 1).isdigit() else None,
                    fecha_ins_p,
                    fecha_pago_p,
                    inicio_p,
                    float(monto_tot) if monto_tot.replace('.', '', 1).isdigit() else None,
                    float(porcentaje) if porcentaje.replace('.', '', 1).isdigit() else None,
                    calificacion or None,
                    entrevista or None,
                    depu or None,
                    perfil or None,
                    ced1 or None,
                    ced2 or None,
                    ref_lab or None,
                    ref_fam or None,
                    acepta_pct.strip().lower() in ('sí','si','true','1'),
                    cedula or None,
                    codigo or None,
                    medio or None,
                    insc.strip().lower() in ('sí','si','true','1')
                ))
    print("✅ Migración completada exitosamente.")
except Exception as e:
    print("❌ Error durante la migración, rollback hecho:", e)
finally:
    conn.close()
