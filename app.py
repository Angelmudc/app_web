from flask import Flask, render_template, request
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session
import os
import json
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_from_directory


# Configuración de la API de Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '1J8cPXScpOCywiJHspSntCo3zPLf7FCOli6vsgSWLNOg'
RANGE_NAME = 'Hoja de trabajo!A1:AA'

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

# Normaliza texto (elimina acentos, espacios y pasa a minúsculas)
def normalizar_texto(texto):
    texto = texto.strip().lower()
    return ''.join(c for c in texto if c.isalnum())

# Función para buscar datos por código, nombre o cédula
def buscar_en_columna(valor, columna_index):
    """
    Busca un valor específico en una columna y devuelve la fila correspondiente.
    :param valor: Valor a buscar.
    :param columna_index: Índice de la columna (0-based).
    :return: Índice de la fila y la fila completa si se encuentra; None si no.
    """
    datos = obtener_datos()
    for fila_index, fila in enumerate(datos):
        if len(fila) > columna_index and valor.lower() == fila[columna_index].strip().lower():
            return fila_index, fila
    return None, None

def obtener_datos():
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        valores = result.get('values', [])
        # Asegura que todas las filas tengan al menos 27 columnas
        datos_completos = [fila + [''] * (27 - len(fila)) for fila in valores]
        return datos_completos
    except Exception as e:
        print(f"Error al obtener datos: {e}")
        return []

# Función para buscar datos por nombre o cédula
def buscar_datos_por_nombre_o_cedula(busqueda):
    datos = obtener_datos()
    for fila_index, fila in enumerate(datos):
        if len(fila) > 16:  # Asegura que al menos hay 17 columnas
            if fila[1].strip().lower() == busqueda.lower() or fila[16].strip() == busqueda:
                return fila_index, fila
    return None, None

# Función para actualizar datos en la hoja
def actualizar_dato_en_columna(fila_index, columna_index, nuevo_valor):
    """
    Actualiza un valor específico en una fila y columna dadas.
    """
    try:
        if fila_index < 0 or columna_index < 0:
            raise ValueError("Índices de fila o columna no válidos.")

        rango = f"Hoja de trabajo!{chr(65 + columna_index)}{fila_index + 1}"  # Convierte índice en letra de columna
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

def calcular_porcentaje(monto_total, porcentaje=0.25):
    """
    Calcula el porcentaje de un monto total.
    :param monto_total: Monto total sobre el cual se calcula el porcentaje.
    :param porcentaje: Porcentaje a calcular (por defecto 25%).
    :return: Monto calculado como porcentaje.
    """
    try:
        return round(monto_total * porcentaje, 2)
    except Exception as e:
        print(f"Error al calcular el porcentaje: {e}")
        return 0.0

def buscar_fila_por_codigo_nombre_cedula(busqueda):
    """
    Busca la fila en la hoja de cálculo por código, nombre o cédula.
    Retorna el índice de la fila y la fila completa si se encuentra.
    """
    datos = obtener_datos()
    for fila_index, fila in enumerate(datos):
        if len(fila) >= 27:  # Asegúrate de que la fila tenga suficientes columnas
            codigo = fila[0].strip().lower()
            nombre = fila[1].strip().lower()
            cedula = fila[17].strip()
            if (
                busqueda.lower() == codigo or
                busqueda.lower() == nombre or
                busqueda == cedula
            ):
                return fila_index, fila  # Devuelve el índice de la fila y la fila completa
    return None, None  # No se encontró

def actualizar_datos(fila_index, nuevos_datos):
    try:
        rango = f"Hoja de trabajo!A{fila_index + 1}:AA{fila_index + 1}"  # Actualizar solo en la fila buscada
        fila_actual = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango
        ).execute().get('values', [[]])[0]

        # Asegurarte de que la fila tenga suficientes columnas
        fila_actual.extend([''] * (27 - len(fila_actual)))  # Completa hasta 27 columnas

        # Actualizar solo las columnas relevantes
        valores = [
            fila_actual[0],  # Columna A: Código (No se modifica porque no es obligatorio)
            nuevos_datos.get("Nombre", fila_actual[1]),  # Columna B: Nombre
            nuevos_datos.get("Edad", fila_actual[2]),  # Columna C: Edad
            nuevos_datos.get("Telefono", fila_actual[3]),  # Columna D: Teléfono
            nuevos_datos.get("Direccion", fila_actual[4]),  # Columna E: Dirección
            nuevos_datos.get("Modalidad", fila_actual[5]),  # Columna F: Modalidad
            fila_actual[6],  # Columna G: (Sin cambios)
            fila_actual[7],  # Columna H: (Sin cambios)
            fila_actual[8],  # Columna I: (Sin cambios)
            nuevos_datos.get("Experiencia", fila_actual[9]),  # Columna J: Experiencia
            fila_actual[10],  # Columna K: (Sin cambios)
            fila_actual[11],  # Columna L: (Sin cambios)
            fila_actual[12],  # Columna M: (Sin cambios)
            fila_actual[13],  # Columna N: (Sin cambios)
            fila_actual[14],  # Columna O: (Sin cambios)
            fila_actual[15],  # Columna P: (Sin cambios)
            fila_actual[16],  # Columna Q: (Sin cambios)
            nuevos_datos.get("Cedula", fila_actual[17]),  # Columna R: Cédula
            nuevos_datos.get("Estado", fila_actual[18]),  # Columna S: Estado
            nuevos_datos.get("Inscripcion", fila_actual[19]),  # Columna T: Inscripción
            fila_actual[20],  # Columna U: (Sin cambios)
            fila_actual[21],  # Columna V: (Sin cambios)
            fila_actual[22],  # Columna W: (Sin cambios)
            fila_actual[23],  # Columna X: (Sin cambios)
            fila_actual[24],  # Columna Y: (Sin cambios)
            fila_actual[25],  # Columna Z: (Sin cambios)
            fila_actual[26]   # Columna AA: (Sin cambios)
        ]

        # Actualizar los valores en la hoja
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [valores]}
        ).execute()

        return True
    except Exception as e:
        print(f"Error al actualizar los datos: {e}")
        return False

def generar_codigo_unico():
    """
    Genera un código único para las candidatas en formato 'CAN-XXX',
    asegurándose de que no haya duplicados, incluso si el orden en la hoja no es secuencial.
    """
    datos = obtener_datos()  # Obtener todos los datos de la hoja de cálculo
    codigos_existentes = set(fila[0] for fila in datos if len(fila) > 0 and fila[0].startswith("CAN-"))
    
    # Busca el primer número no utilizado (permite huecos en la secuencia)
    numero = 1
    while True:
        nuevo_codigo = f"CAN-{str(numero).zfill(3)}"
        if nuevo_codigo not in codigos_existentes:  # Verifica si el código ya existe
            return nuevo_codigo
        numero += 1


# Función para guardar los datos en la hoja de cálculo
def guardar_datos_en_hoja():
    try:
        # Construimos la estructura de los datos a enviar
        rango = f"Hoja de trabajo!A1:AA"
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
            if (valor.lower() == fila[0].lower() or  # Código
                valor.lower() == fila[1].lower() or  # Nombre
                valor == fila[17]):  # Cédula
                return fila
    return None

# Ajuste en el manejo de datos
def procesar_fila(fila, fila_index):
    # Asegúrate de que la fila tenga el tamaño suficiente
    while len(fila) < 27:  # Asegúrate de tener al menos hasta la columna AA
        fila.append("")

    # Procesa los datos
    codigo = fila[0]
    nombre = fila[1]
    cedula = fila[17]
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

def calcular_porcentaje_y_guardar(codigo, monto_total):
    """
    Calcula el porcentaje basado en el monto total y actualiza los datos en la hoja de cálculo.
    """
    try:
        # Obtener los datos de la hoja de cálculo
        datos = obtener_datos()
        fila_index = -1

        # Buscar la fila correspondiente al código ingresado
        for index, fila in enumerate(datos):
            if len(fila) > 0 and fila[0].strip().lower() == codigo.strip().lower():
                fila_index = index
                break

        if fila_index == -1:
            return f"Error: No se encontró el código '{codigo}' en la hoja de cálculo."

        # Asegurar que la fila tenga al menos 27 columnas
        fila_actual = datos[fila_index]
        while len(fila_actual) < 27:
            fila_actual.append("")

        # Calcular el porcentaje
        porciento = round(monto_total * 0.20, 2)  # Calcula el 20% y lo redondea a 2 decimales
        fecha_pago = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")  # Fecha 15 días después
        calificacion = "Pendiente"  # Se puede ajustar según formulario

        # Actualizar valores en las columnas específicas
        fila_actual[24] = str(monto_total)  # Columna Y: Monto Total
        fila_actual[25] = str(porciento)  # Columna Z: Porciento
        fila_actual[22] = fecha_pago  # Columna W: Fecha de Pago
        fila_actual[26] = calificacion  # Columna AA: Calificación

        # Definir el rango de la fila para actualizar
        rango = f"Hoja de trabajo!A{fila_index + 1}:AA{fila_index + 1}"

        # Enviar los datos actualizados a la hoja de cálculo
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [fila_actual]}
        ).execute()

        print(f"Datos actualizados correctamente en la fila {fila_index + 1}.")
        return f"Porciento guardado correctamente para el código {codigo}."

    except Exception as e:
        print(f"Error en calcular_porcentaje_y_guardar: {e}")
        return f"Error al guardar el porciento para el código {codigo}."

def actualizar_datos_porciento(fila_index, monto_total, fecha_pago, porciento, calificacion):
    try:
        rango = f"Hoja de trabajo!A{fila_index + 1}:AA{fila_index + 1}"
        datos = obtener_datos()

        # Copiar la fila existente y actualizar los valores en las columnas específicas
        fila_actualizada = datos[fila_index]
        while len(fila_actualizada) < 27:  # Asegúrate de que la fila tenga al menos 27 columnas
            fila_actualizada.append("")

        # Actualiza las columnas específicas
        fila_actualizada[24] = str(monto_total)  # Columna Y: Monto Total
        fila_actualizada[25] = str(porciento)  # Columna Z: Porciento
        fila_actualizada[22] = fecha_pago  # Columna W: Fecha de Pago
        fila_actualizada[26] = calificacion  # Columna AA: Calificación

        # Actualiza la fila en la hoja de cálculo
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [fila_actualizada]}
        ).execute()

        print(f"Datos actualizados correctamente en la fila {fila_index + 1}")
        return True

    except Exception as e:
        print(f"Error al actualizar datos en la fila {fila_index + 1}: {str(e)}")
        return False

def prueba_actualizacion():
    try:
        rango = "Hoja de trabajo!A1"  # Cualquier celda para prueba
        valores = [["Prueba de conexión"]]
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
        print("Prueba de conexión exitosa.")
    except Exception as e:
        print(f"Error en la prueba de conexión: {e}")

def insertar_datos_en_hoja(fila_datos):
    """
    Inserta una fila de datos en la hoja de cálculo.
    
    :param fila_datos: Lista con los datos a insertar (en el orden de las columnas de la hoja).
    """
    try:
        # Especifica el rango donde se insertará (al final de la hoja)
        rango = "Hoja de trabajo!A:AA"  # Ajusta según el rango de tu hoja
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

def prueba_actualizacion_basica():
    try:
        rango = "Hoja de trabajo!A1:AA1"
        valores = [["Prueba de conexión", "123", "OK"] + [""] * 24]
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
        print("Prueba de actualización exitosa.")
    except Exception as e:
        print(f"Error en la prueba de actualización: {e}")

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
            codigo = normalizar_texto(fila[0])  # Columna A
            nombre = normalizar_texto(fila[1])  # Columna B
            cedula = fila[17]  # Columna R (sin normalizar)

            # Verifica si la búsqueda coincide de forma parcial
            if (
                busqueda in codigo or
                busqueda in nombre or
                busqueda in cedula
            ):
                resultados.append({
                    'codigo': fila[0],  # Columna A
                    'nombre': fila[1],  # Columna B
                    'edad': fila[2],    # Columna C
                    'telefono': fila[3],  # Columna D
                    'direccion': fila[4],  # Columna E
                    'modalidad': fila[5],  # Columna F
                    'experiencia': fila[9],  # Columna J
                    'cedula': fila[17],  # Columna R
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
    """
    Busca información de una candidata en la hoja de cálculo y permite seleccionar una candidata
    para ver sus detalles completos.
    """
    resultados = []  # Lista para almacenar los resultados de búsqueda
    detalles_candidata = None  # Datos completos de la candidata seleccionada
    mensaje = None

    if request.method == 'POST':
        if 'buscar_btn' in request.form:  # Botón para buscar
            busqueda = request.form.get('busqueda', '').strip().lower()

            if not busqueda:
                mensaje = "Por favor, introduce un Código, Nombre o Cédula para buscar."
            else:
                datos = obtener_datos()
                for fila_index, fila in enumerate(datos):
                    if len(fila) >= 27:  # Verifica que la fila tenga suficientes columnas
                        codigo = fila[0].strip().lower()
                        nombre = fila[1].strip().lower()
                        cedula = fila[17].strip().lower()

                        # Coincidencia parcial en Código, Nombre o Cédula
                        if (
                            busqueda in codigo or
                            busqueda in nombre or
                            busqueda in cedula
                        ):
                            resultados.append({
                                'fila_index': fila_index + 1,  # Índice 1-based
                                'codigo': fila[0],
                                'nombre': fila[1],
                                'telefono': fila[3],
                                'cedula': fila[17]
                            })

                if not resultados:
                    mensaje = f"No se encontraron resultados para: {busqueda}"

        elif 'seleccionar_btn' in request.form:  # Botón para seleccionar una candidata
            fila_index = int(request.form.get('fila_index')) - 1
            datos = obtener_datos()
            if 0 <= fila_index < len(datos):
                fila = datos[fila_index]
                detalles_candidata = {
                    'codigo': fila[0],
                    'nombre': fila[1],
                    'edad': fila[2],
                    'telefono': fila[3],
                    'direccion': fila[4],
                    'modalidad': fila[5],
                    'experiencia': fila[9],
                    'plancha': fila[10],
                    'cedula': fila[17],
                    'estado': fila[18],
                    'inscripcion': fila[19],
                    'monto': fila[20],
                    'fecha_inscripcion': fila[21],
                    'monto_total': fila[24],
                    'porciento': fila[25],
                    'calificacion': fila[26]
                }
            else:
                mensaje = "Error: La fila seleccionada no es válida."

    return render_template(
        'buscar.html',
        resultados=resultados,
        detalles_candidata=detalles_candidata,
        mensaje=mensaje
    )


@app.route("/editar", methods=["GET", "POST"])
def editar():
    """
    Permite buscar y actualizar la información de una candidata en la hoja de cálculo.
    """
    datos_candidata = None
    mensaje = ""

    if request.method == "POST":
        # Buscar candidata
        if "buscar" in request.form:  # Botón de buscar
            busqueda = request.form.get("codigo", "").strip().lower()
            datos = obtener_datos()

            for index, fila in enumerate(datos):
                if len(fila) >= 18:  # Asegurarse de que la fila tenga las columnas necesarias
                    codigo = fila[0].strip().lower()
                    nombre = fila[1].strip().lower()
                    cedula = fila[17].strip()

                    if (
                        busqueda == codigo or
                        busqueda == nombre or
                        busqueda == cedula
                    ):
                        # Mapear datos para enviarlos al HTML
                        datos_candidata = {
                            'codigo': fila[0],
                            'nombre': fila[1],
                            'edad': fila[2],
                            'telefono': fila[3],
                            'direccion': fila[4],
                            'modalidad': fila[5],
                            'experiencia': fila[9],
                            'cedula': fila[17],
                            'estado': fila[18],
                            'inscripcion': fila[19],
                        }
                        datos_candidata["fila_index"] = index + 1  # Índice 1-based para actualizar la fila
                        break

            if not datos_candidata:
                mensaje = "No se encontraron resultados para la búsqueda."

        # Guardar cambios
        elif "guardar" in request.form:  # Botón de guardar
            try:
                fila_index = int(request.form.get("fila_index", -1)) - 1  # Índice 0-based
                if fila_index < 0:
                    mensaje = "Error: Índice de fila no válido."
                else:
                    nuevos_datos = {
                        "codigo": request.form.get("codigo", "").strip(),
                        "nombre": request.form.get("nombre", "").strip(),
                        "edad": request.form.get("edad", "").strip(),
                        "telefono": request.form.get("telefono", "").strip(),
                        "direccion": request.form.get("direccion", "").strip(),
                        "modalidad": request.form.get("modalidad", "").strip(),
                        "experiencia": request.form.get("experiencia", "").strip(),
                        "cedula": request.form.get("cedula", "").strip(),
                        "estado": request.form.get("estado", "").strip(),
                        "inscripcion": request.form.get("inscripcion", "").strip(),
                    }

                    # Actualizar la fila correspondiente
                    if actualizar_datos(fila_index, nuevos_datos):
                        mensaje = "Los datos se han actualizado correctamente."
                    else:
                        mensaje = "Error al actualizar los datos."
            except Exception as e:
                mensaje = f"Error: {e}"

    return render_template("editar.html", datos_candidata=datos_candidata, mensaje=mensaje)


def actualizar_datos(fila_index, nuevos_datos):
    """
    Actualiza los datos de una fila específica en la hoja de cálculo.
    """
    try:
        rango = f"Hoja de trabajo!A{fila_index + 1}:AA{fila_index + 1}"  # Desde A hasta AA
        fila_actual = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=rango
        ).execute().get("values", [[]])[0]

        # Completar columnas vacías si es necesario
        fila_actual.extend([""] * (27 - len(fila_actual)))

        # Actualizar las columnas con los nuevos valores
        fila_actual[0] = nuevos_datos.get("codigo", fila_actual[0])  # Columna A
        fila_actual[1] = nuevos_datos.get("nombre", fila_actual[1])  # Columna B
        fila_actual[2] = nuevos_datos.get("edad", fila_actual[2])  # Columna C
        fila_actual[3] = nuevos_datos.get("telefono", fila_actual[3])  # Columna D
        fila_actual[4] = nuevos_datos.get("direccion", fila_actual[4])  # Columna E
        fila_actual[5] = nuevos_datos.get("modalidad", fila_actual[5])  # Columna F
        fila_actual[9] = nuevos_datos.get("experiencia", fila_actual[9])  # Columna J
        fila_actual[17] = nuevos_datos.get("cedula", fila_actual[17])  # Columna R
        fila_actual[18] = nuevos_datos.get("estado", fila_actual[18])  # Columna S
        fila_actual[19] = nuevos_datos.get("inscripcion", fila_actual[19])  # Columna T

        # Enviar los datos actualizados a la hoja
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rango,
            valueInputOption="RAW",
            body={"values": [fila_actual]}
        ).execute()

        return True
    except Exception as e:
        print(f"Error al actualizar datos: {e}")
        return False

@app.route('/filtrar', methods=['GET', 'POST'])
def filtrar():
    resultados = []
    mensaje = None  # Variable para mostrar mensajes

    if request.method == 'POST':
        ciudad = request.form.get('ciudad', '').strip().lower()
        modalidad = request.form.get('modalidad', '').strip().lower()
        experiencia_anos = request.form.get('experiencia_anos', '').strip().lower()
        areas_experiencia = request.form.get('areas_experiencia', '').strip().lower()

        # Obtiene los datos actualizados de la hoja
        datos = obtener_datos()

        # Itera sobre las filas y filtra según los criterios
        for fila in datos:
            if len(fila) < 11:  # Asegurar que la fila tenga al menos hasta la columna J
                continue

            # Normalizar valores de la fila
            ciudad_fila = fila[4].strip().lower()  # Columna E: Ciudad
            modalidad_fila = fila[5].strip().lower()  # Columna F: Modalidad de trabajo preferida
            experiencia_anos_fila = fila[8].strip().lower()  # Columna I: Años de experiencia laboral
            areas_experiencia_fila = fila[9].strip().lower()  # Columna J: Áreas de experiencia

            # Verificar si la fila cumple los criterios
            cumple_ciudad = not ciudad or ciudad in ciudad_fila
            cumple_modalidad = not modalidad or modalidad == modalidad_fila
            cumple_experiencia = not experiencia_anos or experiencia_anos == experiencia_anos_fila
            cumple_areas_experiencia = not areas_experiencia or areas_experiencia in areas_experiencia_fila

            if cumple_ciudad and cumple_modalidad and cumple_experiencia and cumple_areas_experiencia:
                resultados.append({
                    'codigo': fila[0],  # Columna A: Código
                    'nombre': fila[1],  # Columna B: Nombre
                    'edad': fila[2],  # Columna C: Edad
                    'telefono': fila[3],  # Columna D: Teléfono
                    'direccion': fila[4],  # Columna E: Dirección
                    'modalidad': fila[5],  # Columna F: Modalidad
                    'experiencia_anos': fila[8],  # Columna I: Años de experiencia laboral
                    'areas_experiencia': fila[9],  # Columna J: Áreas de experiencia
                    'cedula': fila[17],  # Columna R: Cédula
                })

        # Mensaje si no hay resultados
        if not resultados:
            mensaje = "No se encontraron resultados para los criterios seleccionados."

    return render_template('filtrar.html', resultados=resultados, mensaje=mensaje)
@app.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion():
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'buscar':
            buscar = request.form.get('buscar', '').strip()
            datos = obtener_datos()

            # Buscar por Nombre (B), Cédula (R) o Código (A)
            for index, fila in enumerate(datos):
                if len(fila) > 1 and (
                    buscar.lower() == fila[0].lower() or  # Código (A)
                    buscar.lower() == fila[1].lower() or  # Nombre (B)
                    buscar == fila[17]  # Cédula (R)
                ):
                    datos_candidata = {
                        'fila_index': index + 1,  # Índice de fila (1-based index)
                        'codigo': fila[0] if len(fila) > 0 else "",  # Código (A)
                        'nombre': fila[1],                           # Nombre (B)
                        'cedula': fila[17],                          # Cédula (R)
                        'estado': fila[18],                          # Estado (S)
                        'inscripcion': fila[19],                     # Inscripción (T)
                        'monto': fila[20],                           # Monto (U)
                        'fecha': fila[21]                            # Fecha (V)
                    }
                    break
            if not datos_candidata:
                mensaje = "No se encontraron resultados para el nombre, código o cédula proporcionados."

        elif accion == 'guardar':
            try:
                # Capturar los datos del formulario
                fila_index = int(request.form.get('fila_index', -1))  # Índice de fila (1-based index)
                cedula = request.form.get('cedula', '').strip()
                estado = request.form.get('estado', '').strip()
                monto = request.form.get('monto', '').strip()
                fecha = request.form.get('fecha', '').strip()

                if fila_index == -1:
                    mensaje = "Error: No se pudo determinar la fila a actualizar."
                else:
                    # Generar un código único si no existe
                    datos = obtener_datos()
                    fila = datos[fila_index - 1]
                    codigo = fila[0] if len(fila) > 0 and fila[0] else generar_codigo_unico()

                    # Actualizar los valores en la hoja
                    rango = f"Hoja de trabajo!A{fila_index}:V{fila_index}"  # Desde la columna A hasta V
                    valores = [
                        codigo,  # Código (A)
                        fila[1],  # Nombre (B) (Mantener el valor actual)
                        fila[2] if len(fila) > 2 else "",  # Edad (C)
                        fila[3] if len(fila) > 3 else "",  # Teléfono (D)
                        fila[4] if len(fila) > 4 else "",  # Dirección (E)
                        fila[5] if len(fila) > 5 else "",  # Modalidad (F)
                        "", "", "", "", "",  # Relleno hasta la columna R
                        "", "", "", "", "", "", 
                        cedula,  # Cédula (R)
                        estado,  # Estado (S)
                        "si",    # Inscripción (T)
                        monto,   # Monto (U)
                        fecha    # Fecha (V)
                    ]
                    service.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=rango,
                        valueInputOption="RAW",
                        body={"values": [valores]}
                    ).execute()
                    mensaje = f"Datos actualizados correctamente. Código asignado: {codigo}"
            except Exception as e:
                mensaje = f"Error al guardar los datos: {str(e)}"

    return render_template('inscripcion.html', mensaje=mensaje, datos_candidata=datos_candidata)


@app.route('/porciento', methods=['GET', 'POST'])
def porciento():
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        if 'buscar_btn' in request.form:  # Botón para buscar
            buscar = request.form.get('buscar', '').strip()
            fila_index, fila = buscar_en_columna(buscar, 0)  # Busca por código en columna A
            if fila:
                datos_candidata = {
                    'fila_index': fila_index + 1,  # Índice de fila en formato 1-based
                    'codigo': fila[0],
                    'nombre': fila[1],
                    'inicio': fila[23],
                    'monto_total': fila[24],
                    'porciento': fila[25],
                    'fecha_pago': fila[22]
                }
            else:
                mensaje = f"No se encontraron resultados para: {buscar}"

        elif 'guardar_btn' in request.form:  # Botón para guardar
            try:
                fila_index = int(request.form.get('fila_index', -1))
                monto_total = float(request.form.get('monto_total', 0))
                inicio = request.form.get('inicio', '').strip()

                if fila_index == -1 or not inicio:
                    mensaje = "Error: No se especificó correctamente la fila o la fecha de inicio."
                else:
                    porciento = calcular_porcentaje(monto_total)
                    fecha_pago = (datetime.strptime(inicio, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")

                    # Actualizar la fila correspondiente
                    actualizar_dato_en_columna(fila_index - 1, 22, fecha_pago)  # Fecha de Pago (Columna W)
                    actualizar_dato_en_columna(fila_index - 1, 23, inicio)  # Inicio (Columna X)
                    actualizar_dato_en_columna(fila_index - 1, 24, monto_total)  # Monto Total (Columna Y)
                    actualizar_dato_en_columna(fila_index - 1, 25, porciento)  # Porciento (Columna Z)

                    mensaje = f"Porciento calculado y guardado correctamente para la fila {fila_index}."
            except Exception as e:
                mensaje = f"Error al guardar los datos: {str(e)}"

    return render_template('porciento.html', mensaje=mensaje, datos_candidata=datos_candidata)

@app.route('/pagos', methods=['GET', 'POST'])
def gestionar_pagos():
    mensaje = ""
    datos_candidata = None

    if request.method == 'POST':
        if 'buscar_btn' in request.form:  # Botón para buscar
            buscar = request.form.get('buscar', '').strip()
            if not buscar:
                mensaje = "Por favor, introduce un Código, Nombre o Cédula para buscar."
            else:
                # Buscar datos en la hoja de cálculo
                for fila in obtener_datos():
                    if len(fila) > 17 and (
                        buscar.lower() == fila[0].lower() or  # Código
                        buscar.lower() == fila[1].lower() or  # Nombre
                        buscar == fila[17]  # Cédula
                    ):
                        datos_candidata = {
                            'codigo': fila[0],
                            'nombre': fila[1],
                            'porciento': float(fila[25]) if len(fila) > 25 else 0.0,  # Columna Z: Porciento
                            'calificacion': fila[26] if len(fila) > 26 else "Pendiente",  # Columna AA: Calificación
                        }
                        break
                if not datos_candidata:
                    mensaje = f"No se encontraron resultados para: {buscar}"

        elif 'guardar_btn' in request.form:  # Botón para guardar
            codigo = request.form.get('codigo', '').strip()
            pago = float(request.form.get('pago', 0))
            calificacion = request.form.get('calificacion', 'Pendiente').strip()

            if not codigo:
                mensaje = "Por favor, introduce un Código válido."
            elif pago <= 0:
                mensaje = "El pago debe ser mayor que 0."
            else:
                # Actualizar el porcentaje adeudado en la hoja
                datos = obtener_datos()
                for fila_index, fila in enumerate(datos):
                    if len(fila) > 0 and fila[0].strip() == codigo:
                        while len(fila) < 27:  # Asegurarse de que la fila tenga suficientes columnas
                            fila.append("")
                        porciento_actual = float(fila[25]) if len(fila) > 25 else 0.0
                        nuevo_porciento = max(0, porciento_actual - pago)  # Evitar valores negativos
                        fila[25] = str(nuevo_porciento)  # Actualizar Columna Z: Porciento
                        fila[26] = calificacion  # Actualizar Columna AA: Calificación

                        # Definir el rango y actualizar en la hoja
                        rango = f"Hoja de trabajo!A{fila_index + 1}:AA{fila_index + 1}"
                        service.spreadsheets().values().update(
                            spreadsheetId=SPREADSHEET_ID,
                            range=rango,
                            valueInputOption="RAW",
                            body={"values": [fila]}
                        ).execute()
                        mensaje = f"Pago registrado correctamente. Porcentaje restante: {nuevo_porciento:.2f}"
                        break
                else:
                    mensaje = "No se encontró el código para actualizar."

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
    Se busca por Código, Nombre o Cédula.
    """
    datos_candidata = None
    mensaje = ""

    if request.method == 'POST':
        # Captura el valor ingresado
        busqueda = request.form.get('busqueda', '').strip()

        if not busqueda:
            mensaje = "Por favor, introduce un Código, Nombre o Cédula para buscar."
        else:
            # Lógica para buscar en la hoja de cálculo
            datos = obtener_datos()
            for index, fila in enumerate(datos):
                # Asegurar que la fila tenga suficientes columnas
                if len(fila) >= 27:
                    codigo = fila[0].strip().lower()
                    nombre = fila[1].strip().lower()
                    cedula = fila[17].strip()

                    # Buscar por Código, Nombre o Cédula
                    if (
                        busqueda.lower() == codigo or
                        busqueda.lower() == nombre or
                        busqueda == cedula
                    ):
                        datos_candidata = {
                            'fila_index': index + 1,  # Índice de fila (1-based index)
                            'codigo': fila[0],       # Columna A
                            'nombre': fila[1],       # Columna B
                            'cedula': fila[17],      # Columna R
                            'laborales': fila[11],   # Columna L
                            'familiares': fila[12]   # Columna M
                        }
                        break
            else:
                mensaje = f"No se encontraron resultados para: {busqueda}"

        # Guardar cambios si se presiona el botón "guardar"
        if 'guardar_btn' in request.form:
            try:
                fila_index = int(request.form.get('fila_index', -1))  # Índice de fila
                laborales = request.form.get('laborales', '').strip()
                familiares = request.form.get('familiares', '').strip()

                if fila_index == -1:
                    mensaje = "Error: No se pudo determinar la fila a actualizar."
                else:
                    # Actualizar los valores en la hoja
                    rango = f"Hoja de trabajo!L{fila_index}:M{fila_index}"  # Actualizar columnas L y M
                    valores = [[laborales, familiares]]
                    service.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=rango,
                        valueInputOption="RAW",
                        body={"values": valores}
                    ).execute()
                    mensaje = "Referencias actualizadas correctamente."
            except Exception as e:
                mensaje = f"Error al guardar las referencias: {str(e)}"

    return render_template('referencias.html', datos_candidata=datos_candidata, mensaje=mensaje)

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))