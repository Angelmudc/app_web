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



# Configuración de la API de Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '1J8cPXScpOCywiJHspSntCo3zPLf7FCOli6vsgSWLNOg'
RANGE_NAME = 'Nueva hoja!A1:Z'

# Cargar credenciales desde la variable de entorno
clave1 = json.loads(os.environ.get("CLAVE1_JSON"))
credentials = Credentials.from_service_account_info(clave1, scopes=SCOPES)
service = build('sheets', 'v4', credentials=credentials)

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

def actualizar_datos_editar(fila_index, nuevos_datos):
    """
    Actualiza solo las columnas específicas para la edición en la hoja de cálculo.
    """
    try:
        columnas = {
            "nombre": "B",
            "edad": "C",
            "telefono": "D",
            "direccion": "E",
            "modalidad": "F",
            "experiencia": "J",
            "cedula": "O",
            "estado": "Q",
        }

        for campo, valor in nuevos_datos.items():
            if campo in columnas:  # Asegurar que el campo está en la lista
                rango = f"Nueva hoja!{columnas[campo]}{fila_index}"
                print(f"🔹 Actualizando {campo} en {rango} con valor '{valor}'")  # Debug
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

def obtener_datos_referencias():
    """
    Obtiene solo las columnas necesarias de la hoja de cálculo para referencias.
    """
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, 
            range="Nueva hoja!A:Q"  # 🔹 Solo columnas relevantes
        ).execute()
        return hoja.get('values', [])
    except Exception as e:
        print(f"Error al obtener datos de referencias: {e}")
        return []

def obtener_datos_editar():
    """
    Obtiene solo las columnas necesarias para editar candidatas.
    """
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, 
            range="Nueva hoja!A:Y"  # 🔹 Solo columnas necesarias
        ).execute()
        valores = hoja.get("values", [])

        # Asegurar que cada fila tenga al menos 25 columnas
        datos_completos = [fila + [''] * (25 - len(fila)) if len(fila) < 25 else fila for fila in valores]

        return datos_completos
    except Exception as e:
        print(f"Error al obtener datos de edición: {e}")
        return []

def actualizar_referencias(fila_index, laborales, familiares):
    """
    Actualiza las referencias laborales y familiares en la hoja de cálculo.
    """
    try:
        rango = f"Nueva hoja!L{fila_index}:M{fila_index}"  # 🔹 Columnas L y M
        valores = [[laborales, familiares]]

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
        
        return True
    except Exception as e:
        print(f"Error al actualizar referencias: {e}")
        return False

def buscar_datos_inscripcion(buscar):
    """
    Busca candidatas por Nombre (Columna B) o Cédula (Columna O).
    """
    try:
        datos = obtener_datos_editar()

        for fila_index, fila in enumerate(datos):
            if len(fila) < 15:  # Evitar errores si la fila tiene menos columnas
                continue 

            nombre = normalizar_texto(fila[1]) if len(fila) > 1 else ""
            cedula = fila[14].strip() if len(fila) > 14 else ""

            if buscar.lower() in nombre or buscar == cedula:
                while len(fila) < 25:
                    fila.append("")

                return {
                    'fila_index': fila_index + 1,  # Índice de fila correcto
                    'codigo': fila[15] if len(fila) > 15 else "SIN CÓDIGO",  # Código (P)
                    'nombre': fila[1],
                    'cedula': fila[14] if len(fila) > 14 else "SIN CÉDULA",
                    'telefono': fila[3] if len(fila) > 3 else "SIN TELÉFONO",
                    'estado': fila[16] if len(fila) > 16 else "SIN ESTADO",
                    'inscripcion': fila[17] if len(fila) > 17 else "NO INSCRITA",
                    'monto': fila[18] if len(fila) > 18 else "0",
                    'fecha': fila[19] if len(fila) > 19 else ""
                }
        return None
    except Exception as e:
        print(f"❌ Error al buscar datos en inscripción: {e}")
        return None

def inscribir_candidata(fila_index, estado, monto, fecha):
    """
    Inscribe una candidata actualizando las columnas necesarias.
    - 🔹 Código (P) → Se asigna si está vacío.
    - 🔹 Estado (Q) → Se actualiza con el valor ingresado.
    - 🔹 Inscripción (R) → Se marca como 'Sí'.
    - 🔹 Monto (S) → Se actualiza con el valor ingresado.
    - 🔹 Fecha de Inscripción (T) → Se actualiza con el valor ingresado.
    """
    try:
        datos = obtener_datos_editar()
        fila = datos[fila_index - 1]  # Ajustar índice

        # Asegurar que la fila tenga suficiente espacio
        while len(fila) < 25:
            fila.append("")

        # Si el código está vacío, asignar un código único
        if not fila[15].strip():
            fila[15] = generar_codigo_unico()  # Código (P)

        fila[16] = estado  # Estado (Q)
        fila[17] = "Sí"  # Inscripción (R)
        fila[18] = monto  # Monto (S)
        fila[19] = fecha  # Fecha de inscripción (T)

        # Definir el rango y actualizar en la hoja
        rango = f"Nueva hoja!P{fila_index}:T{fila_index}"
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [fila[15:20]]}  # Solo enviar las columnas de P a T
        ).execute()

        return True
    except Exception as e:
        print(f"❌ Error al inscribir candidata: {e}")
        return False

def obtener_datos_filtrar():
    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, 
            range="Nueva hoja!A:Y"  # Ajusta el rango si es necesario
        ).execute()
        valores = hoja.get("values", [])
        
        if not valores:
            print("⚠️ No se obtuvieron datos de Google Sheets.")
        else:
            print(f"✅ Datos obtenidos ({len(valores)} filas).")

        return valores
    except Exception as e:
        print(f"❌ Error al obtener datos para filtrar: {e}")
        return []


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

            # Lista de campos a evaluar
            campos_a_buscar = [
                codigo, nombre, cedula, telefono, direccion, estado, 
                inscripcion, modalidad, experiencia, referencias_laborales, referencias_familiares
            ]

            # Coincidencia aproximada usando RapidFuzz
            resultado = process.extractOne(busqueda.lower(), campos_a_buscar)

            if resultado and resultado[1] > 80:  # Umbral de coincidencia 80%
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

def buscar_candidata(valor):
    datos = obtener_datos()
    for fila in datos:
        if len(fila) >= 27:  # Asegúrate de que la fila tenga suficientes columnas
            if (valor.lower() == fila[15].lower() or  # Código
                valor.lower() == fila[1].lower() or  # Nombre
                valor == fila[14]):  # Cédula
                return fila
    return None

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


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    resultados = []
    detalles_candidata = None
    mensaje = ""

    if request.method == 'POST':
        busqueda = request.form.get('busqueda', '').strip().lower()

        if not busqueda:
            mensaje = "Por favor, introduce un Código, Nombre, Cédula o Teléfono para buscar."
        else:
            resultados = buscar_candidata_rapida(busqueda)

            if not resultados:
                mensaje = f"No se encontraron resultados para: {busqueda}"

        if 'seleccionar_btn' in request.form:
            fila_index = int(request.form.get('fila_index')) - 1
            datos = obtener_datos_cache()

            if 0 <= fila_index < len(datos):
                fila = datos[fila_index]

                # Verificar que la fila tenga al menos la cantidad de columnas esperadas
                while len(fila) < 25:  # Asegurarnos de que haya suficientes columnas
                    fila.append("")

                detalles_candidata = {
                    'codigo': fila[15],  # Código (Columna P)
                    'nombre': fila[1],   # Nombre (Columna B)
                    'edad': fila[2],     # Edad (Columna C)
                    'ciudad': fila[4],   # Ciudad (Columna E)
                    'cedula': fila[14],  # Cédula (Columna R)
                    'telefono': fila[3], # Teléfono (Columna D)
                    'referencias_laborales': fila[11] if len(fila) > 11 else "No disponible",
                    'referencias_familiares': fila[12] if len(fila) > 12 else "No disponible",
                    'modalidad': fila[5] if len(fila) > 5 else "No disponible",
                    'experiencia': fila[9] if len(fila) > 9 else "No disponible",
                    'planchar': fila[10] if len(fila) > 10 else "No disponible",
                    'porcentaje': fila[23] if len(fila) > 23 else "No disponible",  # Inscripción (Columna R)
                    'estado': fila[16] if len(fila) > 16 else "No disponible"  # Estado (Columna Q)
                }

    return render_template(
        'buscar.html',
        resultados=resultados,
        detalles_candidata=detalles_candidata,
        mensaje=mensaje
    )

@app.route('/sugerir')
def sugerir():
    query = request.args.get('busqueda', '')
    if not query:
        return jsonify([])

    # Aquí deberías obtener los datos de la cache o de la base de datos
    datos_filtrados = [dato for dato in lista_candidatas if query.lower() in dato['nombre'].lower()]
    
    return jsonify(datos_filtrados)

@app.route("/editar", methods=["GET", "POST"])
def editar():
    datos_candidata = None
    mensaje = ""

    if request.method == "POST":
        if "buscar_btn" in request.form:
            busqueda = request.form.get("busqueda", "").strip().lower()
            fila_index = None
            datos = obtener_datos_editar()

            # 🔹 DEBUG: Imprime todas las filas para verificar qué datos están llegando
            print("🔹 Datos obtenidos de la hoja:")
            for fila in datos:
                print(fila)

            # 🔹 Buscar por nombre o cédula con normalización
            for index, fila in enumerate(datos):
                if len(fila) > 14:  # Asegurar que haya suficientes columnas
                    nombre = normalizar_texto(fila[1])  # Nombre (Columna B)
                    cedula = fila[14].strip()  # Cédula (Columna O)

                    # 🔹 DEBUG: Mostrar cómo se están comparando los datos
                    print(f"Comparando: '{busqueda}' con Nombre: '{nombre}', Cédula: '{cedula}'")

                    # Permitir coincidencias flexibles
                    if busqueda in nombre or busqueda == cedula:
                        fila_index = index + 1  # Ajuste para índices 1-based
                        datos_candidata = {
                            'fila_index': fila_index,
                            'codigo': fila[15] if len(fila) > 15 else "",  # Código (Columna P)
                            'nombre': fila[1],
                            'edad': fila[2] if len(fila) > 2 else "",
                            'telefono': fila[3] if len(fila) > 3 else "",
                            'direccion': fila[4] if len(fila) > 4 else "",
                            'modalidad': fila[5] if len(fila) > 5 else "",
                            'experiencia': fila[9] if len(fila) > 9 else "",
                            'cedula': fila[14],
                            'estado': fila[18] if len(fila) > 18 else "",
                            'inscripcion': fila[17] if len(fila) > 17 else ""
                        }
                        break

            if not datos_candidata:
                mensaje = f"No se encontraron datos para: {busqueda}"
        
        elif "guardar" in request.form:
            try:
                fila_index = int(request.form.get("fila_index", -1))
                if fila_index < 1:
                    mensaje = "Error al determinar la fila para actualizar."
                else:
                    nuevos_datos = {
                        "nombre": request.form.get("nombre", "").strip(),
                        "edad": request.form.get("edad", "").strip(),
                        "telefono": request.form.get("telefono", "").strip(),
                        "direccion": request.form.get("direccion", "").strip(),
                        "modalidad": request.form.get("modalidad", "").strip(),
                        "experiencia": request.form.get("experiencia", "").strip(),
                        "cedula": request.form.get("cedula", "").strip(),
                        "estado": request.form.get("estado", "").strip(),
                        "inscripcion": request.form.get("inscripcion", "").strip()
                    }

                    if actualizar_datos_editar(fila_index, nuevos_datos):
                        mensaje = "Los datos se han actualizado correctamente."
                    else:
                        mensaje = "Error al actualizar los datos."

            except Exception as e:
                mensaje = f"Error al actualizar los datos: {str(e)}"

    return render_template(
        "editar.html",
        datos_candidata=datos_candidata,
        mensaje=mensaje
    )

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

@app.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion():
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'buscar':
            buscar = request.form.get('buscar', '').strip()
            datos_candidata = buscar_datos_inscripcion(buscar)

            if not datos_candidata:
                mensaje = "⚠️ No se encontraron resultados para la búsqueda."

        elif accion == 'guardar':
            try:
                fila_index = request.form.get('fila_index', '').strip()

                if not fila_index or not fila_index.isdigit():
                    mensaje = "❌ Error: No se pudo determinar la fila a actualizar."
                else:
                    fila_index = int(fila_index)
                    estado = request.form.get('estado', '').strip()
                    monto = request.form.get('monto', '').strip()
                    fecha = request.form.get('fecha', '').strip()

                    resultado = actualizar_inscripcion(fila_index, estado, monto, fecha)

                    if resultado:
                        mensaje = "✅ Datos actualizados correctamente."
                    else:
                        mensaje = "❌ Error al actualizar los datos."

            except Exception as e:
                mensaje = f"❌ Error inesperado: {str(e)}"

    return render_template(
        'inscripcion.html',
        mensaje=mensaje,
        datos_candidata=datos_candidata
    )

@app.route('/reporte_pagos', methods=['GET'])
def reporte_pagos():
    """
    Genera un reporte de todas las candidatas con pagos pendientes.
    """
    try:
        # Obtener todos los datos de la hoja de cálculo
        datos = obtener_datos()
        reporte = []

        # Filtrar candidatas con Porciento > 0
        for fila in datos:
            try:
                # Imprimir toda la fila para verificar si contiene la columna Fecha_pago
                print(f"Fila completa: {fila}")

                if len(fila) >= 27:  # Asegurarse de que la fila tenga suficientes columnas
                    nombre = fila[1]  # Columna B: Nombre
                    porciento_pendiente = float(fila[25]) if fila[25] else 0.0  # Columna Z: Porciento
                    fecha_inicio = fila[23]  # Columna X: Inicio
                    fecha_pago = fila[22] if len(fila) > 22 and fila[22] else "Fecha no disponible"  # Columna W: Fecha de Pago
                    calificacion = fila[26] if len(fila) > 26 else "Pendiente"  # Columna AA: Calificación

                    if porciento_pendiente > 0:  # Filtrar pagos pendientes
                        print(f"Nombre: {nombre}, Fecha_pago: {fecha_pago}")  # Depuración
                        reporte.append({
                            'nombre': nombre,
                            'porciento_pendiente': porciento_pendiente,
                            'fecha_inicio': fecha_inicio,
                            'fecha_pago': fecha_pago,
                            'calificacion': calificacion
                        })
            except Exception as e:
                print(f"Error procesando fila: {fila}. Error: {e}")

        # Renderizar la plantilla con el reporte
        if not reporte:
            print("No hay candidatas con pagos pendientes.")
        return render_template('reporte_pagos.html', reporte=reporte)

    except Exception as e:
        print(f"Error al generar el reporte: {e}")
        return "Error al generar el reporte", 500

@app.route('/referencias', methods=['GET', 'POST'])
def referencias():
    """
    Busca y edita las referencias laborales y familiares de una candidata.
    Se busca solo por Nombre o Cédula.
    """
    datos_candidata = None
    mensaje = ""

    if request.method == 'POST':
        busqueda = request.form.get('busqueda', '').strip()

        if not busqueda:
            mensaje = "Por favor, introduce un Nombre o Cédula para buscar."
        else:
            # 🔹 Obtener datos solo de las columnas necesarias
            datos = obtener_datos_referencias()

            # 🔹 DEBUG: Imprimir datos obtenidos
            print("🔹 Datos obtenidos de la hoja:")
            for fila in datos:
                print(fila)

            busqueda_normalizada = normalizar_texto(busqueda)  # 🔹 Normalizamos la búsqueda

            for index, fila in enumerate(datos):
                if len(fila) >= 15:  # Asegurar que tenga suficientes columnas
                    nombre_original = fila[1]  # Columna B (Nombre)
                    cedula = fila[14].strip() if len(fila) > 14 else ""  # Columna O (Cédula)

                    # 🔹 Normalizar el nombre para comparar correctamente
                    nombre_normalizado = normalizar_texto(nombre_original)

                    # 🔹 DEBUG: Mostrar la comparación exacta
                    print(f"Comparando búsqueda: '{busqueda_normalizada}' con Nombre: '{nombre_normalizado}', Cédula: '{cedula}'")

                    # 🔹 Permitir coincidencias más flexibles
                    if busqueda_normalizada in nombre_normalizado or busqueda == cedula:
                        datos_candidata = {
                            'fila_index': index + 1,  # 🔹 Índice 1-based
                            'nombre': nombre_original,       # Columna B (Nombre)
                            'cedula': fila[14],      # Columna O (Cédula)
                            'laborales': fila[11],   # Columna L (Referencias Laborales)
                            'familiares': fila[12]   # Columna M (Referencias Familiares)
                        }
                        print(f"✅ Candidata encontrada: {datos_candidata}")  # DEBUG
                        break
            
            if not datos_candidata:
                mensaje = f"No se encontraron resultados para: {busqueda}"

        # 🔹 Guardar cambios si se presiona el botón "guardar"
        if 'guardar_btn' in request.form:
            try:
                fila_index = int(request.form.get('fila_index', -1))
                laborales = request.form.get('laborales', '').strip()
                familiares = request.form.get('familiares', '').strip()

                if fila_index == -1:
                    mensaje = "Error: No se pudo determinar la fila a actualizar."
                else:
                    # 🔹 Llamar a la función para actualizar referencias
                    if actualizar_referencias(fila_index, laborales, familiares):
                        mensaje = "Referencias actualizadas correctamente."
                    else:
                        mensaje = "Error al actualizar referencias."
            except Exception as e:
                mensaje = f"Error al guardar las referencias: {str(e)}"

    return render_template('referencias.html', datos_candidata=datos_candidata, mensaje=mensaje)

if __name__ == "__main__":
    from waitress import serve
    import os
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))