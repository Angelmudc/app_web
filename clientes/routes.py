# -*- coding: utf-8 -*-
from datetime import datetime, date
from functools import wraps
import os
import re
import json
import hashlib
import urllib.parse
from typing import Optional  # ✅ PARA PYTHON 3.9

from flask import (
    render_template, redirect, url_for, flash,
    request, abort, g, session, current_app, jsonify, make_response, send_file
)
from flask_login import (
    login_required, current_user, login_user, logout_user
)
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from config_app import db, cache
try:
    from models import Cliente, Solicitud, Candidata, CandidataWeb, SolicitudCandidata, ClienteNotificacion
except Exception:
    from models import Cliente, Solicitud
    Candidata = None
    CandidataWeb = None
    SolicitudCandidata = None
    ClienteNotificacion = None

from utils import letra_por_indice
from utils.guards import candidata_esta_descalificada, candidatas_activas_filter
from utils.matching_explain import client_bullets_from_breakdown
from utils.audit_logger import log_action

# ✅ IMPORTANTE: traemos también AREAS_COMUNES_CHOICES desde forms
from .forms import (
    AREAS_COMUNES_CHOICES,
    ClienteLoginForm,
    ClienteCancelForm,
    SolicitudForm,
    ClienteSolicitudForm,
    SolicitudPublicaForm
)

from . import clientes_bp
from decorators import cliente_required, politicas_requeridas
from utils.compat_engine import (
    HORARIO_OPTIONS,
    MASCOTAS_CHOICES,
    MASCOTAS_IMPORTANCIA_CHOICES,
    compute_match,
    format_compat_result,
    normalize_mascotas_importancia,
    normalize_mascotas_token,
    normalize_horarios_tokens,
    persist_result_to_solicitud,
)


# ─────────────────────────────────────────────────────────────
# 🔒 Banco de domésticas
# ─────────────────────────────────────────────────────────────

PLANES_BANCO_DOMESTICAS = {'premium', 'vip'}
ESTADOS_SOLICITUD_ACTIVA = {'activa'}


# ─────────────────────────────────────────────────────────────
# 🔒 Anti fuerza bruta (clientes/login)  IP + identificador
# ─────────────────────────────────────────────────────────────
_CLIENTE_LOGIN_MAX_INTENTOS = int((os.getenv("CLIENTE_LOGIN_MAX_INTENTOS") or "6").strip() or 6)
_CLIENTE_LOGIN_LOCK_MINUTOS = int((os.getenv("CLIENTE_LOGIN_LOCK_MINUTOS") or "10").strip() or 10)
_CLIENTE_LOGIN_KEY_PREFIX   = "cliente_login"


def _cliente_ip() -> str:
    trust_xff = (os.getenv("TRUST_XFF", "0").strip().lower() in ("1", "true", "yes", "on"))
    if trust_xff:
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()[:64]
    return (request.remote_addr or "0.0.0.0").strip()[:64]


def _cliente_login_keys(ident_norm: str):
    ip = _cliente_ip()
    u = (ident_norm or "").strip().lower()[:80]
    base = f"{_CLIENTE_LOGIN_KEY_PREFIX}:{ip}:{u}"
    return {"fail": f"{base}:fail", "lock": f"{base}:lock"}


def _cache_ok() -> bool:
    try:
        _ = cache.get("__ping__")
        return True
    except Exception:
        return False


def _cliente_is_locked(ident_norm: str) -> bool:
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            return False
    return False


def _cliente_register_fail(ident_norm: str) -> int:
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        try:
            n = int(cache.get(keys["fail"]) or 0) + 1
        except Exception:
            n = 1

        try:
            cache.set(keys["fail"], n, timeout=_CLIENTE_LOGIN_LOCK_MINUTOS * 60)
        except Exception:
            return n

        if n >= _CLIENTE_LOGIN_MAX_INTENTOS:
            try:
                cache.set(keys["lock"], True, timeout=_CLIENTE_LOGIN_LOCK_MINUTOS * 60)
            except Exception:
                pass
        return n

    return 1


def _cliente_reset_fail(ident_norm: str):
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        try:
            cache.delete(keys["fail"])
            cache.delete(keys["lock"])
        except Exception:
            pass


def _trust_xff() -> bool:
    return (os.getenv("TRUST_XFF", "").strip().lower() in ("1", "true", "yes", "on"))


def _client_ip_for_security_layer() -> str:
    ip = ""
    if _trust_xff():
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            ip = xff.split(",")[0].strip()

    if not ip:
        ip = (request.remote_addr or "").strip()

    return ip[:64]


def _get_plan_solicitud(s: 'Solicitud') -> str:
    for attr in ('tipo_plan', 'plan', 'plan_cliente', 'tipo_plan_cliente'):
        if hasattr(s, attr):
            v = getattr(s, attr)
            return (v or '').strip().lower()
    return ''


def _cliente_tiene_banco_domesticas(cliente_id: int) -> bool:
    try:
        q = Solicitud.query.filter(Solicitud.cliente_id == cliente_id)

        if hasattr(Solicitud, 'estado'):
            q = q.filter(Solicitud.estado == 'activa')

        for s in q.order_by(Solicitud.id.desc()).limit(200).all():
            plan = _get_plan_solicitud(s)
            if plan in PLANES_BANCO_DOMESTICAS:
                return True
        return False
    except Exception:
        return False


def banco_domesticas_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            nxt = request.full_path if request.full_path else request.path
            nxt = nxt if _is_safe_next(nxt) else url_for('clientes.dashboard')
            return redirect(url_for('clientes.login', next=nxt))

        if getattr(current_user, 'role', 'cliente') != 'cliente':
            abort(404)

        ok = _cliente_tiene_banco_domesticas(
            int(getattr(current_user, 'id', 0) or 0)
        )
        if not ok:
            flash(
                'Este acceso es solo para clientes con una solicitud ACTIVA en plan Premium o VIP.',
                'warning'
            )
            return redirect(url_for('clientes.listar_solicitudes'))

        return f(*args, **kwargs)
    return decorated


@clientes_bp.before_request
def _clientes_force_login_view():
    """
    Fuerza que todo /clientes/*:
      - Use siempre el login del blueprint clientes.
      - No permita acceso si no está autenticado.
      - No permita que un usuario que NO sea Cliente (ej: admin) entre al portal.
    """

    # Solo aplica dentro del blueprint de clientes
    if (request.blueprint or '') != 'clientes':
        return None

    # Forzar login_view correcto
    try:
        lm = current_app.extensions.get('login_manager')
        if lm is not None:
            lm.login_view = 'clientes.login'
            if not hasattr(lm, 'blueprint_login_views') or lm.blueprint_login_views is None:
                lm.blueprint_login_views = {}
            lm.blueprint_login_views['clientes'] = 'clientes.login'
    except Exception:
        pass

    # Endpoints públicos dentro del portal
    PUBLIC_ENDPOINTS = {
        'clientes.login',
        'clientes.reset_password',
        'clientes.solicitud_publica',
        'clientes.politicas',
        'clientes.aceptar_politicas',
        'clientes.rechazar_politicas',
        'static',
    }

    if request.endpoint is None:
        return None

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    # 🔒 Si NO está autenticado → login clientes
    if not current_user.is_authenticated:
        next_url = request.full_path if request.full_path else request.path
        next_url = next_url if _is_safe_next(next_url) else url_for('clientes.dashboard')
        return redirect(url_for('clientes.login', next=next_url))

    # 🔒 Si está autenticado pero NO es Cliente → expulsar
    if not isinstance(current_user, Cliente):
        try:
            logout_user()
            session.clear()
        except Exception:
            pass
        next_url = request.full_path if request.full_path else request.path
        next_url = next_url if _is_safe_next(next_url) else url_for('clientes.dashboard')
        return redirect(url_for('clientes.login', next=next_url))

    return None


@clientes_bp.after_request
def _clientes_no_cache_headers(response):
    try:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    except Exception:
        pass
    return response


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _norm_email(v: str) -> str:
    return (v or "").strip().lower()


def _norm_text(v: str) -> str:
    s = (v or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _public_link_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="clientes-solicitud-publica"
    )


def generar_token_publico_cliente(cliente: Cliente) -> str:
    ser = _public_link_serializer()
    payload = {
        "cliente_id": int(cliente.id),
        "codigo": str(cliente.codigo).strip(),
    }
    return ser.dumps(payload)


def _is_safe_next(next_url: str) -> bool:
    if not next_url:
        return False

    next_url = str(next_url).strip()

    # Solo rutas internas seguras
    if next_url.startswith("/"):
        return not next_url.startswith("//")

    # Permitir absoluto SOLO si es el mismo host
    try:
        from urllib.parse import urlparse
        cur = urlparse(request.host_url)
        nxt = urlparse(next_url)
        if (
            nxt.scheme in ("http", "https")
            and nxt.netloc == cur.netloc
            and (nxt.path or "").startswith("/")
        ):
            return True
    except Exception:
        return False

    return False


# ─────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────

@clientes_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = ClienteLoginForm()

    raw_next = (request.args.get('next') or request.form.get('next') or '').strip()
    next_url = raw_next if _is_safe_next(raw_next) else url_for('clientes.dashboard')


    if request.method == "POST":
        # Honeypot (agrega input hidden name="website" en el template si quieres)
        if (request.form.get("website") or "").strip():
            return "", 400

        ident_raw = (getattr(form, "username", None).data if hasattr(form, "username") else request.form.get("username")) or ""
        ident_norm = (ident_raw or "").strip().lower()

        if _cliente_is_locked(ident_norm):
            mins = _CLIENTE_LOGIN_LOCK_MINUTOS
            flash(f'Has excedido el máximo de intentos. Intenta de nuevo en {mins} minutos.', 'danger')
            return render_template('clientes/login.html', form=form, next_url=next_url), 429

    if form.validate_on_submit():
        identificador = (form.username.data or "").strip()
        password = (form.password.data or "")

        ident_norm = identificador.strip().lower()

        user = None
        try:
            if hasattr(Cliente, 'username'):
                user = Cliente.query.filter(Cliente.username == identificador).first()
        except Exception:
            user = None

        if not user:
            user = Cliente.query.filter(Cliente.email == identificador).first()

        if not user:
            user = Cliente.query.filter(Cliente.codigo == identificador).first()

        if not user:
            _cliente_register_fail(ident_norm)
            flash('Usuario o contraseña inválidos.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        if getattr(user, "password_hash", None) == "DISABLED_RESET_REQUIRED":
            flash('Debes restablecer tu contraseña antes de iniciar sesión.', 'warning')
            return redirect(url_for('clientes.reset_password', codigo=user.codigo))

        if not hasattr(user, 'password_hash'):
            _cliente_register_fail(ident_norm)
            flash('Este cliente no tiene credenciales configuradas. Contacta soporte.', 'warning')
            return redirect(url_for('clientes.login', next=next_url))

        ok = False
        try:
            ok = check_password_hash(user.password_hash, password)
        except Exception:
            ok = False

        if not ok:
            _cliente_register_fail(ident_norm)
            flash('Usuario o contraseña inválidos.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        if not getattr(user, "is_active", True):
            flash('Cuenta inactiva. Contacta soporte.', 'warning')
            return redirect(url_for('clientes.login'))

        # ✅ Login correcto
        _cliente_reset_fail(ident_norm)

        try:
            session.clear()
        except Exception:
            pass

        login_user(user, remember=False)

        try:
            session.permanent = False
            session.modified = True
        except Exception:
            pass

        try:
            clear_fn = current_app.extensions.get("clear_login_attempts")
            if callable(clear_fn):
                ip = _client_ip_for_security_layer()
                uname = (getattr(user, "username", "") or identificador or "").strip()
                clear_fn(ip, "/clientes/login", uname)
        except Exception:
            pass

        flash('Bienvenido.', 'success')

        if not _is_safe_next(next_url):
            next_url = url_for('clientes.dashboard')

        return redirect(next_url)

    return render_template('clientes/login.html', form=form, next_url=next_url)


@clientes_bp.app_context_processor
def _inject_client_notif_unread_count():
    unread = 0
    try:
        if (
            ClienteNotificacion is not None
            and getattr(current_user, "is_authenticated", False)
            and isinstance(current_user, Cliente)
        ):
            unread = (
                ClienteNotificacion.query
                .filter_by(cliente_id=current_user.id, is_read=False, is_deleted=False)
                .count()
            )
    except Exception:
        unread = 0
    return {"notif_unread_count": int(unread or 0)}


@clientes_bp.route('/logout', methods=['POST'])
@login_required
@cliente_required
def logout():
    logout_user()
    try:
        session.clear()
    except Exception:
        pass
    flash('Has cerrado sesión correctamente.', 'success')
    return redirect(url_for('clientes.login'))


# ─────────────────────────────────────────────────────────────
# Reset de contraseña (por código del cliente)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/reset-password', methods=['GET', 'POST'], endpoint='reset_password')
def reset_password():
    codigo = (request.args.get('codigo') or request.form.get('codigo') or '').strip()
    next_raw = (request.args.get('next') or request.form.get('next') or '').strip()
    next_url = next_raw if _is_safe_next(next_raw) else url_for('clientes.dashboard')

    if not codigo:
        flash('Debes indicar tu código de cliente para restablecer la contraseña.', 'warning')
        return redirect(url_for('clientes.login', next=next_url))

    user = Cliente.query.filter(db.func.lower(Cliente.codigo) == codigo.lower()).first()
    if not user:
        flash('No encontramos un cliente con ese código.', 'danger')
        return redirect(url_for('clientes.login', next=next_url))

    if request.method == 'POST':
        p1 = (request.form.get('password1') or '').strip()
        p2 = (request.form.get('password2') or '').strip()

        if len(p1) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'warning')
        elif len(p1) > 128:
            flash('La contraseña es demasiado larga.', 'warning')
        elif p1 != p2:
            flash('Las contraseñas no coinciden.', 'warning')
        else:
            try:
                user.password_hash = generate_password_hash(p1)
                if hasattr(user, 'fecha_ultima_actividad'):
                    user.fecha_ultima_actividad = datetime.utcnow()
                db.session.commit()
                flash('Tu contraseña fue restablecida. Ya puedes iniciar sesión.', 'success')
                return redirect(url_for('clientes.login', next=next_url))
            except Exception:
                db.session.rollback()
                flash('No se pudo guardar la nueva contraseña. Intenta nuevamente.', 'danger')

    return render_template('clientes/reset_password.html', user=user, next_url=next_url)


# ─────────────────────────────────────────────────────────────
# Dashboard del cliente
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/dashboard')
@login_required
@cliente_required
def dashboard():
    total = Solicitud.query.filter_by(cliente_id=current_user.id).count()
    por_estado = (
        db.session.query(Solicitud.estado, db.func.count(Solicitud.id))
        .filter(Solicitud.cliente_id == current_user.id)
        .group_by(Solicitud.estado)
        .all()
    )
    por_estado_dict = {estado or 'sin_definir': cnt for estado, cnt in por_estado}

    total_activas = int(por_estado_dict.get('activa', 0) or 0)
    total_pagadas = int(por_estado_dict.get('pagada', 0) or 0)

    # OJO: fecha_solicitud puede no existir en algunos modelos viejos
    q_rec = Solicitud.query.filter_by(cliente_id=current_user.id)
    if hasattr(Solicitud, 'fecha_solicitud'):
        q_rec = q_rec.order_by(Solicitud.fecha_solicitud.desc())
    else:
        q_rec = q_rec.order_by(Solicitud.id.desc())

    recientes = q_rec.limit(5).all()

    return render_template(
        'clientes/dashboard.html',
        total_solicitudes=total,
        por_estado=por_estado_dict,
        recientes=recientes,
        hoy=date.today(),
        total_activas=total_activas,
        total_pagadas=total_pagadas,
    )


# ─────────────────────────────────────────────────────────────
# Páginas informativas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/informacion')
@login_required
@cliente_required
def informacion():
    return render_template('clientes/informacion.html')


@clientes_bp.route('/planes')
@login_required
@cliente_required
def planes():
    return render_template('clientes/planes.html')


@clientes_bp.route('/ayuda')
@login_required
@cliente_required
def ayuda():
    whatsapp = "+1 809 429 6892"  # reemplaza por el real
    return render_template('clientes/ayuda.html', whatsapp=whatsapp)


# ─────────────────────────────────────────────────────────────
# Keep-alive / refresh silencioso (cliente)
# ─────────────────────────────────────────────────────────────
def _json_no_cache(payload: dict, status: int = 200):
    """JSON response con headers anti-cache para refresco silencioso."""
    resp = make_response(jsonify(payload), status)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


_CLIENTE_PRESENCE_TTL_SECONDS = 65
_CLIENTE_PRESENCE_INDEX_KEY = "clientes_presence:index"


def _cliente_presence_key(cliente_id: int) -> str:
    return f"clientes_presence:{int(cliente_id)}"


def _should_log_cliente_live_event(cliente_id: int, event_type: str, path: str) -> bool:
    event = (event_type or "").strip().lower() or "heartbeat"
    timeout = 30 if event == "heartbeat" else 6
    key = f"cliente_live_event:{int(cliente_id)}:{event}:{(path or '')[:140]}"
    try:
        if cache.get(key):
            return False
        cache.set(key, 1, timeout=timeout)
    except Exception:
        return event != "heartbeat"
    return True


@clientes_bp.route('/ping', methods=['GET'])
@login_required
@cliente_required
def clientes_ping():
    """Endpoint liviano para saber si la sesión sigue activa."""
    return _json_no_cache({
        'ok': True,
        'server_time': datetime.utcnow().isoformat() + 'Z',
        'cliente_id': int(getattr(current_user, 'id', 0) or 0),
    })


@clientes_bp.route('/live/ping', methods=['POST'])
@login_required
@cliente_required
def clientes_live_ping():
    payload = request.get_json(silent=True) or {}
    current_path = (payload.get('current_path') or request.path or '').strip()[:255]
    event_type = (payload.get('event_type') or 'heartbeat').strip().lower()[:32]
    action_hint = (payload.get('action_hint') or 'browsing').strip().lower()[:80]
    solicitud_id = str(payload.get('solicitud_id') or '').strip()[:64]
    cliente_id = int(getattr(current_user, 'id', 0) or 0)
    if not cliente_id:
        abort(403)

    presence_payload = {
        'cliente_id': cliente_id,
        'cliente_codigo': str(getattr(current_user, 'codigo', '') or '')[:50],
        'cliente_nombre': str(getattr(current_user, 'nombre_completo', '') or '')[:180],
        'current_path': current_path,
        'event_type': event_type,
        'action_hint': action_hint,
        'solicitud_id': solicitud_id,
        'last_seen_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    }
    try:
        cache.set(_cliente_presence_key(cliente_id), presence_payload, timeout=_CLIENTE_PRESENCE_TTL_SECONDS)
        idx = cache.get(_CLIENTE_PRESENCE_INDEX_KEY) or []
        try:
            idx = [int(x) for x in idx]
        except Exception:
            idx = []
        if cliente_id not in idx:
            idx.append(cliente_id)
        idx = idx[-2000:]
        cache.set(_CLIENTE_PRESENCE_INDEX_KEY, idx, timeout=max(3600, _CLIENTE_PRESENCE_TTL_SECONDS * 20))
    except Exception:
        pass

    if _should_log_cliente_live_event(cliente_id, event_type, current_path):
        meta = {
            'event_type': event_type,
            'action_hint': action_hint,
            'solicitud_id': solicitud_id or None,
            'scope': 'clientes',
        }
        log_action(
            action_type='CLIENTE_LIVE_EVENT',
            entity_type='cliente',
            entity_id=str(cliente_id),
            summary=f'Cliente activo en {current_path}'[:255],
            metadata=meta,
            success=True,
        )
    return _json_no_cache({'ok': True, 'cliente_id': cliente_id})


@clientes_bp.route('/solicitudes/live', methods=['GET'])
@login_required
@cliente_required
def clientes_solicitudes_live():
    """Snapshot mínimo para refrescar listados sin recargar toda la página."""
    q = (request.args.get('q') or '').strip()[:120]
    estado = (request.args.get('estado') or '').strip()[:40]
    ciudad = (request.args.get('ciudad') or '').strip()[:120]
    modalidad = (request.args.get('modalidad') or '').strip()[:120]
    fecha_desde_raw = (request.args.get('fecha_desde') or '').strip()[:20]
    fecha_hasta_raw = (request.args.get('fecha_hasta') or '').strip()[:20]
    limit = request.args.get('limit', 20, type=int)
    limit = max(1, min(limit, 50))

    query = Solicitud.query.filter(Solicitud.cliente_id == current_user.id)

    if estado:
        query = query.filter(Solicitud.estado == estado)

    if ciudad:
        query = query.filter(getattr(Solicitud, 'ciudad_sector', db.literal('')).ilike(f"%{ciudad}%"))

    if modalidad:
        query = query.filter(getattr(Solicitud, 'modalidad_trabajo', db.literal('')).ilike(f"%{modalidad}%"))

    if fecha_desde_raw and hasattr(Solicitud, 'fecha_solicitud'):
        try:
            fecha_desde = datetime.strptime(fecha_desde_raw, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Solicitud.fecha_solicitud) >= fecha_desde)
        except ValueError:
            pass

    if fecha_hasta_raw and hasattr(Solicitud, 'fecha_solicitud'):
        try:
            fecha_hasta = datetime.strptime(fecha_hasta_raw, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Solicitud.fecha_solicitud) <= fecha_hasta)
        except ValueError:
            pass

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Solicitud.codigo_solicitud.ilike(like),
                getattr(Solicitud, 'ciudad_sector', db.literal('')).ilike(like),
                getattr(Solicitud, 'modalidad_trabajo', db.literal('')).ilike(like),
                getattr(Solicitud, 'experiencia', db.literal('')).ilike(like)
            )
        )

    if hasattr(Solicitud, 'fecha_solicitud'):
        query = query.order_by(Solicitud.fecha_solicitud.desc())
    else:
        query = query.order_by(Solicitud.id.desc())

    items = query.limit(limit).all()

    def _dt_iso(dt):
        try:
            return dt.isoformat() + 'Z' if dt else None
        except Exception:
            return None

    data = []
    for s in items:
        data.append({
            'id': int(s.id),
            'codigo_solicitud': getattr(s, 'codigo_solicitud', None),
            'estado': getattr(s, 'estado', None),
            'fecha_solicitud': _dt_iso(getattr(s, 'fecha_solicitud', None)),
            'fecha_ultima_modificacion': _dt_iso(getattr(s, 'fecha_ultima_modificacion', None)),
            'monto_pagado': str(getattr(s, 'monto_pagado', '') or ''),
            'saldo_pendiente': str(getattr(s, 'saldo_pendiente', '') or ''),
        })

    try:
        counts = (
            db.session.query(Solicitud.estado, db.func.count(Solicitud.id))
            .filter(Solicitud.cliente_id == current_user.id)
            .group_by(Solicitud.estado)
            .all()
        )
        counts = {(k or 'sin_definir'): int(v) for k, v in counts}
    except Exception:
        counts = {}

    return _json_no_cache({
        'ok': True,
        'server_time': datetime.utcnow().isoformat() + 'Z',
        'q': q,
        'estado': estado,
        'ciudad': ciudad,
        'modalidad': modalidad,
        'fecha_desde': fecha_desde_raw,
        'fecha_hasta': fecha_hasta_raw,
        'count_by_estado': counts,
        'items': data,
    })


# ─────────────────────────────────────────────────────────────
# Listado de solicitudes (búsqueda + filtro + paginación)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes')
@login_required
@cliente_required
def listar_solicitudes():
    q        = request.args.get('q', '').strip()[:120]
    estado   = request.args.get('estado', '').strip()[:40]
    ciudad   = request.args.get('ciudad', '').strip()[:120]
    modalidad = request.args.get('modalidad', '').strip()[:120]
    fecha_desde_raw = (request.args.get('fecha_desde') or '').strip()[:20]
    fecha_hasta_raw = (request.args.get('fecha_hasta') or '').strip()[:20]
    page     = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    page     = max(page, 1)
    per_page = max(1, min(per_page, 50))

    query = Solicitud.query.filter(Solicitud.cliente_id == current_user.id)

    if estado:
        query = query.filter(Solicitud.estado == estado)

    if ciudad:
        query = query.filter(getattr(Solicitud, 'ciudad_sector', db.literal('')).ilike(f"%{ciudad}%"))

    if modalidad:
        query = query.filter(getattr(Solicitud, 'modalidad_trabajo', db.literal('')).ilike(f"%{modalidad}%"))

    if fecha_desde_raw and hasattr(Solicitud, 'fecha_solicitud'):
        try:
            fecha_desde = datetime.strptime(fecha_desde_raw, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Solicitud.fecha_solicitud) >= fecha_desde)
        except ValueError:
            pass

    if fecha_hasta_raw and hasattr(Solicitud, 'fecha_solicitud'):
        try:
            fecha_hasta = datetime.strptime(fecha_hasta_raw, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Solicitud.fecha_solicitud) <= fecha_hasta)
        except ValueError:
            pass

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Solicitud.codigo_solicitud.ilike(like),
                getattr(Solicitud, 'ciudad_sector', db.literal('')).ilike(like),
                getattr(Solicitud, 'modalidad_trabajo', db.literal('')).ilike(like),
                getattr(Solicitud, 'experiencia', db.literal('')).ilike(like)
            )
        )

    if hasattr(Solicitud, 'fecha_solicitud'):
        query = query.order_by(Solicitud.fecha_solicitud.desc())
    else:
        query = query.order_by(Solicitud.id.desc())

    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    estados_disponibles = [
        e[0] for e in (
            db.session.query(Solicitud.estado)
            .filter(Solicitud.cliente_id == current_user.id)
            .distinct()
            .all()
        ) if e[0]
    ]

    ciudades_disponibles = sorted([
        (e[0] or '').strip() for e in (
            db.session.query(Solicitud.ciudad_sector)
            .filter(Solicitud.cliente_id == current_user.id)
            .filter(Solicitud.ciudad_sector.isnot(None))
            .distinct()
            .all()
        ) if (e[0] or '').strip()
    ])

    modalidades_disponibles = sorted([
        (e[0] or '').strip() for e in (
            db.session.query(Solicitud.modalidad_trabajo)
            .filter(Solicitud.cliente_id == current_user.id)
            .filter(Solicitud.modalidad_trabajo.isnot(None))
            .distinct()
            .all()
        ) if (e[0] or '').strip()
    ])

    return render_template(
        'clientes/solicitudes_list.html',
        solicitudes=paginado.items,
        hoy=date.today(),
        page=page, per_page=per_page, total=paginado.total, pages=paginado.pages,
        has_prev=paginado.has_prev, has_next=paginado.has_next,
        prev_num=getattr(paginado, 'prev_num', None),
        next_num=getattr(paginado, 'next_num', None),
        q=q, estado=estado, ciudad=ciudad, modalidad=modalidad,
        fecha_desde=fecha_desde_raw, fecha_hasta=fecha_hasta_raw,
        estados_disponibles=estados_disponibles,
        ciudades_disponibles=ciudades_disponibles,
        modalidades_disponibles=modalidades_disponibles
    )


# ─────────────────────────────────────────────────────────────
# Helpers para normalización de formularios de solicitud
# ─────────────────────────────────────────────────────────────

def _first_form_data(form, *field_names, default=''):
    """Devuelve el primer .data no vacío de los campos indicados (si existen)."""
    for name in field_names:
        if hasattr(form, name):
            try:
                v = getattr(form, name).data
            except Exception:
                v = None
            if v is None:
                continue
            if isinstance(v, (list, tuple, set)):
                if len(v) > 0:
                    return v
                continue
            s = str(v).strip()
            if s:
                return s
    return default


def _set_attr_if_exists(obj, attr: str, value):
    if hasattr(obj, attr):
        try:
            setattr(obj, attr, value)
        except Exception:
            pass


def _set_attr_if_empty(obj, attr: str, value):
    """Setea solo si el valor actual está vacío/None."""
    if not hasattr(obj, attr):
        return
    try:
        cur = getattr(obj, attr)
    except Exception:
        cur = None
    empty = (cur is None) or (cur == '') or (cur == [])
    if empty:
        try:
            setattr(obj, attr, value)
        except Exception:
            pass


def _clean_list(seq):
    bad = {"-", "–", "—"}
    out, seen = [], set()
    for v in (seq or []):
        s = str(v).strip()
        if not s or s in bad:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _choices_maps(choices):
    code_to_label, label_to_code = {}, {}
    for code, label in (choices or []):
        c = str(code).strip()
        l = str(label).strip()
        if not c or not l:
            continue
        code_to_label[c] = l
        label_to_code[l] = c
    return code_to_label, label_to_code


def _map_edad_choices(codes_selected, edad_choices, otro_text):
    codes_selected = _clean_list([str(x) for x in (codes_selected or [])])
    code_to_label, _ = _choices_maps(edad_choices)

    result = []
    for code in codes_selected:
        if code == "otro":
            continue
        label = code_to_label.get(code)
        if label:
            result.append(label)

    if "otro" in codes_selected:
        extra = (otro_text or "").strip()
        if extra:
            result.append(extra)

    return _clean_list(result)


def _split_edad_for_form(stored_list, edad_choices):
    stored_list = _clean_list(stored_list)
    _, label_to_code = _choices_maps(edad_choices)

    selected_codes, otros = [], []
    for label in stored_list:
        code = label_to_code.get(label)
        if code:
            selected_codes.append(code)
        else:
            otros.append(label)

    otro_text = ", ".join(otros) if otros else ""
    if otros:
        selected_codes = _clean_list(selected_codes + ["otro"])
    return selected_codes, otro_text


def _map_funciones(vals, extra_text):
    vals = _clean_list(vals)
    if 'otro' in vals:
        vals = [v for v in vals if v != 'otro']
        extra = (extra_text or '').strip()
        if extra:
            vals.extend([x.strip() for x in extra.split(',') if x.strip()])
    return _clean_list(vals)


def _map_tipo_lugar(value, extra):
    value = (value or '').strip()
    if value == 'otro':
        return (extra or '').strip() or value
    return value



def _money_sanitize(raw):
    if raw is None:
        return None
    s = str(raw)
    limpio = s.replace('RD$', '').replace('$', '').replace('.', '').replace(',', '').strip()
    return limpio or s.strip()


# ─────────────────────────────────────────────────────────────
# Helpers: Anti-duplicados y locks para formularios de solicitud
# ─────────────────────────────────────────────────────────────
def _cache_add(cache_obj, key: str, value, timeout: int) -> bool:
    """Best-effort atomic add. Returns True if acquired/set, False otherwise."""
    try:
        # Flask-Caching supports .add() for many backends
        if hasattr(cache_obj, 'add'):
            return bool(cache_obj.add(key, value, timeout=timeout))
        # Fallback: if get is empty, set
        if cache_obj.get(key) is None:
            cache_obj.set(key, value, timeout=timeout)
            return True
        return False
    except Exception:
        return False


def _cache_set(cache_obj, key: str, value, timeout: int) -> bool:
    try:
        cache_obj.set(key, value, timeout=timeout)
        return True
    except Exception:
        return False


def _cache_del(cache_obj, key: str) -> bool:
    try:
        cache_obj.delete(key)
        return True
    except Exception:
        return False


def _solicitud_fingerprint(form_obj) -> str:
    """Fingerprint estable del contenido de la solicitud para evitar duplicados por doble click/reintento."""
    try:
        data = getattr(form_obj, 'data', {}) or {}
    except Exception:
        data = {}

    # Quitamos campos que no deben influir (CSRF, submit, tokens)
    drop = {
        'csrf_token', 'submit', 'token', 'codigo_solicitud', 'id', 'created_at', 'updated_at'
    }

    clean = {}
    for k, v in (data or {}).items():
        if k in drop:
            continue
        if isinstance(v, str):
            clean[k] = v.strip()
        elif isinstance(v, (list, tuple, set)):
            clean[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            clean[k] = v

    raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _request_fingerprint_from_form(path: str) -> str:
    """Fingerprint estable del POST actual (sin CSRF/submit) para prevenir doble envío."""
    try:
        items = []
        for k in sorted((request.form or {}).keys()):
            if k in ('csrf_token', 'submit'):
                continue
            vals = request.form.getlist(k)
            vals = [str(v).strip()[:120] for v in vals if str(v).strip()]
            if not vals:
                continue
            items.append((k, vals))
        raw = json.dumps({'p': (path or ''), 'f': items}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(path or '')

    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _session_dedupe_hit(key: str, ttl_seconds: int = 10) -> bool:
    """Fallback anti-doble submit usando session si cache no está disponible."""
    try:
        now = int(datetime.utcnow().timestamp())
        bucket = session.get('_post_dedupe', {}) or {}
        last = int(bucket.get(key) or 0)
        if last and (now - last) < int(ttl_seconds):
            return True
        bucket[key] = now
        # compacta un chin
        if len(bucket) > 60:
            for kk in list(bucket.keys())[:20]:
                bucket.pop(kk, None)
        session['_post_dedupe'] = bucket
        session.modified = True
        return False
    except Exception:
        return False


def _prevent_double_post(scope: str, seconds: int = 8) -> bool:
    """True si se permite, False si detectamos doble POST inmediato (cache o session)."""
    uid = int(getattr(current_user, 'id', 0) or 0)
    if uid <= 0:
        return True

    fp = _request_fingerprint_from_form(request.path or '')
    key = f"clientes:post:{scope}:{uid}:{fp}"

    # Preferir cache (más fuerte)
    if _cache_ok():
        try:
            return _cache_add(cache, key, 1, timeout=max(2, int(seconds)))
        except Exception:
            pass

    # Fallback session
    hit = _session_dedupe_hit(key, ttl_seconds=max(2, int(seconds)))
    return not hit


# ─────────────────────────────────────────────────────────────
# NUEVA SOLICITUD (CLIENTE) — requiere aceptar políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@cliente_required
@politicas_requeridas
def nueva_solicitud():
    form = SolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        form.funciones.data       = form.funciones.data or []
        form.areas_comunes.data   = form.areas_comunes.data or []
        form.edad_requerida.data  = form.edad_requerida.data or []
        if form.dos_pisos.data is None:
            form.dos_pisos.data = False
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = False

    if form.validate_on_submit():
        # Anti doble submit (global, sin JS)
        if not _prevent_double_post('solicitud_create', seconds=10):
            flash('Ya esa solicitud se está enviando. Evitamos duplicados.', 'warning')
            return redirect(url_for('clientes.listar_solicitudes'))
        # ─────────────────────────────────────────────────────────
        # Anti-duplicados / anti doble-click (sin JS)
        # - Lock corto por usuario para evitar carreras concurrentes
        # - Dedupe por fingerprint para evitar guardar 2 iguales por reintentos
        # ─────────────────────────────────────────────────────────
        lock_key = f"solicitud:create_lock:{int(getattr(current_user, 'id', 0) or 0)}"
        dedupe_key = None
        lock_acquired = False

        try:
            if _cache_ok():
                lock_acquired = _cache_add(cache, lock_key, 1, timeout=15)
                if not lock_acquired:
                    flash('Ya se está guardando una solicitud. Espera un momento y vuelve a intentar.', 'warning')
                    return redirect(url_for('clientes.listar_solicitudes'))

                fp = _solicitud_fingerprint(form)
                dedupe_key = f"solicitud:dedupe:{int(getattr(current_user, 'id', 0) or 0)}:{fp}"
                if cache.get(dedupe_key):
                    flash('Esa solicitud ya fue enviada hace un momento. Evitamos duplicados.', 'info')
                    return redirect(url_for('clientes.listar_solicitudes'))

                # Marcamos este fingerprint por 45s para bloquear duplicados por reintento
                _cache_set(cache, dedupe_key, True, timeout=45)
        except Exception:
            # Si el cache falla, no bloqueamos el flujo.
            pass

        try:
            idx = Solicitud.query.filter_by(cliente_id=current_user.id).count()
            while True:
                codigo = f"{current_user.codigo}-{letra_por_indice(idx)}"
                existe = Solicitud.query.filter_by(codigo_solicitud=codigo).first()
                if not existe:
                    break
                idx += 1

            s = Solicitud(
                cliente_id=current_user.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=codigo
            )
            form.populate_obj(s)

            ciudad = _first_form_data(form, 'ciudad', 'ciudad_oferta', 'ciudad_cliente', default='')
            sector = _first_form_data(form, 'sector', 'sector_oferta', 'sector_cliente', default='')
            if ciudad or sector:
                combo = " ".join([x for x in [ciudad, sector] if x]).strip()
                _set_attr_if_empty(s, 'ciudad_sector', combo)

            ruta = _first_form_data(form, 'rutas_cercanas', 'ruta_mas_cercana', 'ruta_cercana', 'ruta', default='')
            if ruta:
                _set_attr_if_empty(s, 'rutas_cercanas', ruta)

            funciones_otro_txt = _first_form_data(form, 'funciones_otro', default='')
            selected_funciones = _clean_list(form.funciones.data)
            funciones_otro_clean = funciones_otro_txt if 'otro' in selected_funciones else ''
            _set_attr_if_exists(s, 'funciones_otro', funciones_otro_clean or None)

            s.funciones      = _map_funciones(selected_funciones, funciones_otro_clean)
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad_choices(
                form.edad_requerida.data,
                form.edad_requerida.choices,
                getattr(form, 'edad_otro', None).data if hasattr(form, 'edad_otro') else ''
            )
            s.tipo_lugar     = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') if hasattr(form, 'tipo_lugar_otro') else ''
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.add(s)
            try:
                current_user.total_solicitudes = (current_user.total_solicitudes or 0) + 1
                current_user.fecha_ultima_solicitud = datetime.utcnow()
                current_user.fecha_ultima_actividad = datetime.utcnow()
            except Exception:
                pass

            # Flush para detectar problemas (y evitar que un error tarde dispare reintentos duplicados)
            db.session.flush()
            db.session.commit()
            flash(f'Solicitud {codigo} creada correctamente.', 'success')
            return redirect(url_for('clientes.listar_solicitudes'))

        except SQLAlchemyError as e:
            db.session.rollback()
            # Si falló, liberar dedupe para permitir reintento limpio
            try:
                if dedupe_key and _cache_ok():
                    _cache_del(cache, dedupe_key)
            except Exception:
                pass
            try:
                current_app.logger.exception("ERROR creando solicitud (cliente)")
            except Exception:
                pass

            msg = 'No se pudo crear la solicitud. Intenta de nuevo.'
            try:
                if bool(getattr(current_app, 'debug', False)):
                    msg = f"No se pudo crear la solicitud: {str(e)}"
            except Exception:
                pass

            flash(msg, 'danger')
        finally:
            # Liberar lock corto (si existe)
            try:
                if lock_acquired and _cache_ok():
                    _cache_del(cache, lock_key)
            except Exception:
                pass

    return render_template('clientes/solicitud_form.html', form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# EDITAR SOLICITUD (CLIENTE) — requiere aceptar políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/editar', methods=['GET','POST'])
@login_required
@cliente_required
@politicas_requeridas
def editar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = SolicitudForm(obj=s)
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        form.funciones.data      = _clean_list(s.funciones)
        form.areas_comunes.data  = _clean_list(s.areas_comunes)

        selected_codes, otro_text = _split_edad_for_form(
            stored_list=s.edad_requerida,
            edad_choices=form.edad_requerida.choices
        )
        form.edad_requerida.data = selected_codes
        if hasattr(form, 'edad_otro'):
            form.edad_otro.data = otro_text

        try:
            allowed_fun = {str(v) for v, _ in form.funciones.choices}
            custom_fun = [v for v in (s.funciones or []) if v and v not in allowed_fun]
            if custom_fun and hasattr(form, 'funciones_otro'):
                data = set(form.funciones.data or [])
                data.add('otro')
                form.funciones.data = list(data)
                form.funciones_otro.data = ', '.join(custom_fun)
        except Exception:
            pass

        try:
            allowed_tl = {str(v) for v, _ in form.tipo_lugar.choices}
            if s.tipo_lugar and s.tipo_lugar not in allowed_tl and hasattr(form, 'tipo_lugar_otro'):
                form.tipo_lugar.data = 'otro'
                form.tipo_lugar_otro.data = s.tipo_lugar
        except Exception:
            pass

        if form.dos_pisos.data is None:
            form.dos_pisos.data = bool(getattr(s, 'dos_pisos', False))
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))

    if form.validate_on_submit():
        # Anti doble submit (sin JS) + lock corto por usuario/solicitud
        if not _prevent_double_post('solicitud_edit', seconds=8):
            flash('Ya esa actualización se está enviando. Evitamos duplicados.', 'warning')
            return redirect(url_for('clientes.detalle_solicitud', id=id))

        lock_key = f"solicitud:edit_lock:{int(getattr(current_user, 'id', 0) or 0)}:{int(id)}"
        lock_acquired = False
        try:
            if _cache_ok():
                lock_acquired = _cache_add(cache, lock_key, 1, timeout=12)
                if not lock_acquired:
                    flash('Ya se está guardando esta solicitud. Espera un momento y vuelve a intentar.', 'warning')
                    return redirect(url_for('clientes.detalle_solicitud', id=id))
        except Exception:
            lock_acquired = False
        try:
            form.populate_obj(s)

            ciudad = _first_form_data(form, 'ciudad', 'ciudad_oferta', 'ciudad_cliente', default='')
            sector = _first_form_data(form, 'sector', 'sector_oferta', 'sector_cliente', default='')
            if ciudad or sector:
                combo = " ".join([x for x in [ciudad, sector] if x]).strip()
                # ✅ en editar sí debe actualizar
                _set_attr_if_exists(s, 'ciudad_sector', combo)

            ruta = _first_form_data(form, 'rutas_cercanas', 'ruta_mas_cercana', 'ruta_cercana', 'ruta', default='')
            if ruta:
                # ✅ en editar sí debe actualizar
                _set_attr_if_exists(s, 'rutas_cercanas', ruta)

            funciones_otro_txt = _first_form_data(form, 'funciones_otro', default='')
            selected_funciones = _clean_list(form.funciones.data)
            funciones_otro_clean = funciones_otro_txt if 'otro' in selected_funciones else ''
            _set_attr_if_exists(s, 'funciones_otro', funciones_otro_clean or None)

            s.funciones      = _map_funciones(selected_funciones, funciones_otro_clean)
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad_choices(
                form.edad_requerida.data,
                form.edad_requerida.choices,
                getattr(form, 'edad_otro', None).data if hasattr(form, 'edad_otro') else ''
            )
            s.tipo_lugar     = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') if hasattr(form, 'tipo_lugar_otro') else ''
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.flush()
            db.session.commit()
            flash('Solicitud actualizada.', 'success')
            return redirect(url_for('clientes.detalle_solicitud', id=id))

        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo actualizar la solicitud. Intenta de nuevo.', 'danger')
        finally:
            try:
                if lock_acquired and _cache_ok():
                    _cache_del(cache, lock_key)
            except Exception:
                pass

    return render_template('clientes/solicitud_form.html', form=form, editar=True, solicitud=s)


# ─────────────────────────────────────────────────────────────
# Detalle de solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>')
@login_required
@cliente_required
def detalle_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    envios = []
    if getattr(s, 'candidata', None):
        envios.append({
            'tipo': 'Envío inicial',
            'candidata': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })
    for idx, r in enumerate(getattr(s, 'reemplazos', []) or [], start=1):
        if getattr(r, 'candidata_new', None):
            envios.append({
                'tipo': f'Reemplazo #{idx}',
                'candidata': r.candidata_new.nombre_completo,
                'fecha': r.fecha_inicio_reemplazo or r.created_at
            })

    cancelaciones = []
    if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
        cancelaciones.append({
            'fecha': s.fecha_cancelacion,
            'motivo': getattr(s, 'motivo_cancelacion', '')
        })

    compat_result = None
    if getattr(s, 'candidata_id', None) and getattr(s, 'candidata', None):
        if getattr(s, 'compat_test_cliente_json', None) or getattr(s, 'compat_test_cliente', None):
            compat_result = format_compat_result(compute_match(s, s.candidata))

    candidatas_enviadas = []
    candidatas_enviadas_cards = []
    if SolicitudCandidata is not None:
        visible_status = ('enviada', 'vista', 'seleccionada')
        candidatas_enviadas = (
            SolicitudCandidata.query
            .filter(
                SolicitudCandidata.solicitud_id == s.id,
                SolicitudCandidata.status.in_(visible_status),
            )
            .order_by(SolicitudCandidata.created_at.desc(), SolicitudCandidata.id.desc())
            .all()
        )
        candidatas_enviadas = [sc for sc in candidatas_enviadas if getattr(sc, "status", None) in visible_status]
        candidatas_enviadas_cards = [_candidate_public_payload(sc) for sc in candidatas_enviadas]

    return render_template(
        'clientes/solicitud_detail.html',
        s=s,
        compat=compat_result,
        envios=envios,
        cancelaciones=cancelaciones,
        hoy=date.today(),
        candidatas_enviadas=candidatas_enviadas,
        candidatas_enviadas_cards=candidatas_enviadas_cards,
        candidatas_enviadas_count=len(candidatas_enviadas),
    )


_NOTIF_TIPO_CANDIDATAS_ENVIADAS = "candidatas_enviadas"


def _cliente_notif_query_base():
    if ClienteNotificacion is None:
        abort(404)
    return ClienteNotificacion.query.filter_by(cliente_id=current_user.id, is_deleted=False)


def _get_cliente_notificacion_or_404(notificacion_id: int):
    return (
        _cliente_notif_query_base()
        .filter_by(id=notificacion_id)
        .first_or_404()
    )


def _notificacion_target_url(notif) -> str:
    solicitud_id = getattr(notif, "solicitud_id", None)
    if solicitud_id:
        return url_for("clientes.solicitud_candidatas", solicitud_id=solicitud_id) + "#candidatas-enviadas"
    return url_for("clientes.listar_solicitudes")


def _notif_wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    xr = (request.headers.get("X-Requested-With") or "").lower()
    if "application/json" in accept:
        return True
    if xr == "xmlhttprequest":
        return True
    if str(request.args.get("ajax") or "").strip() == "1":
        return True
    return False


@clientes_bp.route('/notificaciones')
@login_required
@cliente_required
def notificaciones_list():
    flash("Revisa tus notificaciones en la campana.", "info")
    return redirect(url_for("clientes.dashboard"))


@clientes_bp.route('/notificaciones.json')
@login_required
@cliente_required
def notificaciones_json():
    if ClienteNotificacion is None:
        return jsonify({"ok": False, "error": "Notificaciones no disponibles"}), 404

    limit = request.args.get("limit", 10, type=int)
    limit = max(1, min(int(limit or 10), 15))

    items = (
        _cliente_notif_query_base()
        .order_by(ClienteNotificacion.created_at.desc(), ClienteNotificacion.id.desc())
        .limit(limit)
        .all()
    )
    unread_count = (
        ClienteNotificacion.query
        .filter_by(cliente_id=current_user.id, is_read=False, is_deleted=False)
        .count()
    )
    data = []
    for n in items:
        data.append(
            {
                "id": int(n.id),
                "title": n.titulo or "Notificacion",
                "body": n.cuerpo or "",
                "created_at": n.created_at.isoformat() + "Z" if n.created_at else None,
                "is_read": bool(n.is_read),
                "url": _notificacion_target_url(n),
            }
        )
    return jsonify({"unread_count": int(unread_count or 0), "items": data})


@clientes_bp.route('/notificaciones/<int:notificacion_id>/ver', methods=['POST'])
@login_required
@cliente_required
def notificacion_ver(notificacion_id):
    notif = _get_cliente_notificacion_or_404(notificacion_id)
    if not notif.is_read:
        notif.is_read = True
        notif.updated_at = datetime.utcnow()
        db.session.commit()
    target = _notificacion_target_url(notif)
    if _notif_wants_json():
        unread_count = (
            ClienteNotificacion.query
            .filter_by(cliente_id=current_user.id, is_read=False, is_deleted=False)
            .count()
        )
        return jsonify({"ok": True, "redirect_url": target, "unread_count": int(unread_count or 0)})
    return redirect(target)


@clientes_bp.route('/notificaciones/<int:notificacion_id>/marcar-leida', methods=['POST'])
@login_required
@cliente_required
def notificacion_marcar_leida(notificacion_id):
    notif = _get_cliente_notificacion_or_404(notificacion_id)
    if not notif.is_read:
        notif.is_read = True
        notif.updated_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("clientes.notificaciones_list"))


@clientes_bp.route('/notificaciones/<int:notificacion_id>/eliminar', methods=['POST'])
@login_required
@cliente_required
def notificacion_eliminar(notificacion_id):
    notif = _get_cliente_notificacion_or_404(notificacion_id)
    notif.is_deleted = True
    notif.updated_at = datetime.utcnow()
    db.session.commit()
    if _notif_wants_json():
        unread_count = (
            ClienteNotificacion.query
            .filter_by(cliente_id=current_user.id, is_read=False, is_deleted=False)
            .count()
        )
        return jsonify({"ok": True, "deleted_id": int(notif.id), "unread_count": int(unread_count or 0)})
    return redirect(url_for("clientes.notificaciones_list"))


_CLIENTE_VISIBLE_MATCH_STATUS = ('enviada', 'vista', 'seleccionada')
_MATCH_STATUS_LABELS = {
    'enviada': 'Enviada',
    'vista': 'Vista',
    'seleccionada': 'Seleccionada',
    'descartada': 'Descartada',
}


def _safe_location_summary(breakdown: dict) -> str:
    if not isinstance(breakdown, dict):
        return ''

    def _clean(v):
        return re.sub(r'\s+', ' ', str(v or '')).strip()

    def _tokens(v):
        raw = _clean(v).lower()
        found = re.findall(r'[a-z0-9áéíóúñ]{2,24}', raw)
        blocked = {'tokens', 'coinciden', 'rutas', 'ruta', 'ciudad', 'detectada', 'sin', 'datos'}
        keep = []
        for tok in found:
            if tok in blocked or tok in keep:
                continue
            keep.append(tok)
            if len(keep) >= 2:
                break
        return keep

    city = _clean(breakdown.get('city_detectada'))
    sector_tokens = _tokens(breakdown.get('tokens_match'))
    if city and sector_tokens:
        return f"{city} · Sectores cercanos: {', '.join(sector_tokens)}"
    if city:
        return city
    if sector_tokens:
        return f"Sectores cercanos: {', '.join(sector_tokens)}"
    return ''


def _candidate_public_payload(sc: SolicitudCandidata) -> dict:
    cand = getattr(sc, 'candidata', None)
    breakdown = sc.breakdown_snapshot if isinstance(sc.breakdown_snapshot, dict) else {}
    return {
        'sc': sc,
        'codigo': getattr(cand, 'codigo', None) or '(sin código)',
        'nombre_publico': getattr(cand, 'nombre_completo', None) or 'Sin nombre',
        'edad': getattr(cand, 'edad', None),
        'modalidad': getattr(cand, 'modalidad_trabajo_preferida', None),
        'match_score': int(sc.score_snapshot or 0),
        'status_label': _MATCH_STATUS_LABELS.get(sc.status, sc.status),
        'porque_bullets': client_bullets_from_breakdown(breakdown),
        'ubicacion_resumen': _safe_location_summary(breakdown),
    }


def _get_cliente_sc_or_404(solicitud_id: int, sc_id: int) -> tuple[Solicitud, SolicitudCandidata]:
    solicitud = _get_solicitud_cliente_or_404(solicitud_id)
    if SolicitudCandidata is None or Candidata is None:
        abort(404)
    sc = (
        SolicitudCandidata.query
        .join(Candidata, Candidata.fila == SolicitudCandidata.candidata_id)
        .filter(
            SolicitudCandidata.id == sc_id,
            SolicitudCandidata.solicitud_id == solicitud.id,
            SolicitudCandidata.status.in_(_CLIENTE_VISIBLE_MATCH_STATUS),
            candidatas_activas_filter(Candidata),
        )
        .first_or_404()
    )
    return solicitud, sc


@clientes_bp.route('/solicitudes/<int:solicitud_id>/candidatas')
@login_required
@cliente_required
def solicitud_candidatas(solicitud_id):
    solicitud = _get_solicitud_cliente_or_404(solicitud_id)
    if SolicitudCandidata is None or Candidata is None:
        abort(404)
    items = (
        SolicitudCandidata.query
        .join(Candidata, Candidata.fila == SolicitudCandidata.candidata_id)
        .filter(
            SolicitudCandidata.solicitud_id == solicitud.id,
            SolicitudCandidata.status.in_(_CLIENTE_VISIBLE_MATCH_STATUS),
            candidatas_activas_filter(Candidata),
        )
        .order_by(SolicitudCandidata.created_at.desc(), SolicitudCandidata.id.desc())
        .all()
    )
    items = [sc for sc in items if getattr(sc, "status", None) in _CLIENTE_VISIBLE_MATCH_STATUS]
    assigned_card = None
    if getattr(solicitud, "candidata_id", None):
        assigned_sc = None
        for sc in items:
            if int(getattr(sc, "candidata_id", 0) or 0) == int(solicitud.candidata_id):
                assigned_sc = sc
                break
        if (
            assigned_sc is None
            and getattr(solicitud, "candidata", None)
            and not candidata_esta_descalificada(solicitud.candidata)
        ):
            fallback_sc = SolicitudCandidata()
            fallback_sc.id = 0
            fallback_sc.candidata_id = solicitud.candidata_id
            fallback_sc.score_snapshot = 0
            fallback_sc.status = "seleccionada"
            fallback_sc.breakdown_snapshot = {}
            fallback_sc.candidata = solicitud.candidata
            assigned_sc = fallback_sc
        if assigned_sc is not None:
            assigned_card = _candidate_public_payload(assigned_sc)

    cards = [] if assigned_card else [_candidate_public_payload(sc) for sc in items]
    return render_template(
        'clientes/solicitud_candidatas.html',
        solicitud=solicitud,
        cards=cards,
        assigned_card=assigned_card,
    )


@clientes_bp.route('/solicitudes/<int:solicitud_id>/candidatas/<int:sc_id>')
@login_required
@cliente_required
def solicitud_candidata_detalle(solicitud_id, sc_id):
    solicitud, sc = _get_cliente_sc_or_404(solicitud_id, sc_id)

    if sc.status == 'enviada':
        sc.status = 'vista'
        db.session.commit()

    card = _candidate_public_payload(sc)
    return render_template(
        'clientes/solicitud_candidata_detalle.html',
        solicitud=solicitud,
        card=card,
    )


@clientes_bp.route('/solicitudes/<int:solicitud_id>/candidatas/<int:sc_id>/solicitar-entrevista', methods=['POST'])
@login_required
@cliente_required
def solicitar_entrevista_whatsapp(solicitud_id, sc_id):
    solicitud, sc = _get_cliente_sc_or_404(solicitud_id, sc_id)
    if candidata_esta_descalificada(getattr(sc, "candidata", None)):
        abort(403)
    sc.status = 'seleccionada'
    db.session.commit()

    cliente_nombre = (getattr(current_user, 'nombre_completo', None) or 'Cliente').strip()
    codigo_solicitud = getattr(solicitud, 'codigo_solicitud', None) or f"SOL-{solicitud.id}"
    codigo_candidata = getattr(sc.candidata, 'codigo', None) or '(sin código)'
    nombre_candidata = getattr(sc.candidata, 'nombre_completo', None) or 'Sin nombre'

    mensaje = f"Hola, quiero entrevistar a la candidata {nombre_candidata} ({codigo_candidata}). Solicitud {codigo_solicitud}."
    encoded = urllib.parse.quote(mensaje, safe="")
    agency_phone = "18094296892"
    wa_url = f"https://wa.me/{agency_phone}?text={encoded}"
    return redirect(wa_url)


@clientes_bp.route('/solicitudes/<int:solicitud_id>/candidatas/<int:sc_id>/descartar', methods=['POST'])
@login_required
@cliente_required
def descartar_candidata_enviada(solicitud_id, sc_id):
    solicitud, sc = _get_cliente_sc_or_404(solicitud_id, sc_id)
    sc.status = 'descartada'
    snapshot = sc.breakdown_snapshot if isinstance(sc.breakdown_snapshot, dict) else {}
    snapshot["client_action"] = "rechazada"
    snapshot["client_action_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    reason = (request.form.get("client_reason") or "").strip()
    if reason:
        snapshot["client_reason"] = reason[:500]
    sc.breakdown_snapshot = snapshot
    db.session.commit()
    flash('Candidata descartada.', 'info')
    return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))


# ─────────────────────────────────────────────────────────────
# Seguimiento (línea de tiempo)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/seguimiento')
@login_required
@cliente_required
def seguimiento_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    timeline = []
    timeline.append({
        'titulo': 'Solicitud creada',
        'detalle': f'Código {s.codigo_solicitud}',
        'fecha': s.fecha_solicitud
    })

    if getattr(s, 'candidata', None):
        timeline.append({
            'titulo': 'Candidata enviada',
            'detalle': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })

    for idx, r in enumerate(getattr(s, 'reemplazos', []) or [], start=1):
        if getattr(r, 'candidata_new', None):
            timeline.append({
                'titulo': f'Reemplazo #{idx}',
                'detalle': r.candidata_new.nombre_completo,
                'fecha': (getattr(r, 'fecha_inicio_reemplazo', None) or getattr(r, 'created_at', None))
            })

    if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
        timeline.append({
            'titulo': 'Solicitud cancelada',
            'detalle': getattr(s, 'motivo_cancelacion', ''),
            'fecha': s.fecha_cancelacion
        })

    if getattr(s, 'fecha_ultima_modificacion', None):
        timeline.append({
            'titulo': 'Actualizada',
            'detalle': 'Se registraron cambios en la solicitud.',
            'fecha': s.fecha_ultima_modificacion
        })

    timeline.sort(key=lambda x: x.get('fecha') or datetime.min)

    return render_template('clientes/solicitud_seguimiento.html', s=s, timeline=timeline)


# ─────────────────────────────────────────────────────────────
# Cancelar solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/cancelar', methods=['GET','POST'])
@login_required
@cliente_required
def cancelar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = ClienteCancelForm()
    if form.validate_on_submit():
        s.estado = 'cancelada'
        s.fecha_cancelacion = datetime.utcnow()
        s.motivo_cancelacion = form.motivo.data
        db.session.commit()
        flash('Solicitud marcada como cancelada (pendiente aprobación).', 'warning')
        return redirect(url_for('clientes.listar_solicitudes'))

    return render_template('clientes/solicitud_cancel.html', s=s, form=form)


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE: mostrar modal si no ha aceptado políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.before_app_request
def _show_policies_modal_once():
    WHITELIST = {
        'clientes.politicas',
        'clientes.aceptar_politicas',
        'clientes.rechazar_politicas',
        'clientes.login',
        'clientes.logout',
        'static'
    }

    if not current_user.is_authenticated:
        return None

    if getattr(current_user, 'role', 'cliente') != 'cliente':
        return None

    if bool(getattr(current_user, 'acepto_politicas', False)):
        return None

    g.show_policies_modal = False
    if not session.get('policies_modal_shown', False):
        g.show_policies_modal = True
        session['policies_modal_shown'] = True

    if request.method == 'POST' and request.endpoint not in WHITELIST:
        return redirect(url_for('clientes.politicas', next=request.url))

    return None


@clientes_bp.route('/politicas', methods=['GET'])
@login_required
def politicas():
    if getattr(current_user, 'role', 'cliente') != 'cliente':
        flash('Acceso no permitido.', 'warning')
        return redirect(url_for('clientes.dashboard'))
    return render_template('clientes/politicas.html')


@clientes_bp.route('/politicas/aceptar', methods=['POST'])
@login_required
def aceptar_politicas():
    next_url = request.args.get('next') or url_for('clientes.dashboard')
    if hasattr(current_user, 'acepto_politicas'):
        current_user.acepto_politicas = True
    if hasattr(current_user, 'fecha_acepto_politicas'):
        current_user.fecha_acepto_politicas = datetime.utcnow()
    db.session.commit()
    flash('Gracias por aceptar nuestras políticas.', 'success')
    return redirect(next_url if _is_safe_next(next_url) else url_for('clientes.dashboard'))


@clientes_bp.route('/politicas/rechazar', methods=['POST'])
@login_required
def rechazar_politicas():
    logout_user()
    flash('Debes aceptar las políticas para usar el portal.', 'warning')
    return redirect(url_for('clientes.login'))


# ─────────────────────────────────────────────────────────────
# COMPATIBILIDAD – TEST DEL CLIENTE (por SOLICITUD)
# ─────────────────────────────────────────────────────────────
from json import dumps, loads

PLANES_COMPATIBLES = {'premium', 'vip'}
COMPAT_TEST_VERSION = 'v2.0'
HORARIO_ORDER = {tok: idx for idx, (tok, _lbl) in enumerate(HORARIO_OPTIONS)}

_RITMOS = {'tranquilo', 'activo', 'muy_activo'}
_ESTILOS = {
    'paso_a_paso': 'necesita_instrucciones',
    'prefiere_iniciativa': 'toma_iniciativa',
    'necesita_instrucciones': 'necesita_instrucciones',
    'toma_iniciativa': 'toma_iniciativa',
}
_LEVELS = {'baja', 'media', 'alta'}


def _plan_permite_compat(solicitud: Solicitud) -> bool:
    plan = (getattr(solicitud, 'tipo_plan', '') or '').strip().lower()
    return plan in PLANES_COMPATIBLES


def _get_solicitud_cliente_or_404(solicitud_id: int) -> Solicitud:
    return Solicitud.query.filter_by(id=solicitud_id, cliente_id=current_user.id).first_or_404()


def _list_from_form(name: str):
    vals = request.form.getlist(name)
    return [v.strip() for v in vals if v and v.strip()]


def _norm_ritmo(v: Optional[str]):
    v = (v or '').strip().lower().replace(' ', '_')
    v = v.replace('muyactivo', 'muy_activo')
    return v if v in _RITMOS else None


def _norm_estilo(v: Optional[str]):
    v = (v or '').strip().lower().replace(' ', '_')
    return _ESTILOS.get(v)


def _norm_level(v: Optional[str]):
    v = (v or '').strip().lower()
    return v if v in _LEVELS else None


def _parse_int_1a5(v: Optional[str]):
    try:
        n = int(str(v).strip())
        return n if 1 <= n <= 5 else None
    except Exception:
        return None


def _save_compat_cliente(s: Solicitud, payload_dict: dict) -> str:
    payload_dict = payload_dict or {}

    if hasattr(s, 'compat_test_cliente_json'):
        try:
            s.compat_test_cliente_json = payload_dict
            if hasattr(s, 'compat_test_cliente_at'):
                s.compat_test_cliente_at = datetime.utcnow()
            if hasattr(s, 'compat_test_cliente_version'):
                s.compat_test_cliente_version = COMPAT_TEST_VERSION
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()
            db.session.commit()
            return 'db_json'
        except Exception:
            db.session.rollback()

    if hasattr(s, 'compat_test_cliente'):
        try:
            s.compat_test_cliente = dumps(payload_dict, ensure_ascii=False)
            if hasattr(s, 'compat_test_cliente_at'):
                s.compat_test_cliente_at = datetime.utcnow()
            if hasattr(s, 'compat_test_cliente_version'):
                s.compat_test_cliente_version = COMPAT_TEST_VERSION
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()
            db.session.commit()
            return 'db_text'
        except Exception:
            db.session.rollback()

    session.setdefault('compat_tests_cliente', {})
    session['compat_tests_cliente'][f"{current_user.id}:{s.id}"] = payload_dict
    return 'session'


def _load_compat_cliente(s: Solicitud) -> Optional[dict]:
    if hasattr(s, 'compat_test_cliente_json') and getattr(s, 'compat_test_cliente_json', None):
        return getattr(s, 'compat_test_cliente_json')

    if hasattr(s, 'compat_test_cliente') and getattr(s, 'compat_test_cliente', None):
        try:
            return loads(getattr(s, 'compat_test_cliente'))
        except Exception:
            return {"__raw__": str(getattr(s, 'compat_test_cliente'))}

    by_cliente = session.get('compat_tests_cliente', {})
    return by_cliente.get(f"{current_user.id}:{s.id}")


def _normalize_payload_from_form(s: Solicitud) -> dict:
    cliente_nombre = (getattr(current_user, 'nombre_completo', None) or getattr(current_user, 'username', '')).strip()
    cliente_codigo = (getattr(current_user, 'codigo', '') or '').strip()

    ritmo = _norm_ritmo(request.form.get('ritmo_hogar'))
    estilo = _norm_estilo(request.form.get('direccion_trabajo'))
    exp = _norm_level(request.form.get('experiencia_deseada'))
    puntualidad = _parse_int_1a5(request.form.get('puntualidad_1a5'))
    mascotas = normalize_mascotas_token(request.form.get('mascotas') or getattr(s, 'mascota', None))
    mascotas_importancia = normalize_mascotas_importancia(request.form.get('mascotas_importancia'), default='media')

    horario_raw = _list_from_form('horario_tokens[]')
    horario_tokens = sorted(normalize_horarios_tokens(horario_raw), key=lambda t: HORARIO_ORDER.get(t, 999))

    profile = {
        "cliente_nombre": cliente_nombre,
        "cliente_codigo": cliente_codigo,
        "solicitud_codigo": s.codigo_solicitud,
        "ciudad_sector": (request.form.get('ciudad_sector') or getattr(s, 'ciudad_sector', '') or '').strip(),
        "composicion_hogar": (request.form.get('composicion_hogar') or '').strip(),
        "prioridades": _list_from_form('prioridades[]'),
        "ritmo_hogar": ritmo,
        "puntualidad_1a5": puntualidad,
        "comunicacion": (request.form.get('comunicacion') or '').strip(),
        "direccion_trabajo": estilo,
        "experiencia_deseada": exp,
        "horario_tokens": horario_tokens,
        "horario_preferido": ", ".join(horario_tokens) if horario_tokens else (request.form.get('horario_preferido') or getattr(s, 'horario', '') or '').strip(),
        "no_negociables": _list_from_form('no_negociables[]'),
        "nota_cliente_test": (request.form.get('nota_cliente_test') or '').strip(),
        "ninos": int(getattr(s, 'ninos', 0) or 0),
        "mascota": mascotas,
        "mascotas": mascotas,
        "mascotas_importancia": mascotas_importancia,
    }
    payload = {
        "version": COMPAT_TEST_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
        "profile": profile,
    }
    return payload


@clientes_bp.route('/solicitudes/<int:solicitud_id>/compat/test', methods=['GET', 'POST'])
@login_required
@cliente_required
@politicas_requeridas
def compat_test_cliente(solicitud_id):
    s = _get_solicitud_cliente_or_404(solicitud_id)

    if not _plan_permite_compat(s):
        flash('Esta funcionalidad es exclusiva para planes Premium o VIP de esta solicitud.', 'warning')
        return redirect(url_for('clientes.detalle_solicitud', id=solicitud_id))

    if request.method == 'POST':
        payload = _normalize_payload_from_form(s)
        destino = _save_compat_cliente(s, payload)

        if getattr(s, 'candidata_id', None) and getattr(s, 'candidata', None):
            result = compute_match(s, s.candidata)
            ok = persist_result_to_solicitud(s, result)
            if ok:
                flash(f"Test guardado y compatibilidad recalculada ({result.get('score', 0)}%).", 'success')
            else:
                flash('Test guardado, pero no se pudo persistir el resultado de compatibilidad.', 'warning')
        else:
            flash('Test guardado correctamente.', 'success' if destino.startswith('db') else 'info')

        return redirect(url_for('clientes.detalle_solicitud', id=solicitud_id))

    initial_payload = _load_compat_cliente(s) or {}
    initial = initial_payload.get('profile') if isinstance(initial_payload, dict) else {}
    if not isinstance(initial, dict):
        initial = initial_payload if isinstance(initial_payload, dict) else {}
    raw_horario = initial.get('horario_tokens') or initial.get('horario_preferido') or getattr(s, 'horario', '')
    initial['horario_tokens'] = sorted(normalize_horarios_tokens(raw_horario), key=lambda t: HORARIO_ORDER.get(t, 999))
    initial['mascotas'] = normalize_mascotas_token(initial.get('mascotas') or initial.get('mascota') or getattr(s, 'mascota', None))
    initial['mascotas_importancia'] = normalize_mascotas_importancia(initial.get('mascotas_importancia'), default='media')
    return render_template(
        'clientes/compat_test_cliente.html',
        s=s,
        initial=initial,
        HORARIO_CHOICES=HORARIO_OPTIONS,
        MASCOTAS_CHOICES=MASCOTAS_CHOICES,
        MASCOTAS_IMPORTANCIA_CHOICES=MASCOTAS_IMPORTANCIA_CHOICES,
    )


@clientes_bp.route('/solicitudes/<int:solicitud_id>/compat/recalcular', methods=['POST'])
@login_required
@cliente_required
@politicas_requeridas
def compat_recalcular(solicitud_id):
    s = _get_solicitud_cliente_or_404(solicitud_id)

    if not _plan_permite_compat(s):
        flash('Funcionalidad exclusiva para planes Premium o VIP.', 'warning')
        return redirect(url_for('clientes.detalle_solicitud', id=solicitud_id))

    payload = _load_compat_cliente(s)
    if not payload:
        flash('Aún no hay un test de cliente para recalcular.', 'warning')
        return redirect(url_for('clientes.compat_test_cliente', solicitud_id=solicitud_id))

    if not getattr(s, 'candidata_id', None) or not getattr(s, 'candidata', None):
        flash('No hay candidata asignada todavía. No se puede calcular el match.', 'info')
        return redirect(url_for('clientes.detalle_solicitud', id=solicitud_id))

    result = compute_match(s, s.candidata)
    ok = persist_result_to_solicitud(s, result)
    if ok:
        flash(f"Compatibilidad recalculada: {result.get('score', 0)}%.", 'success')
    else:
        flash('No se pudo guardar el resultado del cálculo.', 'danger')

    return redirect(url_for('clientes.detalle_solicitud', id=solicitud_id))


@clientes_bp.route('/solicitudes/publica/<token>', methods=['GET', 'POST'])
def solicitud_publica(token):
    abort(404)

    from flask import current_app
    import re
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

    form = SolicitudPublicaForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        if hasattr(form, 'token'):
            form.token.data = token
        if hasattr(form, 'funciones'):
            form.funciones.data = form.funciones.data or []
        if hasattr(form, 'areas_comunes'):
            form.areas_comunes.data = form.areas_comunes.data or []
        if hasattr(form, 'edad_requerida'):
            form.edad_requerida.data = form.edad_requerida.data or []
        if hasattr(form, 'dos_pisos') and form.dos_pisos.data is None:
            form.dos_pisos.data = False
        if hasattr(form, 'pasaje_aporte') and form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = False

    if request.method == 'POST' and hasattr(form, 'hp'):
        if (form.hp.data or '').strip():
            abort(400)

    try:
        ser = URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"],
            salt="clientes-solicitud-publica"
        )
        payload = ser.loads(token, max_age=60 * 60 * 24 * 30)
        cliente_id = payload.get("cliente_id")
        codigo_token = (payload.get("codigo") or "").strip()
    except SignatureExpired:
        flash("Este enlace expiró. Pide a la agencia que te envíe uno nuevo.", "warning")
        return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)
    except BadSignature:
        flash("Enlace inválido. Verifica que sea el link correcto.", "danger")
        return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

    c = Cliente.query.filter_by(id=cliente_id).first()
    if not c or (c.codigo or '').strip() != codigo_token:
        flash("Enlace no válido para ningún cliente. Contacta a la agencia.", "danger")
        return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

    if form.validate_on_submit():
        if hasattr(form, 'token'):
            if (form.token.data or '') != token:
                flash("Token inválido.", "danger")
                return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

        def _norm_email(v):
            return (v or "").strip().lower()

        def _norm_text(v):
            s = (v or "").strip()
            s = re.sub(r"\s+", " ", s)
            return s.lower()

        if _norm_text(getattr(form, 'codigo_cliente', type('x',(object,),{'data':''})) .data) != _norm_text(c.codigo):
            flash("El código no coincide con este enlace.", "danger")
            return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

        if _norm_email(getattr(form, 'email_cliente', type('x',(object,),{'data':''})).data) != _norm_email(c.email):
            flash("El Gmail no coincide con ese código.", "danger")
            return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

        if _norm_text(getattr(form, 'nombre_cliente', type('x',(object,),{'data':''})).data) != _norm_text(c.nombre_completo):
            flash("El nombre no coincide con ese código.", "danger")
            return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)

        try:
            idx = Solicitud.query.filter_by(cliente_id=c.id).count()
            while True:
                codigo = f"{c.codigo}-{letra_por_indice(idx)}"
                existe = Solicitud.query.filter_by(codigo_solicitud=codigo).first()
                if not existe:
                    break
                idx += 1

            s = Solicitud(
                cliente_id=c.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=codigo
            )

            form.populate_obj(s)

            selected_funciones = _clean_list(getattr(form, 'funciones', type('x',(object,),{'data':[]})).data)
            funciones_otro_raw = getattr(getattr(form, 'funciones_otro', None), 'data', '') if hasattr(form, 'funciones_otro') else ''
            funciones_otro_clean = (funciones_otro_raw or '').strip() if 'otro' in selected_funciones else ''

            s.funciones = _map_funciones(selected_funciones, funciones_otro_clean)
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = funciones_otro_clean or None

            s.areas_comunes = _clean_list(
                getattr(form, 'areas_comunes', type('x',(object,),{'data':[]})).data
            ) or []

            s.edad_requerida = _map_edad_choices(
                getattr(form, 'edad_requerida', type('x',(object,),{'data':[]})).data,
                getattr(getattr(form, 'edad_requerida', None), 'choices', []) if hasattr(form, 'edad_requerida') else [],
                getattr(getattr(form, 'edad_otro', None), 'data', '') if hasattr(form, 'edad_otro') else ''
            ) or []

            s.tipo_lugar = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') if hasattr(form, 'tipo_lugar_otro') else ''
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None

            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()

            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(getattr(form, 'sueldo', type('x',(object,),{'data':None})).data)

            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.add(s)

            c.total_solicitudes = (c.total_solicitudes or 0) + 1
            c.fecha_ultima_solicitud = datetime.utcnow()
            c.fecha_ultima_actividad = datetime.utcnow()

            db.session.commit()
            flash(f"Solicitud {codigo} enviada correctamente.", "success")
            return redirect(url_for('clientes.solicitud_publica', token=token))

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("ERROR guardando solicitud pública")
            flash(f"No se pudo enviar la solicitud. Error: {str(e)}", "danger")

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template('clientes/solicitud_form_publica.html', form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# Helpers: normalización de tags (Habilidades y fortalezas)
# ─────────────────────────────────────────────────────────────
def _to_tags_text(v) -> str:
    if v is None:
        return ''

    if isinstance(v, (list, tuple, set)):
        parts = [str(x).strip() for x in v if str(x).strip()]
        return ', '.join(parts)

    if isinstance(v, dict):
        parts = [str(x).strip() for x in v.values() if str(x).strip()]
        return ', '.join(parts)

    s = str(v)
    s = s.replace('\n', ',').replace(';', ',').replace('|', ',')
    parts = [p.strip() for p in s.split(',') if p.strip()]
    return ', '.join(parts)


# ─────────────────────────────────────────────────────────────
# Banco de domésticas (Portal Clientes)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/domesticas/disponibles', methods=['GET'], endpoint='domesticas_list')
@clientes_bp.route('/domesticas', methods=['GET'])
@login_required
@cliente_required
@politicas_requeridas
@banco_domesticas_required
def banco_domesticas():
    if Candidata is None or CandidataWeb is None:
        abort(404)

    page = request.args.get('page', 1, type=int)
    page = max(page, 1)
    per_page = request.args.get('per_page', 12, type=int)
    per_page = per_page if per_page in (6, 12, 24, 48) else 12

    q = (request.args.get('q') or '').strip()[:120]
    ciudad = (request.args.get('ciudad') or '').strip()[:120]
    modalidad = (request.args.get('modalidad') or '').strip()[:120]

    query = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(candidatas_activas_filter(Candidata))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == 'disponible')
        .order_by(
            db.case((CandidataWeb.orden_lista.is_(None), 1), else_=0).asc(),
            CandidataWeb.orden_lista.asc(),
            Candidata.nombre_completo.asc()
        )
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.numero_telefono.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    if ciudad:
        query = query.filter(getattr(CandidataWeb, 'ciudad_publica', db.literal('')).ilike(f"%{ciudad}%"))

    if modalidad:
        query = query.filter(getattr(CandidataWeb, 'modalidad_publica', db.literal('')).ilike(f"%{modalidad}%"))

    total = query.count()
    items = (
        query
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )

    pages = (total + per_page - 1) // per_page if per_page else 1
    pages = max(1, pages)
    has_prev = page > 1
    has_next = page < pages

    domesticas = []
    for cand, ficha in (items or []):
        foto_url = (getattr(ficha, 'foto_url_publica', None) or getattr(ficha, 'foto', None) or '').strip()
        if not foto_url:
            try:
                if getattr(cand, 'foto_perfil', None):
                    foto_url = url_for('clientes.domestica_foto_perfil', fila=cand.fila)
            except Exception:
                foto_url = ''

        domesticas.append({
            'foto': foto_url or None,
            'nombre': (getattr(ficha, 'nombre_publico', None) or getattr(cand, 'nombre_completo', None) or '').strip(),
            'edad': (getattr(ficha, 'edad_publica', None) or getattr(cand, 'edad', None) or '').strip(),
            'codigo': (getattr(cand, 'codigo', None) or '').strip() or None,
            'modalidad': (getattr(ficha, 'modalidad_publica', None) or getattr(cand, 'modalidad', None) or '').strip() or None,
            'ciudad': (getattr(ficha, 'ciudad_publica', None) or getattr(cand, 'ciudad', None) or '').strip() or None,
            'sector': (getattr(ficha, 'sector_publico', None) or getattr(cand, 'sector', None) or '').strip() or None,
            'tags': _to_tags_text(
                getattr(ficha, 'tags_publicos', None)
                or getattr(ficha, 'fortalezas_publicas', None)
                or getattr(ficha, 'habilidades_publicas', None)
                or getattr(cand, 'compat_fortalezas', None)
                or getattr(cand, 'tags', None)
            ),
            'fila': int(getattr(cand, 'fila', 0) or 0),
        })

    try:
        base_opts = (
            db.session.query(CandidataWeb)
            .join(Candidata, Candidata.fila == CandidataWeb.candidata_id)
            .filter(CandidataWeb.visible.is_(True))
            .filter(CandidataWeb.estado_publico == 'disponible')
            .filter(candidatas_activas_filter(Candidata))
        )
        ciudades_disponibles = sorted({
            (x.ciudad_publica or '').strip()
            for x in base_opts
            if (x.ciudad_publica or '').strip()
        })
        modalidades_disponibles = sorted({
            (x.modalidad_publica or '').strip()
            for x in base_opts
            if (x.modalidad_publica or '').strip()
        })
    except Exception:
        ciudades_disponibles = []
        modalidades_disponibles = []

    return render_template(
        'clientes/domesticas_list.html',
        resultados=items,
        q=q,
        ciudad=ciudad,
        modalidad=modalidad,
        page=page,
        per_page=per_page,
        total=total,
        pages=pages,
        has_prev=has_prev,
        has_next=has_next,
        prev_num=page-1 if has_prev else 1,
        next_num=page+1 if has_next else pages,
        domesticas=domesticas,
        ciudades_disponibles=ciudades_disponibles,
        modalidades_disponibles=modalidades_disponibles,
    )


@clientes_bp.route('/domesticas/<int:fila>', methods=['GET'])
@login_required
@cliente_required
@politicas_requeridas
@banco_domesticas_required
def domestica_detalle(fila: int):
    if Candidata is None or CandidataWeb is None:
        abort(404)

    cand = Candidata.query.filter_by(fila=fila).first_or_404()
    if candidata_esta_descalificada(cand):
        abort(404)
    ficha = CandidataWeb.query.filter_by(candidata_id=cand.fila).first()

    if not ficha or not getattr(ficha, 'visible', False) or getattr(ficha, 'estado_publico', '') != 'disponible':
        abort(404)

    raw_tags = (
        getattr(ficha, 'tags_publicos', None)
        or getattr(ficha, 'fortalezas_publicas', None)
        or getattr(ficha, 'habilidades_publicas', None)
        or getattr(cand, 'compat_fortalezas', None)
        or getattr(cand, 'tags', None)
    )
    tags_txt = _to_tags_text(raw_tags)

    foto_url = (getattr(ficha, 'foto_url_publica', None) or getattr(ficha, 'foto', None) or '').strip()
    if not foto_url:
        try:
            if getattr(cand, 'foto_perfil', None):
                foto_url = url_for('clientes.domestica_foto_perfil', fila=cand.fila)
        except Exception:
            foto_url = ''

    disponible_inmediato = bool(getattr(ficha, 'disponible_inmediato', False))
    disponible_msg = (getattr(ficha, 'disponible_inmediato_msg', None) or '').strip() or None

    candidata = {
        'foto': foto_url or None,
        'nombre': (getattr(ficha, 'nombre_publico', None) or getattr(cand, 'nombre_completo', None) or '').strip(),
        'edad': (getattr(ficha, 'edad_publica', None) or getattr(cand, 'edad', None) or '').strip(),
        'frase_destacada': (getattr(ficha, 'frase_destacada', None) or '').strip() or None,
        'codigo': (getattr(cand, 'codigo', None) or '').strip() or None,
        'tipo_servicio': (getattr(ficha, 'tipo_servicio_publico', None) or '').strip() or None,
        'disponible_inmediato': disponible_inmediato,
        'disponible_inmediato_msg': disponible_msg,
        'ciudad': (getattr(ficha, 'ciudad_publica', None) or getattr(cand, 'ciudad', None) or '').strip() or None,
        'sector': (getattr(ficha, 'sector_publico', None) or getattr(cand, 'sector', None) or '').strip() or None,
        'modalidad': (getattr(ficha, 'modalidad_publica', None) or getattr(cand, 'modalidad', None) or '').strip() or None,
        'anos_experiencia': (getattr(ficha, 'anos_experiencia_publicos', None) or getattr(cand, 'anos_experiencia', None) or '').strip() or None,
        'experiencia': (getattr(ficha, 'experiencia_resumen', None) or getattr(cand, 'experiencia', None) or '').strip() or None,
        'experiencia_detallada': (getattr(ficha, 'experiencia_detallada', None) or '').strip() or None,
        'tags': tags_txt,
        'sueldo': (getattr(ficha, 'sueldo_publico', None) or '').strip() or None,
        'sueldo_desde': (getattr(ficha, 'sueldo_desde', None) or '').strip() or None,
        'sueldo_hasta': (getattr(ficha, 'sueldo_hasta', None) or '').strip() or None,
    }

    return render_template(
        'clientes/domesticas_detail.html',
        cand=cand,
        ficha=ficha,
        candidata=candidata,
    )


@clientes_bp.route('/domesticas/<int:fila>/foto_perfil', methods=['GET'])
@login_required
@cliente_required
@banco_domesticas_required
def domestica_foto_perfil(fila: int):
    if Candidata is None or CandidataWeb is None:
        abort(404)

    from io import BytesIO
    import imghdr

    cand = Candidata.query.filter_by(fila=fila).first_or_404()
    if candidata_esta_descalificada(cand):
        abort(404)
    ficha = CandidataWeb.query.filter_by(candidata_id=cand.fila).first()
    if not ficha or not bool(getattr(ficha, 'visible', False)) or (getattr(ficha, 'estado_publico', '') != 'disponible'):
        abort(404)

    blob = getattr(cand, 'foto_perfil', None)
    if not blob:
        abort(404)

    kind = imghdr.what(None, h=blob)
    if kind == 'jpeg':
        mimetype, ext = 'image/jpeg', 'jpg'
    elif kind == 'png':
        mimetype, ext = 'image/png', 'png'
    elif kind == 'gif':
        mimetype, ext = 'image/gif', 'gif'
    elif kind == 'webp':
        mimetype, ext = 'image/webp', 'webp'
    else:
        mimetype, ext = 'application/octet-stream', 'bin'

    response = send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=False,
        download_name=f"candidata_{fila}_perfil.{ext}",
        max_age=3600,
    )
    response.headers['Cache-Control'] = 'private, max-age=3600'
    response.headers['Pragma'] = 'private'
    return response
