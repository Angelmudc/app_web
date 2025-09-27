# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import os, re, io, json, zipfile, logging, unicodedata
from datetime import datetime, date, timedelta
from decimal import Decimal

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, send_from_directory, flash, jsonify,
    current_app, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename  # ← agregado (lo usas en subidas de archivos)

from flask_caching import Cache
from flask_migrate import Migrate

from sqlalchemy import or_, cast, String, func
from sqlalchemy.orm import subqueryload
from models import Candidata, LlamadaCandidata, Solicitud, Reemplazo

import pandas as pd
from fpdf import FPDF

# (ya los tenías, pero los consolidamos aquí; no quitamos los de arriba)
from sqlalchemy import func, cast, Date

# ---- App/DB/config ----
from config_app import create_app, db, csrf
from decorators import roles_required, admin_required

from sqlalchemy.exc import OperationalError, IntegrityError, DBAPIError  # ← agregado IntegrityError por si lo usas en commits
# Modelos / Forms
from models import Candidata, LlamadaCandidata
from forms import LlamadaCandidataForm

# Login (varias rutas tuyas usan esto)
from flask_login import login_user, logout_user, login_required, current_user  # ← agregado

# Peticiones HTTP externas (lo utilizas en descargas/ZIP u otras utilidades)
import requests  # ← agregado

# Carga ansiosa opcional si la usas en alguna vista
from sqlalchemy.orm import joinedload  # ← agregado (complemento de subqueryload)

from sqlalchemy.exc import OperationalError, DBAPIError
from sqlalchemy.sql import text

# IMPORTS
from datetime import date, datetime
from flask import request, render_template, url_for, jsonify, flash, redirect
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload, load_only

# Asegúrate de tener estos import reales según tu proyecto:
# from app import app, db
# from models import Solicitud, Reemplazo
# from decorators import roles_required

# ← ESTE TRY/EXCEPT EVITA ROMPER SI CAMBIA LA RUTA DEL FORM
try:
    from admin.forms import AdminSolicitudForm
except Exception:
    AdminSolicitudForm = None

# -----------------------------------------------------------------------------
# APP BOOT
# -----------------------------------------------------------------------------
app = create_app()
cache = Cache(app)
migrate = Migrate(app, db)
# Helper para verificar si un endpoint existe (usable desde Jinja)
# helpers para plantillas
app.jinja_env.globals['has_endpoint'] = lambda name: name in app.view_functions

@app.teardown_appcontext
def _shutdown_session(exception=None):
    # Cerrar/limpiar la sesión SIEMPRE al final del request
    try:
        db.session.remove()
    except Exception:
        pass


def url_for_safe(endpoint, **values):
    from flask import url_for
    return url_for(endpoint, **values) if endpoint in app.view_functions else None

app.jinja_env.globals['url_for_safe'] = url_for_safe

# -----------------------------------------------------------------------------
# USUARIOS DE SESIÓN SENCILLA (panel interno)
# -----------------------------------------------------------------------------
USUARIOS = {
    "angel":    {"pwd": generate_password_hash("0000"), "role": "admin"},
    "divina":   {"pwd": generate_password_hash("67890"), "role": "admin"},
    "xcvcbx":   {"pwd": generate_password_hash("9999"), "role": "secretaria"},
    "darielis": {"pwd": generate_password_hash("3333"), "role": "secretaria"},
}

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
CEDULA_PATTERN = re.compile(r'^\d{11}$')


from sqlalchemy.exc import OperationalError

@app.errorhandler(OperationalError)
def _handle_operational_error(e):
    # Conexión rota (SSL/bad record mac). Limpia y devuelve 503 legible.
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.get_engine().dispose()
    except Exception:
        pass
    return ("⚠️ Conexión a la base de datos no disponible momentáneamente. "
            "Intenta nuevamente."), 503


def _db_retry(fn, *args, **kwargs):
    """
    Ejecuta fn y, si la conexión está rota (SSL / bad record mac / connection reset),
    hace remove() y reintenta UNA vez.
    """
    try:
        return fn(*args, **kwargs)
    except (OperationalError, DBAPIError) as e:
        # Mensajes típicos de socket/SSL roto
        msg = str(e).lower()
        transient = any(
            s in msg for s in [
                "ssl error", "bad record mac", "connection reset",
                "server closed the connection", "terminating connection",
                "could not receive data from server"
            ]
        )
        if transient:
            try:
                db.session.remove()
            except Exception:
                pass
            # segundo intento
            return fn(*args, **kwargs)
        raise

def _get_candidata_safe_by_pk(fila: int):
    """Carga Candidata por PK con un retry si la conexión está rota."""
    def _load():
        return Candidata.query.get(fila)
    return _db_retry(_load)

def _fetch_image_bytes_safe(fila: int):
    """
    Saca los bytes de imagen directamente con conexión cruda (más tolerante),
    probando primero foto_perfil y luego perfil.
    """
    engine = db.get_engine()
    def _load():
        with engine.connect() as conn:
            # 1) foto_perfil
            r = conn.execute(text("SELECT foto_perfil FROM candidatas WHERE fila=:f"), {"f": fila}).fetchone()
            if r and r[0]:
                return bytes(r[0])
            # 2) perfil (fallback)
            r2 = conn.execute(text("SELECT perfil FROM candidatas WHERE fila=:f"), {"f": fila}).fetchone()
            if r2 and r2[0]:
                return bytes(r2[0])
            return None
    return _db_retry(_load)

def run_db_safely(fn, *, retry_once=True, fallback=None):
    """
    Ejecuta una función que toca la DB. Si hay OperationalError (conexión rota),
    hace rollback/cierra y reintenta UNA vez.
    """
    try:
        return fn()
    except OperationalError as e:
        # Conexión del pool rota → limpia y reintenta
        db.session.rollback()
        db.session.close()
        if retry_once:
            try:
                return fn()
            except OperationalError:
                db.session.rollback()
                db.session.close()
                if fallback is not None:
                    return fallback
                raise
        if fallback is not None:
            return fallback
        raise

def normalize_cedula(raw: str):
    digits = re.sub(r'\D', '', raw or '')
    if not CEDULA_PATTERN.fullmatch(digits):
        return None
    return f"{digits[:3]}-{digits[3:9]}-{digits[9:]}"

def normalize_nombre(raw: str) -> str:
    if not raw:
        return ''
    nfkd = unicodedata.normalize('NFKD', raw)
    no_accents = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Za-z\s\-]', '', no_accents).strip()

def parse_date(s: str):
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except Exception:
        return None

def parse_decimal(s: str):
    try:
        return Decimal((s or "").replace(',', '.'))
    except Exception:
        return None

def get_date_bounds(period, date_str=None):
    """
    Devuelve (start_dt, end_dt):
      - 'day'   → última 24h
      - 'week'  → 7 días
      - 'month' → 30 días
      - 'date'  → fecha exacta (YYYY-MM-DD)
      - otro    → (None, None)
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

def get_start_date(period, date_str=None):
    start, _ = get_date_bounds(period, date_str)
    return start

# -----------------------------------------------------------------------------
# CARGA CONFIG DE ENTREVISTAS (JSON local)
# -----------------------------------------------------------------------------
def load_entrevistas_config():
    try:
        cfg_path = os.path.join(app.root_path, 'config', 'config_entrevistas.json')
        with open(cfg_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"❌ Error cargando config_entrevistas.json: {e}")
        return {}

app.config['ENTREVISTAS_CONFIG'] = load_entrevistas_config()

# -----------------------------------------------------------------------------
# ERRORES / STATIC
# -----------------------------------------------------------------------------
@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(app.root_path, 'static'), filename)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")

# -----------------------------------------------------------------------------
# AUTH (panel interno por sesión simple)
# -----------------------------------------------------------------------------
@app.route('/')
def home():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    return render_template('home.html', usuario=session['usuario'], current_year=datetime.utcnow().year)

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip()
        clave   = request.form.get('clave',   '').strip()
        user    = USUARIOS.get(usuario)

        if user and check_password_hash(user['pwd'], clave):
            session['usuario'] = usuario
            session['role']    = user['role']
            return redirect(url_for('home'))

        mensaje = "Usuario o clave incorrectos."
    return render_template('login.html', mensaje=mensaje)

@app.route('/logout')
@roles_required('admin','secretaria')
def logout():
    session.pop('usuario', None)
    return redirect(url_for('login'))

# -----------------------------------------------------------------------------
# CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas', methods=['GET'])
@roles_required('admin', 'secretaria')
def list_candidatas():
    q = request.args.get('q', '').strip()

    base = Candidata.query.order_by(Candidata.nombre_completo.asc())
    if q:
        like = f"%{q}%"
        base = base.filter(or_(
            Candidata.nombre_completo.ilike(like),
            Candidata.cedula.ilike(like),
        ))

    candidatas = safe_all(base)
    return render_template('candidatas.html', candidatas=candidatas, query=q)

@app.route('/candidatas_db')
@roles_required('admin','secretaria')
def list_candidatas_db():
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
            })
        return jsonify({"candidatas": resultado}), 200
    except Exception as e:
        app.logger.exception("Error leyendo desde la DB")
        return jsonify({"error": str(e)}), 500

# -----------------------------------------------------------------------------
# ENTREVISTA (usa JSON local de config)
# -----------------------------------------------------------------------------
@app.route('/entrevista', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def entrevista():
    tipo    = request.values.get('tipo', '').strip().lower()
    fila    = request.values.get('fila', type=int)
    config  = current_app.config['ENTREVISTAS_CONFIG']

    # Guardar respuestas
    if request.method == 'POST' and tipo and fila:
        preguntas  = config.get(tipo, {}).get('preguntas', [])
        respuestas = []
        faltan     = []
        for p in preguntas:
            v = request.form.get(p['id'], '').strip()
            if not v:
                faltan.append(p['id'])
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
        return redirect(url_for('entrevista') + f"?fila={fila}&tipo={tipo}")

    # Buscar candidata
    if not fila:
        resultados = []
        if request.method == 'POST':
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
        return render_template('entrevista.html', etapa='buscar', resultados=resultados)

    # Elegir tipo
    if fila and not tipo:
        candidata = Candidata.query.get(fila)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('entrevista'))
        tipos = [(k, cfg.get('titulo', k)) for k, cfg in config.items()]
        return render_template('entrevista.html', etapa='elegir_tipo', candidata=candidata, tipos=tipos)

    # Form dinámico
    if fila and tipo:
        candidata  = Candidata.query.get(fila)
        cfg        = config.get(tipo)
        if not cfg or not candidata:
            flash("⚠️ Parámetros inválidos.", "danger")
            return redirect(url_for('entrevista'))
        return render_template('entrevista.html',
                               etapa='formulario',
                               candidata=candidata,
                               tipo=tipo,
                               preguntas=cfg.get('preguntas', []),
                               titulo=cfg.get('titulo'),
                               datos={}, mensaje=None, focus_field=None)

    return redirect(url_for('entrevista'))

# -----------------------------------------------------------------------------
# BÚSQUEDA / EDICIÓN BÁSICA
# -----------------------------------------------------------------------------
@app.route('/buscar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def buscar_candidata():
    busqueda = (request.form.get('busqueda', '') if request.method == 'POST'
                else request.args.get('busqueda', '')).strip()
    resultados, candidata, mensaje = [], None, None

    # Guardar edición
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
                obj.contactos_referencias_laborales = request.form.get('contactos_referencias_laborales','').strip() or obj.contactos_referencias_laborales
                obj.referencias_familiares_detalle  = request.form.get('referencias_familiares_detalle','').strip() or obj.referencias_familiares_detalle
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

    # Carga detalles (GET ?candidata_id=)
    cid = request.args.get('candidata_id','').strip()
    if cid.isdigit():
        candidata = Candidata.query.get(int(cid))
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."

    # Búsqueda
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

    return render_template('buscar.html',
                           busqueda=busqueda,
                           resultados=resultados,
                           candidata=candidata,
                           mensaje=mensaje)

# -----------------------------------------------------------------------------
# FILTRAR
# -----------------------------------------------------------------------------
@app.route('/filtrar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def filtrar():
    # --- Captura de filtros desde request ---
    form_data = {
        'ciudad':            (request.values.get('ciudad') or "").strip(),
        'rutas':             (request.values.get('rutas') or "").strip(),
        'modalidad':         (request.values.get('modalidad') or "").strip(),
        'experiencia_anos':  (request.values.get('experiencia_anos') or "").strip(),
        'areas_experiencia': (request.values.get('areas_experiencia') or "").strip(),
        'estado':            (request.values.get('estado') or "").strip(),
    }

    filtros = []

    # --- Ciudad ---
    if form_data['ciudad']:
        ciudades = [p.strip() for p in re.split(r'[,\s]+', form_data['ciudad']) if p.strip()]
        if ciudades:
            filtros.extend([Candidata.direccion_completa.ilike(f"%{c}%") for c in ciudades])

    # --- Rutas ---
    if form_data['rutas']:
        rutas = [r.strip() for r in re.split(r'[,\s]+', form_data['rutas']) if r.strip()]
        if rutas:
            filtros.extend([Candidata.rutas_cercanas.ilike(f"%{r}%") for r in rutas])

    # --- Modalidad ---
    if form_data['modalidad']:
        filtros.append(Candidata.modalidad_trabajo_preferida.ilike(f"%{form_data['modalidad']}%"))

    # --- Experiencia en años ---
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

    # --- Áreas de experiencia ---
    if form_data['areas_experiencia']:
        filtros.append(Candidata.areas_experiencia.ilike(f"%{form_data['areas_experiencia']}%"))

    # --- Estado ---
    if form_data['estado']:
        estado_norm = form_data['estado'].replace(" ", "_")
        filtros.append(Candidata.estado == estado_norm)

    # --- Reglas fijas ---
    filtros.append(Candidata.codigo.isnot(None))
    filtros.append(or_(Candidata.porciento == None, Candidata.porciento == 0))

    mensaje = None
    resultados = []

    try:
        query = Candidata.query.filter(*filtros).order_by(Candidata.nombre_completo)
        candidatas = query.all()

        if candidatas:
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
        else:
            if any(form_data.values()):
                mensaje = "⚠️ No se encontraron resultados para los filtros aplicados."

    except Exception as e:
        current_app.logger.error(f"Error al filtrar candidatas: {e}", exc_info=True)
        mensaje = f"❌ Error al filtrar los datos: {e}"

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


# -----------------------------------------------------------------------------
# INSCRIPCIÓN / PORCENTAJE / PAGOS / REPORTE PAGOS
# -----------------------------------------------------------------------------
from utils_codigo import generar_codigo_unico  # tu función optimizada

@app.route('/inscripcion', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def inscripcion():
    mensaje = ""
    resultados = []
    candidata = None

    if request.method == "POST":
        if request.form.get("guardar_inscripcion"):
            cid = request.form.get("candidata_id", "").strip()
            if not cid.isdigit():
                flash("❌ ID inválido.", "error")
                return redirect(url_for('inscripcion'))

            obj = Candidata.query.get(int(cid))
            if not obj:
                flash("⚠️ Candidata no encontrada.", "error")
                return redirect(url_for('inscripcion'))

            if not obj.codigo:
                obj.codigo = generar_codigo_unico()

            obj.medio_inscripcion = request.form.get("medio", "").strip() or obj.medio_inscripcion
            obj.inscripcion       = (request.form.get("estado") == "si")
            obj.monto             = parse_decimal(request.form.get("monto", "")) or obj.monto
            obj.fecha             = parse_date(request.form.get("fecha", "")) or obj.fecha

            if obj.inscripcion:
                if obj.monto and obj.fecha:
                    obj.estado = 'inscrita'
                else:
                    obj.estado = 'inscrita_incompleta'
            else:
                obj.estado = 'proceso_inscripcion'

            obj.fecha_cambio_estado   = datetime.utcnow()
            obj.usuario_cambio_estado = session.get('usuario', 'desconocido')

            try:
                db.session.commit()
                flash(f"✅ Inscripción guardada. Código: {obj.codigo}", "success")
                candidata = obj
            except Exception as e:
                db.session.rollback()
                flash(f"❌ Error al guardar inscripción: {e}", "error")
                return redirect(url_for('inscripcion'))
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

    else:
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

        sel = request.args.get("candidata_seleccionada", "").strip()
        if not resultados and sel.isdigit():
            candidata = Candidata.query.get(int(sel))
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."

    return render_template("inscripcion.html",
                           resultados=resultados,
                           candidata=candidata,
                           mensaje=mensaje)

@app.route('/porciento', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def porciento():
    resultados, candidata = [], None

    if request.method == "POST":
        fila_id = request.form.get('fila_id', '').strip()
        if not fila_id.isdigit():
            flash("❌ Fila inválida.", "danger")
            return redirect(url_for('porciento'))

        obj = Candidata.query.get(int(fila_id))
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('porciento'))

        fecha_pago   = parse_date(request.form.get("fecha_pago",""))
        fecha_inicio = parse_date(request.form.get("fecha_inicio",""))
        monto_total  = parse_decimal(request.form.get("monto_total",""))

        if None in (fecha_pago, fecha_inicio, monto_total):
            flash("❌ Datos incompletos o inválidos.", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

        porcentaje = (monto_total * Decimal("0.25")).quantize(Decimal("0.01"))

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

        sel = request.args.get('candidata','').strip()
        if sel.isdigit() and not resultados:
            candidata = Candidata.query.get(int(sel))
            if not candidata:
                flash("⚠️ Candidata no encontrada.", "warning")

    return render_template("porciento.html",
                           resultados=resultados,
                           candidata=candidata)

@app.route('/pagos', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def pagos():
    resultados, candidata = [], None

    if request.method == 'POST':
        fila = request.form.get('fila', type=int)
        monto_str = request.form.get('monto_pagado', '').strip()
        calificacion = request.form.get('calificacion', '').strip()

        if not fila or not monto_str or not calificacion:
            flash("❌ Datos inválidos.", "danger")
            return redirect(url_for('pagos'))

        try:
            monto_pagado = Decimal(monto_str)
        except:
            flash("❌ Monto inválido.", "danger")
            return redirect(url_for('pagos'))

        obj = Candidata.query.get(fila)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('pagos'))

        obj.porciento = max(obj.porciento - monto_pagado, Decimal('0'))
        obj.calificacion = calificacion

        try:
            db.session.commit()
            flash("✅ Pago guardado con éxito.", "success")
            candidata = obj
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al guardar: {e}", "danger")

        return render_template('pagos.html', resultados=[], candidata=candidata)

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

    return render_template('pagos.html', resultados=resultados, candidata=candidata)

# ⬇️ Pega esto en tu app.py (reemplaza las dos rutas existentes)



def _retry_query(callable_fn, retries=2, swallow=False):
    """
    Ejecuta una función que hace queries a la BD con reintentos básicos.
    - retries: número de reintentos adicionales.
    - swallow: si True, retorna None en vez de levantar excepción tras agotar reintentos.
    """
    last_err = None
    for _ in range(retries + 1):
        try:
            return callable_fn()
        except (OperationalError, DBAPIError) as e:
            # Limpia la sesión para no dejarla en estado inválido
            db.session.rollback()
            last_err = e
            continue
    if swallow:
        return None
    raise last_err


@app.route('/reporte_inscripciones', methods=['GET'])
@roles_required('admin')
def reporte_inscripciones():
    """
    Reporte de inscripciones por mes/año.
    - Visualización: pagina resultados (page/per_page) y renderiza tabla HTML.
    - Descarga Excel (descargar=1): trae todos los resultados del mes/año y genera XLSX.
    Robusto frente a caídas SSL/db con reintentos y rollback.
    """
    # 1) Parámetros
    try:
        mes       = int(request.args.get('mes', datetime.today().month))
        anio      = int(request.args.get('anio', datetime.today().year))
        descargar = request.args.get('descargar', '0') == '1'
        page      = max(1, request.args.get('page', default=1, type=int))
        per_page  = min(200, max(1, request.args.get('per_page', default=20, type=int)))
    except Exception as e:
        return f"Parámetros inválidos: {e}", 400

    # 2) Base query (solo columnas necesarias)
    def _base_query():
        return (
            db.session.query(
                Candidata.nombre_completo,
                Candidata.direccion_completa,
                Candidata.numero_telefono,
                Candidata.cedula,
                Candidata.codigo,
                Candidata.medio_inscripcion,
                Candidata.inscripcion,
                Candidata.monto,
                Candidata.fecha
            )
            .filter(
                Candidata.inscripcion.is_(True),
                Candidata.fecha.isnot(None),
                db.extract('month', Candidata.fecha) == mes,
                db.extract('year',  Candidata.fecha) == anio
            )
        )

    # 3) Modo descarga (sin paginar): exporta TODO el mes/año
    if descargar:
        def _fetch_all():
            # Trae todo para el Excel, pero solo columnas mínimas
            return _base_query().order_by(Candidata.fecha.asc()).all()

        rows = _retry_query(_fetch_all, retries=2, swallow=True)
        if rows is None:
            return render_template(
                "reporte_inscripciones.html",
                reporte_html="",
                mes=mes, anio=anio,
                mensaje="❌ No fue posible conectarse a la base de datos para generar el Excel. Intenta de nuevo."
            ), 200

        if not rows:
            return render_template(
                "reporte_inscripciones.html",
                reporte_html="",
                mes=mes, anio=anio,
                mensaje=f"No se encontraron inscripciones para {mes}/{anio}."
            ), 200

        # Construir DataFrame para Excel
        df = pd.DataFrame([{
            "Nombre":       r[0],
            "Ciudad":       r[1] or "",
            "Teléfono":     r[2] or "",
            "Cédula":       r[3],
            "Código":       r[4] or "",
            "Medio":        r[5] or "",
            "Inscripción":  "Sí" if r[6] else "No",
            "Monto":        float(r[7] or 0),
            "Fecha":        r[8].strftime("%Y-%m-%d") if r[8] else ""
        } for r in rows])

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

    # 4) Modo visualización (paginado)
    def _fetch_page():
        q = _base_query().order_by(Candidata.fecha.desc())
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return total, items

    fetched = _retry_query(_fetch_page, retries=2, swallow=True)
    if fetched is None:
        return render_template(
            "reporte_inscripciones.html",
            reporte_html="",
            mes=mes, anio=anio,
            mensaje="❌ No fue posible conectarse a la base de datos. Intenta nuevamente."
        ), 200

    total, items = fetched

    if not items:
        return render_template(
            "reporte_inscripciones.html",
            reporte_html="",
            mes=mes, anio=anio,
            mensaje=f"No se encontraron inscripciones para {mes}/{anio}."
        ), 200

    # Constrúyelo rápido con pandas → HTML
    df = pd.DataFrame([{
        "Nombre":       r[0],
        "Ciudad":       r[1] or "",
        "Teléfono":     r[2] or "",
        "Cédula":       r[3],
        "Código":       r[4] or "",
        "Medio":        r[5] or "",
        "Inscripción":  "Sí" if r[6] else "No",
        "Monto":        float(r[7] or 0),
        "Fecha":        r[8].strftime("%Y-%m-%d") if r[8] else ""
    } for r in items])

    reporte_html = df.to_html(classes="table table-striped", index=False, border=0)

    # (Opcional) Si luego quieres renderizar paginación en HTML,
    # aquí tienes los datos; tu template actual no los usa.
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "reporte_inscripciones.html",
        reporte_html=reporte_html,
        mes=mes, anio=anio,
        mensaje="",
        # Datos útiles por si luego activas paginación visual:
        page=page, per_page=per_page, total=total, total_pages=total_pages
    )


@app.route('/reporte_pagos', methods=['GET'])
@roles_required('admin', 'secretaria')
def reporte_pagos():
    """
    Reporte de pagos pendientes (porciento > 0).
    - Visualización paginada (page/per_page).
    Robusto frente a fallos de conexión con reintentos y rollback.
    """
    page     = max(1, request.args.get('page', default=1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', default=20, type=int)))

    def _fetch_page():
        # Solo selecciona columnas necesarias
        q = (
            db.session.query(
                Candidata.nombre_completo,
                Candidata.cedula,
                Candidata.codigo,
                Candidata.direccion_completa,
                Candidata.monto_total,
                Candidata.porciento,
                Candidata.inicio,
                Candidata.fecha_de_pago
            )
            .filter(Candidata.porciento > 0)
            .order_by(Candidata.fecha_de_pago.asc().nullsfirst(), Candidata.nombre_completo.asc())
        )
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return total, items

    fetched = _retry_query(_fetch_page, retries=2, swallow=True)
    if fetched is None:
        return render_template(
            'reporte_pagos.html',
            pagos_pendientes=[],
            mensaje="❌ No fue posible conectarse a la base de datos. Intenta nuevamente."
        ), 200

    total, rows = fetched

    pagos_pendientes = [{
        'nombre':               r[0],
        'cedula':               r[1],
        'codigo':               r[2] or "No especificado",
        'ciudad':               r[3] or "No especificado",
        'monto_total':          float(r[4] or 0),
        'porcentaje_pendiente': float(r[5] or 0),
        'fecha_inicio':         r[6].strftime("%Y-%m-%d") if r[6] else "No registrada",
        'fecha_pago':           r[7].strftime("%Y-%m-%d") if r[7] else "No registrada",
    } for r in rows]

    mensaje = None if pagos_pendientes else "⚠️ No se encontraron pagos pendientes."

    # (Opcional) Datos de paginación por si luego quieres agregar controles en el template
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        'reporte_pagos.html',
        pagos_pendientes=pagos_pendientes,
        mensaje=mensaje,
        page=page, per_page=per_page, total=total, total_pages=total_pages
    )

# -----------------------------------------------------------------------------
# SUBIR FOTOS (BINARIOS EN DB)
# -----------------------------------------------------------------------------
from flask import Blueprint
subir_bp = Blueprint('subir_fotos', __name__, url_prefix='/subir_fotos')

@subir_bp.route('', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def subir_fotos():
    accion = request.args.get('accion', 'buscar')
    fila_id = request.args.get('fila', type=int)
    resultados = []

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

        return render_template('subir_fotos.html', accion='buscar', resultados=resultados)

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

    return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

app.register_blueprint(subir_bp)

# -----------------------------------------------------------------------------
# GESTIONAR ARCHIVOS / PDF (DB only)
# -----------------------------------------------------------------------------
@app.route("/gestionar_archivos", methods=["GET", "POST"])
@roles_required('admin', 'secretaria')
def gestionar_archivos():
    accion = request.args.get("accion", "buscar")
    mensaje = None
    resultados = []
    docs = {}
    fila = request.args.get("fila", "").strip()

    if accion == "descargar":
        doc = request.args.get("doc", "").strip()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        idx = int(fila)
        if doc == "pdf":
            return redirect(url_for("generar_pdf_entrevista", fila=idx))
        return "Documento no reconocido", 400

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
        docs["entrevista"] = c.entrevista or ""

        return render_template("gestionar_archivos.html",
                               accion=accion,
                               fila=idx,
                               docs=docs,
                               mensaje=mensaje)

    return redirect(url_for("gestionar_archivos", accion="buscar"))

@app.route('/generar_pdf_entrevista')
@roles_required('admin', 'secretaria')
def generar_pdf_entrevista():
    fila_index = request.args.get('fila', type=int)
    if not fila_index:
        return "Error: falta parámetro fila", 400

    c = Candidata.query.get(fila_index)
    if not c or not c.entrevista:
        return "No hay entrevista registrada para esa fila", 404
    texto_entrevista = c.entrevista

    ref_laborales  = c.referencias_laboral or ""
    ref_familiares = c.referencias_familiares or ""

    try:
        pdf = FPDF()
        pdf.add_page()

        # Fuentes (fallback si no existen las DejaVu)
        font_dir = os.path.join(app.root_path, "static", "fonts")
        reg = os.path.join(font_dir, "DejaVuSans.ttf")
        bold= os.path.join(font_dir, "DejaVuSans-Bold.ttf")
        try:
            pdf.add_font("DejaVuSans", "", reg, uni=True)
            pdf.add_font("DejaVuSans", "B", bold, uni=True)
            base_font = "DejaVuSans"
        except Exception:
            base_font = "Arial"

        # Logo opcional
        logo = os.path.join(app.root_path, "static", "logo_nuevo.png")
        if os.path.exists(logo):
            w = 70
            x = (pdf.w - w) / 2
            pdf.image(logo, x=x, y=10, w=w)
        pdf.set_line_width(0.5)
        pdf.set_draw_color(0, 0, 0)
        pdf.line(pdf.l_margin, 30, pdf.w - pdf.r_margin, 30)
        pdf.set_y(40)

        # Título
        pdf.set_font(base_font, "B", 18)
        pdf.set_fill_color(0, 102, 204)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, "Entrevista de Candidata", ln=True, align="C", fill=True)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(10)

        # Entrevista
        pdf.set_font(base_font, "", 12)
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
                try:
                    pdf.set_font(base_font, "", 16)
                except:
                    pdf.set_font(base_font, "", 12)
                bw = pdf.get_string_width(bullet + " ")
                pdf.cell(bw, 8, bullet, ln=0)

                pdf.set_font(base_font, "", 12)
                pdf.set_text_color(0, 102, 204)
                avail_w = pdf.w - pdf.r_margin - (pdf.l_margin + bw)
                pdf.multi_cell(avail_w, 8, respuesta)
                pdf.ln(4)

                pdf.set_text_color(0, 0, 0)
                pdf.set_font(base_font, "", 12)
            else:
                pdf.multi_cell(0, 8, line)
                pdf.ln(4)
        pdf.ln(5)

        # Referencias
        pdf.set_font(base_font, "B", 14)
        pdf.set_text_color(0, 102, 204)
        pdf.cell(0, 10, "Referencias", ln=True)
        pdf.ln(3)

        # Laborales
        pdf.set_font(base_font, "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Referencias Laborales:", ln=True)
        pdf.set_font(base_font, "", 12)
        if ref_laborales:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_laborales)
        else:
            pdf.cell(0, 8, "No hay referencias laborales.", ln=True)
        pdf.ln(5)

        # Familiares
        pdf.set_font(base_font, "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, "Referencias Familiares:", ln=True)
        pdf.set_font(base_font, "", 12)
        if ref_familiares:
            pdf.set_text_color(0, 102, 204)
            pdf.multi_cell(0, 8, ref_familiares)
        else:
            pdf.cell(0, 8, "No hay referencias familiares.", ln=True)
        pdf.ln(5)

        output    = pdf.output(dest="S")
        pdf_bytes = output if isinstance(output, (bytes, bytearray)) else output.encode("latin1")
        buf       = io.BytesIO(pdf_bytes); buf.seek(0)
        return send_file(buf,
                         mimetype="application/pdf",
                         as_attachment=True,
                         download_name=f"entrevista_candidata_{fila_index}.pdf")
    except Exception as e:
        return f"Error interno generando PDF: {e}", 500

@app.route("/gestionar_archivos/descargar_uno", methods=["GET"])
@roles_required('admin', 'secretaria')
def descargar_uno_db():
    cid = request.args.get("id", type=int)
    doc = (request.args.get("doc") or "").strip()
    if not cid or doc not in ("depuracion","perfil","cedula1","cedula2"):
        return "Error: parámetros inválidos", 400

    # Cargar con reintento y API moderna
    def _load():
        return db.session.get(Candidata, cid)
    candidata = _retry_query(_load, retries=1, swallow=False)
    if not candidata:
        return "Candidata no encontrada", 404

    data = getattr(candidata, doc, None)
    if not data:
        return f"No hay archivo para {doc}", 404

    # Detectar mimetype por cabecera
    b = data[:8] if isinstance(data, (bytes, bytearray)) else b""
    if b.startswith(b"\x89PNG"):
        mt = "image/png"
        ext = "png"
    elif b.startswith(b"\xFF\xD8\xFF"):
        mt = "image/jpeg"
        ext = "jpg"
    elif b[:4] == b"GIF8":
        mt = "image/gif"
        ext = "gif"
    elif b[:4] == b"%PDF":
        mt = "application/pdf"
        ext = "pdf"
    else:
        mt = "application/octet-stream"
        ext = "bin"

    bio = io.BytesIO(data); bio.seek(0)
    # No fuerces descarga si quieres previsualizar; aquí lo dejo como adjunto (seguro)
    return send_file(
        bio,
        mimetype=mt,
        as_attachment=True,
        download_name=f"{doc}.{ext}",
        max_age=0
    )


# -----------------------------------------------------------------------------
# REFERENCIAS (laborales / familiares)
# -----------------------------------------------------------------------------
@app.route('/referencias', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def referencias():
    mensaje = None
    accion = request.args.get('accion', 'buscar')
    resultados = []
    candidata = None

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
                {'id': c.fila, 'nombre': c.nombre_completo,
                 'cedula': c.cedula, 'telefono': c.numero_telefono or 'No especificado'}
                for c in filas
            ]
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas."
        else:
            mensaje = "⚠️ Ingresa un término de búsqueda."
        return render_template('referencias.html',
                               accion='buscar',
                               resultados=resultados,
                               mensaje=mensaje)

    candidata_id = request.args.get('candidata', type=int)
    if request.method == 'GET' and candidata_id:
        candidata = Candidata.query.get(candidata_id)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."
            return render_template('referencias.html',
                                   accion='buscar',
                                   resultados=[],
                                   mensaje=mensaje)
        return render_template('referencias.html',
                               accion='ver',
                               candidata=candidata,
                               mensaje=mensaje)

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
        return render_template('referencias.html',
                               accion='ver',
                               candidata=candidata,
                               mensaje=mensaje)

    return render_template('referencias.html',
                           accion='buscar',
                           resultados=[],
                           mensaje=mensaje)

# -----------------------------------------------------------------------------
# DASHBOARD / AUTOMATIONS
# -----------------------------------------------------------------------------
# app.py (o donde tengas tus rutas principales)
from datetime import date, datetime
from sqlalchemy import func, cast, Date
from sqlalchemy.exc import OperationalError
from flask import request, render_template, flash
from decorators import roles_required
from models import Candidata
from config_app import db

@app.route('/dashboard_procesos', methods=['GET'])
@roles_required('admin', 'secretaria')
def dashboard_procesos():
    estado_filtro = request.args.get('estado', '').strip()
    desde_str     = request.args.get('desde', '').strip()
    hasta_str     = request.args.get('hasta', '').strip()
    page          = request.args.get('page', 1, type=int)
    per_page      = request.args.get('per_page', 20, type=int)

    # Parseo de fechas
    desde = None
    hasta = None
    try:
        if desde_str:
            desde = datetime.strptime(desde_str, '%Y-%m-%d').date()
        if hasta_str:
            hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date()
    except ValueError:
        desde = None
        hasta = None

    estados = [
        'en_proceso',
        'proceso_inscripcion',
        'inscrita',
        'inscrita_incompleta',
        'lista_para_trabajar',
        'trabajando',
        'descalificada'
    ]

    # Defaults si la BD está caída
    total = 0
    entradas_hoy = 0
    counts_por_estado = {}
    paginado = None

    try:
        # KPIs
        total = Candidata.query.count()
        hoy = date.today()
        entradas_hoy = Candidata.query.filter(
            cast(Candidata.fecha_cambio_estado, Date) == hoy
        ).count()
        counts_por_estado = dict(
            db.session.query(
                Candidata.estado,
                func.count(Candidata.estado)
            ).group_by(Candidata.estado).all()
        )

        # Query filtrada + orden + paginación
        q = Candidata.query
        if estado_filtro:
            q = q.filter(Candidata.estado == estado_filtro)
        if desde:
            q = q.filter(cast(Candidata.fecha_cambio_estado, Date) >= desde)
        if hasta:
            q = q.filter(cast(Candidata.fecha_cambio_estado, Date) <= hasta)

        q = q.order_by(Candidata.fecha_cambio_estado.desc())

        # Paginado compatible con SQLAlchemy 1.4/2.x y Flask-SQLAlchemy 3.x
        try:
            paginado = q.paginate(page=page, per_page=per_page, error_out=False)
        except AttributeError:
            paginado = db.paginate(q, page=page, per_page=per_page, error_out=False)

    except OperationalError:
        flash("⚠️ No se pudo conectar a la base de datos (conexión remota). Reintenta en unos segundos.", "warning")
        # Paginado vacío para que el template no falle
        class _EmptyPagination:
            def __init__(self):
                self.items = []
                self.total = 0
                self.pages = 0
                self.page = page
                self.prev_num = None
                self.next_num = None
            def has_prev(self): return False
            def has_next(self): return False
            def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):
                return iter([])
        paginado = _EmptyPagination()

    return render_template(
        'dashboard_procesos.html',
        total=total,
        entradas_hoy=entradas_hoy,
        counts_por_estado=counts_por_estado,
        estados=estados,
        estado_filtro=estado_filtro,
        desde_str=desde_str,
        hasta_str=hasta_str,
        candidatas=paginado
    )


@app.route('/auto_actualizar_estados', methods=['GET'])
def auto_actualizar_estados():
    pendientes = Candidata.query.filter_by(estado='inscrita_incompleta').all()
    actualizadas = []

    for c in pendientes:
        if (c.codigo and c.entrevista and c.referencias_laboral and c.referencias_familiares
            and c.perfil and c.cedula1 and c.cedula2 and c.depuracion):
            c.estado = 'lista_para_trabajar'
            c.fecha_cambio_estado = datetime.utcnow()
            c.usuario_cambio_estado = 'sistema'
            actualizadas.append(c.fila)

    if actualizadas:
        db.session.commit()

    return jsonify({'conteo_actualizadas': len(actualizadas),
                    'filas_actualizadas': actualizadas})

# -----------------------------------------------------------------------------
# LLAMADAS CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas/llamadas')
@roles_required('admin','secretaria')
def listado_llamadas_candidatas():
    q               = request.args.get('q', '', type=str)
    period          = request.args.get('period', 'all')
    start_date_str  = request.args.get('start_date', None)
    page            = request.args.get('page', 1, type=int)

    start_dt, end_dt = get_date_bounds(period, start_date_str)

    calls_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label('cid'),
            func.count(LlamadaCandidata.id).label('num_calls'),
            func.max(LlamadaCandidata.fecha_llamada).label('last_call')
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

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

    def section(estado):
        qsec = base_q.filter(Candidata.estado == estado)
        if start_dt and end_dt:
            qsec = qsec.filter(
                cast(Candidata.marca_temporal, db.Date) >= start_dt,
                cast(Candidata.marca_temporal, db.Date) <= end_dt
            )
        return qsec.order_by(calls_subq.c.last_call.asc().nullsfirst())\
                   .paginate(page=page, per_page=10, error_out=False)

    en_proceso     = section('en_proceso')
    en_inscripcion = section('proceso_inscripcion')
    lista_trabajar = section('lista_para_trabajar')

    return render_template('llamadas_candidatas.html',
                           q=q,
                           period=period,
                           start_date=start_date_str,
                           en_proceso=en_proceso,
                           en_inscripcion=en_inscripcion,
                           lista_trabajar=lista_trabajar)

@app.route('/candidatas/<int:fila>/llamar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def registrar_llamada_candidata(fila):
    candidata = Candidata.query.get_or_404(fila)
    form      = LlamadaCandidataForm()

    if form.validate_on_submit():
        minutos  = form.duracion_minutos.data
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

    return render_template('registrar_llamada_candidata.html',
                           form=form,
                           candidata=candidata)

@app.route('/candidatas/llamadas/reporte')
@roles_required('admin')
def reporte_llamadas_candidatas():
    period         = request.args.get('period', 'week')
    start_date_str = request.args.get('start_date', None)
    start_dt       = get_start_date(period, start_date_str)
    hoy            = date.today()
    page           = request.args.get('page', 1, type=int)

    stats_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label('cid'),
            func.count(LlamadaCandidata.id).label('num_calls'),
            func.max(LlamadaCandidata.fecha_llamada).label('last_call')
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

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

    def paginate_estado(estado):
        qy = base_q.filter(Candidata.estado == estado)
        if start_dt:
            qy = qy.filter(
                or_(
                    stats_subq.c.last_call == None,
                    cast(stats_subq.c.last_call, db.Date) < start_dt
                )
            )
        return qy.order_by(cast(stats_subq.c.last_call, db.Date).desc().nullsfirst())\
                 .paginate(page=page, per_page=10, error_out=False)

    estancadas_en_proceso  = paginate_estado('en_proceso')
    estancadas_inscripcion = paginate_estado('proceso_inscripcion')
    estancadas_lista       = paginate_estado('lista_para_trabajar')

    calls_query    = db.session.query(
                         LlamadaCandidata.candidata_id,
                         func.count().label('cnt')
                     ).group_by(LlamadaCandidata.candidata_id).all()
    total_calls    = sum(c.cnt for c in calls_query)
    num_with_calls = len(calls_query)
    promedio       = round(total_calls / num_with_calls, 1) if num_with_calls else 0

    calls_q = db.session.query(LlamadaCandidata).order_by(LlamadaCandidata.fecha_llamada.desc())
    if start_dt:
        start_dt_dt = datetime.combine(start_dt, datetime.min.time())
        calls_q = calls_q.filter(LlamadaCandidata.fecha_llamada >= start_dt_dt)
    calls_period = calls_q.all()

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

    return render_template('reporte_llamadas.html',
                           period=period,
                           start_date=start_date_str,
                           hoy=hoy,
                           estancadas_en_proceso=estancadas_en_proceso,
                           estancadas_inscripcion=estancadas_inscripcion,
                           estancadas_lista=estancadas_lista,
                           promedio=promedio,
                           calls_period=calls_period,
                           calls_by_day=calls_by_day,
                           calls_by_week=calls_by_week,
                           calls_by_month=calls_by_month)

# ─────────────────────────────────────────────────────────────
# SECRETARÍAS – mismas plantillas con GUION BAJO (sin textos fijos Modalidad:/Hogar:)
# ─────────────────────────────────────────────────────────────
from datetime import date, datetime
from flask import request, render_template, url_for, jsonify, flash, redirect
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload, load_only

# from app import app, db
# from models import Solicitud, Reemplazo
# from admin.forms import AdminSolicitudForm
# from decorators import roles_required

# ── Helpers (una sola vez) ───────────────────────────────────
def _as_list(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return list(val)
    try:
        return [x.strip() for x in str(val).split(',') if x.strip()]
    except Exception:
        return []

def _fmt_banos(v):
    if v is None or v == "":
        return ""
    return str(v).rstrip('0').rstrip('.') if isinstance(v, float) else str(v)

def _norm_area(s):
    return (s or "").strip()

def _s(v):
    return "" if v is None else str(v).strip()

# ─────────────────────────────────────────────────────────────
# PUBLICAR HOY (listado para copiar+marcar) – usa template: secretarias_solicitudes_copiar.html
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/copiar', methods=['GET'])
@roles_required('admin', 'secretaria')
def secretarias_copiar_solicitudes():
    """
    Lista solicitudes copiables. En el texto:
    - NO imprime 'Modalidad:' ni 'Hogar:' como etiqueta.
    - Si hay modalidad, imprime SOLO el valor en una línea.
    - Si hay descripción de hogar, imprime SOLO la descripción (sin prefijo).
    """
    hoy = date.today()

    base_q = (
        Solicitud.query
        .options(joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new))
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(or_(Solicitud.last_copiado_at.is_(None),
                    func.date(Solicitud.last_copiado_at) < hoy))
        .order_by(Solicitud.fecha_solicitud.desc())
    )

    raw_sols = base_q.all()

    # Mapear funciones code->label (igual que admin)
    form = AdminSolicitudForm()
    FUNCIONES_CHOICES = dict(form.funciones.choices)

    solicitudes = []
    for s in raw_sols:
        # Funciones (labels + otro)
        funcs = []
        try:
            seleccion = set(_as_list(s.funciones))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == 'otro':
                continue
            label = FUNCIONES_CHOICES.get(code)
            if label:
                funcs.append(label)
        if getattr(s, 'funciones_otro', None):
            custom = str(s.funciones_otro).strip()
            if custom:
                funcs.append(custom)

        # Adultos / Niños / Mascota
        adultos = s.adultos or ""
        ninos_line = ""
        if getattr(s, 'ninos', None):
            ninos_line = f"Niños: {s.ninos}"
            if getattr(s, 'edades_ninos', None):
                ninos_line += f" ({s.edades_ninos})"
        mascota_val = (getattr(s, 'mascota', None) or '').strip()
        mascota_line = f"Mascota: {mascota_val}" if mascota_val else ""

        # Modalidad (solo valor)
        modalidad_val = (
            getattr(s, 'modalidad_trabajo', None)
            or getattr(s, 'modalidad', None)
            or getattr(s, 'tipo_modalidad', None)
            or ''
        ).strip()

        # Hogar (armar descripción; sin prefijo)
        hogar_partes = []
        if getattr(s, 'habitaciones', None):
            hogar_partes.append(f"{s.habitaciones} habitaciones")
        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes.append(f"{banos_txt} baños")
        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes.append("2 pisos")
        areas = []
        if getattr(s, 'areas_comunes', None):
            try:
                for a in s.areas_comunes:
                    a = str(a).strip()
                    if a:
                        areas.append(_norm_area(a))
            except Exception:
                pass
        area_otro = (getattr(s, 'area_otro', None) or "").strip()
        if area_otro:
            areas.append(_norm_area(area_otro))
        if areas:
            hogar_partes.append(", ".join(areas))
        tipo_lugar = (getattr(s, 'tipo_lugar', "") or "").strip()
        if tipo_lugar and hogar_partes:
            hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes)}"
        elif tipo_lugar:
            hogar_descr = tipo_lugar
        else:
            hogar_descr = ", ".join(hogar_partes)
        hogar_val = hogar_descr.strip() if hogar_descr else ""

        # Edad requerida
        if isinstance(s.edad_requerida, (list, tuple, set)):
            edad_req = ", ".join([str(x).strip() for x in s.edad_requerida if str(x).strip()])
        else:
            edad_req = s.edad_requerida or ""

        nota_cli  = (s.nota_cliente or "").strip()
        nota_line = f"Nota: {nota_cli}" if nota_cli else ""
        sueldo_txt = f"Sueldo: ${_s(s.sueldo)} mensual{', más ayuda del pasaje' if bool(getattr(s, 'pasaje_aporte', False)) else ', pasaje incluido'}"

        # ===== Texto final (sin etiquetas fijas) =====
        lines = [
            f"Disponible ( {s.codigo_solicitud or ''} )",
            f"📍 {s.ciudad_sector or ''}",
            f"Ruta más cercana: {s.rutas_cercanas or ''}",
            "",
        ]
        if modalidad_val:
            lines += [modalidad_val, ""]   # ← solo el valor, sin "Modalidad:"

        lines += [
            f"Edad: {edad_req}",
            "Dominicana",
            "Que sepa leer y escribir",
            f"Experiencia en: {s.experiencia or ''}",
            f"Horario: {s.horario or ''}",
            "",
            f"Funciones: {', '.join(funcs)}" if funcs else "Funciones: ",
        ]
        if hogar_val:
            lines += ["", hogar_val]       # ← solo la descripción, sin "Hogar:"

        lines += ["", f"Adultos: {adultos}"]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)
        lines += ["", sueldo_txt]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()

        solicitudes.append({
            "id": s.id,
            "codigo_solicitud": _s(s.codigo_solicitud),
            "ciudad_sector": _s(s.ciudad_sector),
            "modalidad": modalidad_val,          # se muestra en la tabla
            "copiada_hoy": False,
            "order_text": order_text,            # sin etiquetas fijas
        })

    return render_template(
        'secretarias_solicitudes_copiar.html',
        solicitudes=solicitudes,
        q="", q_enabled=False,
        endpoint='secretarias_copiar_solicitudes'
    )

# ─────────────────────────────────────────────────────────────
# COPIAR Y MARCAR (POST)
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/<int:id>/copiar', methods=['POST'])
@roles_required('admin', 'secretaria')
def secretarias_copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    s.last_copiado_at = func.now()
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "id": id, "codigo": _s(s.codigo_solicitud)}), 200

    flash(f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.', 'success')
    return redirect(url_for('secretarias_copiar_solicitudes'))

# ─────────────────────────────────────────────────────────────
# BUSCAR (paginado + filtros) – usa template: secretarias_solicitudes_buscar.html
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/buscar', methods=['GET'])
@roles_required('admin', 'secretaria')
def secretarias_buscar_solicitudes():
    q           = (request.args.get('q') or '').strip()
    estado      = (request.args.get('estado') or '').strip()
    desde_str   = (request.args.get('desde') or '').strip()
    hasta_str   = (request.args.get('hasta') or '').strip()
    modalidad   = (request.args.get('modalidad') or '').strip()
    mascota     = (request.args.get('mascota') or '').strip()      # '', 'si', 'no'
    con_ninos   = (request.args.get('con_ninos') or '').strip()    # '', 'si', 'no'
    page        = max(1, request.args.get('page', type=int, default=1))
    per_page    = min(100, max(10, request.args.get('per_page', type=int, default=20)))

    cols = (
        Solicitud.id,
        Solicitud.fecha_solicitud,
        Solicitud.codigo_solicitud,
        Solicitud.ciudad_sector,
        Solicitud.rutas_cercanas,
        Solicitud.modalidad_trabajo,
        Solicitud.modalidad,
        Solicitud.tipo_modalidad,
        Solicitud.edad_requerida,
        Solicitud.experiencia,
        Solicitud.horario,
        Solicitud.funciones,
        Solicitud.funciones_otro,
        Solicitud.adultos,
        Solicitud.ninos,
        Solicitud.edades_ninos,
        Solicitud.mascota,
        Solicitud.tipo_lugar,
        Solicitud.habitaciones,
        Solicitud.banos,
        Solicitud.dos_pisos,
        Solicitud.areas_comunes,
        Solicitud.area_otro,
        Solicitud.direccion,
        Solicitud.sueldo,
        Solicitud.pasaje_aporte,
        Solicitud.nota_cliente,
        Solicitud.last_copiado_at,
        Solicitud.estado,
    )

    qy = (
        db.session.query(Solicitud)
        .options(load_only(*cols))
        .execution_options(stream_results=True)
    )

    if q:
        like = f"%{q}%"
        qy = qy.filter(or_(
            Solicitud.codigo_solicitud.ilike(like),
            Solicitud.ciudad_sector.ilike(like)
        ))

    if estado:
        qy = qy.filter(Solicitud.estado == estado)
    if modalidad:
        qy = qy.filter(or_(
            Solicitud.modalidad_trabajo.ilike(f"%{modalidad}%"),
            Solicitud.modalidad.ilike(f"%{modalidad}%"),
            Solicitud.tipo_modalidad.ilike(f"%{modalidad}%"),
        ))

    if mascota == 'si':
        qy = qy.filter(Solicitud.mascota.isnot(None), func.length(func.trim(Solicitud.mascota)) > 0)
    elif mascota == 'no':
        qy = qy.filter(or_(Solicitud.mascota.is_(None), func.length(func.trim(Solicitud.mascota)) == 0))

    if con_ninos == 'si':
        qy = qy.filter(Solicitud.ninos.isnot(None), Solicitud.ninos > 0)
    elif con_ninos == 'no':
        qy = qy.filter(or_(Solicitud.ninos.is_(None), Solicitud.ninos == 0))

    def _parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None

    desde_dt = _parse_date(desde_str)
    hasta_dt = _parse_date(hasta_str)
    if desde_dt and hasta_dt:
        hasta_end = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        qy = qy.filter(and_(Solicitud.fecha_solicitud >= desde_dt,
                            Solicitud.fecha_solicitud <= hasta_end))
    elif desde_dt:
        qy = qy.filter(Solicitud.fecha_solicitud >= desde_dt)
    elif hasta_dt:
        hasta_end = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        qy = qy.filter(Solicitud.fecha_solicitud <= hasta_end)

    order_col = getattr(Solicitud, 'fecha_solicitud', None) or Solicitud.id
    qy = qy.order_by(order_col.desc())

    try:
        paginado = qy.paginate(page=page, per_page=per_page, error_out=False)
    except AttributeError:
        paginado = db.paginate(qy, page=page, per_page=per_page, error_out=False)

    # Mapear funciones code->label (como admin)
    form = AdminSolicitudForm()
    FUNCIONES_CHOICES = dict(form.funciones.choices)

    items = []
    for s in paginado.items:
        modalidad_val = ((s.modalidad_trabajo or s.modalidad or s.tipo_modalidad or '')).strip()

        funcs = []
        try:
            seleccion = set(_as_list(s.funciones))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == 'otro':
                continue
            label = FUNCIONES_CHOICES.get(code)
            if label:
                funcs.append(label)
        if getattr(s, 'funciones_otro', None):
            custom = str(s.funciones_otro).strip()
            if custom:
                funcs.append(custom)

        adultos = s.adultos or ""
        ninos_line = ""
        if getattr(s, 'ninos', None):
            ninos_line = f"Niños: {s.ninos}"
            if getattr(s, 'edades_ninos', None):
                ninos_line += f" ({s.edades_ninos})"
        mascota_val = (getattr(s, 'mascota', None) or '').strip()
        mascota_line = f"Mascota: {mascota_val}" if mascota_val else ""

        # Hogar (armado; solo valor)
        hogar_partes = []
        if getattr(s, 'habitaciones', None):
            hogar_partes.append(f"{s.habitaciones} habitaciones")
        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes.append(f"{banos_txt} baños")
        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes.append("2 pisos")
        areas = []
        if getattr(s, 'areas_comunes', None):
            try:
                for a in s.areas_comunes:
                    a = str(a).strip()
                    if a:
                        areas.append(_norm_area(a))
            except Exception:
                pass
        area_otro = (getattr(s, 'area_otro', None) or "").strip()
        if area_otro:
            areas.append(_norm_area(area_otro))
        if areas:
            hogar_partes.append(", ".join(areas))
        tipo_lugar = (getattr(s, 'tipo_lugar', "") or "").strip()
        if tipo_lugar and hogar_partes:
            hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes)}"
        elif tipo_lugar:
            hogar_descr = tipo_lugar
        else:
            hogar_descr = ", ".join(hogar_partes)
        hogar_val = hogar_descr.strip() if hogar_descr else ""

        if isinstance(s.edad_requerida, (list, tuple, set)):
            edad_req = ", ".join([str(x).strip() for x in s.edad_requerida if str(x).strip()])
        else:
            edad_req = s.edad_requerida or ""

        nota_cli  = (s.nota_cliente or "").strip()
        nota_line = f"Nota: {nota_cli}" if nota_cli else ""
        sueldo_txt = f"Sueldo: ${_s(s.sueldo)} mensual{', más ayuda del pasaje' if bool(getattr(s, 'pasaje_aporte', False)) else ', pasaje incluido'}"

        # ===== Texto final (sin etiquetas fijas) =====
        lines = [
            f"Disponible ( {s.codigo_solicitud or ''} )",
            f"📍 {s.ciudad_sector or ''}",
            f"Ruta más cercana: {s.rutas_cercanas or ''}",
            "",
        ]
        if modalidad_val:
            lines += [modalidad_val, ""]   # ← solo valor, sin "Modalidad:"

        lines += [
            f"Edad: {edad_req}",
            "Dominicana",
            "Que sepa leer y escribir",
            f"Experiencia en: {s.experiencia or ''}",
            f"Horario: {s.horario or ''}",
            "",
            f"Funciones: {', '.join(funcs)}" if funcs else "Funciones: ",
        ]
        if hogar_val:
            lines += ["", hogar_val]       # ← solo descripción, sin "Hogar:"

        lines += ["", f"Adultos: {adultos}"]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)
        lines += ["", sueldo_txt]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()

        items.append({
            "id": s.id,
            "codigo_solicitud": _s(s.codigo_solicitud),
            "ciudad_sector": _s(s.ciudad_sector),
            "modalidad": modalidad_val,   # se muestra en la tabla
            "estado": _s(s.estado),
            "fecha_solicitud": s.fecha_solicitud.strftime("%Y-%m-%d %H:%M") if s.fecha_solicitud else "",
            "copiada_ciclo": (s.last_copiado_at is not None),
            "order_text": order_text,     # sin etiquetas fijas
        })

    current_params = request.args.to_dict(flat=True)
    def page_url(p):
        d = current_params.copy()
        d['page'] = p
        return url_for('secretarias_buscar_solicitudes') + ('?' + urlencode(d) if d else '')

    total_pages = paginado.pages or 1
    page_links = [{"n": p, "url": page_url(p), "active": (p == paginado.page)} for p in range(1, total_pages + 1)]
    prev_url = page_url(paginado.page - 1) if paginado.page > 1 else None
    next_url = page_url(paginado.page + 1) if paginado.page < total_pages else None

    return render_template(
        'secretarias_solicitudes_buscar.html',
        items=items,
        page=paginado.page,
        pages=total_pages,
        total=paginado.total,
        per_page=per_page,
        q=q,
        estado=estado,
        estados_opts=['proceso','activa','pagada','cancelada','reemplazo'],
        desde=desde_str,
        hasta=hasta_str,
        modalidad=modalidad,
        mascota=mascota,
        con_ninos=con_ninos,
        page_links=page_links,
        prev_url=prev_url,
        next_url=next_url
    )

# --- Registro público de candidatas -----------------------------------------
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from config_app import db, csrf, normalize_cedula
from models import Candidata


def _safe_dispose_pool():
    """Libera conexiones del pool por si hubo un corte SSL."""
    try:
        engine = db.get_engine()
        engine.dispose()
    except Exception:
        pass


@app.route('/registro', methods=['GET', 'POST'])
@app.route('/registro_publico', methods=['GET', 'POST'])
def registro_publico():
    """
    Formulario público de registro de candidatas.
    - GET  -> muestra el formulario
    - POST -> valida y guarda en la tabla `candidatas`
    """
    if request.method == 'GET':
        return render_template('registro_publico.html')

    # --- POST: recoger datos del formulario ---
    nombre       = (request.form.get('nombre_completo') or '').strip()
    edad_raw     = (request.form.get('edad') or '').strip()
    telefono     = (request.form.get('numero_telefono') or '').strip()
    direccion    = (request.form.get('direccion_completa') or '').strip()
    modalidad    = (request.form.get('modalidad_trabajo_preferida') or '').strip()
    rutas        = (request.form.get('rutas_cercanas') or '').strip()
    empleo_prev  = (request.form.get('empleo_anterior') or '').strip()
    anos_exp     = (request.form.get('anos_experiencia') or '').strip()
    areas_list   = request.form.getlist('areas_experiencia')  # checkboxes
    planchar_raw = (request.form.get('sabe_planchar') or '').strip().lower()
    ref_lab      = (request.form.get('contactos_referencias_laborales') or '').strip()
    ref_fam      = (request.form.get('referencias_familiares_detalle') or '').strip()
    acepta_raw   = (request.form.get('acepta_porcentaje_sueldo') or '').strip()
    cedula_raw   = (request.form.get('cedula') or '').strip()

    # --- Validaciones mínimas y mensajes claros ---
    faltantes = []
    for campo, valor in [
        ("Nombre completo", nombre),
        ("Edad", edad_raw),
        ("Número de teléfono", telefono),
        ("Dirección completa", direccion),
        ("Modalidad de trabajo", modalidad),
        ("Rutas cercanas", rutas),
        ("Empleo anterior", empleo_prev),
        ("Años de experiencia", anos_exp),
        ("Referencias laborales", ref_lab),
        ("Referencias familiares", ref_fam),
        ("Cédula", cedula_raw),
    ]:
        if not valor:
            faltantes.append(campo)

    if not planchar_raw in ('si', 'no'):
        faltantes.append("Sabe planchar (sí/no)")

    if not acepta_raw in ('1', '0'):
        faltantes.append("Acepta % de sueldo (sí/no)")

    # Edad razonable (no forzamos entero en DB, pero validamos)
    try:
        edad_num = int(''.join(ch for ch in edad_raw if ch.isdigit()))
        if edad_num < 16 or edad_num > 75:
            flash("📛 La edad debe estar entre 16 y 75 años.", "warning")
            return render_template('registro_publico.html'), 400
    except ValueError:
        faltantes.append("Edad (número)")

    cedula_norm = normalize_cedula(cedula_raw)
    if not cedula_norm:
        flash("📛 Cédula inválida. Debe contener 11 dígitos.", "warning")
        return render_template('registro_publico.html'), 400

    if faltantes:
        flash("Por favor completa: " + ", ".join(faltantes), "warning")
        return render_template('registro_publico.html'), 400

    # Convertir/normalizar algunos valores
    areas_str     = ', '.join(areas_list) if areas_list else ''
    sabe_planchar = True if planchar_raw == 'si' else False
    acepta_pct    = True if acepta_raw == '1' else False

    # --- Comprobación de duplicado por cédula ---
    try:
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()
    except OperationalError:
        # Posible "SSL bad record mac"; limpiamos pool y reintentamos una vez
        _safe_dispose_pool()
        db.session.rollback()
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()

    if dup:
        flash("⚠️ Ya existe una candidata registrada con esta cédula.", "warning")
        return render_template('registro_publico.html'), 400

    # --- Crear objeto y guardar ---
    nueva = Candidata(
        marca_temporal            = datetime.utcnow(),
        nombre_completo           = nombre,
        edad                      = str(edad_num),
        numero_telefono           = telefono,
        direccion_completa        = direccion,
        modalidad_trabajo_preferida = modalidad,
        rutas_cercanas            = rutas,
        empleo_anterior           = empleo_prev,
        anos_experiencia          = anos_exp,
        areas_experiencia         = areas_str,
        sabe_planchar             = sabe_planchar,
        contactos_referencias_laborales = ref_lab,
        referencias_familiares_detalle  = ref_fam,
        acepta_porcentaje_sueldo  = acepta_pct,
        cedula                    = cedula_norm,
        medio_inscripcion         = "Web",
        estado                    = "en_proceso",
        fecha_cambio_estado       = datetime.utcnow(),
        usuario_cambio_estado     = "registro_publico",
    )

    try:
        db.session.add(nueva)
        db.session.commit()
    except OperationalError as e:
        # reconectar y reintentar una vez
        _safe_dispose_pool()
        db.session.rollback()
        try:
            db.session.add(nueva)
            db.session.commit()
        except Exception as e2:
            db.session.rollback()
            flash("❌ Problema momentáneo con la conexión. Intenta de nuevo en unos segundos.", "danger")
            return render_template('registro_publico.html'), 503
    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f"❌ No se pudo guardar el registro: {e.__class__.__name__}", "danger")
        return render_template('registro_publico.html'), 500

    flash("✅ ¡Registro enviado! Te contactaremos por WhatsApp en breve.", "success")
    return redirect(url_for('registro_publico'))
# --- Fin registro público ----------------------------------------------------

# ==== FINALIZAR PROCESO + PERFIL (con vuelta SIEMPRE al BUSCADOR) ====
from flask import (
    request, render_template, redirect, url_for, flash, abort,
    current_app, session, send_file
)
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only
from datetime import datetime
from io import BytesIO
import json

# from config_app import db
# from decorators import roles_required
# from models import Candidata


def _cfg_grupos_empleo():
    default = [
        "Interna", "Dormir Adentro", "Dormir Afuera",
        "Niñera", "Cuidadora", "Limpieza", "Cocinera",
        "Por Días", "Tiempo Completo", "Medio Tiempo"
    ]
    try:
        return current_app.config.get('GRUPOS_EMPLEO', default)
    except Exception:
        return default

def _set_bytes_attr_safe(obj, attr_name, data):
    if hasattr(obj, attr_name):
        setattr(obj, attr_name, data)
        return True
    return False

def _save_grupos_empleo_safe(candidata, grupos_list):
    saved = False
    if hasattr(candidata, 'grupos_empleo'):
        try:
            candidata.grupos_empleo = grupos_list
            saved = True
        except Exception:
            pass
    if not saved and hasattr(candidata, 'grupos'):
        try:
            candidata.grupos = grupos_list
            saved = True
        except Exception:
            pass
    if not saved and hasattr(candidata, 'grupos_empleo_json'):
        try:
            candidata.grupos_empleo_json = json.dumps(grupos_list, ensure_ascii=False)
            saved = True
        except Exception:
            pass
    return saved


# ---------- BUSCADOR (punto central de ida y vuelta) ----------
@app.route('/finalizar_proceso/buscar', methods=['GET'])
@roles_required('admin', 'secretaria')
def finalizar_proceso_buscar():
    q = (request.args.get('q') or '').strip()
    resultados = []
    if q:
        like = f"%{q}%"
        resultados = (
            Candidata.query
            .options(load_only(
                Candidata.fila, Candidata.nombre_completo, Candidata.cedula,
                Candidata.estado, Candidata.codigo
            ))
            .filter(or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            ))
            .order_by(Candidata.nombre_completo.asc())
            .limit(300)
            .all()
        )
    return render_template('finalizar_proceso_buscar.html', q=q, resultados=resultados)


# ---------- FORMULARIO FINALIZAR ----------
@app.route('/finalizar_proceso', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def finalizar_proceso():
    fila = request.values.get('fila', type=int)
    if not fila:
        flash("Falta el parámetro ?fila=<id>.", "warning")
        return redirect(url_for('finalizar_proceso_buscar'))

    candidata = Candidata.query.get(fila)
    if not candidata:
        abort(404, description=f"No existe la candidata con fila={fila}")

    grupos = _cfg_grupos_empleo()

    if request.method == 'GET':
        return render_template('finalizar_proceso.html', candidata=candidata, grupos=grupos)

    # POST: validar archivos obligatorios
    foto_perfil_file = request.files.get('foto_perfil')
    cedula1_file     = request.files.get('cedula1')
    cedula2_file     = request.files.get('cedula2')

    faltan = []
    if not foto_perfil_file or foto_perfil_file.filename == '':
        faltan.append("Foto de perfil")
    if not cedula1_file or cedula1_file.filename == '':
        faltan.append("Cédula (frontal)")
    if not cedula2_file or cedula2_file.filename == '':
        faltan.append("Cédula (reverso)")

    if faltan:
        flash("Faltan archivos: " + ", ".join(faltan) + ".", "danger")
        return render_template('finalizar_proceso.html', candidata=candidata, grupos=grupos)

    # Leer bytes
    try:
        foto_perfil_bytes = foto_perfil_file.read()
        cedula1_bytes     = cedula1_file.read()
        cedula2_bytes     = cedula2_file.read()
    except Exception as e:
        flash(f"Error leyendo archivos: {e}", "danger")
        return render_template('finalizar_proceso.html', candidata=candidata, grupos=grupos)

    # Guardar bytes
    ok_foto = _set_bytes_attr_safe(candidata, 'foto_perfil', foto_perfil_bytes) or \
              _set_bytes_attr_safe(candidata, 'perfil', foto_perfil_bytes)
    ok_ced1 = _set_bytes_attr_safe(candidata, 'cedula1', cedula1_bytes)
    ok_ced2 = _set_bytes_attr_safe(candidata, 'cedula2', cedula2_bytes)

    if not (ok_foto and ok_ced1 and ok_ced2):
        detalles = []
        if not ok_foto: detalles.append("foto_perfil (o perfil) no existe en el modelo")
        if not ok_ced1: detalles.append("cedula1 no existe en el modelo")
        if not ok_ced2: detalles.append("cedula2 no existe en el modelo")
        flash("No se pudieron guardar algunos campos binarios: " + "; ".join(detalles), "warning")

    # Grupos (opcional)
    grupos_sel = request.form.getlist('grupos_empleo')
    if grupos_sel:
        if not _save_grupos_empleo_safe(candidata, grupos_sel):
            flash("No se encontró columna para guardar los grupos (grupos_empleo / grupos / grupos_empleo_json).", "warning")

    # Estado si están los 3 archivos
    try:
        tiene_foto = bool(getattr(candidata, 'foto_perfil', None) or getattr(candidata, 'perfil', None))
        tiene_ced1 = bool(getattr(candidata, 'cedula1', None))
        tiene_ced2 = bool(getattr(candidata, 'cedula2', None))
        if tiene_foto and tiene_ced1 and tiene_ced2 and hasattr(candidata, 'estado'):
            candidata.estado = 'lista_para_trabajar'
            if hasattr(candidata, 'fecha_cambio_estado'):
                candidata.fecha_cambio_estado = datetime.utcnow()
            if hasattr(candidata, 'usuario_cambio_estado'):
                candidata.usuario_cambio_estado = session.get('usuario', 'sistema')
    except Exception:
        pass

    try:
        db.session.commit()
        flash("✅ Proceso finalizado y datos guardados correctamente.", "success")
        # Al terminar, te llevo al PERFIL mejorado (con botón 'Volver a buscar')
        return redirect(url_for('candidata_ver_perfil', fila=candidata.fila))
    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f"❌ Error guardando en la base de datos: {e}", "danger")
        return render_template('finalizar_proceso.html', candidata=candidata, grupos=grupos)


# ---------- PERFIL (HTML) ----------

@app.route('/candidata/perfil', methods=['GET'], endpoint='candidata_ver_perfil')
@roles_required('admin', 'secretaria')
def ver_perfil():
    """
    Perfil detallado de candidata. Usa carga con retry para evitar caídas por SSL.
    """
    fila = request.args.get('fila', type=int)
    if fila is None:
        abort(400, description="Falta el parámetro ?fila=<id>.")

    try:
        candidata = _get_candidata_safe_by_pk(fila)
    except Exception:
        current_app.logger.exception("Error consultando Candidata.fila=%s", fila)
        abort(500, description="Error consultando la base de datos.")

    if not candidata:
        abort(404, description=f"No existe la candidata con fila={fila}")

    # Normaliza grupos (por si vienen como string/JSON)
    grupos = getattr(candidata, 'grupos_empleo', None)
    if isinstance(grupos, str):
        try:
            parsed = json.loads(grupos)
            grupos = parsed if isinstance(parsed, list) else [str(parsed)]
        except Exception:
            grupos = [g.strip() for g in grupos.split(',') if g.strip()] if grupos else []
    elif grupos is None:
        alt = getattr(candidata, 'grupos', None) or getattr(candidata, 'grupos_empleo_json', None)
        if isinstance(alt, str):
            try:
                parsed = json.loads(alt)
                grupos = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                grupos = [g.strip() for g in alt.split(',') if g.strip()] if alt else []
        else:
            grupos = alt or []

    tiene_foto = bool(getattr(candidata, 'foto_perfil', None) or getattr(candidata, 'perfil', None))
    tiene_ced1 = bool(getattr(candidata, 'cedula1', None))
    tiene_ced2 = bool(getattr(candidata, 'cedula2', None))

    return render_template(
        'candidata_perfil.html',
        candidata=candidata,
        tiene_foto=tiene_foto,
        tiene_ced1=tiene_ced1,
        tiene_ced2=tiene_ced2,
        grupos=grupos
    )

@app.route('/perfil_candidata', methods=['GET'])
@roles_required('admin', 'secretaria')
def perfil_candidata():
    """
    Sirve la imagen de perfil (bytes) con ruta más robusta:
    - Lee directo con engine.connect() y text(), con retry.
    - Si no hay imagen, 404.
    """
    fila = request.args.get('fila', type=int)
    if not fila:
        abort(400, description="Falta el parámetro ?fila=<id>.")

    try:
        img_bytes = _fetch_image_bytes_safe(fila)
    except Exception:
        current_app.logger.exception("Error leyendo imagen de Candidata.fila=%s", fila)
        abort(500, description="No se pudo leer la imagen.")

    if not img_bytes:
        abort(404, description="La candidata no tiene foto almacenada.")

    bio = BytesIO(img_bytes)
    bio.seek(0)
    # No forzamos PNG; el navegador lo abre igual, pero dejamos mimetype 'image/jpeg' por defecto
    # Si sabes el formato, cámbialo. Si no, usa 'application/octet-stream'.
    return send_file(
        bio,
        mimetype='image/jpeg',
        as_attachment=False,
        download_name=f"perfil_{fila}.jpg",
        max_age=0
    )
# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
