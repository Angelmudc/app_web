# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

from typing import Optional
from urllib.parse import urlparse
import io
import os
import re
import json
import logging
import unicodedata
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from time import perf_counter

import requests  # HTTP externo (si lo usas en otras partes)

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, send_from_directory, flash, jsonify,
    current_app, abort
)

from jinja2 import TemplateNotFound
from flask_wtf.csrf import generate_csrf

from flask_login import login_user, logout_user, current_user

# SQLAlchemy
from sqlalchemy import or_, cast, String, func, and_, Date
from sqlalchemy.orm import subqueryload, joinedload, load_only
from sqlalchemy.exc import OperationalError, IntegrityError, DBAPIError
from sqlalchemy.sql import text

# 🔐 HASH DE CONTRASEÑAS
from werkzeug.security import generate_password_hash, check_password_hash

# ✅ App factory / DB / CSRF / CACHE / usuarios en memoria
from config_app import db, csrf, cache

# Decoradores
from decorators import roles_required, admin_required, staff_required

# Modelos
from models import (
    Candidata,
    LlamadaCandidata,
    CandidataWeb,
    Solicitud,
    Reemplazo,
    StaffUser,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaRespuesta,
)

# Formularios
from forms import LlamadaCandidataForm

# Utils locales
from utils_codigo import generar_codigo_unico  # tu función optimizada
from utils.upload_security import validate_upload_file
from utils.upload_limits import MAX_FILE_BYTES, get_filestorage_size, file_too_large, human_size
from utils.cedula_normalizer import (
    normalize_cedula_for_compare,
    normalize_cedula_for_store,
)
from utils.cedula_guard import find_duplicate_candidata_by_cedula, duplicate_cedula_message
from utils.compat_engine import (
    ENGINE_VERSION,
    HORARIO_OPTIONS,
    MASCOTAS_CHOICES,
    MASCOTAS_IMPORTANCIA_CHOICES,
    normalize_horarios_tokens,
    normalize_mascotas_importancia,
    normalize_mascotas_token,
)
from utils.guards import assert_candidata_no_descalificada, candidatas_activas_filter
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.staff_auth import (
    breakglass_allowed_ip,
    get_request_ip,
    breakglass_username,
    build_breakglass_user,
    check_breakglass_password,
    clear_breakglass_session,
    is_breakglass_enabled,
    log_breakglass_attempt,
    set_breakglass_session,
)
from utils.audit_logger import log_action, snapshot_model_fields, diff_snapshots
from utils.audit_entity import log_candidata_action
from utils.pdf_labels import humanize_pdf_label
from utils.robust_save import (
    execute_robust_save,
    binary_has_content,
    safe_bytes_length,
    legacy_text_is_useful,
)
from utils.candidate_registration import (
    error_looks_like_duplicate_cedula,
    log_candidate_create_fail,
    log_candidate_create_ok,
    normalize_person_name,
    normalize_phone,
    phone_has_valid_digits,
    robust_create_candidata,
)
from utils.timezone import format_rd_datetime, iso_utc_z, rd_today, utc_now_naive

# Data / reportes
import pandas as pd


# PDF (fpdf2)
try:
    from fpdf import FPDF  # fpdf2
except Exception:
    FPDF = None


# -----------------------------------------------------------------------------
# APP DUMMY (handlers reutilizables sin registrar rutas aquí)
# -----------------------------------------------------------------------------


class _NoopCLI:
    def command(self, *args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator


class _NoopJinjaEnv:
    def __init__(self):
        self.globals = {}


class _DummyApp:
    def __init__(self):
        self.root_path = os.path.dirname(os.path.dirname(__file__))
        self.config = {}
        self.extensions = {}
        self.blueprints = {}
        self.view_functions = {}
        self.cli = _NoopCLI()
        self.jinja_env = _NoopJinjaEnv()

    @property
    def logger(self):
        try:
            return current_app.logger
        except Exception:
            return logging.getLogger("legacy_handlers")

    def route(self, *args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator

    def before_request(self, fn):
        return fn

    def after_request(self, fn):
        return fn

    def errorhandler(self, *args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator

    def register_blueprint(self, bp, *args, **kwargs):
        self.blueprints[getattr(bp, "name", f"bp_{len(self.blueprints)+1}")] = bp


app = _DummyApp()

@app.before_request
def force_session_expire():
    # 🔒 Siempre forzar sesión no permanente
    session.permanent = False

# -----------------------------------------------------------------------------
# 🔒 HARDENING BÁSICO (NO ROMPE LOCAL)
# -----------------------------------------------------------------------------
# En producción (HTTPS) activa cookies seguras. En local se queda normal.
IS_PROD = (
    (os.getenv("FLASK_ENV", "").strip().lower() == "production")
    or (os.getenv("ENV", "").strip().lower() == "production")
)

# Cookies de sesión seguras
app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")  # evita CSRF cross-site sin romper navegación
app.config.setdefault("SESSION_COOKIE_SECURE", bool(IS_PROD))  # True solo en prod con HTTPS

# Cookies remember (Flask-Login)
app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
app.config.setdefault("REMEMBER_COOKIE_SECURE", bool(IS_PROD))

# Vida de sesión (ajustable)
app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=8))
# 🔒 Forzar sesión NO permanente (se cierra al cerrar el navegador)
app.config.setdefault("SESSION_PERMANENT", False)

# CSRF (Flask-WTF)
app.config.setdefault("WTF_CSRF_TIME_LIMIT", 60 * 60 * 8)  # 8 horas

# ProxyFix (SOLO si TRUST_XFF=1, para no confiar en XFF en local)
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    if os.getenv("TRUST_XFF", "0").strip().lower() in ("1", "true", "yes", "on"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
except Exception:
    pass

# Headers de seguridad (suaves, sin romper)
@app.after_request
def _security_headers(resp):
    """Headers de seguridad.

    IMPORTANTE:
    - NO definir CSP aquí.
      `create_app()` (config_app.py) y/o `utils/security_layer.py` ya manejan la CSP.
      Si la seteamos aquí, por el orden de `after_request`, esta CSP termina ganando y
      bloquea CDNs (Bootstrap/Icons), rompiendo los diseños del portal.
    """
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Evita clickjacking
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

        # ⚠️ CSP se define en config_app.py / security_layer.py (NO duplicar aquí)
        # resp.headers.setdefault("Content-Security-Policy", "...")
    except Exception:
        pass
    return resp


# ----------------------------------------------------------------------------
# Flask-Login: asegúrate de que cada blueprint use SU login (clientes/admin)
#  - Evita que /clientes/* termine cayendo en /login (panel interno)
#  - Mantiene el login interno por sesión (ruta /login) sin romperlo
# ----------------------------------------------------------------------------
try:
    _lm = app.extensions.get('login_manager')
    if _lm is not None:
        # defaults (siempre existente)
        _lm.login_view = 'login'

        # Mapea logins por blueprint (lo que Flask-Login consulta primero)
        try:
            if not hasattr(_lm, 'blueprint_login_views') or _lm.blueprint_login_views is None:
                _lm.blueprint_login_views = {}
            # ✅ Portal de clientes
            _lm.blueprint_login_views['clientes'] = 'clientes.login'
            # ✅ Panel admin (si usas @login_required en admin)
            _lm.blueprint_login_views['admin'] = 'admin.login'
        except Exception:
            pass

        # Handler global: si alguien cae en unauthorized dentro de un blueprint,
        # redirige al login correcto.
        @_lm.unauthorized_handler
        def _unauthorized_callback():
            try:
                bp = (request.blueprint or '').strip()
                next_url = request.full_path if request.full_path else request.path
                if bp == 'clientes':
                    return redirect(url_for('clientes.login', next=next_url))
                if bp == 'admin':
                    return redirect(url_for('admin.login', next=next_url))
            except Exception:
                pass
            # fallback: login interno
            try:
                next_url = request.full_path if request.full_path else request.path
                return redirect(url_for('login', next=next_url))
            except Exception:
                return redirect(url_for('login'))
except Exception:
    pass

# Helper para verificar si un endpoint existe (usable desde Jinja)
app.jinja_env.globals['has_endpoint'] = lambda name: name in app.view_functions

def url_for_safe(endpoint: str, **values):
    """url_for que no rompe si el endpoint no existe."""
    return url_for(endpoint, **values) if endpoint in app.view_functions else None

app.jinja_env.globals['url_for_safe'] = url_for_safe


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

CEDULA_PATTERN = re.compile(r'^\d{11}$')

# Código estricto tipo CAN-000000
CODIGO_PATTERN = re.compile(r'^[A-Z]{3}-\d{6}$')


def _strip_accents_py(s: str) -> str:
    """Quita acentos en Python (para normalizar el texto de búsqueda)."""
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')


def normalize_query_text(raw: str) -> str:
    """Normaliza texto para búsquedas flexibles (nombre, etc.).
    - lower
    - sin acentos
    - coma/puntos a espacios
    - colapsa espacios
    """
    s = (raw or '').strip()
    if not s:
        return ''
    s = s.replace(',', ' ').replace('.', ' ').replace(';', ' ').replace(':', ' ')
    s = s.replace('\n', ' ').replace('\t', ' ')
    s = _strip_accents_py(s).lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_digits(raw: str) -> str:
    """Deja solo dígitos (para cédula/teléfono)."""
    return re.sub(r'\D', '', raw or '').strip()


def normalize_code(raw: str) -> str:
    """Normaliza código: MAYÚSCULAS y sin espacios."""
    return re.sub(r"\s+", "", (raw or '').strip().upper())


def _sql_name_norm(col):
    """Normaliza nombre en SQL (PostgreSQL) sin depender de EXTENSION unaccent.
    - lower
    - reemplaza acentos comunes
    - reemplaza puntuación por espacio
    - colapsa espacios
    """
    # Nota: regexp_replace y translate existen en PostgreSQL.
    lowered = func.lower(col)
    # Map básico de acentos + ñ
    translated = func.translate(
        lowered,
        'áàäâãéèëêíìïîóòöôõúùüûñ',
        'aaaaaeeeeiiiiooooouuuun'
    )
    # Cambia puntuación a espacio
    cleaned = func.regexp_replace(translated, r"[^a-z0-9\s\-]", " ", "g")
    cleaned = func.regexp_replace(cleaned, r"[\s]+", " ", "g")
    return func.trim(cleaned)


def _sql_digits(col):
    """Extrae solo dígitos desde una columna (PostgreSQL)."""
    return func.regexp_replace(col, r"\D", "", "g")


def build_flexible_search_filters(q: str):
    """Construye filtros flexibles para nombre/cédula/teléfono.

    Reglas:
    - Código: SOLO match estricto tipo CAN-000000 (sin búsquedas parciales por código).
    - Nombre: flexible con coma/sin coma, acentos/no acentos, espacios extra.
    - Cédula y teléfono: flexible por dígitos (ignorando guiones, espacios, paréntesis, etc.).

    Retorna: (strict_code_filter_or_None, other_filters_list)
    """
    q = (q or '').strip()
    if not q:
        return None, []

    q_code = normalize_code(q)
    q_digits = normalize_digits(q)
    q_text = normalize_query_text(q)

    strict_code = None
    if CODIGO_PATTERN.fullmatch(q_code):
        # estricto: igual exacto (trim + upper)
        strict_code = (func.trim(func.upper(Candidata.codigo)) == q_code)

    filters = []

    # Nombre: si hay texto
    if q_text:
        # Match inteligente: mientras más completo el nombre, más estricto.
        # Requiere que TODOS los tokens estén en el nombre (AND), no cualquiera (OR).
        tokens = [t for t in q_text.split(' ') if t]
        name_norm = _sql_name_norm(Candidata.nombre_completo)

        if tokens:
            name_and = and_(*[name_norm.ilike(f"%{t}%") for t in tokens])
            filters.append(name_and)

    # Cédula / Teléfono: por dígitos (flexible)
    if q_digits:
        ced_digits = _sql_digits(Candidata.cedula).ilike(f"%{q_digits}%")
        tel_digits = _sql_digits(Candidata.numero_telefono).ilike(f"%{q_digits}%")
        filters.append(or_(ced_digits, tel_digits))

    # Si no hay tokens ni dígitos, al menos intenta por raw como fallback
    if not filters:
        like = f"%{q}%"
        filters.extend([
            Candidata.nombre_completo.ilike(like),
            Candidata.cedula.ilike(like),
            Candidata.numero_telefono.ilike(like),
        ])

    return strict_code, filters


def apply_search_to_candidata_query(base_query, q: str):
    """Aplica la lógica de búsqueda estándar a una query de Candidata.

    IMPORTANTE:
    - Esta función NO aplica `order_by()`, `limit()` ni `offset()`.
    - El caller debe hacer: `apply_search_to_candidata_query(...).order_by(...).limit(...)`
      para evitar el error de SQLAlchemy: order_by() después de limit().
    """
    strict_code, filters = build_flexible_search_filters(q)

    # Si el usuario escribió un código válido, SOLO buscamos por código estricto.
    if strict_code is not None:
        return base_query.filter(Candidata.codigo.isnot(None)).filter(strict_code)

    # Si NO es código válido, buscamos por nombre/cédula/teléfono (flexible) y
    # NO hacemos búsquedas por código.
    if filters:
        return base_query.filter(or_(*filters))

    return base_query


def search_candidatas_limited(
    q: str,
    *,
    limit: int = 300,
    base_query=None,
    minimal_fields: bool = False,
    order_mode: str = "nombre_asc",
    log_label: str = "default",
):
    """Ejecuta la búsqueda estándar de candidatas con límite y orden consistentes."""
    q = (q or "").strip()[:128]
    if not q:
        return []

    query = base_query if base_query is not None else Candidata.query
    if minimal_fields:
        query = query.options(
            load_only(
                Candidata.fila,
                Candidata.nombre_completo,
                Candidata.cedula,
                Candidata.numero_telefono,
                Candidata.codigo,
            )
        )
    safe_limit = max(1, min(int(limit or 300), 500))
    t0 = perf_counter()
    filtered = apply_search_to_candidata_query(query, q)
    if order_mode == "id_desc":
        filtered = filtered.order_by(Candidata.fila.desc())
    else:
        filtered = filtered.order_by(Candidata.nombre_completo.asc())
    rows = filtered.limit(safe_limit).all()
    dt_ms = round((perf_counter() - t0) * 1000, 2)
    current_app.logger.info(
        "search_candidatas_limited[%s] q=%r rows=%s dt_ms=%s",
        log_label,
        q,
        len(rows),
        dt_ms,
    )
    return rows


def get_candidata_by_id(raw_id):
    """Obtiene una candidata por ID de forma segura; retorna None si no es válido."""
    cid = str(raw_id or "").strip()
    if not cid.isdigit():
        return None
    return db.session.get(Candidata, int(cid))


# ----------------------------------------------------------------------------
# Seguridad: redirects seguros (evita open-redirect con ?next=...)
# ----------------------------------------------------------------------------

def _is_safe_next(target: str) -> bool:
    """Permite solo redirects internos (sin dominio externo)."""
    if not target:
        return False
    try:
        ref = urlparse(request.host_url)
        test = urlparse(target)
        # Permite rutas relativas ("/home") o URLs del mismo host
        if not test.netloc and test.path.startswith("/"):
            return True
        return (test.scheme, test.netloc) == (ref.scheme, ref.netloc)
    except Exception:
        return False


def safe_redirect_next(default_endpoint: str, **default_values):
    """Redirect a ?next=... si es seguro; si no, usa un endpoint interno."""
    nxt = (request.args.get("next") or request.form.get("next") or "").strip()
    if _is_safe_next(nxt):
        return redirect(nxt)
    return redirect(url_for(default_endpoint, **default_values))


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
        _get_engine().dispose()
    except Exception:
        pass
    return (
        "⚠️ Conexión a la base de datos no disponible momentáneamente. Intenta nuevamente."
    ), 503


def _db_retry(fn, *args, **kwargs):
    """
    Ejecuta fn y, si la conexión está rota, hace remove() y reintenta UNA vez.
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
            return fn(*args, **kwargs)
        raise


def _get_candidata_safe_by_pk(fila: int):
    """Carga Candidata por PK con retry."""
    def _load():
        return Candidata.query.get(fila)
    return _db_retry(_load)


def _fetch_image_bytes_safe(fila: int):
    """
    Saca los bytes de imagen directamente con conexión cruda.
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


def run_db_safely(fn, retry_once: bool = True, fallback=None):
    """
    Ejecuta una función que toca la DB con retry controlado.
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


# -----------------------------------------------------------------------------
# Helpers de queries (evita NameError de safe_all)
# -----------------------------------------------------------------------------

def safe_all(query):
    """Ejecuta query.all() con retry (y fallback a lista vacía)."""
    return run_db_safely(lambda: query.all(), retry_once=True, fallback=[])


def _cache_key_with_role(prefix: str):
    """Genera cache-key aislada por rol + querystring para evitar mezclar vistas."""
    role = (session.get("role") or "anon")
    try:
        path_qs = request.full_path or request.path or ""
    except Exception:
        path_qs = ""
    return f"{prefix}:{role}:{path_qs}"


# -----------------------------------------------------------------------------
# Normalizadores
# -----------------------------------------------------------------------------

def normalize_cedula(raw: str) -> Optional[str]:
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


def parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except Exception:
        return None


def parse_decimal(s: str) -> Optional[Decimal]:
    try:
        return Decimal((s or "").replace(',', '.'))
    except Exception:
        return None


def get_date_bounds(period: str, date_str: Optional[str] = None):
    """
    Devuelve (start_dt, end_dt)
    """
    hoy = rd_today()
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


def get_start_date(period: str, date_str: Optional[str] = None):
    start, _ = get_date_bounds(period, date_str)
    return start


# -----------------------------------------------------------------------------
# CONFIG ENTREVISTAS
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


 # Nota: Flask ya sirve `/static/...` automáticamente (app.static_folder). Evitamos duplicar esa ruta.

@app.route('/robots.txt')
def robots_txt():
    static_folder = current_app.static_folder or os.path.join(current_app.root_path, "static")
    return send_from_directory(static_folder, "robots.txt")

# -----------------------------------------------------------------------------
# AUTH (panel interno por sesión simple)
#  Nota de seguridad:
#  - Autenticación staff basada en tabla staff_users.
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
        current_year=rd_today().year
    )


 
# ---- Anti-bruteforce settings (ajustables)
LOGIN_MAX_INTENTOS = int(os.getenv("LOGIN_MAX_INTENTOS", "6"))   # intentos
LOGIN_LOCK_MINUTOS = int(os.getenv("LOGIN_LOCK_MINUTOS", "10"))  # minutos
LOGIN_KEY_PREFIX   = "panel_login"


def _operational_rate_limits_enabled() -> bool:
    raw = (os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _client_ip() -> str:
    """Obtiene la IP del cliente.
    - En local: NO confía en cabeceras proxy.
    - En producción detrás de proxy: orden CF-Connecting-IP, X-Real-IP, X-Forwarded-For.
    """
    trust_xff = (os.getenv("TRUST_XFF", "0").strip() == "1")
    if trust_xff:
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            return cf_ip[:64]

        x_real = (request.headers.get("X-Real-IP") or "").strip()
        if x_real:
            return x_real[:64]

        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()[:64]
    return (request.remote_addr or "0.0.0.0").strip()[:64]

def _clear_security_layer_lock(endpoint: str = "/login", usuario: str = ""):
    """Limpia el lock global (utils/security_layer.py) si está registrado.
    Soporta limpiar por IP + endpoint + usuario.
    """
    try:
        clear_fn = current_app.extensions.get("clear_login_attempts")
        if callable(clear_fn):
            ip = _client_ip()
            ep = (endpoint or "/login").strip() or "/login"
            uname = (usuario or "").strip()
            # Si el helper soporta (ip, endpoint, username) lo usamos.
            try:
                if uname:
                    clear_fn(ip, ep, uname)
                else:
                    clear_fn(ip, ep)
            except TypeError:
                # Fallback para versiones antiguas que solo aceptan ip
                clear_fn(ip)
    except Exception:
        pass

def _login_keys(usuario_norm: str):
    ip = _client_ip()
    base = f"{LOGIN_KEY_PREFIX}:{ip}:{usuario_norm}"
    return {
        "fail": f"{base}:fail",
        "lock": f"{base}:lock",
    }

def _is_locked(usuario_norm: str) -> bool:
    if not _operational_rate_limits_enabled():
        return False
    keys = _login_keys(usuario_norm)
    return bool(cache.get(keys["lock"]))

def _lock(usuario_norm: str):
    keys = _login_keys(usuario_norm)
    cache.set(keys["lock"], True, timeout=LOGIN_LOCK_MINUTOS * 60)

def _fail_count(usuario_norm: str) -> int:
    keys = _login_keys(usuario_norm)
    return int(cache.get(keys["fail"]) or 0)

def _register_fail(usuario_norm: str) -> int:
    if not _operational_rate_limits_enabled():
        return 0
    keys = _login_keys(usuario_norm)
    n = _fail_count(usuario_norm) + 1
    cache.set(keys["fail"], n, timeout=LOGIN_LOCK_MINUTOS * 60)
    if n >= LOGIN_MAX_INTENTOS:
        _lock(usuario_norm)
    return n

def _reset_fail(usuario_norm: str):
    keys = _login_keys(usuario_norm)
    cache.delete(keys["fail"])
    cache.delete(keys["lock"])



@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""

    if request.method == 'POST':
        # (Opcional pero recomendado) Honeypot: si lo llenan, es bot
        # Si tu login.html tiene input hidden name="website", deja esto:
        if (request.form.get("website") or "").strip():
            return "", 400

        usuario_raw = (request.form.get('usuario') or '').strip()[:64]
        clave       = (request.form.get('clave')   or '').strip()[:128]

        # ✅ Normaliza para llaves internas (bloqueos) y consistencia
        usuario_norm = usuario_raw.lower().strip()

        # 🔒 Bloqueo por intentos (tu bloqueo por usuario+IP)
        if _is_locked(usuario_norm):
            return render_template(
                'login.html',
                mensaje=f"Demasiados intentos. Bloqueado por {LOGIN_LOCK_MINUTOS} minutos."
            ), 429

        # 1) Primero intentar StaffUser (BD) por username o email (case-insensitive)
        staff_user = None
        try:
            staff_user = StaffUser.query.filter(
                or_(
                    func.lower(StaffUser.username) == usuario_norm,
                    func.lower(StaffUser.email) == usuario_norm,
                )
            ).first()
        except Exception:
            staff_user = None

        staff_ok = False
        if staff_user and bool(getattr(staff_user, "is_active", True)):
            role = (getattr(staff_user, "role", "") or "").strip().lower()
            if role in ("owner", "admin", "secretaria"):
                try:
                    staff_ok = bool(staff_user.check_password(clave))
                except Exception:
                    staff_ok = False

        breakglass_ok = False
        if not staff_ok and is_breakglass_enabled() and usuario_norm == breakglass_username().strip().lower():
            ip = get_request_ip()
            ua = request.headers.get("User-Agent") or ""
            if breakglass_allowed_ip(ip) and check_breakglass_password(clave):
                breakglass_ok = True
                log_breakglass_attempt(True, ip, ua)
            else:
                log_breakglass_attempt(False, ip, ua)

        if staff_ok:
            # ✅ Login correcto: limpia intentos (los tuyos) + limpia lock global (IP+endpoint+usuario)
            _reset_fail(usuario_norm)

            # ✅ Limpia lock del security_layer con IP real (Render) si existe helper
            try:
                clear_fn = current_app.extensions.get("clear_login_attempts")
                if callable(clear_fn):
                    ip = _client_ip()
                    # Tu security_layer usa username lower para keys
                    clear_fn(ip, "/login", usuario_norm)
            except Exception:
                pass

            # Si tú también tienes tu helper legacy, lo dejamos (no rompe nada)
            try:
                _clear_security_layer_lock("/login", usuario_norm)
            except Exception:
                pass

            # 🔒 Regenerar sesión completamente al autenticar
            session.clear()
            session.permanent = False
            login_user(staff_user, remember=False)
            session['usuario'] = (staff_user.username or usuario_raw)
            session['role'] = (staff_user.role or "secretaria")
            session['is_staff'] = True
            session['is_admin_session'] = True
            session['logged_at'] = utc_now_naive().isoformat(timespec='seconds')
            clear_breakglass_session(session)
            session.modified = True

            # Auditoría de último login para StaffUser (incluye emergency admin activado).
            try:
                staff_user.last_login_at = utc_now_naive()
                staff_user.last_login_ip = _client_ip()
                db.session.commit()
            except Exception:
                db.session.rollback()

            return safe_redirect_next('home')

        if breakglass_ok:
            _reset_fail(usuario_norm)
            try:
                clear_fn = current_app.extensions.get("clear_login_attempts")
                if callable(clear_fn):
                    ip = _client_ip()
                    clear_fn(ip, "/login", usuario_norm)
            except Exception:
                pass
            try:
                _clear_security_layer_lock("/login", usuario_norm)
            except Exception:
                pass

            session.clear()
            session.permanent = False
            login_user(build_breakglass_user(), remember=False)
            set_breakglass_session(session)
            session['logged_at'] = utc_now_naive().isoformat(timespec='seconds')
            session.modified = True
            return safe_redirect_next('home')

        # ❌ Login incorrecto: registra intento
        n = _register_fail(usuario_norm)

        if _is_locked(usuario_norm):
            return render_template(
                'login.html',
                mensaje=f"Demasiados intentos. Bloqueado por {LOGIN_LOCK_MINUTOS} minutos."
            ), 429

        restantes = max(0, LOGIN_MAX_INTENTOS - n)
        mensaje = f"Usuario o clave incorrectos. Te quedan {restantes} intento(s)."

    return render_template('login.html', mensaje=mensaje)



@app.route('/logout', methods=['POST'])
@roles_required('admin', 'secretaria')
def logout():
    try:
        logout_user()
    except Exception:
        pass
    session.clear()
    return safe_redirect_next('login')


# -----------------------------------------------------------------------------
# REGISTRO INTERNO (privado) - Secretarias/Admin
#  - Usa los mismos campos del registro público
#  - Renderiza template directo en /templates (NO dentro de /templates/registro/)
#  - NO tiene página de gracias (solo flash + recarga)
# -----------------------------------------------------------------------------

@app.route('/registro_interno/', methods=['GET', 'POST'], strict_slashes=False)
@roles_required('admin', 'secretaria')
def registro_interno():
    if request.method == 'GET':
        return render_template('registro_interno.html')

    # --- POST: recoger datos del formulario (limitando tamaños) ---
    nombre       = normalize_person_name(request.form.get('nombre_completo'))
    edad_raw     = (request.form.get('edad') or '').strip()[:10]
    telefono     = normalize_phone(request.form.get('numero_telefono'))
    direccion    = (request.form.get('direccion_completa') or '').strip()[:250]
    modalidad    = (request.form.get('modalidad_trabajo_preferida') or '').strip()[:100]
    rutas        = (request.form.get('rutas_cercanas') or '').strip()[:150]
    empleo_prev  = (request.form.get('empleo_anterior') or '').strip()[:150]
    anos_exp     = (request.form.get('anos_experiencia') or '').strip()[:50]
    areas_list   = request.form.getlist('areas_experiencia')  # checkboxes

    planchar_raw = (request.form.get('sabe_planchar') or '').strip().lower()[:3]
    planchar_raw = planchar_raw.replace('í', 'i')

    ref_lab      = (request.form.get('contactos_referencias_laborales') or '').strip()[:500]
    ref_fam      = (request.form.get('referencias_familiares_detalle') or '').strip()[:500]
    acepta_raw   = (request.form.get('acepta_porcentaje_sueldo') or '').strip()[:1]
    cedula_raw   = (request.form.get('cedula') or '').strip()[:50]
    disponibilidad_inicio = (request.form.get('disponibilidad_inicio') or '').strip()[:80]
    trabaja_con_ninos_raw = (request.form.get('trabaja_con_ninos') or '').strip()[:10]
    trabaja_con_mascotas_raw = (request.form.get('trabaja_con_mascotas') or '').strip()[:10]
    puede_dormir_fuera_raw = (request.form.get('puede_dormir_fuera') or '').strip()[:10]
    sueldo_esperado = (request.form.get('sueldo_esperado') or '').strip()[:80]
    motivacion_trabajo = (request.form.get('motivacion_trabajo') or '').strip()[:350]

    def _fail(message: str, category: str, status_code: int, *, error_message: str, attempts: int = 0):
        flash(message, category)
        log_candidate_create_fail(
            registration_type="interno",
            candidate=None,
            attempt_count=attempts,
            error_message=error_message,
            nombre=nombre,
            cedula=cedula_raw,
        )
        return render_template('registro_interno.html'), status_code

    # --- Validaciones mínimas y mensajes claros ---
    faltantes = []
    for campo, valor in [
        ('Nombre completo', nombre),
        ('Edad', edad_raw),
        ('Número de teléfono', telefono),
        ('Dirección completa', direccion),
        ('Modalidad de trabajo', modalidad),
        ('Rutas cercanas', rutas),
        ('Empleo anterior', empleo_prev),
        ('Años de experiencia', anos_exp),
        ('Referencias laborales', ref_lab),
        ('Referencias familiares', ref_fam),
        ('Cédula', cedula_raw),
    ]:
        if not valor:
            faltantes.append(campo)

    if planchar_raw not in ('si', 'no'):
        faltantes.append('Sabe planchar (sí/no)')

    if acepta_raw not in ('1', '0'):
        faltantes.append('Acepta % de sueldo (sí/no)')

    # Edad razonable
    try:
        edad_num = int(''.join(ch for ch in edad_raw if ch.isdigit()))
        if edad_num < 16 or edad_num > 75:
            return _fail(
                '📛 La edad debe estar entre 16 y 75 años.',
                'warning',
                400,
                error_message='invalid_age_range',
            )
    except ValueError:
        faltantes.append('Edad (número)')
        edad_num = None

    # Validación de cédula
    cedula_digits_input = normalize_cedula_for_compare(cedula_raw)
    if not cedula_raw:
        return _fail('📛 Cédula requerida.', 'warning', 400, error_message='cedula_required')

    if faltantes:
        return _fail(
            'Por favor completa: ' + ', '.join(faltantes),
            'warning',
            400,
            error_message='missing_required_fields',
        )

    if not phone_has_valid_digits(telefono):
        return _fail(
            '📛 Número de teléfono inválido. Debe tener entre 10 y 15 dígitos.',
            'warning',
            400,
            error_message='invalid_phone_number',
        )

    # Convertir/normalizar algunos valores
    areas_str     = ', '.join([s.strip() for s in areas_list if s.strip()]) if areas_list else ''
    sabe_planchar = (planchar_raw == 'si')
    acepta_pct    = (acepta_raw == '1')

    def _parse_optional_yes_no(raw: str):
        val = (raw or '').strip().lower().replace('í', 'i')
        if val in ('si', '1', 'true', 'on'):
            return True
        if val in ('no', '0', 'false', 'off'):
            return False
        return None

    trabaja_con_ninos = _parse_optional_yes_no(trabaja_con_ninos_raw)
    trabaja_con_mascotas = _parse_optional_yes_no(trabaja_con_mascotas_raw)
    puede_dormir_fuera = _parse_optional_yes_no(puede_dormir_fuera_raw)
    disponibilidad_inicio = disponibilidad_inicio or None
    sueldo_esperado = sueldo_esperado or None
    motivacion_trabajo = motivacion_trabajo or None

    # --- Comprobación de duplicado por cédula (DB-safe) ---
    try:
        dup, _ = find_duplicate_candidata_by_cedula(cedula_raw)
    except OperationalError:
        # reconecta/dispose y reintenta una vez
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            _get_engine().dispose()
        except Exception:
            pass
        dup, _ = find_duplicate_candidata_by_cedula(cedula_raw)

    if dup:
        return _fail(
            duplicate_cedula_message(dup),
            'warning',
            400,
            error_message='duplicate_cedula_precheck',
        )

    if len(cedula_digits_input) != 11:
        return _fail(
            '📛 Cédula inválida. Debe contener 11 dígitos.',
            'warning',
            400,
            error_message='invalid_cedula_digits',
        )

    # Guardado normalizado SOLO para altas nuevas
    cedula_store = normalize_cedula_for_store(cedula_raw)
    if not cedula_store:
        return _fail('📛 Cédula requerida.', 'warning', 400, error_message='cedula_required')

    usuario = (session.get('usuario') or 'secretaria').strip()[:64]
    try:
        result, create_state = robust_create_candidata(
            build_candidate=lambda _attempt: Candidata(
                marca_temporal=utc_now_naive(),
                nombre_completo=nombre,
                edad=str(edad_num),
                numero_telefono=telefono,
                direccion_completa=direccion,
                modalidad_trabajo_preferida=modalidad,
                rutas_cercanas=rutas,
                empleo_anterior=empleo_prev,
                anos_experiencia=anos_exp,
                areas_experiencia=areas_str,
                sabe_planchar=sabe_planchar,
                contactos_referencias_laborales=ref_lab,
                referencias_familiares_detalle=ref_fam,
                acepta_porcentaje_sueldo=acepta_pct,
                cedula=cedula_store,
                disponibilidad_inicio=disponibilidad_inicio,
                trabaja_con_ninos=trabaja_con_ninos,
                trabaja_con_mascotas=trabaja_con_mascotas,
                puede_dormir_fuera=puede_dormir_fuera,
                sueldo_esperado=sueldo_esperado,
                motivacion_trabajo=motivacion_trabajo,
                medio_inscripcion='Oficina',
                estado='en_proceso',
                fecha_cambio_estado=utc_now_naive(),
                usuario_cambio_estado=usuario,
            ),
            expected_fields={
                "cedula": cedula_store,
                "nombre_completo": nombre,
                "numero_telefono": telefono,
                "edad": str(edad_num),
            },
            max_retries=2,
            dispose_pool_fn=lambda: _get_engine().dispose(),
        )
    except SQLAlchemyError as e:
        return _fail(
            f'❌ No se pudo guardar el registro: {e.__class__.__name__}',
            'danger',
            500,
            error_message=f'{e.__class__.__name__}: {str(e)[:200]}',
        )

    if not result.ok:
        error_msg = (result.error_message or "").strip()
        if error_looks_like_duplicate_cedula(error_msg):
            return _fail(
                '⚠️ Ya existe una candidata con esta cédula (aunque esté escrita diferente).',
                'warning',
                400,
                error_message=error_msg or 'duplicate_cedula_commit',
                attempts=result.attempts,
            )
        return _fail(
            '❌ No se pudo verificar el registro guardado. Intenta de nuevo en unos segundos.',
            'danger',
            503,
            error_message=error_msg or 'create_verification_failed',
            attempts=result.attempts,
        )

    if not create_state.candidate:
        return _fail(
            '❌ No se pudo verificar el registro guardado. Intenta de nuevo en unos segundos.',
            'danger',
            503,
            error_message='candidate_instance_missing_after_commit',
            attempts=result.attempts,
        )

    log_candidate_create_ok(
        registration_type="interno",
        candidate=create_state.candidate,
        attempt_count=result.attempts,
    )

    # ✅ Sin "gracias": flash + vuelve al mismo formulario
    flash('✅ Candidata registrada correctamente.', 'success')
    return redirect(url_for('registro_interno'))


# -----------------------------------------------------------------------------
# CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas', methods=['GET'])
@roles_required('admin', 'secretaria')
def list_candidatas():
    # Sanitiza búsqueda y evita querys enormes
    q = (request.args.get('q') or '').strip()[:128]
    page = max(1, request.args.get('page', default=1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', default=80, type=int)))

    try:
        base = Candidata.query.filter(
            candidatas_activas_filter(Candidata),
            Candidata.estado != 'trabajando',
        )
        if q:
            base = apply_search_to_candidata_query(base, q)

        pagination = base.order_by(Candidata.nombre_completo.asc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        candidatas = pagination.items
        return render_template(
            'candidatas.html',
            candidatas=candidatas,
            query=q,
            pagination=pagination,
            page=page,
            per_page=per_page,
        )
    except Exception:
        app.logger.exception("❌ Error listando candidatas")
        flash("Ocurrió un error al listar candidatas. Intenta de nuevo.", "danger")
        return render_template('candidatas.html', candidatas=[], query=q), 500


@app.route('/candidatas_db')
@roles_required('admin', 'secretaria')
@cache.cached(
    timeout=int(os.getenv("CACHE_CANDIDATAS_DB_SECONDS", "60")),
    key_prefix=lambda: _cache_key_with_role("candidatas_db"),
)
def list_candidatas_db():
    try:
        max_rows = min(
            5000,
            max(100, int(os.getenv("MAX_CANDIDATAS_DB_ROWS", "1500")))
        )

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
                          Candidata.disponibilidad_inicio,
                          Candidata.trabaja_con_ninos,
                          Candidata.trabaja_con_mascotas,
                          Candidata.puede_dormir_fuera,
                          Candidata.sueldo_esperado,
                          Candidata.motivacion_trabajo,
                      ))
                      .limit(max_rows)
                      .all())

        resultado = []
        for c in candidatas:
            resultado.append({
                "fila": c.fila,
                "marca_temporal": iso_utc_z(c.marca_temporal) if getattr(c, "marca_temporal", None) else None,
                "nombre_completo": c.nombre_completo,
                "edad": c.edad,
                "numero_telefono": c.numero_telefono,
                "direccion_completa": c.direccion_completa,
                "modalidad_trabajo_preferida": c.modalidad_trabajo_preferida,
                "cedula": c.cedula,
                "codigo": c.codigo,
                "disponibilidad_inicio": c.disponibilidad_inicio,
                "trabaja_con_ninos": c.trabaja_con_ninos,
                "trabaja_con_mascotas": c.trabaja_con_mascotas,
                "puede_dormir_fuera": c.puede_dormir_fuera,
                "sueldo_esperado": c.sueldo_esperado,
                "motivacion_trabajo": c.motivacion_trabajo,
            })
        return jsonify({
            "candidatas": resultado,
            "meta": {
                "max_rows": max_rows,
                "returned": len(resultado),
            }
        }), 200

    except Exception:
        app.logger.exception("❌ Error leyendo candidatas desde la DB")
        # No exponemos el error real al cliente
        return jsonify({"error": "Error al consultar la base de datos."}), 500

# -----------------------------------------------------------------------------
# ENTREVISTAS (DB) - HELPERS
# -----------------------------------------------------------------------------

@cache.memoize(timeout=int(os.getenv("CACHE_PREGUNTAS_SECONDS", "300")))
def _get_preguntas_db_por_tipo_cached(tipo_cached: str):
    return (
        EntrevistaPregunta.query
        .filter(EntrevistaPregunta.activa.is_(True))
        .filter(EntrevistaPregunta.clave.like(f"{tipo_cached}.%"))
        .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
        .all()
    )


def _get_preguntas_db_por_tipo(tipo: str):
    """Devuelve preguntas activas para un tipo (domestica/enfermera/empleo_general)."""
    tipo = (tipo or "").strip().lower()
    if not tipo:
        return []

    # Clave: "domestica.xxx" | "enfermera.xxx" | "empleo_general.xxx"
    return _get_preguntas_db_por_tipo_cached(tipo)


def _safe_setattr(obj, name: str, value):
    """Setea un atributo solo si existe en el modelo (para no romper si no está)."""
    if hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            return False
    return False


def _current_staff_actor() -> str:
    return str(
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )


def _safe_seek_upload(file_storage) -> None:
    try:
        if file_storage is not None and getattr(file_storage, "stream", None) is not None:
            file_storage.stream.seek(0)
    except Exception:
        return


def _verify_candidata_docs_saved(candidata_id: int, expected_fields: dict[str, int]) -> bool:
    cand = _get_candidata_by_fila_or_pk(candidata_id)
    if not cand:
        return False
    for field_name in (expected_fields or {}).keys():
        val = getattr(cand, field_name, None)
        if not binary_has_content(val):
            return False
    return True


def _verify_candidata_fields_saved(
    candidata_id: int,
    expected_fields: dict[str, object],
    fallback_obj=None,
) -> bool:
    cand = _get_candidata_by_fila_or_pk(candidata_id)
    if not cand and fallback_obj is not None:
        cand = fallback_obj
    if not cand:
        return False
    for field_name, expected in (expected_fields or {}).items():
        current = getattr(cand, field_name, None)
        if isinstance(expected, str):
            if (str(current or "").strip() != expected.strip()):
                return False
            continue
        if isinstance(expected, bool):
            if bool(current) != expected:
                return False
            continue
        if current != expected:
            return False
    return True


def _verify_interview_new_saved(entrevista_id: int, candidata_id: Optional[int] = None) -> bool:
    if not int(entrevista_id or 0):
        return False
    entrevista = (
        Entrevista.query
        .filter(Entrevista.id == int(entrevista_id))
        .first()
    )
    if not entrevista:
        return False
    if candidata_id and int(getattr(entrevista, "candidata_id", 0) or 0) != int(candidata_id):
        return False
    answers_count = (
        EntrevistaRespuesta.query
        .filter(EntrevistaRespuesta.entrevista_id == int(entrevista_id))
        .count()
    )
    if int(answers_count or 0) <= 0:
        return False
    useful_answers = (
        EntrevistaRespuesta.query
        .filter(EntrevistaRespuesta.entrevista_id == int(entrevista_id))
        .all()
    )
    return any(legacy_text_is_useful(getattr(a, "respuesta", None)) for a in useful_answers)


def _build_legacy_interview_text(preguntas, respuestas_por_pregunta: dict[int, str]) -> str:
    lines = []
    for p in (preguntas or []):
        q = (getattr(p, "enunciado", None) or getattr(p, "clave", None) or f"Pregunta {getattr(p, 'id', '')}").strip()
        a = (respuestas_por_pregunta.get(getattr(p, "id", 0)) or "").strip()
        if not q:
            continue
        lines.append(f"{q}: {a if a else '-'}")
    return "\n".join(lines).strip()[:12000]

# -----------------------------------------------------------------------------
# ENTREVISTAS (DB) - Secretarias/Admin
#   Usa EntrevistaPregunta (sembradas) + Entrevista + EntrevistaRespuesta
# -----------------------------------------------------------------------------


# === Entry routes for NUEVAS entrevistas (DB) ===
@app.route('/entrevistas')
@roles_required('admin', 'secretaria')
def entrevistas_index():
    """Entrada principal a las entrevistas NUEVAS (DB)."""
    return redirect(url_for('entrevistas_buscar'))


@app.route('/entrevistas/buscar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def entrevistas_buscar():
    """Busca una candidata y te manda a la lista de entrevistas de esa candidata."""
    q = (request.form.get('busqueda') or request.args.get('q') or '').strip()[:128]
    resultados = []
    mensaje = None

    if request.method == 'POST':
        if not q:
            flash('⚠️ Escribe algo para buscar.', 'warning')
            return redirect(url_for('entrevistas_buscar'))

    if q:
        try:
            filas = (
                apply_search_to_candidata_query(
                    Candidata.query.filter(candidatas_activas_filter(Candidata)),
                    q
                )
                .order_by(Candidata.nombre_completo.asc())
                .limit(200)
                .all()
            )
            resultados = filas or []
            if not resultados:
                mensaje = '⚠️ No se encontraron candidatas.'
        except Exception:
            current_app.logger.exception('❌ Error buscando candidatas (entrevistas_buscar)')
            mensaje = '❌ Error al buscar. Intenta de nuevo.'

    # Si existe template dedicado lo usamos; si no, fallback simple para probar.
    try:
        return render_template('entrevistas/buscar.html', q=q, resultados=resultados, mensaje=mensaje)
    except TemplateNotFound:
        token = generate_csrf()
        html = [
            '<h2>Entrevistas (NUEVAS - DB) · Buscar candidata</h2>',
            '<form method="POST">',
            f'<input type="hidden" name="csrf_token" value="{token}">',
            f'<input name="busqueda" placeholder="Nombre / Cédula / Teléfono" style="width:320px" value="{q or ""}">',
            '<button type="submit">Buscar</button>',
            '</form>',
        ]
        if mensaje:
            html.append(f"<p>{mensaje}</p>")
        if resultados:
            html.append('<hr><ul>')
            for c in resultados:
                html.append(
                    f"<li><b>{(c.nombre_completo or '').strip()}</b> · {c.cedula or ''} · {c.numero_telefono or ''} "
                    f"— <a href=\"{url_for('entrevistas_de_candidata', fila=c.fila)}\">Ver entrevistas</a> "
                    f"— <a href=\"{url_for('entrevista_nueva_db', fila=c.fila, tipo='domestica')}\">Nueva doméstica</a> "
                    f"— <a href=\"{url_for('entrevista_nueva_db', fila=c.fila, tipo='enfermera')}\">Nueva enfermera</a>"
                    f"</li>"
                )
            html.append('</ul>')
        html.append('<hr><p><a href="/home">Volver a Home</a></p>')
        return "\n".join(html)


@app.route('/entrevistas/lista')
@roles_required('admin', 'secretaria')
def entrevistas_lista():
    """Lista rápida de las últimas entrevistas NUEVAS guardadas (debug/QA)."""
    try:
        q = Entrevista.query
        if hasattr(Entrevista, 'id'):
            q = q.order_by(Entrevista.id.desc())
        entrevistas = q.limit(50).all()
    except Exception:
        current_app.logger.exception('❌ Error cargando entrevistas (lista)')
        entrevistas = []

    # ✅ Usa un template que NO depende de 'candidata'
    try:
        current_app.logger.info('✅ Render entrevistas/lista.html (entrevistas_lista)')
        return render_template('entrevistas/lista.html', entrevistas=entrevistas)
    except TemplateNotFound:
        # Fallback seguro para no quedar en blanco si el template no está donde se espera
        current_app.logger.exception('❌ TemplateNotFound: entrevistas/lista.html')
        html = ['<h2>Entrevistas (NUEVAS - DB) · Últimas 50</h2>']
        html.append('<p><a href="/entrevistas/buscar">Buscar candidata</a></p>')
        if not entrevistas:
            html.append('<p>No hay entrevistas aún.</p>')
        else:
            html.append('<ul>')
            for e in entrevistas:
                fila = getattr(e, 'candidata_id', None)
                tipo = getattr(e, 'tipo', None) if hasattr(e, 'tipo') else None
                eid = getattr(e, 'id', None)

                link_cand = f' — <a href="{url_for("entrevistas_de_candidata", fila=fila)}">ver candidata</a>' if fila else ''
                link_edit = f' — <a href="{url_for("entrevista_editar_db", entrevista_id=eid)}">editar</a>' if eid else ''

                html.append(f"<li>ID: {eid or ''} · candidata_id: {fila or ''} · tipo: {tipo or ''}{link_cand}{link_edit}</li>")
            html.append('</ul>')

        html.append('<hr><p><a href="/home">Volver a Home</a></p>')
        return "\n".join(html)

@app.route("/entrevistas/candidata/<int:fila>")
@roles_required('admin', 'secretaria')
def entrevistas_de_candidata(fila):
    candidata = _get_candidata_safe_by_pk(fila)
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))

    entrevistas = (
        Entrevista.query
        .filter_by(candidata_id=fila)
        .order_by(Entrevista.id.desc())
        .all()
    )

    return render_template(
        "entrevistas/entrevistas_lista.html",
        candidata=candidata,
        entrevistas=entrevistas
    )


@app.route("/entrevistas/nueva/<int:fila>/<string:tipo>", methods=["GET", "POST"])
@roles_required('admin', 'secretaria')
def entrevista_nueva_db(fila, tipo):
    candidata = _get_candidata_safe_by_pk(fila)
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))
    blocked = assert_candidata_no_descalificada(
        candidata,
        action="crear entrevista",
        redirect_endpoint="entrevistas_de_candidata",
        redirect_kwargs={"fila": fila},
    )
    if blocked is not None:
        return blocked

    preguntas = _get_preguntas_db_por_tipo(tipo)
    if not preguntas:
        flash("⚠️ No hay preguntas configuradas para ese tipo de entrevista.", "warning")
        return redirect(url_for('entrevistas_de_candidata', fila=fila))

    if request.method == "POST":
        respuestas_payload = {}
        for p in preguntas:
            field = f"q_{p.id}"
            respuestas_payload[int(p.id)] = (request.form.get(field) or "").strip()

        if not any(legacy_text_is_useful(v) for v in respuestas_payload.values()):
            flash("❌ La entrevista está vacía o no es válida. Completa al menos una respuesta útil.", "danger")
            return redirect(url_for('entrevista_nueva_db', fila=fila, tipo=tipo))

        state = {"entrevista_id": 0, "legacy_text": ""}

        def _persist_interview(_attempt: int):
            entrevista = Entrevista(candidata_id=fila)
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'creada_en', utc_now_naive())
            _safe_setattr(entrevista, 'actualizada_en', None)
            _safe_setattr(entrevista, 'tipo', (tipo or '').strip().lower())
            db.session.add(entrevista)
            db.session.flush()
            state["entrevista_id"] = int(getattr(entrevista, "id", 0) or 0)

            for p in preguntas:
                valor = respuestas_payload.get(int(p.id), "")
                r = EntrevistaRespuesta(
                    entrevista_id=entrevista.id,
                    pregunta_id=p.id,
                    respuesta=valor if valor else None,
                )
                _safe_setattr(r, 'creada_en', utc_now_naive())
                db.session.add(r)

            state["legacy_text"] = _build_legacy_interview_text(preguntas, respuestas_payload)
            if legacy_text_is_useful(state["legacy_text"]):
                candidata.entrevista = state["legacy_text"]

            try:
                maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
            except Exception:
                pass

        result = execute_robust_save(
            session=db.session,
            persist_fn=_persist_interview,
            verify_fn=lambda: _verify_interview_new_saved(int(state.get("entrevista_id") or 0), int(fila)),
        )

        if result.ok:
            metadata_ok = {
                "candidata_id": int(fila),
                "entrevista_nueva": True,
                "entrevista_legacy": bool(legacy_text_is_useful(state.get("legacy_text") or "")),
                "attempt_count": int(result.attempts),
                "bytes_length": {},
            }
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_SAVE_OK",
                candidata=candidata,
                summary=f"Guardado robusto entrevista OK ({(tipo or '').strip().lower() or 'domestica'})",
                metadata=metadata_ok,
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
                candidata=candidata,
                summary=f"Entrevista nueva guardada ({(tipo or '').strip().lower() or 'domestica'})",
                metadata={"entrevista_id": int(state.get("entrevista_id") or 0), "tipo": (tipo or "").strip().lower()},
                success=True,
            )
            flash("✅ Entrevista guardada.", "success")
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        current_app.logger.error(
            "❌ Error guardando entrevista (robust_save) fila=%s attempts=%s error=%s",
            fila,
            result.attempts,
            result.error_message,
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_SAVE_FAIL",
            candidata=candidata,
            summary=f"Fallo guardado robusto entrevista ({(tipo or '').strip().lower() or 'domestica'})",
            metadata={
                "candidata_id": int(fila),
                "entrevista_nueva": True,
                "entrevista_legacy": bool(legacy_text_is_useful(state.get("legacy_text") or "")),
                "attempt_count": int(result.attempts),
                "error_message": (result.error_message or "")[:200],
                "bytes_length": {},
            },
            success=False,
            error="No se pudo guardar entrevista nueva de forma verificada.",
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
            candidata=candidata,
            summary=f"Fallo guardando entrevista nueva ({(tipo or '').strip().lower() or 'domestica'})",
            metadata={"tipo": (tipo or "").strip().lower()},
            success=False,
            error="No se pudo guardar la entrevista nueva.",
        )
        flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
        return redirect(url_for('entrevista_nueva_db', fila=fila, tipo=tipo))

    return render_template(
        "entrevistas/entrevista_form.html",
        modo="nueva",
        tipo=(tipo or '').strip().lower(),
        candidata=candidata,
        preguntas=preguntas,
        respuestas_por_pregunta={},
        entrevista=None
    )

# Compatibilidad: soporta links viejos tipo /entrevistas/editar?id=123 o ?entrevista_id=123
@app.route('/entrevistas/editar', methods=['GET'])
@roles_required('admin', 'secretaria')
def entrevista_editar_redirect():
    """Compat: soporta links viejos tipo /entrevistas/editar?id=123 o ?entrevista_id=123"""
    eid = (request.args.get('entrevista_id', type=int)
           or request.args.get('id', type=int))
    if not eid:
        abort(404)
    return redirect(url_for('entrevista_editar_db', entrevista_id=eid))


@app.route("/entrevistas/editar/<int:entrevista_id>", methods=["GET", "POST"])
@roles_required('admin', 'secretaria')
def entrevista_editar_db(entrevista_id):
    entrevista = Entrevista.query.get_or_404(entrevista_id)
    fila = getattr(entrevista, 'candidata_id', None)

    candidata = _get_candidata_safe_by_pk(fila) if fila else None
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))
    blocked = assert_candidata_no_descalificada(
        candidata,
        action="editar entrevista",
        redirect_endpoint="entrevistas_de_candidata",
        redirect_kwargs={"fila": fila},
    )
    if blocked is not None:
        return blocked

    # Cargar respuestas actuales
    respuestas = (
        EntrevistaRespuesta.query
        .filter_by(entrevista_id=entrevista.id)
        .all()
    )
    respuestas_por_pregunta = {r.pregunta_id: (r.respuesta or "") for r in respuestas}

    # Detectar tipo:
    tipo = None
    if hasattr(entrevista, 'tipo') and getattr(entrevista, 'tipo', None):
        tipo = (getattr(entrevista, 'tipo') or '').strip().lower()

    if not tipo and respuestas:
        p0 = EntrevistaPregunta.query.get(respuestas[0].pregunta_id)
        if p0 and p0.clave and "." in p0.clave:
            tipo = p0.clave.split(".", 1)[0]

    tipo = tipo or "domestica"

    preguntas = _get_preguntas_db_por_tipo(tipo)
    if not preguntas:
        flash("⚠️ No hay preguntas configuradas para este tipo.", "warning")
        return redirect(url_for('entrevistas_de_candidata', fila=fila))

    if request.method == "POST":
        respuestas_payload = {}
        for p in preguntas:
            field = f"q_{p.id}"
            respuestas_payload[int(p.id)] = (request.form.get(field) or "").strip()

        if not any(legacy_text_is_useful(v) for v in respuestas_payload.values()):
            flash("❌ La entrevista está vacía o no es válida. Completa al menos una respuesta útil.", "danger")
            return redirect(url_for('entrevista_editar_db', entrevista_id=entrevista.id))

        def _persist_interview_update(_attempt: int):
            for p in preguntas:
                valor = respuestas_payload.get(int(p.id), "")
                r = (
                    EntrevistaRespuesta.query
                    .filter_by(entrevista_id=entrevista.id, pregunta_id=p.id)
                    .first()
                )

                if not r:
                    r = EntrevistaRespuesta(
                        entrevista_id=entrevista.id,
                        pregunta_id=p.id,
                    )
                    _safe_setattr(r, 'creada_en', utc_now_naive())
                    db.session.add(r)

                r.respuesta = valor if valor else None
                _safe_setattr(r, 'actualizada_en', utc_now_naive())

            _safe_setattr(entrevista, 'actualizada_en', utc_now_naive())
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'tipo', tipo)

            legacy_text = _build_legacy_interview_text(preguntas, respuestas_payload)
            if legacy_text_is_useful(legacy_text):
                candidata.entrevista = legacy_text

            try:
                maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
            except Exception:
                pass

        result = execute_robust_save(
            session=db.session,
            persist_fn=_persist_interview_update,
            verify_fn=lambda: _verify_interview_new_saved(int(getattr(entrevista, "id", 0) or 0), int(fila)),
        )

        if result.ok:
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_SAVE_OK",
                candidata=candidata,
                summary=f"Guardado robusto entrevista OK ({tipo})",
                metadata={
                    "candidata_id": int(fila),
                    "entrevista_id": int(getattr(entrevista, "id", 0) or 0),
                    "entrevista_nueva": True,
                    "entrevista_legacy": True,
                    "attempt_count": int(result.attempts),
                    "bytes_length": {},
                },
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
                candidata=candidata,
                summary=f"Entrevista actualizada ({tipo})",
                metadata={"entrevista_id": entrevista.id, "tipo": tipo},
                success=True,
            )
            flash("✅ Entrevista actualizada.", "success")
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        current_app.logger.error(
            "❌ Error actualizando entrevista (robust_save) entrevista_id=%s attempts=%s error=%s",
            entrevista.id,
            result.attempts,
            result.error_message,
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_SAVE_FAIL",
            candidata=candidata,
            summary=f"Fallo guardado robusto entrevista ({tipo})",
            metadata={
                "candidata_id": int(fila),
                "entrevista_id": int(getattr(entrevista, "id", 0) or 0),
                "entrevista_nueva": True,
                "entrevista_legacy": True,
                "attempt_count": int(result.attempts),
                "error_message": (result.error_message or "")[:200],
                "bytes_length": {},
            },
            success=False,
            error="No se pudo guardar entrevista de forma verificada.",
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
            candidata=candidata,
            summary=f"Fallo actualizando entrevista ({tipo})",
            metadata={"entrevista_id": entrevista.id, "tipo": tipo},
            success=False,
            error="No se pudo actualizar la entrevista.",
        )
        flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
        return redirect(url_for('entrevista_editar_db', entrevista_id=entrevista.id))

    return render_template(
        "entrevistas/entrevista_form.html",
        modo="editar",
        tipo=tipo,
        candidata=candidata,
        preguntas=preguntas,
        respuestas_por_pregunta=respuestas_por_pregunta,
        entrevista=entrevista
    )

# -----------------------------------------------------------------------------
# PDF ENTREVISTA (NUEVAS - DB)
#   - NO toca ni reemplaza el PDF viejo (/generar_pdf_entrevista)
#   - Exporta una entrevista guardada en tablas Entrevista/EntrevistaRespuesta
# -----------------------------------------------------------------------------

@app.route('/entrevistas/pdf/<int:entrevista_id>')
@roles_required('admin', 'secretaria')
def generar_pdf_entrevista_db(entrevista_id: int):
    # Asegura fpdf2
    try:
        from fpdf import FPDF as _FPDF
        from fpdf.errors import FPDFException
    except Exception:
        return "❌ fpdf2 no está instalado. Ejecuta: pip uninstall -y fpdf && pip install -U fpdf2", 500

    entrevista = Entrevista.query.get_or_404(entrevista_id)

    fila = getattr(entrevista, 'candidata_id', None)
    candidata = _get_candidata_safe_by_pk(int(fila)) if fila else None
    if not candidata:
        return "Candidata no encontrada", 404

    # Respuestas + preguntas
    respuestas = (
        EntrevistaRespuesta.query
        .filter_by(entrevista_id=entrevista.id)
        .all()
    )
    if not respuestas:
        return "No hay respuestas registradas para esta entrevista.", 404

    pregunta_ids = [r.pregunta_id for r in respuestas if r.pregunta_id]
    preguntas = (
        EntrevistaPregunta.query
        .filter(EntrevistaPregunta.id.in_(pregunta_ids))
        .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
        .all()
    )

    respuestas_por_pregunta = {r.pregunta_id: (r.respuesta or "").strip() for r in respuestas}

    # ⚠️ IMPORTANTE (clientes): NO incluimos datos personales en el PDF.
    # NO incluimos nombre, cedula, telefono, direccion, modalidad, ni fecha en el PDF.
    tipo = (getattr(entrevista, 'tipo', None) or '').strip().lower()

    # Referencias (SOLO las columnas oficiales del modelo)
    # ✅ NO usamos `contactos_referencias_laborales` ni `referencias_familiares_detalle`
    # porque son otros campos y podrían mezclar información.
    ref_laborales = (getattr(candidata, 'referencias_laboral', None) or '').strip()
    ref_familiares = (getattr(candidata, 'referencias_familiares', None) or '').strip()

    BRAND = (0, 102, 204)
    FAINT = (120, 120, 120)
    GRID  = (210, 210, 210)

    def _ascii_if_needed(s: str, unicode_ok: bool) -> str:
        if unicode_ok:
            return s or ""
        s = s or ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 0x2500)

    def _collapse_ws(s: str) -> str:
        return re.sub(r"[ \t]+", " ", (s or "").strip())

    def _pretty_question(pregunta) -> str:
        """Prioriza enunciado/etiqueta, y si no hay, humaniza la clave."""
        for attr in ('enunciado','pregunta','texto_pregunta','texto','label','etiqueta','titulo','nombre','descripcion'):
            v = (getattr(pregunta, attr, None) or '').strip()
            if v:
                return humanize_pdf_label(v)

        clave = (getattr(pregunta, 'clave', None) or '').strip()
        return humanize_pdf_label(clave) or 'Pregunta'

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
            try:
                pdf.set_font("Arial", font_style or "", max(10, int(font_size)))
            except Exception:
                pdf.set_font("Arial", "", 10)

        try:
            pdf.multi_cell(pdf.epw, 7, txt, align=align)
            pdf.ln(line_space)
        except FPDFException:
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
            self._has_italic  = False
            self._has_bold    = False
            self._has_bi      = False

        def header(self):
            if self.page_no() == 1:
                if self._logo_path and os.path.exists(self._logo_path):
                    w = 92
                    x = (self.w - w) / 2.0
                    self.image(self._logo_path, x=x, y=10, w=w)
                    y_line = 10 + (w * 0.38)
                    self.set_y(y_line)
                else:
                    self.set_y(18)

                self.set_draw_color(*GRID)
                self.set_line_width(0.6)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(3)

                try:
                    self.set_font(self._base_font, "B", 18 if self._has_bold else 17)
                except Exception:
                    self.set_font("Arial", "B", 18)

                # Barra azul con título (como el PDF viejo)
                self.set_fill_color(*BRAND)
                self.set_text_color(255, 255, 255)
                self.cell(self.epw, 11, "Entrevista", ln=True, align="C", fill=True)
                self.set_text_color(0, 0, 0)
                self.ln(4)
            else:
                self.set_y(14)
                self.set_draw_color(*GRID)
                self.set_line_width(0.4)
                self.line(self.l_margin, 14, self.w - self.r_margin, 14)
                self.ln(7)

        def footer(self):
            self.set_y(-15)
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
        pdf._logo_path = os.path.join(current_app.root_path, "static", "logo_nuevo.png")

        base_font  = "Arial"
        unicode_ok = False
        has_bold   = False
        has_italic = False
        has_bi     = False

        try:
            font_dir = os.path.join(current_app.root_path, "static", "fonts")
            reg  = os.path.join(font_dir, "DejaVuSans.ttf")
            bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            it   = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")
            bi   = os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")

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
            base_font  = "Arial"
            unicode_ok = False
            has_bold   = True
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

        for p in preguntas:
            q_txt = _pretty_question(p)
            ans = (respuestas_por_pregunta.get(p.id) or '').strip()

            q_line = _collapse_ws(_ascii_if_needed(q_txt, unicode_ok))
            a_line = _wrap_unbreakables(_collapse_ws(_ascii_if_needed(ans, unicode_ok)), 80)

            # Pregunta (negro)
            safe_multicell(
                pdf,
                (q_line + ":").strip(),
                base_font,
                "B" if has_bold else "",
                12,
                color=(0, 0, 0),
                align="L",
                line_space=1,
            )

            # Respuesta (azul)
            if a_line:
                a_out = (bullet + a_line).strip()
            else:
                a_out = (bullet + "—").strip()

            safe_multicell(
                pdf,
                a_out,
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
                line_space=2,
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

        if ref_laborales:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_laborales, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias laborales.", base_font, "", 12, color=FAINT, align="L")

        # Familiares
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Familiares:", ln=True)

        if ref_familiares:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_familiares, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias familiares.", base_font, "", 12, color=FAINT, align="L")

        raw = pdf.output(dest="S")
        pdf_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("latin1", "ignore")
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_{(tipo or 'general')}_{entrevista.id}.pdf"
        )

    except Exception as e:
        current_app.logger.exception("❌ Error interno generando PDF entrevista (DB)")
        return f"Error interno generando PDF: {e}", 500

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
            obj = get_candidata_by_id(cid)
            if obj:
                audit_fields = [
                    "nombre_completo",
                    "edad",
                    "numero_telefono",
                    "direccion_completa",
                    "modalidad_trabajo_preferida",
                    "rutas_cercanas",
                    "empleo_anterior",
                    "anos_experiencia",
                    "areas_experiencia",
                    "contactos_referencias_laborales",
                    "referencias_familiares_detalle",
                    "cedula",
                    "sabe_planchar",
                    "acepta_porcentaje_sueldo",
                    "disponibilidad_inicio",
                    "trabaja_con_ninos",
                    "trabaja_con_mascotas",
                    "puede_dormir_fuera",
                    "sueldo_esperado",
                    "motivacion_trabajo",
                ]
                before_snapshot = snapshot_model_fields(obj, audit_fields)
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
                obj.contactos_referencias_laborales  = (request.form.get('contactos_referencias_laborales') or '').strip()[:250] or obj.contactos_referencias_laborales
                obj.referencias_familiares_detalle   = (request.form.get('referencias_familiares_detalle') or '').strip()[:250] or obj.referencias_familiares_detalle

                # Campos opcionales nuevos (registro público + compatibilidad legacy)
                if 'disponibilidad_inicio' in request.form:
                    obj.disponibilidad_inicio = (request.form.get('disponibilidad_inicio') or '').strip()[:80] or None
                if 'sueldo_esperado' in request.form:
                    obj.sueldo_esperado = (request.form.get('sueldo_esperado') or '').strip()[:80] or None
                if 'motivacion_trabajo' in request.form:
                    obj.motivacion_trabajo = (request.form.get('motivacion_trabajo') or '').strip()[:350] or None

                def _parse_optional_bool(raw: str):
                    val = (raw or '').strip().lower().replace('í', 'i')
                    if val in ('si', '1', 'true', 'on'):
                        return True
                    if val in ('no', '0', 'false', 'off'):
                        return False
                    return None

                if 'trabaja_con_ninos' in request.form:
                    obj.trabaja_con_ninos = _parse_optional_bool(request.form.get('trabaja_con_ninos'))
                if 'trabaja_con_mascotas' in request.form:
                    obj.trabaja_con_mascotas = _parse_optional_bool(request.form.get('trabaja_con_mascotas'))
                if 'puede_dormir_fuera' in request.form:
                    obj.puede_dormir_fuera = _parse_optional_bool(request.form.get('puede_dormir_fuera'))

                cedula_edit_raw = (request.form.get('cedula') or '').strip()[:50]
                if cedula_edit_raw:
                    cedula_edit_digits = normalize_cedula_for_compare(cedula_edit_raw)
                    if not cedula_edit_digits:
                        mensaje = "❌ Cédula inválida."
                        candidata = obj
                        return render_template(
                            'buscar.html',
                            busqueda=busqueda,
                            resultados=resultados,
                            candidata=candidata,
                            mensaje=mensaje
                        )

                    dup, _ = find_duplicate_candidata_by_cedula(
                        cedula_edit_raw,
                        exclude_fila=getattr(obj, 'fila', None)
                    )
                    if dup:
                        mensaje = duplicate_cedula_message(dup)
                        candidata = obj
                        return render_template(
                            'buscar.html',
                            busqueda=busqueda,
                            resultados=resultados,
                            candidata=candidata,
                            mensaje=mensaje
                        )

                    # En edición no se reescribe formato automáticamente.
                    obj.cedula = cedula_edit_raw
                # ⚠️ IMPORTANTE:
                # Los campos booleanos NO deben resetearse a False si el formulario no los trae.
                # (Checkboxes no marcados a veces NO se envían; eso estaba borrando valores.)

                # Sabe planchar: solo actualizar si el form trae el campo
                if 'sabe_planchar' in request.form:
                    v_planchar = (request.form.get('sabe_planchar') or '').strip().lower()
                    # Acepta varios formatos: 'si', 'sí', 'true', '1', 'on'
                    obj.sabe_planchar = v_planchar in ('si', 'sí', 'true', '1', 'on')

                # Acepta porcentaje: solo actualizar si el form trae el campo
                if 'acepta_porcentaje' in request.form:
                    v_pct = (request.form.get('acepta_porcentaje') or '').strip().lower()
                    obj.acepta_porcentaje_sueldo = v_pct in ('si', 'sí', 'true', '1', 'on')

                expected_verify = {
                    "nombre_completo": (obj.nombre_completo or "").strip(),
                    "edad": (obj.edad or "").strip(),
                    "numero_telefono": (obj.numero_telefono or "").strip(),
                    "direccion_completa": (obj.direccion_completa or "").strip(),
                    "modalidad_trabajo_preferida": (obj.modalidad_trabajo_preferida or "").strip(),
                    "rutas_cercanas": (obj.rutas_cercanas or "").strip(),
                    "empleo_anterior": (obj.empleo_anterior or "").strip(),
                    "anos_experiencia": (obj.anos_experiencia or "").strip(),
                    "areas_experiencia": (obj.areas_experiencia or "").strip(),
                    "contactos_referencias_laborales": (obj.contactos_referencias_laborales or "").strip(),
                    "referencias_familiares_detalle": (obj.referencias_familiares_detalle or "").strip(),
                    "cedula": (obj.cedula or "").strip(),
                }
                if 'disponibilidad_inicio' in request.form:
                    expected_verify["disponibilidad_inicio"] = (obj.disponibilidad_inicio or "").strip() or None
                if 'sueldo_esperado' in request.form:
                    expected_verify["sueldo_esperado"] = (obj.sueldo_esperado or "").strip() or None
                if 'motivacion_trabajo' in request.form:
                    expected_verify["motivacion_trabajo"] = (obj.motivacion_trabajo or "").strip() or None
                if 'sabe_planchar' in request.form:
                    expected_verify["sabe_planchar"] = bool(obj.sabe_planchar)
                if 'acepta_porcentaje' in request.form:
                    expected_verify["acepta_porcentaje_sueldo"] = bool(obj.acepta_porcentaje_sueldo)
                if 'trabaja_con_ninos' in request.form:
                    expected_verify["trabaja_con_ninos"] = obj.trabaja_con_ninos
                if 'trabaja_con_mascotas' in request.form:
                    expected_verify["trabaja_con_mascotas"] = obj.trabaja_con_mascotas
                if 'puede_dormir_fuera' in request.form:
                    expected_verify["puede_dormir_fuera"] = obj.puede_dormir_fuera

                result = execute_robust_save(
                    session=db.session,
                    persist_fn=lambda _attempt: None,
                    verify_fn=lambda: _verify_candidata_fields_saved(int(obj.fila), expected_verify, fallback_obj=obj),
                )

                if result.ok:
                    after_snapshot = snapshot_model_fields(obj, audit_fields)
                    changes = diff_snapshots(before_snapshot, after_snapshot)
                    log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Edición de candidata {obj.nombre_completo or obj.fila}",
                        metadata={"candidata_id": obj.fila, "attempt_count": int(result.attempts)},
                        changes=changes,
                        success=True,
                    )
                    flash("✅ Datos actualizados correctamente.", "success")
                    return redirect(url_for('buscar_candidata', candidata_id=cid))

                error_message = (result.error_message or "").lower()
                if "unique" in error_message or "duplicate" in error_message or "cedula" in error_message:
                    log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Fallo edición de candidata {obj.fila}",
                        metadata={"attempt_count": int(result.attempts)},
                        success=False,
                        error="Conflicto de cédula duplicada.",
                    )
                    mensaje = "⚠️ Ya existe una candidata con esta cédula (aunque esté escrita diferente)."
                else:
                    app.logger.error(
                        "❌ Error al guardar edición de candidata fila=%s attempts=%s error=%s",
                        obj.fila,
                        result.attempts,
                        result.error_message,
                    )
                    log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Fallo edición de candidata {obj.fila}",
                        metadata={"attempt_count": int(result.attempts)},
                        success=False,
                        error="Error al guardar edición de candidata.",
                    )
                    mensaje = "❌ Error al guardar. Intenta de nuevo."
            else:
                mensaje = "⚠️ Candidata no encontrada."
        else:
            mensaje = "❌ ID de candidata inválido."

    # Carga detalles (GET ?candidata_id=)
    cid = (request.args.get('candidata_id') or '').strip()
    if cid.isdigit():
        candidata = get_candidata_by_id(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."

    # ================== BÚSQUEDA ==================
    if busqueda and not candidata:
        try:
            resultados = search_candidatas_limited(busqueda, limit=300)

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

    def _terms(raw: str, max_terms: int = 8):
        tokens = [p.strip() for p in re.split(r'[,\s]+', raw or "") if p.strip()]
        return tokens[:max_terms]

    # Ciudad
    if form_data['ciudad']:
        ciudades = _terms(form_data['ciudad'])
        if ciudades:
            filtros.append(or_(*[Candidata.direccion_completa.ilike(f"%{c}%") for c in ciudades]))

    # Rutas
    if form_data['rutas']:
        rutas = _terms(form_data['rutas'])
        if rutas:
            filtros.append(or_(*[Candidata.rutas_cercanas.ilike(f"%{r}%") for r in rutas]))

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
    filtros.append(candidatas_activas_filter(Candidata))
    filtros.append(Candidata.codigo.isnot(None))
    filtros.append(or_(Candidata.porciento.is_(None), Candidata.porciento == 0))

    mensaje = None
    resultados = []

    try:
        query = (
            db.session.query(
                Candidata.nombre_completo,
                Candidata.codigo,
                Candidata.numero_telefono,
                Candidata.direccion_completa,
                Candidata.rutas_cercanas,
                Candidata.cedula,
                Candidata.modalidad_trabajo_preferida,
                Candidata.anos_experiencia,
                Candidata.estado,
            )
            .filter(*filtros)
            .order_by(Candidata.nombre_completo.asc())
        )
        candidatas = query.limit(500).all()

        if candidatas:
            resultados = [{
                'nombre':           c[0],
                'codigo':           c[1],
                'telefono':         c[2],
                'direccion':        c[3],
                'rutas':            c[4],
                'cedula':           c[5],
                'modalidad':        c[6],
                'experiencia_anos': c[7],
                'estado':           c[8]
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

            obj = get_candidata_by_id(cid)
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

            obj.fecha_cambio_estado    = utc_now_naive()
            obj.usuario_cambio_estado  = session.get('usuario', 'desconocido')[:64]
            try:
                actor = (
                    getattr(current_user, "username", None)
                    or getattr(current_user, "id", None)
                    or session.get("usuario")
                    or "sistema"
                )
                maybe_update_estado_por_completitud(obj, actor=str(actor))
            except Exception:
                pass

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
                try:
                    resultados = search_candidatas_limited(q, limit=300)
                    if not resultados:
                        flash("⚠️ No se encontraron coincidencias.", "error")
                except Exception:
                    app.logger.exception("❌ Error buscando en inscripción")
                    flash("❌ Error al buscar.", "error")

    else:
        q = (request.args.get("buscar") or "").strip()[:128]
        if q:
            try:
                resultados = search_candidatas_limited(q, limit=300)
                if not resultados:
                    mensaje = "⚠️ No se encontraron coincidencias."
            except Exception:
                app.logger.exception("❌ Error buscando candidatas (GET) en inscripción")
                mensaje = "❌ Error al buscar."

        sel = (request.args.get("candidata_seleccionada") or "").strip()
        if not resultados and sel.isdigit():
            candidata = get_candidata_by_id(sel)
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

        obj = get_candidata_by_id(fila_id)
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
        obj.fecha_cambio_estado   = utc_now_naive()
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
            try:
                resultados = search_candidatas_limited(q, limit=300)
                if not resultados:
                    flash("⚠️ No se encontraron coincidencias.", "warning")
            except Exception:
                app.logger.exception("❌ Error buscando (GET) en porciento")
                flash("❌ Error al buscar.", "warning")

        sel = (request.args.get('candidata') or '').strip()
        if sel.isdigit() and not resultados:
            candidata = get_candidata_by_id(sel)
            if not candidata:
                flash("⚠️ Candidata no encontrada.", "warning")

    return render_template("porciento.html", resultados=resultados, candidata=candidata)


@app.route('/pagos', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def pagos():
    resultados, candidata = [], None

    def _parse_money_to_decimal(raw: str) -> Decimal:
        """
        Acepta:
          - 10000
          - 10,000
          - 10.000
          - 10,000.50
          - 10.000,50
        Devuelve Decimal con 2 decimales.
        """
        s = (raw or "").strip()
        if not s:
            raise ValueError("Monto vacío")

        # deja solo dígitos y separadores comunes
        allowed = "0123456789.,"
        s = "".join(ch for ch in s if ch in allowed)

        if not s or not any(ch.isdigit() for ch in s):
            raise ValueError("Monto inválido")

        # Caso: tiene . y ,  -> el último separador es el decimal, el otro son miles
        if "." in s and "," in s:
            if s.rfind(",") > s.rfind("."):
                # 10.000,50 -> decimal=,
                s = s.replace(".", "")
                s = s.replace(",", ".")
            else:
                # 10,000.50 -> decimal=.
                s = s.replace(",", "")
        else:
            # Caso: solo tiene una coma -> puede ser miles o decimal
            if "," in s:
                # si tiene 1 coma y al final hay 1-2 dígitos, asumimos decimal (100,50)
                parts = s.split(",")
                if len(parts) == 2 and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
                    s = s.replace(",", ".")
                else:
                    # si no, asumimos miles (10,000)
                    s = s.replace(",", "")

            # Caso: solo tiene puntos -> puede ser miles o decimal
            if "." in s:
                parts = s.split(".")
                if len(parts) == 2 and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
                    # decimal: 100.50 (se deja)
                    pass
                else:
                    # miles: 10.000 o 1.000.000
                    s = s.replace(".", "")

        try:
            val = Decimal(s)
        except InvalidOperation:
            raise ValueError("Monto inválido")

        if val <= Decimal("0"):
            raise ValueError("El monto debe ser mayor que 0")

        return val.quantize(Decimal("0.01"))

    if request.method == 'POST':
        fila = request.form.get('fila', type=int)
        monto_str = (request.form.get('monto_pagado') or '').strip()[:30]
        calificacion = (request.form.get('calificacion') or '').strip()[:200]

        if not fila or not monto_str or not calificacion:
            flash("❌ Datos inválidos.", "danger")
            return redirect(url_for('pagos'))

        try:
            monto_pagado = _parse_money_to_decimal(monto_str)
        except Exception as e:
            flash(f"❌ Monto inválido: {e}", "danger")
            return redirect(url_for('pagos'))

        obj = get_candidata_by_id(fila)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('pagos'))

        # En PostgreSQL Numeric normalmente ya viene Decimal; esto lo deja seguro
        actual = obj.porciento if obj.porciento is not None else Decimal("0.00")
        try:
            actual = Decimal(str(actual))
        except Exception:
            actual = Decimal("0.00")

        nuevo = actual - monto_pagado
        if nuevo < Decimal("0"):
            nuevo = Decimal("0.00")

        obj.porciento = nuevo.quantize(Decimal("0.01"))
        obj.calificacion = calificacion

        # auditoría simple (opcional pero útil)
        try:
            obj.fecha_de_pago = rd_today()
        except Exception:
            pass

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
        try:
            filas = search_candidatas_limited(q, limit=300)

            resultados = [{
                'fila':     c.fila,
                'nombre':   c.nombre_completo,
                'cedula':   c.cedula,
                'telefono': c.numero_telefono or 'No especificado',
            } for c in filas]

            if not resultados:
                flash("⚠️ No se encontraron coincidencias.", "warning")
        except Exception:
            app.logger.exception("❌ Error buscando en pagos")
            flash("❌ Error al buscar.", "warning")

    if sel.isdigit() and not resultados:
        obj = get_candidata_by_id(sel)
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
        today = rd_today()
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
            "Fecha":        format_rd_datetime(r[8], "%Y-%m-%d", "") if r[8] else ""
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
        "Fecha":        format_rd_datetime(r[8], "%Y-%m-%d", "") if r[8] else ""
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
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_PAGOS_SECONDS", "45")),
    key_prefix=lambda: _cache_key_with_role("reporte_pagos"),
)
def reporte_pagos():
    """
    Reporte de pagos pendientes (porciento > 0).
    """
    page     = max(1, request.args.get('page', default=1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', default=20, type=int)))

    def _fetch_page():
        q = (
            db.session.query(
                Candidata.nombre_completo,
                Candidata.cedula,
                Candidata.codigo,
                Candidata.porciento
            )
            .filter(Candidata.porciento > 0)
            .order_by(Candidata.nombre_completo.asc())
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
        'porcentaje_pendiente': float(r[3] or 0),
    } for r in rows]

    mensaje = None if pagos_pendientes else "⚠️ No se encontraron pagos pendientes."
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        'reporte_pagos.html',
        pagos_pendientes=pagos_pendientes,
        mensaje=mensaje,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages
    )
# -----------------------------------------------------------------------------
# SUBIR FOTOS + GESTIONAR ARCHIVOS (BINARIOS EN DB)
# -----------------------------------------------------------------------------

from flask import (
    Blueprint, Response, abort,
    request, render_template, redirect, url_for, flash,
    current_app, send_file
)
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import io
import os
import re
import unicodedata

# OJO: asumo que ya tienes:
# - db
# - roles_required
# - Candidata
# - _retry_query (si no la tienes, te lo digo al final)
# -----------------------------------------------------------------------------

subir_bp = Blueprint('subir_fotos', __name__, url_prefix='/subir_fotos')

# Registrar blueprint (IMPORTANTE): se registra AL FINAL del archivo, luego de declarar todas las rutas.
# Si se registra aquí y luego declaras @subir_bp.route(...) más abajo, Flask lanza:
# AssertionError: blueprint already registered.
_DEFER_REGISTER_SUBIR_BP = True

# =========================
# Helpers comunes
# =========================

ALLOWED_IMG_FIELDS = ('depuracion', 'perfil', 'cedula1', 'cedula2')

def _is_bytes(x):
    return isinstance(x, (bytes, bytearray, memoryview))

def _to_bytes(data):
    if data is None:
        return None
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    try:
        return bytes(data)
    except Exception:
        return None

def _detect_mimetype_and_ext(data: bytes):
    if not data:
        return ("application/octet-stream", "bin")
    head = data[:12]
    if head.startswith(b"\x89PNG"):
        return ("image/png", "png")
    if head.startswith(b"\xFF\xD8\xFF"):
        return ("image/jpeg", "jpg")
    if head[:4] == b"GIF8":
        return ("image/gif", "gif")
    if head[:4] == b"%PDF":
        return ("application/pdf", "pdf")
    return ("application/octet-stream", "bin")

def _get_candidata_by_fila_or_pk(fila_id: int):
    """
    Robustez:
    - Primero intenta db.session.get (si fila es PK)
    - Si no, cae a filter_by(fila=)
    """
    if not fila_id:
        return None

    cand = None
    try:
        cand = db.session.get(Candidata, fila_id)
    except Exception:
        cand = None

    if cand:
        return cand

    try:
        return Candidata.query.filter_by(fila=fila_id).first()
    except Exception:
        return None

def _build_docs_flags(cand):
    if not cand:
        return {k: False for k in ALLOWED_IMG_FIELDS}
    return {
        'depuracion': bool(getattr(cand, 'depuracion', None)),
        'perfil': bool(getattr(cand, 'perfil', None)),
        'cedula1': bool(getattr(cand, 'cedula1', None)),
        'cedula2': bool(getattr(cand, 'cedula2', None)),
    }


def _upload_limits_view_context() -> dict:
    max_file_bytes = int(MAX_FILE_BYTES(current_app))
    max_file_mb = max_file_bytes / float(1024 * 1024)
    total_limit = int(current_app.config.get("MAX_CONTENT_LENGTH") or 0)
    return {
        "upload_max_file_bytes": max_file_bytes,
        "upload_max_file_mb": f"{max_file_mb:.1f}",
        "upload_total_limit_text": human_size(total_limit) if total_limit > 0 else "sin límite",
    }

# -----------------------------------------------------------------------------
# SUBIR FOTOS (BINARIOS EN DB)
# -----------------------------------------------------------------------------

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

            try:
                filas = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
            except Exception:
                current_app.logger.exception("❌ Error buscando en subir_fotos")
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

        return render_template('subir_fotos.html', accion='buscar', resultados=resultados, **_upload_limits_view_context())

    # ========================= MODO SUBIR =========================
    if accion == 'subir':
        if not fila_id:
            flash("❌ Debes seleccionar primero una candidata.", "danger")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        candidata = _get_candidata_by_fila_or_pk(fila_id)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        # GET: mostrar formulario con info de qué imágenes tiene
        if request.method == 'GET':
            tiene = _build_docs_flags(candidata)
            return render_template(
                'subir_fotos.html',
                accion='subir',
                fila=fila_id,
                tiene=tiene,
                **_upload_limits_view_context(),
            )

        # POST: guardar archivos
        files = {
            'depuracion': request.files.get('depuracion'),
            'perfil': request.files.get('perfil'),
            'cedula1': request.files.get('cedula1'),
            'cedula2': request.files.get('cedula2'),
        }

        # Filtrar solo los archivos realmente seleccionados (nombre no vacío)
        archivos_validos = {}
        for campo, archivo in files.items():
            if campo in ALLOWED_IMG_FIELDS and archivo and archivo.filename:
                archivos_validos[campo] = archivo

        if not archivos_validos:
            flash("⚠️ Debes seleccionar al menos una imagen para subir.", "warning")
            tiene = _build_docs_flags(candidata)
            return render_template(
                'subir_fotos.html',
                accion='subir',
                fila=fila_id,
                tiene=tiene,
                **_upload_limits_view_context(),
            )

        try:
            max_file = int(MAX_FILE_BYTES(current_app))
            payload_bytes = {}

            for campo, archivo in archivos_validos.items():
                if not archivo or not getattr(archivo, "filename", ""):
                    continue
                if file_too_large(archivo, max_file):
                    detected_size = int(get_filestorage_size(archivo) or 0)
                    max_txt = human_size(max_file)
                    size_txt = human_size(detected_size)
                    log_candidata_action(
                        action_type="CANDIDATA_UPLOAD_DOCS_SIZE_REJECT",
                        candidata=candidata,
                        summary=f"Archivo rechazado por tamaño en {campo}",
                        metadata={
                            "candidata_id": int(fila_id),
                            "field": campo,
                            "max_bytes": int(max_file),
                            "size_bytes": int(detected_size),
                            "source": "subir_fotos",
                        },
                        success=False,
                        error="Archivo supera límite por campo.",
                    )
                    flash(f"Archivo demasiado pesado para {campo}. Máximo: {max_txt}. Tu archivo: {size_txt}.", "danger")
                    return redirect(url_for('subir_fotos.subir_fotos', accion='subir', fila=fila_id))

            for campo, archivo in archivos_validos.items():
                _safe_seek_upload(archivo)
                ok, data, err, meta = validate_upload_file(archivo, max_bytes=max_file)
                if not ok:
                    current_app.logger.warning(
                        "⚠️ Upload inválido campo=%s archivo=%s motivo=%s",
                        campo,
                        (meta or {}).get("filename_safe", ""),
                        err,
                    )
                    flash(f"❌ Archivo inválido en {campo}: {err}", "danger")
                    tiene = _build_docs_flags(candidata)
                    return render_template('subir_fotos.html', accion='subir', fila=fila_id, tiene=tiene, **_upload_limits_view_context())
                if safe_bytes_length(data) <= 0:
                    flash(f"❌ Archivo inválido en {campo}: el archivo está vacío.", "danger")
                    tiene = _build_docs_flags(candidata)
                    return render_template('subir_fotos.html', accion='subir', fila=fila_id, tiene=tiene, **_upload_limits_view_context())
                payload_bytes[campo] = data

            if not payload_bytes:
                flash("⚠️ Debes seleccionar al menos una imagen para subir.", "warning")
                tiene = _build_docs_flags(candidata)
                return render_template('subir_fotos.html', accion='subir', fila=fila_id, tiene=tiene, **_upload_limits_view_context())

            def _persist_docs(_attempt: int):
                for campo, data in payload_bytes.items():
                    setattr(candidata, campo, data)
                try:
                    maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
                except Exception:
                    pass

            expected_lengths = {k: safe_bytes_length(v) for k, v in payload_bytes.items()}
            result = execute_robust_save(
                session=db.session,
                persist_fn=_persist_docs,
                verify_fn=lambda: _verify_candidata_docs_saved(int(fila_id), expected_lengths),
            )

            metadata_base = {
                "candidata_id": int(fila_id),
                "fields": sorted(list(payload_bytes.keys())),
                "source": "subir_fotos",
                "attempt_count": int(result.attempts),
                "bytes_length": expected_lengths,
            }
            if not result.ok:
                current_app.logger.error(
                    "❌ Error guardando imágenes (robust_save) fila=%s attempts=%s error=%s",
                    fila_id,
                    result.attempts,
                    result.error_message,
                )
                log_candidata_action(
                    action_type="CANDIDATA_UPLOAD_DOCS_SAVE_FAIL",
                    candidata=candidata,
                    summary="Fallo guardado robusto de documentos de candidata",
                    metadata={**metadata_base, "error_message": (result.error_message or "")[:200]},
                    success=False,
                    error="No se pudo guardar documentos de forma verificada.",
                )
                log_candidata_action(
                    action_type="CANDIDATA_UPLOAD_DOCS",
                    candidata=candidata,
                    summary="Fallo al subir/actualizar documentos de candidata",
                    metadata={"fields": sorted(list(payload_bytes.keys())), "source": "subir_fotos"},
                    success=False,
                    error="Error guardando imágenes en base de datos.",
                )
                flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
                tiene = _build_docs_flags(candidata)
                return render_template('subir_fotos.html', accion='subir', fila=fila_id, tiene=tiene, **_upload_limits_view_context())

            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS_SAVE_OK",
                candidata=candidata,
                summary="Guardado robusto de documentos de candidata",
                metadata=metadata_base,
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS",
                candidata=candidata,
                summary="Carga/actualización de documentos de candidata",
                metadata={
                    "fields": sorted(list(payload_bytes.keys())),
                    "source": "subir_fotos",
                },
                success=True,
            )
            flash("✅ Imágenes subidas/actualizadas correctamente.", "success")
            return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando imágenes en la BD")
            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS_SAVE_FAIL",
                candidata=candidata,
                summary="Fallo guardado robusto de documentos de candidata",
                metadata={
                    "candidata_id": int(fila_id),
                    "fields": sorted(list(archivos_validos.keys())),
                    "source": "subir_fotos",
                    "attempt_count": 1,
                    "bytes_length": {},
                },
                success=False,
                error="No se pudo guardar documentos de forma verificada.",
            )
            flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
            tiene = _build_docs_flags(candidata)
            return render_template('subir_fotos.html', accion='subir', fila=fila_id, tiene=tiene, **_upload_limits_view_context())

    return redirect(url_for('subir_fotos.subir_fotos', accion='buscar'))


@subir_bp.route('/imagen/<int:fila>/<campo>')
@roles_required('admin', 'secretaria')
def ver_imagen(fila, campo):
    """
    Sirve la imagen guardada en la BD para depuración/perfil/cedula1/cedula2.
    """
    if campo not in ALLOWED_IMG_FIELDS:
        abort(404)

    cand = _get_candidata_by_fila_or_pk(fila)
    if not cand:
        abort(404)

    data = _to_bytes(getattr(cand, campo, None))
    if not data:
        abort(404)

    mt, _ext = _detect_mimetype_and_ext(data)
    # Para seguridad: solo servir como imagen (si no es imagen, no servimos aquí)
    if not mt.startswith("image/"):
        abort(404)

    return Response(data, mimetype=mt)


# ✅ IMPORTANTE (la causa del error):
# Estas 2 líneas TIENEN que estar al FINAL DE app.py, después del ÚLTIMO @subir_bp.route(...)
if 'subir_fotos' not in app.blueprints:
    app.register_blueprint(subir_bp)

# -----------------------------------------------------------------------------
# GESTIONAR ARCHIVOS / PDF (DB only)  ✅ MEJORADO
# -----------------------------------------------------------------------------
from flask import render_template, redirect, url_for, request, flash
from sqlalchemy import or_

@app.route("/gestionar_archivos", methods=["GET", "POST"])
@roles_required('admin', 'secretaria')
def gestionar_archivos():
    accion = (request.args.get("accion") or "buscar").strip().lower()
    mensaje = None
    resultados = []
    docs = {}
    fila = (request.args.get("fila") or "").strip()

    # ✅ Helper: NO pasar binarios al template, solo booleanos + entrevista
    def build_docs_flags(c):
        if not c:
            return {
                "depuracion": False,
                "perfil": False,
                "cedula1": False,
                "cedula2": False,
                "entrevista": "",
            }

        return {
            "depuracion": bool(getattr(c, "depuracion", None)),
            "perfil": bool(getattr(c, "perfil", None)),
            "cedula1": bool(getattr(c, "cedula1", None)),
            "cedula2": bool(getattr(c, "cedula2", None)),
            # 👇 entrevista sí la pasamos (es texto, no pesado)
            "entrevista": (getattr(c, "entrevista", "") or "").strip(),
        }

    # ========================= DESCARGAR (PDF) =========================
    if accion == "descargar":
        doc = (request.args.get("doc") or "").strip().lower()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        idx = int(fila)

        if doc == "pdf":
            # PDF entrevista
            return redirect(url_for("generar_pdf_entrevista", fila=idx))

        return "Documento no reconocido", 400

    # ========================= BUSCAR =========================
    if accion == "buscar":
        if request.method == "POST":
            q = (request.form.get("busqueda") or "").strip()[:128]
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for("gestionar_archivos", accion="buscar"))

            try:
                filas = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
            except Exception:
                current_app.logger.exception("❌ Error buscando en gestionar_archivos")
                filas = []

            if filas:
                resultados = [{
                    "fila": c.fila,
                    "nombre": c.nombre_completo,
                    "telefono": c.numero_telefono or "No especificado",
                    "cedula": c.cedula or "No especificado"
                } for c in filas]
            else:
                mensaje = "⚠️ No se encontraron candidatas."

        return render_template(
            "gestionar_archivos.html",
            accion="buscar",
            resultados=resultados,
            mensaje=mensaje
        )

    # ========================= VER =========================
    if accion == "ver":
        if not fila.isdigit():
            mensaje = "Error: Fila inválida."
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        idx = int(fila)
        c = Candidata.query.filter_by(fila=idx).first()
        if not c:
            mensaje = "⚠️ Candidata no encontrada."
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        docs = build_docs_flags(c)

        return render_template(
            "gestionar_archivos.html",
            accion="ver",
            fila=idx,
            docs=docs,
            mensaje=mensaje
        )

    # Si viene una acción rara, volvemos a buscar
    return redirect(url_for("gestionar_archivos", accion="buscar"))



@app.route('/generar_pdf_entrevista')
@roles_required('admin', 'secretaria')
def generar_pdf_entrevista():
    # Asegura fpdf2
    try:
        from fpdf import FPDF as _FPDF
        from fpdf.errors import FPDFException
    except Exception:
        return "❌ fpdf2 no está instalado. Ejecuta: pip uninstall -y fpdf && pip install -U fpdf2", 500

    fila_index = request.args.get('fila', type=int)
    if not fila_index:
        return "Error: falta parámetro fila", 400

    c = _get_candidata_by_fila_or_pk(fila_index)
    if not c:
        return "Candidata no encontrada", 404

    texto_entrevista = (getattr(c, "entrevista", None) or "").strip()
    if not texto_entrevista:
        return "No hay entrevista registrada para esa fila", 404

    # OJO: aquí estás usando nombres que tal vez no coincidan con tu modelo real.
    # Si tus columnas se llaman diferente, se ajusta después.
    ref_laborales  = (getattr(c, "referencias_laboral", "") or "").strip()
    ref_familiares = (getattr(c, "referencias_familiares", "") or "").strip()

    BRAND = (0, 102, 204)
    FAINT = (120, 120, 120)
    GRID  = (210, 210, 210)

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
            try:
                pdf.set_font("Arial", font_style or "", max(10, int(font_size)))
            except Exception:
                pdf.set_font("Arial", "", 10)

        try:
            pdf.multi_cell(pdf.epw, 7, txt, align=align)
            pdf.ln(line_space)
        except FPDFException:
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
            self._has_italic  = False
            self._has_bold    = False
            self._has_bi      = False

        def header(self):
            if self.page_no() == 1:
                if self._logo_path and os.path.exists(self._logo_path):
                    w = 92
                    x = (self.w - w) / 2.0
                    self.image(self._logo_path, x=x, y=10, w=w)
                    y_line = 10 + (w * 0.38)
                    self.set_y(y_line)
                else:
                    self.set_y(18)

                self.set_draw_color(*GRID)
                self.set_line_width(0.6)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(3)

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
                self.set_y(14)
                self.set_draw_color(*GRID)
                self.set_line_width(0.4)
                self.line(self.l_margin, 14, self.w - self.r_margin, 14)
                self.ln(7)

        def footer(self):
            self.set_y(-15)
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
        pdf._logo_path = os.path.join(current_app.root_path, "static", "logo_nuevo.png")

        base_font  = "Arial"
        unicode_ok = False
        has_bold   = False
        has_italic = False
        has_bi     = False

        try:
            font_dir = os.path.join(current_app.root_path, "static", "fonts")
            reg  = os.path.join(font_dir, "DejaVuSans.ttf")
            bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            it   = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")
            bi   = os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")

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
            base_font  = "Arial"
            unicode_ok = False
            has_bold   = True
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
                q = _collapse_ws(humanize_pdf_label(q))
                a = _collapse_ws(a)

                safe_multicell(pdf, (q + ":").strip(), base_font, "B" if has_bold else "", 12,
                               color=(0, 0, 0), align="L", line_space=1)

                ans = _wrap_unbreakables(a, 60)
                ans = (bullet + ans) if ans else ans
                safe_multicell(pdf, ans, base_font, "", 12, color=BRAND, align="J", line_space=2)
            else:
                safe_multicell(pdf, _wrap_unbreakables(line, 60), base_font, "", 12,
                               color=(0, 0, 0), align="J", line_space=1.5)

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

        if ref_laborales:
            safe_multicell(pdf, _wrap_unbreakables(_ascii_if_needed(ref_laborales, unicode_ok), 60),
                           base_font, "", 12, color=BRAND, align="J")
        else:
            safe_multicell(pdf, "No hay referencias laborales.", base_font, "", 12, color=FAINT, align="L")

        # Familiares
        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Familiares:", ln=True)

        if ref_familiares:
            safe_multicell(pdf, _wrap_unbreakables(_ascii_if_needed(ref_familiares, unicode_ok), 60),
                           base_font, "", 12, color=BRAND, align="J")
        else:
            safe_multicell(pdf, "No hay referencias familiares.", base_font, "", 12, color=FAINT, align="L")

        raw = pdf.output(dest="S")
        pdf_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("latin1", "ignore")
        buf = io.BytesIO(pdf_bytes); buf.seek(0)

        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_candidata_{fila_index}.pdf"
        )

    except Exception as e:
        current_app.logger.exception("❌ Error interno generando PDF")
        return f"Error interno generando PDF: {e}", 500

# -----------------------------------------------------------------------------
# NUEVO PDF (ENTREVISTAS NUEVAS EN BD)  ✅ NO BORRA LO VIEJO
# -----------------------------------------------------------------------------

@app.route('/entrevistas/pdf_nuevo/<int:entrevista_id>', methods=['GET'])
@roles_required('admin', 'secretaria')
def generar_pdf_entrevista_nueva_db(entrevista_id: int):
    """Alias de compatibilidad para el endpoint nuevo de entrevista PDF."""
    return generar_pdf_entrevista_db(entrevista_id)


@app.route('/entrevistas/candidata/<int:fila>/pdf', methods=['GET'])
@roles_required('admin', 'secretaria')
def generar_pdf_ultima_entrevista_candidata(fila: int):
    """Acceso rápido: genera el PDF de la última entrevista (nuevo sistema) de una candidata por fila."""

    EntrevistaModel = globals().get('Entrevista')
    if EntrevistaModel is None:
        return "❌ No se encontró el modelo 'Entrevista' en el proyecto.", 500

    # Buscar última entrevista por candidata
    try:
        q = db.session.query(EntrevistaModel)
        # campo típico
        if hasattr(EntrevistaModel, 'candidata_id'):
            q = q.filter(EntrevistaModel.candidata_id == fila)
        elif hasattr(EntrevistaModel, 'fila'):
            q = q.filter(EntrevistaModel.fila == fila)
        elif hasattr(EntrevistaModel, 'candidata_fila'):
            q = q.filter(EntrevistaModel.candidata_fila == fila)

        # ordenar por fecha/id
        if hasattr(EntrevistaModel, 'actualizada_en'):
            q = q.order_by(EntrevistaModel.actualizada_en.desc())
        elif hasattr(EntrevistaModel, 'creada_en'):
            q = q.order_by(EntrevistaModel.creada_en.desc())
        else:
            q = q.order_by(EntrevistaModel.id.desc())

        last = q.first()
    except Exception:
        last = None

    if not last:
        return "No hay entrevistas nuevas registradas para esa candidata", 404

    return redirect(url_for('generar_pdf_entrevista_db', entrevista_id=int(getattr(last, 'id', 0))))


@app.route("/gestionar_archivos/descargar_uno", methods=["GET"])
@roles_required('admin', 'secretaria')
def descargar_uno_db():
    cid = request.args.get("id", type=int)
    doc = (request.args.get("doc") or "").strip().lower()

    if not cid or doc not in ("depuracion", "perfil", "cedula1", "cedula2"):
        return "Error: parámetros inválidos", 400

    # ✅ Cargar candidata (con tu retry)
    def _load():
        # SQLAlchemy moderno
        return db.session.get(Candidata, cid)

    try:
        candidata = _retry_query(_load, retries=1, swallow=False)
    except Exception:
        current_app.logger.exception("❌ Error consultando candidata en descargar_uno_db")
        candidata = None

    if not candidata:
        return "Candidata no encontrada", 404

    data = getattr(candidata, doc, None)
    if not data:
        return f"No hay archivo para {doc}", 404

    # ✅ Asegurar bytes (puede venir memoryview)
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    elif not isinstance(data, (bytes,)):
        try:
            data = bytes(data)
        except Exception:
            return "Formato de archivo inválido.", 400

    # ✅ Detectar mimetype por encabezado
    head = data[:12]
    if head.startswith(b"\x89PNG"):
        mt, ext = "image/png", "png"
    elif head.startswith(b"\xFF\xD8\xFF"):
        mt, ext = "image/jpeg", "jpg"
    elif head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        mt, ext = "image/gif", "gif"
    elif head.startswith(b"%PDF"):
        mt, ext = "application/pdf", "pdf"
    else:
        mt, ext = "application/octet-stream", "bin"

    # ✅ Nombre de archivo bonito y seguro
    nombre = (getattr(candidata, "nombre_completo", "") or "").strip()
    if not nombre:
        nombre = f"fila_{cid}"

    # sanitizar: letras/números/_/-
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", nombre)[:60].strip("_")
    filename = f"{doc}_{safe_name}_{cid}.{ext}"

    bio = io.BytesIO(data)
    bio.seek(0)

    current_app.logger.info("⬇️ Descargando doc=%s fila=%s nombre=%s", doc, cid, nombre)

    return send_file(
        bio,
        mimetype=mt,
        as_attachment=True,
        download_name=filename
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
            try:
                filas = search_candidatas_limited(termino, limit=300)
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
        candidata = get_candidata_by_id(candidata_id)
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
        candidata = get_candidata_by_id(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no existe."
        else:
            # Limitar tamaño para evitar payloads enormes
            cand_ref_lab = (request.form.get('referencias_laboral') or '').strip()[:5000]
            cand_ref_fam = (request.form.get('referencias_familiares') or '').strip()[:5000]

            if not legacy_text_is_useful(cand_ref_lab) or not legacy_text_is_useful(cand_ref_fam):
                mensaje = "⚠️ Referencias inválidas. Usa texto real (no placeholders)."
                return render_template('referencias.html',
                                       accion='ver',
                                       candidata=candidata,
                                       mensaje=mensaje)

            candidata.referencias_laboral = cand_ref_lab
            candidata.referencias_familiares = cand_ref_fam
            result = execute_robust_save(
                session=db.session,
                persist_fn=lambda _attempt: None,
                verify_fn=lambda: _verify_candidata_fields_saved(
                    int(cid),
                    {
                        "referencias_laboral": cand_ref_lab,
                        "referencias_familiares": cand_ref_fam,
                    },
                    fallback_obj=candidata,
                ),
            )
            if result.ok:
                mensaje = "✅ Referencias actualizadas."
            else:
                db.session.rollback()
                current_app.logger.error(
                    "❌ Error al guardar referencias candidata_id=%s attempts=%s error=%s",
                    cid,
                    result.attempts,
                    result.error_message,
                )
                mensaje = "❌ Error al guardar. No se pudo verificar la persistencia."

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
@cache.cached(
    timeout=int(os.getenv("CACHE_DASHBOARD_PROCESOS_SECONDS", "30")),
    key_prefix=lambda: _cache_key_with_role("dashboard_procesos"),
)
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
        hoy = rd_today()
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
                c.fecha_cambio_estado = utc_now_naive()
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
            created_at        = utc_now_naive()
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
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_LLAMADAS_SECONDS", "30")),
    key_prefix=lambda: _cache_key_with_role("reporte_llamadas"),
)
def reporte_llamadas_candidatas():
    period         = (request.args.get('period') or 'week').strip()[:16]
    start_date_str = request.args.get('start_date', None)
    start_dt       = get_start_date(period, start_date_str)
    hoy            = rd_today()
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
    max_calls_period = min(
        10000,
        max(100, int(os.getenv("MAX_REPORT_CALLS_PERIOD_ROWS", "2500")))
    )
    calls_period = calls_q.limit(max_calls_period).all()

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
    hoy = rd_today()

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
            "fecha_solicitud": format_rd_datetime(s.fecha_solicitud, "%Y-%m-%d %H:%M", "") if s.fecha_solicitud else "",
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

    if safe_bytes_length(foto_perfil_bytes) <= 0 or safe_bytes_length(cedula1_bytes) <= 0 or safe_bytes_length(cedula2_bytes) <= 0:
        flash("Los archivos no pueden estar vacíos.", "danger")
        return render_template('finalizar_proceso.html', candidata=candidata, grupos=grupos)

    foto_field = 'foto_perfil' if hasattr(candidata, 'foto_perfil') else ('perfil' if hasattr(candidata, 'perfil') else None)
    ok_foto = foto_field is not None
    ok_ced1 = hasattr(candidata, 'cedula1')
    ok_ced2 = hasattr(candidata, 'cedula2')

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

    try:
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        actor = str(actor)
    except Exception:
        actor = "sistema"

    expected_lengths = {}
    if foto_field:
        expected_lengths[foto_field] = safe_bytes_length(foto_perfil_bytes)
    if ok_ced1:
        expected_lengths["cedula1"] = safe_bytes_length(cedula1_bytes)
    if ok_ced2:
        expected_lengths["cedula2"] = safe_bytes_length(cedula2_bytes)

    def _persist_finalizar(_attempt: int):
        if foto_field:
            setattr(candidata, foto_field, foto_perfil_bytes)
        if ok_ced1:
            candidata.cedula1 = cedula1_bytes
        if ok_ced2:
            candidata.cedula2 = cedula2_bytes
        if grupos_sel:
            _save_grupos_empleo_safe(candidata, grupos_sel)
        try:
            maybe_update_estado_por_completitud(candidata, actor=actor)
        except Exception:
            pass

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist_finalizar,
        verify_fn=lambda: _verify_candidata_docs_saved(int(candidata.fila), expected_lengths),
    )

    if result.ok:
        log_candidata_action(
            action_type="CANDIDATA_UPLOAD_DOCS",
            candidata=candidata,
            summary="Finalización de proceso con carga de documentos",
            metadata={"fields": sorted(list(expected_lengths.keys())), "source": "finalizar_proceso", "attempt_count": int(result.attempts)},
            success=True,
        )
        flash("✅ Proceso finalizado y datos guardados correctamente.", "success")
        return redirect(url_for('candidata_ver_perfil', fila=candidata.fila))

    db.session.rollback()
    log_candidata_action(
        action_type="CANDIDATA_UPLOAD_DOCS",
        candidata=candidata,
        summary="Fallo finalizando proceso con documentos",
        metadata={"source": "finalizar_proceso", "attempt_count": int(result.attempts)},
        success=False,
        error=result.error_message,
    )
    flash("❌ Error guardando en la base de datos: no se pudo verificar la persistencia.", "danger")
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
COMPAT_TEST_CANDIDATA_VERSION = "v2.0"

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
COMPAT_MASCOTAS = list(MASCOTAS_CHOICES)
COMPAT_MASCOTAS_IMPORTANCIA = list(MASCOTAS_IMPORTANCIA_CHOICES)

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
    ("8am-5pm", "8:00 AM a 5:00 PM"),
    ("9am-6pm", "9:00 AM a 6:00 PM"),
    ("10am-6pm", "10:00 AM a 6:00 PM"),
    ("medio_tiempo", "Medio tiempo"),
    ("fin_de_semana", "Fin de semana"),
    ("noche_solo", "Solo de noche"),
    ("dormida_l-v", "Dormida (Lunes a Viernes)"),
    ("dormida_l-s", "Dormida (Lunes a Sábado)"),
    ("salida_quincenal", "Salida quincenal (cada 15 días)"),
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
    "MASCOTAS_IMPORTANCIA": COMPAT_MASCOTAS_IMPORTANCIA,
}
HORARIO_ORDER = {tok: idx for idx, (tok, _lbl) in enumerate(HORARIO_OPTIONS)}

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
        blocked = assert_candidata_no_descalificada(
            c,
            action="test de compatibilidad",
            redirect_endpoint="compat_candidata",
        )
        if blocked is not None:
            return blocked

        # Normalizaciones de selects/radios
        raw_comunicacion = request.form.get('comunicacion')
        raw_experiencia_nivel = request.form.get('experiencia_nivel')
        raw_puntualidad_1a5 = request.form.get('puntualidad_1a5')
        raw_mascotas = request.form.get('mascotas')
        raw_mascotas_importancia = request.form.get('mascotas_importancia')

        ritmo     = _norm_choice(request.form.get('ritmo'),               {k for k, _ in COMPAT_RITMOS})
        estilo    = _norm_choice(request.form.get('estilo'),              {k for k, _ in COMPAT_ESTILOS})
        comun     = _norm_choice(raw_comunicacion,                        {k for k, _ in COMPAT_COMUNICACION})
        rel_n     = _norm_choice(request.form.get('relacion_ninos'),      {k for k, _ in COMPAT_RELACION_NINOS})
        exp_niv   = _norm_choice(raw_experiencia_nivel,                   {k for k, _ in COMPAT_EXPERIENCIA_NIVEL})
        mascotas  = normalize_mascotas_token(raw_mascotas)
        mascotas_importancia = normalize_mascotas_importancia(raw_mascotas_importancia, default=None)
        puntual   = _int_1a5('puntualidad_1a5')
        current_app.logger.debug(
            "compat_candidata POST raw values fila=%s comunicacion=%r experiencia_nivel=%r puntualidad_1a5=%r mascotas=%r mascotas_importancia=%r",
            fila, raw_comunicacion, raw_experiencia_nivel, raw_puntualidad_1a5, raw_mascotas, raw_mascotas_importancia
        )
        current_app.logger.debug(
            "compat_candidata POST normalized fila=%s comun=%r exp_niv=%r puntual=%r mascotas=%r mascotas_importancia=%r",
            fila, comun, exp_niv, puntual, mascotas, mascotas_importancia
        )

        # Checkboxes (filtramos a los permitidos)
        fortalezas = _filter_allowed(_getlist_clean('fortalezas'),              {k for k, _ in FORTALEZAS})
        evitar     = _filter_allowed(_getlist_clean('tareas_evitar'),           {k for k, _ in TAREAS_EVITAR})
        limites    = _filter_allowed(_getlist_clean('limites_no_negociables'),  {k for k, _ in LIMITES_NO_NEG})
        dias       = _filter_allowed(_getlist_clean('disponibilidad_dias'),     {k for k, _ in DIAS_SEMANA})
        allowed_horarios = {k for k, _ in HORARIOS} | {
            'interna', 'manana', 'mañana', 'tarde', 'noche', 'flexible',
            'fin de semana', 'findesemana', 'weekend'
        }
        horarios_raw = _filter_allowed(_getlist_clean('disponibilidad_horarios'), allowed_horarios)
        horarios = sorted(normalize_horarios_tokens(horarios_raw), key=lambda t: HORARIO_ORDER.get(t, 999))

        notas = (request.form.get('nota') or '').strip()[:2000]

        # Validaciones mínimas
        err = []
        if not ritmo:         err.append("Ritmo de hogar")
        if not estilo:        err.append("Estilo de trabajo")
        if not comun:         err.append("Comunicación preferida")
        if not rel_n:         err.append("Relación con niños")
        if puntual is None:   err.append("Puntualidad (1 a 5)")
        if not mascotas:      err.append("Compatibilidad con mascotas")
        if not mascotas_importancia: err.append("Importancia de mascotas")
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
                "mascotas": mascotas, "mascotas_importancia": mascotas_importancia, "nota": notas
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
            if hasattr(c, 'compat_disponibilidad_horario'):  c.compat_disponibilidad_horario = ", ".join(horarios)

            if hasattr(c, 'compat_observaciones'):           c.compat_observaciones = notas

            profile = {
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
                "mascotas_importancia": mascotas_importancia,
                "nota": notas,
            }
            payload = {
                "version": COMPAT_TEST_CANDIDATA_VERSION,
                "timestamp": iso_utc_z(),
                "engine": ENGINE_VERSION,
                "profile": profile,
            }
            if hasattr(c, 'compat_test_candidata_json'):     c.compat_test_candidata_json = payload
            if hasattr(c, 'compat_test_candidata_version'):  c.compat_test_candidata_version = COMPAT_TEST_CANDIDATA_VERSION
            if hasattr(c, 'compat_test_candidata_at'):       c.compat_test_candidata_at = utc_now_naive()

            def _verify_compat_saved() -> bool:
                cand_db = _get_candidata_by_fila_or_pk(int(fila)) or c
                if not cand_db:
                    return False
                payload_db = getattr(cand_db, 'compat_test_candidata_json', None) or {}
                profile_db = payload_db.get('profile', {}) if isinstance(payload_db, dict) else {}
                if (profile_db.get("ritmo") or "") != (ritmo or ""):
                    return False
                if (profile_db.get("estilo") or "") != (estilo or ""):
                    return False
                if (profile_db.get("comunicacion") or "") != (comun or ""):
                    return False
                if int(profile_db.get("puntualidad_1a5") or 0) != int(puntual or 0):
                    return False
                return True

            result = execute_robust_save(
                session=db.session,
                persist_fn=lambda _attempt: None,
                verify_fn=_verify_compat_saved,
            )
            if not result.ok:
                raise RuntimeError(result.error_message or "No se pudo verificar guardado.")

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
        blocked = assert_candidata_no_descalificada(
            c,
            action="test de compatibilidad",
            redirect_endpoint="compat_candidata",
        )
        if blocked is not None:
            return blocked
        payload = getattr(c, 'compat_test_candidata_json', None) or {}
        profile = payload.get('profile', {}) if isinstance(payload, dict) else {}
        data = {
            "ritmo":                   getattr(c, 'compat_ritmo_preferido', None),
            "estilo":                  getattr(c, 'compat_estilo_trabajo', None),
            "comunicacion":            getattr(c, 'compat_comunicacion', None) or profile.get('comunicacion'),
            "relacion_ninos":          getattr(c, 'compat_relacion_ninos', None),
            "experiencia_nivel":       getattr(c, 'compat_experiencia_nivel', None) or profile.get('experiencia_nivel'),
            "puntualidad_1a5":         getattr(c, 'compat_puntualidad_1a5', None) or profile.get('puntualidad_1a5'),
            "fortalezas":              getattr(c, 'compat_habilidades_fuertes', None)
                                       or getattr(c, 'compat_fortalezas', [])
                                       or profile.get('fortalezas', []) or [],
            "tareas_evitar":           getattr(c, 'compat_habilidades_evitar', None)
                                       or getattr(c, 'compat_tareas_evitar', [])
                                       or profile.get('tareas_evitar', []) or [],
            "limites_no_negociables":  getattr(c, 'compat_limites_no_negociables', [])
                                       or profile.get('limites_no_negociables', []) or [],
            "disponibilidad_dias":     getattr(c, 'compat_disponibilidad_dias', [])
                                       or profile.get('disponibilidad_dias', []) or [],
            "disponibilidad_horarios": sorted(
                normalize_horarios_tokens(
                    getattr(c, 'compat_disponibilidad_horarios', [])
                    or profile.get('disponibilidad_horarios', [])
                    or []
                ),
                key=lambda t: HORARIO_ORDER.get(t, 999)
            ),
            "mascotas":                (getattr(c, 'compat_mascotas', None)
                                        if hasattr(c, 'compat_mascotas')
                                        else ('si' if getattr(c, 'compat_mascotas_ok', False) else 'no')
                                        if hasattr(c, 'compat_mascotas_ok') else profile.get('mascotas')),
            "mascotas_importancia":    profile.get('mascotas_importancia') or 'media',
            "nota":                    getattr(c, 'compat_observaciones', '') or profile.get('nota', '') or '',
        }
        return render_template('compat_candidata_form.html', candidata=c, data=data, CHOICES=CHOICES_DICT)

    # ── 3) BUSCADOR (GET/POST sin fila) ───────────────────────
    q = (request.values.get('q') or '').strip()[:128]
    resultados = []
    mensaje = None

    if request.method == 'POST' and request.form.get('accion') == 'buscar':
        q = (request.form.get('q') or '').strip()[:128]

    if q:
        try:
            resultados = search_candidatas_limited(
                q,
                limit=80,
                base_query=Candidata.query.filter(candidatas_activas_filter(Candidata)),
                minimal_fields=True,
                order_mode="id_desc",
                log_label="compat_candidata",
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


@app.route('/candidatas/eliminar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def eliminar_candidata():
    """
    Pantalla para eliminar candidatas manualmente:
    - Paso 1: Buscar por nombre, cédula, teléfono o código.
    - Paso 2: Ver detalle completo (docs, entrevista, etc.).
    - Paso 3: Confirmar eliminación definitiva.

    ✅ SOLO se permite eliminar candidatas SIN historial:
    - sin solicitudes
    - sin llamadas
    - sin reemplazos
    """

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────
    def _has_blob(v) -> bool:
        """Soporta bytes, bytearray, memoryview o None."""
        if v is None:
            return False
        if isinstance(v, memoryview):
            try:
                return len(v.tobytes()) > 0
            except Exception:
                return False
        if isinstance(v, (bytes, bytearray)):
            return len(v) > 0
        try:
            return bool(v)
        except Exception:
            return False

    def _safe_len(rel):
        """Cuenta relaciones sin explotar."""
        try:
            if rel is None:
                return 0
            return len(rel)
        except Exception:
            return 0

    def _count_scalar(query):
        """Devuelve el count como int seguro."""
        try:
            n = query.scalar()
            return int(n or 0)
        except Exception:
            return 0

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
                "tiene_historial": False,
                "puede_eliminar": False,
            }

        # ── Documentos binarios
        tiene_cedula1 = _has_blob(getattr(c, "cedula1", None))
        tiene_cedula2 = _has_blob(getattr(c, "cedula2", None))
        tiene_perfil  = _has_blob(getattr(c, "perfil", None))
        tiene_dep     = _has_blob(getattr(c, "depuracion", None))

        documentos_completos = (tiene_cedula1 and tiene_cedula2 and tiene_perfil and tiene_dep)

        # ── Entrevista (texto)
        entrevista_txt = (getattr(c, "entrevista", "") or "")
        entrevista_realizada = bool(str(entrevista_txt).strip())

        # ─────────────────────────────────────────
        # ✅ RELACIONES (ARREGLO REAL)
        # 1) Primero por relationship (lo más confiable)
        # 2) Si por algo falla, fallback por DB
        # ─────────────────────────────────────────
        solicitudes_count = _safe_len(getattr(c, "solicitudes", None))
        llamadas_count = _safe_len(getattr(c, "llamadas", None))

        if solicitudes_count == 0:
            solicitudes_count = _count_scalar(
                db.session.query(func.count(Solicitud.id)).filter(Solicitud.candidata_id == c.fila)
            )

        if llamadas_count == 0:
            llamadas_count = _count_scalar(
                db.session.query(func.count(LlamadaCandidata.id)).filter(LlamadaCandidata.candidata_id == c.fila)
            )

        # Reemplazos: siempre por DB (no tienes relación directa en Candidata)
        reemplazos_count = _count_scalar(
            db.session.query(func.count(Reemplazo.id)).filter(
                or_(
                    Reemplazo.candidata_old_id == c.fila,
                    Reemplazo.candidata_new_id == c.fila
                )
            )
        )

        tiene_historial = (solicitudes_count > 0) or (llamadas_count > 0) or (reemplazos_count > 0)
        puede_eliminar = not tiene_historial

        return {
            "tiene_cedula1": tiene_cedula1,
            "tiene_cedula2": tiene_cedula2,
            "tiene_perfil": tiene_perfil,
            "tiene_depuracion": tiene_dep,
            "documentos_completos": documentos_completos,
            "entrevista_realizada": entrevista_realizada,
            "solicitudes_count": int(solicitudes_count or 0),
            "llamadas_count": int(llamadas_count or 0),
            "reemplazos_count": int(reemplazos_count or 0),
            "tiene_historial": tiene_historial,
            "puede_eliminar": puede_eliminar,
        }

    # ─────────────────────────────────────────────────────────────
    # Leer búsqueda
    # ─────────────────────────────────────────────────────────────
    if request.method == 'POST' and request.form.get('confirmar_eliminacion'):
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

    # ─────────────────────────────────────────────────────────────
    # 1) CONFIRMAR ELIMINACIÓN (POST)
    # ─────────────────────────────────────────────────────────────
    if request.method == 'POST' and request.form.get('confirmar_eliminacion'):
        role = (
            str(getattr(current_user, "role", "") or "").strip().lower()
            or str(session.get("role", "") or "").strip().lower()
        )
        if role != "admin":
            mensaje = "❌ Solo admin puede confirmar la eliminación definitiva de candidatas."
            return render_template(
                'candidata_eliminar.html',
                busqueda=busqueda,
                resultados=resultados,
                candidata=None,
                mensaje=mensaje,
                docs_info=docs_info,
            )

        cid = (request.form.get('candidata_id') or '').strip()

        if not cid.isdigit():
            mensaje = "❌ ID de candidata inválido."
        else:
            obj = db.session.get(Candidata, int(cid))
            if not obj:
                mensaje = "⚠️ La candidata ya no existe en la base de datos."
            else:
                docs_info = build_docs_info(obj)

                if docs_info["tiene_historial"]:
                    mensaje = (
                        "⚠️ No se puede eliminar esta candidata porque tiene historial: "
                        f"{docs_info['solicitudes_count']} solicitudes, "
                        f"{docs_info['llamadas_count']} llamadas y "
                        f"{docs_info['reemplazos_count']} reemplazos. "
                        "Recomendación: marcarla como inactiva / no disponible, pero NO borrarla."
                    )
                    candidata = obj
                else:
                    try:
                        nombre_log = obj.nombre_completo
                        cedula_log = obj.cedula
                        codigo_log = obj.codigo

                        db.session.delete(obj)
                        db.session.commit()

                        current_app.logger.info(
                            "✅ Candidata eliminada manualmente: fila=%s, nombre=%s, cedula=%s, codigo=%s",
                            cid, nombre_log, cedula_log, codigo_log
                        )
                        flash("✅ Candidata eliminada correctamente.", "success")
                        return redirect(url_for('eliminar_candidata', busqueda=busqueda or ''))

                    except IntegrityError:
                        db.session.rollback()
                        current_app.logger.exception("❌ FK bloqueó la eliminación de la candidata.")
                        mensaje = (
                            "❌ La base de datos no permitió eliminarla porque está ligada a otros registros. "
                            "Para no dañar el historial, es mejor marcarla como no disponible."
                        )
                        candidata = obj
                        docs_info = build_docs_info(obj)

                    except Exception:
                        db.session.rollback()
                        current_app.logger.exception("❌ Error al eliminar candidata manualmente")
                        mensaje = "❌ Ocurrió un error al eliminar. Intenta de nuevo."
                        candidata = obj
                        docs_info = build_docs_info(obj)

    # ─────────────────────────────────────────────────────────────
    # 2) CARGAR DETALLE (GET ?candidata_id=)
    # ─────────────────────────────────────────────────────────────
    if not candidata:
        cid = (request.args.get('candidata_id') or '').strip()
        if cid.isdigit():
            candidata = db.session.get(Candidata, int(cid))
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."
                docs_info = build_docs_info(None)
            else:
                docs_info = build_docs_info(candidata)

    # ─────────────────────────────────────────────────────────────
    # 3) BÚSQUEDA (lista)
    # ─────────────────────────────────────────────────────────────
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
            current_app.logger.exception("❌ Error buscando candidatas para eliminar")
            mensaje = "❌ Ocurrió un error al buscar."

    return render_template(
        'candidata_eliminar.html',
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje,
        docs_info=docs_info,
    )

import click
from config_app import db
from models import EntrevistaPregunta

ENTREVISTAS_BANCO = {
  "domestica": {
    "titulo": "Entrevista para Doméstica",
    "descripcion": "Preguntas específicas para empleadas domésticas.",
    "preguntas": [
      { "id": "nombre", "enunciado": "Nombre completo", "tipo": "texto" },
      { "id": "nacionalidad", "enunciado": "Nacionalidad", "tipo": "texto" },
      { "id": "edad", "enunciado": "Edad", "tipo": "texto" },
      { "id": "direccion", "enunciado": "Dirección", "tipo": "texto_largo" },
      { "id": "estado_civil", "enunciado": "Estado civil", "tipo": "texto" },
      { "id": "tienes_hijos", "enunciado": "¿Tienes hijos?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "numero_hijos", "enunciado": "Número de hijos", "tipo": "texto" },
      { "id": "edades_hijos", "enunciado": "Edades de los hijos", "tipo": "texto" },
      { "id": "quien_cuida", "enunciado": "¿Quién cuida a sus hijos?", "tipo": "texto" },
      { "id": "descripcion_personal", "enunciado": "¿Cómo te describes como persona?", "tipo": "texto_largo" },
      { "id": "fuerte", "enunciado": "¿Cuál es tu fuerte?", "tipo": "texto" },
      { "id": "modalidad", "enunciado": "Modalidad de trabajo", "tipo": "texto" },
      { "id": "razon_trabajo", "enunciado": "¿Por qué eliges trabajar en una casa de familia?", "tipo": "texto_largo" },
      { "id": "labores_anteriores", "enunciado": "Labores desempeñadas en trabajos anteriores", "tipo": "texto_largo" },
      { "id": "tiempo_ultimo_trabajo", "enunciado": "Tiempo desde el último trabajo", "tipo": "texto" },
      { "id": "razon_salida", "enunciado": "¿Por qué saliste de tu último trabajo?", "tipo": "texto_largo" },
      { "id": "situacion_dificil", "enunciado": "¿Has enfrentado situaciones difíciles en el trabajo?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "manejo_situacion", "enunciado": "¿Cómo manejaste esa situación?", "tipo": "texto" },
      { "id": "manejo_reclamo", "enunciado": "¿Cómo manejarías reclamos o malos tratos del jefe?", "tipo": "texto_largo" },
      { "id": "uniforme", "enunciado": "¿Trabajas con uniforme?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "dias_feriados", "enunciado": "¿Trabajas días feriados?", "tipo": "radio", "opciones": ["Sí", "No", "Sí lo pagan"] },
      { "id": "revision_salida", "enunciado": "¿Puedes ser revisada a la salida?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "colaboracion", "enunciado": "¿Estás dispuesta a colaborar en lo que el jefe necesite?", "tipo": "texto" },
      { "id": "tipo_familia", "enunciado": "¿Con qué tipo de familia has trabajado anteriormente?", "tipo": "texto_largo" },
      { "id": "cuidado_ninos", "enunciado": "¿Has cuidado niños y de qué edad?", "tipo": "texto_largo" },
      { "id": "sabes_cocinar", "enunciado": "¿Sabes cocinar?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "gusta_cocinar", "enunciado": "¿Te gusta cocinar?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "que_cocinas", "enunciado": "¿Qué sabes cocinar?", "tipo": "texto_largo" },
      { "id": "postres", "enunciado": "¿Haces postres?", "tipo": "texto_largo" },
      { "id": "tareas_casa", "enunciado": "¿Qué tareas de la casa te gustan y cuáles no?", "tipo": "texto_largo" },
      { "id": "electrodomesticos", "enunciado": "¿Sabes usar electrodomésticos modernos?", "tipo": "texto" },
      { "id": "planchar", "enunciado": "¿Sabes planchar?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "actividad_principal", "enunciado": "¿Tienes alguna actividad principal (trabajo/estudio)?", "tipo": "texto" },
      { "id": "afiliacion_religiosa", "enunciado": "Afiliación religiosa", "tipo": "texto" },
      { "id": "cursos_domesticos", "enunciado": "¿Tienes cursos en el área doméstica?", "tipo": "texto" },
      { "id": "nivel_academico", "enunciado": "Nivel académico", "tipo": "texto" },
      { "id": "condiciones_salud", "enunciado": "¿Tienes condiciones de salud?", "tipo": "texto" },
      { "id": "alergico", "enunciado": "¿Eres alérgica a algo?", "tipo": "texto" },
      { "id": "medicamentos", "enunciado": "¿Tomas medicamentos?", "tipo": "texto" },
      { "id": "seguro_medico", "enunciado": "¿Tienes seguro médico?", "tipo": "texto" },
      { "id": "pruebas_medicas", "enunciado": "¿Aceptas hacer pruebas médicas si se solicita?", "tipo": "texto" },
      { "id": "vacunas_covid", "enunciado": "¿Cuántas vacunas del COVID tienes?", "tipo": "radio", "opciones": ["Dosis 1", "Dosis 2", "Dosis 3", "No tengo ninguna vacuna"] },
      { "id": "tomas_alcohol", "enunciado": "¿Tomas alcohol?", "tipo": "radio", "opciones": ["Sí", "No", "A veces"] },
      { "id": "fumas", "enunciado": "¿Fumas?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "tatuajes_piercings", "enunciado": "¿Tienes tatuajes visibles o piercings?", "tipo": "texto" }
    ]
  },

  "enfermera": {
    "titulo": "Entrevista para Enfermera Domiciliaria",
    "descripcion": "Preguntas específicas para profesionales de enfermería que brindan cuidado a domicilio en casas de familia.",
    "preguntas": [
      { "id": "nombre", "enunciado": "Nombre completo", "tipo": "texto" },
      { "id": "nacionalidad", "enunciado": "Nacionalidad", "tipo": "texto" },
      { "id": "edad", "enunciado": "Edad", "tipo": "texto" },
      { "id": "direccion", "enunciado": "Dirección", "tipo": "texto_largo" },
      { "id": "estado_civil", "enunciado": "Estado civil", "tipo": "texto" },
      { "id": "tienes_hijos", "enunciado": "¿Tienes hijos?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "numero_hijos", "enunciado": "Número de hijos", "tipo": "texto" },
      { "id": "edades_hijos", "enunciado": "Edades de los hijos", "tipo": "texto" },
      { "id": "experiencia", "enunciado": "Años de experiencia en enfermería", "tipo": "texto" },
      { "id": "licencia", "enunciado": "¿Posees licencia de enfermería?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "especialidad", "enunciado": "Especialidad o área de mayor experiencia", "tipo": "texto" },
      { "id": "tipo_cuidado", "enunciado": "¿Tienes experiencia en cuidados a domicilio?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "disponibilidad", "enunciado": "Disponibilidad de turno y horarios", "tipo": "radio", "opciones": ["Diurno", "Nocturno", "Ambos"] },
      { "id": "modalidad_trabajo", "enunciado": "¿Trabajarías con salida diaria o dormida?", "tipo": "radio", "opciones": ["Salida diaria", "Dormida", "Ambos"] },
      { "id": "manejo_emergencias", "enunciado": "¿Tienes experiencia en manejo de emergencias en el hogar?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "signos_vitales", "enunciado": "¿Sabes medir la presión arterial y tomar signos vitales?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "metodo_presion", "enunciado": "¿Con qué método mides la presión arterial?", "tipo": "radio", "opciones": ["Digital", "Manual", "No sé"] },
      { "id": "manejo_medicacion", "enunciado": "¿Consideras que tienes buen manejo en la administración de medicación?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "adaptacion_entorno", "enunciado": "¿Cómo te adaptas a trabajar en entornos familiares?", "tipo": "texto_largo" },
      { "id": "tiempo_ultimo_trabajo", "enunciado": "¿Cuánto tiempo ha pasado desde tu último trabajo?", "tipo": "texto" },
      { "id": "razon_salida", "enunciado": "¿Por qué saliste de tu último trabajo?", "tipo": "texto_largo" },
      { "id": "manejo_reclamo", "enunciado": "¿Cómo manejarías reclamos o malos tratos del jefe?", "tipo": "texto_largo" },
      { "id": "dias_feriados", "enunciado": "¿Trabajas días feriados?", "tipo": "radio", "opciones": ["Sí", "No", "Sí, se lo pagan"] },
      { "id": "revision_salida", "enunciado": "¿Puedes ser revisada a la salida?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "colaboracion", "enunciado": "¿Estás dispuesta a colaborar en lo que el jefe necesite?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "actividad_principal", "enunciado": "¿Tienes alguna actividad principal (trabajo/estudio)?", "tipo": "texto" },
      { "id": "afiliacion_religiosa", "enunciado": "Afiliación religiosa", "tipo": "texto" },
      { "id": "pruebas_medicas", "enunciado": "¿Aceptas hacer pruebas médicas si se solicita?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "tatuajes_piercings", "enunciado": "¿Tienes tatuajes visibles o piercings?", "tipo": "radio", "opciones": ["Sí", "No"] },
      { "id": "vacunas_covid", "enunciado": "¿Cuántas vacunas del COVID tienes?", "tipo": "radio", "opciones": ["Dosis 1", "Dosis 2", "Dosis 3"] },
      { "id": "idiomas", "enunciado": "Idiomas que hablas", "tipo": "texto" },
      { "id": "motivacion", "enunciado": "¿Por qué eliges trabajar en cuidados domiciliarios?", "tipo": "texto_largo" },
      { "id": "fortalezas", "enunciado": "¿Cuáles consideras que son tus fortalezas profesionales?", "tipo": "texto_largo" },
      { "id": "situacion_dificil", "enunciado": "Describe una situación difícil en el cuidado domiciliario y cómo la resolviste", "tipo": "texto_largo" },
      { "id": "tecnologia", "enunciado": "¿Tienes experiencia con tecnología médica o sistemas de monitoreo en el hogar?", "tipo": "radio", "opciones": ["Sí", "No"] }
    ]
  },

  "empleo_general": {
    "titulo": "Entrevista de Empleo General",
    "descripcion": "Preguntas generales para cualquier tipo de empleo.",
    "preguntas": [
      { "id": "nombre", "enunciado": "Nombre del candidato", "tipo": "texto" }
    ]
  }
}


import click
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError

@app.cli.command("seed-entrevista")
@with_appcontext
def seed_entrevista():
    """
    Crea/actualiza el banco de preguntas desde la config JSON.
    Uso:
      flask seed-entrevista
    """

    # ✅ Usa tu config real (la que ya cargas arriba)
    banco = current_app.config.get("ENTREVISTAS_CONFIG") or {}
    if not isinstance(banco, dict) or not banco:
        click.echo("❌ No hay configuración cargada en ENTREVISTAS_CONFIG.")
        return

    total_creadas = 0
    total_actualizadas = 0
    orden_global = 1

    try:
        for categoria, data in banco.items():
            data = data or {}
            preguntas = data.get("preguntas") or []
            if not isinstance(preguntas, list):
                continue

            for p in preguntas:
                p = p or {}
                pid = (p.get("id") or "").strip()
                if not pid:
                    # Si una pregunta viene mal definida, no rompemos el comando
                    continue

                clave = f"{categoria}.{pid}"  # ✅ clave única por categoría
                texto = (p.get("enunciado") or "").strip()
                tipo = (p.get("tipo") or "texto").strip()
                opciones = p.get("opciones")

                # ✅ Opciones: deben ser lista o None (para JSONB)
                if opciones is not None and not isinstance(opciones, (list, tuple)):
                    opciones = None

                q = EntrevistaPregunta.query.filter_by(clave=clave).first()

                if not q:
                    q = EntrevistaPregunta(
                        clave=clave,
                        texto=texto[:255],
                        tipo=tipo[:30],
                        opciones=list(opciones) if isinstance(opciones, (list, tuple)) else None,
                        orden=orden_global,
                        activa=True
                    )
                    db.session.add(q)
                    total_creadas += 1

                else:
                    cambio = False

                    if (q.texto or "") != texto[:255]:
                        q.texto = texto[:255]
                        cambio = True

                    if (q.tipo or "texto") != tipo[:30]:
                        q.tipo = tipo[:30]
                        cambio = True

                    nuevas_opciones = list(opciones) if isinstance(opciones, (list, tuple)) else None
                    if q.opciones != nuevas_opciones:
                        q.opciones = nuevas_opciones
                        cambio = True

                    if (q.orden or 0) != orden_global:
                        q.orden = orden_global
                        cambio = True

                    if q.activa is False:
                        q.activa = True
                        cambio = True

                    if cambio:
                        total_actualizadas += 1

                orden_global += 1

        db.session.commit()
        click.echo(f"OK ✅ Preguntas creadas: {total_creadas} | actualizadas: {total_actualizadas}")

    except SQLAlchemyError:
        db.session.rollback()
        click.echo("❌ Error de base de datos guardando preguntas.")
        raise

    except Exception:
        db.session.rollback()
        click.echo("❌ Error inesperado ejecutando seed-entrevista.")
        raise

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
