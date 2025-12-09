# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import io
import os
import re
import json
import logging
import unicodedata
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

import requests  # HTTP externo (si lo usas en otras partes)

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, send_from_directory, flash, jsonify,
    current_app, abort
)

from flask_login import login_user, logout_user, current_user

from flask_caching import Cache   # si no lo usas directo aquí, igual se puede dejar
from flask_migrate import Migrate

# SQLAlchemy
from sqlalchemy import or_, cast, String, func, and_
from sqlalchemy.orm import subqueryload, joinedload, load_only
from sqlalchemy.exc import OperationalError, IntegrityError, DBAPIError
from sqlalchemy.sql import text

# 🔐 HASH DE CONTRASEÑAS
from werkzeug.security import generate_password_hash, check_password_hash

# App factory / DB / CSRF / usuarios en memoria
from config_app import create_app, db, csrf, USUARIOS

# Decoradores
from decorators import roles_required, admin_required

# Modelos
from models import (
    Candidata,
    LlamadaCandidata,
    CandidataWeb,
    Solicitud,
    Reemplazo,
)

# Formularios
from forms import LlamadaCandidataForm


# PDF (fpdf2)
try:
    from fpdf import FPDF  # fpdf2
except Exception:
    FPDF = None


# -----------------------------------------------------------------------------
# APP BOOT
# -----------------------------------------------------------------------------
app = create_app()
cache = Cache(app)
migrate = Migrate(app, db)

# Helper para verificar si un endpoint existe (usable desde Jinja)
app.jinja_env.globals['has_endpoint'] = lambda name: name in app.view_functions

def url_for_safe(endpoint: str, **values):
    """url_for que no rompe si el endpoint no existe."""
    return url_for(endpoint, **values) if endpoint in app.view_functions else None

app.jinja_env.globals['url_for_safe'] = url_for_safe

@app.teardown_appcontext
def _shutdown_session(exception=None):
    """Cierra/limpia la sesión SIEMPRE al final del request."""
    try:
        db.session.remove()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# USUARIOS DE SESIÓN SENCILLA (panel interno)
#  Nota: idealmente migrar a tabla/ORM + passwords via gestión real de usuarios.
# -----------------------------------------------------------------------------
USUARIOS = {
    "Cruz":    {"pwd": generate_password_hash("8998"), "role": "admin"},
    "vanina": {"pwd": generate_password_hash("2424"), "role": "secretaria"},
}

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
CEDULA_PATTERN = re.compile(r'^\d{11}$')


def _get_engine():
    """Compatibilidad: usa db.engine (v3) o db.get_engine() (v2)."""
    try:
        return db.engine
    except Exception:
        return db.get_engine()


@app.errorhandler(OperationalError)
def _handle_operational_error(e):
    """
    Conexión rota (SSL/bad record mac). Limpia y devuelve 503 legible.
    No expone detalles internos.
    """
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        # cierra conexiones del pool para forzar reconexión limpia
        _get_engine().dispose()
    except Exception:
        pass
    return (
        "⚠️ Conexión a la base de datos no disponible momentáneamente. Intenta nuevamente."
    ), 503


def _db_retry(fn, *args, **kwargs):
    """
    Ejecuta fn y, si la conexión está rota (SSL / bad record mac / connection reset),
    hace remove() y reintenta UNA vez.
    """
    try:
        return fn(*args, **kwargs)
    except (OperationalError, DBAPIError) as e:
        msg = str(e).lower()
        transient = any(
            s in msg
            for s in (
                "ssl error",
                "bad record mac",
                "connection reset",
                "server closed the connection",
                "terminating connection",
                "could not receive data from server",
            )
        )
        if transient:
            try:
                db.session.remove()
            except Exception:
                pass
            return fn(*args, **kwargs)  # segundo intento
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
    engine = _get_engine()

    def _load():
        with engine.connect() as conn:
            r = conn.execute(
                text("SELECT foto_perfil FROM candidatas WHERE fila=:f"),
                {"f": fila},
            ).fetchone()
            if r and r[0]:
                return bytes(r[0])

            r2 = conn.execute(
                text("SELECT perfil FROM candidatas WHERE fila=:f"),
                {"f": fila},
            ).fetchone()
            if r2 and r2[0]:
                return bytes(r2[0])

            return None

    return _db_retry(_load)


def run_db_safely(fn, *, retry_once: bool = True, fallback=None):
    """
    Ejecuta una función que toca la DB. Si hay OperationalError (conexión rota),
    hace rollback/cierra y reintenta UNA vez.
    """
    try:
        return fn()
    except OperationalError:
        db.session.rollback()
        db.session.close()
        if retry_once:
            try:
                return fn()
            except OperationalError:
                db.session.rollback()
                db.session.close()
                return fallback
        return fallback


def normalize_cedula(raw: str):
    """Normaliza cédula a 11 dígitos con guiones. Devuelve None si no es válida."""
    digits = re.sub(r'\D', '', raw or '')
    if not CEDULA_PATTERN.fullmatch(digits):
        return None
    return f"{digits[:3]}-{digits[3:9]}-{digits[9:]}"


def normalize_nombre(raw: str) -> str:
    """Quita acentos y caracteres raros; deja letras, espacios y guiones."""
    if not raw:
        return ''
    nfkd = unicodedata.normalize('NFKD', raw)
    no_accents = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Za-z\s\-]', '', no_accents).strip()


def parse_date(s: str):
    """YYYY-MM-DD → date | None."""
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except Exception:
        return None


def parse_decimal(s: str):
    """Convierte string a Decimal (admite coma). Devuelve None si falla."""
    try:
        return Decimal((s or "").replace(',', '.'))
    except Exception:
        return None


def get_date_bounds(period: str, date_str: str | None = None):
    """
    Devuelve (start_dt, end_dt):
      - 'day'   → últimas 24h
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


def get_start_date(period: str, date_str: str | None = None):
    start, _ = get_date_bounds(period, date_str)
    return start

# -----------------------------------------------------------------------------
# CARGA CONFIG DE ENTREVISTAS (JSON local)
# -----------------------------------------------------------------------------
def load_entrevistas_config():
    """Carga config JSON local para entrevistas."""
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
    """Sirve archivos estáticos desde /static (controlado)."""
    return send_from_directory(os.path.join(app.root_path, 'static'), filename)

@app.route('/robots.txt')
def robots_txt():
    """Archivo robots.txt (si no existe, devolvería 404 estándar)."""
    return send_from_directory(app.static_folder, "robots.txt")


# -----------------------------------------------------------------------------
# AUTH (panel interno por sesión simple)
#  Nota de seguridad:
#  - Mantengo el esquema actual (USUARIOS en memoria) para no romper nada.
#  - Endurecí el login: limpio inputs, corto longitud, y roto sesión al autenticar.
#  - Si usas CSRF con Flask-WTF, asegúrate de incluir {{ csrf_token() }} en login.html.
# -----------------------------------------------------------------------------

@app.route('/home')
def home():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    # Evita UTC si tu app es local/DR; suficiente con fecha local del servidor
    return render_template(
        'home.html',
        usuario=session['usuario'],
        current_year=date.today().year
    )


from datetime import datetime
from flask import request, render_template, redirect, url_for, session
from werkzeug.security import check_password_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""

    if request.method == 'POST':
        # Lo que viene del form
        usuario_raw = (request.form.get('usuario') or '').strip()[:64]
        clave       = (request.form.get('clave')   or '').strip()[:128]

        # Intentamos varias variantes del usuario
        posibles_claves = [
            usuario_raw,
            usuario_raw.lower(),
            usuario_raw.upper(),
        ]

        user = None
        usuario_key = None

        for k in posibles_claves:
            if k in USUARIOS:
                user = USUARIOS[k]
                usuario_key = k
                break

        ok = False
        if user:
            # Acepta tanto 'pwd_hash' como 'pwd'
            stored = user.get('pwd_hash') or user.get('pwd')

            if stored:
                try:
                    # Si está hasheado, esto funciona
                    ok = check_password_hash(stored, clave)
                except Exception:
                    # Si NO es un hash, comparamos directo (texto plano)
                    ok = (stored == clave)

        if ok:
            # Login correcto
            session.clear()
            session['usuario']   = usuario_key
            session['role']      = user.get('role', 'admin')
            session['logged_at'] = datetime.utcnow().isoformat(timespec='seconds')

            return redirect(url_for('home'))

        # Si algo falla:
        mensaje = "Usuario o clave incorrectos."

    return render_template('login.html', mensaje=mensaje)




@app.route('/logout')
@roles_required('admin', 'secretaria')
def logout():
    # Limpia toda la sesión
    session.clear()
    return redirect(url_for('login'))


# -----------------------------------------------------------------------------
# CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas', methods=['GET'])
@roles_required('admin', 'secretaria')
def list_candidatas():
    # Sanitiza búsqueda y evita querys enormes
    q = (request.args.get('q') or '').strip()[:128]

    try:
        base = Candidata.query.order_by(Candidata.nombre_completo.asc())
        if q:
            like = f"%{q}%"
            base = base.filter(or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
            ))

        candidatas = safe_all(base)  # respeta tu helper actual
        return render_template('candidatas.html', candidatas=candidatas, query=q)
    except Exception:
        app.logger.exception("❌ Error listando candidatas")
        flash("Ocurrió un error al listar candidatas. Intenta de nuevo.", "danger")
        return render_template('candidatas.html', candidatas=[], query=q), 500


@app.route('/candidatas_db')
@roles_required('admin', 'secretaria')
def list_candidatas_db():
    try:
        # Cargamos solo columnas necesarias para bajar peso/riesgo
        candidatas = (Candidata.query
                      .options(load_only(
                          Candidata.fila,
                          Candidata.marca_temporal,
                          Candidata.nombre_completo,
                          Candidata.edad,
                          Candidata.numero_telefono,
                          Candidata.direccion_completa,
                          Candidata.modalidad_trabajo_preferida,
                          Candidata.cedula,
                          Candidata.codigo,
                      ))
                      .all())

        resultado = []
        for c in candidatas:
            resultado.append({
                "fila": c.fila,
                "marca_temporal": c.marca_temporal.isoformat() if getattr(c, "marca_temporal", None) else None,
                "nombre_completo": c.nombre_completo,
                "edad": c.edad,
                "numero_telefono": c.numero_telefono,
                "direccion_completa": c.direccion_completa,
                "modalidad_trabajo_preferida": c.modalidad_trabajo_preferida,
                "cedula": c.cedula,
                "codigo": c.codigo,
            })
        return jsonify({"candidatas": resultado}), 200

    except Exception:
        app.logger.exception("❌ Error leyendo candidatas desde la DB")
        # No exponemos el error real al cliente
        return jsonify({"error": "Error al consultar la base de datos."}), 500


# -----------------------------------------------------------------------------
# ENTREVISTA (usa JSON local de config)
# -----------------------------------------------------------------------------
@app.route('/entrevista', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def entrevista():
    # Sanitiza y normaliza parámetros
    tipo = (request.values.get('tipo') or '').strip().lower()[:64]
    fila = request.values.get('fila', type=int)
    config = current_app.config.get('ENTREVISTAS_CONFIG') or {}

    # Guardar respuestas
    if request.method == 'POST' and tipo and fila:
        cfg_tipo = config.get(tipo) or {}
        preguntas = cfg_tipo.get('preguntas') or []

        if not isinstance(preguntas, list) or not preguntas:
            flash("Configuración de entrevista inválida.", "danger")
            return redirect(url_for('entrevista') + f"?fila={fila}&tipo={tipo}")

        # Acumular respuestas; limitar tamaño para evitar payloads gigantes
        respuestas = []
        faltan = []
        for p in preguntas:
            pid = (p.get('id') or '').strip()
            enunciado = (p.get('enunciado') or '').strip()
            if not pid:
                continue  # pregunta mal definida en JSON

            valor = (request.form.get(pid) or '').strip()
            if not valor:
                faltan.append(pid)

            # Limita cada respuesta a 1000 chars por seguridad
            valor = valor[:1000]
            respuestas.append(f"{enunciado}: {valor}")

        if faltan:
            flash("Por favor completa todos los campos.", "warning")
            return redirect(url_for('entrevista') + f"?fila={fila}&tipo={tipo}")

        # Guardar en la fila correspondiente
        candidata = _get_candidata_safe_by_pk(fila)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('entrevista'))

        candidata.entrevista = "\n".join(respuestas)

        try:
            db.session.commit()
            flash("✅ Entrevista guardada.", "success")
        except (IntegrityError, OperationalError, DBAPIError):
            app.logger.exception("❌ Error al guardar entrevista")
            db.session.rollback()
            flash("❌ Error al guardar.", "danger")

        return redirect(url_for('entrevista') + f"?fila={fila}&tipo={tipo}")

    # Buscar candidata (sin fila)
    if not fila:
        resultados = []
        if request.method == 'POST':
            q = (request.form.get('busqueda') or '').strip()[:128]
            if q:
                like = f"%{q}%"
                try:
                    resultados = (Candidata.query
                                  .filter(or_(
                                      Candidata.nombre_completo.ilike(like),
                                      Candidata.cedula.ilike(like)
                                  ))
                                  .order_by(Candidata.nombre_completo.asc())
                                  .all())
                    if not resultados:
                        flash("⚠️ No se encontraron candidatas.", "info")
                except Exception:
                    app.logger.exception("❌ Error buscando candidatas para entrevista")
                    flash("Ocurrió un error al buscar candidatas.", "danger")
            else:
                flash("⚠️ Ingresa un término de búsqueda.", "warning")

        return render_template('entrevista.html', etapa='buscar', resultados=resultados)

    # Elegir tipo (con fila, sin tipo)
    if fila and not tipo:
        candidata = _get_candidata_safe_by_pk(fila)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('entrevista'))

        # Lista de tipos disponibles a partir del JSON
        tipos = [(k, (v or {}).get('titulo', k)) for k, v in (config.items() if isinstance(config, dict) else [])]
        return render_template('entrevista.html',
                               etapa='elegir_tipo',
                               candidata=candidata,
                               tipos=tipos)

    # Form dinámico (con fila y tipo)
    if fila and tipo:
        candidata = _get_candidata_safe_by_pk(fila)
        cfg = config.get(tipo) if isinstance(config, dict) else None

        if not cfg or not candidata:
            flash("⚠️ Parámetros inválidos.", "danger")
            return redirect(url_for('entrevista'))

        return render_template(
            'entrevista.html',
            etapa='formulario',
            candidata=candidata,
            tipo=tipo,
            preguntas=(cfg.get('preguntas') or []),
            titulo=cfg.get('titulo'),
            datos={},
            mensaje=None,
            focus_field=None
        )

    # Fallback
    return redirect(url_for('entrevista'))

# -----------------------------------------------------------------------------
# BÚSQUEDA / EDICIÓN BÁSICA
# -----------------------------------------------------------------------------
@app.route('/buscar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def buscar_candidata():
    # Sanitiza entrada y limita tamaño
    busqueda = (
        (request.form.get('busqueda') if request.method == 'POST'
         else request.args.get('busqueda')) or ''
    ).strip()[:128]

    resultados, candidata, mensaje = [], None, None

    # Guardar edición
    if request.method == 'POST' and request.form.get('guardar_edicion'):
        cid = (request.form.get('candidata_id') or '').strip()
        if cid.isdigit():
            obj = Candidata.query.get(int(cid))
            if obj:
                # Limites razonables por campo para evitar payloads enormes
                obj.nombre_completo                  = (request.form.get('nombre') or '').strip()[:150] or obj.nombre_completo
                obj.edad                             = (request.form.get('edad') or '').strip()[:10] or obj.edad
                obj.numero_telefono                  = (request.form.get('telefono') or '').strip()[:30] or obj.numero_telefono
                obj.direccion_completa               = (request.form.get('direccion') or '').strip()[:250] or obj.direccion_completa
                obj.modalidad_trabajo_preferida      = (request.form.get('modalidad') or '').strip()[:100] or obj.modalidad_trabajo_preferida
                obj.rutas_cercanas                   = (request.form.get('rutas') or '').strip()[:150] or obj.rutas_cercanas
                obj.empleo_anterior                  = (request.form.get('empleo_anterior') or '').strip()[:150] or obj.empleo_anterior
                obj.anos_experiencia                 = (request.form.get('anos_experiencia') or '').strip()[:50] or obj.anos_experiencia
                obj.areas_experiencia                = (request.form.get('areas_experiencia') or '').strip()[:200] or obj.areas_experiencia
                obj.sabe_planchar                    = (request.form.get('sabe_planchar') == 'si')
                obj.contactos_referencias_laborales  = (request.form.get('contactos_referencias_laborales') or '').strip()[:250] or obj.contactos_referencias_laborales
                obj.referencias_familiares_detalle   = (request.form.get('referencias_familiares_detalle') or '').strip()[:250] or obj.referencias_familiares_detalle
                obj.cedula                           = (request.form.get('cedula') or '').strip()[:20] or obj.cedula
                obj.acepta_porcentaje_sueldo         = 1 if request.form.get('acepta_porcentaje') else 0

                try:
                    db.session.commit()
                    flash("✅ Datos actualizados correctamente.", "success")
                    return redirect(url_for('buscar_candidata', candidata_id=cid))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("❌ Error al guardar edición de candidata")
                    mensaje = "❌ Error al guardar. Intenta de nuevo."
            else:
                mensaje = "⚠️ Candidata no encontrada."
        else:
            mensaje = "❌ ID de candidata inválido."

    # Carga detalles (GET ?candidata_id=)
    cid = (request.args.get('candidata_id') or '').strip()
    if cid.isdigit():
        candidata = Candidata.query.get(int(cid))
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."

    # ================== BÚSQUEDA ==================
    if busqueda and not candidata:
        like = f"%{busqueda}%"
        try:
            # 1) Intentar primero búsqueda EXACTA por código, flexible con espacios y mayúsculas
            codigo_normalizado = busqueda.upper()

            resultados = (
                Candidata.query
                .filter(
                    Candidata.codigo.isnot(None),
                    func.trim(func.upper(Candidata.codigo)) == codigo_normalizado
                )
                .order_by(Candidata.nombre_completo.asc())
                .all()
            )

            # 2) Si no hay match exacto por código, probamos búsqueda flexible con ILIKE normal
            if not resultados:
                filtros = [
                    Candidata.codigo.ilike(like),
                    Candidata.nombre_completo.ilike(like),
                    cast(Candidata.edad, String).ilike(like),
                    Candidata.numero_telefono.ilike(like),
                    Candidata.direccion_completa.ilike(like),
                    Candidata.modalidad_trabajo_preferida.ilike(like),
                    Candidata.rutas_cercanas.ilike(like),
                    Candidata.empleo_anterior.ilike(like),
                    Candidata.anos_experiencia.ilike(like),
                    Candidata.areas_experiencia.ilike(like),
                    Candidata.contactos_referencias_laborales.ilike(like),
                    Candidata.referencias_familiares_detalle.ilike(like),
                    Candidata.cedula.ilike(like),
                ]

                resultados = (
                    Candidata.query
                    .filter(or_(*filtros))
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )

            if not resultados:
                mensaje = "⚠️ No se encontraron coincidencias."

        except Exception:
            db.session.rollback()
            app.logger.exception("❌ Error buscando candidatas")
            mensaje = "❌ Ocurrió un error al buscar."

    return render_template(
        'buscar.html',
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje
    )


# -----------------------------------------------------------------------------
# FILTRAR
# -----------------------------------------------------------------------------
@app.route('/filtrar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def filtrar():
    # Captura de filtros desde request (limitamos longitudes)
    form_data = {
        'ciudad':            (request.values.get('ciudad') or "").strip()[:120],
        'rutas':             (request.values.get('rutas') or "").strip()[:120],
        'modalidad':         (request.values.get('modalidad') or "").strip()[:60],
        'experiencia_anos':  (request.values.get('experiencia_anos') or "").strip()[:30],
        'areas_experiencia': (request.values.get('areas_experiencia') or "").strip()[:120],
        'estado':            (request.values.get('estado') or "").strip()[:40],
    }

    filtros = []

    # Ciudad
    if form_data['ciudad']:
        ciudades = [p.strip() for p in re.split(r'[,\s]+', form_data['ciudad']) if p.strip()]
        if ciudades:
            filtros.extend([Candidata.direccion_completa.ilike(f"%{c}%") for c in ciudades])

    # Rutas
    if form_data['rutas']:
        rutas = [r.strip() for r in re.split(r'[,\s]+', form_data['rutas']) if r.strip()]
        if rutas:
            filtros.extend([Candidata.rutas_cercanas.ilike(f"%{r}%") for r in rutas])

    # Modalidad
    if form_data['modalidad']:
        filtros.append(Candidata.modalidad_trabajo_preferida.ilike(f"%{form_data['modalidad']}%"))

    # Experiencia en años
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

    # Áreas de experiencia
    if form_data['areas_experiencia']:
        filtros.append(Candidata.areas_experiencia.ilike(f"%{form_data['areas_experiencia']}%"))

    # Estado (mantengo tu normalización a underscores)
    if form_data['estado']:
        estado_norm = form_data['estado'].replace(" ", "_")
        filtros.append(Candidata.estado == estado_norm)

    # Reglas fijas
    filtros.append(Candidata.codigo.isnot(None))
    filtros.append(or_(Candidata.porciento == None, Candidata.porciento == 0))

    mensaje = None
    resultados = []

    try:
        query = Candidata.query.filter(*filtros).order_by(Candidata.nombre_completo.asc())
        candidatas = query.limit(500).all()

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
            if any(v for v in form_data.values()):
                mensaje = "⚠️ No se encontraron resultados para los filtros aplicados."

    except Exception as e:
        current_app.logger.error(f"❌ Error al filtrar candidatas: {e}", exc_info=True)
        mensaje = "❌ Error al filtrar los datos."

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
            cid = (request.form.get("candidata_id") or "").strip()
            if not cid.isdigit():
                flash("❌ ID inválido.", "error")
                return redirect(url_for('inscripcion'))

            obj = Candidata.query.get(int(cid))
            if not obj:
                flash("⚠️ Candidata no encontrada.", "error")
                return redirect(url_for('inscripcion'))

            # Genera código si falta
            if not obj.codigo:
                try:
                    obj.codigo = generar_codigo_unico()
                except Exception:
                    app.logger.exception("❌ Error generando código único")
                    flash("❌ No se pudo generar el código.", "error")
                    return redirect(url_for('inscripcion'))

            obj.medio_inscripcion = (request.form.get("medio") or "").strip()[:60] or obj.medio_inscripcion
            obj.inscripcion       = (request.form.get("estado") == "si")
            obj.monto             = parse_decimal(request.form.get("monto") or "") or obj.monto
            obj.fecha             = parse_date(request.form.get("fecha") or "") or obj.fecha

            # Estado
            if obj.inscripcion:
                if obj.monto and obj.fecha:
                    obj.estado = 'inscrita'
                else:
                    obj.estado = 'inscrita_incompleta'
            else:
                obj.estado = 'proceso_inscripcion'

            obj.fecha_cambio_estado    = datetime.utcnow()
            obj.usuario_cambio_estado  = session.get('usuario', 'desconocido')[:64]

            try:
                db.session.commit()
                flash(f"✅ Inscripción guardada. Código: {obj.codigo}", "success")
                candidata = obj
            except Exception:
                db.session.rollback()
                app.logger.exception("❌ Error al guardar inscripción")
                flash("❌ Error al guardar inscripción.", "error")
                return redirect(url_for('inscripcion'))
        else:
            q = (request.form.get("buscar") or "").strip()[:128]
            if q:
                like = f"%{q}%"
                try:
                    resultados = (Candidata.query.filter(
                        or_(
                            Candidata.nombre_completo.ilike(like),
                            Candidata.cedula.ilike(like),
                            Candidata.numero_telefono.ilike(like)
                        )
                    ).order_by(Candidata.nombre_completo.asc()).limit(300).all())
                    if not resultados:
                        flash("⚠️ No se encontraron coincidencias.", "error")
                except Exception:
                    app.logger.exception("❌ Error buscando en inscripción")
                    flash("❌ Error al buscar.", "error")

    else:
        q = (request.args.get("buscar") or "").strip()[:128]
        if q:
            like = f"%{q}%"
            try:
                resultados = (Candidata.query.filter(
                    or_(
                        Candidata.nombre_completo.ilike(like),
                        Candidata.cedula.ilike(like),
                        Candidata.numero_telefono.ilike(like)
                    )
                ).order_by(Candidata.nombre_completo.asc()).limit(300).all())
                if not resultados:
                    mensaje = "⚠️ No se encontraron coincidencias."
            except Exception:
                app.logger.exception("❌ Error buscando candidatas (GET) en inscripción")
                mensaje = "❌ Error al buscar."

        sel = (request.args.get("candidata_seleccionada") or "").strip()
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


@app.route('/porciento', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def porciento():
    resultados, candidata = [], None

    if request.method == "POST":
        fila_id = (request.form.get('fila_id') or '').strip()
        if not fila_id.isdigit():
            flash("❌ Fila inválida.", "danger")
            return redirect(url_for('porciento'))

        obj = Candidata.query.get(int(fila_id))
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('porciento'))

        fecha_pago   = parse_date(request.form.get("fecha_pago") or "")
        fecha_inicio = parse_date(request.form.get("fecha_inicio") or "")
        monto_total  = parse_decimal(request.form.get("monto_total") or "")

        if None in (fecha_pago, fecha_inicio, monto_total):
            flash("❌ Datos incompletos o inválidos.", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

        try:
            porcentaje = (monto_total * Decimal("0.25")).quantize(Decimal("0.01"))
        except Exception:
            flash("❌ Monto inválido.", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

        obj.fecha_de_pago         = fecha_pago
        obj.inicio                = fecha_inicio
        obj.monto_total           = monto_total
        obj.porciento             = porcentaje
        obj.estado                = 'trabajando'
        obj.fecha_cambio_estado   = datetime.utcnow()
        obj.usuario_cambio_estado = session.get('usuario', 'desconocido')[:64]

        try:
            db.session.commit()
            flash(f"✅ Se guardó correctamente. 25 % de {monto_total} es {porcentaje}. Estado: Trabajando.", "success")
            candidata = obj
        except Exception:
            db.session.rollback()
            app.logger.exception("❌ Error al actualizar porciento")
            flash("❌ Error al actualizar.", "danger")
            return redirect(url_for('porciento', candidata=fila_id))

    else:
        q = (request.args.get('busqueda') or '').strip()[:128]
        if q:
            like = f"%{q}%"
            try:
                resultados = (Candidata.query.filter(
                    or_(
                        Candidata.nombre_completo.ilike(like),
                        Candidata.cedula.ilike(like),
                        Candidata.numero_telefono.ilike(like)
                    )
                ).order_by(Candidata.nombre_completo.asc()).limit(300).all())
                if not resultados:
                    flash("⚠️ No se encontraron coincidencias.", "warning")
            except Exception:
                app.logger.exception("❌ Error buscando (GET) en porciento")
                flash("❌ Error al buscar.", "warning")

        sel = (request.args.get('candidata') or '').strip()
        if sel.isdigit() and not resultados:
            candidata = Candidata.query.get(int(sel))
            if not candidata:
                flash("⚠️ Candidata no encontrada.", "warning")

    return render_template("porciento.html", resultados=resultados, candidata=candidata)


@app.route('/pagos', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def pagos():
    resultados, candidata = [], None

    if request.method == 'POST':
        fila = request.form.get('fila', type=int)
        monto_str = (request.form.get('monto_pagado') or '').strip()[:30]
        calificacion = (request.form.get('calificacion') or '').strip()[:200]

        if not fila or not monto_str or not calificacion:
            flash("❌ Datos inválidos.", "danger")
            return redirect(url_for('pagos'))

        try:
            monto_pagado = Decimal(monto_str.replace(',', '.'))
        except Exception:
            flash("❌ Monto inválido.", "danger")
            return redirect(url_for('pagos'))

        obj = Candidata.query.get(fila)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('pagos'))

        # Asegura Decimal y evita negativos
        actual = obj.porciento if isinstance(obj.porciento, Decimal) else (parse_decimal(str(obj.porciento)) if obj.porciento is not None else Decimal('0'))
        if actual is None:
            actual = Decimal('0')

        nuevo = actual - monto_pagado
        if nuevo < Decimal('0'):
            nuevo = Decimal('0')

        obj.porciento = nuevo.quantize(Decimal('0.01'))
        obj.calificacion = calificacion

        try:
            db.session.commit()
            flash("✅ Pago guardado con éxito.", "success")
            candidata = obj
        except Exception:
            db.session.rollback()
            app.logger.exception("❌ Error al guardar pago")
            flash("❌ Error al guardar.", "danger")

        return render_template('pagos.html', resultados=[], candidata=candidata)

    # GET
    q = (request.args.get('busqueda') or '').strip()[:128]
    sel = (request.args.get('candidata') or '').strip()

    if q:
        like = f"%{q}%"
        try:
            filas = (Candidata.query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.codigo.ilike(like),
                )
            ).order_by(Candidata.nombre_completo.asc()).limit(300).all())

            for c in filas:
                resultados.append({
                    'fila':     c.fila,
                    'nombre':   c.nombre_completo,
                    'cedula':   c.cedula,
                    'telefono': c.numero_telefono or 'No especificado',
                })

            if not resultados:
                flash("⚠️ No se encontraron coincidencias.", "warning")
        except Exception:
            app.logger.exception("❌ Error buscando en pagos")
            flash("❌ Error al buscar.", "warning")

    if sel.isdigit() and not resultados:
        obj = Candidata.query.get(int(sel))
        if obj:
            candidata = obj
        else:
            flash("⚠️ Candidata no encontrada.", "warning")

    return render_template('pagos.html', resultados=resultados, candidata=candidata)

def _retry_query(callable_fn, retries: int = 2, swallow: bool = False):
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
            try:
                db.session.rollback()
            except Exception:
                pass
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
    # 1) Parámetros (acotamos rangos para evitar explosiones)
    try:
        today = date.today()
        mes       = int(request.args.get('mes', today.month))
        anio      = int(request.args.get('anio', today.year))
        descargar = request.args.get('descargar', '0') == '1'
        page      = max(1, request.args.get('page', default=1, type=int))
        per_page  = min(200, max(1, request.args.get('per_page', default=20, type=int)))
        if not (1 <= mes <= 12):
            return "Parámetro 'mes' inválido.", 400
        if anio < 2000 or anio > today.year + 1:
            return "Parámetro 'anio' inválido.", 400
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
                func.extract('month', Candidata.fecha) == mes,
                func.extract('year',  Candidata.fecha) == anio
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

        # Construir DataFrame para Excel (con nulos seguros)
        df = pd.DataFrame([{
            "Nombre":       r[0] or "",
            "Ciudad":       r[1] or "",
            "Teléfono":     r[2] or "",
            "Cédula":       r[3] or "",
            "Código":       r[4] or "",
            "Medio":        r[5] or "",
            "Inscripción":  "Sí" if r[6] else "No",
            "Monto":        float(r[7] or 0),
            "Fecha":        r[8].strftime("%Y-%m-%d") if r[8] else ""
        } for r in rows])

        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Reporte')
            output.seek(0)
        except Exception as e:
            current_app.logger.exception("❌ Error generando Excel de inscripciones")
            return render_template(
                "reporte_inscripciones.html",
                reporte_html="",
                mes=mes, anio=anio,
                mensaje=f"❌ Error generando el archivo: {e}"
            ), 200

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
        "Nombre":       r[0] or "",
        "Ciudad":       r[1] or "",
        "Teléfono":     r[2] or "",
        "Cédula":       r[3] or "",
        "Código":       r[4] or "",
        "Medio":        r[5] or "",
        "Inscripción":  "Sí" if r[6] else "No",
        "Monto":        float(r[7] or 0),
        "Fecha":        r[8].strftime("%Y-%m-%d") if r[8] else ""
    } for r in items])

    reporte_html = df.to_html(classes="table table-striped", index=False, border=0)
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "reporte_inscripciones.html",
        reporte_html=reporte_html,
        mes=mes, anio=anio,
        mensaje="",
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
        'nombre':               r[0] or "",
        'cedula':               r[1] or "",
        'codigo':               r[2] or "No especificado",
        'ciudad':               r[3] or "No especificado",
        'monto_total':          float(r[4] or 0),
        'porcentaje_pendiente': float(r[5] or 0),
        'fecha_inicio':         r[6].strftime("%Y-%m-%d") if r[6] else "No registrada",
        'fecha_pago':           r[7].strftime("%Y-%m-%d") if r[7] else "No registrada",
    } for r in rows]

    mensaje = None if pagos_pendientes else "⚠️ No se encontraron pagos pendientes."
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
    """
    Vista para:
    - Buscar candidata por nombre, cédula o teléfono.
    - Subir imágenes: depuración, perfil, cédula frente (cedula1) y cédula reverso (cedula2).
    Todo se guarda como binario en la tabla Candidata.
    """
    accion = (request.args.get('accion') or 'buscar').strip()
    fila_id = request.args.get('fila', type=int)
    resultados = []

    # ========================= MODO BUSCAR =========================
    if accion == 'buscar':
        if request.method == 'POST':
            q = (request.form.get('busqueda') or '').strip()[:128]
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

            like = f"%{q}%"
            try:
                filas = (
                    Candidata.query.filter(
                        (Candidata.nombre_completo.ilike(like)) |
                        (Candidata.cedula.ilike(like)) |
                        (Candidata.numero_telefono.ilike(like))
                    )
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
            except Exception:
                app.logger.exception("❌ Error buscando en subir_fotos")
                filas = []

            if not filas:
                flash("⚠️ No se encontraron candidatas.", "warning")
            else:
                resultados = [
                    {
                        'fila': c.fila,
                        'nombre': c.nombre_completo,
                        'telefono': c.numero_telefono or 'No especificado',
                        'cedula': c.cedula or 'No especificado',
                    }
                    for c in filas
                ]

        return render_template('subir_fotos.html', accion='buscar', resultados=resultados)

    # ========================= MODO SUBIR =========================
    if accion == 'subir':
        if not fila_id:
            flash("❌ Debes seleccionar primero una candidata.", "danger")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        candidata = Candidata.query.get(fila_id)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        # GET: mostrar formulario
        if request.method == 'GET':
            return render_template('subir_fotos.html', accion='subir', fila=fila_id)

        # POST: guardar archivos
        files = {
            'depuracion': request.files.get('depuracion'),
            'perfil': request.files.get('perfil'),
            'cedula1': request.files.get('cedula1'),
            'cedula2': request.files.get('cedula2'),
        }

        # Depuración y perfil OBLIGATORIOS
        required_campos = ['depuracion', 'perfil']
        for campo in required_campos:
            archivo = files.get(campo)
            if not archivo or archivo.filename == '':
                etiqueta = "depuración" if campo == "depuracion" else "perfil"
                flash(f"❌ Falta el archivo de {etiqueta}.", "danger")
                return render_template('subir_fotos.html', accion='subir', fila=fila_id)

        try:
            # Siempre que venga archivo válido, se guarda
            dep = files.get('depuracion')
            if dep and dep.filename:
                candidata.depuracion = dep.read()

            perf = files.get('perfil')
            if perf and perf.filename:
                candidata.perfil = perf.read()

            c1 = files.get('cedula1')
            if c1 and c1.filename:
                candidata.cedula1 = c1.read()

            c2 = files.get('cedula2')
            if c2 and c2.filename:
                candidata.cedula2 = c2.read()

            db.session.commit()
            flash("✅ Imágenes subidas y guardadas en la base de datos.", "success")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        except Exception:
            db.session.rollback()
            app.logger.exception("❌ Error guardando imágenes en la BD")
            flash("❌ Error guardando en la BD.", "danger")
            return render_template('subir_fotos.html', accion='subir', fila=fila_id)

    # Si algo raro, mandamos a buscar de nuevo
    return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))


# Registrar blueprint
app.register_blueprint(subir_bp)



# -----------------------------------------------------------------------------
# GESTIONAR ARCHIVOS / PDF (DB only)
# -----------------------------------------------------------------------------
@app.route("/gestionar_archivos", methods=["GET", "POST"])
@roles_required('admin', 'secretaria')
def gestionar_archivos():
    accion = (request.args.get("accion") or "buscar").strip()
    mensaje = None
    resultados = []
    docs = {}
    fila = (request.args.get("fila") or "").strip()

    if accion == "descargar":
        doc = (request.args.get("doc") or "").strip()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        idx = int(fila)
        if doc == "pdf":
            return redirect(url_for("generar_pdf_entrevista", fila=idx))
        return "Documento no reconocido", 400

    if accion == "buscar":
        if request.method == "POST":
            q = (request.form.get("busqueda") or "").strip()[:128]
            if q:
                like = f"%{q}%"
                try:
                    filas = (Candidata.query.filter(
                        (Candidata.nombre_completo.ilike(like)) |
                        (Candidata.cedula.ilike(like)) |
                        (Candidata.numero_telefono.ilike(like))
                    ).order_by(Candidata.nombre_completo.asc()).limit(300).all())
                except Exception:
                    app.logger.exception("❌ Error buscando en gestionar_archivos")
                    filas = []

                resultados = [{
                    "fila": c.fila,
                    "nombre": c.nombre_completo,
                    "telefono": c.numero_telefono or "No especificado",
                    "cedula": c.cedula or "No especificado"
                } for c in filas] if filas else []

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

        # Pasamos los binarios como están (tu template actual puede revisarlos)
        docs["depuracion"] = getattr(c, "depuracion", None)
        docs["perfil"]     = getattr(c, "perfil", None)
        docs["cedula1"]    = getattr(c, "cedula1", None)
        docs["cedula2"]    = getattr(c, "cedula2", None)
        docs["entrevista"] = getattr(c, "entrevista", "") or ""

        return render_template("gestionar_archivos.html",
                               accion=accion,
                               fila=idx,
                               docs=docs,
                               mensaje=mensaje)

    return redirect(url_for("gestionar_archivos", accion="buscar"))


@app.route('/generar_pdf_entrevista')
@roles_required('admin', 'secretaria')
def generar_pdf_entrevista():
    # Asegura que usamos fpdf2
    try:
        from fpdf import FPDF as _FPDF
        from fpdf.errors import FPDFException
    except Exception:
        return "❌ fpdf2 no está instalado. Ejecuta: pip uninstall -y fpdf && pip install -U fpdf2", 500

    fila_index = request.args.get('fila', type=int)
    if not fila_index:
        return "Error: falta parámetro fila", 400

    c = Candidata.query.get(fila_index)
    if not c or not getattr(c, "entrevista", None):
        return "No hay entrevista registrada para esa fila", 404

    texto_entrevista = c.entrevista or ""
    ref_laborales    = getattr(c, "referencias_laboral", "") or ""
    ref_familiares   = getattr(c, "referencias_familiares", "") or ""

    import os, io, re, unicodedata

    BRAND  = (0, 102, 204)
    FAINT  = (120, 120, 120)
    GRID   = (210, 210, 210)

    # ── Helpers robustos ─────────────────────────────────────
    def _ascii_if_needed(s: str, unicode_ok: bool) -> str:
        if unicode_ok:
            return s or ""
        s = s or ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 0x2500)

    def _collapse_ws(s: str) -> str:
        return re.sub(r"[ \t]+", " ", (s or "").strip())

    def _wrap_unbreakables(s: str, chunk=60) -> str:
        out = []
        for w in (s or "").split(" "):
            if len(w) > chunk:
                out.extend([w[i:i+chunk] for i in range(0, len(w), chunk)])
            else:
                out.append(w)
        return " ".join(out)

    def safe_multicell(pdf, txt, font_name, font_style, font_size, color=None, align="J", line_space=1.2):
        pdf.set_x(pdf.l_margin)
        if color:
            pdf.set_text_color(*color)
        try:
            pdf.set_font(font_name, font_style, font_size)
        except Exception:
            # fallback duro
            try:
                pdf.set_font("Arial", font_style or "", max(10, int(font_size)))
            except Exception:
                pdf.set_font("Arial", "", 10)
        try:
            pdf.multi_cell(pdf.epw, 7, txt, align=align)
            pdf.ln(line_space)
        except FPDFException:
            # Fuerza cortes en palabras enormes
            txt2 = _wrap_unbreakables(txt, chunk=35)
            pdf.set_font(font_name, "", 10)
            pdf.multi_cell(pdf.epw, 7, txt2, align="L")
            pdf.ln(line_space)

    class InterviewPDF(_FPDF):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._logo_path   = None
            self._base_font   = "Arial"
            self._unicode_ok  = False
            self._has_italic  = False  # para saber si podemos usar "I"
            self._has_bold    = False
            self._has_bi      = False

        def header(self):
            if self.page_no() == 1:
                # Logo (solo primera página) + menos espacio
                if self._logo_path and os.path.exists(self._logo_path):
                    w = 92  # grande pero elegante
                    x = (self.w - w) / 2.0
                    self.image(self._logo_path, x=x, y=10, w=w)
                    y_line = 10 + (w * 0.38)
                    self.set_y(y_line)
                else:
                    self.set_y(18)
                # línea delgada
                self.set_draw_color(*GRID)
                self.set_line_width(0.6)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(3)  # menos separación

                # Título
                try:
                    self.set_font(self._base_font, "B", 18 if self._has_bold else 17)
                except Exception:
                    self.set_font("Arial", "B", 18)
                self.set_fill_color(*BRAND)
                self.set_text_color(255, 255, 255)
                self.cell(self.epw, 11, "Entrevista", ln=True, align="C", fill=True)
                self.set_text_color(0, 0, 0)
                self.ln(4)
            else:
                # páginas siguientes: una línea fina arriba
                self.set_y(14)
                self.set_draw_color(*GRID)
                self.set_line_width(0.4)
                self.line(self.l_margin, 14, self.w - self.r_margin, 14)
                self.ln(7)

        def footer(self):
            self.set_y(-15)
            # Intentar itálica; si no existe, caer a regular; si todo falla, Arial
            try:
                if self._has_italic or self._has_bi:
                    self.set_font(self._base_font, "I", 9)
                else:
                    self.set_font(self._base_font, "", 9)
            except Exception:
                try:
                    self.set_font("Arial", "I", 9)
                except Exception:
                    self.set_font("Arial", "", 9)
            self.set_text_color(*FAINT)
            self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

    try:
        pdf = InterviewPDF(format="A4")
        pdf.alias_nb_pages()
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.set_margins(16, 16, 16)
        pdf._logo_path = os.path.join(app.root_path, "static", "logo_nuevo.png")

        # ── Registro de fuentes ──
        base_font   = "Arial"
        unicode_ok  = False
        has_bold    = False
        has_italic  = False
        has_bi      = False

        try:
            font_dir = os.path.join(app.root_path, "static", "fonts")
            reg   = os.path.join(font_dir, "DejaVuSans.ttf")
            bold  = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            it    = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")      # si existe
            bi    = os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")  # si existe

            if os.path.exists(reg):
                pdf.add_font("DejaVuSans", "", reg, uni=True)
                base_font  = "DejaVuSans"
                unicode_ok = True
            if os.path.exists(bold):
                pdf.add_font("DejaVuSans", "B", bold, uni=True)
                has_bold = True
            if os.path.exists(it):
                pdf.add_font("DejaVuSans", "I", it, uni=True)
                has_italic = True
            if os.path.exists(bi):
                pdf.add_font("DejaVuSans", "BI", bi, uni=True)
                has_bi = True
        except Exception:
            # Si algo falla, Arial built-in
            base_font  = "Arial"
            unicode_ok = False
            has_bold   = True  # Arial tiene B/I built-in
            has_italic = True
            has_bi     = True

        pdf._base_font  = base_font
        pdf._unicode_ok = unicode_ok
        pdf._has_bold   = has_bold
        pdf._has_italic = has_italic
        pdf._has_bi     = has_bi

        pdf.add_page()

        bullet = "• " if unicode_ok else "- "

        # ===== ENTREVISTA =====
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, "📝 Entrevista" if unicode_ok else "Entrevista", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        for raw in (texto_entrevista or "").splitlines():
            line = _collapse_ws(_ascii_if_needed(raw, unicode_ok))
            if ":" in line:
                q, a = line.split(":", 1)
                q = _collapse_ws(q)
                a = _collapse_ws(a)

                # Pregunta en negro (bold)
                safe_multicell(
                    pdf,
                    (q + ":").strip(),
                    base_font,
                    "B" if has_bold else "",
                    12,
                    color=(0, 0, 0),
                    align="L",
                    line_space=1
                )
                # Respuesta en azul + bullet
                ans = _wrap_unbreakables(a, 60)
                ans = (bullet + ans) if ans else ans
                safe_multicell(
                    pdf,
                    ans,
                    base_font,
                    "",
                    12,
                    color=BRAND,
                    align="J",
                    line_space=2
                )
            else:
                # Línea suelta (negro)
                safe_multicell(
                    pdf,
                    _wrap_unbreakables(line, 60),
                    base_font,
                    "",
                    12,
                    color=(0, 0, 0),
                    align="J",
                    line_space=1.5
                )

        pdf.ln(3)

        # ===== REFERENCIAS =====
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, ("📌 " if unicode_ok else "") + "Referencias", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        # Laborales
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Laborales:", ln=True)
        if ref_laborales.strip():
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_laborales, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J"
            )
        else:
            safe_multicell(pdf, "No hay referencias laborales.", base_font, "", 12, color=FAINT, align="L")

        # Familiares
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Familiares:", ln=True)
        if ref_familiares.strip():
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_familiares, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J"
            )
        else:
            safe_multicell(pdf, "No hay referencias familiares.", base_font, "", 12, color=FAINT, align="L")

        # ── Salida ──
        raw = pdf.output(dest="S")
        pdf_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("latin1", "ignore")
        buf = io.BytesIO(pdf_bytes); buf.seek(0)
        return send_file(buf, mimetype="application/pdf", as_attachment=True,
                         download_name=f"entrevista_candidata_{fila_index}.pdf")

    except Exception as e:
        current_app.logger.exception("❌ Error interno generando PDF")
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

    # Asegura bytes (puede venir como memoryview en algunos backends)
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif not isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data)
        except Exception:
            return "Formato de archivo inválido.", 400

    # Detectar mimetype por encabezado
    head = data[:8]
    if head.startswith(b"\x89PNG"):
        mt, ext = "image/png", "png"
    elif head.startswith(b"\xFF\xD8\xFF"):
        mt, ext = "image/jpeg", "jpg"
    elif head[:4] == b"GIF8":
        mt, ext = "image/gif", "gif"
    elif head[:4] == b"%PDF":
        mt, ext = "application/pdf", "pdf"
    else:
        mt, ext = "application/octet-stream", "bin"

    bio = io.BytesIO(data); bio.seek(0)
    return send_file(
        bio,
        mimetype=mt,
        as_attachment=True,
        download_name=f"{doc}.{ext}"
    )



# -----------------------------------------------------------------------------
# REFERENCIAS (laborales / familiares)
# -----------------------------------------------------------------------------
@app.route('/referencias', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def referencias():
    mensaje = None
    accion = (request.args.get('accion') or 'buscar').strip()
    resultados = []
    candidata = None

    # Buscar por término
    if request.method == 'POST' and 'busqueda' in request.form:
        termino = (request.form.get('busqueda') or '').strip()[:128]
        if termino:
            like = f"%{termino}%"
            try:
                filas = (Candidata.query.filter(
                    or_(
                        Candidata.nombre_completo.ilike(like),
                        Candidata.cedula.ilike(like),
                        Candidata.numero_telefono.ilike(like)
                    )
                ).order_by(Candidata.nombre_completo.asc()).limit(300).all())
            except Exception:
                current_app.logger.exception("❌ Error buscando candidatas en /referencias")
                filas = []

            resultados = [
                {
                    'id': c.fila,
                    'nombre': c.nombre_completo,
                    'cedula': c.cedula,
                    'telefono': c.numero_telefono or 'No especificado'
                } for c in filas
            ]
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas."
        else:
            mensaje = "⚠️ Ingresa un término de búsqueda."

        return render_template('referencias.html',
                               accion='buscar',
                               resultados=resultados,
                               mensaje=mensaje)

    # Ver candidata seleccionada
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

    # Guardar referencias
    if request.method == 'POST' and 'candidata_id' in request.form:
        cid = request.form.get('candidata_id', type=int)
        candidata = Candidata.query.get(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no existe."
        else:
            # Limitar tamaño para evitar payloads enormes
            cand_ref_lab = (request.form.get('referencias_laboral') or '').strip()[:5000]
            cand_ref_fam = (request.form.get('referencias_familiares') or '').strip()[:5000]

            candidata.referencias_laboral    = cand_ref_lab
            candidata.referencias_familiares = cand_ref_fam
            try:
                db.session.commit()
                mensaje = "✅ Referencias actualizadas."
            except Exception:
                db.session.rollback()
                current_app.logger.exception("❌ Error al guardar referencias")
                mensaje = "❌ Error al guardar."

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
@app.route('/dashboard_procesos', methods=['GET'])
@roles_required('admin', 'secretaria')
def dashboard_procesos():
    estado_filtro = (request.args.get('estado') or '').strip()[:40]
    desde_str     = (request.args.get('desde') or '').strip()[:10]
    hasta_str     = (request.args.get('hasta') or '').strip()[:10]
    page          = max(1, request.args.get('page', 1, type=int))
    per_page      = min(100, max(1, request.args.get('per_page', 20, type=int)))

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
        flash("⚠️ No se pudo conectar a la base de datos. Reintenta en unos segundos.", "warning")

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
    except Exception:
        current_app.logger.exception("❌ Error construyendo dashboard")
        class _EmptyPagination:
            def __init__(self):
                self.items = []
                self.total = 0
                self.pages = 0
                self.page = page
                self.prev_num = None
            def has_prev(self): return False
            def has_next(self): return False
            def iter_pages(self, *args, **kwargs):
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
@roles_required('admin', 'secretaria')
def auto_actualizar_estados():
    """
    Revisa candidatas en 'inscrita_incompleta' y promueve a 'lista_para_trabajar'
    si ya tienen todos los documentos/datos requeridos.
    """
    try:
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
    except Exception:
        db.session.rollback()
        current_app.logger.exception("❌ Error auto_actualizando estados")
        return jsonify({'error': 'No se pudo actualizar estados automáticamente'}), 500


# -----------------------------------------------------------------------------
# LLAMADAS CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas/llamadas')
@roles_required('admin','secretaria')
def listado_llamadas_candidatas():
    q               = (request.args.get('q') or '').strip()[:128]
    period          = (request.args.get('period') or 'all').strip()[:16]
    start_date_str  = request.args.get('start_date', None)
    page            = max(1, request.args.get('page', 1, type=int))

    start_dt, end_dt = get_date_bounds(period, start_date_str)

    # Subconsulta de llamadas por candidata
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

    def section(estado: str):
        qsec = base_q.filter(Candidata.estado == estado)
        if start_dt and end_dt:
            qsec = qsec.filter(
                cast(Candidata.marca_temporal, Date) >= start_dt,
                cast(Candidata.marca_temporal, Date) <= end_dt
            )
        # Paginación segura (10 por sección)
        try:
            return qsec.order_by(calls_subq.c.last_call.asc().nullsfirst())\
                       .paginate(page=page, per_page=10, error_out=False)
        except AttributeError:
            return db.paginate(qsec.order_by(calls_subq.c.last_call.asc().nullsfirst()),
                               page=page, per_page=10, error_out=False)

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
        segundos = (minutos * 60) if (minutos is not None) else None

        llamada = LlamadaCandidata(
            candidata_id      = candidata.fila,
            fecha_llamada     = func.now(),
            agente            = session.get('usuario', 'desconocido')[:64],
            resultado         = (form.resultado.data or '').strip()[:200],
            duracion_segundos = segundos,
            notas             = (form.notas.data or '').strip()[:2000],
            created_at        = datetime.utcnow()
        )
        db.session.add(llamada)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando llamada de candidata")
            flash('❌ Error al registrar la llamada.', 'danger')
            return redirect(url_for('listado_llamadas_candidatas'))

        flash(f'Llamada registrada para {candidata.nombre_completo}.', 'success')
        return redirect(url_for('listado_llamadas_candidatas'))

    return render_template('registrar_llamada_candidata.html',
                           form=form,
                           candidata=candidata)


@app.route('/candidatas/llamadas/reporte')
@roles_required('admin')
def reporte_llamadas_candidatas():
    period         = (request.args.get('period') or 'week').strip()[:16]
    start_date_str = request.args.get('start_date', None)
    start_dt       = get_start_date(period, start_date_str)
    hoy            = date.today()
    page           = max(1, request.args.get('page', 1, type=int))

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

    def paginate_estado(estado: str):
        qy = base_q.filter(Candidata.estado == estado)
        if start_dt:
            qy = qy.filter(
                or_(
                    stats_subq.c.last_call == None,
                    cast(stats_subq.c.last_call, Date) < start_dt
                )
            )
        try:
            return qy.order_by(cast(stats_subq.c.last_call, Date).desc().nullsfirst())\
                     .paginate(page=page, per_page=10, error_out=False)
        except AttributeError:
            return db.paginate(qy.order_by(cast(stats_subq.c.last_call, Date).desc().nullsfirst()),
                               page=page, per_page=10, error_out=False)

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
from datetime import date, datetime
from flask import request, render_template, url_for, jsonify, flash, redirect
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload, load_only
from urllib.parse import urlencode  # ← lo usas más abajo

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
# PUBLICAR HOY (listado para copiar+marcar) – template: secretarias_solicitudes_copiar.html
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

    try:
        raw_sols = base_q.limit(500).all()
    except Exception:
        current_app.logger.exception("❌ Error listando solicitudes copiables")
        raw_sols = []

    # Mapear funciones code->label (igual que admin)
    FUNCIONES_CHOICES = {}
    try:
        form = AdminSolicitudForm() if AdminSolicitudForm else None
        if form and hasattr(form, "funciones") and hasattr(form.funciones, "choices"):
            FUNCIONES_CHOICES = dict(form.funciones.choices)
    except Exception:
        FUNCIONES_CHOICES = {}

    solicitudes = []
    for s in raw_sols:
        # Funciones (labels + otro)
        funcs = []
        try:
            seleccion = set(_as_list(getattr(s, 'funciones', None)))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == 'otro':
                continue
            label = FUNCIONES_CHOICES.get(code)
            if label:
                funcs.append(label)
        custom_otro = (getattr(s, 'funciones_otro', None) or '').strip()
        if custom_otro:
            funcs.append(custom_otro)

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
        )
        modalidad_val = modalidad_val.strip()

        # Hogar (solo descripción, sin prefijo)
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
            lines += [modalidad_val, ""]

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
            lines += ["", hogar_val]

        lines += ["", f"Adultos: {adultos}"]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)
        lines += ["", sueldo_txt]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()[:4000]  # seguridad

        solicitudes.append({
            "id": s.id,
            "codigo_solicitud": _s(s.codigo_solicitud),
            "ciudad_sector": _s(s.ciudad_sector),
            "modalidad": modalidad_val,
            "copiada_hoy": False,
            "order_text": order_text,
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
    try:
        s.last_copiado_at = func.now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("❌ Error marcando solicitud copiada")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": False, "error": "No se pudo marcar como copiada"}), 500
        flash('❌ No se pudo marcar la solicitud como copiada.', 'danger')
        return redirect(url_for('secretarias_copiar_solicitudes'))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "id": id, "codigo": _s(s.codigo_solicitud)}), 200

    flash(f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.', 'success')
    return redirect(url_for('secretarias_copiar_solicitudes'))


# ─────────────────────────────────────────────────────────────
# BUSCAR (paginado + filtros) – template: secretarias_solicitudes_buscar.html
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/buscar', methods=['GET'])
@roles_required('admin', 'secretaria')
def secretarias_buscar_solicitudes():
    q           = (request.args.get('q') or '').strip()[:128]
    estado      = (request.args.get('estado') or '').strip()[:20]
    desde_str   = (request.args.get('desde') or '').strip()[:10]
    hasta_str   = (request.args.get('hasta') or '').strip()[:10]
    modalidad   = (request.args.get('modalidad') or '').strip()[:60]
    mascota     = (request.args.get('mascota') or '').strip()[:3]      # '', 'si', 'no'
    con_ninos   = (request.args.get('con_ninos') or '').strip()[:3]    # '', 'si', 'no'
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
    FUNCIONES_CHOICES = {}
    try:
        form = AdminSolicitudForm() if AdminSolicitudForm else None
        if form and hasattr(form, "funciones") and hasattr(form.funciones, "choices"):
            FUNCIONES_CHOICES = dict(form.funciones.choices)
    except Exception:
        FUNCIONES_CHOICES = {}

    items = []
    for s in paginado.items:
        modalidad_val = ((s.modalidad_trabajo or s.modalidad or s.tipo_modalidad or '')).strip()

        funcs = []
        try:
            seleccion = set(_as_list(getattr(s, 'funciones', None)))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == 'otro':
                continue
            label = FUNCIONES_CHOICES.get(code)
            if label:
                funcs.append(label)
        custom_otro = (getattr(s, 'funciones_otro', None) or '').strip()
        if custom_otro:
            funcs.append(custom_otro)

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
            lines += [modalidad_val, ""]

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
            lines += ["", hogar_val]

        lines += ["", f"Adultos: {adultos}"]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)
        lines += ["", sueldo_txt]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()[:4000]

        items.append({
            "id": s.id,
            "codigo_solicitud": _s(s.codigo_solicitud),
            "ciudad_sector": _s(s.ciudad_sector),
            "modalidad": modalidad_val,
            "estado": _s(s.estado),
            "fecha_solicitud": s.fecha_solicitud.strftime("%Y-%m-%d %H:%M") if s.fecha_solicitud else "",
            "copiada_ciclo": (s.last_copiado_at is not None),
            "order_text": order_text,
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

    # --- POST: recoger datos del formulario (limitando tamaños) ---
    nombre       = (request.form.get('nombre_completo') or '').strip()[:150]
    edad_raw     = (request.form.get('edad') or '').strip()[:10]
    telefono     = (request.form.get('numero_telefono') or '').strip()[:30]
    direccion    = (request.form.get('direccion_completa') or '').strip()[:250]
    modalidad    = (request.form.get('modalidad_trabajo_preferida') or '').strip()[:100]
    rutas        = (request.form.get('rutas_cercanas') or '').strip()[:150]
    empleo_prev  = (request.form.get('empleo_anterior') or '').strip()[:150]
    anos_exp     = (request.form.get('anos_experiencia') or '').strip()[:50]
    areas_list   = request.form.getlist('areas_experiencia')  # checkboxes
    planchar_raw = (request.form.get('sabe_planchar') or '').strip().lower()[:2]
    ref_lab      = (request.form.get('contactos_referencias_laborales') or '').strip()[:500]
    ref_fam      = (request.form.get('referencias_familiares_detalle') or '').strip()[:500]
    acepta_raw   = (request.form.get('acepta_porcentaje_sueldo') or '').strip()[:1]
    cedula_raw   = (request.form.get('cedula') or '').strip()[:20]

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

    if planchar_raw not in ('si', 'no'):
        faltantes.append("Sabe planchar (sí/no)")

    if acepta_raw not in ('1', '0'):
        faltantes.append("Acepta % de sueldo (sí/no)")

    # Edad razonable
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
    areas_str     = ', '.join([s.strip() for s in areas_list if s.strip()]) if areas_list else ''
    sabe_planchar = (planchar_raw == 'si')
    acepta_pct    = (acepta_raw == '1')

    # --- Comprobación de duplicado por cédula ---
    try:
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()
    except OperationalError:
        _safe_dispose_pool()
        db.session.rollback()
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()

    if dup:
        flash("⚠️ Ya existe una candidata registrada con esta cédula.", "warning")
        return render_template('registro_publico.html'), 400

    # --- Crear objeto y guardar ---
    nueva = Candidata(
        marca_temporal               = datetime.utcnow(),
        nombre_completo              = nombre,
        edad                         = str(edad_num),
        numero_telefono              = telefono,
        direccion_completa           = direccion,
        modalidad_trabajo_preferida  = modalidad,
        rutas_cercanas               = rutas,
        empleo_anterior              = empleo_prev,
        anos_experiencia             = anos_exp,
        areas_experiencia            = areas_str,
        sabe_planchar                = sabe_planchar,
        contactos_referencias_laborales = ref_lab,
        referencias_familiares_detalle  = ref_fam,
        acepta_porcentaje_sueldo     = acepta_pct,
        cedula                       = cedula_norm,
        medio_inscripcion            = "Web",
        estado                       = "en_proceso",
        fecha_cambio_estado          = datetime.utcnow(),
        usuario_cambio_estado        = "registro_publico",
    )

    try:
        db.session.add(nueva)
        db.session.commit()
    except OperationalError:
        _safe_dispose_pool()
        db.session.rollback()
        try:
            db.session.add(nueva)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("❌ Problema momentáneo con la conexión. Intenta de nuevo en unos segundos.", "danger")
            return render_template('registro_publico.html'), 503
    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f"❌ No se pudo guardar el registro: {e.__class__.__name__}", "danger")
        return render_template('registro_publico.html'), 500

    flash("✅ ¡Registro enviado! Te contactaremos por WhatsApp en breve.", "success")
    return redirect(url_for('registro_publico'))


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
    q = (request.args.get('q') or '').strip()[:128]
    resultados = []
    if q:
        like = f"%{q}%"
        try:
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
        except Exception:
            current_app.logger.exception("❌ Error buscando en finalizar_proceso_buscar")
            resultados = []
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

    bio = BytesIO(img_bytes); bio.seek(0)
    return send_file(
        bio,
        mimetype='image/jpeg',
        as_attachment=False,
        download_name=f"perfil_{fila}.jpg",
        max_age=0
    )


# ─────────────────────────────────────────────────────────────
# SECRETARÍAS – TEST DE COMPATIBILIDAD PARA CANDIDATA
# ─────────────────────────────────────────────────────────────
COMPAT_TEST_CANDIDATA_VERSION = "v1.0"

# Catálogos (alineados con models.py)
COMPAT_RITMOS = [
    ('tranquilo',   'Tranquilo'),
    ('activo',      'Activo'),
    ('muy_activo',  'Muy activo'),
]
COMPAT_ESTILOS = [
    ('necesita_instrucciones', 'Paso a paso'),
    ('toma_iniciativa',        'Prefiere iniciativa'),
]
COMPAT_COMUNICACION = [
    ('breve',     'Breve y directa'),
    ('detallada', 'Detallada'),
    ('mixta',     'Mixta'),
]
COMPAT_RELACION_NINOS = [
    ('comoda',         'Cómoda con niños'),
    ('neutral',        'Neutral'),
    ('prefiere_evitar','Prefiere evitar niños'),
]
COMPAT_EXPERIENCIA_NIVEL = [
    ('baja',  'Básica'),
    ('media', 'Intermedia'),
    ('alta',  'Alta'),
]
COMPAT_MASCOTAS = [
    ('si', 'Sí, sin problema'),
    ('no', 'No, prefiero evitarlo'),
]

# Checklists (guardamos el "code")
FORTALEZAS = [
    ('limpieza_general',   'Limpieza general'),
    ('limpieza_profunda',  'Limpieza profunda'),
    ('cocina_basica',      'Cocina básica'),
    ('cocina_avanzada',    'Cocina avanzada'),
    ('lavado',             'Lavado'),
    ('planchado',          'Planchado'),
    ('cuidado_ninos',      'Cuidado de niños'),
    ('cuidado_mayores',    'Cuidado de personas mayores'),
    ('compras',            'Compras / mandados'),
    ('inventario',         'Orden / inventario'),
    ('electrodomesticos',  'Manejo de electrodomésticos'),
]
TAREAS_EVITAR = [
    ('cocinar',          'Cocinar'),
    ('planchar',         'Planchar'),
    ('animales_grandes', 'Mascotas grandes'),
    ('subir_escaleras',  'Subir muchas escaleras'),
    ('nocturno',         'Trabajar de noche'),
    ('dormir_fuera',     'Dormir fuera de casa'),
    ('altas_exigencias', 'Hogares de alta exigencia'),
]
LIMITES_NO_NEG = [
    ('no_cocinar',       'No cocinar'),
    ('no_planchar',      'No planchar'),
    ('no_cuidar_ninos',  'No cuidado de niños'),
    ('no_mascotas',      'No mascotas'),
    ('no_fines_semana',  'No fines de semana'),
    ('no_nocturno',      'No horario nocturno'),
]
DIAS_SEMANA = [
    ('lun','Lunes'), ('mar','Martes'), ('mie','Miércoles'),
    ('jue','Jueves'), ('vie','Viernes'), ('sab','Sábado'), ('dom','Domingo')
]
HORARIOS = [
    ('manana','Mañana'),
    ('tarde','Tarde'),
    ('noche','Noche'),
    ('interna','Interna'),
    ('flexible','Flexible'),
]

# ── Helpers de normalización ─────────────────────────────────
def _getlist_clean(name: str):
    return [x.strip() for x in request.form.getlist(name) if x and x.strip()]

def _int_1a5(name: str):
    try:
        v = int((request.form.get(name) or '').strip())
        return v if 1 <= v <= 5 else None
    except Exception:
        return None

def _norm_choice(v: str, allowed: set):
    v = (v or '').strip().lower()
    return v if v in allowed else None

def _filter_allowed(items, allowed: set):
    out = []
    for it in items or []:
        key = (it or '').strip().lower()
        if key in allowed:
            out.append(key)
    return out

CHOICES_DICT = {
    "RITMOS": COMPAT_RITMOS,
    "ESTILOS": COMPAT_ESTILOS,
    "COMUNICACION": COMPAT_COMUNICACION,
    "REL_NINOS": COMPAT_RELACION_NINOS,
    "EXP_NIVEL": COMPAT_EXPERIENCIA_NIVEL,
    "FORTALEZAS": FORTALEZAS,
    "TAREAS_EVITAR": TAREAS_EVITAR,
    "LIMITES": LIMITES_NO_NEG,
    "DIAS": DIAS_SEMANA,
    "HORARIOS": HORARIOS,
    "MASCOTAS": COMPAT_MASCOTAS,
}

# ─────────────────────────────────────────────────────────────
# RUTA PRINCIPAL
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/compat/candidata', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def compat_candidata():
    """
    - GET  sin ?fila  → buscador (acepta ?q= en GET).
    - GET  con ?fila   → muestra formulario del test.
    - POST (accion=guardar & fila) → guarda el test y redirige:
        * si next=home  → home
        * si no         → buscador del test (no al perfil/fotos)
    """
    fila = request.values.get('fila', type=int)

    # ── 1) GUARDAR (POST) ────────────────────────────────────
    if request.method == 'POST' and request.form.get('accion') == 'guardar' and fila:
        c = Candidata.query.get_or_404(fila)

        # Normalizaciones de selects/radios
        ritmo     = _norm_choice(request.form.get('ritmo'),               {k for k, _ in COMPAT_RITMOS})
        estilo    = _norm_choice(request.form.get('estilo'),              {k for k, _ in COMPAT_ESTILOS})
        comun     = _norm_choice(request.form.get('comunicacion'),        {k for k, _ in COMPAT_COMUNICACION})
        rel_n     = _norm_choice(request.form.get('relacion_ninos'),      {k for k, _ in COMPAT_RELACION_NINOS})
        exp_niv   = _norm_choice(request.form.get('experiencia_nivel'),   {k for k, _ in COMPAT_EXPERIENCIA_NIVEL})
        mascotas  = _norm_choice(request.form.get('mascotas'),            {k for k, _ in COMPAT_MASCOTAS})
        puntual   = _int_1a5('puntualidad_1a5')

        # Checkboxes (filtramos a los permitidos)
        fortalezas = _filter_allowed(_getlist_clean('fortalezas'),              {k for k, _ in FORTALEZAS})
        evitar     = _filter_allowed(_getlist_clean('tareas_evitar'),           {k for k, _ in TAREAS_EVITAR})
        limites    = _filter_allowed(_getlist_clean('limites_no_negociables'),  {k for k, _ in LIMITES_NO_NEG})
        dias       = _filter_allowed(_getlist_clean('disponibilidad_dias'),     {k for k, _ in DIAS_SEMANA})
        horarios   = _filter_allowed(_getlist_clean('disponibilidad_horarios'), {k for k, _ in HORARIOS})

        notas = (request.form.get('nota') or '').strip()[:2000]

        # Validaciones mínimas
        err = []
        if not ritmo:         err.append("Ritmo de hogar")
        if not estilo:        err.append("Estilo de trabajo")
        if not comun:         err.append("Comunicación preferida")
        if not rel_n:         err.append("Relación con niños")
        if puntual is None:   err.append("Puntualidad (1 a 5)")
        if not mascotas:      err.append("Compatibilidad con mascotas")
        if not fortalezas:    err.append("Fortalezas (al menos una)")
        if not dias:          err.append("Disponibilidad en días")
        if not horarios:      err.append("Disponibilidad en horarios")

        if err:
            flash("Completa: " + ", ".join(err), "warning")
            data = {
                "ritmo": ritmo, "estilo": estilo, "comunicacion": comun,
                "relacion_ninos": rel_n, "experiencia_nivel": exp_niv,
                "puntualidad_1a5": puntual, "fortalezas": fortalezas,
                "tareas_evitar": evitar, "limites_no_negociables": limites,
                "disponibilidad_dias": dias, "disponibilidad_horarios": horarios,
                "mascotas": mascotas, "nota": notas
            }
            return render_template('compat_candidata_form.html', candidata=c, data=data, CHOICES=CHOICES_DICT)

        # Persistir en columnas dedicadas (soporta alias alternos)
        try:
            if hasattr(c, 'compat_ritmo_preferido'):   c.compat_ritmo_preferido = ritmo
            if hasattr(c, 'compat_estilo_trabajo'):    c.compat_estilo_trabajo = estilo
            if hasattr(c, 'compat_comunicacion'):      c.compat_comunicacion = comun
            if hasattr(c, 'compat_relacion_ninos'):    c.compat_relacion_ninos = rel_n
            if hasattr(c, 'compat_experiencia_nivel'): c.compat_experiencia_nivel = exp_niv
            if hasattr(c, 'compat_puntualidad_1a5'):   c.compat_puntualidad_1a5 = puntual

            if hasattr(c, 'compat_mascotas'):
                c.compat_mascotas = mascotas
            if hasattr(c, 'compat_mascotas_ok'):
                c.compat_mascotas_ok = (mascotas == 'si')

            if hasattr(c, 'compat_habilidades_fuertes'):
                c.compat_habilidades_fuertes = fortalezas
            elif hasattr(c, 'compat_fortalezas'):
                c.compat_fortalezas = fortalezas

            if hasattr(c, 'compat_habilidades_evitar'):
                c.compat_habilidades_evitar = evitar
            elif hasattr(c, 'compat_tareas_evitar'):
                c.compat_tareas_evitar = evitar

            if hasattr(c, 'compat_limites_no_negociables'):  c.compat_limites_no_negociables = limites
            if hasattr(c, 'compat_disponibilidad_dias'):     c.compat_disponibilidad_dias = dias
            if hasattr(c, 'compat_disponibilidad_horarios'): c.compat_disponibilidad_horarios = horarios

            if hasattr(c, 'compat_observaciones'):           c.compat_observaciones = notas

            payload = {
                "version": COMPAT_TEST_CANDIDATA_VERSION,
                "timestamp": datetime.utcnow().isoformat(),
                "ritmo": ritmo,
                "estilo": estilo,
                "comunicacion": comun,
                "relacion_ninos": rel_n,
                "experiencia_nivel": exp_niv,
                "puntualidad_1a5": puntual,
                "fortalezas": fortalezas,
                "tareas_evitar": evitar,
                "limites_no_negociables": limites,
                "disponibilidad_dias": dias,
                "disponibilidad_horarios": horarios,
                "mascotas": mascotas,
                "nota": notas,
            }
            if hasattr(c, 'compat_test_candidata_json'):     c.compat_test_candidata_json = payload
            if hasattr(c, 'compat_test_candidata_version'):  c.compat_test_candidata_version = COMPAT_TEST_CANDIDATA_VERSION
            if hasattr(c, 'compat_test_candidata_at'):       c.compat_test_candidata_at = datetime.utcnow()

            db.session.commit()
            flash("✅ Test de compatibilidad guardado correctamente.", "success")

            next_url = request.values.get('next')
            if next_url == 'home':
                return redirect(url_for('home'))
            return redirect(url_for('compat_candidata'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando test de compatibilidad")
            flash("❌ No se pudo guardar.", "danger")
            return redirect(url_for('compat_candidata', fila=fila))

    # ── 2) FORMULARIO (GET con fila) ──────────────────────────
    if request.method == 'GET' and fila:
        c = Candidata.query.get_or_404(fila)
        data = {
            "ritmo":                   getattr(c, 'compat_ritmo_preferido', None),
            "estilo":                  getattr(c, 'compat_estilo_trabajo', None),
            "comunicacion":            getattr(c, 'compat_comunicacion', None),
            "relacion_ninos":          getattr(c, 'compat_relacion_ninos', None),
            "experiencia_nivel":       getattr(c, 'compat_experiencia_nivel', None),
            "puntualidad_1a5":         getattr(c, 'compat_puntualidad_1a5', None),
            "fortalezas":              getattr(c, 'compat_habilidades_fuertes', None)
                                       or getattr(c, 'compat_fortalezas', []) or [],
            "tareas_evitar":           getattr(c, 'compat_habilidades_evitar', None)
                                       or getattr(c, 'compat_tareas_evitar', []) or [],
            "limites_no_negociables":  getattr(c, 'compat_limites_no_negociables', []) or [],
            "disponibilidad_dias":     getattr(c, 'compat_disponibilidad_dias', []) or [],
            "disponibilidad_horarios": getattr(c, 'compat_disponibilidad_horarios', []) or [],
            "mascotas":                (getattr(c, 'compat_mascotas', None)
                                        if hasattr(c, 'compat_mascotas')
                                        else ('si' if getattr(c, 'compat_mascotas_ok', False) else 'no')
                                        if hasattr(c, 'compat_mascotas_ok') else None),
            "nota":                    getattr(c, 'compat_observaciones', '') or '',
        }
        return render_template('compat_candidata_form.html', candidata=c, data=data, CHOICES=CHOICES_DICT)

    # ── 3) BUSCADOR (GET/POST sin fila) ───────────────────────
    q = (request.values.get('q') or '').strip()[:128]
    resultados = []
    mensaje = None

    if request.method == 'POST' and request.form.get('accion') == 'buscar':
        q = (request.form.get('q') or '').strip()[:128]

    if q:
        like = f"%{q}%"
        try:
            resultados = (
                Candidata.query
                .filter(or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.codigo.ilike(like),
                ))
                .order_by(Candidata.nombre_completo.asc())
                .limit(200)
                .all()
            )
        except Exception:
            current_app.logger.exception("❌ Error buscando candidatas en compat_candidata")
            resultados = []
        if not resultados:
            mensaje = "⚠️ No se encontraron coincidencias."

    return render_template('compat_candidata_buscar.html',
                           resultados=resultados,
                           mensaje=mensaje,
                           q=q)

# ─────────────────────────────────────────────────────────────
# ADMIN / SECRETARÍA – GESTIÓN DE CANDIDATAS PARA LA WEB
# ─────────────────────────────────────────────────────────────

@app.route('/admin/candidatas_web', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def listar_candidatas_web():

    q = (request.form.get('q') or request.args.get('q') or '').strip()

    # BASE: TODAS las candidatas
    query = (
        db.session.query(Candidata, CandidataWeb)
        .outerjoin(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
    )

    # 🔍 BÚSQUEDA SOLO EN CANDIDATA (donde están los datos reales)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
                Candidata.numero_telefono.ilike(like),
            )
        )

    # Orden limpio y lógico
    query = query.order_by(
        Candidata.nombre_completo.asc()
    )

    resultados = query.all()  # [(candidata, ficha_web), ...]

    return render_template(
        'candidatas_web_list.html',
        resultados=resultados,
        q=q
    )


@app.route('/admin/candidatas_web/<int:fila>/editar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def editar_candidata_web(fila):
    """
    Editar la ficha pública de una candidata:
    - Lee los datos internos desde Candidata (nombre_completo, etc.).
    - Guarda todo lo que es de la web en CandidataWeb.
    """
    # 'fila' = identificador interno de la candidata
    cand = Candidata.query.filter_by(fila=fila).first_or_404()

    # Buscar ficha web; si no existe, crearla en memoria
    ficha = CandidataWeb.query.filter_by(candidata_id=cand.fila).first()
    if not ficha:
        ficha = CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico='disponible',
        )
        db.session.add(ficha)
        db.session.flush()  # la deja lista sin hacer commit todavía

    if request.method == 'POST':
        # Checkboxes
        ficha.visible = bool(request.form.get('visible'))
        ficha.es_destacada = bool(request.form.get('es_destacada'))
        ficha.disponible_inmediato = bool(request.form.get('disponible_inmediato'))

        # Estado público (select con opciones válidas)
        estado = (request.form.get('estado_publico') or '').strip()
        if estado in ['disponible', 'reservada', 'no_disponible']:
            ficha.estado_publico = estado

        # Orden manual
        orden_raw = (request.form.get('orden_lista') or '').strip()
        if orden_raw:
            try:
                ficha.orden_lista = int(orden_raw)
            except ValueError:
                flash("⚠️ El orden debe ser un número entero.", "warning")
        else:
            ficha.orden_lista = None

        # Textos públicos
        ficha.nombre_publico = (request.form.get('nombre_publico') or '').strip()[:200] or None
        ficha.edad_publica = (request.form.get('edad_publica') or '').strip()[:50] or None
        ficha.ciudad_publica = (request.form.get('ciudad_publica') or '').strip()[:120] or None
        ficha.sector_publico = (request.form.get('sector_publico') or '').strip()[:120] or None
        ficha.modalidad_publica = (request.form.get('modalidad_publica') or '').strip()[:120] or None
        ficha.tipo_servicio_publico = (request.form.get('tipo_servicio_publico') or '').strip()[:50] or None
        ficha.anos_experiencia_publicos = (request.form.get('anos_experiencia_publicos') or '').strip()[:50] or None

        ficha.experiencia_resumen = (request.form.get('experiencia_resumen') or '').strip() or None
        ficha.experiencia_detallada = (request.form.get('experiencia_detallada') or '').strip() or None
        ficha.tags_publicos = (request.form.get('tags_publicos') or '').strip()[:255] or None
        ficha.frase_destacada = (request.form.get('frase_destacada') or '').strip()[:200] or None

        # Sueldo y foto
        sueldo_desde_raw = (request.form.get('sueldo_desde') or '').strip()
        sueldo_hasta_raw = (request.form.get('sueldo_hasta') or '').strip()

        ficha.sueldo_desde = int(sueldo_desde_raw) if sueldo_desde_raw.isdigit() else None
        ficha.sueldo_hasta = int(sueldo_hasta_raw) if sueldo_hasta_raw.isdigit() else None

        ficha.sueldo_texto_publico = (request.form.get('sueldo_texto_publico') or '').strip()[:120] or None
        ficha.foto_publica_url = (request.form.get('foto_publica_url') or '').strip()[:255] or None

        # Fecha de publicación la primera vez que se marca visible
        if ficha.visible and ficha.fecha_publicacion is None:
            ficha.fecha_publicacion = datetime.utcnow()

        try:
            db.session.commit()
            flash("✅ Ficha para la web actualizada correctamente.", "success")
            # Para que no se ponga lento, volvemos a la misma ficha en vez de cargar el listado completo
            return redirect(url_for('editar_candidata_web', fila=cand.fila))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error guardando ficha web de candidata")
            flash("❌ Ocurrió un error guardando los cambios.", "danger")

    return render_template(
        'candidata_web_form.html',
        cand=cand,
        ficha=ficha,
    )


from flask import render_template, session, redirect, url_for, request

@app.route('/candidatas_porcentaje')
@roles_required('admin', 'secretaria')
def candidatas_porcentaje():
    """
    Lista todas las candidatas que tienen un porcentaje configurado.
    Optimizado con:
      - with_entities (solo columnas necesarias)
      - paginación
    """

    # Proteger la vista: si no hay usuario logueado, mandar a login
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Página actual (por defecto 1)
    page = request.args.get('page', 1, type=int)
    per_page = 50  # puedes subir o bajar este número

    # Query optimizada: solo las columnas que usamos en la tabla
    base_query = (
        Candidata.query
        .with_entities(
            Candidata.fila,
            Candidata.codigo,
            Candidata.nombre_completo.label('nombre'),
            Candidata.numero_telefono.label('telefono'),
            Candidata.modalidad_trabajo_preferida.label('modalidad'),
            Candidata.inicio.label('fecha_inicio'),
            Candidata.fecha_de_pago.label('fecha_pago'),
            Candidata.monto_total,
            Candidata.porciento,
        )
        .filter(
            Candidata.porciento.isnot(None),
            Candidata.porciento > 0
        )
        .order_by(
            Candidata.fecha_de_pago.asc().nullslast(),
            Candidata.fila.asc()
        )
    )

    pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
    candidatas = pagination.items

    return render_template(
        'candidatas_porcentaje.html',
        candidatas=candidatas,
        pagination=pagination
    )

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

@app.route('/candidatas/eliminar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def eliminar_candidata():
    """
    Pantalla para eliminar candidatas manualmente:
    - Paso 1: Buscar por nombre, cédula, teléfono o código.
    - Paso 2: Ver detalle completo (docs, entrevista, etc.).
    - Paso 3: Confirmar eliminación definitiva.
    SOLO se permitirá eliminar candidatas que no tengan historial
    (sin solicitudes, sin llamadas, sin reemplazos).
    """

    # ----- Helper interno para armar info de documentos -----
    def build_docs_info(c):
        if not c:
            return {
                "tiene_cedula1": False,
                "tiene_cedula2": False,
                "tiene_perfil": False,
                "tiene_depuracion": False,
                "documentos_completos": False,
                "entrevista_realizada": False,
                "solicitudes_count": 0,
                "llamadas_count": 0,
                "reemplazos_count": 0,
            }

        tiene_cedula1 = bool(c.cedula1)
        tiene_cedula2 = bool(c.cedula2)
        tiene_perfil  = bool(c.perfil)
        tiene_dep     = bool(c.depuracion)

        documentos_completos = (
            tiene_cedula1 and tiene_cedula2 and tiene_perfil and tiene_dep
        )

        entrevista_realizada = bool(
            c.entrevista and str(c.entrevista).strip()
        )

        solicitudes_count = len(c.solicitudes or [])
        llamadas_count    = len(c.llamadas or [])

        # 🔍 Buscar registros en la tabla de reemplazos donde aparezca esta candidata
        reemplazos_count = Reemplazo.query.filter(
            or_(
                Reemplazo.candidata_old_id == c.fila,
                Reemplazo.candidata_new_id == c.fila
            )
        ).count()

        return {
            "tiene_cedula1": tiene_cedula1,
            "tiene_cedula2": tiene_cedula2,
            "tiene_perfil": tiene_perfil,
            "tiene_depuracion": tiene_dep,
            "documentos_completos": documentos_completos,
            "entrevista_realizada": entrevista_realizada,
            "solicitudes_count": solicitudes_count,
            "llamadas_count": llamadas_count,
            "reemplazos_count": reemplazos_count,
        }

    # --- Entrada de búsqueda (GET/POST normal) ---
    if request.method == 'POST' and request.form.get('confirmar_eliminacion'):
        # En el POST de confirmación la búsqueda no importa tanto, pero la mantenemos
        busqueda = (request.form.get('busqueda') or '').strip()[:128]
    else:
        busqueda = (
            (request.form.get('busqueda') if request.method == 'POST'
             else request.args.get('busqueda')) or ''
        ).strip()[:128]

    resultados = []
    candidata = None
    mensaje = None
    docs_info = build_docs_info(None)

    # ─────────────────────────────────────────────
    # 1) CONFIRMAR ELIMINACIÓN (POST)
    # ─────────────────────────────────────────────
    if request.method == 'POST' and request.form.get('confirmar_eliminacion'):
        cid = (request.form.get('candidata_id') or '').strip()
        if not cid.isdigit():
            mensaje = "❌ ID de candidata inválido."
        else:
            obj = Candidata.query.get(int(cid))
            if not obj:
                mensaje = "⚠️ La candidata ya no existe en la base de datos."
            else:
                # Armamos info y verificamos si tiene historial
                docs_info = build_docs_info(obj)

                tiene_historial = (
                    docs_info["solicitudes_count"] > 0
                    or docs_info["llamadas_count"] > 0
                    or docs_info["reemplazos_count"] > 0
                )

                if tiene_historial:
                    # ❌ No permitimos borrar candidatas con historial
                    mensaje = (
                        "⚠️ No se puede eliminar esta candidata porque tiene "
                        f"{docs_info['solicitudes_count']} solicitudes, "
                        f"{docs_info['llamadas_count']} llamadas "
                        f"y {docs_info['reemplazos_count']} reemplazos registrados. "
                        "En estos casos se recomienda marcarla como inactiva / no disponible, "
                        "pero no borrarla para no dañar el historial."
                    )
                    candidata = obj
                else:
                    # ✅ Candidata sin historial: intentamos borrar
                    try:
                        nombre_log = obj.nombre_completo
                        cedula_log = obj.cedula
                        codigo_log = obj.codigo

                        db.session.delete(obj)
                        db.session.commit()

                        app.logger.info(
                            "✅ Candidata eliminada manualmente: fila=%s, nombre=%s, cedula=%s, codigo=%s",
                            cid, nombre_log, cedula_log, codigo_log
                        )
                        flash("✅ Candidata eliminada correctamente.", "success")
                        # Luego de borrar, volvemos a la pantalla limpia de búsqueda
                        return redirect(url_for('eliminar_candidata', busqueda=busqueda or ''))
                    except IntegrityError:
                        # Si por alguna razón aún hay FKs, prevenimos caída y avisamos
                        db.session.rollback()
                        app.logger.exception("❌ FK bloqueó la eliminación de la candidata.")
                        mensaje = (
                            "❌ La base de datos no permitió eliminarla porque está ligada "
                            "a otros registros (por ejemplo reemplazos o movimientos). "
                            "Para no dañar el historial, es mejor marcarla como no disponible."
                        )
                        candidata = obj
                    except Exception:
                        db.session.rollback()
                        app.logger.exception("❌ Error al eliminar candidata manualmente")
                        mensaje = "❌ Ocurrió un error al eliminar. Intenta de nuevo."
                        candidata = obj

    # ─────────────────────────────────────────────
    # 2) CARGAR DETALLE (GET ?candidata_id=)
    # ─────────────────────────────────────────────
    if not candidata:
        cid = (request.args.get('candidata_id') or '').strip()
        if cid.isdigit():
            candidata = Candidata.query.get(int(cid))
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."
                docs_info = build_docs_info(None)
            else:
                docs_info = build_docs_info(candidata)

    # ─────────────────────────────────────────────
    # 3) BÚSQUEDA (lista de posibles candidatas)
    # ─────────────────────────────────────────────
    if busqueda and not candidata:
        like = f"%{busqueda}%"
        try:
            resultados = (
                Candidata.query
                .filter(
                    or_(
                        Candidata.codigo.ilike(like),
                        Candidata.nombre_completo.ilike(like),
                        Candidata.cedula.ilike(like),
                        Candidata.numero_telefono.ilike(like),
                    )
                )
                .order_by(Candidata.nombre_completo.asc())
                .limit(100)
                .all()
            )
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas con ese dato."
        except Exception:
            app.logger.exception("❌ Error buscando candidatas para eliminar")
            mensaje = "❌ Ocurrió un error al buscar."

    return render_template(
        'candidata_eliminar.html',
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje,
        docs_info=docs_info,
    )

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
