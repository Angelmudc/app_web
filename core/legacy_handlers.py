# -*- coding: utf-8 -*-
from typing import Optional
from urllib.parse import urlparse
import io
import os
import re
import json
import hashlib
import logging
import unicodedata
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from time import perf_counter
import time

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
from sqlalchemy import or_, cast, String, func, and_, Date, inspect
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
    StaffNotificacion,
    StaffNotificacionLectura,
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
from utils.staff_mfa import (
    MFA_SETUP_SECRET_SESSION_KEY,
    generate_mfa_secret,
    mfa_enforced_for_staff,
    session_begin_mfa_pending,
    staff_role_requires_mfa,
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
from utils.staff_notifications import create_staff_notification
from core.services.cache_keys import _cache_key_with_role as _svc_cache_key_with_role
from core.services.candidatas_shared import get_candidata_by_id as _svc_get_candidata_by_id
from core.services.db_retry import _retry_query as _svc_retry_query
from core.services.search import (
    _prioritize_candidata_result as _svc_prioritize_candidata_result,
    apply_search_to_candidata_query as _svc_apply_search_to_candidata_query,
    build_flexible_search_filters as _svc_build_flexible_search_filters,
    normalize_query_text as _svc_normalize_query_text,
    search_candidatas_limited as _svc_search_candidatas_limited,
)

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
    return _svc_normalize_query_text(raw)


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
    return _svc_build_flexible_search_filters(q)


def apply_search_to_candidata_query(base_query, q: str):
    return _svc_apply_search_to_candidata_query(base_query, q)


def search_candidatas_limited(
    q: str,
    *,
    limit: int = 300,
    base_query=None,
    minimal_fields: bool = False,
    order_mode: str = "nombre_asc",
    log_label: str = "default",
):
    return _svc_search_candidatas_limited(
        q,
        limit=limit,
        base_query=base_query,
        minimal_fields=minimal_fields,
        order_mode=order_mode,
        log_label=log_label,
    )


def _prioritize_candidata_result(
    rows: list,
    prioritized_fila: Optional[int],
) -> list:
    return _svc_prioritize_candidata_result(rows, prioritized_fila)


def _legacy_buscar_trace(event: str, **payload) -> None:
    """Traza diagnóstica ligera para flujo legacy buscar/editar."""
    enabled = str(os.getenv("LEGACY_BUSCAR_TRACE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return
    try:
        current_app.logger.info("legacy.buscar.%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        current_app.logger.info("legacy.buscar.%s %s", event, payload)


def _trace_preview(value, max_len: int = 180):
    if value is None:
        return None
    txt = str(value)
    if len(txt) <= max_len:
        return txt
    return f"{txt[:max_len]}…[{len(txt)} chars]"


def _trace_request_form_snapshot() -> dict:
    snap = {}
    try:
        for key in request.form.keys():
            vals = request.form.getlist(key)
            snap[key] = [_trace_preview(v, 160) for v in vals]
    except Exception as exc:
        snap["__error__"] = str(exc)
    return snap


def _legacy_db_trace_enabled() -> bool:
    return str(os.getenv("LEGACY_DB_TRACE", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _db_runtime_snapshot() -> dict:
    """Snapshot de runtime para verificar fuente de datos/engine/sesión."""
    snapshot = {
        "app_env": (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower() or None,
        "config_uri": (current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""),
        "cache_type": (current_app.config.get("CACHE_TYPE") or ""),
    }
    try:
        engine = db.engine
        engine_url = str(getattr(engine, "url", "") or "")
        snapshot["engine_url"] = engine_url
        snapshot["engine_id"] = id(engine)
        snapshot["pool_id"] = id(getattr(engine, "pool", None))
        snapshot["dialect"] = str(getattr(engine, "dialect", None).name if getattr(engine, "dialect", None) else "")
        snapshot["is_sqlite"] = bool(engine_url.startswith("sqlite:"))
        if snapshot["is_sqlite"]:
            snapshot["sqlite_path"] = engine_url.replace("sqlite:///", "", 1)
    except Exception as exc:
        snapshot["engine_error"] = str(exc)
    try:
        sess = db.session()
        snapshot["session_obj_id"] = id(sess)
        bind = None
        try:
            bind = sess.get_bind()
        except Exception:
            bind = None
        snapshot["session_bind_id"] = id(bind) if bind is not None else None
        snapshot["session_bind_url"] = str(getattr(bind, "url", "") or "") if bind is not None else None
    except Exception as exc:
        snapshot["session_error"] = str(exc)
    return snapshot


def _query_candidata_snapshot_session(fila_id: int) -> dict:
    try:
        cand = db.session.get(Candidata, int(fila_id))
        if not cand:
            return {"exists": False}
        return {
            "exists": True,
            "fila": int(getattr(cand, "fila", 0) or 0),
            "nombre_completo": getattr(cand, "nombre_completo", None),
            "numero_telefono": getattr(cand, "numero_telefono", None),
            "empleo_anterior": getattr(cand, "empleo_anterior", None),
            "referencias_laboral": getattr(cand, "referencias_laboral", None),
            "referencias_familiares": getattr(cand, "referencias_familiares", None),
        }
    except Exception as exc:
        return {"exists": False, "error": str(exc)}


def _query_candidata_snapshot_fresh_connection(fila_id: int) -> dict:
    stmt = text(
        """
        SELECT fila, nombre_completo, numero_telefono, empleo_anterior, referencias_laboral, referencias_familiares
        FROM candidatas
        WHERE fila = :f
        """
    )
    try:
        with db.engine.connect() as conn:
            row = conn.execute(stmt, {"f": int(fila_id)}).mappings().first()
        if not row:
            return {"exists": False}
        return {
            "exists": True,
            "fila": int(row.get("fila") or 0),
            "nombre_completo": row.get("nombre_completo"),
            "numero_telefono": row.get("numero_telefono"),
            "empleo_anterior": row.get("empleo_anterior"),
            "referencias_laboral": row.get("referencias_laboral"),
            "referencias_familiares": row.get("referencias_familiares"),
        }
    except Exception as exc:
        return {"exists": False, "error": str(exc)}


def _legacy_buscar_db_trace(event: str, **payload) -> None:
    """Traza de DB/engine/sesión para diagnosticar doble fuente de datos."""
    if not _legacy_db_trace_enabled():
        return
    data = dict(payload or {})
    data["db_runtime"] = _db_runtime_snapshot()
    _legacy_buscar_trace(event, **data)


def get_candidata_by_id(raw_id):
    """Compat: delega al helper neutral compartido."""
    return _svc_get_candidata_by_id(raw_id)


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
    """Compat: delega al helper neutral compartido."""
    return _svc_cache_key_with_role(prefix)


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
    from core.handlers import auth_home_handlers as auth_home_h
    return auth_home_h.robots_txt()

# -----------------------------------------------------------------------------
# AUTH (panel interno por sesión simple)
#  Nota de seguridad:
#  - Autenticación staff basada en tabla staff_users.
#  - Endurecí el login: limpio inputs, corto longitud, y roto sesión al autenticar.
#  - Si usas CSRF con Flask-WTF, asegúrate de incluir {{ csrf_token() }} en login.html.
# -----------------------------------------------------------------------------

@app.route('/home')
def home():
    from core.handlers import auth_home_handlers as auth_home_h
    return auth_home_h.home()


def _staff_reader_key() -> Optional[str]:
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h._staff_reader_key()


def _notif_review_url(notif: StaffNotificacion) -> str:
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h._notif_review_url(notif)


def _staff_notifications_unread_count(reader_key: str) -> int:
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h._staff_notifications_unread_count(reader_key)


def _staff_notification_to_item(notif: StaffNotificacion, is_read: bool) -> dict:
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h._staff_notification_to_item(notif, is_read)


def _staff_notifications_ready() -> bool:
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h._staff_notifications_ready()


@app.route('/home/notificaciones-publicas/count.json', methods=['GET'])
@roles_required('admin', 'secretaria')
def home_public_notifications_count():
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h.home_public_notifications_count()


@app.route('/home/notificaciones-publicas/list.json', methods=['GET'])
@roles_required('admin', 'secretaria')
def home_public_notifications_list():
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h.home_public_notifications_list()


@app.route('/home/notificaciones-publicas/<int:notificacion_id>/leer', methods=['POST'])
@roles_required('admin', 'secretaria')
def home_public_notifications_mark_read(notificacion_id: int):
    from core.handlers import home_notifications_handlers as notif_h
    return notif_h.home_public_notifications_mark_read(notificacion_id)


 
# ---- Anti-bruteforce settings (ajustables)
LOGIN_MAX_INTENTOS = int(os.getenv("LOGIN_MAX_INTENTOS", "10"))   # intentos
LOGIN_LOCK_MINUTOS = int(os.getenv("LOGIN_LOCK_MINUTOS", "10"))  # minutos
LOGIN_KEY_PREFIX   = "panel_login"


def _operational_rate_limits_enabled() -> bool:
    raw = os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS")
    if raw is not None and str(raw).strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    return run_env in ("prod", "production")


def _login_debug_enabled() -> bool:
    raw = (os.getenv("LOGIN_DEBUG", "0") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _normalize_staff_role_loose(role_raw) -> str:
    role = (str(role_raw or "").strip().lower())
    if role in ("owner", "admin", "secretaria"):
        return role
    if role in ("secretary", "secre", "secretaría"):
        return "secretaria"
    return ""


def _staff_mfa_required_for_user(staff_user) -> bool:
    if not isinstance(staff_user, StaffUser):
        return False
    if not bool(getattr(staff_user, "is_active", False)):
        return False
    if not mfa_enforced_for_staff(testing=bool(current_app.config.get("TESTING"))):
        return False
    role = _normalize_staff_role_loose(getattr(staff_user, "role", "") or "")
    return staff_role_requires_mfa(role)


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


def _login_sess_key(usuario_norm: str) -> str:
    ip = _client_ip()
    user = (usuario_norm or "").strip().lower()[:64]
    return f"legacy_login_fail:{ip}:{user}"


def _session_is_locked(usuario_norm: str) -> bool:
    data = session.get(_login_sess_key(usuario_norm)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return False
    try:
        return time.time() < float(locked_until)
    except Exception:
        return False


def _session_fail_count(usuario_norm: str) -> int:
    data = session.get(_login_sess_key(usuario_norm)) or {}
    try:
        return int(data.get("tries") or 0)
    except Exception:
        return 0


def _session_lock(usuario_norm: str):
    key = _login_sess_key(usuario_norm)
    data = session.get(key) or {}
    data["locked_until"] = time.time() + (LOGIN_LOCK_MINUTOS * 60)
    session[key] = data


def _session_register_fail(usuario_norm: str) -> int:
    key = _login_sess_key(usuario_norm)
    data = session.get(key) or {}
    tries = int(data.get("tries") or 0) + 1
    data["tries"] = tries
    if tries >= LOGIN_MAX_INTENTOS:
        data["locked_until"] = time.time() + (LOGIN_LOCK_MINUTOS * 60)
    session[key] = data
    return tries


def _session_reset_fail(usuario_norm: str):
    try:
        session.pop(_login_sess_key(usuario_norm), None)
    except Exception:
        pass


def _is_locked(usuario_norm: str) -> bool:
    if not _operational_rate_limits_enabled():
        return False
    keys = _login_keys(usuario_norm)
    try:
        return bool(cache.get(keys["lock"]))
    except Exception:
        return _session_is_locked(usuario_norm)

def _lock(usuario_norm: str):
    keys = _login_keys(usuario_norm)
    try:
        cache.set(keys["lock"], True, timeout=LOGIN_LOCK_MINUTOS * 60)
    except Exception:
        _session_lock(usuario_norm)

def _fail_count(usuario_norm: str) -> int:
    keys = _login_keys(usuario_norm)
    try:
        return int(cache.get(keys["fail"]) or 0)
    except Exception:
        return _session_fail_count(usuario_norm)

def _register_fail(usuario_norm: str) -> int:
    if not _operational_rate_limits_enabled():
        return 0
    keys = _login_keys(usuario_norm)
    n = _fail_count(usuario_norm) + 1
    try:
        cache.set(keys["fail"], n, timeout=LOGIN_LOCK_MINUTOS * 60)
    except Exception:
        return _session_register_fail(usuario_norm)
    if n >= LOGIN_MAX_INTENTOS:
        _lock(usuario_norm)
    return n

def _reset_fail(usuario_norm: str):
    keys = _login_keys(usuario_norm)
    try:
        cache.delete(keys["fail"])
        cache.delete(keys["lock"])
    except Exception:
        pass
    _session_reset_fail(usuario_norm)



@app.route('/login', methods=['GET', 'POST'])
def login():
    from core.handlers import auth_home_handlers as auth_home_h
    return auth_home_h.login()



@app.route('/logout', methods=['POST'])
@roles_required('admin', 'secretaria')
def logout():
    from core.handlers import auth_home_handlers as auth_home_h
    return auth_home_h.logout()


# -----------------------------------------------------------------------------
# REGISTRO INTERNO (privado) - Secretarias/Admin
#  - Usa los mismos campos del registro público
#  - Renderiza template directo en /templates (NO dentro de /templates/registro/)
#  - NO tiene página de gracias (solo flash + recarga)
# -----------------------------------------------------------------------------

@app.route('/registro_interno/', methods=['GET', 'POST'], strict_slashes=False)
@roles_required('admin', 'secretaria')
def registro_interno():
    from core.handlers import registro_interno_handlers as registro_h
    return registro_h.registro_interno()


# -----------------------------------------------------------------------------
# CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas', methods=['GET'])
@roles_required('admin', 'secretaria')
def list_candidatas():
    from core.handlers import candidatas_list_handlers as list_h
    return list_h.list_candidatas()


@app.route('/candidatas_db')
@roles_required('admin', 'secretaria')
@cache.cached(
    timeout=int(os.getenv("CACHE_CANDIDATAS_DB_SECONDS", "60")),
    key_prefix=lambda: _cache_key_with_role("candidatas_db"),
)
def list_candidatas_db():
    from core.handlers import candidatas_list_handlers as list_h
    return list_h.list_candidatas_db()

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
) -> bool:
    cand = _get_candidata_by_fila_or_pk(candidata_id)
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

def generar_pdf_entrevista_db(entrevista_id: int):
    from core.handlers import entrevistas_pdf_handlers as pdf_h
    return pdf_h.generar_pdf_entrevista_db(entrevista_id)

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
    edit_form_overrides = {}
    _legacy_buscar_db_trace(
        "request_start",
        method=request.method,
        path=request.path,
        candidata_id_form=(request.form.get("candidata_id") or "").strip() if request.method == "POST" else None,
        candidata_id_query=(request.args.get("candidata_id") or "").strip() if request.method == "GET" else None,
        busqueda=(request.values.get("busqueda") or "").strip()[:128] or None,
    )
    if request.method == "POST":
        _legacy_buscar_trace(
            "post_form_snapshot",
            keys=sorted(list(request.form.keys())),
            form=_trace_request_form_snapshot(),
            guardar_edicion_present=("guardar_edicion" in request.form),
            guardar_edicion_value=_trace_preview(request.form.get("guardar_edicion")),
        )

    # Guardar edición
    if request.method == 'POST' and request.form.get('guardar_edicion'):
        cid = (request.form.get('candidata_id') or '').strip()
        _legacy_buscar_trace(
            "post_received",
            candidata_id=cid,
            busqueda=busqueda,
            path=request.path,
        )
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
                _legacy_buscar_trace(
                    "post_target_loaded",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    before_nombre=getattr(obj, "nombre_completo", None),
                    before_telefono=getattr(obj, "numero_telefono", None),
                    before_empleo=getattr(obj, "empleo_anterior", None),
                )

                def _trace_field_apply(field_name: str, form_key: str, before_value, after_value):
                    _legacy_buscar_trace(
                        "post_field_apply",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        field=field_name,
                        form_key=form_key,
                        in_form=(form_key in request.form),
                        before=_trace_preview(before_value),
                        received=_trace_preview(request.form.get(form_key)),
                        after=_trace_preview(after_value),
                    )

                # Limites razonables por campo para evitar payloads enormes
                before_value = obj.nombre_completo
                obj.nombre_completo                  = (request.form.get('nombre') or '').strip()[:150] or obj.nombre_completo
                _trace_field_apply("nombre_completo", "nombre", before_value, obj.nombre_completo)
                before_value = obj.edad
                obj.edad                             = (request.form.get('edad') or '').strip()[:10] or obj.edad
                _trace_field_apply("edad", "edad", before_value, obj.edad)
                before_value = obj.numero_telefono
                obj.numero_telefono                  = (request.form.get('telefono') or '').strip()[:30] or obj.numero_telefono
                _trace_field_apply("numero_telefono", "telefono", before_value, obj.numero_telefono)
                before_value = obj.direccion_completa
                obj.direccion_completa               = (request.form.get('direccion') or '').strip()[:250] or obj.direccion_completa
                _trace_field_apply("direccion_completa", "direccion", before_value, obj.direccion_completa)
                before_value = obj.modalidad_trabajo_preferida
                obj.modalidad_trabajo_preferida      = (request.form.get('modalidad') or '').strip()[:100] or obj.modalidad_trabajo_preferida
                _trace_field_apply("modalidad_trabajo_preferida", "modalidad", before_value, obj.modalidad_trabajo_preferida)
                before_value = obj.rutas_cercanas
                obj.rutas_cercanas                   = (request.form.get('rutas') or '').strip()[:150] or obj.rutas_cercanas
                _trace_field_apply("rutas_cercanas", "rutas", before_value, obj.rutas_cercanas)
                before_value = obj.empleo_anterior
                obj.empleo_anterior                  = (request.form.get('empleo_anterior') or '').strip()[:150] or obj.empleo_anterior
                _trace_field_apply("empleo_anterior", "empleo_anterior", before_value, obj.empleo_anterior)
                before_value = obj.anos_experiencia
                obj.anos_experiencia                 = (request.form.get('anos_experiencia') or '').strip()[:50] or obj.anos_experiencia
                _trace_field_apply("anos_experiencia", "anos_experiencia", before_value, obj.anos_experiencia)
                before_value = obj.areas_experiencia
                obj.areas_experiencia                = (request.form.get('areas_experiencia') or '').strip()[:200] or obj.areas_experiencia
                _trace_field_apply("areas_experiencia", "areas_experiencia", before_value, obj.areas_experiencia)
                before_value = obj.contactos_referencias_laborales
                obj.contactos_referencias_laborales  = (request.form.get('contactos_referencias_laborales') or '').strip()[:250] or obj.contactos_referencias_laborales
                _trace_field_apply("contactos_referencias_laborales", "contactos_referencias_laborales", before_value, obj.contactos_referencias_laborales)
                before_value = obj.referencias_familiares_detalle
                obj.referencias_familiares_detalle   = (request.form.get('referencias_familiares_detalle') or '').strip()[:250] or obj.referencias_familiares_detalle
                _trace_field_apply("referencias_familiares_detalle", "referencias_familiares_detalle", before_value, obj.referencias_familiares_detalle)
                # Mantiene sincronizadas columnas legacy/canónicas de referencias.
                obj.referencias_laboral              = obj.contactos_referencias_laborales
                obj.referencias_familiares           = obj.referencias_familiares_detalle
                _trace_field_apply("referencias_laboral", "contactos_referencias_laborales", before_snapshot.get("referencias_laboral"), obj.referencias_laboral)
                _trace_field_apply("referencias_familiares", "referencias_familiares_detalle", before_snapshot.get("referencias_familiares"), obj.referencias_familiares)

                # Campos opcionales nuevos (registro público + compatibilidad legacy)
                if 'disponibilidad_inicio' in request.form:
                    before_value = obj.disponibilidad_inicio
                    obj.disponibilidad_inicio = (request.form.get('disponibilidad_inicio') or '').strip()[:80] or None
                    _trace_field_apply("disponibilidad_inicio", "disponibilidad_inicio", before_value, obj.disponibilidad_inicio)
                if 'sueldo_esperado' in request.form:
                    before_value = obj.sueldo_esperado
                    obj.sueldo_esperado = (request.form.get('sueldo_esperado') or '').strip()[:80] or None
                    _trace_field_apply("sueldo_esperado", "sueldo_esperado", before_value, obj.sueldo_esperado)
                if 'motivacion_trabajo' in request.form:
                    before_value = obj.motivacion_trabajo
                    obj.motivacion_trabajo = (request.form.get('motivacion_trabajo') or '').strip()[:350] or None
                    _trace_field_apply("motivacion_trabajo", "motivacion_trabajo", before_value, obj.motivacion_trabajo)

                def _parse_optional_bool(raw: str):
                    val = (raw or '').strip().lower().replace('í', 'i')
                    if val in ('si', '1', 'true', 'on'):
                        return True
                    if val in ('no', '0', 'false', 'off'):
                        return False
                    return None

                if 'trabaja_con_ninos' in request.form:
                    before_value = obj.trabaja_con_ninos
                    obj.trabaja_con_ninos = _parse_optional_bool(request.form.get('trabaja_con_ninos'))
                    _trace_field_apply("trabaja_con_ninos", "trabaja_con_ninos", before_value, obj.trabaja_con_ninos)
                if 'trabaja_con_mascotas' in request.form:
                    before_value = obj.trabaja_con_mascotas
                    obj.trabaja_con_mascotas = _parse_optional_bool(request.form.get('trabaja_con_mascotas'))
                    _trace_field_apply("trabaja_con_mascotas", "trabaja_con_mascotas", before_value, obj.trabaja_con_mascotas)
                if 'puede_dormir_fuera' in request.form:
                    before_value = obj.puede_dormir_fuera
                    obj.puede_dormir_fuera = _parse_optional_bool(request.form.get('puede_dormir_fuera'))
                    _trace_field_apply("puede_dormir_fuera", "puede_dormir_fuera", before_value, obj.puede_dormir_fuera)

                cedula_edit_raw = (request.form.get('cedula') or '').strip()[:50]
                cedula_valid_for_update = False
                if cedula_edit_raw:
                    cedula_edit_digits = normalize_cedula_for_compare(cedula_edit_raw)
                    if not cedula_edit_digits:
                        _legacy_buscar_trace(
                            "post_validation_warning",
                            reason="cedula_invalid",
                            candidata_id_form=cid,
                            received_cedula=_trace_preview(cedula_edit_raw),
                        )
                        mensaje = "⚠️ Cédula inválida: se guardaron los demás campos, pero la cédula no se actualizó."
                        edit_form_overrides["cedula"] = cedula_edit_raw

                    dup = None
                    if not mensaje:
                        dup, _ = find_duplicate_candidata_by_cedula(
                            cedula_edit_raw,
                            exclude_fila=getattr(obj, 'fila', None)
                        )
                    if dup:
                        _legacy_buscar_trace(
                            "post_validation_warning",
                            reason="cedula_duplicate",
                            candidata_id_form=cid,
                            received_cedula=_trace_preview(cedula_edit_raw),
                            duplicate_fila=getattr(dup, "fila", None),
                        )
                        mensaje = "⚠️ Cédula duplicada: se guardaron los demás campos, pero la cédula no se actualizó."
                        edit_form_overrides["cedula"] = cedula_edit_raw

                    # En edición no se reescribe formato automáticamente.
                    if not mensaje:
                        before_value = obj.cedula
                        obj.cedula = cedula_edit_raw
                        cedula_valid_for_update = True
                        _trace_field_apply("cedula", "cedula", before_value, obj.cedula)
                # ⚠️ IMPORTANTE:
                # Los campos booleanos NO deben resetearse a False si el formulario no los trae.
                # (Checkboxes no marcados a veces NO se envían; eso estaba borrando valores.)

                # Sabe planchar: solo actualizar si el form trae el campo
                if 'sabe_planchar' in request.form:
                    v_planchar = (request.form.get('sabe_planchar') or '').strip().lower()
                    # Acepta varios formatos: 'si', 'sí', 'true', '1', 'on'
                    before_value = obj.sabe_planchar
                    obj.sabe_planchar = v_planchar in ('si', 'sí', 'true', '1', 'on')
                    _trace_field_apply("sabe_planchar", "sabe_planchar", before_value, obj.sabe_planchar)

                # Acepta porcentaje: solo actualizar si el form trae el campo
                if 'acepta_porcentaje' in request.form:
                    v_pct = (request.form.get('acepta_porcentaje') or '').strip().lower()
                    before_value = obj.acepta_porcentaje_sueldo
                    obj.acepta_porcentaje_sueldo = v_pct in ('si', 'sí', 'true', '1', 'on')
                    _trace_field_apply("acepta_porcentaje_sueldo", "acepta_porcentaje", before_value, obj.acepta_porcentaje_sueldo)

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
                    "referencias_laboral": (obj.referencias_laboral or "").strip(),
                    "referencias_familiares": (obj.referencias_familiares or "").strip(),
                }
                if cedula_valid_for_update:
                    expected_verify["cedula"] = (obj.cedula or "").strip()
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

                _legacy_buscar_trace(
                    "post_before_persist",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    expected_verify_keys=sorted(list(expected_verify.keys())),
                )
                result = execute_robust_save(
                    session=db.session,
                    persist_fn=lambda _attempt: None,
                    verify_fn=lambda: _verify_candidata_fields_saved(int(obj.fila), expected_verify),
                )
                _legacy_buscar_trace(
                    "post_after_persist",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    ok=bool(result.ok),
                    attempts=int(result.attempts),
                    error=(result.error_message or ""),
                )

                if result.ok:
                    after_snapshot = snapshot_model_fields(obj, audit_fields)
                    changes = diff_snapshots(before_snapshot, after_snapshot)
                    session["last_edited_candidata_fila"] = int(obj.fila)
                    _legacy_buscar_db_trace(
                        "post_persist_consistency",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        value_same_session=_query_candidata_snapshot_session(int(obj.fila)),
                        value_fresh_connection=_query_candidata_snapshot_fresh_connection(int(obj.fila)),
                    )
                    _legacy_buscar_trace(
                        "post_saved_ok",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        attempts=int(result.attempts),
                        after_nombre=getattr(obj, "nombre_completo", None),
                        after_telefono=getattr(obj, "numero_telefono", None),
                        after_empleo=getattr(obj, "empleo_anterior", None),
                    )
                    log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Edición de candidata {obj.nombre_completo or obj.fila}",
                        metadata={"candidata_id": obj.fila, "attempt_count": int(result.attempts)},
                        changes=changes,
                        success=True,
                    )
                    if mensaje:
                        flash("✅ Datos actualizados (cédula no actualizada).", "warning")
                        candidata = obj
                        return render_template(
                            'buscar.html',
                            busqueda=busqueda,
                            resultados=resultados,
                            candidata=candidata,
                            mensaje=mensaje,
                            edit_form_overrides=edit_form_overrides,
                        )
                    flash("✅ Datos actualizados correctamente.", "success")
                    return redirect(url_for('buscar_candidata', candidata_id=cid))

                error_message = (result.error_message or "").lower()
                _legacy_buscar_trace(
                    "post_saved_fail",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    attempts=int(result.attempts),
                    error=(result.error_message or ""),
                )
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
                _legacy_buscar_trace(
                    "post_early_return",
                    reason="candidata_not_found",
                    candidata_id_form=cid,
                )
                mensaje = "⚠️ Candidata no encontrada."
        else:
            _legacy_buscar_trace(
                "post_early_return",
                reason="invalid_candidata_id",
                candidata_id_form=cid,
            )
            mensaje = "❌ ID de candidata inválido."
    elif request.method == "POST":
        _legacy_buscar_trace(
            "post_skip_update_branch",
            reason="guardar_edicion_missing_or_empty",
            keys=sorted(list(request.form.keys())),
        )

    # Carga detalles (GET ?candidata_id=)
    cid = (request.args.get('candidata_id') or '').strip()
    if cid.isdigit():
        candidata = get_candidata_by_id(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."
        else:
            session["last_edited_candidata_fila"] = int(candidata.fila)
            _legacy_buscar_trace(
                "get_open_candidate",
                candidata_id_query=cid,
                fila_obj=getattr(candidata, "fila", None),
                nombre=getattr(candidata, "nombre_completo", None),
            )

    # ================== BÚSQUEDA ==================
    if busqueda and not candidata:
        try:
            resultados = search_candidatas_limited(
                busqueda,
                limit=300,
                order_mode="id_desc",
                log_label="buscar",
            )
            resultados = _prioritize_candidata_result(
                resultados,
                session.get("last_edited_candidata_fila"),
            )
            _legacy_buscar_trace(
                "search_results",
                busqueda=busqueda,
                filas=[int(getattr(r, "fila", 0) or 0) for r in (resultados or [])[:10]],
                total=len(resultados or []),
                last_edited=session.get("last_edited_candidata_fila"),
            )
            _legacy_buscar_db_trace(
                "search_results_db",
                busqueda=busqueda,
                first_fila=int(getattr((resultados or [None])[0], "fila", 0) or 0) if resultados else None,
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
        mensaje=mensaje,
        edit_form_overrides=edit_form_overrides,
    )


# -----------------------------------------------------------------------------
# FILTRAR
# -----------------------------------------------------------------------------
@app.route('/filtrar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def filtrar():
    from core.handlers import candidatas_filtrar_handlers as filtrar_h
    return filtrar_h.filtrar()


# -----------------------------------------------------------------------------
# INSCRIPCIÓN / PORCENTAJE / PAGOS / REPORTE PAGOS
# -----------------------------------------------------------------------------

@app.route('/inscripcion', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def inscripcion():
    from core.handlers import procesos_transacciones_handlers as procesos_transacciones_h
    return procesos_transacciones_h.inscripcion()


@app.route('/porciento', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def porciento():
    from core.handlers import procesos_transacciones_handlers as procesos_transacciones_h
    return procesos_transacciones_h.porciento()


@app.route('/pagos', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def pagos():
    from core.handlers import procesos_transacciones_handlers as procesos_transacciones_h
    return procesos_transacciones_h.pagos()

def _retry_query(callable_fn, retries: int = 2, swallow: bool = False):
    return _svc_retry_query(callable_fn, retries=retries, swallow=swallow)


@app.route('/reporte_inscripciones', methods=['GET'])
@roles_required('admin')
def reporte_inscripciones():
    from core.handlers import procesos_reportes_handlers as procesos_reportes_h
    return procesos_reportes_h.reporte_inscripciones()



@app.route('/reporte_pagos', methods=['GET'])
@roles_required('admin', 'secretaria')
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_PAGOS_SECONDS", "45")),
    key_prefix=lambda: _cache_key_with_role("reporte_pagos"),
)
def reporte_pagos():
    from core.handlers import procesos_reportes_handlers as procesos_reportes_h
    return procesos_reportes_h.reporte_pagos()
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
    from core.handlers import gestionar_archivos_handlers as gestionar_h
    return gestionar_h.gestionar_archivos()



def generar_pdf_entrevista():
    from core.handlers import entrevistas_pdf_handlers as pdf_h
    return pdf_h.generar_pdf_entrevista()

# -----------------------------------------------------------------------------
# NUEVO PDF (ENTREVISTAS NUEVAS EN BD)  ✅ NO BORRA LO VIEJO
# -----------------------------------------------------------------------------

def generar_pdf_entrevista_nueva_db(entrevista_id: int):
    from core.handlers import entrevistas_pdf_handlers as pdf_h
    return pdf_h.generar_pdf_entrevista_nueva_db(entrevista_id)


def generar_pdf_ultima_entrevista_candidata(fila: int):
    from core.handlers import entrevistas_pdf_handlers as pdf_h
    return pdf_h.generar_pdf_ultima_entrevista_candidata(fila)


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
                filas = search_candidatas_limited(
                    termino,
                    limit=300,
                    order_mode="id_desc",
                    log_label="referencias",
                )
                filas = _prioritize_candidata_result(
                    filas,
                    session.get("last_edited_candidata_fila"),
                )
                _legacy_buscar_trace(
                    "referencias_search_results",
                    termino=termino,
                    filas=[int(getattr(r, "fila", 0) or 0) for r in (filas or [])[:10]],
                    total=len(filas or []),
                    last_edited=session.get("last_edited_candidata_fila"),
                )
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
            candidata.contactos_referencias_laborales = cand_ref_lab
            candidata.referencias_familiares_detalle = cand_ref_fam
            result = execute_robust_save(
                session=db.session,
                persist_fn=lambda _attempt: None,
                verify_fn=lambda: _verify_candidata_fields_saved(
                    int(cid),
                    {
                        "referencias_laboral": cand_ref_lab,
                        "referencias_familiares": cand_ref_fam,
                        "contactos_referencias_laborales": cand_ref_lab,
                        "referencias_familiares_detalle": cand_ref_fam,
                    },
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
    from core.handlers import procesos_dashboard_handlers as procesos_dashboard_h
    return procesos_dashboard_h.dashboard_procesos()


@app.route('/auto_actualizar_estados', methods=['GET'])
@roles_required('admin', 'secretaria')
def auto_actualizar_estados():
    from core.handlers import procesos_automatizaciones_handlers as procesos_auto_h
    return procesos_auto_h.auto_actualizar_estados()


# -----------------------------------------------------------------------------
# LLAMADAS CANDIDATAS
# -----------------------------------------------------------------------------
@app.route('/candidatas/llamadas')
@roles_required('admin','secretaria')
def listado_llamadas_candidatas():
    from core.handlers import llamadas_candidatas_handlers as llamadas_h
    return llamadas_h.listado_llamadas_candidatas()


@app.route('/candidatas/<int:fila>/llamar', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def registrar_llamada_candidata(fila):
    from core.handlers import llamadas_candidatas_handlers as llamadas_h
    return llamadas_h.registrar_llamada_candidata(fila)


@app.route('/candidatas/llamadas/reporte')
@roles_required('admin')
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_LLAMADAS_SECONDS", "30")),
    key_prefix=lambda: _cache_key_with_role("reporte_llamadas"),
)
def reporte_llamadas_candidatas():
    from core.handlers import llamadas_candidatas_handlers as llamadas_h
    return llamadas_h.reporte_llamadas_candidatas()


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
    txt = (s or "").strip()
    if txt.lower() in {"otro", "otro...", "otro…"}:
        return ""
    return txt

def _normalize_modalidad_publicar(value):
    txt = (value or "").strip()
    if not txt:
        return ""
    low = txt.lower()
    if "viernes a lunes" in low:
        if "dormida" in low or "interna" in low:
            return "Con dormida 💤 fin de semana"
        if "salida diaria" in low:
            return "Salida diaria - fin de semana"
    if low.startswith("con dormida") and "💤" not in txt:
        rest = txt[len("con dormida"):].strip()
        return f"Con dormida 💤 {rest}".strip()
    return txt

def _s(v):
    return "" if v is None else str(v).strip()


# ─────────────────────────────────────────────────────────────
# PUBLICAR HOY (listado para copiar+marcar) – template: secretarias_solicitudes_copiar.html
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/copiar', methods=['GET'])
@roles_required('admin', 'secretaria')
def secretarias_copiar_solicitudes():
    from core.handlers import secretarias_solicitudes_handlers as secretarias_solicitudes_h
    return secretarias_solicitudes_h.secretarias_copiar_solicitudes()


# ─────────────────────────────────────────────────────────────
# COPIAR Y MARCAR (POST)
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/<int:id>/copiar', methods=['POST'])
@roles_required('admin', 'secretaria')
def secretarias_copiar_solicitud(id):
    from core.handlers import secretarias_solicitudes_handlers as secretarias_solicitudes_h
    return secretarias_solicitudes_h.secretarias_copiar_solicitud(id)


# ─────────────────────────────────────────────────────────────
# BUSCAR (paginado + filtros) – template: secretarias_solicitudes_buscar.html
# ─────────────────────────────────────────────────────────────
@app.route('/secretarias/solicitudes/buscar', methods=['GET'])
@roles_required('admin', 'secretaria')
def secretarias_buscar_solicitudes():
    from core.handlers import secretarias_solicitudes_handlers as secretarias_solicitudes_h
    return secretarias_solicitudes_h.secretarias_buscar_solicitudes()

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
    from core.handlers import finalizar_proceso_handlers as finalizar_h
    return finalizar_h.finalizar_proceso_buscar()


# ---------- FORMULARIO FINALIZAR ----------
@app.route('/finalizar_proceso', methods=['GET', 'POST'])
@roles_required('admin', 'secretaria')
def finalizar_proceso():
    from core.handlers import finalizar_proceso_handlers as finalizar_h
    return finalizar_h.finalizar_proceso()


# ---------- PERFIL (HTML/IMAGEN) ----------
# Compat temporal: handlers migrados a core.handlers.candidata_perfil_handlers
def ver_perfil():
    from core.handlers import candidata_perfil_handlers as perfil_h
    return perfil_h.ver_perfil()


def perfil_candidata():
    from core.handlers import candidata_perfil_handlers as perfil_h
    return perfil_h.perfil_candidata()


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
# Compat temporal: handler migrado a core.handlers.compat_candidata_handlers
# ─────────────────────────────────────────────────────────────
def compat_candidata():
    from core.handlers import compat_candidata_handlers as compat_h
    return compat_h.compat_candidata()


from flask import render_template, session, redirect, url_for, request

@app.route('/candidatas_porcentaje')
@roles_required('admin', 'secretaria')
def candidatas_porcentaje():
    from core.handlers import candidatas_porcentaje_handlers as porcentaje_h
    return porcentaje_h.candidatas_porcentaje()


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
