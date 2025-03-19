import logging
import os
import json
import io
import zipfile
import requests
import traceback
from datetime import datetime, timedelta
import pandas as pd
import unicodedata

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    send_from_directory, jsonify, send_file, current_app
)
from flask_caching import Cache

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import gspread

from rapidfuzz import process

import cloudinary
import cloudinary.uploader
import cloudinary.api

from fpdf import FPDF

import unicodedata
import logging
from flask import render_template

from werkzeug.security import generate_password_hash
from flask import Flask, render_template, request, redirect, url_for, session

from flask import Flask, render_template, request, redirect, url_for, session, send_file
from fpdf import FPDF
import io
import os

from flask import send_file




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
app.secret_key = "clave_secreta_segura"  # Asegúrate de que esta clave sea robusta y esté en una variable de entorno

# Base de datos de usuarios (puedes usar una real)
usuarios = {
    "angel": generate_password_hash("0000"),
    "cris": generate_password_hash("0722"),
    "Athy": generate_password_hash("2004"),
    "divina": generate_password_hash("0607")
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
    """
    Función mejorada para buscar candidatas:
    - Si 'busqueda' empieza con "can-", se interpreta como código y se realiza una coincidencia exacta en la columna de código.
    - De lo contrario, se busca de forma flexible por nombre (coincidencia parcial).
    
    Retorna una lista de diccionarios con los campos: fila_index, codigo, nombre, cedula y telefono.
    """
    try:
        # Obtiene todos los datos de la hoja
        datos = sheet.get_all_values()
        if not datos or len(datos) < 2:
            return []

        resultados = []
        termino = busqueda.strip().lower()

        # Si se busca por código (ej.: "CAN-000123"), la búsqueda es exacta en la columna P (índice 15)
        if termino.startswith("can-"):
            for index, fila in enumerate(datos[1:], start=2):
                if len(fila) > 15 and fila[15].strip().lower() == termino:
                    resultados.append({
                        "fila_index": index,
                        "codigo": fila[15],
                        "nombre": fila[1] if len(fila) > 1 else "",
                        "cedula": fila[14] if len(fila) > 14 else "",
                        "telefono": fila[3] if len(fila) > 3 else ""
                    })
        else:
            # Búsqueda flexible por nombre (columna B, índice 1)
            for index, fila in enumerate(datos[1:], start=2):
                if len(fila) > 1 and termino in fila[1].strip().lower():
                    resultados.append({
                        "fila_index": index,
                        "codigo": fila[15] if len(fila) > 15 else "",
                        "nombre": fila[1],
                        "cedula": fila[14] if len(fila) > 14 else "",
                        "telefono": fila[3] if len(fila) > 3 else ""
                    })
        return resultados

    except Exception as e:
        logging.error(f"Error en buscar_candidata: {str(e)}", exc_info=True)
        return []


def filtrar_por_busqueda(filas, termino):
    resultados = []
    termino = termino.lower()
    for index, fila in enumerate(filas, start=2):  # Considerando que la primera fila es encabezado
        if len(fila) > 1:
            nombre = fila[1].strip().lower()  # Se asume que el nombre está en la columna B (índice 1)
            if termino in nombre:
                resultados.append({
                    'fila_index': index,
                    'nombre': fila[1],
                    'cedula': fila[14] if len(fila) > 14 else "No especificado",
                    'ciudad': fila[4] if len(fila) > 4 else "No especificado",
                    'telefono': fila[3] if len(fila) > 3 else "No especificado",
                })
    return resultados

def actualizar_registro(fila_index, usuario_actual):
    try:
        # Definir la celda de la columna EA (donde se registra la edición)
        celda_registro = f"Nueva hoja!EA{fila_index}"
        
        # Obtener el valor actual en la celda EA
        respuesta = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=celda_registro
        ).execute()
        valores = respuesta.get("values", [])
        registro_actual = valores[0][0] if valores and valores[0] else ""
        
        # Evitar duplicados: si el usuario ya se encuentra, no se agrega de nuevo
        if usuario_actual in registro_actual.split(", "):
            nuevo_registro = registro_actual
        else:
            # Si ya existe un registro, concatenamos el nuevo usuario
            nuevo_registro = f"{registro_actual}, {usuario_actual}" if registro_actual else usuario_actual
        
        # Actualizar la celda EA con el nuevo registro
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=celda_registro,
            valueInputOption="RAW",
            body={"values": [[nuevo_registro]]}
        ).execute()
    except Exception as e:
        logging.error(f"Error actualizando el registro en la fila {fila_index}: {str(e)}", exc_info=True)


def normalizar_texto(texto):
    """
    Convierte un texto a minúsculas, elimina acentos y espacios extras.
    """
    if not texto:
        return ""
    texto = texto.strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFKD', texto) if unicodedata.category(c) != 'Mn')

def extend_row(row, min_length=24):
    """Asegura que la fila tenga al menos 'min_length' elementos."""
    if len(row) < min_length:
        row.extend([""] * (min_length - len(row)))
    return row

def extraer_candidata(fila, idx):
    """
    Extrae la información relevante de la fila y la devuelve como diccionario.
    Se asume:
      - Nombre: Columna B (índice 1)
      - Teléfono: Columna D (índice 3)
      - Dirección: Columna E (índice 4)
      - Modalidad: Columna F (índice 5)
      - Años de experiencia: Columna I (índice 8)
      - Áreas de experiencia: Columna J (índice 9)
      - Cédula: Columna O (índice 14)
      - Código: Columna P (índice 15)
      - Porciento: Columna X (índice 23)
    """
    fila = extend_row(fila, 24)
    return {
        'fila_index': idx,
        'codigo': fila[15].strip() if len(fila) > 15 else "",
        'nombre': fila[1].strip() if len(fila) > 1 else "",
        'telefono': fila[3].strip() if len(fila) > 3 else "",
        'direccion': fila[4].strip() if len(fila) > 4 else "",
        'modalidad': fila[5].strip() if len(fila) > 5 else "",
        'experiencia_anos': fila[8].strip() if len(fila) > 8 else "",
        'areas_experiencia': fila[9].strip() if len(fila) > 9 else "",
        'cedula': fila[14].strip() if len(fila) > 14 else "",
        'porciento': fila[23].strip() if len(fila) > 23 else ""
    }


def filtrar_candidata(candidata, filtros):
    # Retorna True si la candidata cumple con todos los filtros.
    ciudad = filtros.get("ciudad")
    modalidad = filtros.get("modalidad")
    experiencia = filtros.get("experiencia_anos")
    areas = filtros.get("areas_experiencia")
    return ((not ciudad or ciudad in candidata.get("direccion", "").lower()) and
            (not modalidad or modalidad in candidata.get("modalidad", "").lower()) and
            (not experiencia or experiencia in candidata.get("experiencia_anos", "").lower()) and
            (not areas or areas in candidata.get("areas_experiencia", "").lower()))

def cumple_filtros(candidata, filtros):
    """
    Verifica si la candidata cumple con los filtros aplicados en forma parcial.
    Se normalizan tanto el valor de la candidata como el término de filtro para que:
      - No importe mayúsculas/minúsculas.
      - Se eliminen acentos.
    Se usan las claves: 'direccion', 'modalidad', 'experiencia_anos' y 'areas_experiencia'.
    """
    for clave in ['direccion', 'modalidad', 'experiencia_anos', 'areas_experiencia']:
        termino = filtros.get(clave, "")
        if termino:
            termino_norm = normalizar_texto(termino)
            valor_norm = normalizar_texto(candidata.get(clave, ""))
            if termino_norm not in valor_norm:
                return False
    return True

def obtener_datos_filtrar():
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Z"
        ).execute()
        return result.get('values', [])
    except Exception as e:
        logging.error(f"Error al obtener datos para filtrar: {e}", exc_info=True)
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


def cargar_datos_hoja(rango="Nueva hoja!A:T"):
    """
    Carga los datos de la hoja de cálculo según el rango especificado.
    """
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango
        ).execute()
        return hoja.get("values", [])
    except Exception as e:
        logging.error(f"Error al cargar datos de la hoja: {e}", exc_info=True)
        return []


def buscar_candidatas_inscripcion(query, datos):
    """
    Busca candidatas en los datos cargados.
    Se asume:
      - Nombre en columna B (índice 1)
      - Cédula en columna O (índice 14)
      - Código en columna P (índice 15)
    Retorna una lista de diccionarios con fila_index, código, nombre y cédula.
    """
    resultados = []
    query_lower = query.strip().lower()
    for idx, fila in enumerate(datos[1:], start=2):  # se omite encabezado
        nombre = fila[1].strip().lower() if len(fila) > 1 else ""
        cedula = fila[14].strip().lower() if len(fila) > 14 else ""
        if query_lower in nombre or query_lower in cedula:
            resultados.append({
                "fila_index": idx,
                "codigo": fila[15] if len(fila) > 15 else "",
                "nombre": fila[1] if len(fila) > 1 else "",
                "cedula": fila[14] if len(fila) > 14 else ""
            })
    return resultados


def generar_codigo_unico():
    """
    Genera un código único en el formato 'CAN-XXXXXX' basado en los códigos existentes en la columna P.
    """
    try:
        datos = cargar_datos_hoja(rango="Nueva hoja!A:Z")
        codigos_existentes = set()
        for fila in datos[1:]:
            if len(fila) > 15:
                codigo = fila[15].strip()
                if codigo.startswith("CAN-"):
                    codigos_existentes.add(codigo)
        numero = 1
        while True:
            nuevo_codigo = f"CAN-{str(numero).zfill(6)}"
            if nuevo_codigo not in codigos_existentes:
                return nuevo_codigo
            numero += 1
    except Exception as e:
        logging.error(f"Error al generar código único: {e}", exc_info=True)
        return ""


def guardar_inscripcion(fila_index, medio, estado, monto, fecha):
    """
    Guarda los datos de inscripción para la candidata en la fila especificada.
    
    Se actualizan las siguientes columnas:
      - Q (índice 16): Medio de inscripción
      - R (índice 17): Inscripción (Estado)
      - S (índice 18): Monto
      - T (índice 19): Fecha
    
    Si la fila no tiene código en la columna P (índice 15), se genera un código único.
    Retorna (True, fila_actual) si tiene éxito; de lo contrario, (False, None).
    """
    try:
        # Primero, obtenemos la fila actual (rango A:T)
        rango_fila = f"Nueva hoja!A{fila_index}:T{fila_index}"
        respuesta = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_fila
        ).execute()
        datos = respuesta.get("values", [])
        if not datos:
            return False, None
        fila_actual = datos[0]

        # Verificar si existe código en la columna P (índice 15) y generarlo si es necesario
        if len(fila_actual) < 16 or not fila_actual[15].strip():
            nuevo_codigo = generar_codigo_unico()
            rango_codigo = f"Nueva hoja!P{fila_index}"
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=rango_codigo,
                valueInputOption="RAW",
                body={"values": [[nuevo_codigo]]}
            ).execute()
            # Aseguramos que la lista tenga al menos 16 elementos
            if len(fila_actual) < 16:
                fila_actual.extend([""] * (16 - len(fila_actual)))
            fila_actual[15] = nuevo_codigo

        # Actualizar las columnas Q, R, S y T en bloque
        update_range = f"Nueva hoja!Q{fila_index}:T{fila_index}"
        valores = [[medio, estado, monto, fecha]]
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=update_range,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()

        # Releer la fila actualizada para retornar datos consistentes
        respuesta_actual = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_fila
        ).execute()
        fila_actualizada = respuesta_actual.get("values", [])[0]

        return True, fila_actualizada
    except Exception as e:
        logging.error(f"Error al guardar inscripción en la fila {fila_index}: {e}", exc_info=True)
        return False, None

def filtrar_por_busqueda(filas, termino):
    """
    Filtra las candidatas según el término ingresado, buscando en el nombre (columna B) y cédula (columna O).
    """
    resultados = []
    termino = termino.lower()
    for index, fila in enumerate(filas, start=2):  # Se omite la primera fila (encabezado)
        if len(fila) > 1:
            nombre = fila[1].strip().lower()  # Columna B
            cedula = fila[14].strip().lower() if len(fila) > 14 else ""
            if termino in nombre or termino in cedula:
                resultados.append({
                    'fila_index': index,
                    'nombre': fila[1],
                    'cedula': fila[14] if len(fila) > 14 else "No especificado",
                    'telefono': fila[3] if len(fila) > 3 else "No especificado",
                })
    return resultados

def cargar_detalles_candidata(valores, candidata_param):
    """
    Carga los detalles básicos (nombre, teléfono y cédula) de la candidata.
    Se asume:
      - Nombre en la columna B (índice 1)
      - Teléfono en la columna D (índice 3)
      - Cédula en la columna O (índice 14)
    """
    try:
        fila_index = int(candidata_param)
        fila = valores[fila_index - 1]  # Google Sheets es 1-indexado
    except (ValueError, IndexError):
        return None
    return {
        'fila_index': fila_index,
        'nombre': fila[1] if len(fila) > 1 else "No especificado",
        'telefono': fila[3] if len(fila) > 3 else "No especificado",
        'cedula': fila[14] if len(fila) > 14 else "No especificado",
    }



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

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""
    if request.method == 'POST':
        usuario = request.form['usuario']
        clave = request.form['clave']
        if usuario in usuarios and check_password_hash(usuarios[usuario], clave):
            session['usuario'] = usuario
            return redirect(url_for('home'))
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
        # Se asume que obtener_datos_filtrar() está definida y retorna datos del rango "Nueva hoja!A:Z"
        datos = obtener_datos_filtrar()  # Ejemplo de implementación:
        # def obtener_datos_filtrar():
        #     result = service.spreadsheets().values().get(
        #         spreadsheetId=SPREADSHEET_ID,
        #         range="Nueva hoja!A:Z"
        #     ).execute()
        #     return result.get('values', [])
        logging.info(f"🔍 Datos obtenidos ({len(datos)} filas)")
        if not datos or len(datos) < 2:
            mensaje = "⚠️ No se encontraron datos en la hoja de cálculo."
            return render_template('filtrar.html', resultados=[], mensaje=mensaje)

        # Construir la lista de candidatas sin filtrar por inscripción
        for idx, fila in enumerate(datos[1:], start=2):
            fila = extend_row(fila, 24)
            candidata = extraer_candidata(fila, idx)
            resultados.append(candidata)

        # Filtrar para incluir solo candidatas que tengan código y que en "porciento" estén vacías o sean 0
        resultados = [
            c for c in resultados
            if c['codigo'] and (
                not c['porciento'] or (c['porciento'].replace('.', '', 1).isdigit() and float(c['porciento']) == 0)
            )
        ]

        # Recoger los filtros adicionales (para búsqueda en dirección, modalidad, experiencia y áreas)
        filtro_direccion = normalizar_texto(request.values.get('ciudad', ''))
        filtro_modalidad = normalizar_texto(request.values.get('modalidad', ''))
        filtro_experiencia = normalizar_texto(request.values.get('experiencia_anos', ''))
        filtro_areas = normalizar_texto(request.values.get('areas_experiencia', ''))

        # Aplicar filtros si al menos uno tiene valor
        if filtro_direccion or filtro_modalidad or filtro_experiencia or filtro_areas:
            resultados_filtrados = []
            for candidata in resultados:
                direccion_norm = normalizar_texto(candidata.get('direccion', ''))
                modalidad_norm = normalizar_texto(candidata.get('modalidad', ''))
                experiencia_norm = normalizar_texto(candidata.get('experiencia_anos', ''))
                areas_norm = normalizar_texto(candidata.get('areas_experiencia', ''))
                if (filtro_direccion in direccion_norm and
                    filtro_modalidad in modalidad_norm and
                    filtro_experiencia in experiencia_norm and
                    filtro_areas in areas_norm):
                    resultados_filtrados.append(candidata)
            if resultados_filtrados:
                resultados = resultados_filtrados
            else:
                mensaje = ("⚠️ No se encontraron resultados para los filtros aplicados.")

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        logging.error(mensaje, exc_info=True)

    return render_template('filtrar.html', resultados=resultados, mensaje=mensaje)

import traceback  # Importa para depuración

@app.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion():
    mensaje = ""
    datos_candidata = {}
    resultados = []

    # Cargar los datos de la hoja en el rango A:T
    datos = cargar_datos_hoja(rango="Nueva hoja!A:T")
    if not datos or len(datos) < 2:
        mensaje = "⚠️ No hay datos disponibles en la hoja."
        return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)

    if request.method == "POST":
        # Caso A: Guardar inscripción
        if request.form.get("guardar_inscripcion"):
            fila_index_str = request.form.get("fila_index", "").strip()
            if not fila_index_str or not fila_index_str.isdigit():
                mensaje = "Error: Índice de fila inválido."
                return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
            fila_index = int(fila_index_str)
            if fila_index < 2 or fila_index > len(datos):
                mensaje = "Índice de fila fuera de rango."
                return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
            # Recoger los datos ingresados
            medio = request.form.get("medio", "").strip()
            estado = request.form.get("estado", "").strip()
            monto = request.form.get("monto", "").strip()
            fecha = request.form.get("fecha", "").strip()
            exito, fila_actual = guardar_inscripcion(fila_index, medio, estado, monto, fecha)
            if exito:
                mensaje = "Inscripción guardada correctamente."
                datos_candidata = {
                    "fila_index": fila_index,
                    "codigo": fila_actual[15] if len(fila_actual) > 15 else "",
                    "nombre": fila_actual[1] if len(fila_actual) > 1 else "No disponible",
                    "cedula": fila_actual[14] if len(fila_actual) > 14 else "No disponible",
                    "telefono": fila_actual[3] if len(fila_actual) > 3 else "No disponible",
                    "direccion": fila_actual[4] if len(fila_actual) > 4 else "No disponible",
                    "medio": fila_actual[16] if len(fila_actual) > 16 else "No disponible",  # Columna Q
                    "inscripcion": fila_actual[17] if len(fila_actual) > 17 else "No disponible",  # Columna R
                    "monto": fila_actual[18] if len(fila_actual) > 18 else "No disponible",        # Columna S
                    "fecha": fila_actual[19] if len(fila_actual) > 19 else "No disponible"           # Columna T
                }
            else:
                mensaje = "Error al guardar la inscripción."
            return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
        else:
            # Caso B: Búsqueda (POST)
            query = request.form.get("buscar", "").strip()
            if query:
                resultados = buscar_candidatas_inscripcion(query, datos)
                if not resultados:
                    mensaje = "⚠️ No se encontraron coincidencias."
            return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)
    else:
        # Método GET: Mostrar detalles o realizar búsqueda
        candidata_param = request.args.get("candidata_seleccionada", "").strip()
        if candidata_param:
            try:
                fila_index = int(candidata_param)
                if fila_index < 2 or fila_index > len(datos):
                    mensaje = "Índice de fila fuera de rango."
                else:
                    fila = datos[fila_index - 1]
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
                resultados = buscar_candidatas_inscripcion(query, datos)
                if not resultados:
                    mensaje = "⚠️ No se encontraron coincidencias."
        return render_template("inscripcion.html", resultados=resultados, datos_candidata=datos_candidata, mensaje=mensaje)

@app.route('/porciento', methods=['GET', 'POST'])
def porciento():
    if request.method == "POST":
        # Actualización: Se reciben los datos y se calcula el 25%
        try:
            fila_index = request.form.get('fila_index', '').strip()
            if not fila_index or not fila_index.isdigit():
                return "Error: Fila inválida.", 400
            fila_index = int(fila_index)

            monto_total_str = request.form.get('monto_total', '').strip()
            if not monto_total_str:
                return "Error: monto_total es requerido.", 400

            try:
                monto_total = float(monto_total_str)
            except ValueError:
                return "Error: monto_total debe ser numérico.", 400

            porcentaje = round(monto_total * 0.25, 2)
            fecha_pago = request.form.get('fecha_pago', '').strip()
            fecha_inicio = request.form.get('fecha_inicio', '').strip()

            # Actualiza el rango de columnas U a X (índices 20 a 23)
            rango = f"Nueva hoja!U{fila_index}:X{fila_index}"
            valores = [[fecha_pago, fecha_inicio, monto_total_str, str(porcentaje)]]
            body = {"values": valores}

            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=rango,
                valueInputOption="RAW",
                body=body
            ).execute()

            mensaje = "✅ Datos actualizados correctamente."
        except Exception as e:
            logging.error(f"Error al actualizar porcentaje: {e}", exc_info=True)
            return f"❌ Error al actualizar: {e}", 500

        return render_template('porciento.html', mensaje=mensaje)
    else:
        resultados = []
        candidata_detalles = None
        busqueda = request.args.get('busqueda', '').strip().lower()
        candidata_id = request.args.get('candidata', '').strip()

        try:
            hoja = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Nueva hoja!A:Y"
            ).execute()
            valores = hoja.get("values", [])

            for fila_index, fila in enumerate(valores[1:], start=2):
                # Aseguramos que la fila tenga al menos 24 columnas (esto cubre hasta índice 23)
                if len(fila) < 24:
                    fila.extend([""] * (24 - len(fila)))

                # Solo procesamos filas que tengan código en la columna P (índice 15)
                codigo = fila[15].strip() if len(fila) > 15 else ""
                if not codigo:
                    continue

                nombre = fila[1].strip().lower() if len(fila) > 1 else ""
                cedula = fila[14].strip() if len(fila) > 14 else ""
                telefono = fila[3].strip() if len(fila) > 3 else ""

                if busqueda and (busqueda in nombre or busqueda in cedula):
                    resultados.append({
                        'fila_index': fila_index,
                        'codigo': fila[15],
                        'nombre': fila[1],
                        'telefono': telefono,
                        'cedula': cedula
                    })

                if candidata_id and str(fila_index) == candidata_id:
                    candidata_detalles = {
                        'fila_index': fila_index,
                        'codigo': fila[15],
                        'nombre': fila[1],
                        'fecha_pago': fila[20],
                        'fecha_inicio': fila[21],
                        'monto_total': fila[22],
                        'porcentaje': fila[23],
                        'telefono': telefono,
                        'cedula': cedula
                    }
        except Exception as e:
            logging.error(f"Error en búsqueda de candidatas: {e}", exc_info=True)

        return render_template('porciento.html', resultados=resultados, candidata=candidata_detalles)

@app.route('/pagos', methods=['GET', 'POST'])
def pagos():
    mensaje = ""
    resultados = []
    candidata_detalles = None

    # Cargar la hoja completa (rango A:Y)
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"
        ).execute()
        datos = hoja.get("values", [])
        # Aseguramos que cada fila tenga al menos 25 columnas (índice 0 a 24)
        for fila in datos:
            if len(fila) < 25:
                fila.extend([""] * (25 - len(fila)))
    except Exception as e:
        logging.error(f"Error al cargar datos de la hoja: {e}", exc_info=True)
        return render_template('pagos.html', resultados=[], candidata=None, mensaje="Error al cargar datos.")

    # Si se envía un POST: procesamos la actualización del pago
    if request.method == "POST":
        try:
            fila_index_str = request.form.get('fila_index', '').strip()
            if not fila_index_str or not fila_index_str.isdigit():
                mensaje = "Error: Índice de fila inválido."
                return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)
            fila_index = int(fila_index_str)

            monto_pagado_str = request.form.get('monto_pagado', '').strip()
            if not monto_pagado_str:
                mensaje = "Error: Ingrese un monto válido."
                return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)
            monto_pagado = float(monto_pagado_str)

            # Obtener la calificación desde el select
            calificacion = request.form.get('calificacion', '').strip()
            if not calificacion:
                mensaje = "Error: Seleccione una calificación."
                return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)

            # Obtener el saldo actual desde la columna X (índice 23)
            hoja_x = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"Nueva hoja!X{fila_index}"
            ).execute()
            valores_x = hoja_x.get("values", [])
            saldo_actual = float(valores_x[0][0]) if valores_x and valores_x[0] and valores_x[0][0] else 0.0

            nuevo_saldo = max(saldo_actual - monto_pagado, 0)
            # Actualizar en bloque las columnas X y Y: 
            # Columna X: nuevo saldo, Columna Y: calificación
            update_range = f"Nueva hoja!X{fila_index}:Y{fila_index}"
            body = {"values": [[nuevo_saldo, calificacion]]}
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=update_range,
                valueInputOption="RAW",
                body=body
            ).execute()
            mensaje = "✅ Pago guardado con éxito."
        except Exception as e:
            logging.error(f"Error al guardar pago: {e}", exc_info=True)
            mensaje = f"❌ Error al guardar el pago: {e}"
        return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)

    # Procesamiento para el método GET: búsqueda y visualización de detalles
    busqueda = request.args.get('busqueda', '').strip().lower() or request.form.get('busqueda', '').strip().lower()
    candidata_id = request.args.get('candidata', '').strip()

    if busqueda:
        for idx, fila in enumerate(datos[1:], start=2):
            nombre = fila[1].strip().lower() if len(fila) > 1 else ""
            cedula = fila[14].strip() if len(fila) > 14 else ""
            codigo = fila[15].strip() if len(fila) > 15 else ""
            telefono = fila[3].strip() if len(fila) > 3 else ""
            if busqueda in nombre or busqueda in cedula or busqueda == codigo:
                resultados.append({
                    'fila_index': idx,
                    'nombre': fila[1] if len(fila) > 1 else "No especificado",
                    'telefono': telefono if telefono else "No especificado",
                    'cedula': cedula if cedula else "No especificado",
                    'codigo': codigo if codigo else "No especificado"
                })
    if candidata_id:
        try:
            fila_index = int(candidata_id)
            fila = datos[fila_index - 1]  # Ajuste: Sheets es 1-indexado
            candidata_detalles = {
                'fila_index': fila_index,
                'nombre': fila[1] if len(fila) > 1 else "No especificado",
                'telefono': fila[3] if len(fila) > 3 else "No especificado",
                'cedula': fila[14] if len(fila) > 14 else "No especificado",
                'monto_total': fila[22] if len(fila) > 22 else "0",    # Columna W
                'porcentaje': fila[23] if len(fila) > 23 else "0",      # Columna X
                'fecha_pago': fila[20] if len(fila) > 20 else "No registrada",  # Columna U
                'calificacion': fila[24] if len(fila) > 24 else ""      # Columna Y
            }
        except Exception as e:
            logging.error(f"Error al cargar detalles de candidata: {e}", exc_info=True)
            mensaje = f"Error al cargar detalles: {e}"

    return render_template('pagos.html', resultados=resultados, candidata=candidata_detalles, mensaje=mensaje)

import io
import logging
from fpdf import FPDF
from flask import render_template, send_file

# Función auxiliar para extender una fila a una longitud mínima
def extend_row(row, min_length=25):
    if len(row) < min_length:
        row.extend([""] * (min_length - len(row)))
    return row

@app.route('/reporte_pagos', methods=['GET'])
def reporte_pagos():
    pagos_pendientes = []
    try:
        # Obtener todos los datos (A:Y) de la hoja
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:Y"
        ).execute()
        valores = hoja.get("values", [])
        
        if not valores or len(valores) < 2:
            return render_template('reporte_pagos.html', pagos_pendientes=[], mensaje="⚠️ No hay datos disponibles.")
        
        # Procesar cada fila (excluyendo encabezado)
        for fila in valores[1:]:
            fila = extend_row(fila, 25)
            try:
                # Intentar convertir a float; si falla, se asume que es una fila de encabezado u otro tipo y se salta
                monto_total = float(fila[22].strip()) if fila[22].strip().replace('.', '', 1).isdigit() else None
                porcentaje_pendiente = float(fila[23].strip()) if fila[23].strip().replace('.', '', 1).isdigit() else None
                if monto_total is None or porcentaje_pendiente is None:
                    # Salta filas que no tienen datos numéricos en las columnas correspondientes
                    continue
                
                if porcentaje_pendiente > 0:
                    pagos_pendientes.append({
                        'nombre': fila[1] if fila[1] else "No especificado",
                        'cedula': fila[14] if fila[14] else "No especificado",
                        'codigo': fila[15] if fila[15] else "No especificado",
                        'ciudad': fila[4] if fila[4] else "No especificado",
                        'monto_total': monto_total,
                        'porcentaje_pendiente': porcentaje_pendiente,
                        'fecha_inicio': fila[20] if fila[20] else "No registrada",
                        'fecha_pago': fila[21] if fila[21] else "No registrada"
                    })
            except Exception as e:
                logging.error(f"Error procesando fila: {fila} - {e}", exc_info=True)
                continue

    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        logging.error(mensaje, exc_info=True)
        return render_template('reporte_pagos.html', pagos_pendientes=[], mensaje=mensaje)

    return render_template('reporte_pagos.html', pagos_pendientes=pagos_pendientes)


@app.route('/generar_pdf')
def generar_pdf():
    try:
        # Obtener datos de la hoja
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

        # Procesar filas (saltamos el encabezado)
        for fila in valores[1:]:
            fila = extend_row(fila, 25)
            try:
                # Validamos que los datos sean numéricos antes de la conversión
                if not fila[23].strip().replace('.', '', 1).isdigit():
                    continue
                porcentaje_pendiente = float(fila[23].strip())
                if porcentaje_pendiente > 0:
                    nombre = fila[1] if fila[1] else "No especificado"
                    cedula = fila[14] if fila[14] else "No especificado"
                    codigo = fila[15] if fila[15] else "No especificado"
                    ciudad = fila[4] if fila[4] else "No especificado"
                    monto_total = fila[22] if fila[22] else "0"
                    fecha_inicio = fila[20] if fila[20] else "No registrada"
                    fecha_pago = fila[21] if fila[21] else "No registrada"

                    pdf.cell(0, 8, f"Nombre: {nombre} | Cédula: {cedula} | Código: {codigo}", ln=True)
                    pdf.cell(0, 8, f"Ciudad: {ciudad} | Monto Total: {monto_total} | Pendiente: {porcentaje_pendiente}", ln=True)
                    pdf.cell(0, 8, f"Inicio: {fecha_inicio} | Pago: {fecha_pago}", ln=True)
                    pdf.ln(5)
            except Exception as e:
                logging.error(f"Error procesando fila para PDF: {fila} - {e}", exc_info=True)
                continue

        # Generar PDF en memoria (BytesIO) sin intentar codificar si ya es bytearray
        pdf_output = pdf.output(dest='S')
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        else:
            pdf_bytes = pdf_output
        pdf_buffer = io.BytesIO(pdf_bytes)
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name="reporte_pagos.pdf", mimetype="application/pdf")

    except Exception as e:
        mensaje = f"❌ Error al generar PDF: {str(e)}"
        logging.error(mensaje, exc_info=True)
        return mensaje


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
    Genera un PDF de la entrevista de la candidata que imprime:
      - La entrevista (columna Z)
      - Las referencias laborales (columna AE)
      - Las referencias familiares (columna AF)
      
    Requiere:
      - DejaVuSans.ttf y DejaVuSans-Bold.ttf en: app_web/static/fonts/
      - logo_nuevo.png en: app_web/static/
    """
    try:
        # 1. Leer la entrevista (columna Z)
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
        # 2. Leer las referencias (columnas AE y AF)
        rango_referencias = f"Nueva hoja!AE{fila_index}:AF{fila_index}"
        hoja_referencias = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_referencias
        ).execute()
        ref_values = hoja_referencias.get("values", [])
        if ref_values and len(ref_values[0]) >= 2:
            ref_laborales = ref_values[0][0]
            ref_familiares = ref_values[0][1]
        else:
            ref_laborales = ""
            ref_familiares = ""
    except Exception as e:
        return f"Error al leer referencias: {str(e)}", 500

    try:
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

        # Mover el cursor un poco más abajo del logo
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
        pdf.ln(10)

        # Sección: Entrevista (Columna Z)
        pdf.set_font("DejaVuSans", "", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Entrevista:", ln=True)
        pdf.ln(2)
        lines = texto_entrevista.split("\n")

def generar_pdf_entrevista(fila_index):
    """
    Genera un PDF de la entrevista de la candidata que imprime:
      - La entrevista (columna Z) con el diseño original:
           * Preguntas en negro.
           * Respuestas en azul precedidas de un bullet grande ("•").
           * Líneas de separación y encabezado con logo.
      - Una sección "Referencias" que imprime:
           * Referencias Laborales (columna AE).
           * Referencias Familiares (columna AF).
           
    Requiere:
      - Las fuentes DejaVuSans.ttf y DejaVuSans-Bold.ttf en: app_web/static/fonts/
      - logo_nuevo.png en: app_web/static/
    """
    # 1. Leer la entrevista (columna Z)
    try:
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

    # 2. Leer las referencias (columnas AE y AF)
    try:
        rango_referencias = f"Nueva hoja!AE{fila_index}:AF{fila_index}"
        hoja_referencias = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango_referencias
        ).execute()
        ref_values = hoja_referencias.get("values", [])
        if ref_values and len(ref_values[0]) >= 2:
            ref_laborales = ref_values[0][0]
            ref_familiares = ref_values[0][1]
        else:
            ref_laborales = ""
            ref_familiares = ""
    except Exception as e:
        return f"Error al leer referencias: {str(e)}", 500

    # 3. Generar el PDF con el diseño solicitado
    try:
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

        # Línea superior debajo del logo
        pdf.set_line_width(0.5)
        pdf.set_draw_color(0, 0, 0)
        pdf.line(pdf.l_margin, 30, pdf.w - pdf.r_margin, 30)

        pdf.set_y(40)

        # Título con fondo azul
        pdf.set_font("DejaVuSans", "B", 18)
        pdf.set_fill_color(0, 102, 204)  # Azul
        pdf.set_text_color(255, 255, 255)  # Texto en blanco
        pdf.cell(0, 10, "Entrevista de Candidata", ln=True, align="C", fill=True)

        # Línea inferior debajo del título
        current_y = pdf.get_y()
        pdf.set_line_width(0.5)
        pdf.set_draw_color(0, 0, 0)
        pdf.line(pdf.l_margin, current_y, pdf.w - pdf.r_margin, current_y)
        pdf.ln(10)

        # Sección: Entrevista (Columna Z)
        pdf.set_font("DejaVuSans", "", 12)
        pdf.set_text_color(0, 0, 0)  # Preguntas en negro
        lines = texto_entrevista.split("\n")
        for line in lines:
            pdf.set_x(pdf.l_margin)
            if ":" in line:
                parts = line.split(":", 1)
                pregunta = parts[0].strip() + ":"
                respuesta = parts[1].strip()

                # Imprimir pregunta en negro
                pdf.multi_cell(0, 8, pregunta)
                pdf.ln(1)

                # Imprimir respuesta en azul con bullet
                bullet = "•"
                pdf.set_font("DejaVuSans", "", 16)  # Fuente grande para bullet
                bullet_width = pdf.get_string_width(bullet + " ")
                pdf.set_x(pdf.l_margin)
                pdf.set_text_color(0, 102, 204)  # Azul para respuesta
                pdf.cell(bullet_width, 8, bullet + " ", ln=0)
                pdf.set_font("DejaVuSans", "", 12)
                pdf.cell(0, 8, respuesta, ln=1)
                pdf.ln(4)
                pdf.set_text_color(0, 0, 0)  # Regresar a negro para la siguiente pregunta
            else:
                pdf.multi_cell(0, 8, line)
                pdf.ln(4)
        pdf.ln(5)
        
        # Sección: Referencias
        pdf.set_font("DejaVuSans", "B", 14)
        pdf.set_text_color(0, 102, 204)
        pdf.cell(0, 10, "Referencias", ln=True)
        pdf.ln(3)
        
        # Referencias Laborales (Columna AE)
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Referencias Laborales:", ln=True)
        pdf.set_font("DejaVuSans", "", 12)
        if ref_laborales:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_laborales)
        else:
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 8, "No hay referencias laborales.", ln=True)
        pdf.ln(5)
        
        # Referencias Familiares (Columna AF)
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.cell(0, 8, "Referencias Familiares:", ln=True)
        pdf.set_font("DejaVuSans", "", 12)
        if ref_familiares:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_familiares)
        else:
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 8, "No hay referencias familiares.", ln=True)
        pdf.ln(5)

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

@app.route('/referencias', methods=['GET', 'POST'])
def referencias():
    resultados = []
    candidata = None
    mensaje = None

    # Se obtiene el término de búsqueda y el parámetro 'candidata'
    busqueda_input = request.form.get('busqueda', '').strip().lower()
    # Se intenta obtener el parámetro candidato de request.args o request.form
    candidata_param = (request.args.get('candidata', '').strip() or 
                        request.form.get('candidata', '').strip())

    try:
        # Se carga el rango A:AF (para incluir columnas AE y AF)
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:AF"
        ).execute()
        valores = hoja.get("values", [])
        if not valores or len(valores) < 2:
            return render_template('referencias.html', resultados=[], candidata=None,
                                   mensaje="⚠️ No hay datos disponibles.")
        
        # Si se envía un término de búsqueda y aún no se ha seleccionado candidata, filtramos los resultados
        if busqueda_input and not candidata_param:
            resultados = filtrar_por_busqueda(valores[1:], busqueda_input)
            if not resultados:
                mensaje = "No se encontraron candidatas con ese criterio."
        
        # Si se ha seleccionado una candidata, se cargan sus detalles
        if candidata_param:
            candidata = cargar_detalles_candidata(valores, candidata_param)
            fila_idx = int(candidata_param)
            fila = valores[fila_idx - 1]
            if len(fila) < 32:
                fila.extend([""] * (32 - len(fila)))
            # Asumimos que:
            # - Referencias laborales en columna AE (índice 30)
            # - Referencias familiares en columna AF (índice 31)
            candidata['referencias_laborales'] = fila[30]
            candidata['referencias_familiares'] = fila[31]
    except Exception as e:
        mensaje = f"❌ Error al obtener los datos: {str(e)}"
        return render_template('referencias.html', resultados=[], candidata=None, mensaje=mensaje)
    
    # Bloque de actualización: se ejecuta solo si se envía el formulario y candidata_param existe
    if request.method == 'POST' and candidata_param:
        referencias_laborales = request.form.get('referencias_laborales', '').strip()
        referencias_familiares = request.form.get('referencias_familiares', '').strip()
        try:
            fila_index = int(candidata_param)
            # Se actualizan ambas columnas (AE y AF) en un solo llamado
            rango_referencias = f"Nueva hoja!AE{fila_index}:AF{fila_index}"
            body = {
                "values": [[referencias_laborales, referencias_familiares]]
            }
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=rango_referencias,
                valueInputOption="RAW",
                body=body
            ).execute()
            mensaje = "Referencias actualizadas correctamente."
            # Se relee la fila para recargar los datos
            respuesta = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Nueva hoja!A:AF"
            ).execute()
            nuevos_valores = respuesta.get("values", [])
            if len(nuevos_valores) >= fila_index:
                fila = nuevos_valores[fila_index - 1]
                if len(fila) < 32:
                    fila.extend([""] * (32 - len(fila)))
                candidata = cargar_detalles_candidata(nuevos_valores, candidata_param)
                candidata['referencias_laborales'] = fila[30]
                candidata['referencias_familiares'] = fila[31]
            else:
                mensaje += " (No se pudo recargar la información actualizada.)"
        except Exception as e:
            mensaje = f"Error al actualizar referencias: {str(e)}"
    
    return render_template('referencias.html', resultados=resultados, candidata=candidata, mensaje=mensaje)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)