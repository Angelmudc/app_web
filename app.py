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
from sqlalchemy import or_, cast, String, func, and_
from sqlalchemy.orm import subqueryload, joinedload, load_only
from sqlalchemy.exc import OperationalError, IntegrityError, DBAPIError
from sqlalchemy.sql import text

# 🔐 HASH DE CONTRASEÑAS
from werkzeug.security import generate_password_hash, check_password_hash

# ✅ App factory / DB / CSRF / CACHE / usuarios en memoria
from config_app import create_app, db, csrf, cache, USUARIOS

# Decoradores
from decorators import roles_required, admin_required

# Modelos
from models import (
    Candidata,
    LlamadaCandidata,
    CandidataWeb,
    Solicitud,
    Reemplazo,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaRespuesta,
)

# Formularios
from forms import LlamadaCandidataForm

# Utils locales
from utils_codigo import generar_codigo_unico  # tu función optimizada

# Data / reportes
import pandas as pd


# PDF (fpdf2)
try:
    from fpdf import FPDF  # fpdf2
except Exception:
    FPDF = None


# -----------------------------------------------------------------------------
# APP BOOT
# -----------------------------------------------------------------------------


app = create_app()

@app.before_request
def force_session_expire():
    # 🔒 Siempre forzar sesión no permanente
    session.permanent = False

# -----------------------------------------------------------------------------
# 🔒 HARDENING BÁSICO (NO ROMPE LOCAL)
# -----------------------------------------------------------------------------
# Determinar si estamos en producción HTTPS.
# NOTA IMPORTANTE:
# - En local (http://127.0.0.1 / localhost) una cookie marcada como Secure NO se guarda,
#   lo que provoca que el login (admin/clientes) parezca “no entrar” porque la sesión no persiste.
# - En Render normalmente pondrás TRUST_XFF=1 (ProxyFix) y la app corre detrás de HTTPS.
# Por eso, aquí activamos cookies Secure solo cuando realmente estamos en entorno tipo producción.
IS_RENDER = (os.getenv("RENDER") or os.getenv("ON_RENDER") or os.getenv("RENDER_EXTERNAL_URL"))
TRUST_XFF_ON = os.getenv("TRUST_XFF", "0").strip().lower() in ("1", "true", "yes", "on")

IS_PROD = bool(
    IS_RENDER
    or (
        (os.getenv("FLASK_ENV", "").strip().lower() == "production")
        or (os.getenv("ENV", "").strip().lower() == "production")
        or (os.getenv("ENVIRONMENT", "").strip().lower() == "production")
    )
)

# Override explícito si alguna vez lo necesitas.
# - FORCE_SECURE_COOKIES=1 fuerza Secure
# - FORCE_INSECURE_COOKIES=1 lo desactiva
if os.getenv("FORCE_SECURE_COOKIES", "0").strip().lower() in ("1", "true", "yes", "on"):
    IS_PROD = True
if os.getenv("FORCE_INSECURE_COOKIES", "0").strip().lower() in ("1", "true", "yes", "on"):
    IS_PROD = False

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


 
# ---- Anti-bruteforce settings (ajustables)
LOGIN_MAX_INTENTOS = int(os.getenv("LOGIN_MAX_INTENTOS", "6"))   # intentos
LOGIN_LOCK_MINUTOS = int(os.getenv("LOGIN_LOCK_MINUTOS", "10"))  # minutos
LOGIN_KEY_PREFIX   = "panel_login"


def _client_ip() -> str:
    """Obtiene la IP del cliente.
    - En local: NO confía en X-Forwarded-For.
    - En producción detrás de proxy: solo confía si TRUST_XFF=1.
    """
    trust_xff = (os.getenv("TRUST_XFF", "0").strip() == "1")
    if trust_xff:
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
    keys = _login_keys(usuario_norm)
    return bool(cache.get(keys["lock"]))

def _lock(usuario_norm: str):
    keys = _login_keys(usuario_norm)
    cache.set(keys["lock"], True, timeout=LOGIN_LOCK_MINUTOS * 60)

def _fail_count(usuario_norm: str) -> int:
    keys = _login_keys(usuario_norm)
    return int(cache.get(keys["fail"]) or 0)

def _register_fail(usuario_norm: str) -> int:
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

        # ✅ Validación usando el USUARIOS importado desde config_app
        # (mantengo tu lógica de intentar raw/lower/upper)
        user_data = (
            USUARIOS.get(usuario_raw)
            or USUARIOS.get(usuario_raw.lower())
            or USUARIOS.get(usuario_raw.upper())
        )

        ok = False
        if user_data:
            stored = user_data.get("pwd_hash") or user_data.get("pwd")
            if stored:
                try:
                    ok = check_password_hash(stored, clave)
                except Exception:
                    ok = (stored == clave)

        if ok:
            # ✅ Login correcto: limpia intentos (los tuyos) + limpia lock global (IP+endpoint+usuario)
            _reset_fail(usuario_norm)

            # ✅ Limpia lock del security_layer con IP real (Render) si existe helper
            try:
                clear_fn = current_app.extensions.get("clear_login_attempts")
                if callable(clear_fn):
                    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                    ip = xff or (request.remote_addr or "").strip()
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
            session['usuario']   = usuario_raw
            session['role']      = (user_data.get("role") or "admin")
            session['logged_at'] = datetime.utcnow().isoformat(timespec='seconds')
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



@app.route('/logout')
@roles_required('admin', 'secretaria')
def logout():
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
    nombre       = (request.form.get('nombre_completo') or '').strip()[:150]
    edad_raw     = (request.form.get('edad') or '').strip()[:10]
    telefono     = (request.form.get('numero_telefono') or '').strip()[:30]
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
    cedula_raw   = (request.form.get('cedula') or '').strip()[:20]

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
            flash('📛 La edad debe estar entre 16 y 75 años.', 'warning')
            return render_template('registro_interno.html'), 400
    except ValueError:
        faltantes.append('Edad (número)')
        edad_num = None

    # Usa el normalizador existente en app.py
    cedula_norm = normalize_cedula(cedula_raw)
    if not cedula_norm:
        flash('📛 Cédula inválida. Debe contener 11 dígitos.', 'warning')
        return render_template('registro_interno.html'), 400

    if faltantes:
        flash('Por favor completa: ' + ', '.join(faltantes), 'warning')
        return render_template('registro_interno.html'), 400

    # Convertir/normalizar algunos valores
    areas_str     = ', '.join([s.strip() for s in areas_list if s.strip()]) if areas_list else ''
    sabe_planchar = (planchar_raw == 'si')
    acepta_pct    = (acepta_raw == '1')

    # --- Comprobación de duplicado por cédula (pre-check) ---
    try:
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()
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
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()

    if dup:
        flash('⚠️ Ya existe una candidata registrada con esta cédula.', 'warning')
        return render_template('registro_interno.html'), 400

    usuario = (session.get('usuario') or 'secretaria').strip()[:64]

    # --- Crear objeto y guardar ---
    nueva = Candidata(
        marca_temporal                  = datetime.utcnow(),
        nombre_completo                 = nombre,
        edad                            = str(edad_num),
        numero_telefono                 = telefono,
        direccion_completa              = direccion,
        modalidad_trabajo_preferida     = modalidad,
        rutas_cercanas                  = rutas,
        empleo_anterior                 = empleo_prev,
        anos_experiencia                = anos_exp,
        areas_experiencia               = areas_str,
        sabe_planchar                   = sabe_planchar,
        contactos_referencias_laborales = ref_lab,
        referencias_familiares_detalle  = ref_fam,
        acepta_porcentaje_sueldo        = acepta_pct,
        cedula                          = cedula_norm,
        medio_inscripcion               = 'Oficina',
        estado                          = 'en_proceso',
        fecha_cambio_estado             = datetime.utcnow(),
        usuario_cambio_estado           = usuario,
    )

    try:
        db.session.add(nueva)
        db.session.flush()
        db.session.commit()

    except OperationalError:
        # Reintenta una vez por fallo SSL/transitorio
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            _get_engine().dispose()
        except Exception:
            pass

        try:
            db.session.add(nueva)
            db.session.flush()
            db.session.commit()

        except IntegrityError:
            db.session.rollback()
            flash('⚠️ Ya existe una candidata registrada con esta cédula.', 'warning')
            return render_template('registro_interno.html'), 400

        except Exception:
            db.session.rollback()
            flash('❌ Problema momentáneo con la conexión. Intenta de nuevo en unos segundos.', 'danger')
            return render_template('registro_interno.html'), 503

    except IntegrityError:
        db.session.rollback()
        flash('⚠️ Ya existe una candidata registrada con esta cédula.', 'warning')
        return render_template('registro_interno.html'), 400

    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f'❌ No se pudo guardar el registro: {e.__class__.__name__}', 'danger')
        return render_template('registro_interno.html'), 500

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

    try:
        base = Candidata.query.order_by(Candidata.nombre_completo.asc())
        if q:
            base = apply_search_to_candidata_query(base, q).limit(300)
            candidatas = safe_all(base)
        else:
            candidatas = safe_all(base)
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
# ENTREVISTAS (DB) - HELPERS
# -----------------------------------------------------------------------------

def _get_preguntas_db_por_tipo(tipo: str):
    """Devuelve preguntas activas para un tipo (domestica/enfermera/empleo_general)."""
    tipo = (tipo or "").strip().lower()
    if not tipo:
        return []

    # Clave: "domestica.xxx" | "enfermera.xxx" | "empleo_general.xxx"
    return (
        EntrevistaPregunta.query
        .filter(EntrevistaPregunta.activa.is_(True))
        .filter(EntrevistaPregunta.clave.like(f"{tipo}.%"))
        .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
        .all()
    )


def _safe_setattr(obj, name: str, value):
    """Setea un atributo solo si existe en el modelo (para no romper si no está)."""
    if hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            return False
    return False

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
                apply_search_to_candidata_query(Candidata.query, q)
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

    preguntas = _get_preguntas_db_por_tipo(tipo)
    if not preguntas:
        flash("⚠️ No hay preguntas configuradas para ese tipo de entrevista.", "warning")
        return redirect(url_for('entrevistas_de_candidata', fila=fila))

    if request.method == "POST":
        try:
            entrevista = Entrevista(candidata_id=fila)
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'creada_en', datetime.utcnow())
            _safe_setattr(entrevista, 'actualizada_en', None)
            _safe_setattr(entrevista, 'tipo', (tipo or '').strip().lower())

            db.session.add(entrevista)
            db.session.flush()  # para obtener entrevista.id

            for p in preguntas:
                field = f"q_{p.id}"
                valor = (request.form.get(field) or "").strip()

                r = EntrevistaRespuesta(
                    entrevista_id=entrevista.id,
                    pregunta_id=p.id,
                    respuesta=valor if valor else None,
                )
                _safe_setattr(r, 'creada_en', datetime.utcnow())
                db.session.add(r)

            db.session.commit()
            flash("✅ Entrevista guardada.", "success")
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando entrevista (DB)")
            flash("❌ Error al guardar la entrevista.", "danger")
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
        try:
            for p in preguntas:
                field = f"q_{p.id}"
                valor = (request.form.get(field) or "").strip()

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
                    _safe_setattr(r, 'creada_en', datetime.utcnow())
                    db.session.add(r)

                r.respuesta = valor if valor else None
                _safe_setattr(r, 'actualizada_en', datetime.utcnow())

            _safe_setattr(entrevista, 'actualizada_en', datetime.utcnow())
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'tipo', tipo)

            db.session.commit()
            flash("✅ Entrevista actualizada.", "success")
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error actualizando entrevista (DB)")
            flash("❌ Error al actualizar la entrevista.", "danger")
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

    def _humanize_clave(clave: str) -> str:
        """Convierte `domestica.tienes_hijos` -> `Tienes hijos` (fallback)."""
        clave = (clave or '').strip()
        if not clave:
            return ''
        if '.' in clave:
            _, tail = clave.split('.', 1)
        else:
            tail = clave
        tail = tail.replace('_', ' ').strip()
        tail = re.sub(r'\s+', ' ', tail)
        tail = tail[:1].upper() + tail[1:] if tail else tail
        return tail

    _LABELS = {
        'tienes_hijos': '¿Tiene hijos?',
        'numero_hijos': '¿Cuántos hijos tiene?',
        'edades_hijos': 'Edades de los hijos',
        'quien_cuida': '¿Con quién deja a los niños?',
        'descripcion_personal': 'Descripción personal',
        'fuerte': 'Fortalezas',
        'razon_trabajo': 'Motivo para trabajar',
        'labores_anteriores': 'Experiencia / trabajos anteriores',
        'tiempo_ultimo_trabajo': 'Tiempo en el último trabajo',
        'razon_salida': 'Motivo de salida del último trabajo',
        'situacion_dificil': '¿Ha tenido situaciones difíciles?',
        'manejo_situacion': '¿Cómo manejó la situación?',
        'manejo_reclamo': '¿Cómo maneja un reclamo?',
        'uniforme': 'Uso de uniforme',
        'dias_feriados': 'Disponibilidad en días feriados',
        'revision_salida': 'Revisión al salir',
        'colaboracion': 'Trabajo en equipo / colaboración',
        'tipo_familia': 'Tipo de familia',
        'cuidado_ninos': 'Cuidado de niños',
        'sabes_cocinar': '¿Sabe cocinar?',
        'gusta_cocinar': '¿Le gusta cocinar?',
        'que_cocinas': '¿Qué cocina?',
        'postres': 'Postres',
        'tareas_casa': 'Tareas del hogar',
        'electrodomesticos': 'Manejo de electrodomésticos',
        'planchar': '¿Sabe planchar?',
        'actividad_principal': 'Actividad principal',
        'nivel_academico': 'Nivel académico',
        'condiciones_salud': 'Condiciones de salud',
        'alergico': 'Alergias',
        'medicamentos': 'Medicamentos',
        'seguro_medico': 'Seguro médico',
        'pruebas_medicas': 'Pruebas médicas',
        'vacunas_covid': 'Vacunas COVID',
        'tomas_alcohol': 'Consumo de alcohol',
        'fumas': '¿Fuma?',
        'tatuajes_piercings': 'Tatuajes / piercings',
    }

    def _pretty_question(pregunta) -> str:
        """Prioriza enunciado/etiqueta, y si no hay, humaniza la clave."""
        for attr in ('enunciado','pregunta','texto_pregunta','texto','label','etiqueta','titulo','nombre','descripcion'):
            v = (getattr(pregunta, attr, None) or '').strip()
            if v:
                return v

        clave = (getattr(pregunta, 'clave', None) or '').strip()
        tail = clave.split('.', 1)[1] if (clave and '.' in clave) else clave
        tail_key = (tail or '').strip().lower()
        if tail_key in _LABELS:
            return _LABELS[tail_key]

        return _humanize_clave(clave) or 'Pregunta'

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
                obj.contactos_referencias_laborales  = (request.form.get('contactos_referencias_laborales') or '').strip()[:250] or obj.contactos_referencias_laborales
                obj.referencias_familiares_detalle   = (request.form.get('referencias_familiares_detalle') or '').strip()[:250] or obj.referencias_familiares_detalle
                obj.cedula                           = (request.form.get('cedula') or '').strip()[:20] or obj.cedula
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
        try:
            base = Candidata.query.order_by(Candidata.nombre_completo.asc())
            qtxt = busqueda

            # Aplica reglas:
            # - código (CAN-000000) => estricto
            # - si no es código => nombre/cédula/teléfono flexible (sin código)
            resultados = (
                apply_search_to_candidata_query(base, qtxt)
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
                try:
                    resultados = (
                        apply_search_to_candidata_query(Candidata.query, q)
                        .order_by(Candidata.nombre_completo.asc())
                        .limit(300)
                        .all()
                    )
                    if not resultados:
                        flash("⚠️ No se encontraron coincidencias.", "error")
                except Exception:
                    app.logger.exception("❌ Error buscando en inscripción")
                    flash("❌ Error al buscar.", "error")

    else:
        q = (request.args.get("buscar") or "").strip()[:128]
        if q:
            try:
                resultados = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
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
            try:
                resultados = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
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