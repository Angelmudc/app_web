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
import io

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
    ruta_config = os.path.join(os.path.dirname(__file__), "Config", "config_entrevistas.json")
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
    Si el código ya existe, incrementa el número hasta encontrar uno disponible.
    """
    try:
        # Obtener los datos de la hoja de cálculo
        datos = obtener_datos_editar()

        # 🔹 Extraer todos los códigos existentes en la Columna P (índice 15)
        codigos_existentes = set()
        for fila in datos:
            if len(fila) > 15 and fila[15].startswith("CAN-"):
                codigos_existentes.add(fila[15])

        # 🔹 Generar el primer código disponible
        numero = 1
        while True:
            nuevo_codigo = f"CAN-{str(numero).zfill(6)}"  # CAN-000001, CAN-000002...
            if nuevo_codigo not in codigos_existentes:  # Si no está en la lista, lo usamos
                return nuevo_codigo
            numero += 1  # Si ya existe, probamos el siguiente número

    except Exception as e:
        print(f"Error al generar código único: {e}")
        return None  # Retorna None si hay un error 


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
    # Obtener el parámetro "tipo" de la URL, por ejemplo: /entrevista?tipo=domestica
    tipo_entrevista = request.args.get("tipo", "").strip().lower()

    # Validar si el tipo existe en la configuración
    if tipo_entrevista not in ENTREVISTAS_CONFIG:
        return "⚠️ Tipo de entrevista no válido.", 400

    # Extraer la configuración específica para ese tipo de entrevista
    entrevista_config = ENTREVISTAS_CONFIG[tipo_entrevista]
    titulo = entrevista_config.get("titulo", "Entrevista sin título")
    preguntas = entrevista_config.get("preguntas", [])

    # Por ahora, simplemente se renderiza una plantilla con el título y la lista de preguntas
    return render_template("entrevista_dinamica.html", titulo=titulo, preguntas=preguntas)


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    resultados = []
    candidata_detalles = None
    busqueda = request.form.get('busqueda', '').strip().lower()
    candidata_id = request.args.get('candidata', '').strip()

    try:
        # Obtener los datos de la hoja
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!B:O"  # Desde la columna B hasta la O
        ).execute()
        valores = hoja.get("values", [])

        if not valores or len(valores) < 2:
            return render_template('buscar.html', resultados=[], candidata=None, mensaje="⚠️ No hay datos disponibles.")

        # 🔹 Búsqueda flexible por nombre
        for fila_index, fila in enumerate(valores[1:], start=2):  # Empezar en la segunda fila
            nombre = fila[0].strip().lower() if len(fila) > 0 else ""  # Columna B (Nombre)

            if busqueda and busqueda in nombre:
                resultados.append({
                    'fila_index': fila_index,
                    'nombre': fila[0] if len(fila) > 0 else "No especificado",
                    'telefono': fila[2] if len(fila) > 2 else "No especificado",
                    'ciudad': fila[3] if len(fila) > 3 else "No especificado",
                    'cedula': fila[13] if len(fila) > 13 else "No especificado",
                })

        # 🔹 Cargar detalles si se seleccionó una candidata
        if candidata_id:
            fila_index = int(candidata_id)  # Convertir ID a número de fila
            fila = valores[fila_index - 1]  # Ajustar índice (Sheets empieza en 1)

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
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        return render_template('buscar.html', resultados=[], candidata=None, mensaje=mensaje)

    return render_template('buscar.html', resultados=resultados, candidata=candidata_detalles)

@app.route('/editar', methods=['GET', 'POST'])
def editar():
    resultados = []
    candidata_detalles = None
    busqueda = request.form.get('busqueda', '').strip().lower()
    candidata_id = request.form.get('candidata_seleccionada', '').strip()

    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:P"  # Incluye todas las columnas necesarias
        ).execute()

        valores = hoja.get("values", [])

        # Búsqueda de candidatas
        for fila_index, fila in enumerate(valores[1:], start=2):  # Fila 2 en adelante
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""
            cedula = fila[14].strip() if len(fila) > 14 else ""

            if busqueda and (busqueda in nombre or busqueda in cedula):
                resultados.append({
                    'fila_index': fila_index,
                    'nombre': fila[1] if len(fila) > 1 else "",
                    'telefono': fila[3] if len(fila) > 3 else "",
                    'direccion': fila[4] if len(fila) > 4 else "",
                    'cedula': fila[14] if len(fila) > 14 else "",
                })

        # Cargar detalles de la candidata si se seleccionó una
        if candidata_id:
            for fila_index, fila in enumerate(valores[1:], start=2):
                if str(fila_index) == candidata_id:
                    candidata_detalles = {
                        'fila_index': fila_index,
                        'nombre': fila[1] if len(fila) > 1 else "",
                        'edad': fila[2] if len(fila) > 2 else "",
                        'telefono': fila[3] if len(fila) > 3 else "",
                        'direccion': fila[4] if len(fila) > 4 else "",
                        'modalidad': fila[5] if len(fila) > 5 else "",
                        'anos_experiencia': fila[8] if len(fila) > 8 else "",
                        'experiencia': fila[9] if len(fila) > 9 else "",
                        'sabe_planchar': fila[10] if len(fila) > 10 else "",
                        'referencia_laboral': fila[11] if len(fila) > 11 else "",
                        'referencia_familiar': fila[12] if len(fila) > 12 else "",
                        'cedula': fila[14] if len(fila) > 14 else "",
                        'codigo': fila[15] if len(fila) > 15 else "",
                        'inscripcion': fila[17] if len(fila) > 17 else "",
                    }
                    break  # Detener búsqueda al encontrar la candidata

    except Exception as e:
        print(f"❌ Error en la búsqueda o carga de detalles: {e}")

    return render_template('editar.html', resultados=resultados, candidata=candidata_detalles)

@app.route('/guardar_edicion', methods=['POST'])
def guardar_edicion():
    try:
        fila_index = request.form.get('fila_index')
        if not fila_index or not fila_index.isdigit():
            return "Error: No se pudo determinar la fila a actualizar."

        fila_index = int(fila_index)  # Convertir a número

        nuevos_datos = {
            'nombre': request.form.get('nombre', '').strip(),
            'edad': request.form.get('edad', '').strip(),
            'telefono': request.form.get('telefono', '').strip(),
            'direccion': request.form.get('direccion', '').strip(),
            'modalidad': request.form.get('modalidad', '').strip(),
            'anos_experiencia': request.form.get('anos_experiencia', '').strip(),
            'experiencia': request.form.get('experiencia', '').strip(),
            'referencia_laboral': request.form.get('referencia_laboral', '').strip(),
            'referencia_familiar': request.form.get('referencia_familiar', '').strip(),
            'cedula': request.form.get('cedula', '').strip()
        }

        columnas = {
            'nombre': "B",
            'edad': "C",
            'telefono': "D",
            'direccion': "E",
            'modalidad': "F",
            'anos_experiencia': "I",
            'experiencia': "J",
            'referencia_laboral': "L",
            'referencia_familiar': "M",
            'cedula': "O"
        }

        for campo, valor in nuevos_datos.items():
            if valor:  # Solo actualizar si hay un valor
                rango = f'Nueva hoja!{columnas[campo]}{fila_index}'
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango,
                    valueInputOption="RAW",
                    body={"values": [[valor]]}
                ).execute()

        return "Los datos fueron actualizados correctamente."

    except Exception as e:
        return f"Error al actualizar los datos: {str(e)}"

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
    datos_candidata = {}  # Aseguramos que siempre sea un diccionario

    if request.method == "POST":
        busqueda = request.form.get("buscar", "").strip()
        
        if busqueda:
            datos = buscar_candidata(busqueda)  # Buscar en Google Sheets

            if datos and isinstance(datos, list) and len(datos) > 0:
                primera_coincidencia = datos[0]  # Tomar la primera candidata encontrada
                datos_candidata = {
                    'fila_index': primera_coincidencia.get('fila_index', ''),
                    'codigo': primera_coincidencia.get('codigo', 'Se generará automáticamente'),
                    'nombre': primera_coincidencia.get('nombre', 'No disponible'),
                    'edad': primera_coincidencia.get('edad', 'No disponible'),
                    'telefono': primera_coincidencia.get('telefono', 'No disponible'),
                    'direccion': primera_coincidencia.get('direccion', 'No disponible'),
                    'cedula': primera_coincidencia.get('cedula', 'No disponible'),
                    'estado': primera_coincidencia.get('estado', 'No disponible')
                }
            else:
                mensaje = "⚠️ No se encontró ninguna candidata con ese criterio de búsqueda."

    return render_template("inscripcion.html", datos_candidata=datos_candidata, mensaje=mensaje)

@app.route("/procesar_inscripcion", methods=["POST"])
def procesar_inscripcion():
    try:
        data = request.json
        fila_index = int(data.get("fila_index", "0"))  # Asegurar que sea un número válido
        estado = data.get("estado", "").strip()
        monto = data.get("monto", "").strip()
        fecha = data.get("fecha", "").strip()

        # Verificar que el índice de fila sea válido
        if fila_index < 1:
            return jsonify({"success": False, "error": "Índice de fila no válido"})

        # ✅ Conectar a la hoja de Google Sheets
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Nueva hoja")  # Asegurar conexión
        datos_hoja = sheet.get_all_values()  # Obtener todas las filas
        fila = datos_hoja[fila_index - 1]  # Obtener valores actuales

        # ✅ Verificar si la candidata ya tiene un código en la columna P (índice 15)
        codigo_actual = fila[15] if len(fila) > 15 else ""

        if not codigo_actual or codigo_actual.strip() == "":
            nuevo_codigo = generar_codigo_unico()  # Generar solo si no existe
            sheet.update(f"P{fila_index}", [[nuevo_codigo]])  # Guardar en la columna P
        else:
            nuevo_codigo = codigo_actual  # Mantener código existente

        # ✅ Guardar los datos en la hoja de cálculo en las columnas correctas
        sheet.update(f"R{fila_index}:T{fila_index}", [[estado, monto, fecha]])  # Solo estado, monto y fecha

        return jsonify({"success": True, "codigo": nuevo_codigo})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/guardar_inscripcion', methods=['POST'])
def guardar_inscripcion():
    try:
        datos = request.json
        fila_index = datos.get('fila_index')
        estado = datos.get('estado')
        monto = datos.get('monto')
        fecha = datos.get('fecha')

        if not fila_index:
            return jsonify({'error': 'Error: No se encontró el índice de la fila.'}), 400

        # Actualizar los datos en Google Sheets
        rango = f'Nueva hoja!Q{fila_index}:T{fila_index}'  # Actualiza estado, monto y fecha
        valores = [[estado, monto, fecha]]

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()

        return jsonify({'success': 'Inscripción guardada correctamente.'})
    except Exception as e:
        return jsonify({'error': f'Error al guardar la inscripción: {str(e)}'}), 500

@app.route('/buscar_inscripcion', methods=['GET'])
def buscar_inscripcion():
    busqueda = request.args.get("query", "").strip()
    datos = obtener_datos_editar()  # Leer la hoja completa

    for fila_index, fila in enumerate(datos, start=1):  # Empezar desde la fila 1
        if len(fila) > 14 and (busqueda.lower() in fila[1].lower() or busqueda == fila[14]):
            return jsonify({
                "fila_index": fila_index,  # 🔹 Asegurar que devuelve el índice correcto
                "nombre": fila[1],
                "telefono": fila[3],
                "cedula": fila[14]
            })

    return jsonify({"error": "No se encontró la candidata"}), 404

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


@app.route('/entrevista', methods=['GET', 'POST'])
def entrevista():
    mensaje = None
    resultados = []
    candidata_detalles = None

    # 1. Leer los datos de la hoja (A:Z) de la pestaña "Nueva hoja"
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Z"
        ).execute()
        valores = hoja.get("values", [])
        if not valores or len(valores) < 2:
            return render_template('entrevista.html',
                                   resultados=[],
                                   candidata=None,
                                   mensaje="⚠️ No hay datos disponibles en la hoja.")
    except Exception as e:
        return render_template('entrevista.html',
                               resultados=[],
                               candidata=None,
                               mensaje=f"❌ Error al obtener los datos: {str(e)}")

    # 2. Procesamiento de peticiones POST
    if request.method == 'POST':
        # A) Guardar la entrevista en la columna Z
        if 'guardar' in request.form:
            fila_index = request.form.get('fila_index', '').strip()
            if not fila_index:
                mensaje = "⚠️ Error. No se recibió la fila de la candidata."
            else:
                try:
                    fila_index = int(fila_index)
                    # Extraer todos los campos del formulario
                    nombre = request.form.get('nombre', '').strip()
                    nacionalidad = request.form.get('nacionalidad', '').strip()
                    edad = request.form.get('edad', '').strip()
                    direccion = request.form.get('direccion', '').strip()
                    estado_civil = request.form.get('estado_civil', '').strip()
                    tienes_hijos = request.form.get('tienes_hijos', '').strip()
                    numero_hijos = request.form.get('numero_hijos', '').strip()
                    edades_hijos = request.form.get('edades_hijos', '').strip()
                    quien_cuida = request.form.get('quien_cuida', '').strip()
                    descripcion_personal = request.form.get('descripcion_personal', '').strip()
                    fuerte = request.form.get('fuerte', '').strip()
                    modalidad = request.form.get('modalidad', '').strip()
                    razon_trabajo = request.form.get('razon_trabajo', '').strip()
                    labores_anteriores = request.form.get('labores_anteriores', '').strip()
                    tiempo_ultimo_trabajo = request.form.get('tiempo_ultimo_trabajo', '').strip()
                    razon_salida = request.form.get('razon_salida', '').strip()
                    situacion_dificil = request.form.get('situacion_dificil', '').strip()
                    manejo_situacion = request.form.get('manejo_situacion', '').strip()
                    manejo_reclamo = request.form.get('manejo_reclamo', '').strip()
                    uniforme = request.form.get('uniforme', '').strip()
                    dias_feriados = request.form.get('dias_feriados', '').strip()
                    revision_salida = request.form.get('revision_salida', '').strip()
                    colaboracion = request.form.get('colaboracion', '').strip()
                    tipo_familia = request.form.get('tipo_familia', '').strip()
                    cuidado_ninos = request.form.get('cuidado_ninos', '').strip()
                    sabes_cocinar = request.form.get('sabes_cocinar', '').strip()
                    gusta_cocinar = request.form.get('gusta_cocinar', '').strip()
                    que_cocinas = request.form.get('que_cocinas', '').strip()
                    postres = request.form.get('postres', '').strip()
                    tareas_casa = request.form.get('tareas_casa', '').strip()
                    electrodomesticos = request.form.get('electrodomesticos', '').strip()
                    planchar = request.form.get('planchar', '').strip()
                    actividad_principal = request.form.get('actividad_principal', '').strip()
                    afiliacion_religiosa = request.form.get('afiliacion_religiosa', '').strip()
                    cursos_domesticos = request.form.get('cursos_domesticos', '').strip()
                    nivel_academico = request.form.get('nivel_academico', '').strip()
                    condiciones_salud = request.form.get('condiciones_salud', '').strip()
                    alergico = request.form.get('alergico', '').strip()
                    medicamentos = request.form.get('medicamentos', '').strip()
                    seguro_medico = request.form.get('seguro_medico', '').strip()
                    pruebas_medicas = request.form.get('pruebas_medicas', '').strip()
                    vacunas_covid = request.form.get('vacunas_covid', '').strip()
                    tomas_alcohol = request.form.get('tomas_alcohol', '').strip()
                    fumas = request.form.get('fumas', '').strip()
                    tatuajes_piercings = request.form.get('tatuajes_piercings', '').strip()
                    referencias_laborales = request.form.get('referencias_laborales', '').strip()
                    referencias_familiares = request.form.get('referencias_familiares', '').strip()

                    # Construir el contenido completo de la entrevista
                    entrevista_completa = f"""
Nombre: {nombre}
Nacionalidad: {nacionalidad}
Edad: {edad}
Dirección: {direccion}
Estado Civil: {estado_civil}
¿Tienes hijos?: {tienes_hijos}
Número de hijos: {numero_hijos}
Edades de los hijos: {edades_hijos}
¿Quién cuida a sus hijos?: {quien_cuida}
¿Cómo te describes como persona?: {descripcion_personal}
¿Cuál es tu fuerte?: {fuerte}
Modalidad de trabajo: {modalidad}
¿Por qué eliges trabajar en una casa de familia?: {razon_trabajo}
Labores desempeñadas en trabajos anteriores: {labores_anteriores}
Tiempo desde el último trabajo: {tiempo_ultimo_trabajo}
¿Por qué saliste de tu último trabajo?: {razon_salida}
¿Has enfrentado situaciones difíciles en el trabajo?: {situacion_dificil}
¿Cómo manejaste esa situación?: {manejo_situacion}
¿Cómo manejarías reclamos o malos tratos del jefe?: {manejo_reclamo}
¿Trabajas con uniforme?: {uniforme}
¿Trabajas días feriados?: {dias_feriados}
¿Puedes ser revisada a la salida?: {revision_salida}
¿Estás dispuesta a colaborar en lo que el jefe necesite?: {colaboracion}
¿Con qué tipo de familia has trabajado anteriormente?: {tipo_familia}
¿Has cuidado niños y de qué edad?: {cuidado_ninos}
¿Sabes cocinar?: {sabes_cocinar}
¿Te gusta cocinar?: {gusta_cocinar}
¿Qué sabes cocinar?: {que_cocinas}
¿Haces postres?: {postres}
¿Qué tareas de la casa te gustan y cuáles no?: {tareas_casa}
¿Sabes usar electrodomésticos modernos?: {electrodomesticos}
¿Sabes planchar?: {planchar}
¿Tienes alguna actividad principal (trabajo/estudio)?: {actividad_principal}
Afiliación religiosa: {afiliacion_religiosa}
¿Tienes cursos en el área doméstica?: {cursos_domesticos}
Nivel académico: {nivel_academico}
¿Tienes condiciones de salud?: {condiciones_salud}
¿Eres alérgica a algo?: {alergico}
¿Tomas medicamentos?: {medicamentos}
¿Tienes seguro médico?: {seguro_medico}
¿Aceptas hacer pruebas médicas si se solicita?: {pruebas_medicas}
¿Cuántas vacunas del COVID tienes?: {vacunas_covid}
¿Tomas alcohol?: {tomas_alcohol}
¿Fumas?: {fumas}
¿Tienes tatuajes visibles o piercings?: {tatuajes_piercings}
Referencias laborales: {referencias_laborales}
Referencias familiares: {referencias_familiares}
                    """.strip()

                    # Actualizar la columna Z de la fila indicada
                    service.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=f"Nueva hoja!Z{fila_index}",
                        valueInputOption="RAW",
                        body={"values": [[entrevista_completa]]}
                    ).execute()

                    mensaje = "✅ Entrevista guardada correctamente."
                except Exception as e:
                    mensaje = f"❌ Error al guardar la entrevista: {str(e)}"

        # B) Buscar candidatas por nombre
        elif 'busqueda' in request.form:
            busqueda = request.form.get('busqueda', '').strip().lower()
            for fila_index, fila in enumerate(valores[1:], start=2):
                nombre = (fila[1].strip().lower() if len(fila) > 1 else "")
                if busqueda in nombre:
                    resultados.append({
                        'fila_index': fila_index,
                        'nombre': fila[1] if len(fila) > 1 else "No especificado",
                        'telefono': fila[3] if len(fila) > 3 else "No especificado",
                        'cedula': fila[14] if len(fila) > 14 else "No especificado",
                        'entrevista': fila[25] if len(fila) > 25 else ""
                    })
            mensaje = "✅ Se procesó la búsqueda."

    # 3. Procesamiento de peticiones GET para seleccionar una candidata (por ejemplo, ?candidata=3)
    else:
        candidata_id = request.args.get('candidata', '').strip()
        if candidata_id:
            try:
                fila_index = int(candidata_id)
                if fila_index < 1 or fila_index > len(valores):
                    mensaje = "⚠️ Candidata no encontrada."
                else:
                    fila = valores[fila_index - 1]
                    candidata_detalles = {
                        'fila_index': fila_index,
                        'nombre': fila[1] if len(fila) > 1 else "No especificado",
                        'telefono': fila[3] if len(fila) > 3 else "No especificado",
                        'cedula': fila[14] if len(fila) > 14 else "No especificado",
                        'entrevista': fila[25] if len(fila) > 25 else ""
                    }
                mensaje = "✅ Candidata cargada."
            except Exception as e:
                mensaje = f"❌ Error al procesar la candidata: {str(e)}"

    return render_template('entrevista.html',
                           resultados=resultados,
                           candidata=candidata_detalles,
                           mensaje=mensaje)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)