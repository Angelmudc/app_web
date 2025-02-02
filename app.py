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


# Configuración de la API de Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '1J8cPXScpOCywiJHspSntCo3zPLf7FCOli6vsgSWLNOg'
RANGE_NAME = 'Nueva hoja!A1:Y'

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

import unicodedata

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

# Función para buscar datos por código, nombre o cédula
def buscar_en_columna(valor, columna_index):
    """
    Busca un valor dentro de una columna específica sin ser estricto.
    - No distingue mayúsculas y minúsculas.
    - Ignora espacios en blanco antes y después.
    - Devuelve todas las coincidencias.
    """
    valor_normalizado = valor.strip().lower()  # Convierte todo a minúsculas y elimina espacios

    datos = obtener_datos_pagos()

    resultados = []
    for fila in datos:
        if len(fila) > columna_index:  # Evita errores si la fila tiene menos columnas
            if valor_normalizado in fila[columna_index].strip().lower():
                resultados.append(fila)

    return resultados  # Devuelve todas las coincidencias encontradas

def obtener_datos_pagos():
    """
    Obtiene solo las columnas necesarias para gestionar pagos en la hoja de cálculo.
    Columnas:
    - P: Código
    - B: Nombre
    - U: Fecha de Pago
    - V: Fecha de Inicio del Trabajo
    - W: Monto Total
    - X: Porcentaje (25%)
    - Y: Calificación de Pago
    """

    try:
        hoja = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!P:Y"  # Obtiene solo las columnas necesarias
        ).execute()

        datos = hoja.get("values", [])

        # Asegurar que todas las filas tienen el mismo número de columnas
        for fila in datos:
            while len(fila) < 12:  # Se ajusta al número de columnas esperadas
                fila.append("")  # Se rellenan los vacíos para evitar errores

        return datos

    except Exception as e:
        print(f"⚠️ Error al obtener datos de pagos: {e}")
        return []

def actualizar_datos_pagos(fila_index, nuevos_datos):
    """
    Actualiza solo las columnas específicas de pagos en la hoja de cálculo.
    Columnas afectadas:
    - U: Fecha de Pago
    - W: Monto Total
    - X: Porcentaje (25%)
    """
    try:
        rango = f" Nueva hoja!U{fila_index + 1}:Y{fila_index + 1}"

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="USER_ENTERED",
            body={"values": [nuevos_datos]}
        ).execute()

        print(f"✅ Datos de pago actualizados en fila {fila_index}")

    except Exception as e:
        print(f"⚠️ Error al actualizar datos de pago: {e}")


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
            range="Nueva hoja!A:Y"  # 🔹 Solo columnas B-R (Nombre - Inscripción)
        ).execute()
        return hoja.get("values", [])
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
    Permite trabajar con filas incompletas (sin inscripción, monto o fecha).
    """
    try:
        # 🔹 Buscar primero por Nombre (Columna B, índice 1)
        fila_index, fila = buscar_en_columna(buscar, 1)  

        if not fila:
            # 🔹 Si no se encontró por Nombre, buscar por Cédula (Columna O, índice 14)
            fila_index, fila = buscar_en_columna(buscar, 14)

        if fila:
            # Asegurar que la fila tenga las columnas necesarias
            while len(fila) < 23:  # Completa con valores vacíos hasta la columna W
                fila.append("")

            return {
                'fila_index': fila_index + 1,  # Índice de fila (1-based index)
                'codigo': fila[15],  # Código (P)
                'nombre': fila[1],  # Nombre (B)
                'cedula': fila[14],  # Cédula (O)
                'estado': fila[15],  # Estado (P)
                'inscripcion': fila[16],  # Inscripción (Q)
                'monto': fila[18],  # Monto (R)
                'fecha': fila[19]  # Fecha de Pago (S)
            }
        return None  # Si no se encuentran resultados, devuelve None
    except Exception as e:
        print(f"Error al buscar datos: {e}")
        return None

def inscribir_candidata(fila_index, cedula, estado, monto, fecha_inscripcion):
    """
    Inscribe una candidata solo si la columna Código (P) está vacía y asigna un código único.
    """
    try:
        # Obtener los datos actuales
        datos = obtener_datos_editar()
        fila = datos[fila_index - 1]  # Ajustar índice

        # Si la columna Código (P) ya tiene un valor, no hacer nada
        if len(fila) > 15 and fila[15].strip():
            return "La candidata ya tiene un código asignado."

        # Generar código único solo si la columna P (Código) está vacía
        codigo = generar_codigo_unico()

        # Asegurar que la fila tenga al menos hasta la columna Y
        while len(fila) < 25:
            fila.append("")

        # Actualizar los valores en las columnas correctas
        fila[15] = codigo  # *Código (P)*
        fila[16] = estado  # *Estado (Q)*
        fila[17] = "Sí"  # *Inscripción (R)*
        fila[18] = monto  # *Monto (S)*
        fila[19] = fecha_inscripcion  # *Fecha de inscripción (T)*

        # Definir el rango y actualizar en la hoja
        rango = f"Nueva hoja!P{fila_index}:Y{fila_index}"
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [fila[15:25]]}  # Solo enviar las columnas de P a Y
        ).execute()

        return f"Candidata inscrita con código {codigo}."
    except Exception as e:
        print(f"Error al inscribir candidata: {e}")
        return "Error al inscribir candidata."

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

def actualizar_pago(fila_index, fecha_inicio, monto_total):
    """
    Actualiza la información de pago en la hoja de cálculo.
    """
    try:
        fecha_pago = calcular_fecha_pago(fecha_inicio)
        porcentaje = calcular_porcentaje(monto_total)

        valores = [[fecha_pago, fecha_inicio, monto_total, porcentaje, "Pendiente"]]

        rango = f"Nueva hoja!U{fila_index}:Y{fila_index}"  # Columnas U - Y
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()

        print(f"Pago actualizado en fila {fila_index}. Fecha Pago: {fecha_pago}, Porcentaje: {porcentaje}")
        return True
    except Exception as e:
        print(f"Error al actualizar el pago: {e}")
        return False

def calcular_porcentaje():
    """
    Calcula el porcentaje de pago (25%) y establece la fecha de pago según la lógica establecida.
    - Si la fecha de inicio es entre el 5 y 15 → Fecha de pago será el 30.
    - Si la fecha de inicio es después del 15 → Fecha de pago será el 15 del mes siguiente.
    """
    try:
        codigo = request.form.get('codigo')
        monto_total = request.form.get('monto_total')
        fecha_inicio = request.form.get('fecha_inicio')

        if not codigo or not monto_total or not fecha_inicio:
            return jsonify({"error": "Todos los campos son obligatorios"}), 400

        # Calcular porcentaje (25%)
        monto_total = float(monto_total)
        porcentaje = round(monto_total * 0.25, 2)

        # Calcular la fecha de pago
        fecha_inicio_dt = datetime.strptime(fecha_inicio, "%Y-%m-%d")
        dia_inicio = fecha_inicio_dt.day

        if 5 <= dia_inicio <= 15:
            fecha_pago = fecha_inicio_dt.replace(day=30)
        else:
            mes_siguiente = fecha_inicio_dt.month + 1 if fecha_inicio_dt.month < 12 else 1
            año_siguiente = fecha_inicio_dt.year if mes_siguiente > 1 else fecha_inicio_dt.year + 1
            fecha_pago = datetime(año_siguiente, mes_siguiente, 15)

        fecha_pago_str = fecha_pago.strftime("%Y-%m-%d")

        return jsonify({
            "codigo": codigo,
            "monto_total": monto_total,
            "porcentaje": porcentaje,
            "fecha_pago": fecha_pago_str
        })

    except Exception as e:
        print(f"⚠️ Error en cálculo de porcentaje: {e}")
        return jsonify({"error": "Error interno en el cálculo"}), 500

def calcular_fecha_pago(fecha_inicio):
    """
    Calcula la fecha de pago basada en la fecha de inicio.
    - Si empieza entre el día 5 y 15 → el pago será el día 30 del mismo mes.
    - Si empieza desde el 20 en adelante → el pago será el día 15 del mes siguiente.
    """
    try:
        fecha = datetime.strptime(fecha_inicio, "%Y-%m-%d")
        dia = fecha.day

        if 5 <= dia <= 15:
            fecha_pago = fecha.replace(day=30)
        elif dia >= 20:
            mes_siguiente = fecha.month + 1 if fecha.month < 12 else 1
            año_siguiente = fecha.year if fecha.month < 12 else fecha.year + 1
            fecha_pago = fecha.replace(day=15, month=mes_siguiente, year=año_siguiente)
        else:
            fecha_pago = fecha  # Si no entra en esos rangos, se queda igual

        return fecha_pago.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error en calcular_fecha_pago: {e}")
        return ""   


# Función para actualizar datos en la hoja
def actualizar_dato_en_columna(fila_index, columna_index, nuevo_valor):
    """
    Actualiza un valor específico en una fila y columna dadas.
    """
    try:
        if fila_index < 0 or columna_index < 0:
            raise ValueError("Índices de fila o columna no válidos.")

        rango = f"Nueva hoja!{chr(65 + columna_index)}{fila_index + 1}"  # Convierte índice en letra de columna
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [[nuevo_valor]]}
        ).execute()
        print(f"Valor actualizado en fila {fila_index + 1}, columna {columna_index + 1}: {nuevo_valor}")
        return True
    except Exception as e:
        print(f"Error al actualizar valor: {e}")
        return False



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
    Busca candidatas por Nombre (Columna B) o Cédula (Columna R).
    Permite trabajar con filas incompletas (sin inscripción, monto o fecha).
    """
    try:
        # 🔹 Buscar primero por Nombre (Columna B, índice 1)
        fila_index, fila = buscar_en_columna(buscar, 1)

        if not fila:
            # Si no se encontró por Nombre, buscar por Cédula (Columna R, índice 17)
            fila_index, fila = buscar_en_columna(buscar, 17)

        if fila:
            # Asegurar que la fila tenga las columnas necesarias
            while len(fila) < 25:  # Ajustar hasta la última columna necesaria
                fila.append("")

            return {
                'fila_index': fila_index + 1,  # Índice de fila (1-based index)
                'codigo': fila[15],      # Código (P)
                'nombre': fila[1],       # Nombre (B)
                'cedula': fila[14],      # Cédula (R)
                'estado': fila[18],      # Estado (S)
                'inscripcion': fila[19], # Inscripción (T)
                'experiencia': fila[9],  # Áreas de experiencia (J)
                'monto': fila[20],       # Monto (U)
                'fecha_pago': fila[21],   # Fecha de Pago (V)
            }

        return None  # Si no se encuentran resultados, devuelve None
    except Exception as e:
        print(f"Error al buscar datos: {e}")
        return None

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
    """
    Ruta para inscribir candidatas.
    - 🔹 Busca candidatas por Nombre o Cédula.
    - 🔹 Si se encuentra, permite inscribirlas y asigna un código único si no tienen.
    - 🔹 Actualiza los datos en la hoja de cálculo.
    """
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'buscar':
            buscar = request.form.get('buscar', '').strip()
            
            # 🔹 Buscar en la hoja de cálculo (solo por Nombre o Cédula)
            datos_candidata = buscar_datos_inscripcion(buscar)

            if not datos_candidata:
                mensaje = "No se encontraron resultados para el nombre o cédula proporcionados."

        elif accion == 'guardar':
            try:
                fila_index = int(request.form.get('fila_index', -1))  # Índice de fila (1-based index)
                cedula = request.form.get('cedula', '').strip()
                estado = request.form.get('estado', '').strip()
                monto = request.form.get('monto', '').strip()
                fecha = request.form.get('fecha', '').strip()

                if fila_index == -1:
                    mensaje = "Error: No se pudo determinar la fila a actualizar."
                else:
                    # 🔹 Llamar a la función que inscribe y actualiza los datos
                    mensaje = inscribir_candidata(fila_index, cedula, estado, monto, fecha)

            except Exception as e:
                mensaje = f"Error al guardar los datos: {str(e)}"

    return render_template('inscripcion.html', mensaje=mensaje, datos_candidata=datos_candidata)


### 📌 RUTA PARA RENDERIZAR EL HTML DE PORCENTAJE
@app.route("/porciento", methods=["GET"])
def porciento():
    return render_template("porciento.html")


### 📌 FUNCIÓN PARA BUSCAR CANDIDATAS (FLEXIBLE)
def buscar_candidata(valor):
    try:
        data = sheet.get_all_records()
        for fila in data:
            if valor.lower() in str(fila["Código"]).lower() or valor.lower() in str(fila["Nombre"]).lower() or valor.lower() in str(fila["Cédula"]).lower():
                return {
                    "codigo": fila["Código"],
                    "nombre": fila["Nombre"],
                    "cedula": fila["Cédula"],
                    "telefono": fila["Teléfono"],
                    "ciudad": fila["Ciudad"]
                }
        return None
    except Exception as e:
        print(f"Error al buscar candidata: {e}")
        return None


### 📌 API PARA BUSCAR CANDIDATAS
@app.route("/buscar_candidata", methods=["GET"])
def buscar_candidata_api():
    valor = request.args.get("valor", "").strip()
    if not valor:
        return jsonify({"error": "Debe ingresar un valor"}), 400

    candidata = buscar_candidata(valor)
    if candidata:
        return jsonify(candidata)
    else:
        return jsonify({"error": "No se encontraron resultados"})


### 📌 API PARA CALCULAR EL PORCENTAJE Y FECHA DE PAGO
@app.route("/calcular_porcentaje", methods=["POST"])
def calcular_porcentaje():
    try:
        datos = request.json
        monto_total = float(datos.get("monto_total", 0))
        fecha_inicio = datos.get("fecha_inicio", "")

        if not monto_total or not fecha_inicio:
            return jsonify({"error": "Todos los campos son obligatorios"}), 400

        # Calcular el 25% del monto total
        porcentaje = round(monto_total * 0.25, 2)

        # Calcular la fecha de pago
        fecha_inicio_dt = datetime.datetime.strptime(fecha_inicio, "%Y-%m-%d")
        dia_inicio = fecha_inicio_dt.day

        if 5 <= dia_inicio <= 15:
            fecha_pago = fecha_inicio_dt.replace(day=30)
        else:
            mes_siguiente = fecha_inicio_dt.month + 1 if fecha_inicio_dt.month < 12 else 1
            año_siguiente = fecha_inicio_dt.year if mes_siguiente > 1 else fecha_inicio_dt.year + 1
            fecha_pago = fecha_inicio_dt.replace(month=mes_siguiente, year=año_siguiente, day=15)

        fecha_pago_str = fecha_pago.strftime("%Y-%m-%d")

        return jsonify({"porcentaje": porcentaje, "fecha_pago": fecha_pago_str})

    except Exception as e:
        print(f"Error al calcular porcentaje: {e}")
        return jsonify({"error": "Error en el cálculo"}), 500


### 📌 API PARA GUARDAR EL PAGO
@app.route("/guardar_pago", methods=["POST"])
def guardar_pago():
    try:
        datos = request.json
        codigo = datos.get("codigo", "").strip()
        monto_total = datos.get("monto_total", "").strip()
        porcentaje = datos.get("porcentaje", "").strip()
        fecha_pago = datos.get("fecha_pago", "").strip()
        fecha_inicio = datos.get("fecha_inicio", "").strip()

        if not codigo or not monto_total or not porcentaje or not fecha_pago or not fecha_inicio:
            return jsonify({"error": "Debe completar todos los campos"}), 400

        # Buscar fila por código
        data = sheet.get_all_records()
        fila_index = None
        for index, fila in enumerate(data):
            if str(fila["Código"]).strip().lower() == codigo.lower():
                fila_index = index + 2  # Ajuste por encabezados en Google Sheets
                break

        if fila_index is None:
            return jsonify({"error": "Candidata no encontrada"}), 404

        # Actualizar los datos en la hoja
        sheet.update(f"U{fila_index}", fecha_pago)  # Fecha de Pago
        sheet.update(f"V{fila_index}", fecha_inicio)  # Fecha de Inicio
        sheet.update(f"W{fila_index}", monto_total)  # Monto Total
        sheet.update(f"X{fila_index}", porcentaje)  # Porcentaje

        return jsonify({"mensaje": "Pago registrado correctamente"}), 200

    except Exception as e:
        print(f"Error al guardar pago: {e}")
        return jsonify({"error": "Error al guardar los datos"}), 500


@app.route('/pagos', methods=['GET', 'POST'])
def gestionar_pagos():
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        if 'buscar_btn' in request.form:
            buscar = request.form.get('buscar', '').strip()
            datos = obtener_datos_pagos()
            for fila_index, fila in enumerate(datos):
                if len(fila) > 24 and (buscar in fila[15] or buscar in fila[1] or buscar in fila[14]):  
                    datos_candidata = {
                        'fila_index': fila_index + 1,
                        'codigo': fila[15],  # Código
                        'nombre': fila[1],  # Nombre
                        'monto_total': fila[22] if len(fila) > 22 else "0",
                        'fecha_inicio': fila[21] if len(fila) > 21 else "",
                        'porcentaje': fila[23] if len(fila) > 23 else "",
                        'fecha_pago': fila[20] if len(fila) > 20 else "",
                        'calificacion': fila[24] if len(fila) > 24 else "Pendiente",
                    }
                    break
            if not datos_candidata:
                mensaje = "No se encontraron resultados."

        elif 'guardar_btn' in request.form:
            try:
                fila_index = int(request.form.get('fila_index', -1))
                fecha_inicio = request.form.get('fecha_inicio', '').strip()
                monto_total = float(request.form.get('monto_total', 0))

                if fila_index == -1 or not fecha_inicio:
                    mensaje = "Error: Faltan datos."
                else:
                    actualizar_pago(fila_index, fecha_inicio, monto_total)
                    mensaje = "Pago registrado correctamente."

            except Exception as e:
                mensaje = f"Error al guardar datos: {str(e)}"

    return render_template('pagos.html', mensaje=mensaje, datos_candidata=datos_candidata)

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