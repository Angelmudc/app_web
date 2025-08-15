from dotenv import load_dotenv
load_dotenv()

from decorators import roles_required, admin_required

import os, re, unicodedata, io, json, zipfile, logging, calendar
from datetime import datetime, date, time, timedelta
from decimal import Decimal

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, send_from_directory, flash, jsonify, current_app
)
from flask_caching import Cache
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from sqlalchemy import or_, cast, String, func
from sqlalchemy.orm import subqueryload

import requests
from fpdf import FPDF

from models import Candidata, LlamadaCandidata
from config_app import create_app, db

from flask_wtf.csrf import CSRFProtect
from config_app import csrf

from forms import LlamadaCandidataForm



from sqlalchemy import cast, Date



# —————— Normaliza cédula ——————
CEDULA_PATTERN = re.compile(r'^\d{11}$')
def normalize_cedula(raw: str) -> str | None:
    """
    Quita todo lo que no sea dígito y formatea como XXX-XXXXXX-X.
    Devuelve None si tras limpiar no quedan 11 dígitos.
    """
    digits = re.sub(r'\D', '', raw or '')
    if not CEDULA_PATTERN.fullmatch(digits):
        return None
    return f"{digits[:3]}-{digits[3:9]}-{digits[9:]}"

# —————— Normaliza nombre ——————
def normalize_nombre(raw: str) -> str:
    """
    Elimina acentos y caracteres extraños de un nombre,
    dejando sólo letras básicas, espacios y guiones.
    """
    if not raw:
        return ''
    nfkd = unicodedata.normalize('NFKD', raw)
    no_accents = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Za-z\s\-]', '', no_accents).strip()

# ─────────── Inicializa la app ───────────
app = create_app()

# ─── 2) Inicializamos Cache ────────────────────────────────────────────
cache = Cache(app)

# ─── 3) Arrancamos Flask-Migrate ─────────────────────────────────────
migrate = Migrate(app, db)

csrf = CSRFProtect(app)

# ─── 4) Configuración de Cloudinary ─────────────────────────────────
import cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "")
)

USUARIOS = {
    "angel":    {"pwd": generate_password_hash("0000"), "role": "admin"},
    "divina":   {"pwd": generate_password_hash("67890"), "role": "admin"},
    "xcvcbx":     {"pwd": generate_password_hash("9999"), "role": "secretaria"},
    "darielis": {"pwd": generate_password_hash("3333"), "role": "secretaria"},
}

# ─── 5) Carga de configuración de entrevistas ─────────────────────────
def load_entrevistas_config():
    try:
        cfg_path = os.path.join(app.root_path, 'config', 'config_entrevistas.json')
        with open(cfg_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"❌ Error cargando config_entrevistas.json: {e}")
        return {}

app.config['ENTREVISTAS_CONFIG'] = load_entrevistas_config()



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
    return ''.join(c for c in unicodedata.normalize('NFKD', texto) if unicodedata.category(c) != 'Mn')

def extend_row(row, min_length=24):
    """Asegura que la fila tenga al menos 'min_length' elementos."""
    if len(row) < min_length:
        row.extend([""] * (min_length - len(row)))
    return row

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

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

from flask import render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash

from flask import jsonify

@app.route('/_test_sheets')
@roles_required('admin','secretaria')
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

# --- bloque antiguo comentado arriba ----------------------------------

@app.route('/candidatas', methods=['GET'])
@roles_required('admin', 'secretaria')
def list_candidatas():
    q = request.args.get('q', '').strip()
    if q:
        like = f"%{q}%"
        candidatas = Candidata.query.filter(
            (Candidata.nombre_completo.ilike(like)) |
            (Candidata.cedula.ilike(like))
        ).order_by(Candidata.nombre_completo).all()
    else:
        candidatas = Candidata.query.order_by(Candidata.nombre_completo).all()

    return render_template(
        'candidatas.html',
        candidatas=candidatas,
        query=q
    )


@app.route('/candidatas_db')
@roles_required('admin','secretaria')
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
        clave   = request.form.get('clave',   '').strip()
        user    = USUARIOS.get(usuario)

        if user and check_password_hash(user['pwd'], clave):
            # Guardamos usuario y rol
            session['usuario'] = usuario
            session['role']    = user['role']
            return redirect(url_for('home'))

        mensaje = "Usuario o clave incorrectos."

    return render_template('login.html', mensaje=mensaje)


@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")

# Ruta de Logout
@app.route('/logout')
@roles_required('admin','secretaria')
def logout():
    session.pop('usuario', None)  # Cierra la sesión
    return redirect(url_for('login'))

@app.route('/sugerir')
@roles_required('admin','secretaria')
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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



from flask import current_app, render_template, request
from sqlalchemy import or_
import re
from decorators import roles_required 
from models import Candidata

@app.route('/filtrar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def filtrar():
    # 1) Leer valores y mantenerlos para la plantilla
    form_data = {
        'ciudad':            request.values.get('ciudad', '').strip(),
        'rutas':             request.values.get('rutas', '').strip(),
        'modalidad':         request.values.get('modalidad', '').strip(),
        'experiencia_anos':  request.values.get('experiencia_anos', '').strip(),
        'areas_experiencia': request.values.get('areas_experiencia', '').strip(),
        'estado':            request.values.get('estado', '').strip(),  # ej. "proceso inscripcion"
    }

    # 2) Construir filtros dinámicos
    filtros = []

    if form_data['ciudad']:
        for p in re.split(r'[,\s]+', form_data['ciudad']):
            filtros.append(Candidata.direccion_completa.ilike(f"%{p}%"))

    if form_data['rutas']:
        for r in re.split(r'[,\s]+', form_data['rutas']):
            filtros.append(Candidata.rutas_cercanas.ilike(f"%{r}%"))

    if form_data['modalidad']:
        filtros.append(Candidata.modalidad_trabajo_preferida.ilike(f"%{form_data['modalidad']}%"))

    if form_data['experiencia_anos']:
        ea = form_data['experiencia_anos']
        if ea == '3 años o más':
            filtros.append(or_(
                Candidata.anos_experiencia.ilike('%3 años%'),
                Candidata.anos_experiencia.ilike('%4 años%'),
                Candidata.anos_experiencia.ilike('%5 años%'),
            ))
        else:
            filtros.append(Candidata.anos_experiencia == ea)

    if form_data['areas_experiencia']:
        filtros.append(Candidata.areas_experiencia.ilike(f"%{form_data['areas_experiencia']}%"))

    # — estado: normalizamos espacios a guión bajo para que encaje con el enum
    if form_data['estado']:
        estado_norm = form_data['estado'].replace(' ', '_')
        filtros.append(Candidata.estado == estado_norm)

    # 3) Condiciones fijas
    filtros.append(Candidata.codigo.isnot(None))
    filtros.append(or_(Candidata.porciento == None, Candidata.porciento == 0))

    # 4) Ejecutar consulta
    mensaje = None
    try:
        candidatas = (Candidata.query
                      .filter(*filtros)
                      .order_by(Candidata.nombre_completo)
                      .all())
        if any(form_data.values()) and not candidatas:
            mensaje = "⚠️ No se encontraron resultados para los filtros aplicados."
    except Exception as e:
        current_app.logger.error(f"Error al filtrar candidatas: {e}", exc_info=True)
        candidatas = []
        mensaje = f"❌ Error al filtrar los datos: {e}"

    # 5) Preparar resultado para la tabla
    resultados = [{
        'nombre':           c.nombre_completo,
        'codigo':           c.codigo,
        'telefono':         c.numero_telefono,
        'direccion':        c.direccion_completa,
        'rutas':            c.rutas_cercanas,
        'cedula':           c.cedula,
        'modalidad':        c.modalidad_trabajo_preferida,
        'experiencia_anos': c.anos_experiencia,
        'estado':           c.estado
    } for c in candidatas]

    # 6) Lista de estados para el select
    estados = [
        'en_proceso',
        'proceso_inscripcion',
        'inscrita',
        'inscrita_incompleta',
        'lista_para_trabajar',
        'trabajando',
        'descalificada'
    ]

    return render_template(
        'filtrar.html',
        form_data=form_data,
        resultados=resultados,
        mensaje=mensaje,
        estados=estados
    )



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
@roles_required('admin', 'secretaria')
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

            # Actualizar campos básicos
            obj.medio_inscripcion = request.form.get("medio", "").strip() or obj.medio_inscripcion
            obj.inscripcion       = (request.form.get("estado") == "si")
            obj.monto             = parse_decimal(request.form.get("monto", "")) or obj.monto
            obj.fecha             = parse_date(request.form.get("fecha", "")) or obj.fecha

            # — Ajustar estado según datos de inscripción —
            if obj.inscripcion:
                # Si tiene monto y fecha, está completamente inscrita
                if obj.monto and obj.fecha:
                    obj.estado = 'inscrita'
                else:
                    obj.estado = 'inscrita_incompleta'
            else:
                obj.estado = 'proceso_inscripcion'

            # Auditoría de estado
            obj.fecha_cambio_estado  = datetime.utcnow()
            obj.usuario_cambio_estado = session.get('usuario', 'desconocido')

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
            q = request.form.get("buscar", "").strip()
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
        q = request.args.get("buscar", "").strip()
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
        sel = request.args.get("candidata_seleccionada", "").strip()
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
@roles_required('admin', 'secretaria')
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

        # — Asignar valores y cambiar estado a ‘trabajando’ —
        obj.fecha_de_pago       = fecha_pago
        obj.inicio              = fecha_inicio
        obj.monto_total         = monto_total
        obj.porciento           = porcentaje
        obj.estado              = 'trabajando'
        obj.fecha_cambio_estado = datetime.utcnow()
        obj.usuario_cambio_estado = session.get('usuario', 'desconocido')

        try:
            db.session.commit()
            flash(f"✅ Se guardó correctamente. 25 % de {monto_total} es {porcentaje}. Estado: Trabajando.", "success")
            candidata = obj
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al actualizar: {e}", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

    else:
        # ——— Búsqueda GET ———
        q = request.args.get('busqueda', '').strip()
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
        sel = request.args.get('candidata','').strip()
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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



from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash
)
from decorators import roles_required
from models import Candidata, db

subir_bp = Blueprint('subir_fotos', __name__, url_prefix='/subir_fotos')

@subir_bp.route('', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def subir_fotos():
    accion = request.args.get('accion', 'buscar')
    fila_id = request.args.get('fila', type=int)
    resultados = []

    # 1) BUSCAR
    if accion == 'buscar':
        if request.method == 'POST':
            q = request.form.get('busqueda', '').strip()
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

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

    # 2) SUBIR
    if accion == 'subir':
        if not fila_id:
            flash("❌ Debes seleccionar primero una candidata.", "danger")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        candidata = Candidata.query.get(fila_id)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        if request.method == 'GET':
            return render_template('subir_fotos.html', accion='subir', fila=fila_id)

        # Solo dos archivos ahora
        files = {
            'depuracion': request.files.get('depuracion'),
            'perfil':     request.files.get('perfil'),
        }

        for campo, archivo in files.items():
            if not archivo or archivo.filename == '':
                flash(f"❌ Falta el archivo para {campo}.", "danger")
                return render_template('subir_fotos.html', accion='subir', fila=fila_id)

        try:
            candidata.depuracion = files['depuracion'].read()
            candidata.perfil     = files['perfil'].read()
            db.session.commit()
            flash("✅ Imágenes subidas y guardadas en la base de datos.", "success")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error guardando en la BD: {e}", "danger")
            return render_template('subir_fotos.html', accion='subir', fila=fila_id)

    # DEFAULT
    return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

# Registro del blueprint
app.register_blueprint(subir_bp)


@app.route('/descargar_documentos', methods=["GET"])
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin')
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
@roles_required('admin', 'secretaria')
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

# ─────────────────────────────────────────────────────────────
# Ruta: Registro/Inscripción de Otros Empleos
# ─────────────────────────────────────────────────────────────
@app.route('/otros_empleos/inscripcion', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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
@roles_required('admin', 'secretaria')
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

        # — Ajuste para áreas de experiencia múltiples —
        areas_list = request.form.getlist('areas_experiencia')
        if areas_list:
            areas_exp = ', '.join(a.strip() for a in areas_list if a.strip())
        else:
            areas_exp = None

        plancha     = request.form.get('sabe_planchar') == 'si'
        ref_laboral = request.form.get('contactos_referencias_laborales', '').strip() or None
        ref_familia = request.form.get('referencias_familiares_detalle', '').strip() or None
        acepta_pct  = request.form.get('acepta_porcentaje_sueldo') == '1'

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
            acepta_porcentaje_sueldo        = acepta_pct,
            # auditoría inicial
            estado                          = 'en_proceso',
            fecha_cambio_estado             = datetime.utcnow(),
            usuario_cambio_estado           = session.get('usuario', 'public')
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
@roles_required('admin', 'secretaria')
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
        plancha       = request.form.get('sabe_planchar') == 'si'
        ref_laboral   = request.form.get('contactos_referencias_laborales', '').strip() or None
        ref_familia   = request.form.get('referencias_familiares_detalle', '').strip() or None
        raw_pct       = request.form.get('acepta_porcentaje_sueldo')
        pct_acepta    = raw_pct == 'si'

        # 2) Normalizamos y validamos
        nombre = normalize_nombre(nombre_raw)
        cedula = normalize_cedula(cedula_raw)
        if not cedula:
            flash('❌ Cédula inválida. Debe tener 11 dígitos.', 'danger')
            return redirect(url_for('register'))
        if not nombre:
            flash('❌ El nombre es obligatorio.', 'danger')
            return redirect(url_for('register'))
        if Candidata.query.filter_by(cedula=cedula).first():
            flash('❌ Ya existe una candidata con esa cédula.', 'danger')
            return redirect(url_for('register'))

        # 3) Crear objeto con auditoría
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
            acepta_porcentaje_sueldo       = pct_acepta,
            # auditoría inicial
            estado                          = 'en_proceso',
            fecha_cambio_estado             = datetime.utcnow(),
            usuario_cambio_estado           = session.get('usuario', 'public')
        )

        # 4) Guardar en la base
        try:
            db.session.add(nueva)
            db.session.commit()
            flash('✅ Candidata registrada.', 'success')
            return redirect(url_for('register'))
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error al guardar: {e}', 'danger')
            return redirect(url_for('register'))

    # GET → mostramos el formulario
    return render_template('register.html')


@app.route('/finalizar_proceso', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def finalizar_proceso():
    # 1) Parámetro fila
    fila = request.values.get('fila', type=int)
    if not fila:
        flash("⚠️ Debes especificar la candidata (fila).", "warning")
        return redirect(url_for('list_candidatas'))

    # 2) Cargar la candidata
    candidata = Candidata.query.get(fila)
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('list_candidatas'))

    # 3) POST: procesar formulario
    if request.method == 'POST':
        # — Archivos —
        foto = request.files.get('foto_perfil')
        ced1 = request.files.get('cedula1')
        ced2 = request.files.get('cedula2')
        if foto and foto.filename:
            candidata.foto_perfil = foto.read()
        if ced1 and ced1.filename:
            candidata.cedula1 = ced1.read()
        if ced2 and ced2.filename:
            candidata.cedula2 = ced2.read()

        # — Grupos seleccionados (multi‑checkbox) —
        seleccion = request.form.getlist('grupos_empleo')
        candidata.grupos_empleo = seleccion

        # — Marcar fin de proceso y cambiar estado —
        candidata.fecha_finalizacion_proceso = datetime.utcnow()
        candidata.estado = 'proceso_inscripcion'
        candidata.fecha_cambio_estado = datetime.utcnow()
        candidata.usuario_cambio_estado = session.get('usuario', 'desconocido')

        try:
            db.session.commit()
            flash("✅ Proceso finalizado: archivos subidos y grupos asignados.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al guardar: {e}", "danger")

        # 4) Redirigir al listado de candidatas
        return redirect(url_for('list_candidatas'))

    # 5) GET: mostrar formulario de finalización
    return render_template(
        'finalizar_proceso.html',
        candidata=candidata,
        grupos=[
            'Santiago salida diaria',
            'Santiago con dormida',
            'Sto Dgo',
            'La Vega',
            'San Francisco',
            'Cotui',
            'Moca',
            'Pto Pta',
            'Fin de semana',
            'San Cristobal'
        ],
    )

# Desactiva CSRF solo para esta vista
csrf.exempt(finalizar_proceso)

from io import BytesIO
from flask import (
    render_template, request, redirect, url_for,
    send_file, abort, flash
)
from sqlalchemy import or_
from config_app import csrf
from models import Candidata
from decorators import roles_required

# Ruta que sirve la imagen binaria de la foto de perfil
@app.route('/perfil_candidata', methods=['GET'])
@roles_required('admin', 'secretaria')
def perfil_candidata():
    # Permite usar ?fila=123 o ?q=texto
    fila = request.args.get('fila', type=int)
    q    = request.args.get('q', '').strip()

    if not fila and not q:
        abort(400, "Debes pasar ?fila=ID o ?q=texto")
    if q:
        # Intentar buscar por cédula exacta o nombre parecido
        c = Candidata.query.filter(
            or_(
                Candidata.cedula == q,
                Candidata.nombre_completo.ilike(f"%{q}%")
            )
        ).first()
        if not c:
            abort(404, f"No se encontró candidata para '{q}'")
    else:
        c = Candidata.query.get(fila)
        if not c:
            abort(404, f"No existe la candidata con fila={fila}")

    if not c.foto_perfil:
        abort(404, "Esta candidata no tiene foto de perfil cargada")

    return send_file(
        BytesIO(c.foto_perfil),
        mimetype='image/png',
        as_attachment=False,
        download_name=f"perfil_{c.fila}.png"
    )

# Página para ver el perfil (imagen) y datos mínimos
@app.route('/ver_perfil', methods=['GET'])
@roles_required('admin', 'secretaria')
def ver_perfil():
    # Soporta ?fila=ID o ?q=texto
    fila = request.args.get('fila', type=int)
    q    = request.args.get('q', '').strip()

    if not fila and not q:
        flash("Debes especificar candidata con ?fila=ID o buscar con ?q=texto", "warning")
        return redirect(url_for('list_candidatas'))

    if q:
        # Buscar todas las potenciales coincidencias
        like = f"%{q}%"
        matches = Candidata.query.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like)
            )
        ).order_by(Candidata.nombre_completo).all()
        if not matches:
            flash(f"No se encontraron candidatas para '{q}'", "warning")
            return redirect(url_for('list_candidatas'))
        if len(matches) > 1:
            # Muestra lista de coincidencias
            return render_template('ver_perfil_list.html', matches=matches, query=q)
        # Si solo hay una, la mostramos
        candidata = matches[0]
    else:
        candidata = Candidata.query.get(fila)
        if not candidata:
            flash(f"No existe la candidata con fila={fila}", "warning")
            return redirect(url_for('list_candidatas'))

    return render_template('ver_perfil.html', candidata=candidata)

# Eximir esta ruta de CSRF (si tu app lo requiere)
csrf.exempt(perfil_candidata)
csrf.exempt(ver_perfil)

from datetime import date, datetime
from sqlalchemy import func, cast, Date
from flask import request, render_template
from decorators import roles_required   # ajústalo si tu decorador está en otro módulo
from models import Candidata, db

@app.route('/dashboard_procesos', methods=['GET'])
@roles_required('admin', 'secretaria')
def dashboard_procesos():
    # 1) Leer filtros
    estado_filtro = request.args.get('estado', '').strip()       # valor del enum
    desde_str     = request.args.get('desde', '').strip()        # YYYY-MM-DD
    hasta_str     = request.args.get('hasta', '').strip()        # YYYY-MM-DD

    # 2) Parsear fechas
    desde = None
    hasta = None
    try:
        if desde_str:
            desde = datetime.strptime(desde_str, '%Y-%m-%d').date()
        if hasta_str:
            hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date()
    except ValueError:
        # Ignorar formato inválido; podrías flash un warning aquí
        pass

    # 3) Stats globales
    total           = Candidata.query.count()
    hoy             = date.today()
    entradas_hoy    = Candidata.query.filter(
                          cast(Candidata.fecha_cambio_estado, Date) == hoy
                      ).count()
    counts_por_estado = dict(
        db.session.query(
            Candidata.estado,
            func.count(Candidata.estado)
        ).group_by(Candidata.estado).all()
    )

    # 4) Consulta filtrada
    q = Candidata.query
    if estado_filtro:
        q = q.filter(Candidata.estado == estado_filtro)
    if desde:
        q = q.filter(cast(Candidata.fecha_cambio_estado, Date) >= desde)
    if hasta:
        q = q.filter(cast(Candidata.fecha_cambio_estado, Date) <= hasta)
    candidatas = q.order_by(Candidata.fecha_cambio_estado.desc()).all()

    # 5) Lista de estados para el <select>
    estados = [
        'en_proceso',
        'proceso_inscripcion',
        'inscrita',
        'inscrita_incompleta',
        'lista_para_trabajar',
        'trabajando',
        'descalificada'
    ]

    return render_template(
        'dashboard_procesos.html',
        total=total,
        entradas_hoy=entradas_hoy,
        counts_por_estado=counts_por_estado,
        estados=estados,
        estado_filtro=estado_filtro,
        desde_str=desde_str,
        hasta_str=hasta_str,
        candidatas=candidatas
    )

from flask import jsonify
from datetime import datetime
from models import Candidata, db

@app.route('/auto_actualizar_estados', methods=['GET'])
def auto_actualizar_estados():
    """
    Escanea todas las candidatas con estado 'inscrita_incompleta' y,
    si ya tienen:
      • código único (obj.codigo)
      • entrevista (obj.entrevista)
      • referencias laborales y familiares (obj.referencias_laboral, obj.referencias_familiares)
      • todas las imágenes cargadas (obj.perfil, obj.cedula1, obj.cedula2, obj.depuracion)
    las marca automáticamente con estado 'lista_para_trabajar'.
    Devuelve JSON con el listado de filas actualizadas.
    """
    pendientes = Candidata.query.filter_by(estado='inscrita_incompleta').all()
    actualizadas = []

    for c in pendientes:
        if (c.codigo
            and c.entrevista
            and c.referencias_laboral
            and c.referencias_familiares
            and c.perfil
            and c.cedula1
            and c.cedula2
            and c.depuracion):
            
            c.estado = 'lista_para_trabajar'
            c.fecha_cambio_estado = datetime.utcnow()
            c.usuario_cambio_estado = 'sistema'
            actualizadas.append(c.fila)

    if actualizadas:
        db.session.commit()

    return jsonify({
        'conteo_actualizadas': len(actualizadas),
        'filas_actualizadas': actualizadas
    })


# app.py

from flask import request, render_template
from datetime import date, timedelta
from sqlalchemy import func, cast, Date, or_
from models import Candidata, LlamadaCandidata
from config_app import db
from decorators import roles_required

# ────────────────────────────────────────────────────────────────────────
def get_date_bounds(period, date_str=None):
    """
    Devuelve (start_dt, end_dt) para filtrar Candidata.marca_temporal:
      - 'day'   → desde hace 1 día hasta hoy
      - 'week'  → desde hace 7 días hasta hoy
      - 'month' → desde hace 30 días hasta hoy
      - 'date'  → solo la fecha exacta (date_str en 'YYYY-MM-DD')
      - otro    → (None, None) = sin filtro
    """
    hoy = date.today()
    if period == 'day':
        return hoy - timedelta(days=1), hoy
    if period == 'week':
        return hoy - timedelta(days=7), hoy
    if period == 'month':
        return hoy - timedelta(days=30), hoy
    if period == 'date' and date_str:
        d = date.fromisoformat(date_str)
        return d, d
    return None, None
# ────────────────────────────────────────────────────────────────────────

@app.route('/candidatas/llamadas')
@roles_required('admin','secretaria')
def listado_llamadas_candidatas():
    # Parámetros
    q               = request.args.get('q', '', type=str)
    period          = request.args.get('period', 'all')
    start_date_str  = request.args.get('start_date', None)
    page            = request.args.get('page', 1, type=int)

    # Obtiene límites de filtro sobre marca_temporal
    start_dt, end_dt = get_date_bounds(period, start_date_str)

    # Subconsulta: num_calls + última llamada por candidata
    calls_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label('cid'),
            func.count(LlamadaCandidata.id).label('num_calls'),
            func.max(LlamadaCandidata.fecha_llamada).label('last_call')
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

    # Query base: candidatas + stats
    base_q = (
        db.session.query(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.codigo,
            Candidata.numero_telefono,
            Candidata.marca_temporal,
            calls_subq.c.num_calls,
            calls_subq.c.last_call
        )
        .outerjoin(calls_subq, Candidata.fila == calls_subq.c.cid)
    )

    # Filtro de búsqueda por texto
    if q:
        il = f'%{q}%'
        base_q = base_q.filter(
            or_(
                Candidata.codigo.ilike(il),
                Candidata.nombre_completo.ilike(il),
                Candidata.numero_telefono.ilike(il),
                Candidata.cedula.ilike(il),
            )
        )

    # Helper para cada sección con paginación
    def section(estado):
        qsec = base_q.filter(Candidata.estado == estado)
        if start_dt and end_dt:
            qsec = qsec.filter(
                cast(Candidata.marca_temporal, Date) >= start_dt,
                cast(Candidata.marca_temporal, Date) <= end_dt
            )
        # Primero candidatas sin llamadas (NULL), luego llamadas antiguas primero
        return qsec.order_by(
            calls_subq.c.last_call.asc().nullsfirst()
        ).paginate(page=page, per_page=10, error_out=False)

    en_proceso     = section('en_proceso')
    en_inscripcion = section('proceso_inscripcion')
    lista_trabajar = section('lista_para_trabajar')

    return render_template(
        'llamadas_candidatas.html',
        q               = q,
        period          = period,
        start_date      = start_date_str,
        en_proceso      = en_proceso,
        en_inscripcion  = en_inscripcion,
        lista_trabajar  = lista_trabajar
    )


@app.route('/candidatas/<int:fila>/llamar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def registrar_llamada_candidata(fila):
    candidata = Candidata.query.get_or_404(fila)
    form      = LlamadaCandidataForm()

    if form.validate_on_submit():
        # Convertimos minutos a segundos para el modelo
        minutos = form.duracion_minutos.data
        segundos = minutos * 60 if minutos is not None else None

        llamada = LlamadaCandidata(
            candidata_id      = candidata.fila,
            fecha_llamada     = func.now(),
            agente            = session.get('usuario', 'desconocido'),
            resultado         = form.resultado.data,
            duracion_segundos = segundos,
            notas             = form.notas.data,
            created_at        = datetime.utcnow()
        )
        db.session.add(llamada)
        db.session.commit()

        flash(f'Llamada registrada para {candidata.nombre_completo}.', 'success')
        return redirect(url_for('listado_llamadas_candidatas'))

    return render_template(
        'registrar_llamada_candidata.html',
        form      = form,
        candidata = candidata
    )

@app.route('/candidatas/llamadas/reporte')
@roles_required('admin')
def reporte_llamadas_candidatas():
    # --- Parámetros y cálculo de rango ---
    period         = request.args.get('period', 'week')
    start_date_str = request.args.get('start_date', None)
    start_dt       = get_start_date(period, start_date_str)
    hoy            = date.today()
    page           = request.args.get('page', 1, type=int)

    # --- 1) Subquery: num_calls + last_call por candidata ---
    stats_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label('cid'),
            func.count(LlamadaCandidata.id).label('num_calls'),
            func.max(LlamadaCandidata.fecha_llamada).label('last_call')
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

    # --- 2) Base query con todos los campos necesarios ---
    base_q = (
        db.session.query(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.codigo,
            Candidata.numero_telefono,
            Candidata.marca_temporal,
            stats_subq.c.num_calls,
            stats_subq.c.last_call
        )
        .outerjoin(stats_subq, Candidata.fila == stats_subq.c.cid)
    )

    # --- Helper para paginar cada estado (estancadas) ---
    def paginate_estado(estado):
        q = base_q.filter(Candidata.estado == estado)
        if start_dt:
            # estancadas: última llamada NULL o anterior al corte
            q = q.filter(
                or_(
                    stats_subq.c.last_call == None,
                    cast(stats_subq.c.last_call, Date) < start_dt
                )
            )
        return q.order_by(
            cast(stats_subq.c.last_call, Date).desc().nullsfirst()
        ).paginate(page=page, per_page=10, error_out=False)

    estancadas_en_proceso  = paginate_estado('en_proceso')
    estancadas_inscripcion = paginate_estado('proceso_inscripcion')
    estancadas_lista       = paginate_estado('lista_para_trabajar')

    # --- 3) Promedio general de llamadas ---
    calls_query    = db.session.query(
                         LlamadaCandidata.candidata_id,
                         func.count().label('cnt')
                     ).group_by(LlamadaCandidata.candidata_id).all()
    total_calls    = sum(c.cnt for c in calls_query)
    num_with_calls = len(calls_query)
    promedio       = round(total_calls / num_with_calls, 1) if num_with_calls else 0

    # --- 4) Todas las llamadas en el período ---
    calls_q = db.session.query(LlamadaCandidata) \
                        .order_by(LlamadaCandidata.fecha_llamada.desc())
    if start_dt:
        # convertir fecha → datetime a medianoche para incluir todo el día
        start_dt_dt = datetime.combine(start_dt, datetime.min.time())
        calls_q = calls_q.filter(LlamadaCandidata.fecha_llamada >= start_dt_dt)
    calls_period = calls_q.all()

    # --- 5) Métricas agrupadas por día/semana/mes ---
    filtros = []
    if start_dt:
        filtros.append(LlamadaCandidata.fecha_llamada >= start_dt_dt)

    calls_by_day = (
        db.session.query(
            func.date_trunc('day', LlamadaCandidata.fecha_llamada).label('periodo'),
            func.count().label('cnt')
        )
        .filter(*filtros)
        .group_by('periodo')
        .order_by('periodo')
        .all()
    )
    calls_by_week = (
        db.session.query(
            func.date_trunc('week', LlamadaCandidata.fecha_llamada).label('periodo'),
            func.count().label('cnt')
        )
        .filter(*filtros)
        .group_by('periodo')
        .order_by('periodo')
        .all()
    )
    calls_by_month = (
        db.session.query(
            func.date_trunc('month', LlamadaCandidata.fecha_llamada).label('periodo'),
            func.count().label('cnt')
        )
        .filter(*filtros)
        .group_by('periodo')
        .order_by('periodo')
        .all()
    )

    # --- Renderizar plantilla con todos los datos ---
    return render_template(
        'reporte_llamadas.html',
        period                    = period,
        start_date                = start_date_str,
        hoy                       = hoy,
        estancadas_en_proceso     = estancadas_en_proceso,
        estancadas_inscripcion    = estancadas_inscripcion,
        estancadas_lista          = estancadas_lista,
        promedio                  = promedio,
        calls_period              = calls_period,
        calls_by_day              = calls_by_day,
        calls_by_week             = calls_by_week,
        calls_by_month            = calls_by_month
    )

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)