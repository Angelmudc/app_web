from dotenv import load_dotenv
load_dotenv()

import os, re, unicodedata, io, json, zipfile, logging, calendar
from datetime import datetime, timedelta
from decimal import Decimal

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, send_from_directory, flash, jsonify, current_app
)
from flask_caching import Cache
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from sqlalchemy import or_, cast, String

from config_app import create_app, db, sheets, normalize_cedula, credentials


# Tu modelo
from models import Candidata

# —————— Normaliza cédula ——————
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    """
    Quita todo lo que no sea dígito y formatea como XXX-XXXXXX-X.
    Devuelve None si tras limpiar no quedan 11 dígitos.
    """
    # Eliminamos cualquier carácter no numérico
    digits = re.sub(r'\D', '', raw or '')
    # Debe tener 11 dígitos
    if not CEDULA_PATTERN.fullmatch(digits):
        return None
    # Formateamos
    return f"{digits[:3]}-{digits[3:9]}-{digits[9:]}"

# —————— Normaliza nombre ——————
def normalize_nombre(raw: str) -> str:
    """
    Elimina acentos y caracteres extraños de un nombre,
    dejando sólo letras básicas, espacios y guiones.
    """
    if not raw:
        return ''
    # Descomponer acentos
    nfkd = unicodedata.normalize('NFKD', raw)
    # Quitar marcas diacríticas
    no_accents = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    # Conservar sólo A–Z, espacios y guiones
    return re.sub(r'[^A-Za-z\s\-]', '', no_accents).strip()




app = create_app()

# ─── 2) Inicializamos Cache ───────────────────────────────────────────────────────────────
cache = Cache(app)

# ─── 3) Arrancamos Flask-Migrate ──────────────────────────────────────────────────────────
migrate = Migrate(app, db)

# ─── 4) Configuración de Cloudinary ───────────────────────────────────────────────────────
import cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)

# ─── 5) Scopes y cliente de Google Sheets ─────────────────────────────────────────────────
import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread

SERVICE_ACCOUNT_FILE = os.environ["SERVICE_ACCOUNT_FILE"]
SPREADSHEET_ID       = os.environ["SPREADSHEET_ID"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service     = build("sheets", "v4", credentials=credentials)
gspread_cli  = gspread.authorize(credentials)
sheet        = gspread_cli.open_by_key(SPREADSHEET_ID).worksheet("Nueva hoja")

# ─── 6) Usuarios de ejemplo ────────────────────────────────────────────────────────────────
from werkzeug.security import generate_password_hash

usuarios = {
    "angel":    generate_password_hash("0000"),
    "Edilenia": generate_password_hash("2003"),
    "caty":        generate_password_hash("0000"),
    "divina":   generate_password_hash("0607"),
}

# ─── 7) Carga de configuración de entrevistas ───────────────────────────────────────────────
import json
import os

try:
    cfg_path = os.path.join(app.root_path, 'config', 'config_entrevistas.json')
    with open(cfg_path, encoding='utf-8') as f:
        entrevistas_cfg = json.load(f)
    app.logger.info("✅ Configuración de entrevistas cargada con éxito.")
except Exception as e:
    app.logger.error(f"❌ Error cargando config_entrevistas.json: {e}")
    entrevistas_cfg = {}

app.config['ENTREVISTAS_CONFIG'] = entrevistas_cfg

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

def filtrar_por_busqueda(valores, termino):
    resultados = []
    termino = termino.lower()
    # Omitimos las dos primeras filas y enumeramos desde 3
    for index, fila in enumerate(valores[2:], start=3):
        if len(fila) > 1:
            nombre = fila[1].strip().lower()  # Se asume que el nombre está en la columna B
            if termino in nombre:
                resultados.append({
                    'fila_index': index,  # index = la fila real en la hoja
                    'nombre': fila[1],
                    'telefono': fila[3] if len(fila) > 3 else "No especificado",
                    'cedula': fila[14] if len(fila) > 14 else "No especificado",
                })
    return resultados



def cargar_detalles_candidata(valores, candidata_param):
    try:
        fila_index = int(candidata_param)  # Por ejemplo, si se selecciona la fila 3, fila_index = 3
        # La fila 2 es encabezado, por lo que la primera fila de datos es la 3 y se corresponde con valores[1] si la hoja empieza en la fila 2.
        # Pero en tu caso, si la hoja tiene Fila1 VACÍA, Fila2 = Encabezado y Fila3 = Datos,
        # entonces para la fila 3 (datos) se debe acceder a valores[3 - 2] = valores[1].
        fila = valores[fila_index - 2]
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

  # ─────────────────────────────────────────────────────────────
# MÓDULO: OTROS EMPLEOS
# ─────────────────────────────────────────────────────────────
# Este bloque trabaja sobre la hoja "Otros" ubicada en:
# https://docs.google.com/spreadsheets/d/14cB_82oGO5eBvt5QyrfGkSkoVrTRmfVHpI5K-Dzyilc/edit?usp=sharing
#
# La hoja tiene la siguiente estructura:
#
# A: Marca temporal  
# B: Dirección de correo electrónico  
# C: Nombre completo  
# D: ¿Qué edad tienes?  
# E: Número de teléfono  
# F: Dirección completa  
# G: Cédula  
# H: Nivel educativo alcanzado  
# I: Carrera o especialidad (si aplica)  
# J: Idioma que dominas  
# K: ¿Sabe usar computadora?  
# L: ¿Tiene licencia de conducir?  
# M: Habilidades o conocimientos especiales  
# N: ¿Tiene experiencia laboral?  
# O: Servicios que está dispuesto(a) a ofrecer  
# P: Dos contactos de referencias laborales y explicación  
# Q: Dos referencias familiares y especifique qué relación tiene  
# R: Términos y Condiciones  
# S: codigo  
# T: fecha  
# U: monto  
# V: via
# ─────────────────────────────────────────────────────────────

def get_sheet_otros():
    """
    Retorna el worksheet "Otros" de la hoja de cálculo usando la URL proporcionada.
    Se reutiliza el objeto 'client' que ya se tiene configurado en la app.
    """
    try:
        spreadsheet_otros = client.open_by_url(
            "https://docs.google.com/spreadsheets/d/14cB_82oGO5eBvt5QyrfGkSkoVrTRmfVHpI5K-Dzyilc/edit?usp=sharing"
        )
        worksheet_otros = spreadsheet_otros.worksheet("Otros")
        return worksheet_otros
    except Exception as e:
        logging.error("Error al obtener la hoja 'Otros': " + str(e), exc_info=True)
        return None

def get_headers_otros():
    """Retorna la lista de encabezados (la primera fila) de la hoja 'Otros'."""
    ws = get_sheet_otros()
    if ws:
        return ws.row_values(1)
    return []

def generate_next_code_otros():
    """
    Genera el siguiente código único escalable en el formato 'OPE-XXXXXX'
    basándose en los códigos existentes en la columna "codigo" (Columna S).
    """
    ws = get_sheet_otros()
    if not ws:
        return "OPE-000001"
    headers = get_headers_otros()
    try:
        idx = headers.index("codigo") + 1  # Las columnas se cuentan desde 1.
    except ValueError:
        idx = 1
    codes = ws.col_values(idx)[1:]  # Omitir la fila de encabezados
    max_num = 0
    for code in codes:
        if code.startswith("OPE-"):
            try:
                num = int(code.split("-")[1])
                if num > max_num:
                    max_num = num
            except:
                continue
    next_num = max_num + 1
    return f"OPE-{next_num:06d}"

from flask import render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash

from flask import jsonify

@app.route('/_test_sheets')
def _test_sheets():
    """
    Endpoint de prueba para verificar conexión a Google Sheets.
    Lee el rango A1:B5 de la hoja "Nueva hoja".
    """
    try:
        valores = sheets.get_values('Nueva hoja!A1:B5')
        return jsonify({'filas': valores}), 200
    except Exception as e:
        app.logger.exception("Error leyendo Google Sheets")
        return jsonify({'error': str(e)}), 500

@app.route('/candidatas')
def list_candidatas():
    """
    Devuelve sólo los encabezados reales (fila 2) de la hoja "Nueva hoja"
    para que veamos sus nombres exactos.
    """
    try:
        # Leer la fila 2 completa
        encabezados = sheets.get_values('Nueva hoja!A2:Z2')[0]
        return jsonify({'encabezados': encabezados}), 200
    except Exception as e:
        app.logger.exception("Error obteniendo encabezados en fila 2")
        return jsonify({'error': str(e)}), 500


@app.route('/candidatas_db')
def list_candidatas_db():
    """
    Devuelve todas las candidatas que existen en la tabla 'candidatas' de PostgreSQL.
    """
    try:
        candidatas = Candidata.query.all()
        resultado = []
        for c in candidatas:
            resultado.append({
                "fila": c.fila,
                "marca_temporal": c.marca_temporal.isoformat() if c.marca_temporal else None,
                "nombre_completo": c.nombre_completo,
                "edad": c.edad,
                "numero_telefono": c.numero_telefono,
                "direccion_completa": c.direccion_completa,
                "modalidad_trabajo_preferida": c.modalidad_trabajo_preferida,
                "cedula": c.cedula,
                "codigo": c.codigo,
                # agrega aquí más campos que quieras exponer
            })
        return jsonify({"candidatas": resultado}), 200
    except Exception as e:
        app.logger.exception("Error leyendo desde la DB")
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    return render_template(
        'home.html',
        usuario=session['usuario'],
        current_year=datetime.utcnow().year
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip()
        clave = request.form.get('clave', '').strip()
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

@app.route('/sugerir')
def sugerir():
    query = request.args.get('busqueda', '')
    if not query:
        return jsonify([])

    # Aquí deberías obtener los datos de la cache o de la base de datos
    datos_filtrados = [dato for dato in lista_candidatas if query.lower() in dato['nombre'].lower()]
    
    return jsonify(datos_filtrados)

# ─── 2) Cargar ENTREVISTAS_CONFIG desde JSON ──────────────────────

from flask import (
    render_template, request, redirect, url_for, flash, current_app
)
from models import Candidata, db


@app.route('/entrevista', methods=['GET', 'POST'])
def entrevista():
    # 1) Obtener parámetros
    tipo    = request.values.get('tipo', '').strip().lower()
    fila    = request.values.get('fila', type=int)
    config  = current_app.config['ENTREVISTAS_CONFIG']

    # 2) Si llegó POST con respuestas, guardamos
    if request.method == 'POST' and tipo and fila:
        preguntas = config[tipo]['preguntas']
        respuestas = []
        faltan = []
        for p in preguntas:
            v = request.form.get(p['id'], '').strip()
            if not v: faltan.append(p['id'])
            respuestas.append(f"{p['enunciado']}: {v}")
        if faltan:
            flash("Por favor completa todos los campos.", "warning")
        else:
            c = Candidata.query.get(fila)
            c.entrevista = "\n".join(respuestas)
            try:
                db.session.commit()
                flash("✅ Entrevista guardada.", "success")
            except:
                db.session.rollback()
                flash("❌ Error al guardar.", "danger")
        # Después de guardar, redirigimos al mismo formulario para verlo de nuevo
        return redirect(url_for('entrevista') + f"?fila={fila}&tipo={tipo}")

    # 3) Si no hay 'fila' seleccionada → mostramos buscador
    if not fila:
        resultados = []
        if request.method == 'POST':  # búsqueda por POST
            q = request.form.get('busqueda','').strip()
            if q:
                like = f"%{q}%"
                resultados = Candidata.query.filter(
                    (Candidata.nombre_completo.ilike(like)) |
                    (Candidata.cedula.ilike(like))
                ).all()
                if not resultados:
                    flash("⚠️ No se encontraron candidatas.", "info")
            else:
                flash("⚠️ Ingresa un término de búsqueda.", "warning")
        return render_template(
            'entrevista.html',
            etapa='buscar',
            resultados=resultados
        )

    # 4) Si hay fila pero no tipo → mostrar selección de tipo
    if fila and not tipo:
        candidata = Candidata.query.get(fila)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('entrevista'))
        # lista de (clave, título) para los tipos disponibles
        tipos = [(k, cfg['titulo']) for k, cfg in config.items()]
        return render_template(
            'entrevista.html',
            etapa='elegir_tipo',
            candidata=candidata,
            tipos=tipos
        )

    # 5) Si tenemos fila y tipo → mostrar formulario dinámico
    if fila and tipo:
        candidata  = Candidata.query.get(fila)
        cfg         = config.get(tipo)
        if not cfg or not candidata:
            flash("⚠️ Parámetros inválidos.", "danger")
            return redirect(url_for('entrevista'))
        return render_template(
            'entrevista.html',
            etapa='formulario',
            candidata=candidata,
            tipo=tipo,
            preguntas=cfg.get('preguntas', []),
            titulo=cfg.get('titulo'),
            datos={}, mensaje=None, focus_field=None
        )

    # fallback
    return redirect(url_for('entrevista'))



from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from sqlalchemy import or_
from models import Candidata, db
from config_app import normalize_cedula

@app.route('/buscar', methods=['GET', 'POST'])
def buscar_candidata():
    busqueda = (request.form.get('busqueda', '') if request.method == 'POST'
                else request.args.get('busqueda', '')).strip()
    resultados = []
    candidata = None
    mensaje = None

    # Edición
    if request.method == 'POST' and request.form.get('guardar_edicion'):
        cid = request.form.get('candidata_id','').strip()
        if cid.isdigit():
            obj = Candidata.query.get(int(cid))
            if obj:
                obj.nombre_completo             = request.form.get('nombre','').strip() or obj.nombre_completo
                obj.edad                        = request.form.get('edad','').strip() or obj.edad
                obj.numero_telefono             = request.form.get('telefono','').strip() or obj.numero_telefono
                obj.direccion_completa          = request.form.get('direccion','').strip() or obj.direccion_completa
                obj.modalidad_trabajo_preferida = request.form.get('modalidad','').strip() or obj.modalidad_trabajo_preferida
                obj.rutas_cercanas              = request.form.get('rutas','').strip() or obj.rutas_cercanas
                obj.empleo_anterior             = request.form.get('empleo_anterior','').strip() or obj.empleo_anterior
                obj.anos_experiencia            = request.form.get('anos_experiencia','').strip() or obj.anos_experiencia
                obj.areas_experiencia           = request.form.get('areas_experiencia','').strip() or obj.areas_experiencia

                obj.sabe_planchar               = request.form.get('sabe_planchar') == 'si'
                obj.contactos_referencias_laborales = request.form.get('contactos_referencias_laborales','').strip() \
                                                      or obj.contactos_referencias_laborales
                obj.referencias_familiares_detalle  = request.form.get('referencias_familiares_detalle','').strip() \
                                                      or obj.referencias_familiares_detalle

                obj.cedula                      = request.form.get('cedula','').strip() or obj.cedula
                obj.acepta_porcentaje_sueldo    = 1 if request.form.get('acepta_porcentaje') else 0

                try:
                    db.session.commit()
                    flash("✅ Datos actualizados correctamente.", "success")
                    return redirect(url_for('buscar_candidata', candidata_id=cid))
                except Exception as e:
                    db.session.rollback()
                    mensaje = f"❌ Error al guardar: {e}"
        else:
            mensaje = "❌ ID de candidata inválido."

    # Carga detalles
    cid = request.args.get('candidata_id','').strip()
    if cid.isdigit():
        candidata = Candidata.query.get(int(cid))
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."

    # Búsqueda global: convertimos edad a String para usar ILIKE
    if busqueda and not candidata:
        filtros = [
            Candidata.nombre_completo.ilike(f"%{busqueda}%"),
            cast(Candidata.edad, String).ilike(f"%{busqueda}%"),
            Candidata.numero_telefono.ilike(f"%{busqueda}%"),
            Candidata.direccion_completa.ilike(f"%{busqueda}%"),
            Candidata.modalidad_trabajo_preferida.ilike(f"%{busqueda}%"),
            Candidata.rutas_cercanas.ilike(f"%{busqueda}%"),
            Candidata.empleo_anterior.ilike(f"%{busqueda}%"),
            Candidata.anos_experiencia.ilike(f"%{busqueda}%"),
            Candidata.areas_experiencia.ilike(f"%{busqueda}%"),
            Candidata.contactos_referencias_laborales.ilike(f"%{busqueda}%"),
            Candidata.referencias_familiares_detalle.ilike(f"%{busqueda}%"),
            Candidata.cedula.ilike(f"%{busqueda}%"),
        ]
        resultados = Candidata.query.filter(or_(*filtros)).all()
        if not resultados:
            mensaje = "⚠️ No se encontraron coincidencias."

    return render_template(
        'buscar.html',
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje
    )

from flask import current_app
from sqlalchemy import or_

@app.route('/filtrar', methods=['GET', 'POST'])
def filtrar():
    # 1) Lee filtros del formulario (GET o POST)
    ciudad      = request.values.get('ciudad', '').strip()
    rutas       = request.values.get('rutas', '').strip()
    modalidad   = request.values.get('modalidad', '').strip()
    experiencia = request.values.get('experiencia_anos', '').strip()
    areas       = request.values.get('areas_experiencia', '').strip()

    # 2) Construye dinámicamente la lista de condiciones
    filtros = []
    if ciudad:
        filtros.append(Candidata.direccion_completa.ilike(f'%{ciudad}%'))
    if rutas:
        filtros.append(Candidata.rutas_cercanas.ilike(f'%{rutas}%'))
    if modalidad:
        filtros.append(Candidata.modalidad_trabajo_preferida.ilike(f'%{modalidad}%'))
    if experiencia:
        filtros.append(Candidata.anos_experiencia.ilike(f'%{experiencia}%'))
    if areas:
        filtros.append(Candidata.areas_experiencia.ilike(f'%{areas}%'))

    # 3) Condiciones fijas: debe tener código y porciento vacío o 0
    filtros.append(Candidata.codigo.isnot(None))
    filtros.append(or_(Candidata.porciento == None, Candidata.porciento == 0))

    try:
        # 4) Ejecuta la consulta
        query = Candidata.query.filter(*filtros)
        candidatas = query.all()

        # 5) Mapea a dicts para el template
        resultados = [{
            'fila':               c.fila,
            'nombre':             c.nombre_completo,
            'codigo':             c.codigo,
            'telefono':           c.numero_telefono,
            'direccion':          c.direccion_completa,
            'rutas':              c.rutas_cercanas,
            'cedula':             c.cedula,
            'modalidad':          c.modalidad_trabajo_preferida,
            'experiencia_anos':   c.anos_experiencia,
            'areas_experiencia':  c.areas_experiencia,
        } for c in candidatas]

        mensaje = None
        if (ciudad or rutas or modalidad or experiencia or areas) and not resultados:
            mensaje = "⚠️ No se encontraron resultados para los filtros aplicados."

    except Exception as e:
        current_app.logger.error(f"Error al filtrar candidatas en la DB: {e}", exc_info=True)
        resultados = []
        mensaje = f"❌ Error al filtrar los datos: {e}"

    # 6) Renderiza la plantilla
    return render_template('filtrar.html', resultados=resultados, mensaje=mensaje)



import traceback  # Importa para depuración

from flask import flash, render_template, request, url_for, redirect
from sqlalchemy import or_
from datetime import datetime
from decimal import Decimal

from models import Candidata
from config_app import db
from utils_codigo import generar_codigo_unico  # tu nueva función optimizada

def parse_date(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        return None

def parse_decimal(s: str):
    try:
        return Decimal(s.replace(',', '.'))
    except:
        return None

@app.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion():
    mensaje = ""
    resultados = []
    candidata = None

    # 1) POST: guardado o búsqueda vía formulario
    if request.method == "POST":
        # — Guardar inscripción —
        if request.form.get("guardar_inscripcion"):
            cid = request.form.get("candidata_id", "").strip()
            if not cid.isdigit():
                flash("❌ ID inválido.", "error")
                return redirect(url_for('inscripcion'))

            obj = Candidata.query.get(int(cid))
            if not obj:
                flash("⚠️ Candidata no encontrada.", "error")
                return redirect(url_for('inscripcion'))

            # Generar código si no existe
            if not obj.codigo:
                obj.codigo = generar_codigo_unico()

            # Actualizar campos
            obj.medio_inscripcion = request.form.get("medio","").strip() or obj.medio_inscripcion
            obj.inscripcion       = request.form.get("estado") == "si"
            obj.monto             = parse_decimal(request.form.get("monto","")) or obj.monto
            obj.fecha             = parse_date(request.form.get("fecha","")) or obj.fecha

            try:
                db.session.commit()
                flash(f"✅ Inscripción guardada. Código: {obj.codigo}", "success")
                candidata = obj
            except Exception as e:
                db.session.rollback()
                flash(f"❌ Error al guardar inscripción: {e}", "error")
                return redirect(url_for('inscripcion'))

        # — Búsqueda vía POST —
        else:
            q = request.form.get("buscar","").strip()
            if q:
                like = f"%{q}%"
                resultados = Candidata.query.filter(
                    or_(
                        Candidata.nombre_completo.ilike(like),
                        Candidata.cedula.ilike(like),
                        Candidata.numero_telefono.ilike(like)
                    )
                ).all()
                if not resultados:
                    flash("⚠️ No se encontraron coincidencias.", "error")

    # 2) GET: búsqueda o ver detalles
    else:
        # — Búsqueda GET —
        q = request.args.get("buscar","").strip()
        if q:
            like = f"%{q}%"
            resultados = Candidata.query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like)
                )
            ).all()
            if not resultados:
                mensaje = "⚠️ No se encontraron coincidencias."

        # — Detalles GET —
        sel = request.args.get("candidata_seleccionada","").strip()
        if not resultados and sel.isdigit():
            candidata = Candidata.query.get(int(sel))
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."

    return render_template(
        "inscripcion.html",
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje
    )


from flask import flash, render_template, request, url_for, redirect
from sqlalchemy import or_
from datetime import datetime
from decimal import Decimal

from models import Candidata
from config_app import db

def parse_date(s: str):
    """Convierte 'YYYY-MM-DD' a date, o devuelve None."""
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except ValueError:
        return None

def parse_decimal(s: str):
    """Convierte '123.45' o '123,45' a Decimal, o devuelve None."""
    try:
        return Decimal((s or "").replace(',', '.'))
    except:
        return None

@app.route('/porciento', methods=['GET', 'POST'])
def porciento():
    resultados = []
    candidata = None

    if request.method == "POST":
        # ——— Guardar porcentaje ———
        fila_id = request.form.get('fila_id', '').strip()
        if not fila_id.isdigit():
            flash("❌ Fila inválida.", "danger")
            return redirect(url_for('porciento'))

        obj = Candidata.query.get(int(fila_id))
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('porciento'))

        # parse inputs
        fecha_pago   = parse_date(request.form.get("fecha_pago",""))
        fecha_inicio = parse_date(request.form.get("fecha_inicio",""))
        monto_total  = parse_decimal(request.form.get("monto_total",""))

        if None in (fecha_pago, fecha_inicio, monto_total):
            flash("❌ Datos incompletos o inválidos.", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

        # calcula 25 %
        porcentaje = (monto_total * Decimal("0.25")).quantize(Decimal("0.01"))

        # guarda en la BD
        obj.fecha_de_pago = fecha_pago
        obj.inicio        = fecha_inicio
        obj.monto_total   = monto_total
        obj.porciento     = porcentaje

        try:
            db.session.commit()
            flash(f"✅ Se guardó correctamente. 25 % de {monto_total} es {porcentaje}.", "success")
            candidata = obj
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al actualizar: {e}", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

    else:
        # ——— Búsqueda GET ———
        q = request.args.get("busqueda","").strip()
        if q:
            like = f"%{q}%"
            resultados = Candidata.query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like)
                )
            ).all()
            if not resultados:
                flash("⚠️ No se encontraron coincidencias.", "warning")

        # ——— Detalle GET ———
        sel = request.args.get("candidata","").strip()
        if sel.isdigit() and not resultados:
            candidata = Candidata.query.get(int(sel))
            if not candidata:
                flash("⚠️ Candidata no encontrada.", "warning")

    return render_template(
        "porciento.html",
        resultados=resultados,
        candidata=candidata
    )


from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy import or_
from decimal import Decimal
from models import Candidata
from config_app import db
from datetime import datetime

@app.route('/pagos', methods=['GET', 'POST'])
def pagos():
    resultados = []
    candidata = None

    # — POST: actualizar pago —
    if request.method == 'POST':
        fila = request.form.get('fila', type=int)
        monto_str = request.form.get('monto_pagado', '').strip()
        calificacion = request.form.get('calificacion', '').strip()

        if not fila or not monto_str or not calificacion:
            flash("❌ Datos inválidos.", "danger")
            return redirect(url_for('pagos'))

        # Convertir a Decimal
        try:
            monto_pagado = Decimal(monto_str)
        except:
            flash("❌ Monto inválido.", "danger")
            return redirect(url_for('pagos'))

        obj = Candidata.query.get(fila)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('pagos'))

        # Ahora restamos de porciento (lo que debe), no de monto_total
        obj.porciento = max(obj.porciento - monto_pagado, Decimal('0'))
        obj.calificacion = calificacion

        try:
            db.session.commit()
            flash("✅ Pago guardado con éxito.", "success")
            candidata = obj
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al guardar: {e}", "danger")

        return render_template('pagos.html',
                               resultados=[],
                               candidata=candidata)

    # — GET: búsqueda y detalle —
    q = request.args.get('busqueda', '').strip()
    sel = request.args.get('candidata', '').strip()

    if q:
        like = f"%{q}%"
        filas = Candidata.query.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        ).all()

        for c in filas:
            resultados.append({
                'fila':     c.fila,
                'nombre':   c.nombre_completo,
                'cedula':   c.cedula,
                'telefono': c.numero_telefono or 'No especificado',
            })

        if not resultados:
            flash("⚠️ No se encontraron coincidencias.", "warning")

    if sel.isdigit() and not resultados:
        obj = Candidata.query.get(int(sel))
        if obj:
            candidata = obj
        else:
            flash("⚠️ Candidata no encontrada.", "warning")

    return render_template('pagos.html',
                           resultados=resultados,
                           candidata=candidata)



from flask import Flask, render_template, url_for
from config_app import create_app, db
from models import Candidata
from datetime import datetime


@app.route('/reporte_pagos', methods=['GET'])
def reporte_pagos():
    """Muestra un listado de todas las candidatas con porciento pendiente (> 0)."""
    try:
        # Traemos todas las candidatas cuyo campo `porciento` sea mayor a 0
        pendientes = Candidata.query.filter(Candidata.porciento > 0).all()

        # Construimos la lista de dicts que pasaremos al template
        pagos_pendientes = []
        for c in pendientes:
            pagos_pendientes.append({
                'nombre': c.nombre_completo,
                'cedula': c.cedula,
                'codigo': c.codigo or "No especificado",
                'ciudad': c.direccion_completa or "No especificado",
                'monto_total': float(c.monto_total or 0),
                'porcentaje_pendiente': float(c.porciento or 0),
                'fecha_inicio': c.inicio.strftime("%Y-%m-%d") if c.inicio else "No registrada",
                'fecha_pago':  c.fecha_de_pago.strftime("%Y-%m-%d") if c.fecha_de_pago else "No registrada",
            })

        if not pagos_pendientes:
            mensaje = "⚠️ No se encontraron pagos pendientes."
        else:
            mensaje = None

        return render_template(
            'reporte_pagos.html',
            pagos_pendientes=pagos_pendientes,
            mensaje=mensaje
        )

    except Exception as e:
        # Loguea el error y muestra mensaje al usuario
        current_app.logger.exception("Error en reporte_pagos")
        return render_template(
            'reporte_pagos.html',
            pagos_pendientes=[],
            mensaje=f"❌ Ocurrió un error al generar el reporte: {e}"
        ), 500


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


from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash
)
from werkzeug.utils import secure_filename
from models import Candidata, db

subir_bp = Blueprint('subir_fotos', __name__, url_prefix='/subir_fotos')

@subir_bp.route('', methods=['GET', 'POST'])
def subir_fotos():
    accion = request.args.get('accion', 'buscar')
    fila_id = request.args.get('fila', type=int)
    mensaje = None
    resultados = []

    # 1) BUSCAR: mostrar formulario y procesar búsqueda en la BD
    if accion == 'buscar':
        if request.method == 'POST':
            q = request.form.get('busqueda', '').strip()
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

            # Busca en nombre, cédula o teléfono
            like = f"%{q}%"
            filas = Candidata.query.filter(
                (Candidata.nombre_completo.ilike(like)) |
                (Candidata.cedula.ilike(like)) |
                (Candidata.numero_telefono.ilike(like))
            ).all()

            if not filas:
                flash("⚠️ No se encontraron candidatas.", "warning")
            else:
                resultados = [{
                    'fila': c.fila,
                    'nombre': c.nombre_completo,
                    'telefono': c.numero_telefono or 'No especificado',
                    'cedula': c.cedula
                } for c in filas]

        return render_template(
            'subir_fotos.html',
            accion='buscar',
            resultados=resultados
        )

    # 2) SUBIR: mostrar formulario de subida o procesarlo
    if accion == 'subir':
        # Validamos que exista fila_id y la candidata
        candidata = None
        if not fila_id:
            flash("❌ Debes seleccionar primero una candidata.", "danger")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        candidata = Candidata.query.get(fila_id)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        # GET: muestro el formulario
        if request.method == 'GET':
            return render_template(
                'subir_fotos.html',
                accion='subir',
                fila=fila_id
            )

        # POST: recibo los archivos y los guardo en la BD
        files = {
            'depuracion': request.files.get('depuracion'),
            'perfil':     request.files.get('perfil'),
            'cedula1':    request.files.get('cedula1'),
            'cedula2':    request.files.get('cedula2'),
        }

        # Validación básica
        for campo, archivo in files.items():
            if not archivo or archivo.filename == '':
                flash(f"❌ Falta el archivo para {campo}.", "danger")
                return render_template(
                    'subir_fotos.html',
                    accion='subir',
                    fila=fila_id
                )

        try:
            # Leo cada archivo como bytes y lo asigno al modelo
            candidata.depuracion = files['depuracion'].read()
            candidata.perfil     = files['perfil'].read()
            candidata.cedula1    = files['cedula1'].read()
            candidata.cedula2    = files['cedula2'].read()

            db.session.commit()
            flash("✅ Imágenes subidas y guardadas en la base de datos.", "success")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error guardando en la BD: {e}", "danger")
            return render_template(
                'subir_fotos.html',
                accion='subir',
                fila=fila_id
            )

    # Cualquier otro caso, vuelvo a buscar
    return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))
app.register_blueprint(subir_bp)

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

from flask import send_file, request, redirect, url_for, render_template
import os, io
from fpdf import FPDF
from models import Candidata

# -------------------------------------------------------
# RUTA PRINCIPAL: BUSCAR, VER y DESCARGAR
# -------------------------------------------------------
@app.route("/gestionar_archivos", methods=["GET", "POST"])
def gestionar_archivos():
    accion = request.args.get("accion", "buscar")
    mensaje = None
    resultados = []
    docs = {}
    fila = request.args.get("fila", "").strip()

    # -------- ACCIÓN: DESCARGAR PDF O ZIP O IMÁGENES --------
    if accion == "descargar":
        doc = request.args.get("doc", "").strip()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        idx = int(fila)
        if doc == "pdf":
            return redirect(url_for("generar_pdf_entrevista", fila=idx))
        # aquí podrías seguir delegando a descargar_todo_en_zip o descargar_uno si las necesitas
        return "Documento no reconocido", 400

    # -------- ACCIÓN: BUSCAR CANDIDATAS --------
    if accion == "buscar":
        if request.method == "POST":
            q = request.form.get("busqueda", "").strip()
            if q:
                like = f"%{q}%"
                filas = Candidata.query.filter(
                    (Candidata.nombre_completo.ilike(like)) |
                    (Candidata.cedula.ilike(like)) |
                    (Candidata.numero_telefono.ilike(like))
                ).all()
                resultados = [{
                    "fila": c.fila,
                    "nombre": c.nombre_completo,
                    "telefono": c.numero_telefono or "No especificado",
                    "cedula": c.cedula or "No especificado"
                } for c in filas]
                if not resultados:
                    mensaje = "⚠️ No se encontraron candidatas."
        return render_template("gestionar_archivos.html",
                               accion=accion,
                               resultados=resultados,
                               mensaje=mensaje)

    # -------- ACCIÓN: VER DOCUMENTOS Y ENTREVISTA --------
    if accion == "ver":
        if not fila.isdigit():
            mensaje = "Error: Fila inválida."
            return render_template("gestionar_archivos.html",
                                   accion="buscar",
                                   mensaje=mensaje)
        idx = int(fila)
        c = Candidata.query.filter_by(fila=idx).first()
        if not c:
            mensaje = "⚠️ Candidata no encontrada."
            return render_template("gestionar_archivos.html",
                                   accion="buscar",
                                   mensaje=mensaje)

        docs["depuracion"] = c.depuracion
        docs["perfil"]     = c.perfil
        docs["cedula1"]    = c.cedula1
        docs["cedula2"]    = c.cedula2

        # Leemos la entrevista desde la BD o desde Sheets si la guardas allí:
        docs["entrevista"] = c.entrevista or ""

        return render_template("gestionar_archivos.html",
                               accion=accion,
                               fila=idx,
                               docs=docs,
                               mensaje=mensaje)

    # Si no hay acción válida, redirige a buscar
    return redirect(url_for("gestionar_archivos", accion="buscar"))


# -------------------------------------------------------
# RUTA PARA GENERAR/DESCARGAR EL PDF DE LA ENTREVISTA
# -------------------------------------------------------
@app.route('/generar_pdf_entrevista')
def generar_pdf_entrevista():
    # 0) Parámetro fila
    fila_index = request.args.get('fila', type=int)
    if not fila_index:
        return "Error: falta parámetro fila", 400

    # 1) Recupera la candidata y su entrevista
    c = Candidata.query.get(fila_index)
    if not c or not c.entrevista:
        return "No hay entrevista registrada para esa fila", 404
    texto_entrevista = c.entrevista

    # 2) Referencias desde la BD
    ref_laborales  = c.referencias_laboral or ""
    ref_familiares = c.referencias_familiares or ""

    try:
        pdf = FPDF()
        pdf.add_page()

        # — Fuentes Unicode —
        font_dir = os.path.join(app.root_path, "static", "fonts")
        reg = os.path.join(font_dir, "DejaVuSans.ttf")
        bold= os.path.join(font_dir, "DejaVuSans-Bold.ttf")
        pdf.add_font("DejaVuSans", "", reg, uni=True)
        pdf.add_font("DejaVuSans", "B", bold, uni=True)

        # — Logo y líneas —
        logo = os.path.join(app.root_path, "static", "logo_nuevo.png")
        if os.path.exists(logo):
            w = 70
            x = (pdf.w - w) / 2
            pdf.image(logo, x=x, y=10, w=w)
        pdf.set_line_width(0.5)
        pdf.set_draw_color(0, 0, 0)
        pdf.line(pdf.l_margin, 30, pdf.w - pdf.r_margin, 30)
        pdf.set_y(40)

        # — Título —
        pdf.set_font("DejaVuSans", "B", 18)
        pdf.set_fill_color(0, 102, 204)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, "Entrevista de Candidata", ln=True, align="C", fill=True)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(10)

        # — Entrevista —
        pdf.set_font("DejaVuSans", "", 12)
        pdf.set_text_color(0, 0, 0)
        for line in texto_entrevista.split("\n"):
            pdf.set_x(pdf.l_margin)
            if ":" in line:
                q, a = line.split(":", 1)
                pregunta  = q.strip() + ":"
                respuesta = a.strip()

                pdf.multi_cell(0, 8, pregunta)
                pdf.ln(1)

                bullet = "•"
                pdf.set_font("DejaVuSans", "", 16)
                bw = pdf.get_string_width(bullet + " ")
                pdf.cell(bw, 8, bullet, ln=0)

                pdf.set_font("DejaVuSans", "", 12)
                pdf.set_text_color(0, 102, 204)
                avail_w = pdf.w - pdf.r_margin - (pdf.l_margin + bw)
                pdf.multi_cell(avail_w, 8, respuesta)
                pdf.ln(4)

                pdf.set_text_color(0, 0, 0)
                pdf.set_font("DejaVuSans", "", 12)
            else:
                pdf.multi_cell(0, 8, line)
                pdf.ln(4)
        pdf.ln(5)

        # — Referencias —
        pdf.set_font("DejaVuSans", "B", 14)
        pdf.set_text_color(0, 102, 204)
        pdf.cell(0, 10, "Referencias", ln=True)
        pdf.ln(3)

        # Laborales
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Referencias Laborales:", ln=True)
        pdf.set_font("DejaVuSans", "", 12)
        if ref_laborales:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_laborales)
        else:
            pdf.cell(0, 8, "No hay referencias laborales.", ln=True)
        pdf.ln(5)

        # Familiares
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Referencias Familiares:", ln=True)
        pdf.set_font("DejaVuSans", "", 12)
        if ref_familiares:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_familiares)
        else:
            pdf.cell(0, 8, "No hay referencias familiares.", ln=True)
        pdf.ln(5)

        # — Salida —
        output    = pdf.output(dest="S")
        pdf_bytes = output if isinstance(output, (bytes, bytearray)) else output.encode("latin1")
        buf       = io.BytesIO(pdf_bytes); buf.seek(0)
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_candidata_{fila_index}.pdf"
        )

    except Exception as e:
        return f"Error interno generando PDF: {e}", 500


@app.route("/gestionar_archivos/descargar_uno", methods=["GET"])
def descargar_uno_db():
    cid = request.args.get("id", type=int)
    doc = request.args.get("doc", "").strip()
    if not cid or doc not in ("depuracion","perfil","cedula1","cedula2"):
        return "Error: parámetros inválidos", 400
    candidata = Candidata.query.get(cid)
    if not candidata:
        return "Candidata no encontrada", 404
    data = getattr(candidata, doc)
    if not data:
        return f"No hay archivo para {doc}", 404

    return send_file(
        io.BytesIO(data),
        mimetype="image/png",
        as_attachment=True,
        download_name=f"{doc}.png"
    )

from flask import send_file, render_template, request
import io
import pandas as pd
from datetime import datetime
from models import Candidata

@app.route('/reporte_inscripciones', methods=['GET'])
def reporte_inscripciones():
    # 1) Parámetros mes, año y descarga
    try:
        mes = int(request.args.get('mes', datetime.today().month))
        anio = int(request.args.get('anio', datetime.today().year))
        descargar = request.args.get('descargar', '0')  # "1" para descargar Excel
    except Exception as e:
        return f"Parámetros inválidos: {e}", 400

    # 2) Query a la base de datos
    try:
        # Solo candidatas inscritas (inscripcion=True) con fecha de inscripción en ese mes/año
        query = Candidata.query.filter(
            Candidata.inscripcion.is_(True),
            Candidata.fecha.isnot(None),
            db.extract('month', Candidata.fecha) == mes,
            db.extract('year', Candidata.fecha) == anio
        ).all()
    except Exception as e:
        return f"Error al consultar la base de datos: {e}", 500

    # 3) Si no hay resultados
    if not query:
        mensaje = f"No se encontraron inscripciones para {mes}/{anio}."
        return render_template("reporte_inscripciones.html",
                               reporte_html="",
                               mes=mes,
                               anio=anio,
                               mensaje=mensaje)

    # 4) Convertir a DataFrame
    df = pd.DataFrame([{
        "Nombre":         c.nombre_completo,
        "Ciudad":         (c.direccion_completa or ""),
        "Teléfono":       (c.numero_telefono or ""),
        "Cédula":         c.cedula,
        "Código":         (c.codigo or ""),
        "Medio":          (c.medio_inscripcion or ""),
        "Inscripción":    "Sí" if c.inscripcion else "No",
        "Monto":          float(c.monto or 0),
        "Fecha":          c.fecha.strftime("%Y-%m-%d") if c.fecha else ""
    } for c in query])

    # 5) Descarga o visualización
    if descargar == "1":
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Reporte')
        output.seek(0)
        filename = f"Reporte_Inscripciones_{anio}_{mes:02d}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    else:
        reporte_html = df.to_html(classes="table table-striped", index=False, border=0)
        return render_template("reporte_inscripciones.html",
                               reporte_html=reporte_html,
                               mes=mes,
                               anio=anio,
                               mensaje="")

from flask import (
    render_template, request, redirect, url_for, flash
)
from models import Candidata, db
from sqlalchemy import or_

@app.route('/referencias', methods=['GET', 'POST'])
def referencias():
    mensaje = None
    accion = request.args.get('accion', 'buscar')
    resultados = []
    candidata = None

    # 1) BÚSQUEDA
    if request.method == 'POST' and 'busqueda' in request.form:
        termino = request.form['busqueda'].strip()
        if termino:
            like = f"%{termino}%"
            filas = Candidata.query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like)
                )
            ).all()
            resultados = [
                {
                    'id': c.fila,
                    'nombre': c.nombre_completo,
                    'cedula': c.cedula,
                    'telefono': c.numero_telefono or 'No especificado'
                }
                for c in filas
            ]
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas."
        else:
            mensaje = "⚠️ Ingresa un término de búsqueda."
        accion = 'buscar'
        return render_template(
            'referencias.html',
            accion=accion,
            resultados=resultados,
            mensaje=mensaje
        )

    # 2) VER DETALLE
    candidata_id = request.args.get('candidata', type=int)
    if request.method == 'GET' and candidata_id:
        candidata = Candidata.query.get(candidata_id)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."
            accion = 'buscar'
            return render_template(
                'referencias.html',
                accion=accion,
                resultados=[],
                mensaje=mensaje
            )
        accion = 'ver'
        return render_template(
            'referencias.html',
            accion=accion,
            candidata=candidata,
            mensaje=mensaje
        )

    # 3) GUARDAR REFERENCIAS
    if request.method == 'POST' and 'candidata_id' in request.form:
        cid = request.form.get('candidata_id', type=int)
        candidata = Candidata.query.get(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no existe."
        else:
            candidata.referencias_laboral    = request.form.get('referencias_laboral', '').strip()
            candidata.referencias_familiares = request.form.get('referencias_familiares', '').strip()
            try:
                db.session.commit()
                mensaje = "✅ Referencias actualizadas."
            except Exception as e:
                db.session.rollback()
                mensaje = f"❌ Error al guardar: {e}"
        accion = 'ver'
        return render_template(
            'referencias.html',
            accion=accion,
            candidata=candidata,
            mensaje=mensaje
        )

    # 4) MODO BÚSQUEDA POR DEFECTO
    return render_template(
        'referencias.html',
        accion='buscar',
        resultados=[],
        mensaje=mensaje
    )




from flask import render_template, redirect, url_for, session, request
from datetime import datetime, timedelta
import calendar, difflib, logging

def flexible_match(search_term, text, threshold=0.6):
    st = search_term.lower()
    t = text.lower()
    if st in t:
        return True
    ratio = difflib.SequenceMatcher(None, st, t).ratio()
    return ratio >= threshold

from flask import Flask, render_template, request, redirect, url_for, session
import logging
from datetime import datetime, timedelta
import calendar

@app.route('/solicitudes', methods=['GET', 'POST'])
def solicitudes():
    # Verifica la sesión
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    # Determina la acción (si no viene, usa "buscar" o "ver")
    accion = request.args.get('accion', None)
    if not accion or accion.strip() == "":
        accion = "buscar" if request.args.get("codigo") else "ver"
    else:
        accion = accion.strip()
    
    mensaje = None

    # ---------------- REGISTRO: Crear nueva orden ----------------
    if accion == 'registro':
        if request.method == 'GET':
            return render_template('solicitudes_registro.html', accion=accion, mensaje=mensaje)
        elif request.method == 'POST':
            # Datos originales (Columnas A a I)
            codigo = request.form.get("codigo", "").strip()
            if not codigo:
                mensaje = "El Código de la Orden es obligatorio."
                return render_template('solicitudes_registro.html', accion=accion, mensaje=mensaje)
            descripcion = request.form.get("descripcion", "").strip()
            if not descripcion:
                mensaje = "La descripción es obligatoria."
                return render_template('solicitudes_registro.html', accion=accion, mensaje=mensaje)
            fecha_solicitud = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            empleado_orden = session.get('usuario', 'desconocido')
            estado = "En proceso"
            empleado_asignado = ""
            fecha_actualizacion = ""
            notas_inicial = ""
            historial_inicial = ""
            # Datos del cliente (Columnas J a M)
            nombre_cliente = request.form.get("nombre_cliente", "").strip()
            ciudad_cliente = request.form.get("ciudad_cliente", "").strip()
            sector = request.form.get("sector", "").strip()
            telefono_cliente = request.form.get("telefono_cliente", "").strip()
            extra_original = [nombre_cliente, ciudad_cliente, sector, telefono_cliente]
            
            datos_originales = [
                codigo, fecha_solicitud, empleado_orden, descripcion,
                estado, empleado_asignado, fecha_actualizacion, notas_inicial,
                historial_inicial
            ] + extra_original
            
            # Datos adicionales (Columnas N a Z o AB)
            direccion = request.form.get("direccion", "").strip()
            ruta = request.form.get("ruta", "").strip()
            modalidad_trabajo = request.form.get("modalidad_trabajo", "").strip()
            edad = request.form.get("edad", "").strip()
            nacionalidad = request.form.get("nacionalidad", "Dominicana").strip()
            alfabetizacion = "Sí" if request.form.get("habilidades_alfabetizacion") else "No"
            experiencia = request.form.get("experiencia", "").strip()
            horario = request.form.get("horario", "").strip()
            funciones = request.form.get("funciones", "").strip()
            descripcion_casa = request.form.get("descripcion_casa", "").strip()
            adultos = request.form.get("adultos", "").strip()
            sueldo = request.form.get("sueldo", "").strip()
            notas_solicitud = request.form.get("notas", "").strip()
            
            datos_nuevos = [
                direccion, ruta, modalidad_trabajo, edad, nacionalidad,
                alfabetizacion, experiencia, horario, funciones, descripcion_casa,
                adultos, sueldo, notas_solicitud
            ]
            
            # Nota: Ajusta el rango según la estructura de tu hoja (si es A:Z o A:AB)
            nueva_fila = datos_originales + datos_nuevos
            
            try:
                service.spreadsheets().values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Solicitudes!A1:Z",  # Modifica a "A1:AB" si la hoja contiene columnas hasta AB
                    valueInputOption="RAW",
                    body={"values": [nueva_fila]}
                ).execute()
                mensaje = "Orden registrada con éxito."
            except Exception as e:
                logging.error("Error al registrar orden: " + str(e), exc_info=True)
                mensaje = "Error al registrar la orden."
            return render_template('solicitudes_registro.html', accion=accion, mensaje=mensaje)
    
    # ---------------- VER: Listado completo --------------
    elif accion == 'ver':
        solicitudes_data = []
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Solicitudes!A1:Z"
            ).execute()
            solicitudes_data = result.get("values", [])
        except Exception as e:
            logging.error("Error al obtener listado: " + str(e), exc_info=True)
            mensaje = "Error al cargar el listado de órdenes."
        return render_template('solicitudes_ver.html', accion=accion, mensaje=mensaje, solicitudes=solicitudes_data)
    
    # ---------------- BUSCAR: Buscar por código --------------
    elif accion == 'buscar':
        codigo = request.args.get("codigo", "").strip()
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Solicitudes!A1:Z"
            ).execute()
            data = result.get("values", [])
            if not data:
                mensaje = "No se encontraron datos en la hoja."
                return render_template('solicitudes_busqueda.html', accion='buscar', mensaje=mensaje, solicitudes=[])
            header = data[0]
            matches = [row for row in data[1:] if row and row[0].strip() == codigo]
            if matches:
                found_order = matches[0]
                mensaje = f"Orden encontrada con el código {codigo}."
                search_result = [header, found_order]
                return render_template('solicitudes_busqueda.html', accion='buscar', mensaje=mensaje, solicitudes=search_result)
            else:
                mensaje = "No se encontró ninguna orden con el código proporcionado."
                return render_template('solicitudes_busqueda.html', accion='buscar', mensaje=mensaje, solicitudes=[header])
        except Exception as e:
            logging.error("Error al buscar la orden: " + str(e), exc_info=True)
            mensaje = "Error al buscar la orden."
            return render_template('solicitudes_busqueda.html', accion='buscar', mensaje=mensaje, solicitudes=[])
    
    # ---------------- REPORTES: Filtrado flexible y por fechas --------------
    elif accion == 'reportes':
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Solicitudes!A1:Z"
            ).execute()
            data = result.get("values", [])
        except Exception as e:
            logging.error("Error al obtener datos para reportes: " + str(e), exc_info=True)
            mensaje = "Error al obtener datos para reportes."
            return render_template('solicitudes_reportes.html', accion=accion, mensaje=mensaje, solicitudes_reporte=[])
        
        filtered = data[1:] if len(data) > 1 else []
        # (Filtrado por fechas y otros criterios se omiten aquí por brevedad)
        header = data[0] if data else []
        solicitudes_reporte = [header] + filtered if filtered else [header]
        return render_template('solicitudes_reportes.html',
                               accion=accion,
                               mensaje=f"Total órdenes: {len(data)-1 if len(data)>1 else 0}",
                               solicitudes_reporte=solicitudes_reporte)
    
    # ---------------- ACCIÓN "actualizar" (PARCIAL): Actualiza solo rangos específicos --------------
    elif accion == 'actualizar':
        fila_str = request.args.get("fila", "").strip()
        if not fila_str.isdigit():
            mensaje = "Fila inválida para actualizar."
            return render_template('solicitudes_actualizar.html', accion=accion, mensaje=mensaje)
        fila_index = int(fila_str)
        
        if request.method == 'GET':
            try:
                rango = f"Solicitudes!A{fila_index}:AB{fila_index}"
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango
                ).execute()
                solicitud_fila = result.get("values", [])
                solicitud_fila = solicitud_fila[0] if solicitud_fila else []
            except Exception as e:
                logging.error("Error al cargar la orden para actualizar: " + str(e), exc_info=True)
                mensaje = "Error al cargar la orden."
                solicitud_fila = []
            return render_template('solicitudes_actualizar.html', accion=accion, mensaje=mensaje, solicitud=solicitud_fila, fila=fila_index)
        
        elif request.method == 'POST':
            # Leer la fila original completa (A–AB)
            try:
                original_range = f"Solicitudes!A{fila_index}:AB{fila_index}"
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=original_range
                ).execute()
                original_row = result.get("values", [])[0]
                if len(original_row) < 28:
                    original_row.extend([""] * (28 - len(original_row)))
            except Exception as e:
                logging.error("Error al obtener la fila original: " + str(e), exc_info=True)
                mensaje = "Error al cargar los datos originales."
                return render_template('solicitudes_actualizar.html', accion=accion, mensaje=mensaje)
            
            # Para el segmento 1 (Columnas D a I: índices 3 a 8)
            # Si el campo del formulario es vacío, se conserva el valor original.
            new_desc = request.form.get("descripcion", "").strip() or original_row[3]
            new_estado = request.form.get("estado", "").strip() or original_row[4]
            new_empleado = request.form.get("empleado_asignado", "").strip() or original_row[5]
            new_fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Para "notas" (columna H, índice 7): si vacía, se conserva el valor original.
            new_notas = request.form.get("notas", "").strip()
            if new_notas == "":
                new_notas = original_row[7]
            # Para el historial (columna I, índice 8): se añade un registro nuevo al final.
            original_historial = original_row[8]
            new_historial = f"{new_fecha} - {session.get('usuario','desconocido')}: Cambió estado a {new_estado}"
            if new_notas:
                new_historial += f" Notas: {new_notas}"
            if original_historial:
                new_historial = original_historial + "\n" + new_historial
            update_data_1 = [new_desc, new_estado, new_empleado, new_fecha, new_notas, new_historial]
            update_range_1 = f"Solicitudes!D{fila_index}:I{fila_index}"
            
            # Para el segmento 2 (Columnas N a AB: índices 13 a 27)
            # Se aplicará la misma lógica: conservar el dato original si el formulario entrega cadena vacía.
            new_direccion = request.form.get("direccion", "").strip() or original_row[13]
            new_ruta = request.form.get("ruta", "").strip() or original_row[14]
            new_modalidad = request.form.get("modalidad_trabajo", "").strip() or original_row[15]
            new_edad = request.form.get("edad", "").strip() or original_row[16]
            new_nacionalidad = request.form.get("nacionalidad", "").strip() or original_row[17]
            # Para alfabetización, si no se marca, usamos "No" a menos que original tenga otro valor.
            new_alfabetizacion = "Sí" if request.form.get("habilidades_alfabetizacion") else (original_row[18] or "No")
            new_experiencia = request.form.get("experiencia", "").strip() or original_row[19]
            new_horario = request.form.get("horario", "").strip() or original_row[20]
            new_funciones = request.form.get("funciones", "").strip() or original_row[21]
            new_desc_casa = request.form.get("descripcion_casa", "").strip() or original_row[22]
            new_adultos = request.form.get("adultos", "").strip() or original_row[23]
            new_sueldo = request.form.get("sueldo", "").strip() or original_row[24]
            new_notas_solicitud = request.form.get("notas_solicitud", "").strip() or original_row[25]
            new_pago = request.form.get("pago", "").strip() or original_row[26]
            if request.form.get("pago", "").strip() != "":
                new_pago_fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                new_pago_fecha = original_row[27]
            update_data_2 = [
                new_direccion, new_ruta, new_modalidad, new_edad, new_nacionalidad,
                new_alfabetizacion, new_experiencia, new_horario, new_funciones,
                new_desc_casa, new_adultos, new_sueldo, new_notas_solicitud,
                new_pago, new_pago_fecha
            ]
            update_range_2 = f"Solicitudes!N{fila_index}:AB{fila_index}"
            
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=update_range_1,
                    valueInputOption="RAW",
                    body={"values": [update_data_1]}
                ).execute()
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=update_range_2,
                    valueInputOption="RAW",
                    body={"values": [update_data_2]}
                ).execute()
                mensaje = "Orden actualizada correctamente."
            except Exception as e:
                logging.error("Error al actualizar la orden: " + str(e), exc_info=True)
                mensaje = "Error al actualizar la orden."
            return render_template('solicitudes_actualizar.html', accion=accion, mensaje=mensaje)
    
    # ---------------- EDITAR: Actualización completa --------------
    elif accion == 'editar':
        if request.method == 'GET':
            codigo = request.args.get("codigo", "").strip()
            if not codigo:
                mensaje = "Debe proporcionar el código de la orden a editar."
                return render_template('solicitudes_editar_buscar.html', accion=accion, mensaje=mensaje)
            try:
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Solicitudes!A1:AB"
                ).execute()
                data = result.get("values", [])
                orden_encontrada = None
                fila_index = None
                for idx, row in enumerate(data[1:], start=2):
                    if row and row[0] == codigo:
                        orden_encontrada = row
                        fila_index = idx
                        break
                if orden_encontrada:
                    mensaje = f"Orden encontrada en la fila {fila_index}."
                    return render_template('solicitudes_editar.html', accion=accion, mensaje=mensaje, orden=orden_encontrada, fila=fila_index)
                else:
                    mensaje = "No se encontró ninguna orden con el código proporcionado."
                    return render_template('solicitudes_editar_buscar.html', accion=accion, mensaje=mensaje)
            except Exception as e:
                logging.error("Error al buscar la orden para editar: " + str(e), exc_info=True)
                mensaje = "Error al cargar la orden para editar."
                return render_template('solicitudes_editar_buscar.html', accion=accion, mensaje=mensaje)
        
        elif request.method == 'POST':
            fila_str = request.form.get("fila", "").strip()
            if not fila_str.isdigit():
                mensaje = "Fila inválida para editar."
                return render_template('solicitudes_editar.html', accion=accion, mensaje=mensaje)
            fila_index = int(fila_str)
            try:
                rango_completo = f"Solicitudes!A{fila_index}:AB{fila_index}"
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango_completo
                ).execute()
                fila_original = result.get("values", [])[0]
                if len(fila_original) < 28:
                    fila_original.extend([""] * (28 - len(fila_original)))
            except Exception as e:
                logging.error("Error al obtener datos originales para edición: " + str(e), exc_info=True)
                mensaje = "Error al cargar la fila original."
                return render_template('solicitudes_editar.html', accion=accion, mensaje=mensaje)
            
            descripcion = request.form.get("descripcion", "").strip()
            if not descripcion:
                mensaje = "La descripción es obligatoria."
                return render_template('solicitudes_editar.html', accion=accion, mensaje=mensaje, orden=fila_original, fila=fila_index)
            estado = request.form.get("estado", "").strip()
            empleado_asignado = request.form.get("empleado_asignado", "").strip()
            notas_actuales = request.form.get("notas", "").strip()
            fecha_actualizacion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                rango_historial = f"Solicitudes!I{fila_index}"
                respuesta_hist = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango_historial
                ).execute()
                historial_actual = respuesta_hist.get("values", [])
                historial_texto = historial_actual[0][0] if historial_actual and historial_actual[0] else ""
            except Exception as e:
                logging.error("Error al leer historial: " + str(e), exc_info=True)
                historial_texto = ""
            nuevo_registro = f"{fecha_actualizacion} - {session.get('usuario','desconocido')}: Edición completa."
            if notas_actuales:
                nuevo_registro += f" Notas: {notas_actuales}"
            historial_texto = (historial_texto + "\n" + nuevo_registro) if historial_texto else nuevo_registro
            
            nueva_fila = fila_original.copy()
            nueva_fila[3] = descripcion            # Columna D
            nueva_fila[4] = estado                 # Columna E
            nueva_fila[5] = empleado_asignado      # Columna F
            nueva_fila[6] = fecha_actualizacion    # Columna G
            nueva_fila[7] = ""                     # Columna H
            nueva_fila[8] = historial_texto        # Columna I
            # Actualiza el resto de las columnas con los valores recibidos (N a AB)
            nueva_fila[13] = request.form.get("direccion", "").strip()    
            nueva_fila[14] = request.form.get("ruta", "").strip()         
            nueva_fila[15] = request.form.get("modalidad_trabajo", "").strip()  
            nueva_fila[16] = request.form.get("edad", "").strip()         
            nueva_fila[17] = request.form.get("nacionalidad", "Dominicana").strip()  
            nueva_fila[18] = "Sí" if request.form.get("habilidades_alfabetizacion") else "No"  
            nueva_fila[19] = request.form.get("experiencia", "").strip()    
            nueva_fila[20] = request.form.get("horario", "").strip()        
            nueva_fila[21] = request.form.get("funciones", "").strip()      
            nueva_fila[22] = request.form.get("descripcion_casa", "").strip()  
            nueva_fila[23] = request.form.get("adultos", "").strip()        
            nueva_fila[24] = request.form.get("sueldo", "").strip()         
            nueva_fila[25] = request.form.get("notas_solicitud", "").strip() 
            
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=rango_completo,
                    valueInputOption="RAW",
                    body={"values": [nueva_fila]}
                ).execute()
                mensaje = "Orden editada correctamente."
            except Exception as e:
                logging.error("Error al editar la orden: " + str(e), exc_info=True)
                mensaje = "Error al editar la orden."
            return render_template('solicitudes_editar.html', accion=accion, mensaje=mensaje, orden=nueva_fila, fila=fila_index)
    
    elif accion == 'disponibles':
         solicitudes_data = {}
         try:
             result = service.spreadsheets().values().get(
                   spreadsheetId=SPREADSHEET_ID,
                   range="Solicitudes!A1:AC"
             ).execute()
             data = result.get("values", [])
             disponibles = []
             today_str = datetime.today().strftime("%Y-%m-%d")
             if len(data) > 1:
                 header = data[0]
                 for i, sol in enumerate(data[1:], start=2):
                     if len(sol) < 5:
                         continue
                     estado_sol = sol[4].strip().lower() if sol[4] else ""
                     if estado_sol in ["disponible", "reemplazo"]:
                         if len(sol) >= 29:
                             fecha_copia = sol[28].strip()
                             if fecha_copia == today_str:
                                 continue
                         disponibles.append({"datos": sol, "fila": i})
             else:
                 header = []
             solicitudes_data = {"header": header, "ordenes": disponibles}
         except Exception as e:
             logging.error("Error al cargar órdenes disponibles: " + str(e), exc_info=True)
             mensaje = "Error al cargar órdenes disponibles."
             solicitudes_data = {"header": [], "ordenes": []}
         return render_template('solicitudes_disponibles.html', accion=accion, mensaje=mensaje, solicitudes=solicitudes_data)
    
    else:
         mensaje = "Acción no reconocida."
         return render_template('solicitudes_base.html', accion=accion, mensaje=mensaje)

@app.route('/marcar_copiada', methods=['POST'])
def marcar_copiada():
    fila_str = request.form.get("fila", "").strip()
    if not fila_str.isdigit():
         return "Error: Fila inválida", 400
    fila_index = int(fila_str)
    today_str = datetime.today().strftime("%Y-%m-%d")
    try:
         update_range = f"Solicitudes!AC{fila_index}:AC{fila_index}"
         service.spreadsheets().values().update(
               spreadsheetId=SPREADSHEET_ID,
               range=update_range,
               valueInputOption="RAW",
               body={"values": [[today_str]]}
         ).execute()
         return "OK", 200
    except Exception as e:
         logging.error("Error al marcar orden como copiada: " + str(e), exc_info=True)
         return "Error", 500

# ─────────────────────────────────────────────────────────────
# Ruta: Registro/Inscripción de Otros Empleos
# ─────────────────────────────────────────────────────────────
@app.route('/otros_empleos/inscripcion', methods=['GET', 'POST'])
def otros_inscripcion():
    mensaje = ""
    ws = get_sheet_otros()  # Obtiene la hoja de cálculo
    if not ws:
        mensaje = "Error al acceder a la hoja 'Otros'."
        return render_template("otros_inscripcion.html", mensaje=mensaje)

    headers = get_headers_otros()

    # Definir "keys" basados en índices:
    # Nombre -> índice 2, Edad -> índice 3, Teléfono -> índice 4, Cédula -> índice 6.
    nombre_key = headers[2]
    edad_key = headers[3]
    telefono_key = headers[4]
    cedula_key = headers[6]

    if request.method == 'GET':
        query = request.args.get("q", "").strip()
        if not query:
            return render_template("otros_inscripcion.html", 
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        try:
            data = ws.get_all_records()
        except Exception as e:
            mensaje = f"Error al obtener datos: {str(e)}"
            return render_template("otros_inscripcion.html", mensaje=mensaje,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        matches = []
        for row in data:
            # Convertir a cadena para evitar errores si la cédula es numérica.
            if (query.lower() in row.get(nombre_key, "").lower() or 
                query.lower() in str(row.get(cedula_key, "")).lower()):
                matches.append(row)
        if not matches:
            mensaje = "Candidato no encontrado."
            return render_template("otros_inscripcion.html", mensaje=mensaje,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        elif len(matches) == 1:
            candidato = matches[0]
            return render_template("otros_inscripcion.html", candidato=candidato, modo="enrolar", query=query,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        else:
            return render_template("otros_inscripcion.html", candidatos=matches, modo="seleccion", query=query,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
    
    if request.method == 'POST':
        cedula = request.form.get("cedula", "").strip()
        fecha_inscripcion = request.form.get("fecha_inscripcion", "").strip()
        monto = request.form.get("monto", "").strip()
        via_inscripcion = request.form.get("via_inscripcion", "").strip()
        try:
            data = ws.get_all_records()
        except Exception as e:
            mensaje = f"Error al obtener datos: {str(e)}"
            return render_template("otros_inscripcion.html", mensaje=mensaje,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        row_index = None
        candidato_actual = None
        for idx, row in enumerate(data, start=2):  # La fila 1 es el encabezado.
            if str(row.get(cedula_key, "")).strip() == cedula:
                row_index = idx
                candidato_actual = row
                break
        if not row_index or not candidato_actual:
            mensaje = "Candidato no encontrado para actualización."
            return render_template("otros_inscripcion.html", mensaje=mensaje,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)
        codigo = generate_next_code_otros()
        # Se actualizan las columnas de inscripción:
        # Columna S (índice 18): código, T (índice 19): fecha, U (índice 20): monto, V (índice 21): vía.
        new_row = []
        for i, header in enumerate(headers):
            if i == 18:
                new_row.append(codigo)
            elif i == 19:
                new_row.append(fecha_inscripcion)
            elif i == 20:
                new_row.append(monto)
            elif i == 21:
                new_row.append(via_inscripcion)
            else:
                new_row.append(candidato_actual.get(header, ""))
        try:
            ultima_col = chr(65 + len(headers) - 1)  # Calcula la letra de la última columna
            ws.update(f"A{row_index}:{ultima_col}{row_index}", [new_row])
            mensaje = f"Inscripción exitosa. Código asignado: {codigo}"
            flash(mensaje, "success")
            return redirect(url_for("otros_listar"))
        except Exception as e:
            mensaje = f"Error al actualizar inscripción: {str(e)}"
            return render_template("otros_inscripcion.html", mensaje=mensaje,
                                   nombre_key=nombre_key, cedula_key=cedula_key,
                                   edad_key=edad_key, telefono_key=telefono_key)

# La ruta 'otros_listar' y otras rutas relacionadas deben estar implementadas para completar el flujo.


# ─────────────────────────────────────────────────────────────
# Ruta: Listado y Búsqueda Flexible de Otros Empleos
# ─────────────────────────────────────────────────────────────
@app.route('/otros_empleos', methods=['GET'])
def otros_listar():
    mensaje = ""
    ws = get_sheet_otros()  # Obtiene la hoja "Otros"
    if not ws:
        mensaje = "Error al acceder a la hoja 'Otros'."
        return render_template("otros_listar.html", mensaje=mensaje, candidatos=[], query="")

    try:
        # Usamos get_all_values() para trabajar por índice (lista de listas)
        values = ws.get_all_values()  
    except Exception as e:
        mensaje = f"Error al obtener datos: {e}"
        return render_template("otros_listar.html", mensaje=mensaje, candidatos=[], query="")

    # La primera fila son encabezados; las siguientes son datos.
    # Creamos una lista de candidatos con los campos deseados:
    candidatos = []
    for row in values[1:]:
        # Aseguramos que la fila tenga al menos 7 columnas; si no, la extendemos
        if len(row) < 7:
            row.extend([""] * (7 - len(row)))
        # Creamos un diccionario con las columnas que se mostrarán
        candidato = {
            "Nombre completo": row[2],
            "¿Qué edad tienes?": row[3],
            "Número de teléfono": row[4],
            "Cédula": row[6],
            # Identificador: utilizamos el nombre (índice 2); si está vacío, usamos la cédula (índice 6)
            "identifier": row[2] if row[2].strip() != "" else row[6]
        }
        candidatos.append(candidato)
    
    # Obtener el query para la búsqueda (por ejemplo, por nombre, cédula o correo si se desea)
    query = request.args.get("q", "").strip().lower()
    if query:
        candidatos = [c for c in candidatos if query in c["Nombre completo"].lower() or
                      query in c["Número de teléfono"].lower() or query in c["Cédula"].lower()]
    
    return render_template("otros_listar.html", candidatos=candidatos, query=query)



# ─────────────────────────────────────────────────────────────
# Ruta: Detalle y Edición Inline de Otros Empleos
# ─────────────────────────────────────────────────────────────
@app.route('/otros_empleos/<identifier>', methods=['GET', 'POST'])
def otros_detalle(identifier):
    ws = get_sheet_otros()
    if not ws:
        mensaje = "Error al acceder a la hoja 'Otros'."
        return render_template("otros_detalle.html", mensaje=mensaje, candidato=None, short_headers=[])
    
    try:
        values = ws.get_all_values()
    except Exception as e:
        mensaje = f"Error al obtener datos: {e}"
        return render_template("otros_detalle.html", mensaje=mensaje, candidato=None, short_headers=[])
    
    if not values or len(values) < 2:
        mensaje = "No hay registros en la hoja."
        return render_template("otros_detalle.html", mensaje=mensaje, candidato=None, short_headers=values[0] if values else [])
    
    # Usaremos las columnas de la C hasta la T (índices 2 a 19) para mostrar los detalles.
    short_headers = ["Nombre", "Edad", "Teléfono", "Dirección", "Cédula", "Educación", "Carrera", "Idioma", "PC", "Licencia", "Habilidades", "Experiencia", "Servicios", "Ref Lab", "Ref Fam", "Términos", "Código", "Fecha"]
    
    identifier_norm = identifier.strip().lower()
    candidato_row = None
    row_index = None
    for i in range(1, len(values)):  # i desde 1 para omitir encabezado
        row = values[i]
        if len(row) < 22:
            row.extend([""] * (22 - len(row)))
        nombre = row[2].strip().lower() if len(row) > 2 else ""
        cedula = row[6].strip().lower() if len(row) > 6 else ""
        codigo = row[18].strip().lower() if len(row) > 18 else ""
        # Comparar con el identificador
        if identifier_norm == nombre or identifier_norm == cedula or identifier_norm == codigo:
            candidato_row = row
            row_index = i + 1
            break

    if not candidato_row:
        mensaje = "Candidato no encontrado."
        return render_template("otros_detalle.html", mensaje=mensaje, candidato=None, short_headers=short_headers)
    
    # En la vista de detalles, mostraremos las columnas 2 a 19 (C a T)
    # Para facilitar la edición, crearemos un solo formulario con una única tabla donde cada celda es un input.
    if request.method == 'POST':
        updated = candidato_row[:]  # Copia de la fila
        for idx in range(2, 18):  # Editables: desde columna C (índice 2) hasta R (índice 17)
            input_name = "col" + str(idx)
            value = request.form.get(input_name, "").strip()
            updated[idx] = value
        try:
            ultima_col = chr(65 + len(values[0]) - 1)
            ws.update(f"A{row_index}:{ultima_col}{row_index}", [updated])
            mensaje = "Información actualizada correctamente."
            flash(mensaje, "success")
            candidato_row = updated
        except Exception as e:
            mensaje = f"Error al actualizar: {e}"
            logging.error(mensaje, exc_info=True)
        candidate_details = { short_headers[i]: candidato_row[i+2] for i in range(len(short_headers)) }
        return render_template("otros_detalle.html", candidato=candidate_details, short_headers=short_headers, mensaje=mensaje)
    else:
        candidate_details = { short_headers[i]: candidato_row[i+2] for i in range(len(short_headers)) }
        return render_template("otros_detalle.html", candidato=candidate_details, short_headers=short_headers, mensaje="")




@app.route('/registro-publico', methods=['GET', 'POST'])
def registro_publico():
    """
    Versión amigable y sin login del formulario de inscripción.
    """
    if request.method == 'POST':
        # 1) Recogemos datos
        nombre_raw  = request.form.get('nombre_completo', '').strip()
        cedula_raw  = request.form.get('cedula', '').strip()
        edad        = request.form.get('edad', '').strip() or None
        telefono    = request.form.get('numero_telefono', '').strip() or None
        direccion   = request.form.get('direccion_completa', '').strip() or None
        modalidad   = request.form.get('modalidad_trabajo_preferida', '').strip() or None
        rutas       = request.form.get('rutas_cercanas', '').strip() or None
        empleo_ant  = request.form.get('empleo_anterior', '').strip() or None
        anos_exp    = request.form.get('anos_experiencia', '').strip() or None
        areas_exp   = request.form.get('areas_experiencia', '').strip() or None
        plancha     = True if request.form.get('sabe_planchar') == 'si' else False
        ref_laboral = request.form.get('contactos_referencias_laborales', '').strip() or None
        ref_familia = request.form.get('referencias_familiares_detalle', '').strip() or None
        acepta_pct  = True if request.form.get('acepta_porcentaje_sueldo') == '1' else False

        # 2) Normalizamos y validamos
        nombre = normalize_nombre(nombre_raw)
        cedula = normalize_cedula(cedula_raw)
        if not cedula:
            flash('❌ Tu cédula no es válida. Debe tener 11 dígitos.', 'danger')
            return redirect(url_for('registro_publico'))
        if not nombre:
            flash('❌ Tu nombre es obligatorio.', 'danger')
            return redirect(url_for('registro_publico'))
        if Candidata.query.filter_by(cedula=cedula).first():
            flash('❌ Ya estamos registrados con tu cédula.', 'danger')
            return redirect(url_for('registro_publico'))

        # 3) Creamos y guardamos
        nueva = Candidata(
            nombre_completo                 = nombre,
            cedula                          = cedula,
            edad                            = edad,
            numero_telefono                 = telefono,
            direccion_completa              = direccion,
            modalidad_trabajo_preferida     = modalidad,
            rutas_cercanas                  = rutas,
            empleo_anterior                 = empleo_ant,
            anos_experiencia                = anos_exp,
            areas_experiencia               = areas_exp,
            sabe_planchar                   = plancha,
            contactos_referencias_laborales = ref_laboral,
            referencias_familiares_detalle  = ref_familia,
            acepta_porcentaje_sueldo        = acepta_pct
        )
        try:
            db.session.add(nueva)
            db.session.commit()
            flash('✅ ¡Listo! Ya estás registrada.', 'success')
            return redirect(url_for('registro_publico'))
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error al guardar: {e}', 'danger')
            return redirect(url_for('registro_publico'))

    # GET → mostramos el formulario
    return render_template('registro_publico.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 1) Recogemos datos del formulario
        nombre_raw    = request.form.get('nombre_completo', '').strip()
        cedula_raw    = request.form.get('cedula', '').strip()
        edad          = request.form.get('edad', '').strip() or None
        telefono      = request.form.get('numero_telefono', '').strip() or None
        direccion     = request.form.get('direccion_completa', '').strip() or None
        modalidad     = request.form.get('modalidad_trabajo_preferida', '').strip() or None
        rutas         = request.form.get('rutas_cercanas', '').strip() or None
        empleo_ant    = request.form.get('empleo_anterior', '').strip() or None
        anos_exp      = request.form.get('anos_experiencia', '').strip() or None
        areas_exp     = request.form.get('areas_experiencia', '').strip() or None
        plancha       = True if request.form.get('sabe_planchar') == 'si' else False
        ref_laboral   = request.form.get('contactos_referencias_laborales', '').strip() or None
        ref_familia   = request.form.get('referencias_familiares_detalle', '').strip() or None
        raw_pct = request.form.get('acepta_porcentaje_sueldo')
        pct_acepta = True if raw_pct == 'si' else False


        # 2) Normalizamos algunos
        nombre = normalize_nombre(nombre_raw)
        cedula = normalize_cedula(cedula_raw)
        if not cedula:
            flash('Cédula inválida. Debe tener 11 dígitos.', 'danger')
            return redirect(url_for('register'))

        # 3) Validaciones mínimas
        if not nombre:
            flash('El nombre es obligatorio.', 'danger')
            return redirect(url_for('register'))

        # 4) Chequear duplicados
        if Candidata.query.filter_by(cedula=cedula).first():
            flash('Ya existe una candidata con esa cédula.', 'danger')
            return redirect(url_for('register'))

        # 5) Crear objeto
        nueva = Candidata(
            nombre_completo                = nombre,
            cedula                         = cedula,
            edad                           = edad,
            numero_telefono                = telefono,
            direccion_completa             = direccion,
            modalidad_trabajo_preferida    = modalidad,
            rutas_cercanas                 = rutas,
            empleo_anterior                = empleo_ant,
            anos_experiencia               = anos_exp,
            areas_experiencia              = areas_exp,
            sabe_planchar                  = plancha,
            contactos_referencias_laborales= ref_laboral,
            referencias_familiares_detalle = ref_familia,
            acepta_porcentaje_sueldo       = pct_acepta
        )

        # 6) Guardar en la base
        try:
            db.session.add(nueva)
            db.session.commit()
            flash('Candidata registrada ✅', 'success')
            return redirect(url_for('register'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar: {e}', 'danger')
            return redirect(url_for('register'))

    # GET → mostramos el formulario
    return render_template('register.html')



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)