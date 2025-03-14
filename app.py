import logging
logging.basicConfig(level=logging.DEBUG)
from flask import Flask, render_template, request
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session
import os
import json
import gspread
from flask import request
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_from_directory
from flask_caching import Cache
from rapidfuzz import process
from google.oauth2.service_account import Credentials
from flask import Flask, request, render_template, jsonify
import unicodedata
from flask import Flask, render_template, request, send_from_directory
import os
import traceback
from flask import Flask, request, render_template
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from fpdf import FPDF
from flask import send_file
from flask import Flask, render_template, request, redirect, send_file
from fpdf import FPDF
import os
import os
from werkzeug.utils import secure_filename
import os
from flask import Flask, render_template, request, redirect
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import cloudinary
import cloudinary.uploader
import cloudinary.api
import io
import zipfile
import requests
from flask import Flask, request, send_file
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import io
import zipfile
import requests
from flask import Flask, request, render_template, redirect, url_for, send_file
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import cloudinary
import cloudinary.uploader
from flask import Flask, request, send_file, current_app
from fpdf import FPDF
from io import BytesIO
import os
import datetime
from datetime import datetime
import pandas as pd
import io
from flask import send_file, render_template, request

# Configuración de la API de Google Sheets
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file'
]

# Verifica que CLAVE1_JSON está cargando correctamente
clave1_json = os.environ.get("CLAVE1_JSON")
if not clave1_json:
    raise ValueError("❌ ERROR: La variable de entorno CLAVE1_JSON no está configurada correctamente.")

SPREADSHEET_ID = "1J8cPXScpOCywiJHspSntCo3zPLf7FCOli6vsgSWLNOg"

clave1 = json.loads(clave1_json)
credentials = Credentials.from_service_account_info(clave1, scopes=SCOPES)
client = gspread.authorize(credentials)
service = build('sheets', 'v4', credentials=credentials)

# Accede a la hoja de cálculo y obtiene la hoja "Nueva hoja"
spreadsheet = client.open_by_key(SPREADSHEET_ID)
sheet = spreadsheet.worksheet("Nueva hoja")

cloudinary.config(
    cloud_name="dntyert1y",
    api_key="146572744812483",
    api_secret="huBvPbEs1oE5dSJ62FNej_NX-tI"
)


try:
    resultado = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Nueva hoja!A1:Z100"
    ).execute()
    print("✅ Conexión exitosa a Google Sheets. Datos obtenidos:", resultado)
except Exception as e:
    print("❌ Error conectando a Google Sheets:", e)

# Configuración básica de Flask
app = Flask(__name__)
app.secret_key = "clave_secreta_segura"

# Base de datos de usuarios (puedes usar una real)
usuarios = {
    "angel": generate_password_hash("1234"),  # Usuario: admin, Clave: 12345
    "usuario1": generate_password_hash("clave123")
}

# Configuración de caché
cache_config = {
    "CACHE_TYPE": "simple",  # Uso de caché en memoria
    "CACHE_DEFAULT_TIMEOUT": 120  # Caché de búsqueda por 2 minutos
}
app.config.from_mapping(cache_config)
cache = Cache(app)

ENTREVISTAS_CONFIG = {}

try:
    ruta_config = os.path.join(os.path.dirname(__file__), "config", "config_entrevistas.json")

    # Imprime la ruta final que se usará
    print("Ruta final de config:", ruta_config)

    # Muestra el contenido de la carpeta donde está app.py
    print("Contenido de la carpeta principal:",
          os.listdir(os.path.dirname(__file__)))

    # Muestra el contenido de la carpeta "Config"
    carpeta_config = os.path.join(os.path.dirname(__file__), "config")
    print("Contenido de la carpeta config:",
          os.listdir(carpeta_config))

    # Ahora sí abrimos el archivo JSON
    with open(ruta_config, "r", encoding="utf-8") as f:
        ENTREVISTAS_CONFIG = json.load(f)

    print("✅ Configuración de entrevistas cargada con éxito.")

except Exception as e:
    print(f"❌ Error al cargar la configuración de entrevistas: {str(e)}")
    ENTREVISTAS_CONFIG = {}



# Ruta para servir archivos estáticos correctamente
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(app.root_path, 'static'), filename)

def normalizar_texto(texto):
    """
    Convierte un texto a minúsculas, elimina acentos y espacios extras.
    """
    if not texto:
        return ""
    texto = texto.strip().lower()
    return ''.join(
        c for c in unicodedata.normalize('NFKD', texto) if unicodedata.category(c) != 'Mn'
    )

def obtener_siguiente_fila():
    """
    Esta función obtiene la siguiente fila vacía en la hoja de cálculo.
    Se asume que la columna A se usa para indicar filas ocupadas.
    """
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:A"  # Asegúrate de usar la columna adecuada.
        ).execute()
        values = result.get("values", [])
        # La cantidad de filas ocupadas + 1 es la siguiente fila disponible.
        return len(values) + 1
    except Exception as e:
        print(f"Error al obtener la siguiente fila: {str(e)}")
        return None


def buscar_candidata(busqueda):
    try:
        datos = sheet.get_all_values()  # Obtener todos los valores de la hoja
        resultados = []

        for fila_index, fila in enumerate(datos[1:], start=2):  # Saltamos la primera fila (encabezado)
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""  # Columna B
            cedula = fila[14].strip() if len(fila) > 14 else ""  # Columna O
            saldo_pendiente = fila[23] if len(fila) > 23 else "0"  # Columna X (Saldo pendiente)

            if busqueda.lower() in nombre or busqueda == cedula:  # Búsqueda flexible
                resultados.append({
                    'fila_index': fila_index,
                    'nombre': fila[1],
                    'telefono': fila[3] if len(fila) > 3 else "No disponible",
                    'cedula': fila[14] if len(fila) > 14 else "No disponible",
                    'monto_total': fila[22] if len(fila) > 22 else "0",  # Columna W (Monto Total)
                    'saldo_pendiente': saldo_pendiente,
                    'fecha_pago': fila[21] if len(fila) > 21 else "No registrada",  # Columna U
                    'calificacion': fila[24] if len(fila) > 24 else "Pendiente"  # Columna Y
                })

        return resultados

    except Exception as e:
        print(f"❌ Error en la búsqueda: {e}")
        return []

def actualizar_datos_editar(fila_index, nuevos_datos):
    try:
        columnas = {
            "codigo": "P",
            "nombre": "B",
            "telefono": "D",
            "cedula": "O",
            "estado": "Q",
            "monto": "S",
            "fecha": "T",
        }

        for campo, valor in nuevos_datos.items():
            if campo in columnas and valor:
                rango = f"Nueva hoja!{columnas[campo]}{fila_index}"
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango,
                    valueInputOption="RAW",
                    body={"values": [[valor]]}
                ).execute()

        print(f"✅ Datos actualizados correctamente en la fila {fila_index}")
        return True
    except Exception as e:
        print(f"❌ Error al actualizar datos en la fila {fila_index}: {e}")
        return False

def buscar_candidata(busqueda):
    """
    Busca candidatas en la hoja de cálculo SOLO por nombre (columna B).
    - La búsqueda no es estricta, permite coincidencias parciales.
    - Retorna toda la fila encontrada sin importar si hay columnas vacías.
    """
    try:
        datos = sheet.get_all_values()  # Obtiene todas las filas de la hoja
        resultados = []

        busqueda = busqueda.strip().lower()  # Normalizar búsqueda

        for fila_index, fila in enumerate(datos[1:], start=2):  # Saltamos el encabezado
            if len(fila) > 1:  # Verifica que la fila tenga al menos la columna B (nombre)
                nombre = fila[1].strip().lower()  # Columna B (Nombre)

                if busqueda in nombre:  # Coincidencia parcial
                    resultados.append({
                        'fila_index': fila_index,
                        'datos_completos': fila  # Guarda toda la fila encontrada
                    })

        return resultados  # Retorna TODAS las coincidencias encontradas

    except Exception as e:
        print(f"❌ Error en la búsqueda: {e}")
        return []
        encabezados = datos[0]  # Primera fila son los encabezados

        # 🔹 Buscar en cada fila (desde la segunda fila en adelante)
        for fila_index, fila in enumerate(datos[1:], start=2):  
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""  # Columna B
            cedula = fila[14].strip() if len(fila) > 14 else ""  # Columna O

            if not busqueda:
                continue

            busqueda = busqueda.strip().lower()

            # 🔹 Coincidencia parcial en nombre o coincidencia exacta en cédula
            if busqueda in nombre or busqueda == cedula:
                # Asegurar que la fila tenga suficientes columnas
                while len(fila) < 16:
                    fila.append("")

                # 🔹 Agregar resultado
                resultados.append({
                    "fila_index": fila_index,  # Índice de la fila en la hoja (1-based)
                    "nombre": fila[1] if len(fila) > 1 else "No disponible",  # Columna B
                    "edad": fila[2] if len(fila) > 2 else "No disponible",  # Columna C
                    "telefono": fila[3] if len(fila) > 3 else "No disponible",  # Columna D
                    "direccion": fila[4] if len(fila) > 4 else "No disponible",  # Columna E
                    "modalidad": fila[5] if len(fila) > 5 else "No disponible",  # Columna F
                    "anos_experiencia": fila[8] if len(fila) > 8 else "No disponible",  # Columna I
                    "experiencia": fila[9] if len(fila) > 9 else "No disponible",  # Columna J
                    "sabe_planchar": fila[10] if len(fila) > 10 else "No disponible",  # Columna K
                    "referencia_laboral": fila[11] if len(fila) > 11 else "No disponible",  # Columna L
                    "referencia_familiar": fila[12] if len(fila) > 12 else "No disponible",  # Columna M
                    "cedula": fila[14] if len(fila) > 14 else "No disponible",  # Columna O
                    "codigo": fila[15] if len(fila) > 15 else "No asignado",  # Columna P
                })

        # 🔹 Retorna todos los resultados encontrados
        return resultados

    except Exception as e:
        print(f"❌ Error al buscar candidatas: {e}")
        return []

def obtener_datos_editar():
    """
    Obtiene los datos de la hoja de cálculo y se asegura de que cada fila tenga suficientes columnas.
    """
    try:
        print("📌 Intentando obtener datos de Google Sheets...")  # DEBUG
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Nueva hoja!A:Y'
        ).execute()
        valores = result.get('values', [])

        print(f"🔹 Datos obtenidos ({len(valores)} filas):")  # DEBUG
        for fila in valores[:5]:  # Solo muestra las primeras 5 filas
            print(fila)

        # Asegurar que cada fila tenga al menos 25 columnas
        datos_completos = [fila + [''] * (25 - len(fila)) for fila in valores]

        return datos_completos
    except Exception as e:
        print(f"❌ Error al obtener datos de edición: {e}")
        return []

def obtener_datos_editar():
    """
    Obtiene los datos de la hoja de cálculo y se asegura de que cada fila tenga suficientes columnas.
    """
    try:
        print("📌 Intentando obtener datos de Google Sheets...")  # DEBUG
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range='Nueva hoja!A:Y'
        ).execute()
        valores = result.get('values', [])

        print(f"🔹 Datos obtenidos ({len(valores)} filas):")  # DEBUG
        for fila in valores[:5]:  # Solo muestra las primeras 5 filas
            print(fila)

        # Asegurar que cada fila tenga al menos 25 columnas
        datos_completos = [fila + [''] * (25 - len(fila)) for fila in valores]

        return datos_completos
    except Exception as e:
        logging.error(f"❌ Error al obtener datos de edición: {e}", exc_info=True)
        return []


def actualizar_inscripcion(fila_index, estado, monto, fecha):
    try:
        print(f"📌 Actualizando fila {fila_index} con estado={estado}, monto={monto}, fecha={fecha}")

        rango = f'Nueva hoja!Q{fila_index}:T{fila_index}'  # Rango de actualización en Google Sheets
        valores = [[estado, monto, fecha]]

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()

        print(f"✅ Inscripción actualizada en fila {fila_index}")
        return True
    except Exception as e:
        print(f"❌ Error al actualizar inscripción en fila {fila_index}: {e}")
        return False
    
def inscribir_candidata(fila_index, estado, monto, fecha):
    """
    Actualiza los datos de la candidata en la hoja de cálculo para registrar su inscripción.
    """
    try:
        datos = obtener_datos_editar()
        fila = datos[fila_index - 1]  # Ajusta el índice porque los índices de fila en Sheets empiezan en 1

        # Generar código si no tiene
        if len(fila) <= 15 or not fila[15].startswith("CAN-"):
            fila[15] = generar_codigo_unico()  # Columna P

        # Actualizar los valores específicos en la fila
        fila[16] = estado  # Estado en la columna Q
        fila[18] = monto  # Monto en la columna S
        fila[19] = fecha  # Fecha en la columna T

        # Escribir los cambios de vuelta en la hoja
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f'Nueva hoja!P{fila_index}:T{fila_index}',  # Rango de P a T
            valueInputOption='USER_ENTERED',
            body={'values': [fila[15:20]]}  # Asegurar que se escriben todos los datos necesarios
        ).execute()
        return True
    except Exception as e:
        print(f"Error al inscribir candidata: {e}")
        return False

def filtrar_por_busqueda(filas, termino):
    resultados = []
    termino = termino.lower()
    for index, fila in enumerate(filas, start=2):
        nombre = fila[0].strip().lower() if len(fila) > 0 else ""
        if termino in nombre:
            resultados.append({
                'fila_index': index,
                'nombre': fila[0],
                'cedula': fila[13] if len(fila) > 13 else "No especificado",
                'ciudad': fila[3] if len(fila) > 3 else "No especificado",
                'telefono': fila[2] if len(fila) > 2 else "No especificado",
            })
    return resultados

def cargar_detalles_candidata(valores, candidata_param):
    try:
        fila_index = int(candidata_param)
        # Ajustar índice de lista (la primera fila en 'valores' suele ser encabezado)
        fila = valores[fila_index - 1]  # Asumiendo que la fila_index viene desde enumeración + 1
    except (ValueError, IndexError):
        return None

    return {
        'nombre': fila[0] if len(fila) > 0 else "No especificado",
        'edad': fila[1] if len(fila) > 1 else "No especificado",
        'telefono': fila[2] if len(fila) > 2 else "No especificado",
        'direccion': fila[3] if len(fila) > 3 else "No especificado",
        'modalidad': fila[4] if len(fila) > 4 else "No especificado",
        'rutas': fila[5] if len(fila) > 5 else "No especificado",
        'empleo_anterior': fila[6] if len(fila) > 6 else "No especificado",
        'anos_experiencia': fila[7] if len(fila) > 7 else "No especificado",
        'areas_experiencia': fila[8] if len(fila) > 8 else "No especificado",
        'sabe_planchar': fila[9] if len(fila) > 9 else "No especificado",
        'referencias_laborales': fila[10] if len(fila) > 10 else "No especificado",
        'referencias_familiares': fila[11] if len(fila) > 11 else "No especificado",
        'acepta_porcentaje': fila[12] if len(fila) > 12 else "No especificado",
        'cedula': fila[13] if len(fila) > 13 else "No especificado",
    }


def buscar_candidata(busqueda):
    try:
        sheet = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="Nueva hoja!A:Y").execute()
        valores = sheet.get("values", [])

        if not valores:
            return None  

        encabezados = valores[1]  
        datos_candidatas = [dict(zip(encabezados, fila)) for fila in valores[2:] if len(fila) > 1]  

        # Buscar por nombre o cédula
        for fila_index, candidata in enumerate(datos_candidatas, start=3):  
            if (busqueda.lower() in candidata.get("Nombre", "").lower() or busqueda == candidata.get("Telefono", "")):
                candidata["fila_index"] = fila_index  
                print(f"✅ Fila encontrada: {fila_index}")  # 🔍 DEPURACIÓN
                return candidata  

        print("❌ No se encontró la candidata.")
        return None  

    except Exception as e:
        print(f"❌ Error en la búsqueda: {e}")
        return None
def inscribir_candidata(fila_index, estado, monto, fecha):
    """
    Actualiza los datos de la candidata en la hoja de cálculo para registrar su inscripción.
    """
    try:
        datos = obtener_datos_editar()
        fila = datos[fila_index - 1]  # Ajusta el índice porque los índices de fila en Sheets empiezan en 1

        # Generar código si no tiene
        if len(fila) <= 15 or not fila[15].startswith("CAN-"):
            fila[15] = generar_codigo_unico()  # Columna P

        # Actualizar los valores específicos en la fila
        fila[16] = estado  # Estado en la columna Q
        fila[18] = monto  # Monto en la columna S
        fila[19] = fecha  # Fecha en la columna T

        # Escribir los cambios de vuelta en la hoja
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f'Nueva hoja!P{fila_index}:T{fila_index}',  # Rango de P a T
            valueInputOption='USER_ENTERED',
            body={'values': [fila[15:20]]}  # Asegurar que se escriben todos los datos necesarios
        ).execute()
        return True
    except Exception as e:
        print(f"Error al inscribir candidata: {e}")
        return False


def filtrar_candidatas(ciudad="", modalidad="", experiencia="", areas=""):
    """
    Filtra candidatas basándose en los criterios ingresados.
    - Permite coincidencias parciales en la *Ciudad* (Columna E).
    - Solo muestra candidatas con inscripción en "Sí" (Columna R).
    """
    try:
        datos = obtener_datos_filtrar()
        resultados = []

        for fila in datos:
            if len(fila) < 16:  # Asegurar que haya suficientes columnas
                continue

            # 🔹 Extraer valores y normalizar
            ciudad_fila = normalizar_texto(fila[0])  # Columna E: Ciudad/Dirección
            modalidad_fila = normalizar_texto(fila[1])  # Columna F: Modalidad
            experiencia_fila = normalizar_texto(fila[2])  # Columna I: Años de experiencia
            areas_fila = normalizar_texto(fila[3])  # Columna J: Áreas de experiencia
            inscripcion_fila = fila[4].strip().lower()  # Columna R: Inscripción

            # 🔹 Solo mostrar inscritas
            if inscripcion_fila != "si":
                continue

            # 🔹 Validar filtros
            cumple_ciudad = not ciudad or ciudad in ciudad_fila
            cumple_modalidad = not modalidad or modalidad == modalidad_fila
            cumple_experiencia = not experiencia or experiencia == experiencia_fila
            cumple_areas = not areas or areas in areas_fila

            if cumple_ciudad and cumple_modalidad and cumple_experiencia and cumple_areas:
                resultados.append({
                    'ciudad': fila[0],  # Columna E
                    'modalidad': fila[1],  # Columna F
                    'experiencia': fila[2],  # Columna I
                    'areas_experiencia': fila[3],  # Columna J
                })

        return resultados

    except Exception as e:
        print(f"Error al filtrar candidatas: {e}")
        return []

def buscar_candidata(busqueda):
    try:
        sheet = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="Nueva hoja!A:Y").execute()
        valores = sheet.get("values", [])

        if not valores:
            return None  # Si la hoja está vacía

        encabezados = valores[1]  # Segunda fila como nombres de columna
        datos_candidatas = [dict(zip(encabezados, fila)) for fila in valores[2:] if len(fila) > 1]  # Solo filas con datos

        # Filtrar por nombre o cédula
        resultado = [
            candidata for candidata in datos_candidatas
            if busqueda.lower() in candidata.get("Nombre", "").lower()
            or busqueda in candidata.get("Telefono", "")
        ]

        return resultado if resultado else None

    except Exception as e:
        print(f"❌ Error en la búsqueda: {e}")
        return None

@cache.memoize(timeout=120)
def obtener_datos_cache():
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME
    ).execute()
    valores = result.get('values', [])
    return [fila + [''] * (27 - len(fila)) for fila in valores]  # Asegurar columnas mínimas

# Función para buscar datos por nombre o cédula
def buscar_datos_por_nombre_o_cedula(busqueda):
    datos = obtener_datos()
    for fila_index, fila in enumerate(datos):
        if len(fila) > 16:  # Asegura que al menos hay 17 columnas
            if fila[1].strip().lower() == busqueda.lower() or fila[16].strip() == busqueda:
                return fila_index, fila
    return None, None

def buscar_candidata_rapida(busqueda):
    datos = obtener_datos_cache()
    candidatos = []

    for fila_index, fila in enumerate(datos):
        if len(fila) >= 27:
            codigo = fila[15].strip().lower()
            nombre = fila[1].strip().lower()
            cedula = fila[14].strip()
            telefono = fila[3].strip()
            direccion = fila[4].strip().lower()
            estado = fila[16].strip().lower()
            inscripcion = fila[18].strip().lower()
            modalidad = fila[5].strip().lower()
            experiencia = fila[9].strip()
            referencias_laborales = fila[11].strip().lower()
            referencias_familiares = fila[12].strip().lower()

            
            campos_a_buscar = [
                codigo, nombre, cedula, telefono, direccion, estado, 
                inscripcion, modalidad, experiencia, referencias_laborales, referencias_familiares
            ]

            
            resultado = process.extractOne(busqueda.lower(), campos_a_buscar)

            if resultado and resultado[1] > 80:  
                candidatos.append({
                    'fila_index': fila_index + 1,
                    'codigo': fila[15],
                    'nombre': fila[1],
                    'ciudad': fila[4],
                    'cedula': fila[14],
                    'telefono': fila[3],
                    'direccion': fila[4],
                    'estado': fila[16],
                    'inscripcion': fila[18],
                    'modalidad': fila[5],
                    'experiencia': fila[9],
                    'referencias_laborales': fila[11],
                    'referencias_familiares': fila[12]
                })
    
    return candidatos


def buscar_fila_por_codigo_nombre_cedula(busqueda):
    """
    Busca la fila en la hoja de cálculo por código, nombre o cédula.
    Retorna el índice de la fila y la fila completa si se encuentra.
    """
    datos = obtener_datos()
    for fila_index, fila in enumerate(datos):
        if len(fila) >= 27:  # Asegúrate de que la fila tenga suficientes columnas
            codigo = fila[15].strip().lower()
            nombre = fila[1].strip().lower()
            cedula = fila[14].strip()
            if (
                busqueda.lower() == codigo or
                busqueda.lower() == nombre or
                busqueda == cedula
            ):
                return fila_index, fila  # Devuelve el índice de la fila y la fila completa
    return None, None  # No se encontró

def generar_codigo_unico():
    """
    Genera un código único para las candidatas en formato 'CAN-XXXXXX'.
    Se revisa la columna P (índice 15) de la hoja para encontrar los códigos existentes,
    y se incrementa el número hasta encontrar uno que no exista.
    """
    try:
        # Obtener los datos de la hoja (se espera que obtener_datos_editar() retorne una lista de listas)
        datos = obtener_datos_editar()
        if not datos:
            raise ValueError("No se pudieron obtener datos de la hoja.")

        # Extraer códigos existentes de la columna P (índice 15)
        codigos_existentes = {fila[15].strip() for fila in datos if len(fila) > 15 and fila[15].strip().startswith("CAN-")}
        
        # Empezar la generación de códigos
        numero = 1
        while True:
            nuevo_codigo = f"CAN-{str(numero).zfill(6)}"
            if nuevo_codigo not in codigos_existentes:
                return nuevo_codigo
            numero += 1

    except Exception as e:
        print(f"Error al generar código único: {e}")
        return None



# Función para guardar los datos en la hoja de cálculo
def guardar_datos_en_hoja():
    try:
        # Construimos la estructura de los datos a enviar
        rango = f"Nueva hoja!A1:Y"
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": datos_candidatas}
        ).execute()
        print("Datos actualizados en la hoja correctamente.")
    except Exception as e:
        print(f"Error al guardar datos en la hoja: {e}")


def buscar_datos_inscripcion(buscar):
    """
    Busca candidatas por Nombre (Columna B) o Cédula (Columna O).
    Permite trabajar con filas incompletas (sin inscripción, monto o fecha).
    """
    try:
        # 🔹 Obtener todas las filas de la hoja
        datos = obtener_datos_editar()
        resultados = []
        busqueda = normalizar_texto(buscar)  # 🔹 Normaliza la búsqueda

        for fila_index, fila in enumerate(datos):
            if len(fila) < 15:  # 🔹 Si la fila tiene menos columnas, la ignora
                continue 

            nombre = normalizar_texto(fila[1]) if len(fila) > 1 else ""
            cedula = fila[14].strip() if len(fila) > 14 else ""

            # 🔹 Comparación flexible (como en editar)
            if busqueda in nombre or busqueda == cedula:
                # 🔹 Asegurar que la fila tenga suficientes columnas
                while len(fila) < 25:
                    fila.append("")

                resultados.append({
                    'fila_index': fila_index + 1,  # 🔹 Índice de fila (1-based index)
                    'codigo': fila[15] if len(fila) > 15 else "",  # Código (P)
                    'nombre': fila[1] if len(fila) > 1 else "",  # Nombre (B)
                    'cedula': fila[14] if len(fila) > 14 else "",  # Cédula (O)
                    'estado': fila[16] if len(fila) > 16 else "",  # Estado (Q)
                    'inscripcion': fila[17] if len(fila) > 17 else "",  # Inscripción (R)
                    'monto': fila[18] if len(fila) > 18 else "",  # Monto (S)
                    'fecha': fila[19] if len(fila) > 19 else ""  # Fecha de Pago (T)
                })

        return resultados  # 🔹 Devuelve todas las coincidencias encontradas
    except Exception as e:
        print(f"❌ Error al buscar datos en inscripción: {e}")
        return []
# Ajuste en el manejo de datos
def procesar_fila(fila, fila_index):
    # Asegúrate de que la fila tenga el tamaño suficiente
    while len(fila) < 27:  # Asegúrate de tener al menos hasta la columna AA
        fila.append("")

    # Procesa los datos
    codigo = fila[15]
    nombre = fila[1]
    cedula = fila[14]
    estado = fila[18]
    monto = fila[19]
    fecha_inscripcion = fila[20]
    fecha_pago = fila[22]
    inicio = fila[23]
    monto_total = fila[24]
    porciento = fila[25]
    calificacion = fila[26]

    # Actualiza o devuelve los valores necesarios
    return {
        "codigo": codigo,
        "nombre": nombre,
        "cedula": cedula,
        "estado": estado,
        "monto": monto,
        "fecha_inscripcion": fecha_inscripcion,
        "fecha_pago": fecha_pago,
        "inicio": inicio,
        "monto_total": monto_total,
        "porciento": porciento,
        "calificacion": calificacion,
    }

def insertar_datos_en_hoja(fila_datos):
    """
    Inserta una fila de datos en la hoja de cálculo.
    
    :param fila_datos: Lista con los datos a insertar (en el orden de las columnas de la hoja).
    """
    try:
        # Especifica el rango donde se insertará (al final de la hoja)
        rango = "Nueva hoja!A:Y"  # Ajusta según el rango de tu hoja
        body = {"values": [fila_datos]}  # Convierte la fila en el formato esperado por la API

        # Llamada a la API para añadir datos al final
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",  # Usamos RAW para mantener el formato exacto de los datos
            insertDataOption="INSERT_ROWS",  # Inserta como nuevas filas
            body=body
        ).execute()

        print("Datos insertados correctamente en la hoja.")
        return True

    except Exception as e:
        print(f"Error al insertar datos en la hoja: {e}")
        return False


def buscar_candidatas_por_texto(busqueda):
    """
    Realiza una búsqueda más flexible en los datos de la hoja de cálculo.
    Retorna una lista de candidatas que coincidan con la búsqueda (parcial, sin acentos, etc.).
    """
    datos = obtener_datos()
    resultados = []

    # Normaliza el texto de búsqueda
    busqueda = normalizar_texto(busqueda)

    for fila in datos:
        if len(fila) >= 27:  # Asegúrate de que la fila tenga suficientes columnas
            codigo = normalizar_texto(fila[15])  # Columna O
            nombre = normalizar_texto(fila[1])  # Columna B
            cedula = fila[14]  # Columna R (sin normalizar)

            # Verifica si la búsqueda coincide de forma parcial
            if (
                busqueda in codigo or
                busqueda in nombre or
                busqueda in cedula
            ):
                resultados.append({
                    'codigo': fila[15],  # Columna P
                    'nombre': fila[1],  # Columna B
                    'edad': fila[2],    # Columna C
                    'telefono': fila[3],  # Columna D
                    'direccion': fila[4],  # Columna E
                    'modalidad': fila[5],  # Columna F
                    'experiencia': fila[9],  # Columna J
                    'cedula': fila[14],  # Columna O
                })

    return resultados

def buscar_en_columna(valor, columna_index):
    """
    Busca un valor dentro de una columna específica sin ser estricto.
    - No distingue mayúsculas y minúsculas.
    - Ignora espacios en blanco antes y después.
    - Permite coincidencias parciales.
    - Devuelve todas las coincidencias.
    """
    valor_normalizado = normalizar_texto(valor)  # 🔹 Convierte todo a minúsculas y elimina espacios extras
    datos = obtener_datos_editar()

    resultados = []
    for fila in datos:
        if len(fila) > columna_index:  # Evita errores si la fila tiene menos columnas
            texto_fila = normalizar_texto(fila[columna_index])
            if valor_normalizado in texto_fila:  # 🔹 Ahora permite coincidencias parciales
                resultados.append(fila)

    return resultados  # Devuelve todas las coincidencias encontradas



# Ruta de Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""
    if request.method == 'POST':
        usuario = request.form['usuario']
        clave = request.form['clave']
        
        # Validación del usuario
        if usuario in usuarios and check_password_hash(usuarios[usuario], clave):
            session['usuario'] = usuario
            return redirect(url_for('home'))  # Redirige al home después de iniciar sesión
        else:
            mensaje = "Usuario o clave incorrectos."

    return render_template('login.html', mensaje=mensaje)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")

# Ruta de Logout
@app.route('/logout')
def logout():
    session.pop('usuario', None)  # Cierra la sesión
    return redirect(url_for('login'))

# Ruta protegida (Home)
@app.route('/')
def home():
    if 'usuario' not in session:
        return redirect(url_for('login'))  # Redirige al login si no está autenticado
    return render_template('home.html', usuario=session['usuario'])


@app.route('/sugerir')
def sugerir():
    query = request.args.get('busqueda', '')
    if not query:
        return jsonify([])

    # Aquí deberías obtener los datos de la cache o de la base de datos
    datos_filtrados = [dato for dato in lista_candidatas if query.lower() in dato['nombre'].lower()]
    
    return jsonify(datos_filtrados)

@app.route('/entrevista', methods=['GET', 'POST'])
def entrevista():
    # Obtener parámetros de la URL: tipo de entrevista y fila (opcional)
    tipo_entrevista = request.args.get("tipo", "").strip().lower()
    fila_param = request.args.get("fila", "").strip()

    if tipo_entrevista not in ENTREVISTAS_CONFIG:
        return "⚠️ Tipo de entrevista no válido.", 400

    entrevista_config = ENTREVISTAS_CONFIG[tipo_entrevista]
    titulo = entrevista_config.get("titulo", "Entrevista sin título")
    preguntas = entrevista_config.get("preguntas", [])

    mensaje = None
    datos = {}         # Aquí se almacenarán los datos ingresados
    focus_field = None # El id del primer campo faltante

    if request.method == 'POST':
        respuestas = []
        missing_fields = []
        # Recorrer todas las preguntas
        for pregunta in preguntas:
            campo_id = pregunta['id']
            valor = request.form.get(campo_id, '').strip()
            datos[campo_id] = valor  # Guardamos el valor ingresado
            if not valor:
                missing_fields.append(campo_id)
            linea = f"{pregunta['enunciado']}: {valor}"
            respuestas.append(linea)
        
        if missing_fields:
            mensaje = "Por favor, complete todos los campos."
            focus_field = missing_fields[0]  # Primer campo faltante
            # Re-renderizamos el formulario conservando los datos ingresados
            return render_template("entrevista_dinamica.html",
                                   titulo=titulo,
                                   preguntas=preguntas,
                                   mensaje=mensaje,
                                   datos=datos,
                                   focus_field=focus_field)
        
        entrevista_completa = "\n".join(respuestas)
        
        if fila_param.isdigit():
            fila_index = int(fila_param)
        else:
            fila_index = obtener_siguiente_fila()
        
        if fila_index is None:
            mensaje = "❌ Error: No se pudo determinar la fila libre."
        else:
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Nueva hoja!Z{fila_index}",
                    valueInputOption="RAW",
                    body={"values": [[entrevista_completa]]}
                ).execute()
                mensaje = f"✅ Entrevista guardada correctamente en la fila {fila_index}."
            except Exception as e:
                mensaje = f"❌ Error al guardar la entrevista: {str(e)}"
    
    return render_template("entrevista_dinamica.html",
                           titulo=titulo,
                           preguntas=preguntas,
                           mensaje=mensaje,
                           datos=datos,
                           focus_field=focus_field)




@app.route('/buscar_candidata', methods=['GET', 'POST'])
def buscar_candidata():
    resultados = []
    mensaje = None

    if request.method == 'POST':
        busqueda = request.form.get('busqueda', '').strip().lower()

        try:
            # Lee la hoja de cálculo (ajusta el rango a tus columnas)
            hoja = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Nueva hoja!A:Z"
            ).execute()
            valores = hoja.get("values", [])

            # Itera sobre cada fila (desde la 2 en adelante)
            for fila_index, fila in enumerate(valores[1:], start=2):
                # Supongamos que el nombre está en la columna B (fila[1])
                if len(fila) > 1:
                    nombre = fila[1].strip().lower()
                else:
                    nombre = ""

                # Coincidencia parcial
                if busqueda in nombre:
                    resultados.append({
                        'fila_index': fila_index,
                        'nombre': fila[1] if len(fila) > 1 else "No especificado",
                        'telefono': fila[3] if len(fila) > 3 else "No especificado",
                        # Agrega más campos si deseas
                    })

        except Exception as e:
            mensaje = f"❌ Error al buscar: {str(e)}"

    return render_template('buscar_candidata.html', resultados=resultados, mensaje=mensaje)




@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    resultados = []
    candidata_detalles = None
    mensaje = None

    # Capturar parámetros
    busqueda_input = request.form.get('busqueda', '').strip().lower()  # Para POST (búsqueda)
    candidata_param = request.args.get('candidata', '').strip()        # Para GET (ver detalles)

    try:
        # 1) Cargar siempre los datos de la hoja
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!B:O"
        ).execute()
        valores = hoja.get("values", [])

        if not valores or len(valores) < 2:
            return render_template('buscar.html', resultados=[], candidata=None,
                                   mensaje="⚠️ No hay datos disponibles.")

        # 2) Si hay término de búsqueda (POST), filtrar resultados
        if busqueda_input:
            # Ejemplo de filtrado flexible
            resultados = filtrar_por_busqueda(valores[1:], busqueda_input)
        
        # 3) Si se pasa un parámetro 'candidata' por GET, cargar sus detalles
        if candidata_param:
            # Cargar detalles sin requerir busqueda_input
            candidata_detalles = cargar_detalles_candidata(valores, candidata_param)

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        return render_template('buscar.html', resultados=[], candidata=None, mensaje=mensaje)

    return render_template('buscar.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)



@app.route('/editar', methods=['GET', 'POST'])
def editar():
    resultados = []
    candidata_detalles = None
    mensaje = None

    # Primero: Cargar la hoja de Google Sheets (rango "Nueva hoja!B:O")
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!B:O"
        ).execute()
        valores = hoja.get("values", [])
        if not valores or len(valores) < 2:
            mensaje = "⚠️ No hay datos disponibles."
            return render_template('editar.html', resultados=resultados, candidata=None, mensaje=mensaje)
    except Exception as e:
        mensaje = f"Error al obtener datos: {str(e)}"
        return render_template('editar.html', resultados=resultados, candidata=None, mensaje=mensaje)

    # Caso A: GET con "candidata_seleccionada" para ver detalles
    if request.method == 'GET' and request.args.get('candidata_seleccionada'):
        candidata_id = request.args.get('candidata_seleccionada').strip()
        try:
            fila_index = int(candidata_id)
            # El arreglo "valores" incluye la cabecera en posición 0, por lo que fila_index coincide con el número de fila real
            fila = valores[fila_index - 1]
            candidata_detalles = {
                'fila_index': fila_index,
                'nombre': fila[0] if len(fila) > 0 else "No especificado",
                'edad': fila[1] if len(fila) > 1 else "No especificado",
                'telefono': fila[2] if len(fila) > 2 else "No especificado",
                'direccion': fila[3] if len(fila) > 3 else "No especificado",
                'modalidad': fila[4] if len(fila) > 4 else "No especificado",
                'rutas': fila[5] if len(fila) > 5 else "No especificado",
                'empleo_anterior': fila[6] if len(fila) > 6 else "No especificado",
                'anos_experiencia': fila[7] if len(fila) > 7 else "No especificado",
                'areas_experiencia': fila[8] if len(fila) > 8 else "No especificado",
                'sabe_planchar': fila[9] if len(fila) > 9 else "No especificado",
                'referencias_laborales': fila[10] if len(fila) > 10 else "No especificado",
                'referencias_familiares': fila[11] if len(fila) > 11 else "No especificado",
                'acepta_porcentaje': fila[12] if len(fila) > 12 else "No especificado",
                'cedula': fila[13] if len(fila) > 13 else "No especificado",
            }
        except Exception as e:
            mensaje = f"Error al cargar detalles: {str(e)}"

    # Caso B: POST para guardar cambios (se detecta con el campo "guardar_edicion")
    elif request.method == 'POST' and request.form.get('guardar_edicion'):
        try:
            fila_index = request.form.get('fila_index', '').strip()
            if not fila_index or not fila_index.isdigit():
                mensaje = "Error: No se pudo determinar la fila a actualizar."
            else:
                fila_index = int(fila_index)
                nuevos_datos = {
                    'nombre': request.form.get('nombre', '').strip(),
                    'edad': request.form.get('edad', '').strip(),
                    'telefono': request.form.get('telefono', '').strip(),
                    'direccion': request.form.get('direccion', '').strip(),
                    'modalidad': request.form.get('modalidad', '').strip(),
                    'rutas': request.form.get('rutas', '').strip(),
                    'empleo_anterior': request.form.get('empleo_anterior', '').strip(),
                    'anos_experiencia': request.form.get('anos_experiencia', '').strip(),
                    'areas_experiencia': request.form.get('areas_experiencia', '').strip(),
                    'sabe_planchar': request.form.get('sabe_planchar', '').strip(),
                    'referencias_laborales': request.form.get('referencias_laborales', '').strip(),
                    'referencias_familiares': request.form.get('referencias_familiares', '').strip(),
                    'acepta_porcentaje': request.form.get('acepta_porcentaje', '').strip(),
                    'cedula': request.form.get('cedula', '').strip()
                }
                columnas = {
                    'nombre': "B",
                    'edad': "C",
                    'telefono': "D",
                    'direccion': "E",
                    'modalidad': "F",
                    'rutas': "G",
                    'empleo_anterior': "H",
                    'anos_experiencia': "I",
                    'areas_experiencia': "J",
                    'sabe_planchar': "K",
                    'referencias_laborales': "L",
                    'referencias_familiares': "M",
                    'acepta_porcentaje': "N",
                    'cedula': "O"
                }
                for campo, valor in nuevos_datos.items():
                    if valor:  # Actualiza solo si se proporcionó un valor
                        rango = f'Nueva hoja!{columnas[campo]}{fila_index}'
                        service.spreadsheets().values().update(
                            spreadsheetId=SPREADSHEET_ID,
                            range=rango,
                            valueInputOption="RAW",
                            body={"values": [[valor]]}
                        ).execute()
                mensaje = "Los datos fueron actualizados correctamente."
                # Recargar detalles actualizados
                fila = valores[fila_index - 1]
                candidata_detalles = {
                    'fila_index': fila_index,
                    'nombre': fila[0] if len(fila) > 0 else "No especificado",
                    'edad': fila[1] if len(fila) > 1 else "No especificado",
                    'telefono': fila[2] if len(fila) > 2 else "No especificado",
                    'direccion': fila[3] if len(fila) > 3 else "No especificado",
                    'modalidad': fila[4] if len(fila) > 4 else "No especificado",
                    'rutas': fila[5] if len(fila) > 5 else "No especificado",
                    'empleo_anterior': fila[6] if len(fila) > 6 else "No especificado",
                    'anos_experiencia': fila[7] if len(fila) > 7 else "No especificado",
                    'areas_experiencia': fila[8] if len(fila) > 8 else "No especificado",
                    'sabe_planchar': fila[9] if len(fila) > 9 else "No especificado",
                    'referencias_laborales': fila[10] if len(fila) > 10 else "No especificado",
                    'referencias_familiares': fila[11] if len(fila) > 11 else "No especificado",
                    'acepta_porcentaje': fila[12] if len(fila) > 12 else "No especificado",
                    'cedula': fila[13] if len(fila) > 13 else "No especificado",
                }
        except Exception as e:
            mensaje = f"Error al actualizar datos: {str(e)}"

    # Caso C: Búsqueda simple (sin parámetros de selección ni guardado)
    else:
        busqueda = request.values.get('busqueda', '').strip().lower()

    # Si aún no se cargaron detalles (caso C o si no se seleccionó candidata), se generan los resultados de búsqueda
    if not candidata_detalles:
        for fila_index, fila in enumerate(valores[1:], start=2):
            nombre = fila[0].strip().lower() if len(fila) > 0 else ""
            cedula = fila[13].strip() if len(fila) > 13 else ""
            if busqueda and not (busqueda in nombre or busqueda in cedula):
                continue
            resultados.append({
                'fila_index': fila_index,
                'nombre': fila[0] if len(fila) > 0 else "No especificado",
                'telefono': fila[2] if len(fila) > 2 else "No especificado",
                'direccion': fila[3] if len(fila) > 3 else "No especificado",
                'cedula': fila[13] if len(fila) > 13 else "No especificado",
            })

    return render_template('editar.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)



@app.route('/filtrar', methods=['GET', 'POST'])
def filtrar():
    resultados = []  
    mensaje = None  

    try:
        # Obtener los datos de la hoja de cálculo
        datos = obtener_datos_filtrar()
        print(f"🔍 Datos obtenidos ({len(datos)} filas)")

        if not datos:
            mensaje = "⚠️ No se encontraron datos en la hoja de cálculo."
            return render_template('filtrar.html', resultados=[], mensaje=mensaje)

        # 🔹 Mostrar TODAS las candidatas inscritas al cargar la página (sin filtros)
        for fila in datos:
            if len(fila) < 18:  
                continue  

            inscripcion_fila = fila[17].strip().lower()  # Índice 17: Inscripción

            if inscripcion_fila == "sí":  # Solo mostrar inscritas
                resultados.append({
                    'codigo': fila[15] if len(fila) > 15 else "",  # Código en P (15)
                    'estado': fila[16] if len(fila) > 16 else "",  # Estado en Q (16)
                    'inscripcion': fila[17],  # Inscripción en R (17)
                    'nombre': fila[1],  # Nombre en B (1)
                    'edad': fila[2] if len(fila) > 2 else "",  
                    'telefono': fila[3] if len(fila) > 3 else "",  
                    'direccion': fila[4],  # Dirección en E (4)
                    'modalidad': fila[5],  # Modalidad en F (5)
                    'experiencia_anos': fila[8],  # Años de experiencia en I (8)
                    'areas_experiencia': fila[9],  # Áreas de experiencia en J (9)
                    'cedula': fila[14] if len(fila) > 14 else "",  # Cédula en O (14)
                })

        # 🔹 Aplicar filtros si se hace una búsqueda
        if request.method == 'POST':
            ciudad = request.form.get('ciudad', '').strip().lower()
            modalidad = request.form.get('modalidad', '').strip().lower()
            experiencia_anos = request.form.get('experiencia_anos', '').strip().lower()
            areas_experiencia = request.form.get('areas_experiencia', '').strip().lower()

            resultados_filtrados = []
            for candidata in resultados:
                # 🔹 Coincidencias parciales y filtros flexibles
                cumple_ciudad = ciudad in candidata['direccion'].lower() if ciudad else True
                cumple_modalidad = modalidad in candidata['modalidad'].lower() if modalidad else True
                cumple_experiencia = experiencia_anos in candidata['experiencia_anos'].lower() if experiencia_anos else True
                cumple_areas_experiencia = areas_experiencia in candidata['areas_experiencia'].lower() if areas_experiencia else True

                if cumple_ciudad and cumple_modalidad and cumple_experiencia and cumple_areas_experiencia:
                    resultados_filtrados.append(candidata)

            # Si no se encuentra nada, mostrar todas las candidatas inscritas
            if resultados_filtrados:
                resultados = resultados_filtrados
            else:
                mensaje = "⚠️ No se encontraron resultados para los filtros aplicados. Mostrando todas las candidatas inscritas."

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"

    return render_template('filtrar.html', resultados=resultados, mensaje=mensaje)

import traceback  # Importa para depuración

@app.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion():
    mensaje = ""
    datos_candidata = {}
    resultados = []  # Para almacenar múltiples coincidencias en la búsqueda

    # --- 1. Cargar la hoja completa en el rango "Nueva hoja!A:T" ---
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:T"  # A hasta T (columnas 0 a 19)
        ).execute()
        valores = hoja.get("values", [])
        if not valores or len(valores) < 2:
            mensaje = "⚠️ No hay datos disponibles en la hoja."
            return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
    except Exception as e:
        mensaje = f"Error al obtener datos: {str(e)}"
        return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)

    # --- Función interna para buscar candidatas ---
    def buscar_candidatas(query):
        coincidencias = []
        # Se asume: Nombre en columna B (índice 1), Cédula en columna O (índice 14), Código en columna P (índice 15)
        for idx, fila in enumerate(valores[1:], start=2):
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""
            cedula = fila[14].strip().lower() if len(fila) > 14 else ""
            if query.lower() in nombre or query.lower() in cedula:
                coincidencias.append({
                    "fila_index": idx,
                    "codigo": fila[15] if len(fila) > 15 else "",
                    "nombre": fila[1] if len(fila) > 1 else "",
                    "cedula": fila[14] if len(fila) > 14 else ""
                })
        return coincidencias

    # --- 2. Procesar solicitud ---
    if request.method == "POST":
        # Caso A: Guardar inscripción
        if request.form.get("guardar_inscripcion"):
            try:
                fila_index_str = request.form.get("fila_index", "").strip()
                if not fila_index_str or not fila_index_str.isdigit():
                    mensaje = "Error: Índice de fila inválido."
                else:
                    fila_index = int(fila_index_str)
                    if fila_index < 2 or fila_index > len(valores):
                        mensaje = "Índice de fila fuera de rango."
                    else:
                        # Acceder a la hoja completa usando 'client'
                        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Nueva hoja")
                        # Leer la fila completa (A:T)
                        fila_actual = sheet.row_values(fila_index)
                        # Verificar el código en columna P (índice 15)
                        codigo_actual = fila_actual[15] if len(fila_actual) > 15 else ""
                        if not codigo_actual.strip():
                            nuevo_codigo = generar_codigo_unico()
                            sheet.update(f"P{fila_index}", [[nuevo_codigo]])
                            fila_actual = sheet.row_values(fila_index)  # Releer para refrescar
                        else:
                            nuevo_codigo = codigo_actual

                        # Recoger datos de inscripción
                        medio = request.form.get("medio", "").strip()  # Nuevo campo: Medio de inscripción
                        estado = request.form.get("estado", "").strip()
                        monto = request.form.get("monto", "").strip()
                        fecha = request.form.get("fecha", "").strip()
                        # Actualizar columnas Q (medio), R (estado), S (monto) y T (fecha)
                        sheet.update(f"Q{fila_index}:T{fila_index}", [[medio, estado, monto, fecha]])

                        mensaje = "Inscripción guardada correctamente."
                        # Reconstruir datos de la candidata usando la fila actualizada
                        datos_candidata = {
                            "fila_index": fila_index,
                            "codigo": fila_actual[15] if len(fila_actual) > 15 else "",
                            "nombre": fila_actual[1] if len(fila_actual) > 1 else "No disponible",
                            "cedula": fila_actual[14] if len(fila_actual) > 14 else "No disponible",
                            "telefono": fila_actual[3] if len(fila_actual) > 3 else "No disponible",
                            "direccion": fila_actual[4] if len(fila_actual) > 4 else "No disponible",
                            "medio": fila_actual[16] if len(fila_actual) > 16 else "No disponible"
                        }
            except Exception as e:
                mensaje = f"Error al guardar la inscripción: {str(e)}"
            return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
        else:
            # Caso B: Búsqueda (POST)
            query = request.form.get("buscar", "").strip()
            if query:
                coincidencias = buscar_candidatas(query)
                if coincidencias:
                    resultados = coincidencias
                else:
                    mensaje = "⚠️ No se encontraron coincidencias."
            return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
    else:
        # GET: ver detalles o búsqueda
        candidata_param = request.args.get("candidata_seleccionada", "").strip()
        if candidata_param:
            try:
                fila_index = int(candidata_param)
                if fila_index < 2 or fila_index > len(valores):
                    mensaje = "Índice de fila fuera de rango."
                else:
                    fila = valores[fila_index - 1]
                    datos_candidata = {
                        "fila_index": fila_index,
                        "codigo": fila[15] if len(fila) > 15 and fila[15].strip() else "Se generará automáticamente",
                        "nombre": fila[1] if len(fila) > 1 else "No disponible",
                        "cedula": fila[14] if len(fila) > 14 else "No disponible",
                        "telefono": fila[3] if len(fila) > 3 else "No disponible",
                        "direccion": fila[4] if len(fila) > 4 else "No disponible",
                        "medio": fila[16] if len(fila) > 16 else "No disponible"
                    }
            except Exception as e:
                mensaje = f"Error al cargar detalles: {str(e)}"
        else:
            query = request.args.get("query", "").strip() or request.args.get("buscar", "").strip()
            if query:
                coincidencias = buscar_candidatas(query)
                if coincidencias:
                    resultados = coincidencias
                else:
                    mensaje = "⚠️ No se encontraron coincidencias."
        return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)



@app.route('/porciento', methods=['GET', 'POST'])
def porciento():
    resultados = []
    candidata_detalles = None
    busqueda = request.form.get('busqueda', '').strip().lower()
    candidata_id = request.args.get('candidata', '').strip()

    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"  # Incluye hasta la columna Y
        ).execute()

        valores = hoja.get("values", [])

        for fila_index, fila in enumerate(valores[1:], start=2):  # Empezamos en la fila 2
            codigo = fila[15] if len(fila) > 15 else ""

            if not codigo.strip():  # Filtrar solo las que tienen código
                continue

            nombre = fila[1].strip().lower() if len(fila) > 1 else ""
            cedula = fila[14].strip() if len(fila) > 14 else ""

            if busqueda and (busqueda in nombre or busqueda in cedula):
                resultados.append({
                    'fila_index': fila_index,
                    'codigo': fila[15] if len(fila) > 15 else "",
                    'nombre': fila[1] if len(fila) > 1 else "",
                    'telefono': fila[3] if len(fila) > 3 else "",
                    'cedula': fila[14] if len(fila) > 14 else "",
                })

            if candidata_id and str(fila_index) == candidata_id:
                candidata_detalles = {
                    'fila_index': fila_index,
                    'codigo': fila[15] if len(fila) > 15 else "",
                    'nombre': fila[1] if len(fila) > 1 else "",
                    'fecha_pago': fila[20] if len(fila) > 20 else "",
                    'fecha_inicio': fila[21] if len(fila) > 21 else "",
                    'monto_total': fila[22] if len(fila) > 22 else "",
                    'porcentaje': fila[23] if len(fila) > 23 else "",
                    'calificacion': fila[24] if len(fila) > 24 else "",
                }

    except Exception as e:
        print(f"❌ Error en la búsqueda: {e}")

    return render_template('porciento.html', resultados=resultados, candidata=candidata_detalles)


@app.route('/guardar_porciento', methods=['POST'])
def guardar_porciento():
    try:
        fila_index = request.form.get('fila_index')
        if not fila_index or not fila_index.isdigit():
            return "Error: No se pudo determinar la fila a actualizar."

        fila_index = int(fila_index)

        monto_total = request.form.get('monto_total', '').strip()
        porcentaje = str(round(float(monto_total) * 0.25, 2)) if monto_total else ""

        nuevos_datos = {
            'fecha_pago': request.form.get('fecha_pago', '').strip(),
            'fecha_inicio': request.form.get('fecha_inicio', '').strip(),
            'monto_total': monto_total,
            'porcentaje': porcentaje,
            'calificacion': request.form.get('calificacion', '').strip()
        }

        columnas = {
            'fecha_pago': "U",
            'fecha_inicio': "V",
            'monto_total': "W",
            'porcentaje': "X",
            'calificacion': "Y"
        }

        for campo, valor in nuevos_datos.items():
            if valor:
                rango = f'Nueva hoja!{columnas[campo]}{fila_index}'
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango,
                    valueInputOption="RAW",
                    body={"values": [[valor]]}
                ).execute()

        return "✅ Datos actualizados correctamente."

    except Exception as e:
        return f"❌ Error al actualizar: {str(e)}"

@app.route('/buscar_pagos', methods=['GET', 'POST'])
def buscar_pagos():
    resultados = []
    candidata_detalles = None
    busqueda = request.form.get('busqueda', '').strip().lower()
    candidata_id = request.args.get('candidata', '')

    print(f"🔍 Buscando: {busqueda}")  # <-- ¿Se está enviando la búsqueda?

    if busqueda:
        try:
            hoja = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Nueva hoja!A:Y"
            ).execute()

            valores = hoja.get("values", [])

            print(f"📜 Datos de la hoja: {len(valores)} filas cargadas")  # <-- Verifica si está leyendo datos

            for fila_index, fila in enumerate(valores[1:], start=2):  
                nombre = fila[1].strip().lower() if len(fila) > 1 else ""
                cedula = fila[14].strip() if len(fila) > 14 else ""
                codigo = fila[15] if len(fila) > 15 else ""

                if busqueda in nombre or busqueda in cedula or busqueda == codigo:
                    print(f"✅ Candidata encontrada: {nombre}, {cedula}, {codigo}")  # <-- Muestra si encuentra algo
                    resultados.append({
                        'fila_index': fila_index,
                        'nombre': fila[1] if len(fila) > 1 else "No especificado",
                        'telefono': fila[3] if len(fila) > 3 else "No especificado",
                        'cedula': fila[14] if len(fila) > 14 else "No especificado",
                        'codigo': fila[15] if len(fila) > 15 else "No especificado"
                    })

        except Exception as e:
            print(f"❌ Error en la búsqueda: {e}")

    return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles)

@app.route('/pagos', methods=['GET', 'POST'])
def pagos():
    resultados = []
    candidata_detalles = None
    busqueda = request.form.get('busqueda', '').strip().lower()
    candidata_id = request.args.get('candidata', '').strip()

    try:
        # Obtener los datos de la hoja de cálculo
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"  # Asegura incluir hasta la columna Y
        ).execute()
        valores = hoja.get("values", [])

        if not valores or len(valores) < 2:
            return render_template('pagos.html', resultados=[], candidata=None, mensaje="⚠️ No hay datos disponibles.")

        # 🔹 Búsqueda flexible por nombre
        for fila_index, fila in enumerate(valores[1:], start=2):  # Empezar en la segunda fila
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""  # Columna B

            if busqueda and busqueda in nombre:
                resultados.append({
                    'fila_index': fila_index,
                    'nombre': fila[1] if len(fila) > 1 else "No especificado",
                    'telefono': fila[3] if len(fila) > 3 else "No especificado",
                    'cedula': fila[14] if len(fila) > 14 else "No especificado",
                    'monto_total': fila[22] if len(fila) > 22 else "0",  # W
                    'porcentaje': fila[23] if len(fila) > 23 else "0",  # X
                    'fecha_pago': fila[20] if len(fila) > 20 else "No registrada",  # U
                    'calificacion': fila[24] if len(fila) > 24 else "",  # Y
                })

        # 🔹 Cargar detalles si se seleccionó una candidata
        if candidata_id:
            fila_index = int(candidata_id)  # Convertir ID a número de fila
            fila = valores[fila_index - 1]  # Ajustar índice (Sheets empieza en 1)

            candidata_detalles = {
                'fila_index': fila_index,
                'nombre': fila[1] if len(fila) > 1 else "No especificado",
                'telefono': fila[3] if len(fila) > 3 else "No especificado",
                'cedula': fila[14] if len(fila) > 14 else "No especificado",
                'monto_total': fila[22] if len(fila) > 22 else "0",  # W
                'porcentaje': fila[23] if len(fila) > 23 else "0",  # X
                'fecha_pago': fila[20] if len(fila) > 20 else "No registrada",  # U
                'calificacion': fila[24] if len(fila) > 24 else "",  # Y
            }

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        return render_template('pagos.html', resultados=[], candidata=None, mensaje=mensaje)

    return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles)


@app.route('/guardar_pago', methods=['POST'])
def guardar_pago():
    try:
        fila_index = int(request.form.get('fila_index'))
        monto_pagado = request.form.get('monto_pagado', '').strip()

        if not monto_pagado:
            return render_template('pagos.html', mensaje="❌ Error: Ingrese un monto válido.")

        # 🔹 Convertir correctamente el monto pagado
        monto_pagado = float(monto_pagado)  # Ahora admite valores decimales

        # Obtener datos actuales de la columna X (porcentaje pendiente)
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"Nueva hoja!X{fila_index}"
        ).execute()
        valores = hoja.get("values", [])

        saldo_actual = float(valores[0][0]) if valores and valores[0] else 0  # Convertir a float para evitar errores
        nuevo_saldo = saldo_actual - monto_pagado

        # Asegurar que el saldo no sea negativo
        nuevo_saldo = max(nuevo_saldo, 0)

        # 🔹 Actualizar la columna X con el nuevo saldo
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"Nueva hoja!X{fila_index}",
            valueInputOption="RAW",
            body={"values": [[nuevo_saldo]]}  # Guardar como número decimal sin errores
        ).execute()

        # Mensaje de éxito
        return render_template('pagos.html', mensaje="✅ Pago guardado con éxito.")

    except Exception as e:
        return render_template('pagos.html', mensaje=f"❌ Error al guardar los datos: {str(e)}")

@app.route('/reporte_pagos', methods=['GET'])
def reporte_pagos():
    pagos_pendientes = []

    try:
        # Obtener los datos de la hoja de cálculo
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"  # Asegura incluir hasta la columna X (Porcentaje pendiente)
        ).execute()
        valores = hoja.get("values", [])

        if not valores or len(valores) < 2:
            return render_template('reporte_pagos.html', pagos_pendientes=[], mensaje="⚠️ No hay datos disponibles.")

        for fila in valores[1:]:  # Excluir encabezados
            try:
                # Extraer valores y limpiar datos vacíos
                nombre = fila[1] if len(fila) > 1 else "No especificado"
                cedula = fila[14] if len(fila) > 14 else "No especificado"
                codigo = fila[15] if len(fila) > 15 else "No especificado"
                ciudad = fila[4] if len(fila) > 4 else "No especificado"
                monto_total = float(fila[22]) if len(fila) > 22 and fila[22].strip() else 0  # Columna W
                porcentaje_pendiente = float(fila[23]) if len(fila) > 23 and fila[23].strip() else 0  # Columna X
                fecha_inicio = fila[20] if len(fila) > 20 else "No registrada"  # Columna V
                fecha_pago = fila[21] if len(fila) > 21 else "No registrada"  # Columna U

                # Filtrar solo las candidatas que deben dinero
                if porcentaje_pendiente > 0:
                    pagos_pendientes.append({
                        'nombre': nombre,
                        'cedula': cedula,
                        'codigo': codigo,
                        'ciudad': ciudad,
                        'monto_total': monto_total,
                        'porcentaje_pendiente': porcentaje_pendiente,
                        'fecha_inicio': fecha_inicio,
                        'fecha_pago': fecha_pago
                    })
            except ValueError:
                continue  # Evitar errores si un dato no es convertible a número

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        return render_template('reporte_pagos.html', pagos_pendientes=[], mensaje=mensaje)

    return render_template('reporte_pagos.html', pagos_pendientes=pagos_pendientes)

@app.route('/generar_pdf')
def generar_pdf():
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"
        ).execute()
        valores = hoja.get("values", [])

        if not valores or len(valores) < 2:
            return "⚠️ No hay datos disponibles para generar el PDF."

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Arial", "B", 12)
        pdf.cell(200, 10, "Reporte de Pagos Pendientes", ln=True, align="C")
        pdf.ln(10)

        pdf.set_font("Arial", "", 10)
        for fila in valores[1:]:
            try:
                porcentaje_pendiente = float(fila[23]) if len(fila) > 23 and fila[23].strip() else 0
                if porcentaje_pendiente > 0:
                    nombre = fila[1] if len(fila) > 1 else "No especificado"
                    cedula = fila[14] if len(fila) > 14 else "No especificado"
                    codigo = fila[15] if len(fila) > 15 else "No especificado"
                    ciudad = fila[4] if len(fila) > 4 else "No especificado"
                    monto_total = fila[22] if len(fila) > 22 else "0"
                    fecha_inicio = fila[20] if len(fila) > 20 else "No registrada"
                    fecha_pago = fila[21] if len(fila) > 21 else "No registrada"

                    pdf.cell(0, 8, f"Nombre: {nombre} | Cédula: {cedula} | Código: {codigo}", ln=True)
                    pdf.cell(0, 8, f"Ciudad: {ciudad} | Monto Total: {monto_total} | Pendiente: {porcentaje_pendiente}", ln=True)
                    pdf.cell(0, 8, f"Inicio: {fecha_inicio} | Pago: {fecha_pago}", ln=True)
                    pdf.ln(5)
            except ValueError:
                continue

        pdf.output("reporte_pagos.pdf")
        return send_file("reporte_pagos.pdf", as_attachment=True)

    except Exception as e:
        return f"❌ Error al generar PDF: {str(e)}"

@app.route("/subir_fotos", methods=["GET", "POST"])
def subir_fotos():
    accion = request.args.get("accion", "buscar").strip()  # Por defecto "buscar"
    mensaje = None
    resultados = []
    fila = request.args.get("fila", "").strip()

    # 1. Acción BUSCAR: mostrar formulario y procesar búsqueda
    if accion == "buscar":
        if request.method == "POST":
            busqueda = request.form.get("busqueda", "").strip().lower()
            try:
                hoja = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Nueva hoja!A:Z"
                ).execute()
                valores = hoja.get("values", [])
                # Ajusta índices de columna según tu hoja
                for fila_index, fila_vals in enumerate(valores[1:], start=2):
                    nombre = (fila_vals[1].strip().lower() if len(fila_vals) > 1 else "")
                    cedula = (fila_vals[14].strip().lower() if len(fila_vals) > 14 else "")
                    telefono = (fila_vals[3].strip().lower() if len(fila_vals) > 3 else "")
                    # Coincidencia flexible
                    if busqueda in nombre or busqueda in cedula or busqueda in telefono:
                        resultados.append({
                            "fila_index": fila_index,
                            "nombre": fila_vals[1] if len(fila_vals) > 1 else "No especificado",
                            "telefono": fila_vals[3] if len(fila_vals) > 3 else "No especificado",
                            "cedula": fila_vals[14] if len(fila_vals) > 14 else "No especificado"
                        })
            except Exception as e:
                mensaje = f"Error al buscar: {str(e)}"
        return render_template("subir_fotos.html", accion=accion, mensaje=mensaje, resultados=resultados)

    # 2. Acción SUBIR: mostrar o procesar formulario de subida
    if accion == "subir":
        if request.method == "GET":
            # Muestra el formulario de subida
            return render_template("subir_fotos.html", accion=accion, fila=fila)

        if request.method == "POST":
            # Subir a Cloudinary
            if not fila.isdigit():
                mensaje = "Error: La fila debe ser un número válido."
                return render_template("subir_fotos.html", accion=accion, fila=fila, mensaje=mensaje)

            fila_index = int(fila)

            depuracion_file = request.files.get("depuracion")
            perfil_file = request.files.get("perfil")
            cedula1_file = request.files.get("cedula1")
            cedula2_file = request.files.get("cedula2")

            # Helper para subir
            def subir_a_cloudinary(archivo, folder):
                if archivo:
                    resp = cloudinary.uploader.upload(archivo, folder=folder)
                    return resp.get("secure_url", "")
                return ""

            subcarpeta = f"candidata_{fila_index}"
            try:
                depuracion_url = subir_a_cloudinary(depuracion_file, subcarpeta)
                perfil_url = subir_a_cloudinary(perfil_file, subcarpeta)
                cedula1_url = subir_a_cloudinary(cedula1_file, subcarpeta)
                cedula2_url = subir_a_cloudinary(cedula2_file, subcarpeta)
            except Exception as e:
                mensaje = f"Error subiendo a Cloudinary: {str(e)}"
                return render_template("subir_fotos.html", accion=accion, fila=fila, mensaje=mensaje)

            # Guardar en Google Sheets en AA, AB, AC, AD
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Nueva hoja!AA{fila_index}:AD{fila_index}",
                    valueInputOption="RAW",
                    body={"values": [[depuracion_url, perfil_url, cedula1_url, cedula2_url]]}
                ).execute()
                mensaje = "Imágenes subidas y guardadas correctamente."
            except Exception as e:
                mensaje = f"Error al actualizar Google Sheets: {str(e)}"

            return render_template("subir_fotos.html", accion=accion, fila=fila, mensaje=mensaje)

    # Si no coincide, redirigimos a buscar
    return redirect("/subir_fotos?accion=buscar")


@app.route('/descargar_documentos', methods=["GET"])
def descargar_documentos():
    fila = request.args.get("fila", "").strip()
    if not fila.isdigit():
        return "Error: La fila debe ser un número válido.", 400
    fila_index = int(fila)
    
    try:
        # Lee SOLO AA a AD (4 columnas)
        rango = f"Nueva hoja!AA{fila_index}:AD{fila_index}"
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango
        ).execute()
        row_values = hoja.get("values", [])
        if not row_values or len(row_values[0]) < 4:
            return "No se encontraron suficientes datos en la fila especificada.", 404

        # Obtenemos 4 valores
        depuracion_url, perfil_url, cedula1_url, cedula2_url = row_values[0][:4]
    except Exception as e:
        return f"Error al leer Google Sheets: {str(e)}", 500

    # Crear el ZIP en memoria
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w") as zf:
        archivos = {
            "depuracion.png": depuracion_url,
            "perfil.png": perfil_url,
            "cedula1.png": cedula1_url,
            "cedula2.png": cedula2_url
        }
        for nombre_archivo, url in archivos.items():
            if url:
                try:
                    r = requests.get(url)
                    r.raise_for_status()
                    zf.writestr(nombre_archivo, r.content)
                except Exception as ex:
                    print(f"Error al descargar {nombre_archivo}: {ex}")
                    continue
    memory_file.seek(0)
    
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"documentos_candidata_{fila_index}.zip"
    )

@app.route("/gestionar_archivos", methods=["GET", "POST"])
def gestionar_archivos():
    accion = request.args.get("accion", "buscar").strip()
    mensaje = None
    resultados = []
    docs = {}
    fila = request.args.get("fila", "").strip()

    # -----------------------------------------
    # ACCIÓN: BUSCAR CANDIDATA
    # -----------------------------------------
    if accion == "buscar":
        if request.method == "POST":
            busqueda = request.form.get("busqueda", "").strip().lower()
            try:
                hoja = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Nueva hoja!A:Z"  # Ajusta el rango si lo deseas
                ).execute()
                valores = hoja.get("values", [])
                for fila_index, fila_vals in enumerate(valores[1:], start=2):
                    # Ajusta índices de columna (ej: nombre en B->fila_vals[1], cédula en O->fila_vals[14], etc.)
                    nombre = (fila_vals[1].strip().lower() if len(fila_vals) > 1 else "")
                    cedula = (fila_vals[14].strip().lower() if len(fila_vals) > 14 else "")
                    telefono = (fila_vals[3].strip().lower() if len(fila_vals) > 3 else "")
                    # Coincidencia flexible
                    if busqueda in nombre or busqueda in cedula or busqueda in telefono:
                        resultados.append({
                            "fila_index": fila_index,
                            "nombre": fila_vals[1] if len(fila_vals) > 1 else "No especificado",
                            "telefono": fila_vals[3] if len(fila_vals) > 3 else "No especificado",
                            "cedula": fila_vals[14] if len(fila_vals) > 14 else "No especificado"
                        })
            except Exception as e:
                mensaje = f"Error al buscar: {str(e)}"
        return render_template("gestionar_archivos.html", accion=accion, mensaje=mensaje, resultados=resultados)

    # -----------------------------------------
    # ACCIÓN: VER DOCUMENTOS
    # -----------------------------------------
    elif accion == "ver":
        if not fila.isdigit():
            mensaje = "Error: La fila debe ser un número válido."
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        fila_index = int(fila)
        try:
            # Leer las columnas AA->AD para imágenes y Z para la entrevista (texto).
            # Ajusta si tu entrevista está en otra columna.
            rango_imagenes = f"Nueva hoja!AA{fila_index}:AD{fila_index}"
            hoja_imagenes = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=rango_imagenes
            ).execute()
            row_vals = hoja_imagenes.get("values", [])

            depuracion_url = ""
            perfil_url = ""
            cedula1_url = ""
            cedula2_url = ""
            if row_vals and len(row_vals[0]) >= 4:
                depuracion_url, perfil_url, cedula1_url, cedula2_url = row_vals[0][:4]

            # Leer la columna Z para la entrevista (suponiendo que la entrevista se guardó en Z)
            rango_entrevista = f"Nueva hoja!Z{fila_index}"
            hoja_entrevista = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=rango_entrevista
            ).execute()
            entrevista_val = hoja_entrevista.get("values", [])

            entrevista_texto = ""
            if entrevista_val and entrevista_val[0]:
                entrevista_texto = entrevista_val[0][0]

            docs = {
                "depuracion_url": depuracion_url,
                "perfil_url": perfil_url,
                "cedula1_url": cedula1_url,
                "cedula2_url": cedula2_url,
                "entrevista": entrevista_texto
            }
        except Exception as e:
            mensaje = f"Error al leer datos: {str(e)}"
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        return render_template("gestionar_archivos.html", accion=accion, fila=fila, docs=docs, mensaje=mensaje)

    # -----------------------------------------
    # ACCIÓN: DESCARGAR (doc=todo, depuracion, perfil, cedula1, cedula2, pdf)
    # -----------------------------------------
    elif accion == "descargar":
        doc = request.args.get("doc", "").strip()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        fila_index = int(fila)

        if doc == "todo":
            # Descargar todos en ZIP
            return descargar_todo_en_zip(fila_index)

        elif doc in ["depuracion", "perfil", "cedula1", "cedula2"]:
            # Descargar un solo archivo
            return descargar_uno(fila_index, doc)

        elif doc == "pdf":
            # Generar PDF de la entrevista y descargar
            return generar_pdf_entrevista(fila_index)

        else:
            return "Documento no reconocido", 400

    # Si no coincide, redirigimos a buscar
    return redirect("/gestionar_archivos?accion=buscar")

# -------------------------------------------------------
# FUNCIONES AUXILIARES PARA /gestionar_archivos
# -------------------------------------------------------

def descargar_todo_en_zip(fila_index):
    """ Crea un ZIP con depuracion, perfil, cedula1, cedula2 si existen. """
    try:
        rango_imagenes = f"Nueva hoja!AA{fila_index}:AD{fila_index}"
        hoja_imagenes = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_imagenes
        ).execute()
        row_vals = hoja_imagenes.get("values", [])
        depuracion_url, perfil_url, cedula1_url, cedula2_url = [""] * 4
        if row_vals and len(row_vals[0]) >= 4:
            depuracion_url, perfil_url, cedula1_url, cedula2_url = row_vals[0][:4]
    except Exception as e:
        return f"Error al leer imágenes: {str(e)}", 500

    archivos = {
        "depuracion.png": depuracion_url,
        "perfil.png": perfil_url,
        "cedula1.png": cedula1_url,
        "cedula2.png": cedula2_url
    }

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w") as zf:
        for nombre_archivo, url in archivos.items():
            if url:
                try:
                    r = requests.get(url)
                    r.raise_for_status()
                    zf.writestr(nombre_archivo, r.content)
                except Exception as ex:
                    print(f"Error al descargar {nombre_archivo}: {ex}")
                    continue
    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"documentos_candidata_{fila_index}.zip"
    )

def descargar_uno(fila_index, doc):
    """ Descarga un archivo individual (depuracion, perfil, cedula1, cedula2). """
    col_map = {
        "depuracion": 0,
        "perfil": 1,
        "cedula1": 2,
        "cedula2": 3
    }
    try:
        rango_imagenes = f"Nueva hoja!AA{fila_index}:AD{fila_index}"
        hoja_imagenes = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_imagenes
        ).execute()
        row_vals = hoja_imagenes.get("values", [])
        if not row_vals or len(row_vals[0]) < 4:
            return "No se encontraron datos en AA-AD", 404

        url = row_vals[0][col_map[doc]]
        if not url:
            return f"No hay URL para {doc}", 404

        r = requests.get(url)
        r.raise_for_status()
        # Retornamos el archivo con un mimetype genérico
        # Ajusta si sabes que siempre es PNG
        return send_file(
            io.BytesIO(r.content),
            mimetype="image/png",
            as_attachment=True,
            download_name=f"{doc}.png"
        )
    except Exception as e:
        return f"Error al descargar {doc}: {str(e)}", 500

from flask import send_file
from fpdf import FPDF
import io
import os

def generar_pdf_entrevista(fila_index):
    """
    Genera un PDF de la entrevista de la candidata con:
      - Encabezado SIN fondo general. Solo el logo y, debajo, el título con fondo azul.
      - Preguntas en negro, respuestas en azul con un bullet grande ("•") en la misma línea.
      - (Se eliminó el texto de pie de página)

    Requiere:
      - DejaVuSans.ttf y DejaVuSans-Bold.ttf en: app_web/static/fonts/
      - logo_nuevo.png en: app_web/static/
    """
    try:
        # 1. Leer la entrevista (columna Z) de Google Sheets
        rango_entrevista = f"Nueva hoja!Z{fila_index}"
        hoja_entrevista = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_entrevista
        ).execute()
        entrevista_val = hoja_entrevista.get("values", [])
        if not entrevista_val or not entrevista_val[0]:
            return "No hay entrevista guardada en la columna Z", 404
        texto_entrevista = entrevista_val[0][0]
    except Exception as e:
        return f"Error al leer entrevista: {str(e)}", 500

    try:
        from fpdf import FPDF
        import io
        from flask import send_file
        import os

        pdf = FPDF()
        pdf.add_page()

        # Agregar fuentes Unicode
        font_dir = os.path.join(app.root_path, "static", "fonts")
        regular_font_path = os.path.join(font_dir, "DejaVuSans.ttf")
        bold_font_path = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
        if not os.path.exists(regular_font_path) or not os.path.exists(bold_font_path):
            return "No se encontraron las fuentes en static/fonts/", 500

        pdf.add_font("DejaVuSans", "", regular_font_path, uni=True)
        pdf.add_font("DejaVuSans", "B", bold_font_path, uni=True)

        # LOGO (sin fondo)
        logo_path = os.path.join(app.root_path, "static", "logo_nuevo.png")
        if os.path.exists(logo_path):
            image_width = 70  # Ajusta el tamaño del logo
            x_pos = (pdf.w - image_width) / 2
            pdf.image(logo_path, x=x_pos, y=10, w=image_width)
        else:
            print("Logo no encontrado:", logo_path)

        # Movemos el cursor un poco más abajo del logo
        pdf.set_y(40)

        # Título con fondo azul
        pdf.set_font("DejaVuSans", "B", 18)
        pdf.set_fill_color(0, 102, 204)  # Azul
        pdf.set_text_color(255, 255, 255)  # Texto en blanco
        pdf.cell(0, 10, "Entrevista de Candidata", ln=True, align="C", fill=True)

        # Línea de separación debajo del título
        pdf.set_line_width(0.5)
        pdf.set_draw_color(0, 0, 0)
        current_y = pdf.get_y()
        pdf.line(pdf.l_margin, current_y, pdf.w - pdf.r_margin, current_y)

        pdf.ln(10)  # Espacio debajo del título

        # Procesar la entrevista
        pdf.set_font("DejaVuSans", "", 12)
        pdf.set_text_color(0, 0, 0)  # Texto negro por defecto
        lines = texto_entrevista.split("\n")

        for line in lines:
            pdf.set_x(pdf.l_margin)
            if ":" in line:
                parts = line.split(":", 1)
                pregunta = parts[0].strip() + ":"
                respuesta = parts[1].strip()

                # Pregunta en negro
                pdf.multi_cell(0, 8, pregunta)
                pdf.ln(1)

                # Bullet grande + respuesta en la misma línea
                bullet = "•"
                pdf.set_font("DejaVuSans", "", 16)  # Fuente grande para bullet
                bullet_width = pdf.get_string_width(bullet + " ")

                pdf.set_x(pdf.l_margin)
                pdf.set_text_color(0, 102, 204)  # Azul para la respuesta
                pdf.cell(bullet_width, 8, bullet + " ", ln=0)
                pdf.set_font("DejaVuSans", "", 12)
                pdf.cell(0, 8, respuesta, ln=1)
                pdf.ln(4)
                # Volvemos a negro para la siguiente pregunta
                pdf.set_text_color(0, 0, 0)
            else:
                # Si no hay ":", se imprime tal cual
                pdf.multi_cell(0, 8, line)
                pdf.ln(4)

        # Se ha removido el pie de página que mostraba el texto © 2024 Doméstica del Cibao A&D

        # Generar el PDF en memoria
        pdf_output = pdf.output(dest="S")
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode("latin1")
        else:
            pdf_bytes = pdf_output

        memory_file = io.BytesIO(pdf_bytes)
        memory_file.seek(0)

        return send_file(
            memory_file,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_candidata_{fila_index}.pdf"
        )
    except Exception as e:
        return f"Error interno generando PDF: {str(e)}", 500


from datetime import datetime
import pandas as pd
import io
from flask import render_template, request, send_file

from datetime import datetime
import pandas as pd
import io
from flask import render_template, request, send_file

@app.route('/reporte_inscripciones', methods=['GET'])
def reporte_inscripciones():
    try:
        mes = int(request.args.get('mes', datetime.today().month))
        anio = int(request.args.get('anio', datetime.today().year))
        descargar = request.args.get('descargar', '0')  # "1" para descargar, "0" para visualizar
    except Exception as e:
        return f"Parámetros inválidos: {str(e)}", 400

    try:
        # Leer la hoja completa desde A hasta T (20 columnas)
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:T"
        ).execute()
        datos = hoja.get("values", [])
        if not datos or len(datos) < 2:
            return "No hay inscripciones registradas.", 404

        # Definir nombres de columnas para el rango A:T (20 columnas)
        # Solo usaremos algunas, pero es necesario asignar nombres para todas.
        columnas = [
            "Col_A",      # Columna A (no utilizada)
            "Nombre",     # Columna B
            "Col_C",      # Columna C
            "Teléfono",   # Columna D
            "Dirección",  # Columna E (la usaremos como Ciudad)
            "Col_F",      # Columna F
            "Col_G",      # Columna G
            "Col_H",      # Columna H
            "Col_I",      # Columna I
            "Col_J",      # Columna J
            "Col_K",      # Columna K
            "Col_L",      # Columna L
            "Col_M",      # Columna M
            "Col_N",      # Columna N
            "Cédula",     # Columna O
            "Código",     # Columna P
            "Medio",      # Columna Q
            "Inscripción",# Columna R
            "Monto",      # Columna S
            "Fecha"       # Columna T
        ]
        df = pd.DataFrame(datos[1:], columns=columnas)
        
        # Convertir la columna 'Fecha' a texto y limpiarla
        df['Fecha'] = df['Fecha'].astype(str).str.strip().str.replace('"', '').str.replace("'", "")
        # Convertir la columna 'Fecha' a datetime usando el formato ISO "YYYY-MM-DD"
        df['Fecha'] = pd.to_datetime(df['Fecha'], format='%Y-%m-%d', errors='coerce')
        # Si siguen siendo todas NaT, intentar sin formato
        if df['Fecha'].isnull().all():
            df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
        if df['Fecha'].isnull().all():
            return "No se pudieron convertir las fechas. Revisa el contenido de la columna Fecha en la hoja.", 400

        # Filtrar registros por mes y año solicitados
        df_reporte = df[(df['Fecha'].dt.month == mes) & (df['Fecha'].dt.year == anio)]
        
        if df_reporte.empty:
            mensaje = f"No se encontraron inscripciones para {mes}/{anio}."
            return render_template("reporte_inscripciones.html", reporte_html="", mes=mes, anio=anio, mensaje=mensaje)
        
        # Seleccionar únicamente las columnas que se quieren mostrar
        columnas_mostrar = ["Nombre", "Dirección", "Teléfono", "Cédula", "Código", "Medio", "Inscripción", "Monto", "Fecha"]
        df_reporte = df_reporte[columnas_mostrar]
        # Renombrar "Dirección" a "Ciudad"
        df_reporte.rename(columns={"Dirección": "Ciudad"}, inplace=True)
        
        if descargar == "1":
            output = io.BytesIO()
            writer = pd.ExcelWriter(output, engine='xlsxwriter')
            df_reporte.to_excel(writer, index=False, sheet_name='Reporte')
            writer.save()
            output.seek(0)
            filename = f"Reporte_Inscripciones_{anio}_{str(mes).zfill(2)}.xlsx"
            return send_file(
                output,
                attachment_filename=filename,
                as_attachment=True,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        else:
            reporte_html = df_reporte.to_html(classes="table table-striped", index=False)
            return render_template("reporte_inscripciones.html", reporte_html=reporte_html, mes=mes, anio=anio, mensaje="")
    except Exception as e:
        return f"Error al generar reporte: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)