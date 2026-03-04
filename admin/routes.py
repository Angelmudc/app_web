# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, flash, request, jsonify, abort, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from sqlalchemy import or_, func, cast, desc
from sqlalchemy.types import Numeric
from sqlalchemy.orm import joinedload  # ➜ para evitar N+1 en copiar_solicitudes
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from functools import wraps  # si otros decoradores locales lo usan

from config_app import db, USUARIOS, cache
from models import Cliente, Solicitud, Candidata, Reemplazo, TareaCliente
from admin.forms import (
    AdminClienteForm,
    AdminSolicitudForm,
    AdminPagoForm,
    AdminReemplazoForm,
    AdminGestionPlanForm,
    AdminReemplazoFinForm,  # 🔹 NUEVO FORM PARA FINALIZAR REEMPLAZO
)
from utils import letra_por_indice

from . import admin_bp
from .decorators import admin_required, staff_required

from clientes.routes import generar_token_publico_cliente

# =============================================================================
#                                AUTH
# =============================================================================
class AdminUser:
    """Wrapper mínimo para flask-login basado en USUARIOS del config."""

    def __init__(self, username: str):
        self.id = username
        self.role = USUARIOS[username]["role"]

    # Flask-Login interface mínima
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)



# ─────────────────────────────────────────────────────────────
# 🔒 Aislamiento real de sesión ADMIN vs CLIENTE + Rate-limit admin
# ─────────────────────────────────────────────────────────────
# Marcador de sesión: si no existe, NO se permite navegar en /admin/*
_ADMIN_SESSION_MARKER = "is_admin_session"

# Rate-limit global para acciones ADMIN (POST/PUT/PATCH/DELETE)
# Configurable por env:
#   ADMIN_ACTION_MAX=80   (acciones por ventana)
#   ADMIN_ACTION_WINDOW=60 (segundos)
#   ADMIN_ACTION_LOCK=120  (segundos bloqueado si se pasa)
_ADMIN_ACTION_MAX = int((os.getenv("ADMIN_ACTION_MAX") or "80").strip() or 80)
_ADMIN_ACTION_WINDOW = int((os.getenv("ADMIN_ACTION_WINDOW") or "60").strip() or 60)
_ADMIN_ACTION_LOCK = int((os.getenv("ADMIN_ACTION_LOCK") or "120").strip() or 120)
_ADMIN_ACTION_KEY_PREFIX = "admin_action"


def _admin_action_limits(bucket: str = "default"):
    """Devuelve (max, window, lock) por bucket."""
    b = (bucket or "default").strip().lower()

    mx = _ADMIN_ACTION_MAX
    win = _ADMIN_ACTION_WINDOW
    lock = _ADMIN_ACTION_LOCK

    try:
        if b == "pagos":
            mx = int((os.getenv("ADMIN_ACTION_MAX_PAGOS") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_PAGOS") or str(win)).strip())
        elif b == "solicitudes":
            mx = int((os.getenv("ADMIN_ACTION_MAX_SOL") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_SOL") or str(win)).strip())
        elif b == "reemplazos":
            mx = int((os.getenv("ADMIN_ACTION_MAX_REEMP") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_REEMP") or str(win)).strip())
        elif b == "delete":
            mx = int((os.getenv("ADMIN_ACTION_MAX_DEL") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_DEL") or str(win)).strip())
    except Exception:
        pass

    try:
        lock = int((os.getenv("ADMIN_ACTION_LOCK") or str(lock)).strip())
    except Exception:
        lock = _ADMIN_ACTION_LOCK

    return mx, win, lock


def _admin_action_keys(usuario_norm: str, bucket: str = "default"):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    base = f"{_ADMIN_ACTION_KEY_PREFIX}:{ip}:{u}:{b}"
    return {
        "count": f"{base}:count",
        "lock": f"{base}:lock",
    }


def _sess_action_key(usuario_norm: str, bucket: str = "default") -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    return f"admin_action:{ip}:{u}:{b}"


def _session_action_is_locked(usuario_norm: str, bucket: str = "default") -> bool:
    data = session.get(_sess_action_key(usuario_norm, bucket)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return False
    try:
        return datetime.utcnow().timestamp() < float(locked_until)
    except Exception:
        return False


def _session_action_register(usuario_norm: str, bucket: str, mx: int, win: int, lock: int) -> int:
    key = _sess_action_key(usuario_norm, bucket)
    data = session.get(key) or {}

    now_ts = datetime.utcnow().timestamp()
    window_start = float(data.get("window_start") or 0.0)

    if not window_start or (now_ts - window_start) > win:
        data["window_start"] = now_ts
        data["count"] = 0
        data.pop("locked_until", None)

    if data.get("locked_until"):
        session[key] = data
        return int(data.get("count") or 0)

    data["count"] = int(data.get("count") or 0) + 1

    if int(data["count"]) > mx:
        data["locked_until"] = now_ts + lock

    session[key] = data
    return int(data.get("count") or 0)


def _session_action_lock_left_seconds(usuario_norm: str, bucket: str = "default") -> int:
    data = session.get(_sess_action_key(usuario_norm, bucket)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return 0
    try:
        left = int(float(locked_until) - datetime.utcnow().timestamp())
        return max(0, left)
    except Exception:
        return 0


def _admin_action_is_locked(usuario_norm: str, bucket: str = "default") -> bool:
    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            return _session_action_is_locked(usuario_norm, bucket)
    return _session_action_is_locked(usuario_norm, bucket)


def _admin_action_lock_left_seconds(usuario_norm: str, bucket: str = "default") -> int:
    mx, win, lock = _admin_action_limits(bucket)

    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            left = cache.get(keys["lock"])
            try:
                return max(0, int(left or 0))
            except Exception:
                return lock
        except Exception:
            return _session_action_lock_left_seconds(usuario_norm, bucket)

    return _session_action_lock_left_seconds(usuario_norm, bucket)


def _admin_action_register(usuario_norm: str, bucket: str = "default") -> int:
    mx, win, lock = _admin_action_limits(bucket)

    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            if cache.get(keys["lock"]):
                return int(cache.get(keys["count"]) or 0)

            n = int(cache.get(keys["count"]) or 0) + 1
            cache.set(keys["count"], n, timeout=win)

            if n > mx:
                cache.set(keys["lock"], lock, timeout=lock)
            return n
        except Exception:
            return _session_action_register(usuario_norm, bucket, mx=mx, win=win, lock=lock)

    return _session_action_register(usuario_norm, bucket, mx=mx, win=win, lock=lock)


#
# ─────────────────────────────────────────────────────────────
# ✅ Decorador CANÓNICO: rate-limit por ruta (admin_action_limit)
# IMPORTANTE: debe existir ANTES de cualquier uso @admin_action_limit(...)
# ─────────────────────────────────────────────────────────────

def admin_action_limit(bucket: str = "default", max_actions: int | None = None, window_sec: int | None = None):
    """Rate-limit por IP + usuario para rutas ADMIN.

    - bucket: agrupa acciones (ej: 'pagos', 'solicitudes', 'delete', 'tareas')
    - max_actions: override del máximo en la ventana
    - window_sec: override del tamaño de la ventana (segundos)

    Usa cache (Flask-Caching) si está disponible; si no, usa sesión como fallback.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                # usuario normalizado
                try:
                    uname = (current_user.get_id() if current_user else "") or ""
                except Exception:
                    uname = getattr(current_user, "id", "") or ""

                usuario_norm = str(uname).strip().lower()[:64]
                b = (bucket or "default").strip().lower()[:32]

                # límites base por bucket
                mx, win, lock = _admin_action_limits(b)

                # overrides por ruta
                if max_actions is not None:
                    try:
                        mx = int(max_actions)
                    except Exception:
                        pass
                if window_sec is not None:
                    try:
                        win = int(window_sec)
                    except Exception:
                        pass

                # nunca permitir valores absurdos
                try:
                    mx = max(1, min(int(mx), 5000))
                except Exception:
                    mx = 80
                try:
                    win = max(1, min(int(win), 3600))
                except Exception:
                    win = 60
                try:
                    lock = max(5, min(int(lock), 3600))
                except Exception:
                    lock = 120

                # --- con CACHE (preferido) ---
                if _cache_ok():
                    keys = _admin_action_keys(usuario_norm, bucket=b)
                    try:
                        # locked?
                        left = cache.get(keys["lock"])
                        if left:
                            try:
                                left_i = int(left)
                            except Exception:
                                left_i = lock

                            wants_json = False
                            try:
                                wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                            except Exception:
                                wants_json = False

                            if wants_json:
                                return jsonify({
                                    "ok": False,
                                    "error": "rate_limited",
                                    "bucket": b,
                                    "retry_after_sec": max(10, left_i),
                                }), 429

                            flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, left_i)} segundos.", "warning")
                            return redirect(url_for("admin.listar_clientes"))

                        # count
                        n = int(cache.get(keys["count"]) or 0) + 1
                        cache.set(keys["count"], n, timeout=win)

                        if n > mx:
                            cache.set(keys["lock"], lock, timeout=lock)

                            wants_json = False
                            try:
                                wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                            except Exception:
                                wants_json = False

                            if wants_json:
                                return jsonify({
                                    "ok": False,
                                    "error": "rate_limited",
                                    "bucket": b,
                                    "retry_after_sec": max(10, lock),
                                }), 429

                            flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, lock)} segundos.", "warning")
                            return redirect(url_for("admin.listar_clientes"))

                        return fn(*args, **kwargs)
                    except Exception:
                        # si cache falla, cae a sesión
                        pass

                # --- fallback por SESIÓN ---
                try:
                    key = _sess_action_key(usuario_norm, bucket=b)
                    data = session.get(key) or {}

                    now_ts = datetime.utcnow().timestamp()
                    window_start = float(data.get("window_start") or 0.0)

                    if not window_start or (now_ts - window_start) > win:
                        data["window_start"] = now_ts
                        data["count"] = 0
                        data.pop("locked_until", None)

                    locked_until = data.get("locked_until")
                    if locked_until:
                        try:
                            left = int(float(locked_until) - now_ts)
                        except Exception:
                            left = lock
                        left = max(0, left)

                        wants_json = False
                        try:
                            wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                        except Exception:
                            wants_json = False

                        if wants_json:
                            return jsonify({
                                "ok": False,
                                "error": "rate_limited",
                                "bucket": b,
                                "retry_after_sec": max(10, left or 10),
                            }), 429

                        flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, left or 10)} segundos.", "warning")
                        return redirect(url_for("admin.listar_clientes"))

                    data["count"] = int(data.get("count") or 0) + 1

                    if int(data["count"]) > mx:
                        data["locked_until"] = now_ts + lock
                        session[key] = data

                        wants_json = False
                        try:
                            wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                        except Exception:
                            wants_json = False

                        if wants_json:
                            return jsonify({
                                "ok": False,
                                "error": "rate_limited",
                                "bucket": b,
                                "retry_after_sec": max(10, lock),
                            }), 429

                        flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, lock)} segundos.", "warning")
                        return redirect(url_for("admin.listar_clientes"))

                    session[key] = data
                except Exception:
                    # si todo falla, no rompemos el flujo
                    pass

            except Exception:
                # si algo raro pasa, no rompemos la ruta
                pass

            return fn(*args, **kwargs)
        return wrapper
    return deco

@admin_bp.before_request
def _admin_guard_and_rate_limit():
    """
    1) Aislamiento real de sesión Admin:
       - Solo permite navegar por /admin/* si:
         - current_user es AdminUser
         - session[_ADMIN_SESSION_MARKER] == True
       - Si no, logout y manda a /admin/login

    2) Rate-limit global para acciones sensibles:
       - Solo aplica a métodos mutadores: POST/PUT/PATCH/DELETE
       - Excluye: login/logout/ping/solicitudes_live
    """
    try:
        # Endpoint actual
        ep = (request.endpoint or "").strip()

        # Permitir siempre rutas públicas del admin blueprint (login)
        # y utilidades (logout, ping, live)
        allow_eps = {
            "admin.login",
            "admin.logout",
            "admin.admin_ping",
            "admin.solicitudes_live",
        }
        if ep in allow_eps:
            return None

        # Si NO hay usuario logueado, que flask-login maneje @login_required más abajo
        # (pero aquí evitamos que un cliente autenticado con otra sesión se cuele).
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return None

        # Debe ser identidad ADMIN válida + sesión marcada como admin.
        # OJO: NO usamos isinstance(AdminUser) porque flask-login reconstruye el usuario
        # vía user_loader y puede no devolver la misma clase.
        def _is_admin_identity() -> bool:
            try:
                uid = None
                try:
                    uid = current_user.get_id()
                except Exception:
                    uid = getattr(current_user, "id", None)

                if uid is None:
                    return False

                uid_str = str(uid).strip()
                if not uid_str:
                    return False

                # Match exacto o case-insensitive contra USUARIOS
                if uid_str in (USUARIOS or {}):
                    return True

                uid_norm = uid_str.lower()
                for k in (USUARIOS or {}).keys():
                    if str(k).strip().lower() == uid_norm:
                        return True

                return False
            except Exception:
                return False

        is_admin_user = _is_admin_identity()
        is_admin_session = bool(session.get(_ADMIN_SESSION_MARKER))

        if (not is_admin_user) or (not is_admin_session):
            try:
                logout_user()
            except Exception:
                pass
            try:
                session.clear()
            except Exception:
                pass
            return redirect(url_for("admin.login"))

        # Rate-limit solo para acciones que cambian cosas
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            usuario_norm = ""
            try:
                usuario_norm = (current_user.get_id() or "").strip().lower()
            except Exception:
                usuario_norm = ""

            # Bucket automático según endpoint/path/método
            ep_l = (ep or "").lower()
            path_l = (request.path or "").lower()
            if request.method == "DELETE" or "eliminar" in ep_l or "/eliminar" in path_l:
                bucket = "delete"
            elif "pago" in ep_l or "/pago" in path_l or "abono" in ep_l or "/abono" in path_l:
                bucket = "pagos"
            elif "reemplazo" in ep_l or "/reemplazo" in path_l:
                bucket = "reemplazos"
            elif "solicitud" in ep_l or "/solicitud" in path_l:
                bucket = "solicitudes"
            elif "tarea" in ep_l or "/tarea" in path_l:
                bucket = "tareas"
            else:
                bucket = "default"

            if _admin_action_is_locked(usuario_norm, bucket=bucket):
                left = _admin_action_lock_left_seconds(usuario_norm, bucket=bucket)
                # Mensaje corto y claro
                flash(f"Demasiadas acciones seguidas. Intenta de nuevo en {max(10, left)} segundos.", "danger")
                return redirect(url_for("admin.listar_clientes"))

            _admin_action_register(usuario_norm, bucket=bucket)

    except Exception:
        # Nunca rompemos el request por seguridad
        return None

    return None



#
# —— Anti fuerza-bruta (cache) por IP + usuario ——
# Nota: usamos `cache` (Flask-Caching) para que el lock sea real incluso si el usuario cambia de navegador.
# Fallback seguro: si `cache` no está disponible o falla, usamos sesión (NO rompe el login).
# Configurable por env: ADMIN_LOGIN_MAX_INTENTOS y ADMIN_LOGIN_LOCK_MINUTOS.
_ADMIN_LOGIN_MAX_INTENTOS = int((os.getenv("ADMIN_LOGIN_MAX_INTENTOS") or "6").strip() or 6)
_ADMIN_LOGIN_LOCK_MINUTOS = int((os.getenv("ADMIN_LOGIN_LOCK_MINUTOS") or "10").strip() or 10)
_ADMIN_LOGIN_KEY_PREFIX   = "admin_login"


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


def _admin_login_max_intentos() -> int:
    # permite cambiar en runtime si hace falta
    try:
        return int((os.getenv("ADMIN_LOGIN_MAX_INTENTOS") or str(_ADMIN_LOGIN_MAX_INTENTOS)).strip())
    except Exception:
        return _ADMIN_LOGIN_MAX_INTENTOS


def _admin_login_lock_minutos() -> int:
    try:
        return int((os.getenv("ADMIN_LOGIN_LOCK_MINUTOS") or str(_ADMIN_LOGIN_LOCK_MINUTOS)).strip())
    except Exception:
        return _ADMIN_LOGIN_LOCK_MINUTOS


def _admin_login_keys(usuario_norm: str):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    base = f"{_ADMIN_LOGIN_KEY_PREFIX}:{ip}:{u}"
    return {
        "fail": f"{base}:fail",
        "lock": f"{base}:lock",
    }


def _cache_ok() -> bool:
    """Retorna True si el cache está disponible y operativo."""
    try:
        # Un get simple no debería explotar; si explota, asumimos cache no usable
        _ = cache.get("__ping__")
        return True
    except Exception:
        return False


def _sess_key(usuario_norm: str) -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    return f"admin_login_fail:{ip}:{u}"


def _session_is_locked(usuario_norm: str) -> bool:
    data = session.get(_sess_key(usuario_norm)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return False
    try:
        return datetime.utcnow().timestamp() < float(locked_until)
    except Exception:
        return False


def _session_fail_count(usuario_norm: str) -> int:
    data = session.get(_sess_key(usuario_norm)) or {}
    try:
        return int(data.get("tries") or 0)
    except Exception:
        return 0


def _session_lock(usuario_norm: str):
    key = _sess_key(usuario_norm)
    data = session.get(key) or {}
    data["locked_until"] = datetime.utcnow().timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data


def _session_register_fail(usuario_norm: str) -> int:
    key = _sess_key(usuario_norm)
    data = session.get(key) or {}
    tries = int(data.get("tries") or 0) + 1
    data["tries"] = tries
    # lock cuando llega al máximo
    if tries >= _admin_login_max_intentos():
        data["locked_until"] = datetime.utcnow().timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data
    return tries


def _session_reset_fail(usuario_norm: str):
    try:
        session.pop(_sess_key(usuario_norm), None)
    except Exception:
        pass


def _admin_is_locked(usuario_norm: str) -> bool:
    """Chequea lock (cache si sirve, si no sesión)."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            # si falla cache en runtime, cae a sesión
            return _session_is_locked(usuario_norm)
    return _session_is_locked(usuario_norm)


def _admin_lock(usuario_norm: str):
    """Activa lock (cache si sirve, si no sesión)."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            cache.set(keys["lock"], True, timeout=_admin_login_lock_minutos() * 60)
            return
        except Exception:
            pass
    _session_lock(usuario_norm)


def _admin_fail_count(usuario_norm: str) -> int:
    """Conteo de fallos (cache si sirve, si no sesión)."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            return int(cache.get(keys["fail"]) or 0)
        except Exception:
            return _session_fail_count(usuario_norm)
    return _session_fail_count(usuario_norm)


def _admin_register_fail(usuario_norm: str) -> int:
    """Registra intento fallido y bloquea al llegar al máximo."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        n = _admin_fail_count(usuario_norm) + 1
        try:
            cache.set(keys["fail"], n, timeout=_admin_login_lock_minutos() * 60)
        except Exception:
            # cae a sesión si cache falla
            return _session_register_fail(usuario_norm)

        if n >= _admin_login_max_intentos():
            _admin_lock(usuario_norm)
        return n

    return _session_register_fail(usuario_norm)


def _admin_reset_fail(usuario_norm: str):
    """Limpia contadores y locks."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            cache.delete(keys["fail"])
            cache.delete(keys["lock"])
        except Exception:
            pass
    _session_reset_fail(usuario_norm)


def _clear_security_layer_lock_admin(endpoint: str = "/admin/login", usuario: str = ""):
    """Limpia el lock global (utils/security_layer.py) si está registrado.
    Soporta limpiar por IP + endpoint + usuario.
    """
    try:
        clear_fn = current_app.extensions.get("clear_login_attempts")
        if callable(clear_fn):
            ip = _client_ip()
            ep = (endpoint or "/admin/login").strip() or "/admin/login"
            uname = (usuario or "").strip()
            try:
                if uname:
                    clear_fn(ip, ep, uname)
                else:
                    clear_fn(ip, ep)
            except TypeError:
                clear_fn(ip)
    except Exception:
        pass


def _is_safe_next(target: str) -> bool:
    """Permite solo redirects internos (sin dominio externo)."""
    if not target:
        return False
    try:
        from urllib.parse import urlparse
        ref = urlparse(request.host_url)
        test = urlparse(target)
        if not test.netloc and test.path.startswith("/"):
            return True
        return (test.scheme, test.netloc) == (ref.scheme, ref.netloc)
    except Exception:
        return False


def _safe_next_url(fallback: str) -> str:
    nxt = (request.args.get("next") or request.form.get("next") or "").strip()
    return nxt if _is_safe_next(nxt) else fallback


def _reset_inicio_seguimiento_si_reactiva(s, now: datetime):
    """Si una solicitud se (re)activa para seguimiento, reinicia `fecha_inicio_seguimiento`.

    Esto evita que solicitudes viejas “arrastren” días viejos al reactivarlas.

    Nota: esta función es defensiva (solo actúa si el modelo tiene el atributo).
    """
    if not hasattr(s, 'fecha_inicio_seguimiento'):
        return

    estado = (getattr(s, 'estado', '') or '').strip().lower()

    # Estados que cuentan como "seguimiento"
    tracking_states = {'proceso', 'activa', 'reemplazo'}

    if estado in tracking_states:
        # Si se llama al (re)activar, queremos resetear el inicio del seguimiento.
        s.fecha_inicio_seguimiento = now


def build_resumen_cliente_solicitud(s: Solicitud) -> str:
    """
    Arma un resumen limpio y entendible de la solicitud para compartir con el cliente.
    Formato pensado para WhatsApp / correo: con emojis, espacios y todo organizado.
    """
    # Para mapear funciones (códigos -> etiquetas legibles)
    try:
        form_tmp = AdminSolicitudForm()
        FUNCIONES_LABELS = {code: label for code, label in (getattr(form_tmp, "funciones", None).choices or [])}
    except Exception:
        FUNCIONES_LABELS = {}

    # Campos base
    codigo        = _s(getattr(s, 'codigo_solicitud', None))
    ciudad_sector = _s(getattr(s, 'ciudad_sector', None))
    rutas         = _s(getattr(s, 'rutas_cercanas', None))
    modalidad     = _s(getattr(s, 'modalidad_trabajo', None))
    edad_req_raw  = getattr(s, 'edad_requerida', None)
    experiencia   = _s(getattr(s, 'experiencia', None))
    horario       = _s(getattr(s, 'horario', None))
    nota_cli      = _s(getattr(s, 'nota_cliente', None))

    # Edad requerida (suele estar como lista de labels)
    edad_list = _as_list(edad_req_raw)
    edad_txt  = ", ".join(edad_list) if edad_list else ""

    # Funciones (códigos -> etiquetas)
    raw_fun_codes = _unique_keep_order(_as_list(getattr(s, 'funciones', None)))
    fun_labels = []
    for code in raw_fun_codes:
        if code == 'otro':
            continue
        label = FUNCIONES_LABELS.get(code, code)
        if label:
            fun_labels.append(label)

    otros_fun = _s(getattr(s, 'funciones_otro', None))
    if otros_fun:
        fun_labels.append(otros_fun)

    funciones_txt = ", ".join(fun_labels) if fun_labels else ""

    # Hogar
    tipo_lugar   = _s(getattr(s, 'tipo_lugar', None))
    habitaciones = _s(getattr(s, 'habitaciones', None))
    banos_txt    = _fmt_banos(getattr(s, 'banos', None))

    # Áreas comunes
    areas_raw   = _as_list(getattr(s, 'areas_comunes', None))
    area_otro   = _s(getattr(s, 'area_otro', None))
    if area_otro:
        areas_raw.append(area_otro)
    areas_txt = ", ".join(_unique_keep_order([_norm_area(a) for a in areas_raw])) if areas_raw else ""

    # Familia
    adultos    = _s(getattr(s, 'adultos', None))
    ninos_val  = _s(getattr(s, 'ninos', None))
    edades_n   = _s(getattr(s, 'edades_ninos', None))
    mascota    = _s(getattr(s, 'mascota', None))

    # Dinero
    sueldo_raw    = getattr(s, 'sueldo', None)
    sueldo_txt    = _format_money_usd(sueldo_raw)
    pasaje_aporte = bool(getattr(s, 'pasaje_aporte', False))

    lineas = []

    # Encabezado
    if codigo:
        lineas.append(f"🧾 Resumen de su solicitud ({codigo})")
    else:
        lineas.append("🧾 Resumen de su solicitud")
    lineas.append("")

    # Ubicación / modalidad
    if ciudad_sector:
        lineas.append(f"📍 Ciudad / Sector: {ciudad_sector}")
    if rutas:
        lineas.append(f"🚌 Ruta más cercana: {rutas}")
    if modalidad:
        lineas.append(f"💼 Modalidad: {modalidad}")
    if edad_txt:
        lineas.append(f"👤 Edad requerida: {edad_txt}")
    if horario:
        lineas.append(f"⏰ Horario: {horario}")
    if experiencia:
        lineas.append(f"⭐ Experiencia solicitada: {experiencia}")
    lineas.append("")

    # Hogar
    lineas.append("🏠 Detalles del hogar:")
    hogar_sub = []
    if tipo_lugar:
        hogar_sub.append(f"• Tipo de lugar: {tipo_lugar}")
    if habitaciones:
        hogar_sub.append(f"• Habitaciones: {habitaciones}")
    if banos_txt:
        hogar_sub.append(f"• Baños: {banos_txt}")
    if areas_txt:
        hogar_sub.append(f"• Áreas comunes: {areas_txt}")

    if hogar_sub:
        lineas.extend(hogar_sub)
    else:
        lineas.append("• (No se especificaron detalles del hogar)")
    lineas.append("")

    # Familia
    lineas.append("👨‍👩‍👧‍👦 Composición del hogar:")
    fam_sub = []
    if adultos:
        fam_sub.append(f"• Adultos en casa: {adultos}")
    if ninos_val:
        if edades_n:
            fam_sub.append(f"• Niños: {ninos_val} (edades: {edades_n})")
        else:
            fam_sub.append(f"• Niños: {ninos_val}")
    if mascota:
        fam_sub.append(f"• Mascotas: {mascota}")

    if fam_sub:
        lineas.extend(fam_sub)
    else:
        lineas.append("• (No se especificó información de adultos/niños/mascotas)")
    lineas.append("")

    # Funciones
    lineas.append("🧹 Funciones principales:")
    if funciones_txt:
        lineas.append(f"• {funciones_txt}")
    else:
        lineas.append("• (No se especificaron funciones en detalle)")
    lineas.append("")

    # Dinero
    lineas.append("💰 Oferta económica:")
    if sueldo_txt:
        extra = "más ayuda del pasaje" if pasaje_aporte else "pasaje incluido"
        lineas.append(f"• Sueldo: {sueldo_txt} mensual, {extra}")
    else:
        lineas.append("• (No se especificó sueldo)")

    lineas.append("")

    # Nota del cliente
    if nota_cli:
        lineas.append("📝 Nota adicional del cliente:")
        lineas.append(f"{nota_cli}")
        lineas.append("")

    return "\n".join(lineas).rstrip()


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login de admin basado en diccionario USUARIOS.

    Endurecido:
      - Anti fuerza bruta por IP+usuario usando cache.
      - Sanitiza inputs (strip + límites).
      - Limpia sesión al autenticar (reduce session fixation).
      - Evita open-redirect con ?next=...
      - Honeypot opcional (anti-bots) via input hidden name="website".

    Nota: Asegúrate de incluir {{ csrf_token() }} en admin/login.html.
    """
    error = None

    if request.method == 'POST':
        # Honeypot (opcional). Si el template no lo tiene, no afecta.
        if (request.form.get('website') or '').strip():
            return "", 400

        usuario_raw = (request.form.get('usuario') or '').strip()[:64]
        clave       = (request.form.get('clave') or '').strip()[:128]
        usuario_norm = (usuario_raw or '').strip().lower()

        # Si está bloqueado por IP+usuario
        if _admin_is_locked(usuario_norm):
            mins = _admin_login_lock_minutos()
            error = f'Has excedido el máximo de intentos. Intenta de nuevo en {mins} minutos.'
            return render_template('admin/login.html', error=error), 429

        user_data = None
        try:
            # USUARIOS normalmente viene con keys exactas. Permitimos match case-insensitive.
            # Primero intento exacto, luego busco por lower.
            user_data = USUARIOS.get(usuario_raw) or USUARIOS.get(usuario_norm)
            if user_data is None:
                for k, v in (USUARIOS or {}).items():
                    if str(k).strip().lower() == usuario_norm:
                        user_data = v
                        usuario_raw = k  # preserva el username real para role y session
                        break
        except Exception:
            user_data = None

        ok = False
        try:
            if user_data and check_password_hash(user_data.get('pwd_hash', ''), clave):
                ok = True
        except Exception:
            ok = False

        if ok:
            # ✅ Login correcto
            try:
                session.clear()
            except Exception:
                pass

            try:
                session.permanent = True
            except Exception:
                pass

            login_user(AdminUser(str(usuario_raw)), remember=False)

            # ✅ MARCAR ESTA SESIÓN COMO ADMIN (AISLAMIENTO REAL)
            try:
                session[_ADMIN_SESSION_MARKER] = True
                session.modified = True
            except Exception:
                pass

            # Reset locks
            _admin_reset_fail(usuario_norm)
            _clear_security_layer_lock_admin(endpoint="/admin/login", usuario=str(usuario_raw))

            fallback = url_for('admin.listar_clientes')
            return redirect(_safe_next_url(fallback))

        # ❌ Login incorrecto
        _admin_register_fail(usuario_norm)
        error = 'Credenciales inválidas.'

    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
@login_required
def logout():
    try:
        # captura usuario antes de salir
        uname = None
        try:
            uname = (current_user.get_id() if current_user else None)
        except Exception:
            uname = None

        # ✅ bajar marcador de sesión admin (por si session.clear falla)
        try:
            session.pop(_ADMIN_SESSION_MARKER, None)
        except Exception:
            pass

        logout_user()

        # limpiar locks (si se puede)
        if uname:
            usuario_norm = str(uname).strip().lower()

            # 🔐 reset de bruteforce login
            try:
                _admin_reset_fail(usuario_norm)
            except Exception:
                pass

            # 🔐 limpiar capa global (si existe)
            try:
                _clear_security_layer_lock_admin(endpoint="/admin/login", usuario=str(uname))
            except Exception:
                pass

            # 🟡 reset de rate-limit admin (acciones)
            try:
                # limpiamos buckets comunes para que al salir quede limpio
                buckets = ["default", "pagos", "solicitudes", "reemplazos", "delete", "tareas"]

                if _cache_ok():
                    for b in buckets:
                        try:
                            keys = _admin_action_keys(usuario_norm, bucket=b)
                            cache.delete(keys["count"])
                            cache.delete(keys["lock"])
                        except Exception:
                            pass

                for b in buckets:
                    try:
                        session.pop(_sess_action_key(usuario_norm, bucket=b), None)
                    except Exception:
                        pass
            except Exception:
                pass

        # ✅ limpieza total de sesión
        try:
            session.clear()
        except Exception:
            pass

    except Exception:
        try:
            # por si algo explotó, igual nos aseguramos de salir
            try:
                session.pop(_ADMIN_SESSION_MARKER, None)
            except Exception:
                pass
            logout_user()
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass

    return redirect(url_for('admin.login'))

# =============================================================================
#                 GUARD GLOBAL ADMIN (aislamiento real)
# =============================================================================

def _is_admin_identity_LEGACY() -> bool:
    """True si el current_user pertenece a USUARIOS (admin/staff/secretaria).
    Esto evita que un cliente autenticado (con otra sesión) pueda tocar /admin/*
    aunque haya un bug de roles.
    """
    try:
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return False

        uid = None
        try:
            uid = current_user.get_id()
        except Exception:
            uid = getattr(current_user, "id", None)

        if uid is None:
            return False

        uid_str = str(uid).strip()
        if not uid_str:
            return False

        # Match exacto o case-insensitive contra USUARIOS
        if uid_str in (USUARIOS or {}):
            return True

        uid_norm = uid_str.lower()
        for k in (USUARIOS or {}).keys():
            if str(k).strip().lower() == uid_norm:
                return True

        return False
    except Exception:
        return False


@admin_bp.before_request
def _admin_guard_before_request_LEGACY():
    """Se ejecuta antes de cualquier endpoint del blueprint admin.
    Si la sesión no es de admin real -> logout y pa' fuera.
    """
    return None
    try:
        # Permitir login sin estar autenticado
        if request.endpoint in ("admin.login", "admin.static"):
            return None

        # Si no está logueado, pa' login
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("admin.login"))

        # ✅ Aislamiento real: si NO es usuario admin/staff/secretaria => sacar
        if not _is_admin_identity_LEGACY():
            try:
                logout_user()
            except Exception:
                pass
            try:
                session.clear()
            except Exception:
                pass
            return redirect(url_for("admin.login"))

        return None
    except Exception:
        # fallback ultra seguro
        try:
            logout_user()
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass
        return redirect(url_for("admin.login"))

# =============================================================================
#                 RATE-LIMIT ADMIN (acciones sensibles)
# =============================================================================

_ADMIN_ACTION_KEY_PREFIX_LEGACY = "admin_act"

def _admin_action_max_LEGACY() -> int:
    # acciones permitidas por ventana
    try:
        return int((os.getenv("ADMIN_ACTION_MAX") or "40").strip())
    except Exception:
        return 40

def _admin_action_window_sec_LEGACY() -> int:
    # ventana en segundos
    try:
        return int((os.getenv("ADMIN_ACTION_WINDOW_SEC") or "60").strip())
    except Exception:
        return 60

def _admin_action_lock_min_LEGACY() -> int:
    # lock en minutos si se pasa
    try:
        return int((os.getenv("ADMIN_ACTION_LOCK_MIN") or "5").strip())
    except Exception:
        return 5

def _admin_action_keys_LEGACY(usuario_norm: str, bucket: str = "default"):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    base = f"{_ADMIN_ACTION_KEY_PREFIX_LEGACY}:{ip}:{u}:{b}"
    return {
        "count": f"{base}:count",
        "lock":  f"{base}:lock",
    }

def _sess_action_key_LEGACY(usuario_norm: str, bucket: str = "default") -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    return f"admin_act:{ip}:{u}:{b}"

def _session_action_get_LEGACY(usuario_norm: str, bucket: str):
    return session.get(_sess_action_key_LEGACY(usuario_norm, bucket)) or {}

def _session_action_is_locked_LEGACY(usuario_norm: str, bucket: str) -> bool:
    data = _session_action_get_LEGACY(usuario_norm, bucket)
    until = data.get("locked_until")
    if not until:
        return False
    try:
        return datetime.utcnow().timestamp() < float(until)
    except Exception:
        return False

def _session_action_register_LEGACY(usuario_norm: str, bucket: str, max_actions: int, window_sec: int) -> int:
    key = _sess_action_key_LEGACY(usuario_norm, bucket)
    now = datetime.utcnow().timestamp()
    data = session.get(key) or {}

    start = float(data.get("start_ts") or now)
    count = int(data.get("count") or 0)

    # si se venció la ventana, resetea
    if (now - start) > window_sec:
        start = now
        count = 0

    count += 1
    data["start_ts"] = start
    data["count"] = count

    if count >= max_actions:
        data["locked_until"] = now + (_admin_action_lock_min_LEGACY() * 60)

    session[key] = data
    return count

def _admin_action_is_locked_LEGACY(usuario_norm: str, bucket: str) -> bool:
    if _cache_ok():
        keys = _admin_action_keys_LEGACY(usuario_norm, bucket=bucket)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            return _session_action_is_locked_LEGACY(usuario_norm, bucket)
    return _session_action_is_locked_LEGACY(usuario_norm, bucket)

def _admin_action_register_LEGACY(usuario_norm: str, bucket: str, max_actions: int, window_sec: int) -> int:
    if _cache_ok():
        keys = _admin_action_keys_LEGACY(usuario_norm, bucket=bucket)
        try:
            # ventana: count expira con la ventana
            n = int(cache.get(keys["count"]) or 0) + 1
            cache.set(keys["count"], n, timeout=window_sec)

            if n >= max_actions:
                cache.set(keys["lock"], True, timeout=_admin_action_lock_min_LEGACY() * 60)
            return n
        except Exception:
            return _session_action_register_LEGACY(usuario_norm, bucket, max_actions, window_sec)

    return _session_action_register_LEGACY(usuario_norm, bucket, max_actions, window_sec)

def admin_action_limit_LEGACY(bucket: str = "default", max_actions: int | None = None, window_sec: int | None = None):
    """Decorador para limitar acciones admin por IP+usuario.
    bucket: agrupa acciones (ej: 'delete', 'edit', 'pay', 'reemplazo')
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                uname = ""
                try:
                    uname = current_user.get_id()
                except Exception:
                    uname = getattr(current_user, "id", "") or ""
                usuario_norm = str(uname).strip().lower()

                lim = int(max_actions) if max_actions is not None else _admin_action_max_LEGACY()
                win = int(window_sec) if window_sec is not None else _admin_action_window_sec_LEGACY()

                if _admin_action_is_locked_LEGACY(usuario_norm, bucket=bucket):
                    mins = _admin_action_lock_min_LEGACY()
                    flash(f"Demasiadas acciones seguidas. Intenta de nuevo en {mins} minutos.", "warning")
                    return redirect(url_for("admin.listar_clientes"))

                _admin_action_register_LEGACY(usuario_norm, bucket=bucket, max_actions=lim, window_sec=win)

            except Exception:
                # si falla el rate-limit, NO rompemos la app
                pass

            return fn(*args, **kwargs)
        return wrapper
    return deco

# =============================================================================
#                            CLIENTES (CRUD BÁSICO)
# =============================================================================
@admin_bp.route('/clientes')
@login_required
@staff_required
def listar_clientes():
    """
    Lista de clientes con búsqueda básica.
    - Evita escaneos completos si la query de texto es de 1 carácter (excepto ID numérica).
    """
    q = (request.args.get('q') or '').strip()
    query = Cliente.query

    if q:
        filtros = []
        q_lower = q.lower()

        # 1) Si es un ID exacto (entero), permite búsqueda directa por ID
        if q.isdigit():
            try:
                filtros.append(Cliente.id == int(q))
            except Exception:
                pass

        # 2) Búsqueda por CÓDIGO (exacto + parcial)
        #    - Exacto: rápido y preciso
        #    - Parcial: útil para fragmentos (ej: "ADC" o "ADC-" o "-A")
        try:
            filtros.append(Cliente.codigo == q)
        except Exception:
            pass
        if len(q) >= 2:
            filtros.append(Cliente.codigo.ilike(f"%{q}%"))

        # 3) Búsqueda por EMAIL (case-insensitive) — soporta "gmail" o el email completo
        #    - Si el query incluye '@' o '.' o la palabra 'gmail', asumimos que es email/fragmento
        looks_like_email = ('@' in q) or ('.' in q) or ('gmail' in q_lower)
        if looks_like_email:
            try:
                filtros.append(func.lower(Cliente.email).like(f"%{q_lower}%"))
            except Exception:
                # fallback si el motor no soporta func.lower
                filtros.append(Cliente.email.ilike(f"%{q}%"))
        else:
            # Si no parece email, solo lo incluimos cuando q tenga mínimo 2 chars (evita full scan por 1 char)
            if len(q) >= 2:
                filtros.append(Cliente.email.ilike(f"%{q}%"))

        # 4) Campos extra (solo cuando hay suficiente texto para evitar escaneo completo)
        if len(q) >= 2:
            filtros.extend([
                Cliente.nombre_completo.ilike(f"%{q}%"),
                Cliente.telefono.ilike(f"%{q}%"),
            ])

        if filtros:
            query = query.filter(or_(*filtros))

    clientes = query.order_by(Cliente.fecha_registro.desc()).all()
    return render_template('admin/clientes_list.html', clientes=clientes, q=q)


# ─────────────────────────────────────────────────────────────
# Helpers de fecha (UTC) para listados/filtrado
# ─────────────────────────────────────────────────────────────

def _today_utc_bounds():
    """Devuelve (start_utc, end_utc) del día actual en UTC como datetimes NAIVE.

    Se usa para filtros por rango diario sin depender de timezone-aware datetimes,
    manteniendo consistencia con columnas típicamente naive en Postgres.
    """
    now_utc = datetime.utcnow()
    start = datetime(now_utc.year, now_utc.month, now_utc.day)
    end = start + timedelta(days=1)
    return start, end


# ─────────────────────────────────────────────────────────────
# Endpoint liviano para auto-refresh (JSON)
# ─────────────────────────────────────────────────────────────

def _dt_iso(d) -> str | None:
    """Convierte date/datetime a ISO string (naive) para JSON."""
    if not d:
        return None
    try:
        return d.isoformat()
    except Exception:
        return str(d)


def _solicitudes_live_payload(limit: int = 50) -> dict:
    """Snapshot compacto de solicitudes para refresco silencioso en UI."""
    try:
        limit = int(limit)
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))

    # Cargamos relaciones básicas para evitar N+1
    solicitudes = (
        Solicitud.query
        .options(
            joinedload(Solicitud.cliente),
            joinedload(Solicitud.candidata),
        )
        .order_by(Solicitud.id.desc())
        .limit(limit)
        .all()
    )

    rows = []
    last_ts = None

    for s in solicitudes:
        # timestamps relevantes
        ts = getattr(s, 'fecha_ultima_modificacion', None) or getattr(s, 'fecha_solicitud', None)
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts

        cli = getattr(s, 'cliente', None)
        cand = getattr(s, 'candidata', None)

        rows.append({
            "id": s.id,
            "codigo_solicitud": (getattr(s, 'codigo_solicitud', None) or '').strip() or None,
            "estado": (getattr(s, 'estado', None) or '').strip() or None,
            "sueldo": (getattr(s, 'sueldo', None) or '').strip() if getattr(s, 'sueldo', None) is not None else None,
            "monto_pagado": (getattr(s, 'monto_pagado', None) or '').strip() if getattr(s, 'monto_pagado', None) is not None else None,
            "cliente": {
                "id": getattr(cli, 'id', None),
                "nombre": (getattr(cli, 'nombre_completo', None) or '').strip() or None,
                "codigo": (getattr(cli, 'codigo', None) or '').strip() or None,
            } if cli else None,
            "candidata": {
                "id": getattr(cand, 'fila', None),
                "nombre": (getattr(cand, 'nombre_completo', None) or '').strip() or None,
                "codigo": (getattr(cand, 'codigo', None) or '').strip() or None,
            } if cand else None,
            "fecha_solicitud": _dt_iso(getattr(s, 'fecha_solicitud', None)),
            "fecha_ultima_modificacion": _dt_iso(getattr(s, 'fecha_ultima_modificacion', None)),
        })

    return {
        "ok": True,
        "count": len(rows),
        "last_updated": _dt_iso(last_ts) or _dt_iso(datetime.utcnow()),
        "rows": rows,
    }


@admin_bp.route('/solicitudes/live')
@login_required
@staff_required
def solicitudes_live():
    """Endpoint JSON para refrescar la lista de solicitudes sin recargar la página.

    Uso típico en el front:
      GET /admin/solicitudes/live?limit=50

    Devuelve:
      { ok, count, last_updated, rows: [...] }
    """
    limit = request.args.get('limit', 50)
    payload = _solicitudes_live_payload(limit=limit)

    # Cache-control: evita que el navegador lo guarde; siempre trae lo más nuevo
    resp = jsonify(payload)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@admin_bp.route('/ping')
@login_required
def admin_ping():
    """Ping simple para saber si la sesión sigue viva (útil para UI)."""
    resp = jsonify({"ok": True, "utc": datetime.utcnow().isoformat()})
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

# =============================================================================
#                       HELPERS DE LIMPIEZA / NORMALIZACIÓN
# =============================================================================

def _only_digits(text: str) -> str:
    """Retorna solo dígitos de un texto (para teléfonos, etc.)."""
    return re.sub(r"\D+", "", text or "")


# Nuevo helper: normalizar strings numéricos (para sueldo, etc.)
def _norm_numeric_str(value) -> str | None:
    """Normaliza strings numéricos para campos como sueldo.

    - Acepta: "30000", "RD$ 30,000", "30.000", "30 000"
    - Retorna SOLO dígitos (sin decimales) o None si queda vacío.

    Importante: esto NO toca lo ya guardado en BD; solo normaliza lo que entra por formularios.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = _only_digits(s)
    return s or None

def _normalize_email(value: str) -> str:
    """Email normalizado (lower + strip)."""
    return (value or '').strip().lower()

def _normalize_phone(value: str) -> str:
    """
    Normaliza teléfono manteniendo dígitos. Si quieres guardar con formato,
    hazlo en la vista; persiste solo dígitos en la BD si tu modelo lo permite.
    """
    digits = _only_digits(value)
    return digits

def _strip_if_str(x):
    return x.strip() if isinstance(x, str) else x

def _norm_cliente_form(form: AdminClienteForm) -> None:
    """
    Normaliza/limpia entradas de texto del formulario de cliente.
    """
    if hasattr(form, 'codigo') and form.codigo.data:
        form.codigo.data = _strip_if_str(form.codigo.data)

    if hasattr(form, 'nombre_completo') and form.nombre_completo.data:
        form.nombre_completo.data = _strip_if_str(form.nombre_completo.data)

    if hasattr(form, 'email') and form.email.data:
        form.email.data = _normalize_email(form.email.data)

    if hasattr(form, 'telefono') and form.telefono.data:
        # guarda limpio; si prefieres mantener guiones para UI, renderízalos en plantilla
        form.telefono.data = _normalize_phone(form.telefono.data)

    if hasattr(form, 'ciudad') and form.ciudad.data:
        form.ciudad.data = _strip_if_str(form.ciudad.data)

    if hasattr(form, 'sector') and form.sector.data:
        form.sector.data = _strip_if_str(form.sector.data)

    if hasattr(form, 'notas_admin') and form.notas_admin.data:
        form.notas_admin.data = _strip_if_str(form.notas_admin.data)


def parse_integrity_error(err: IntegrityError) -> str:
    """
    Intenta detectar qué constraint única falló.
    Retorna 'codigo', 'email' o '' si no se pudo identificar.
    Funciona para SQLite, MySQL y PostgreSQL en la mayoría de casos.
    """
    msg = ""
    try:
        msg = str(getattr(err, "orig", err))
    except Exception:
        msg = str(err)

    m = msg.lower()

    # PostgreSQL: nombre del constraint si está disponible
    try:
        cstr = getattr(getattr(err, "orig", None), "diag", None)
        if cstr and getattr(cstr, "constraint_name", None):
            cname = cstr.constraint_name.lower()
            if "codigo" in cname:
                return "codigo"
            if "email" in cname or "correo" in cname:
                return "email"
    except Exception:
        pass

    # Heurísticas por mensaje (MySQL/SQLite)
    if "codigo" in m:
        return "codigo"
    if "email" in m or "correo" in m:
        return "email"

    if "for key" in m and "email" in m:
        return "email"
    if "for key" in m and "codigo" in m:
        return "codigo"

    return ""


# =============================================================================
#                 HELPERS CONSISTENTES PARA EDAD Y LISTAS (ADMIN)
#            (VERSIÓN CANÓNICA — ELIMINAR CUALQUIER DUPLICADO LUEGO)
# =============================================================================
def _as_list(value):
    """Devuelve siempre una lista (acepta None, str o list/tuple/set)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(',') if p.strip()]
        return parts if parts else ([value.strip()] if value.strip() else [])
    return [value]

def _clean_list(seq):
    """Lista sin vacíos/guiones, preservando orden y quitando duplicados."""
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
    """Mapeos code<->label a partir de choices [(code, label), ...]."""
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
    """
    Recibe lista de CÓDIGOS marcados en el form, choices y el texto de 'otro'.
    Devuelve lista final de LABELS legibles (lo que se guarda en BD).
    """
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
            result.extend([x.strip() for x in extra.split(',') if x.strip()])

    return _clean_list(result)

def _split_edad_for_form(stored_list, edad_choices):
    """
    Convierte lo guardado en BD (LABELS legibles) a (CÓDIGOS seleccionados, texto_otro)
    para precargar el formulario.
    """
    stored_list = _clean_list(stored_list)
    code_to_label, label_to_code = _choices_maps(edad_choices)

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


# =============================================================================
#                      CONSTANTES / CHOICES PARA FORMULARIOS
# =============================================================================
AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina', 'Cocina'), ('salon_juegos', 'Salón de juegos'),
    ('terraza', 'Terraza'), ('jardin', 'Jardín'),
    ('estudio', 'Estudio'), ('patio', 'Patio'),
    ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
    ('todas_anteriores', 'Todas las anteriores'),
]


# =============================================================================
#                              HELPERS NUEVOS (HOGAR)
# =============================================================================
def _norm_area(text: str) -> str:
    """Reemplaza guiones bajos por espacios y colapsa espacios múltiples."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _fmt_banos(value) -> str:
    """Devuelve baños sin .0 si es entero; si no, muestra el decimal tal cual."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:
        return str(value)

def _map_funciones(vals, extra_text):
    """
    Combina funciones seleccionadas con valores personalizados de 'otro',
    eliminando duplicados y vacíos.
    """
    vals = _clean_list(vals)
    if 'otro' in vals:
        vals = [v for v in vals if v != 'otro']
        extra = (extra_text or '').strip()
        if extra:
            vals.extend([x.strip() for x in extra.split(',') if x.strip()])
    return _clean_list(vals)

def _map_tipo_lugar(value, extra):
    """
    Si el valor es 'otro', usa el texto extra; en otro caso retorna el valor tal cual.
    """
    value = (value or '').strip()
    if value == 'otro':
        return (extra or '').strip() or value
    return value


# ─────────────────────────────────────────────────────────────
# Helpers internos específicos de Solicitud
# ─────────────────────────────────────────────────────────────
def _allowed_codes_from_choices(choices):
    """Devuelve el set de códigos válidos a partir de choices [(code,label), ...]."""
    try:
        return {str(v).strip() for v, _ in (choices or []) if str(v).strip()}
    except Exception:
        return set()

def _normalize_areas_comunes_selected(selected_vals, choices):
    """Normaliza áreas comunes y expande 'todas_anteriores' a todas las opciones reales."""
    vals = _clean_list(selected_vals)
    allowed = _allowed_codes_from_choices(choices)
    vals = [v for v in vals if v in allowed]

    if 'todas_anteriores' in vals:
        all_codes = [
            str(code).strip()
            for code, _ in (choices or [])
            if str(code).strip() and str(code).strip() not in {'todas_anteriores', 'otro'}
        ]
        vals = [v for v in vals if v != 'todas_anteriores']
        vals = _clean_list(vals + all_codes)

    return [v for v in vals if v != 'todas_anteriores']

def _next_codigo_solicitud(cliente: Cliente) -> str:
    """
    Genera un código único del tipo <CODCLI>-<LETRA>.
    Usa un loop defensivo para evitar colisiones si hubo borrados o concurrencia.
    """
    prefix = (cliente.codigo or str(cliente.id)).strip()
    base_count = Solicitud.query.filter_by(cliente_id=cliente.id).count()
    intento = 0
    while True:
        suf = letra_por_indice(base_count + intento)
        code = f"{prefix}-{suf}"
        exists = Solicitud.query.filter(Solicitud.codigo_solicitud == code).first()
        if not exists:
            return code
        intento += 1

# =============================================================================
#                         CLIENTES – CREAR / EDITAR / ELIMINAR / DETALLE
# =============================================================================

@admin_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="create_cliente", max_actions=25, window_sec=60)
def nuevo_cliente():
    """🟢 Crear un nuevo cliente desde el panel de administración (sin credenciales de login)."""
    form = AdminClienteForm()

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validación de código único (case-sensitive) ---
        try:
            if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
        except Exception:
            flash("No se pudo validar el código del cliente.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de email único (case-insensitive) ---
        email_norm = (form.email.data or "").lower().strip()
        try:
            if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
                form.email.errors.append("Este email ya está registrado.")
                flash("El email ya está registrado.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
        except Exception:
            flash("No se pudo validar el email del cliente.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de USERNAME único (opcional, si existe en el modelo) ---
        username_norm = None
        if hasattr(Cliente, 'username'):
            # Preferimos el campo del form si existe; si no, intentamos leerlo del POST directo
            raw_username = None
            if hasattr(form, 'username'):
                raw_username = form.username.data
            if not raw_username:
                raw_username = request.form.get('username')

            username_norm = (raw_username or '').strip().lower()
            if not username_norm:
                # Si no envían username, usamos el email como username por defecto
                username_norm = email_norm

            try:
                if Cliente.query.filter(func.lower(Cliente.username) == username_norm).first():
                    if hasattr(form, 'username'):
                        form.username.errors.append("Este usuario ya está registrado.")
                    flash("Este usuario ya está registrado.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
            except Exception:
                flash("No se pudo validar el usuario del cliente.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Creación del cliente (con username/password si existen) ---
        try:
            ahora = datetime.utcnow()
            c = Cliente()
            form.populate_obj(c)

            # Normalizamos email y fechas clave
            c.email = email_norm

            # Username (si existe en el modelo)
            if hasattr(c, 'username'):
                # Si ya calculamos username_norm arriba úsalo, si no, usa el email
                c.username = (username_norm or email_norm)

            # Password (si existe en el modelo)
            if hasattr(c, 'password_hash'):
                raw_pw = None
                if hasattr(form, 'password'):
                    raw_pw = form.password.data
                if not raw_pw:
                    raw_pw = request.form.get('password')

                raw_pw = (raw_pw or '').strip()
                # Si mandan contraseña, la seteamos. Si no, dejamos el server_default (DISABLED_RESET_REQUIRED)
                if raw_pw:
                    # Fuerza PBKDF2 (compatibilidad): algunos Python/macOS pueden no traer hashlib.scrypt
                    c.password_hash = generate_password_hash(raw_pw, method="pbkdf2:sha256")

            if not c.fecha_registro:
                c.fecha_registro = ahora
            if not c.created_at:
                c.created_at = ahora
            c.updated_at = ahora

            db.session.add(c)
            db.session.commit()

            flash('Cliente creado correctamente ✅', 'success')
            return redirect(url_for('admin.listar_clientes'))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
            elif which == "email":
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
            else:
                flash("Conflicto con datos únicos. Verifica código y/o email.", "danger")

        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear el cliente. Intenta de nuevo.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# 🔵 Editar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="edit_cliente", max_actions=35, window_sec=60)
def editar_cliente(cliente_id):
    """✏️ Editar la información de un cliente existente.

    Fix:
    - El bug solo ocurría cuando se tocaba username/password.
    - Evitamos `form.populate_obj(c)` para que WTForms no intente setear atributos no mapeados.
    - Actualizamos username/password SOLO si el usuario escribió algo.
    - Logueamos el error real en terminal para depurar rápido.
    """
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validar código si se modifica ---
        if hasattr(c, 'codigo') and hasattr(form, 'codigo'):
            new_codigo = (form.codigo.data or '').strip()
            old_codigo = (c.codigo or '').strip()
            if new_codigo != old_codigo:
                try:
                    if Cliente.query.filter(Cliente.codigo == new_codigo).first():
                        form.codigo.errors.append("Este código ya está en uso.")
                        flash("El código ya está en uso.", "danger")
                        return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)
                except Exception:
                    flash("No se pudo validar el código del cliente.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)

        # --- Validar email si se modifica ---
        email_norm = (getattr(form, 'email', type('x', (), {'data': ''})) .data or '').lower().strip()
        email_actual = (getattr(c, 'email', '') or '').lower().strip()
        if email_norm != email_actual:
            try:
                if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
                    if hasattr(form, 'email'):
                        form.email.errors.append("Este email ya está registrado.")
                    flash("Este email ya está registrado.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)
            except Exception:
                flash("No se pudo validar el email del cliente.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)

        # --- Username: validar solo si el usuario escribió uno ---
        username_to_set = None
        if hasattr(c, 'username'):
            raw_username = None
            if hasattr(form, 'username'):
                raw_username = form.username.data
            if raw_username is None:
                raw_username = request.form.get('username')

            raw_username = (raw_username or '').strip()
            if raw_username:
                username_norm = raw_username.lower()
                username_actual = (getattr(c, 'username', '') or '').strip().lower()

                # Solo validar si realmente cambia
                if username_norm != username_actual:
                    try:
                        # Excluir el mismo cliente
                        exists = Cliente.query.filter(
                            func.lower(Cliente.username) == username_norm,
                            Cliente.id != c.id
                        ).first()
                        if exists:
                            if hasattr(form, 'username'):
                                form.username.errors.append("Este usuario ya está registrado.")
                            flash("Este usuario ya está registrado.", "danger")
                            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)
                    except Exception:
                        flash("No se pudo validar el usuario del cliente.", "danger")
                        return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)

                username_to_set = username_norm

        # --- Password: solo si escriben una nueva ---
        password_to_set = None
        if hasattr(c, 'password_hash'):
            raw_pw = None
            if hasattr(form, 'password'):
                raw_pw = form.password.data
            if raw_pw is None:
                raw_pw = request.form.get('password')
            raw_pw = (raw_pw or '').strip()
            if raw_pw:
                password_to_set = raw_pw

        # --- Guardar cambios (sin populate_obj) ---
        try:
            # Campos base (solo si existen)
            if hasattr(c, 'codigo') and hasattr(form, 'codigo'):
                c.codigo = (form.codigo.data or '').strip()

            if hasattr(c, 'nombre_completo') and hasattr(form, 'nombre_completo'):
                c.nombre_completo = (form.nombre_completo.data or '').strip()

            if hasattr(c, 'email'):
                c.email = email_norm

            if hasattr(c, 'telefono') and hasattr(form, 'telefono'):
                c.telefono = _normalize_phone(form.telefono.data or '')

            if hasattr(c, 'ciudad') and hasattr(form, 'ciudad'):
                c.ciudad = (form.ciudad.data or '').strip()

            if hasattr(c, 'sector') and hasattr(form, 'sector'):
                c.sector = (form.sector.data or '').strip()

            if hasattr(c, 'notas_admin') and hasattr(form, 'notas_admin'):
                c.notas_admin = (form.notas_admin.data or '').strip()

            # Username (solo si el usuario escribió uno)
            if username_to_set is not None and hasattr(c, 'username'):
                c.username = username_to_set

            # Password (solo si escribió una nueva)
            if password_to_set is not None and hasattr(c, 'password_hash'):
                # Fuerza PBKDF2 (compatibilidad): algunos Python/macOS pueden no traer hashlib.scrypt
                c.password_hash = generate_password_hash(password_to_set, method="pbkdf2:sha256")

            if hasattr(c, 'fecha_ultima_actividad'):
                c.fecha_ultima_actividad = datetime.utcnow()
            if hasattr(c, 'updated_at'):
                c.updated_at = datetime.utcnow()

            db.session.commit()

            flash('Cliente actualizado correctamente ✅', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                if hasattr(form, 'codigo'):
                    form.codigo.errors.append("Este código ya está en uso.")
                flash("Este código ya está en uso.", "danger")
            elif which == "email":
                if hasattr(form, 'email'):
                    form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
            else:
                # Puede incluir username unique si el parser no lo detecta
                flash('No se pudo actualizar: conflicto con datos únicos (código/email/usuario).', 'danger')

        except Exception:
            db.session.rollback()
            # Mostrar el error real en terminal
            try:
                import traceback
                print("\n=== ERROR REAL editar_cliente ===")
                traceback.print_exc()
                print("=== FIN ERROR ===\n")
            except Exception:
                pass
            flash('Ocurrió un error al actualizar el cliente. Intenta de nuevo.', 'danger')

    elif request.method == 'POST':
        # Si llegó POST pero no pasó validación, NO debe “parecer” que guardó.
        flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
        try:
            current_app.logger.warning('editar_cliente validate_on_submit=False | cliente_id=%s | errors=%s', cliente_id, form.errors)
        except Exception:
            pass
        try:
            print('editar_cliente validate_on_submit=False', 'cliente_id=', cliente_id, 'errors=', form.errors)
        except Exception:
            pass

    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)


# ─────────────────────────────────────────────────────────────
# 🔴 Eliminar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="delete_cliente", max_actions=10, window_sec=60)
def eliminar_cliente(cliente_id):
    """🗑️ Eliminar un cliente definitivamente."""
    c = Cliente.query.get_or_404(cliente_id)

    try:
        db.session.delete(c)
        db.session.commit()
        flash('Cliente eliminado correctamente 🗑️', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo eliminar el cliente.', 'danger')

    return redirect(url_for('admin.listar_clientes'))


# ─────────────────────────────────────────────────────────────
# 🔍 Detalle de cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@staff_required
def detalle_cliente(cliente_id):
    """
    Vista 360° del cliente:
    - Datos del cliente
    - Resumen de solicitudes (totales, estados, monto pagado)
    - Lista de solicitudes del cliente
    - Línea de tiempo simple de eventos (creación, publicaciones, pagos, cancelaciones, reemplazos)
    - Tareas de seguimiento del cliente
    """

    cliente = Cliente.query.get_or_404(cliente_id)

    # Cargar todas las solicitudes del cliente con relaciones básicas
    solicitudes = (
        Solicitud.query
        .options(
            joinedload(Solicitud.candidata),
            joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new)
        )
        .filter_by(cliente_id=cliente_id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )

    # ------------------------------
    # RESUMEN / KPI POR CLIENTE
    # ------------------------------
    total_sol = len(solicitudes)
    estados_count = {
        'proceso': 0,
        'activa': 0,
        'pagada': 0,
        'cancelada': 0,
        'reemplazo': 0,
        'otro': 0,
    }

    monto_total_pagado = Decimal('0.00')
    primera_solicitud = None
    ultima_solicitud = None

    for s in solicitudes:
        # Contar estados
        estado = (s.estado or '').strip().lower() or 'otro'
        if estado not in estados_count:
            estado = 'otro'
        estados_count[estado] += 1

        # Monto pagado (guardado como string "1234.56" normalmente)
        raw_monto = (s.monto_pagado or '').strip() if hasattr(s, 'monto_pagado') else ''
        if raw_monto:
            try:
                monto_total_pagado += Decimal(raw_monto)
            except Exception:
                # Si hay valores viejos mal formateados, no rompemos el flujo
                pass

        # Fechas de solicitudes para KPIs
        fs = getattr(s, 'fecha_solicitud', None)
        if fs:
            if primera_solicitud is None or fs < primera_solicitud:
                primera_solicitud = fs
            if ultima_solicitud is None or fs > ultima_solicitud:
                ultima_solicitud = fs

    # Última actividad del cliente (si no hay, usamos última_solicitud)
    ultima_actividad = getattr(cliente, 'fecha_ultima_actividad', None) or ultima_solicitud

    # Formato de dinero para mostrar
    monto_total_pagado_str = f"RD$ {monto_total_pagado:,.2f}"

    kpi_cliente = {
        'total_solicitudes': total_sol,
        'estados': estados_count,
        'monto_total_pagado': monto_total_pagado,
        'monto_total_pagado_str': monto_total_pagado_str,
        'primera_solicitud': primera_solicitud,
        'ultima_solicitud': ultima_solicitud,
        'ultima_actividad': ultima_actividad,
    }

    # ------------------------------
    # TIMELINE SIMPLE (HUMANO)
    # ------------------------------
    timeline = []

    for s in solicitudes:
        codigo = s.codigo_solicitud or s.id

        # 1) Creación de la solicitud
        if s.fecha_solicitud:
            timeline.append({
                'fecha': s.fecha_solicitud,
                'tipo': 'Solicitud creada',
                'detalle': f"Se creó la solicitud {codigo} para este cliente."
            })

        # 2) Solicitud activada / en búsqueda (lo más parecido a 'publicada')
        #    Usamos fecha_ultima_modificacion como referencia.
        if s.estado == 'activa' and getattr(s, 'fecha_ultima_modificacion', None):
            timeline.append({
                'fecha': s.fecha_ultima_modificacion,
                'tipo': 'Solicitud activada',
                'detalle': f"La solicitud {codigo} está activa y en búsqueda de candidata."
            })

        # 3) Solicitud copiada para publicar (texto que se copia para redes / grupos)
        if getattr(s, 'last_copiado_at', None):
            timeline.append({
                'fecha': s.last_copiado_at,
                'tipo': 'Solicitud copiada para publicar',
                'detalle': f"Se copió el texto de la solicitud {codigo} para publicarla en redes o grupos."
            })

        # 4) Pago registrado
        if s.estado == 'pagada' and getattr(s, 'fecha_ultima_modificacion', None):
            timeline.append({
                'fecha': s.fecha_ultima_modificacion,
                'tipo': 'Pago registrado',
                'detalle': f"La solicitud {codigo} fue marcada como pagada."
            })

        # 5) Solicitud cancelada
        if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
            motivo = (s.motivo_cancelacion or '').strip()
            texto_motivo = motivo or 'Sin motivo especificado por el cliente.'
            timeline.append({
                'fecha': s.fecha_cancelacion,
                'tipo': 'Solicitud cancelada',
                'detalle': f"La solicitud {codigo} fue cancelada. Motivo: {texto_motivo}"
            })

        # 6) Reemplazos activados
        for r in (s.reemplazos or []):
            fecha_r = getattr(r, 'fecha_inicio_reemplazo', None) or getattr(r, 'created_at', None)
            if not fecha_r:
                continue

            nombre_new = getattr(getattr(r, 'candidata_new', None), 'nombre_completo', None)
            if nombre_new:
                detalle_r = f"Se activó un reemplazo en la solicitud {codigo} con la candidata {nombre_new}."
            else:
                detalle_r = f"Se activó un reemplazo en la solicitud {codigo}."

            timeline.append({
                'fecha': fecha_r,
                'tipo': 'Reemplazo activado',
                'detalle': detalle_r
            })

    # Ordenar timeline de más reciente a más viejo
    timeline = sorted(timeline, key=lambda e: e['fecha'], reverse=True)

    # ------------------------------
    # TAREAS DEL CLIENTE
    # ------------------------------
    tareas = (
        TareaCliente.query
        .filter_by(cliente_id=cliente_id)
        .order_by(
            TareaCliente.estado != 'pendiente',             # primero pendientes
            TareaCliente.fecha_vencimiento.is_(None),       # luego las que no tienen fecha
            TareaCliente.fecha_vencimiento.asc(),           # las que vencen antes van arriba
            TareaCliente.fecha_creacion.desc()              # últimas creadas al final dentro del mismo grupo
        )
        .all()
    )

    return render_template(
        'admin/cliente_detail.html',
        cliente=cliente,
        solicitudes=solicitudes,
        kpi_cliente=kpi_cliente,
        timeline=timeline,
        tareas=tareas
    )


@admin_bp.route('/tareas/pendientes')
@login_required
@staff_required
def tareas_pendientes():
    """
    Lista todas las tareas que NO están completadas, ordenadas por fecha de vencimiento.
    """
    hoy = date.today()

    tareas = (
        TareaCliente.query
        .options(joinedload(TareaCliente.cliente))
        .filter(TareaCliente.estado != 'completada')
        .order_by(
            TareaCliente.fecha_vencimiento.is_(None),
            TareaCliente.fecha_vencimiento.asc(),
            TareaCliente.fecha_creacion.desc()
        )
        .all()
    )

    return render_template(
        'admin/tareas_pendientes.html',
        tareas=tareas,
        hoy=hoy
    )

@admin_bp.route('/tareas/hoy')
@login_required
@staff_required
def tareas_hoy():
    """
    Lista tareas con fecha_vencimiento == hoy y que no están completadas.
    """
    hoy = date.today()

    tareas = (
        TareaCliente.query
        .options(joinedload(TareaCliente.cliente))
        .filter(
            TareaCliente.estado != 'completada',
            TareaCliente.fecha_vencimiento == hoy
        )
        .order_by(TareaCliente.fecha_creacion.desc())
        .all()
    )

    return render_template(
        'admin/tareas_hoy.html',
        tareas=tareas,
        hoy=hoy
    )

@admin_bp.route('/clientes/<int:cliente_id>/tareas/rapida', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="tareas", max_actions=60, window_sec=60)
def crear_tarea_rapida(cliente_id):
    """
    Crea una tarea rápida para hoy asociada al cliente.
    No pide formulario, simplemente genera:
      - Título: "Dar seguimiento a <nombre>"
      - fecha_vencimiento: hoy
      - estado: pendiente
    """
    cliente = Cliente.query.get_or_404(cliente_id)

    titulo = (request.form.get('titulo') or '').strip()
    if not titulo:
        titulo = f"Dar seguimiento a {cliente.nombre_completo}"

    hoy = date.today()

    try:
        tarea = TareaCliente(
            cliente_id=cliente.id,
            titulo=titulo,
            fecha_creacion=datetime.utcnow(),
            fecha_vencimiento=hoy,
            estado='pendiente',
            prioridad='media'
        )
        db.session.add(tarea)
        db.session.commit()
        flash('Tarea rápida creada para hoy.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo crear la tarea rápida.', 'danger')

    return redirect(url_for('admin.detalle_cliente', cliente_id=cliente.id))



# ─────────────────────────────────────────────────────────────
# HELPERS: Detalles por tipo de servicio (JSONB)
# ─────────────────────────────────────────────────────────────

def _build_detalles_servicio_from_form(form) -> dict | None:
    """
    Construye el JSON que se guarda en Solicitud.detalles_servicio
    según el tipo de servicio seleccionado.
    """
    ts = getattr(form, 'tipo_servicio', None).data if hasattr(form, 'tipo_servicio') else None
    if not ts:
        return None

    detalles: dict = {
        "tipo": ts  # siempre guardamos el tipo aquí
    }

    # ─────────────────────────────
    # NIÑERA
    # ─────────────────────────────
    if ts == 'NINERA':
        cant_ninos = form.ninera_cant_ninos.data if hasattr(form, 'ninera_cant_ninos') else None
        edades = (form.ninera_edades.data or '').strip() if hasattr(form, 'ninera_edades') else ''
        tareas = _clean_list(form.ninera_tareas.data) if hasattr(form, 'ninera_tareas') else []
        tareas_otro = (form.ninera_tareas_otro.data or '').strip() if hasattr(form, 'ninera_tareas_otro') else ''
        condicion = (form.ninera_condicion_especial.data or '').strip() if hasattr(form, 'ninera_condicion_especial') else ''
        usa_otro = ('otro' in tareas)
        tareas = [t for t in tareas if t != 'otro']
        if not usa_otro:
            tareas_otro = ''

        detalles.update({
            "cantidad_ninos": cant_ninos,
            "edades_ninos": edades or None,
            "tareas": _clean_list(tareas or []),
            # Clave específica para evitar cruces con ENFERMERA.
            "ninera_tareas_otro": tareas_otro or None,
            "condicion_especial": condicion or None,
        })

    # ─────────────────────────────
    # ENFERMERA / CUIDADORA
    # ─────────────────────────────
    elif ts == 'ENFERMERA':
        a_quien = (form.enf_a_quien_cuida.data or '').strip() if hasattr(form, 'enf_a_quien_cuida') else ''
        condicion = (form.enf_condicion_principal.data or '').strip() if hasattr(form, 'enf_condicion_principal') else ''
        movilidad = form.enf_movilidad.data if hasattr(form, 'enf_movilidad') else ''
        tareas = _clean_list(form.enf_tareas.data) if hasattr(form, 'enf_tareas') else []
        tareas_otro = (form.enf_tareas_otro.data or '').strip() if hasattr(form, 'enf_tareas_otro') else ''
        usa_otro = ('otro' in tareas)
        tareas = [t for t in tareas if t != 'otro']
        if not usa_otro:
            tareas_otro = ''

        detalles.update({
            "a_quien_cuida": a_quien or None,
            "condicion_principal": condicion or None,
            "movilidad": movilidad or None,
            "tareas": _clean_list(tareas or []),
            # Clave específica para evitar cruces con NIÑERA.
            "enf_tareas_otro": tareas_otro or None,
        })

    # ─────────────────────────────
    # CHOFER
    # ─────────────────────────────
    elif ts == 'CHOFER':
        vehiculo = form.chofer_vehiculo.data if hasattr(form, 'chofer_vehiculo') else ''
        tipo_vehiculo = form.chofer_tipo_vehiculo.data if hasattr(form, 'chofer_tipo_vehiculo') else ''
        tipo_vehiculo_otro = (form.chofer_tipo_vehiculo_otro.data or '').strip() if hasattr(form, 'chofer_tipo_vehiculo_otro') else ''
        if tipo_vehiculo != 'otro':
            tipo_vehiculo_otro = ''
        rutas = (form.chofer_rutas.data or '').strip() if hasattr(form, 'chofer_rutas') else ''
        viajes_largos = bool(form.chofer_viajes_largos.data) if hasattr(form, 'chofer_viajes_largos') else None
        licencia = (form.chofer_licencia_detalle.data or '').strip() if hasattr(form, 'chofer_licencia_detalle') else ''

        detalles.update({
            "vehiculo": vehiculo or None,
            "tipo_vehiculo": tipo_vehiculo or None,
            "tipo_vehiculo_otro": tipo_vehiculo_otro or None,
            "rutas": rutas or None,
            "viajes_largos": viajes_largos,
            "licencia_requisitos": licencia or None,
        })

    # ─────────────────────────────
    # DOMÉSTICA DE LIMPIEZA
    # ─────────────────────────────
    elif ts == 'DOMESTICA_LIMPIEZA':
        # No metemos más cosas aquí porque ya usamos columnas normales (funciones, áreas, etc.)
        pass

    # Limpiar claves vacías
    clean = {
        k: v for k, v in detalles.items()
        if v not in (None, '', [], {})
    }
    return clean or None


def _populate_form_detalles_from_solicitud(form, solicitud: Solicitud) -> None:
    """
    Cuando se edita una solicitud, toma solicitud.detalles_servicio (JSON)
    y rellena los campos específicos correspondientes en el form.
    """
    try:
        if not hasattr(solicitud, 'detalles_servicio') or not solicitud.detalles_servicio:
            return

        data = solicitud.detalles_servicio or {}
        ts = data.get("tipo") or getattr(solicitud, 'tipo_servicio', None)

        # Aseguramos que el select tenga el tipo
        if hasattr(form, 'tipo_servicio') and not form.tipo_servicio.data:
            form.tipo_servicio.data = ts

        # ─────────────────────────────
        # NIÑERA
        # ─────────────────────────────
        if ts == 'NINERA':
            if hasattr(form, 'ninera_cant_ninos'):
                form.ninera_cant_ninos.data = data.get("cantidad_ninos")
            if hasattr(form, 'ninera_edades'):
                form.ninera_edades.data = data.get("edades_ninos") or ''
            if hasattr(form, 'ninera_tareas'):
                form.ninera_tareas.data = data.get("tareas") or []
            if hasattr(form, 'ninera_tareas_otro'):
                # Compat retroactiva: lee clave nueva y fallback legado.
                form.ninera_tareas_otro.data = (
                    data.get("ninera_tareas_otro")
                    or data.get("tareas_otro")
                    or ''
                )
            try:
                if hasattr(form, 'ninera_tareas') and hasattr(form, 'ninera_tareas_otro'):
                    if (form.ninera_tareas_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.ninera_tareas.choices)
                        if 'otro' in allowed:
                            vals = set(_clean_list(form.ninera_tareas.data))
                            vals.add('otro')
                            form.ninera_tareas.data = list(vals)
            except Exception:
                pass
            if hasattr(form, 'ninera_condicion_especial'):
                form.ninera_condicion_especial.data = data.get("condicion_especial") or ''

        # ─────────────────────────────
        # ENFERMERA / CUIDADORA
        # ─────────────────────────────
        elif ts == 'ENFERMERA':
            if hasattr(form, 'enf_a_quien_cuida'):
                form.enf_a_quien_cuida.data = data.get("a_quien_cuida") or ''
            if hasattr(form, 'enf_condicion_principal'):
                form.enf_condicion_principal.data = data.get("condicion_principal") or ''
            if hasattr(form, 'enf_movilidad'):
                form.enf_movilidad.data = data.get("movilidad") or ''
            if hasattr(form, 'enf_tareas'):
                form.enf_tareas.data = data.get("tareas") or []
            if hasattr(form, 'enf_tareas_otro'):
                # Compat retroactiva: lee clave nueva y fallback legado.
                form.enf_tareas_otro.data = (
                    data.get("enf_tareas_otro")
                    or data.get("tareas_otro")
                    or ''
                )
            try:
                if hasattr(form, 'enf_tareas') and hasattr(form, 'enf_tareas_otro'):
                    if (form.enf_tareas_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.enf_tareas.choices)
                        if 'otro' in allowed:
                            vals = set(_clean_list(form.enf_tareas.data))
                            vals.add('otro')
                            form.enf_tareas.data = list(vals)
            except Exception:
                pass

        # ─────────────────────────────
        # CHOFER
        # ─────────────────────────────
        elif ts == 'CHOFER':
            if hasattr(form, 'chofer_vehiculo'):
                form.chofer_vehiculo.data = data.get("vehiculo") or None
            if hasattr(form, 'chofer_tipo_vehiculo'):
                form.chofer_tipo_vehiculo.data = data.get("tipo_vehiculo") or ''
            if hasattr(form, 'chofer_tipo_vehiculo_otro'):
                form.chofer_tipo_vehiculo_otro.data = data.get("tipo_vehiculo_otro") or ''
            try:
                if hasattr(form, 'chofer_tipo_vehiculo') and hasattr(form, 'chofer_tipo_vehiculo_otro'):
                    if (form.chofer_tipo_vehiculo_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.chofer_tipo_vehiculo.choices)
                        if 'otro' in allowed:
                            form.chofer_tipo_vehiculo.data = 'otro'
            except Exception:
                pass
            if hasattr(form, 'chofer_rutas'):
                form.chofer_rutas.data = data.get("rutas") or ''
            if hasattr(form, 'chofer_viajes_largos'):
                form.chofer_viajes_largos.data = bool(data.get("viajes_largos")) if "viajes_largos" in data else None
            if hasattr(form, 'chofer_licencia_detalle'):
                form.chofer_licencia_detalle.data = data.get("licencia_requisitos") or ''

        # DOMESTICA_LIMPIEZA no tiene extras en JSON

    except Exception:
        # Si algo falla, no explotamos el render
        return


# ─────────────────────────────────────────────────────────────
# ADMIN: Nueva solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="create_solicitud", max_actions=25, window_sec=60)
def nueva_solicitud_admin(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # Valores iniciales
        if hasattr(form, 'tipo_servicio'):
            form.tipo_servicio.data = 'DOMESTICA_LIMPIEZA'

        if hasattr(form, 'funciones'):        form.funciones.data = []
        if hasattr(form, 'funciones_otro'):   form.funciones_otro.data = ''
        if hasattr(form, 'areas_comunes'):    form.areas_comunes.data = []
        if hasattr(form, 'area_otro'):        form.area_otro.data = ''
        if hasattr(form, 'edad_requerida'):   form.edad_requerida.data = []
        if hasattr(form, 'edad_otro'):        form.edad_otro.data = ''
        if hasattr(form, 'tipo_lugar_otro'):  form.tipo_lugar_otro.data = ''
        if hasattr(form, 'mascota'):          form.mascota.data = ''

        # Limpia bloques específicos
        if hasattr(form, 'ninera_cant_ninos'):
            form.ninera_cant_ninos.data = None
            form.ninera_edades.data = ''
            form.ninera_tareas.data = []
            form.ninera_tareas_otro.data = ''
            form.ninera_condicion_especial.data = ''

        if hasattr(form, 'enf_a_quien_cuida'):
            form.enf_a_quien_cuida.data = ''
            form.enf_movilidad.data = ''
            form.enf_condicion_principal.data = ''
            form.enf_tareas.data = []
            form.enf_tareas_otro.data = ''

        if hasattr(form, 'chofer_vehiculo'):
            form.chofer_vehiculo.data = None
            form.chofer_tipo_vehiculo.data = ''
            form.chofer_tipo_vehiculo_otro.data = ''
            form.chofer_rutas.data = ''
            form.chofer_viajes_largos.data = None
            form.chofer_licencia_detalle.data = ''

    # POST válido
    if form.validate_on_submit():
        try:
            # Código único
            nuevo_codigo = _next_codigo_solicitud(c)

            # Instanciar con mínimos
            s = Solicitud(
                cliente_id=c.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=nuevo_codigo
            )

            # Carga general desde WTForms
            form.populate_obj(s)

            # Sueldo (solo números): normaliza para evitar guardar "RD$", comas, etc.
            if hasattr(form, 'sueldo'):
                try:
                    s.sueldo = _norm_numeric_str(form.sueldo.data)
                except Exception:
                    # No rompemos la creación por un formato raro; se validará en el form
                    pass

            # Tipo de servicio
            if hasattr(form, 'tipo_servicio'):
                s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None

            # Tipo de lugar
            s.tipo_lugar = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
            )

            # Edad requerida (guardar LABELS)
            s.edad_requerida = _map_edad_choices(
                codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
            )

            # Mascota
            if hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            # Funciones
            selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
            extra_text    = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if 'otro' not in selected_codes:
                extra_text = ''
            if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                s.funciones = [c for c in selected_codes if c in valid_codes and c != 'otro']
            else:
                s.funciones = [c for c in selected_codes if c != 'otro']
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = extra_text or None

            # Áreas comunes válidas
            selected_areas = []
            if hasattr(form, 'areas_comunes'):
                selected_areas = _normalize_areas_comunes_selected(
                    selected_vals=getattr(form, 'areas_comunes', type('x', (object,), {'data': []})).data,
                    choices=form.areas_comunes.choices
                )
            s.areas_comunes = selected_areas

            # Área "otro"
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None

            # Pasaje
            s.pasaje_aporte = bool(getattr(form, 'pasaje_aporte', type('x', (object,), {'data': False})).data)

            # Detalles específicos según tipo_servicio (JSONB)
            s.detalles_servicio = _build_detalles_servicio_from_form(form)

            # Métricas cliente
            db.session.add(s)
            c.total_solicitudes = (c.total_solicitudes or 0) + 1
            c.fecha_ultima_solicitud = datetime.utcnow()
            c.fecha_ultima_actividad = datetime.utcnow()

            db.session.commit()
            flash(f'Solicitud {nuevo_codigo} creada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('Conflicto de datos. Verifica los campos (códigos únicos, etc.).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al crear la solicitud.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear la solicitud.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        nuevo=True
    )


# ─────────────────────────────────────────────────────────────
# ADMIN: Editar solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="edit_solicitud", max_actions=35, window_sec=60)
def editar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminSolicitudForm(obj=s)

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    # ─────────────────────────────────────────
    # GET: pre-cargar campos
    # ─────────────────────────────────────────
    if request.method == 'GET':
        # Tipo de servicio
        if hasattr(form, 'tipo_servicio'):
            valid_ts = {code for code, _ in form.tipo_servicio.choices}
            if not s.tipo_servicio:
                if 'DOMESTICA_LIMPIEZA' in valid_ts:
                    form.tipo_servicio.data = 'DOMESTICA_LIMPIEZA'
            else:
                if s.tipo_servicio in valid_ts:
                    form.tipo_servicio.data = s.tipo_servicio

        # Tipo de lugar
        try:
            if hasattr(form, 'tipo_lugar') and hasattr(form, 'tipo_lugar_otro'):
                allowed_tl = _allowed_codes_from_choices(form.tipo_lugar.choices)
                if s.tipo_lugar and s.tipo_lugar in allowed_tl:
                    form.tipo_lugar.data = s.tipo_lugar
                    form.tipo_lugar_otro.data = ''
                else:
                    form.tipo_lugar.data = 'otro'
                    form.tipo_lugar_otro.data = (s.tipo_lugar or '').strip()
        except Exception:
            pass

        # Edad requerida
        if hasattr(form, 'edad_requerida'):
            selected_codes, otro_text = _split_edad_for_form(
                stored_list=s.edad_requerida,
                edad_choices=form.edad_requerida.choices
            )
            try:
                edad_codes = set(selected_codes or [])
                if (otro_text or '').strip():
                    allowed_edad = _allowed_codes_from_choices(form.edad_requerida.choices)
                    if 'otro' in allowed_edad:
                        edad_codes.add('otro')
                form.edad_requerida.data = list(edad_codes)
            except Exception:
                form.edad_requerida.data = selected_codes or []
            if hasattr(form, 'edad_otro'):
                form.edad_otro.data = (otro_text or '').strip()

        # Funciones
        if hasattr(form, 'funciones'):
            allowed_fun_codes = _allowed_codes_from_choices(form.funciones.choices)
            funs_guardadas = _clean_list(s.funciones)
            form.funciones.data = [f for f in funs_guardadas if f in allowed_fun_codes]

            extras = [f for f in funs_guardadas if f not in allowed_fun_codes and f != 'otro']

            base_otro = (getattr(s, 'funciones_otro', '') or '').strip()
            if hasattr(form, 'funciones_otro'):
                form.funciones_otro.data = (", ".join(extras) if extras else base_otro)

            try:
                if (form.funciones_otro.data or '').strip():
                    fun_codes = set(form.funciones.data or [])
                    if 'otro' in allowed_fun_codes:
                        fun_codes.add('otro')
                    form.funciones.data = list(fun_codes)
            except Exception:
                pass

        # Mascota / Áreas / Pasaje
        if hasattr(form, 'mascota'):
            form.mascota.data = (getattr(s, 'mascota', '') or '')
        if hasattr(form, 'areas_comunes'):
            form.areas_comunes.data = _clean_list(s.areas_comunes)
        if hasattr(form, 'area_otro'):
            form.area_otro.data = (getattr(s, 'area_otro', '') or '')
        try:
            if hasattr(form, 'areas_comunes') and hasattr(form, 'area_otro'):
                if (form.area_otro.data or '').strip():
                    allowed_areas = _allowed_codes_from_choices(form.areas_comunes.choices)
                    if 'otro' in allowed_areas:
                        area_codes = set(_clean_list(form.areas_comunes.data))
                        area_codes.add('otro')
                        form.areas_comunes.data = list(area_codes)
        except Exception:
            pass
        if hasattr(form, 'pasaje_aporte'):
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))

        # Detalles específicos (JSONB)
        _populate_form_detalles_from_solicitud(form, s)

    # ─────────────────────────────────────────
    # POST válido
    # ─────────────────────────────────────────
    if form.validate_on_submit():
        try:
            # Carga general
            form.populate_obj(s)

            # Sueldo (solo números): normaliza para evitar guardar "RD$", comas, etc.
            if hasattr(form, 'sueldo'):
                try:
                    s.sueldo = _norm_numeric_str(form.sueldo.data)
                except Exception:
                    pass

            # Tipo de servicio
            if hasattr(form, 'tipo_servicio'):
                s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None

            # Tipo de lugar
            s.tipo_lugar = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
            )

            # Edad requerida (LABELS)
            s.edad_requerida = _map_edad_choices(
                codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
            )

            # Mascota
            if hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            # Funciones
            selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
            extra_text    = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if 'otro' not in selected_codes:
                extra_text = ''
            if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                s.funciones = [c for c in selected_codes if c in valid_codes and c != 'otro']
            else:
                s.funciones = [c for c in selected_codes if c != 'otro']
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = extra_text or None

            # Áreas válidas
            if hasattr(form, 'areas_comunes'):
                s.areas_comunes = _normalize_areas_comunes_selected(
                    selected_vals=form.areas_comunes.data,
                    choices=form.areas_comunes.choices
                )

            # Área "otro"
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None

            # Pasaje
            if hasattr(form, 'pasaje_aporte'):
                s.pasaje_aporte = bool(form.pasaje_aporte.data)

            # Timestamp
            s.fecha_ultima_modificacion = datetime.utcnow()

            # Detalles específicos (JSONB)
            s.detalles_servicio = _build_detalles_servicio_from_form(form)

            db.session.commit()
            flash(f'Solicitud {s.codigo_solicitud} actualizada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('No se pudo actualizar por conflicto de datos (únicos/relaciones).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al actualizar la solicitud.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al actualizar la solicitud.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        nuevo=False
    )



# ─────────────────────────────────────────────────────────────
# Helpers: Autocomplete/Select de candidatas (para reemplazos, pagos, etc.)
# ─────────────────────────────────────────────────────────────

def _load_candidatas_choices(q: str, limit: int = 50):
    """Devuelve lista de tuples (id, label) para WTForms SelectField.

    Se usa en pantallas con barra de búsqueda por querystring `?q=...`.
    Busca por: nombre, cédula, código y teléfono.

    NOTA: Si `q` viene vacío, devolvemos [] para evitar cargar 50 candidatas sin necesidad.
    """
    q = (q or '').strip()
    if not q:
        return []

    like = f"%{q}%"

    candidatas = (
        Candidata.query
        .filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
                Candidata.numero_telefono.ilike(like),
            )
        )
        .order_by(Candidata.nombre_completo.asc())
        .limit(int(limit))
        .all()
    )

    choices = []
    for c in candidatas:
        nombre = (c.nombre_completo or '').strip()
        ced = (c.cedula or '').strip()
        tel = (c.numero_telefono or '').strip()

        extra = ""
        if ced and tel:
            extra = f" — {ced} — {tel}"
        elif ced:
            extra = f" — {ced}"
        elif tel:
            extra = f" — {tel}"

        label = f"{nombre}{extra}".strip() if nombre else f"ID {c.fila}{extra}".strip()
        choices.append((c.fila, label))

    return choices

# ─────────────────────────────────────────────────────────────
# Helpers de apoyo (dinero, choices)
# ─────────────────────────────────────────────────────────────
def _parse_money_to_decimal_str(raw: str, places: int = 2) -> str:
    """Convierte entradas humanas a string decimal normalizado con punto y N decimales.

    Acepta formatos comunes:
      - "RD$ 1,234.50", "$1200", "1200,50", "  5000  "
      - "1,500" (miles), "1.500" (miles), "1.500,50" (EU), "1,500.50" (US)

    Retorna string canónica: "1234.56".
    Lanza ValueError si no se puede parsear.
    """
    if raw is None:
        raise ValueError("Monto vacío")

    s = str(raw).strip()
    if not s:
        raise ValueError("Monto vacío")

    # quitar símbolos y espacios
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")

    # Caso mixto: tiene punto y coma
    if "." in s and "," in s:
        # Si la última coma está a la derecha del último punto -> coma es decimal (EU)
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            # Punto decimal, coma miles (US)
            s = s.replace(",", "")
    else:
        # Solo comas -> puede ser decimal con coma o miles con coma
        if "," in s:
            parts = s.split(",")
            if len(parts) > 2:
                # 1,234,567 -> miles
                s = "".join(parts)
            else:
                # Ambiguo: si hay 1-2 dígitos al final asumimos decimales
                if len(parts[-1]) in (1, 2):
                    s = s.replace(",", ".")
                else:
                    s = s.replace(",", "")

        # Solo puntos -> puede ser miles con punto o decimal con punto
        elif "." in s:
            parts = s.split(".")
            if len(parts) > 2:
                # 1.234.567,89 o 1.234.567 -> asumimos miles
                s = "".join(parts[:-1]) + "." + parts[-1]

    try:
        val = Decimal(s)
    except InvalidOperation:
        raise ValueError("Monto inválido")

    if val < 0:
        raise ValueError("Monto negativo no permitido")

    q = Decimal(10) ** -int(places)
    val = val.quantize(q)
    return f"{val:.{places}f}"

def _to_decimal_safe(value) -> Decimal:
    """Convierte un valor (None/Decimal/int/float/str) a Decimal(0.01) de forma segura.

    - Si el valor no se puede convertir, devuelve 0.00
    - Limpia strings raros (RD$, comas, espacios) y deja solo dígitos y punto.
    """
    if value is None:
        return Decimal('0.00')

    if isinstance(value, Decimal):
        return value.quantize(Decimal('0.01'))

    # si viene como int/float
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal('0.01'))

    # si viene string
    txt = str(value).strip()
    if not txt:
        return Decimal('0.00')

    # Dejar solo dígitos y punto (quitamos RD$, comas, letras, etc.)
    cleaned = ''.join(ch for ch in txt if ch.isdigit() or ch == '.')
    if cleaned.count('.') > 1:
        parts = cleaned.split('.')
        cleaned = parts[0] + '.' + ''.join(parts[1:])

    if cleaned in ('', '.'):
        return Decimal('0.00')

    try:
        return Decimal(cleaned).quantize(Decimal('0.01'))
    except InvalidOperation:
        return Decimal('0.00')



def _sum_decimal_fields(current_value, add_value_decimal: Decimal) -> Decimal:
    """Suma segura para campos Numeric/String mezclados.

    - current_value puede venir como None, Decimal, número o string viejo.
    - add_value_decimal debe venir como Decimal ya calculado.
    """
    actual = _to_decimal_safe(current_value)
    total = (actual + add_value_decimal).quantize(Decimal('0.01'))
    return total


def _clamp_decimal(value: Decimal, min_v: Decimal, max_v: Decimal) -> Decimal:
    """Limita un Decimal entre min_v y max_v (ambos inclusive)."""
    v = _to_decimal_safe(value)
    if v < min_v:
        return min_v
    if v > max_v:
        return max_v
    return v


def _percent_paid(monto_total, monto_pagado) -> Decimal:
    """Calcula porcentaje pagado (0–100) a partir de total vs pagado."""
    total = _to_decimal_safe(monto_total)
    pagado = _to_decimal_safe(monto_pagado)

    if total <= Decimal('0.00'):
        return Decimal('0.00')

    pct = (pagado / total) * Decimal('100.00')
    pct = _clamp_decimal(pct, Decimal('0.00'), Decimal('100.00'))
    return pct.quantize(Decimal('0.01'))

def _choice_codes(choices):
    """Devuelve set de códigos válidos de choices [(code,label), ...]."""
    out = set()
    for c in (choices or []):
        try:
            out.add(str(c[0]).strip())
        except Exception:
            try:
                out.add(str(c).strip())
            except Exception:
                pass
    return {x for x in out if x}

# ─────────────────────────────────────────────────────────────
# ADMIN: Eliminar solicitud (seguro)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="delete_solicitud", max_actions=10, window_sec=60)
def eliminar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # Reglas de negocio: no permitir borrar pagadas o con reemplazos
    if s.estado == 'pagada':
        flash('No puedes eliminar una solicitud pagada. Cancélala o revierte el pago primero.', 'warning')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))
    if getattr(s, 'reemplazos', None):
        if len(s.reemplazos) > 0:
            flash('No puedes eliminar la solicitud porque tiene reemplazos asociados.', 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    try:
        c = Cliente.query.get_or_404(cliente_id)
        db.session.delete(s)

        # Métricas del cliente
        c.total_solicitudes = max((c.total_solicitudes or 1) - 1, 0)
        c.fecha_ultima_actividad = datetime.utcnow()

        db.session.commit()
        flash('Solicitud eliminada.', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('No se pudo eliminar: existen relaciones asociadas (FK).', 'danger')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Error de base de datos al eliminar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al eliminar la solicitud.', 'danger')

    return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))


# ─────────────────────────────────────────────────────────────
# ADMIN: Gestionar plan (valida choices y abono OBLIGATORIO)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/plan', methods=['GET','POST'])
@login_required
@admin_required
@admin_action_limit(bucket="plan_abono", max_actions=25, window_sec=60)
def gestionar_plan(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminGestionPlanForm(obj=s)

    if form.validate_on_submit():
        try:
            # --- Validar tipo_plan contra choices si existen ---
            if hasattr(form, 'tipo_plan') and getattr(form.tipo_plan, "choices", None):
                allowed = _choice_codes(form.tipo_plan.choices)
                if str(form.tipo_plan.data) not in allowed:
                    flash('Tipo de plan inválido.', 'danger')
                    return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            s.tipo_plan = form.tipo_plan.data

            # --- Abono OBLIGATORIO + parseo robusto ---
            if not hasattr(form, 'abono'):
                flash('Falta el campo abono en el formulario.', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            raw_abono = (form.abono.data or '').strip()
            if not raw_abono:
                flash('El abono es obligatorio.', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            try:
                s_abono = _parse_money_to_decimal_str(raw_abono)  # '1500.00'
            except ValueError as e:
                flash(f'Abono inválido: {e}. Formatos válidos: 1500, 1,500, 1.500,50', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            # Guardar abono
            s.abono = s_abono

            # --- Estado ---
            # Guardamos el estado anterior para detectar reactivación real
            estado_anterior = (s.estado or '').strip().lower()

            # Reactivar SIEMPRE, aunque esté pagada o cancelada.
            s.estado = 'activa'
            s.fecha_cancelacion = None
            s.motivo_cancelacion = None

            # --- Timestamps ---
            now = datetime.utcnow()

            # ✅ Seguimiento:
            # Al guardar el ABONO/PLAN, refrescamos el inicio de seguimiento.
            # - Si ya tiene fecha_inicio_seguimiento, se ACTUALIZA a ahora.
            # - Si no tiene, se setea a ahora.
            if hasattr(s, 'fecha_inicio_seguimiento'):
                if getattr(s, 'fecha_inicio_seguimiento', None):
                    # Ya existía -> refrescar
                    s.fecha_inicio_seguimiento = now
                else:
                    # No existía -> iniciar
                    s.fecha_inicio_seguimiento = now

            s.fecha_ultima_actividad = now
            s.fecha_ultima_modificacion = now

            db.session.commit()
            flash('Plan y abono actualizados correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('Conflicto al guardar el plan (valores únicos/relaciones).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al guardar el plan.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al guardar el plan.', 'danger')

    return render_template(
        'admin/gestionar_plan.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )



# ─────────────────────────────────────────────────────────────
# ADMIN: Registrar pago (robusto y consistente)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/pago', methods=['GET', 'POST'])
@login_required
@admin_required
@admin_action_limit(bucket="pagos", max_actions=20, window_sec=60)
def registrar_pago(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminPagoForm()

    q = (request.args.get('q') or request.form.get('q') or '').strip()

    def _build_candidata_choices(search_text):
        query = Candidata.query
        if search_text:
            like = f"%{search_text}%"
            query = query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.codigo.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                )
            )

        candidatas = query.order_by(Candidata.nombre_completo.asc()).limit(50).all()
        choices = [(c.fila, c.nombre_completo) for c in candidatas]

        if s.candidata_id:
            cand_actual = Candidata.query.get(s.candidata_id)
            if cand_actual and cand_actual.fila not in [x[0] for x in choices]:
                choices.insert(0, (cand_actual.fila, cand_actual.nombre_completo))

        return choices

    form.candidata_id.choices = _build_candidata_choices(q)

    if request.method == 'GET' and s.candidata_id:
        form.candidata_id.data = s.candidata_id

    if form.validate_on_submit():

        if s.estado in ('cancelada', 'pagada'):
            flash('Esta solicitud no admite pagos.', 'warning')
            return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s, q=q)

        cand = Candidata.query.get(form.candidata_id.data)
        if not cand:
            flash('Candidata inválida.', 'danger')
            return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s, q=q)

        s.candidata_id = cand.fila

        # Monto pagado
        s.monto_pagado = _parse_money_to_decimal_str(form.monto_pagado.data)

        # Siempre calculamos el 25% si hay sueldo en la solicitud.
        # Si una candidata no acepta porcentaje, por requisito queda descalificada antes,
        # así que aquí no validamos esa columna.
        if s.sueldo:
            try:
                sueldo = Decimal(_parse_money_to_decimal_str(s.sueldo))
                monto_25 = (sueldo * Decimal('0.25')).quantize(Decimal('0.01'))

                # Guardamos el total (si existe el campo)
                # ✅ Si ya tenía un monto_total previo, lo acumulamos.
                if hasattr(cand, 'monto_total'):
                    try:
                        cand.monto_total = _sum_decimal_fields(getattr(cand, 'monto_total', None), sueldo)
                    except Exception:
                        cand.monto_total = sueldo

                # ✅ Guardar MONTO del 25% (en dinero), no el número 25.
                # Nota: si tu BD tenía un CHECK que obliga 0–100, ese CHECK debe ajustarse
                # para permitir montos (>= 0). En código, aquí guardamos el monto real.
                if hasattr(cand, 'porciento'):
                    try:
                        cand.porciento = _sum_decimal_fields(getattr(cand, 'porciento', None), monto_25)
                    except Exception:
                        cand.porciento = monto_25

                # Fecha de pago (si existe)
                if hasattr(cand, 'fecha_de_pago') and not getattr(cand, 'fecha_de_pago', None):
                    cand.fecha_de_pago = datetime.utcnow().date()

                db.session.add(cand)
            except Exception:
                # Si el sueldo viene raro, no rompemos el pago
                pass

        s.estado = 'pagada'
        s.fecha_ultima_modificacion = datetime.utcnow()

        try:
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            msg = str(getattr(e, "orig", e))
            # Caso: constraint viejo que obliga porciento entre 0 y 100
            if "chk_porciento" in msg:
                flash(
                    "Tu BD tiene un CHECK (chk_porciento) que obliga 'porciento' a estar entre 0 y 100. "
                    "Ahora estás guardando el MONTO del 25% (ej: 16000.00), por eso falla. "
                    "Solución: cambia ese constraint para permitir montos (porciento >= 0) o guarda 25 (porcentaje) en vez del monto.",
                    "danger"
                )
            else:
                flash('No se pudo registrar el pago por un conflicto de datos en la base de datos.', 'danger')
            return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s, q=q)

        flash('Pago registrado correctamente.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/registrar_pago.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        q=q
    )


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
@admin_action_limit(bucket="reemplazos", max_actions=15, window_sec=60)
def nuevo_reemplazo(s_id):
    sol = (
        Solicitud.query
        .options(joinedload(Solicitud.reemplazos), joinedload(Solicitud.candidata))
        .get_or_404(s_id)
    )

    form = AdminReemplazoForm()

    # ✅ SIEMPRE usar la candidata asignada originalmente a la solicitud (por relación)
    assigned_id = getattr(sol, 'candidata_id', None)

    # Si no hay candidata asignada, no se puede iniciar reemplazo
    if not assigned_id or not getattr(sol, 'candidata', None):
        flash(
            'Esta solicitud no tiene candidata asignada. Primero asigna una candidata (por pago/asignación) antes de iniciar un reemplazo.',
            'danger'
        )
        return redirect(url_for('admin.detalle_cliente', cliente_id=sol.cliente_id))

    # Prefill (por si tu form/template muestra campos)
    # No hay búsqueda ni selección manual: todo viene de sol.candidata
    try:
        if hasattr(form, 'candidata_old_id'):
            form.candidata_old_id.data = str(int(assigned_id))
    except Exception:
        pass

    try:
        if hasattr(form, 'candidata_old_name'):
            form.candidata_old_name.data = (sol.candidata.nombre_completo or '').strip()
    except Exception:
        pass

    if form.validate_on_submit():
        try:
            # ✅ Candidata anterior: SIEMPRE la asignada actual
            cand_old = sol.candidata
            if not cand_old:
                flash('No se encontró la candidata asignada a esta solicitud.', 'danger')
                return redirect(url_for('admin.detalle_cliente', cliente_id=sol.cliente_id))

            r = Reemplazo(
                solicitud_id=sol.id,
                candidata_old_id=cand_old.fila,
                motivo_fallo=(form.motivo_fallo.data or '').strip(),
            )

            ahora = datetime.utcnow()
            r.fecha_fallo = ahora
            r.fecha_inicio_reemplazo = ahora
            r.oportunidad_nueva = True

            sol.estado = 'reemplazo'
            sol.fecha_ultima_actividad = ahora
            sol.fecha_ultima_modificacion = ahora

            db.session.add(r)
            db.session.commit()

            flash('Reemplazo iniciado correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=sol.cliente_id))

        except Exception:
            db.session.rollback()
            flash('Error al iniciar el reemplazo.', 'danger')

    # 👇 Ya no se manda "q" porque eliminamos búsqueda
    return render_template('admin/reemplazo_inicio.html', form=form, solicitud=sol)


@admin_bp.route(
    '/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/finalizar',
    methods=['GET', 'POST']
)
@login_required
@admin_required
def finalizar_reemplazo(s_id, reemplazo_id):
    s = (
        Solicitud.query
        .options(
            joinedload(Solicitud.reemplazos),
            joinedload(Solicitud.candidata)
        )
        .get_or_404(s_id)
    )

    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    form = AdminReemplazoFinForm()

    # ✅ Igual que PAGO
    q = (request.args.get('q') or request.form.get('q') or '').strip()

    # ✅ Detectar el field real que existe en el form
    if hasattr(form, 'domestica_id'):
        pick_field = form.domestica_id
        pick_name = 'domestica_id'
    elif hasattr(form, 'candidata_new_id'):
        pick_field = form.candidata_new_id
        pick_name = 'candidata_new_id'
    elif hasattr(form, 'candidata_id'):
        pick_field = form.candidata_id
        pick_name = 'candidata_id'
    else:
        flash('Error: el formulario no tiene un campo para seleccionar candidata.', 'danger')
        return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

    def _query_candidatas(search_text: str):
        # ✅ Si no hay búsqueda, NO cargamos nada
        if not search_text:
            return []

        like = f"%{search_text}%"
        return (
            Candidata.query
            .filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.codigo.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                )
            )
            .order_by(Candidata.nombre_completo.asc())
            .limit(50)
            .all()
        )

    def _build_choices_from_list(items):
        """✅ Para SelectField(coerce=int): value SIEMPRE int (nunca '' / None)."""
        out = []
        for c in items:
            nombre = (c.nombre_completo or '').strip()
            ced = (c.cedula or '').strip()
            tel = (c.numero_telefono or '').strip()

            extra = ""
            if ced and tel:
                extra = f" — {ced} — {tel}"
            elif ced:
                extra = f" — {ced}"
            elif tel:
                extra = f" — {tel}"

            label = f"{nombre}{extra}".strip() if nombre else f"ID {c.fila}{extra}".strip()

            try:
                out.append((int(c.fila), label))
            except Exception:
                continue

        return out

    # ✅ RESULTADOS (para tabla) + CHOICES (para select)
    candidatas = _query_candidatas(q)
    choices = _build_choices_from_list(candidatas)

    # ✅ Si ya hay candidata guardada en el reemplazo, subirla arriba (aunque no esté en búsqueda)
    cand_actual_id = getattr(r, 'candidata_new_id', None)
    try:
        cand_actual_id_int = int(cand_actual_id) if cand_actual_id else None
    except Exception:
        cand_actual_id_int = None

    if cand_actual_id_int:
        cand_actual = Candidata.query.get(cand_actual_id_int)
        if cand_actual:
            nombre = (cand_actual.nombre_completo or '').strip()
            ced = (cand_actual.cedula or '').strip()
            tel = (cand_actual.numero_telefono or '').strip()

            extra = ""
            if ced and tel:
                extra = f" — {ced} — {tel}"
            elif ced:
                extra = f" — {ced}"
            elif tel:
                extra = f" — {tel}"

            top = (
                int(cand_actual.fila),
                f"{nombre}{extra}".strip() if nombre else f"ID {cand_actual.fila}{extra}".strip()
            )

            ids = [x[0] for x in choices]
            if top[0] in ids:
                choices = [top] + [x for x in choices if x[0] != top[0]]
            else:
                choices.insert(0, top)

    # ✅ Placeholder arriba (OJO: value int=0, NO '')
    pick_field.choices = [(0, '— Selecciona una doméstica —')] + choices

    # ✅ GET: precargar si ya existe candidata_new_id en el reemplazo
    if request.method == 'GET':
        if cand_actual_id_int:
            try:
                pick_field.data = int(cand_actual_id_int)
            except Exception:
                pick_field.data = 0
        else:
            pick_field.data = 0

    if form.validate_on_submit():
        try:
            # ✅ leer id seleccionado (int)
            try:
                cand_new_id = int(pick_field.data or 0)
            except Exception:
                cand_new_id = 0

            if cand_new_id <= 0:
                flash('Debes seleccionar la nueva candidata.', 'danger')
                return render_template(
                    'admin/reemplazo_fin.html',
                    form=form,
                    solicitud=s,
                    reemplazo=r,
                    q=q,
                    pick_name=pick_name,
                    candidatas=candidatas
                )

            cand_new = Candidata.query.get(cand_new_id)
            if not cand_new:
                flash('La candidata seleccionada no existe.', 'danger')
                return render_template(
                    'admin/reemplazo_fin.html',
                    form=form,
                    solicitud=s,
                    reemplazo=r,
                    q=q,
                    pick_name=pick_name,
                    candidatas=candidatas
                )

            ahora = datetime.utcnow()

            # Guardar reemplazo
            r.candidata_new_id = cand_new.fila

            if hasattr(form, 'nota_adicional'):
                r.nota_adicional = (form.nota_adicional.data or '').strip() or None

            if hasattr(r, 'fecha_fin_reemplazo'):
                r.fecha_fin_reemplazo = ahora
            elif hasattr(r, 'fecha_fin'):
                r.fecha_fin = ahora

            # Reasignar solicitud (mantener pagada)
            s.candidata_id = cand_new.fila
            s.estado = 'pagada'

            # ✅ Timestamps (solo si existen en tu modelo)
            if hasattr(s, 'fecha_ultima_actividad'):
                s.fecha_ultima_actividad = ahora
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = ahora

            # Porcentaje (MISMA lógica que PAGO)
            if getattr(s, 'sueldo', None):
                try:
                    sueldo = Decimal(_parse_money_to_decimal_str(s.sueldo))
                    monto_25 = (sueldo * Decimal('0.25')).quantize(Decimal('0.01'))

                    # ✅ Si ya tenía un monto_total previo, lo acumulamos.
                    if hasattr(cand_new, 'monto_total'):
                        try:
                            cand_new.monto_total = _sum_decimal_fields(getattr(cand_new, 'monto_total', None), sueldo)
                        except Exception:
                            cand_new.monto_total = sueldo

                    # ✅ Guardar MONTO del 25% (en dinero), igual que en PAGO.
                    if hasattr(cand_new, 'porciento'):
                        try:
                            cand_new.porciento = _sum_decimal_fields(getattr(cand_new, 'porciento', None), monto_25)
                        except Exception:
                            cand_new.porciento = monto_25

                    # Fecha de pago (si existe)
                    if hasattr(cand_new, 'fecha_de_pago') and not getattr(cand_new, 'fecha_de_pago', None):
                        cand_new.fecha_de_pago = datetime.utcnow().date()

                    if hasattr(cand_new, 'fecha_ultima_modificacion'):
                        cand_new.fecha_ultima_modificacion = ahora

                    db.session.add(cand_new)
                except Exception:
                    # Si el sueldo viene raro, no rompemos el flujo
                    pass

            db.session.commit()
            flash('Reemplazo finalizado correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        except Exception as e:
            db.session.rollback()
            # ✅ Mostrar el error real en terminal para poder corregirlo de una vez
            try:
                import traceback
                print('ERROR finalizar_reemplazo:', repr(e))
                traceback.print_exc()
            except Exception:
                pass
            flash('Error al finalizar el reemplazo.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/reemplazo_fin.html',
        form=form,
        solicitud=s,
        reemplazo=r,
        q=q,
        pick_name=pick_name,
        candidatas=candidatas
    )

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>')
@login_required
@admin_required
def detalle_solicitud(cliente_id, id):
    # Carga completa para evitar N+1 en plantilla
    s = (Solicitud.query
         .options(
             joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new),
             joinedload(Solicitud.candidata)
         )
         .filter_by(id=id, cliente_id=cliente_id)
         .first_or_404())

    # Historial de envíos (inicial + reemplazos válidos)
    envios = []
    if s.candidata:
        envios.append({
            'tipo':     'Envío inicial',
            'candidata': s.candidata,
            'fecha':     s.fecha_solicitud
        })

    reemplazos_ordenados = sorted(list(s.reemplazos or []),
                                  key=lambda r: r.fecha_inicio_reemplazo or r.created_at or datetime.min)
    for idx, r in enumerate(reemplazos_ordenados, start=1):
        if r.candidata_new:
            envios.append({
                'tipo':     f'Reemplazo {idx}',
                'candidata': r.candidata_new,
                'fecha':     r.fecha_inicio_reemplazo or r.created_at
            })

    # Cancelaciones
    cancelaciones = []
    if s.estado == 'cancelada' and s.fecha_cancelacion:
        cancelaciones.append({
            'fecha':  s.fecha_cancelacion,
            'motivo': s.motivo_cancelacion
        })

    # 👉 Resumen listo para enviar al cliente (helper que ya te di antes)
    resumen_cliente = build_resumen_cliente_solicitud(s)

    return render_template(
        'admin/solicitud_detail.html',
        solicitud      = s,
        envios         = envios,
        cancelaciones  = cancelaciones,
        reemplazos     = reemplazos_ordenados,
        resumen_cliente=resumen_cliente
    )

from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload
from sqlalchemy import func

@admin_bp.route('/solicitudes/prioridad')
@login_required
@admin_required
def solicitudes_prioridad():
    """
    Lista solicitudes prioritarias basadas ÚNICAMENTE en `fecha_inicio_seguimiento`.

    ✅ Regla clave (SQL):
      - estado in ('proceso', 'activa', 'reemplazo')
      - fecha_inicio_seguimiento IS NOT NULL
      - fecha_inicio_seguimiento <= (UTC ahora - dias)

    Extra:
      - Por defecto NO muestra solicitudes con candidata asignada (porque ya no son “sin candidata”)
        Puedes verlas con ?incluye_asignadas=1

    Niveles (para tu template: s.nivel_prioridad):
      - media:  dias_en_seguimiento >= dias_media (default 7)
      - alta:   dias_en_seguimiento >= dias_alta  (default 10)
      - critica:dias_en_seguimiento >= dias_critica (default 14)

    Params:
      - q=...         búsqueda
      - estado=...    filtra por estado (si es válido)
      - dias=7        umbral mínimo para entrar en “prioritarias”
      - dias_media=7  umbral badge MEDIA
      - dias_alta=10  umbral badge ALTA
      - dias_critica=14 umbral badge CRITICA
      - page=1, per_page=50
      - incluye_asignadas=1  para incluir solicitudes con candidata asignada
    """

    # -------------------------
    # View model (clave para no romper con @property sin setter)
    # -------------------------
    class _SolicitudVM:
        __slots__ = ("_s", "dias_en_seguimiento", "nivel_prioridad", "es_prioritaria")

        def __init__(self, s, dias: int, nivel: str, es: bool):
            self._s = s
            self.dias_en_seguimiento = dias
            self.nivel_prioridad = nivel
            self.es_prioritaria = es

        def __getattr__(self, name):
            return getattr(self._s, name)

    # -------------------------
    # Params
    # -------------------------
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().lower()

    def _as_int(name, default, lo=None, hi=None):
        try:
            v = int(request.args.get(name, default) or default)
        except Exception:
            v = default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    dias = _as_int('dias', 7, lo=1, hi=90)

    dias_media = _as_int('dias_media', 7, lo=1, hi=365)
    dias_alta = _as_int('dias_alta', 10, lo=1, hi=365)
    dias_critica = _as_int('dias_critica', 14, lo=1, hi=365)

    # coherencia (critica >= alta >= media)
    dias_media = max(1, dias_media)
    dias_alta = max(dias_media, dias_alta)
    dias_critica = max(dias_alta, dias_critica)

    page = _as_int('page', 1, lo=1, hi=10_000)
    per_page = _as_int('per_page', 50, lo=10, hi=200)

    incluye_asignadas = (request.args.get('incluye_asignadas') or '').strip() in ('1', 'true', 'True', 'yes', 'si')

    ahora = datetime.utcnow()
    limite_fecha = ahora - timedelta(days=dias)

    # -------------------------
    # Estados permitidos
    # -------------------------
    allowed_states = {'proceso', 'activa', 'reemplazo'}
    estados_filtrados = [estado] if (estado and estado in allowed_states) else list(allowed_states)

    # -------------------------
    # Query base (SOLO fecha_inicio_seguimiento)
    # -------------------------
    query = (
        Solicitud.query
        .options(
            joinedload(Solicitud.cliente),
            joinedload(Solicitud.candidata),
        )
        .filter(
            Solicitud.estado.in_(estados_filtrados),
            Solicitud.fecha_inicio_seguimiento.isnot(None),
            Solicitud.fecha_inicio_seguimiento <= limite_fecha,
        )
    )

    # Por defecto: solo “sin candidata asignada”
    if not incluye_asignadas:
        if hasattr(Solicitud, 'candidata_id'):
            query = query.filter(or_(Solicitud.candidata_id.is_(None), Solicitud.candidata_id == 0))

    # -------------------------
    # Búsqueda
    # -------------------------
    if q:
        like = f"%{q}%"
        filtros = []

        for attr in ('codigo_solicitud', 'ciudad_sector', 'rutas_cercanas', 'modalidad_trabajo', 'horario'):
            if hasattr(Solicitud, attr):
                filtros.append(getattr(Solicitud, attr).ilike(like))

        try:
            if hasattr(Cliente, 'nombre_completo'):
                filtros.append(Cliente.nombre_completo.ilike(like))
            if hasattr(Cliente, 'codigo'):
                filtros.append(Cliente.codigo.ilike(like))
            if hasattr(Cliente, 'telefono'):
                filtros.append(Cliente.telefono.ilike(like))
        except Exception:
            pass

        try:
            if hasattr(Candidata, 'nombre_completo'):
                filtros.append(Candidata.nombre_completo.ilike(like))
            if hasattr(Candidata, 'cedula'):
                filtros.append(Candidata.cedula.ilike(like))
            if hasattr(Candidata, 'codigo'):
                filtros.append(Candidata.codigo.ilike(like))
            if hasattr(Candidata, 'numero_telefono'):
                filtros.append(Candidata.numero_telefono.ilike(like))
        except Exception:
            pass

        if filtros:
            try:
                query = query.join(Cliente, Solicitud.cliente_id == Cliente.id)
            except Exception:
                pass
            try:
                if hasattr(Solicitud, 'candidata_id') and hasattr(Candidata, 'fila'):
                    query = query.outerjoin(Candidata, Solicitud.candidata_id == Candidata.fila)
            except Exception:
                pass

            query = query.filter(or_(*filtros))

    # Orden: más antiguas primero (prioridad real)
    query = query.order_by(Solicitud.fecha_inicio_seguimiento.asc(), Solicitud.id.asc())

    total = query.count()
    solicitudes = (
        query.offset((page - 1) * per_page)
             .limit(per_page)
             .all()
    )

    # -------------------------
    # Helpers (para tu template)
    # -------------------------
    def _to_dt(d):
        if not d:
            return None
        if isinstance(d, datetime):
            return d
        try:
            return datetime(d.year, d.month, d.day)
        except Exception:
            return None

    def _dias_en_seguimiento(s) -> int:
        dt = _to_dt(getattr(s, 'fecha_inicio_seguimiento', None))
        if not dt:
            return 0
        return max(0, int((ahora - dt).total_seconds() // 86400))

    def _nivel_por_dias(n: int) -> str:
        if n >= dias_critica:
            return 'critica'
        if n >= dias_alta:
            return 'alta'
        if n >= dias_media:
            return 'media'
        return 'normal'

    wrapped = []
    for s in (solicitudes or []):
        n = _dias_en_seguimiento(s)
        nivel = _nivel_por_dias(n)
        es = n >= dias_media
        wrapped.append(_SolicitudVM(s, dias=n, nivel=nivel, es=es))

    return render_template(
        'admin/solicitudes_prioridad.html',
        solicitudes=wrapped,
        q=q,
        estado=estado,
        dias=dias,
        dias_media=dias_media,
        dias_alta=dias_alta,
        dias_critica=dias_critica,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total,
        incluye_asignadas=incluye_asignadas
    )



# ============================================================
#                                   API
# ============================================================
from flask import request, jsonify
from sqlalchemy import or_, and_

@admin_bp.route('/api/candidatas', methods=['GET'])
@login_required
@admin_required
def api_candidatas():
    """
    API para autocomplete de candidatas.

    - Si no hay 'q', devuelve hasta 50 candidatas ordenadas por nombre.
    - Respuesta: {"results":[{"id":..., "text":...}, ...]}

    - Busca por nombre, cédula, teléfono y código (coincidencia parcial, case-insensitive)
    - Soporta múltiples palabras/tokens
    - Devuelve texto: "Nombre — Cédula — Teléfono" (según aplique)

    IMPORTANTE:
    - Fuerza NO-CACHE para evitar respuestas 304 que rompen el fetch/json en el front.
    """
    term = (request.args.get('q') or '').strip()

    query = Candidata.query

    def _norm_tokens(s: str):
        s = (s or '').strip()
        if not s:
            return []
        return [t for t in s.split() if t]

    def _label(c: Candidata) -> str:
        nombre = (c.nombre_completo or '').strip()
        ced = (c.cedula or '').strip()
        tel = (c.numero_telefono or '').strip()
        cod = (c.codigo or '').strip()

        extra_parts = []
        if ced:
            extra_parts.append(ced)
        if tel:
            extra_parts.append(tel)
        # Si quieres mostrar el código también, descomenta:
        # if cod:
        #     extra_parts.append(cod)

        extra = ""
        if extra_parts:
            extra = " — " + " — ".join(extra_parts)

        if nombre:
            return f"{nombre}{extra}".strip()

        base = f"ID {c.fila}"
        return f"{base}{extra}".strip()

    if term:
        tokens = _norm_tokens(term)

        filters = []
        for t in tokens:
            like = f"%{t}%"
            filters.append(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                    Candidata.codigo.ilike(like),
                )
            )

        query = query.filter(and_(*filters))

    candidatas = (
        query
        .order_by(Candidata.nombre_completo.asc(), Candidata.fila.asc())
        .limit(50)
        .all()
    )

    results = [{"id": int(c.fila), "text": _label(c)} for c in candidatas]

    resp = jsonify({"results": results})

    # ✅ Anti-cache duro (evita 304 y respuestas “sin body”)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"

    return resp


# ============================================================
#                           LISTADO / CONTADORES
# ============================================================
@admin_bp.route('/solicitudes')
@login_required
@staff_required
def listar_solicitudes():
    """
    Muestra contadores clave:
    - En proceso
    - Copiables (activa/reemplazo) cuya última copia fue antes del inicio del día UTC actual
    """
    proc_count = Solicitud.query.filter_by(estado='proceso').count()

    # Consistencia UTC para "copiable hasta hoy"
    start_utc, _ = _today_utc_bounds()
    copiable_count = (Solicitud.query
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                Solicitud.last_copiado_at < start_utc
            )
        )
        .count()
    )

    return render_template(
        'admin/solicitudes_list.html',
        proc_count=proc_count,
        copiable_count=copiable_count
    )


# ============================================================
#                               RESUMEN KPI
# ============================================================
@admin_bp.route('/solicitudes/resumen')
@login_required
@admin_required
def resumen_solicitudes():
    """
    KPIs con fechas coherentes en UTC y casteo numérico robusto.
    Requiere Postgres (usa date_trunc/extract). Si usas otro motor, adaptar funciones.
    """
    # Bordes UTC para hoy/semana/mes
    hoy = datetime.utcnow().date()
    week_start = hoy - timedelta(days=hoy.weekday())
    month_start = date(hoy.year, hoy.month, 1)

    # — Totales y estados —
    total_sol    = Solicitud.query.count()
    proc_count   = Solicitud.query.filter_by(estado='proceso').count()
    act_count    = Solicitud.query.filter_by(estado='activa').count()
    pag_count    = Solicitud.query.filter_by(estado='pagada').count()
    cancel_count = Solicitud.query.filter_by(estado='cancelada').count()
    repl_count   = Solicitud.query.filter_by(estado='reemplazo').count()

    # — Tasas —
    conversion_rate  = (pag_count    / total_sol * 100) if total_sol else 0
    replacement_rate = (repl_count   / total_sol * 100) if total_sol else 0
    abandon_rate     = (cancel_count / total_sol * 100) if total_sol else 0

    # — Promedios de tiempo (en días) —
    # Promedio publicación (last_copiado_at - fecha_solicitud)
    avg_pub_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.last_copiado_at - Solicitud.fecha_solicitud))
    ).filter(Solicitud.last_copiado_at.isnot(None)).scalar()) or 0
    avg_pub_days = avg_pub_secs / 86400

    # Promedio hasta pago (fecha_ultima_modificacion - fecha_solicitud) solo pagadas
    avg_pay_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.fecha_ultima_modificacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.estado == 'pagada').scalar()) or 0
    avg_pay_days = avg_pay_secs / 86400

    # Promedio hasta cancelación
    avg_cancel_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.fecha_cancelacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.fecha_cancelacion.isnot(None)).scalar()) or 0
    avg_cancel_days = avg_cancel_secs / 86400

    # — Top 5 ciudades (ignora NULL/'' para calidad de dato) —
    top_cities = (
        db.session.query(
            Solicitud.ciudad_sector,
            func.count(Solicitud.id).label('cnt')
        )
        .filter(Solicitud.ciudad_sector.isnot(None))
        .filter(func.length(func.trim(Solicitud.ciudad_sector)) > 0)
        .group_by(Solicitud.ciudad_sector)
        .order_by(desc('cnt'))
        .limit(5)
        .all()
    )

    # — Distribución por modalidad de trabajo —
    modality_dist = (
        db.session.query(
            Solicitud.modalidad_trabajo,
            func.count(Solicitud.id)
        )
        .group_by(Solicitud.modalidad_trabajo)
        .all()
    )

    # — Backlog: en proceso >7 días —
    backlog_threshold_days = 7
    backlog_alert = (
        Solicitud.query
        .filter_by(estado='proceso')
        .filter(Solicitud.fecha_solicitud < _now_utc() - timedelta(days=backlog_threshold_days))
        .count()
    )

    # — Tendencias (semanal/mensual) —
    trend_new_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_solicitud).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )
    trend_new_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_solicitud).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )

    trend_paid_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_paid_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )

    trend_cancel_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_cancel_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )

    # Bordes para filtros por periodo (UTC)
    start_today_utc, _ = _today_utc_bounds()
    start_week_utc = datetime(week_start.year, week_start.month, week_start.day)
    start_month_utc = datetime(month_start.year, month_start.month, month_start.day)

    # — Órdenes realizadas (fecha_solicitud) —
    orders_today = Solicitud.query.filter(
        Solicitud.fecha_solicitud >= start_today_utc,
        Solicitud.fecha_solicitud < start_today_utc + timedelta(days=1)
    ).count()
    orders_week  = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_week_utc).count()
    orders_month = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_month_utc).count()

    # — Publicadas (copias) —
    daily_copy   = Solicitud.query.filter(
        Solicitud.last_copiado_at >= start_today_utc,
        Solicitud.last_copiado_at < start_today_utc + timedelta(days=1)
    ).count()
    weekly_copy  = Solicitud.query.filter(Solicitud.last_copiado_at >= start_week_utc).count()
    monthly_copy = Solicitud.query.filter(Solicitud.last_copiado_at >= start_month_utc).count()

    # — Pagos por periodo —
    daily_paid   = (Solicitud.query.filter_by(estado='pagada')
                    .filter(
                        Solicitud.fecha_ultima_modificacion >= start_today_utc,
                        Solicitud.fecha_ultima_modificacion < start_today_utc + timedelta(days=1)
                    ).count())
    weekly_paid  = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_paid = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Cancelaciones por periodo —
    daily_cancel   = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(
                          Solicitud.fecha_cancelacion >= start_today_utc,
                          Solicitud.fecha_cancelacion < start_today_utc + timedelta(days=1)
                      ).count())
    weekly_cancel  = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_week_utc).count())
    monthly_cancel = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_month_utc).count())

    # — Reemplazos por periodo (usa fecha_ultima_modificacion como proxy de cambio) —
    weekly_repl  = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_repl = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Estadísticas mensuales de ingreso (pagadas) —
    # NOTA: con el monto guardado en formato canónico "1234.56",
    # el casteo directo a NUMERIC es seguro.
    stats_mensual = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_solicitud).label('mes'),
            func.count(Solicitud.id).label('cantidad'),
            func.sum(cast(Solicitud.monto_pagado, Numeric(12, 2))).label('total_pagado')
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('mes').order_by('mes')
        .all()
    )

    return render_template(
        'admin/solicitudes_resumen.html',
        # Totales y estados
        total_sol=total_sol,
        proc_count=proc_count,
        act_count=act_count,
        pag_count=pag_count,
        cancel_count=cancel_count,
        repl_count=repl_count,
        # Tasas y promedios
        conversion_rate=conversion_rate,
        replacement_rate=replacement_rate,
        abandon_rate=abandon_rate,
        avg_pub_days=avg_pub_days,
        avg_pay_days=avg_pay_days,
        avg_cancel_days=avg_cancel_days,
        # Top y distribución
        top_cities=top_cities,
        modality_dist=modality_dist,
        backlog_threshold_days=backlog_threshold_days,
        backlog_alert=backlog_alert,
        # Tendencias
        trend_new_weekly=trend_new_weekly,
        trend_new_monthly=trend_new_monthly,
        trend_paid_weekly=trend_paid_weekly,
        trend_paid_monthly=trend_paid_monthly,
        trend_cancel_weekly=trend_cancel_weekly,
        trend_cancel_monthly=trend_cancel_monthly,
        # Órdenes realizadas
        orders_today=orders_today,
        orders_week=orders_week,
        orders_month=orders_month,
        # Publicadas (copias)
        daily_copy=daily_copy,
        weekly_copy=weekly_copy,
        monthly_copy=monthly_copy,
        # Pagos
        daily_paid=daily_paid,
        weekly_paid=weekly_paid,
        monthly_paid=monthly_paid,
        # Cancelaciones
        daily_cancel=daily_cancel,
        weekly_cancel=weekly_cancel,
        monthly_cancel=monthly_cancel,
        # Reemplazos
        weekly_repl=weekly_repl,
        monthly_repl=monthly_repl,
        # Ingreso mensual
        stats_mensual=stats_mensual
    )



# =============================================================================
#                     COPIAR SOLICITUDES (LISTA + POST) — ROBUSTO
# =============================================================================
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, desc, cast
from sqlalchemy.sql import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
import json
import re
from decimal import Decimal, InvalidOperation

# ──────────────────────────────────────────────────────────────────────────────
# AREAS_COMUNES_CHOICES centralizado (con fallback)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from .routes import AREAS_COMUNES_CHOICES  # type: ignore
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'), ('comedor', 'Comedor'), ('cocina', 'Cocina'),
        ('salon_juegos', 'Salón de juegos'), ('terraza', 'Terraza'),
        ('jardin', 'Jardín'), ('estudio', 'Estudio'), ('patio', 'Patio'),
        ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'), ('otro', 'Otro'),
    ]
AREAS_MAP = {k: v for k, v in AREAS_COMUNES_CHOICES}

# --------------------------- HELPERS SEGUROS ---------------------------------

def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    try:
        return str(v).strip()
    except Exception:
        return str(v)

def _to_naive_utc(dt):
    if dt is None:
        return None
    try:
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return dt

def _utc_day_bounds(dt: datetime | None = None):
    base = (dt or datetime.utcnow()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

def _safe_join(parts, sep="\n"):
    out = []
    for p in parts or []:
        s = _s(p)
        if s:
            out.append(s)
    return sep.join(out)

def _first_nonempty_attr(obj, names: list[str], default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            s = _s(v)
            if s:
                return s
    return default

def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [_s(x) for x in v if _s(x)]
    if isinstance(v, dict):
        return [_s(x) for x in v.values() if _s(x)]
    if isinstance(v, str):
        txt = v.strip()
        if not txt:
            return []
        try:
            parsed = json.loads(txt)
            return _as_list(parsed)
        except Exception:
            if "," in txt:
                return [_s(x) for x in txt.split(",") if _s(x)]
            if ";" in txt:
                return [_s(x) for x in txt.split(";") if _s(x)]
            return [txt]
    return [_s(v)]

def _unique_keep_order(seq):
    """Devuelve únicos preservando el orden de aparición."""
    seen = set()
    out = []
    for x in seq or []:
        sx = _s(x)
        if not sx:
            continue
        if sx in seen:
            continue
        seen.add(sx)
        out.append(sx)
    return out

def _fmt_banos(val) -> str:
    if val is None:
        return ""
    s = _s(val).lower().replace("½", ".5").replace(" 1/2", ".5").replace("1/2", ".5")
    try:
        x = float(s) if any(ch.isdigit() for ch in s) else None
        if x is None:
            return _s(val)
        if abs(x - int(x)) < 1e-9:
            return str(int(x))
        return str(x)
    except Exception:
        return _s(val)

def _norm_area(a: str) -> str:
    k = _s(a).lower()
    if k in AREAS_MAP:
        return AREAS_MAP[k]
    alias = {
        "balcon": "Balcón", "balcón": "Balcón",
        "lavado": "Lavado", "terraza": "Terraza",
        "jardin": "Jardín", "salon_juegos": "Salón de juegos",
    }
    if k in alias:
        return alias[k]
    return a.strip().title()

def _fmt_codigo_humano(codigo: str) -> str:
    c = (codigo or "").strip()
    if not c:
        return ""
    if "-" in c:
        left, right = c.split("-", 1)
    else:
        left, right = c, ""
    try:
        digits = re.findall(r"(\d+)", left)
        if digits:
            n_str = digits[-1]
            n = int(n_str)
            left_fmt = left[: left.rfind(n_str)] + f"{n:,}"
        else:
            left_fmt = left
    except Exception:
        left_fmt = left
    return f"{left_fmt}-{right}" if right else left_fmt

def _format_money_usd(raw) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")
    us_pattern = r"^\d{1,3}(,\d{3})+(\.\d+)?$"
    eu_pattern = r"^\d{1,3}(\.\d{3})+(,\d+)?$"
    plain_digits = r"^\d+$"
    try:
        if re.match(us_pattern, s):
            num = s.replace(",", "")
            val = Decimal(num)
        elif re.match(eu_pattern, s):
            num = s.replace(".", "").replace(",", ".")
            val = Decimal(num)
        elif re.match(plain_digits, s):
            val = Decimal(s)
        else:
            if "," in s and "." not in s and re.match(r"^\d{1,3}(,\d{3})+$", s):
                val = Decimal(s.replace(",", ""))
            else:
                if "," in s and "." not in s and s.count(",") == 1:
                    val = Decimal(s.replace(",", "."))
                else:
                    val = Decimal(s.replace(",", ""))
        if val == val.to_integral():
            return f"${int(val):,}"
        return f"${val:,.2f}"
    except Exception:
        return f"${s}"

# ------------------------------ RUTAS ----------------------------------------

# RUTAS ADMIN – copiar solicitudes (con nota_cliente al final si existe)

# Helper específico para formatear el código de la solicitud
# ------------------------------ RUTAS ----------------------------------------

# RUTAS ADMIN – copiar solicitudes (con nota_cliente al final si existe)

# Helper específico para formatear el código de la solicitud
def _fmt_codigo_solicitud(codigo: str) -> str:
    """
    Formatea solo el tramo numérico final del código si:
      - NO tiene ya comas ni puntos (es decir, no fue formateado antes).
    Ejemplos:
      'SOL-1000'  -> 'SOL-1,000'
      '1000'      -> '1,000'
      'SOL-1,333' -> 'SOL-1,333'  (no se toca)
      '2,005'     -> '2,005'      (no se toca, evita el bug 2,5)
    """
    c = (codigo or "").strip()
    if not c:
        return ""

    # Si ya tiene coma o punto, asumimos que el usuario ya le dio el formato que quiere
    if "," in c or "." in c:
        return c

    # Buscar el último bloque de dígitos en el string
    m = re.search(r"(\d+)(?!.*\d)", c)
    if not m:
        # No hay números, devuelve tal cual
        return c

    n_str = m.group(1)
    try:
        n = int(n_str)
    except ValueError:
        return c

    # Formatear con separador de miles
    formatted = f"{n:,}"  # 1000 -> '1,000'
    # Reconstruir el código con el tramo numérico formateado
    return c[:m.start(1)] + formatted + c[m.end(1):]


@admin_bp.route('/solicitudes/copiar')
@login_required
@staff_required
def copiar_solicitudes():
    """
    Lista solicitudes copiables y arma el texto final:
    - Modalidad/Hogar sin prefijos fijos.
    - Mascotas solo si hay.
    - Líneas en blanco entre bloques.
    - Funciones en el MISMO ORDEN seleccionado (y 'otro' al final si aplica).
    - Agrega detalles extras según el tipo (niñera / enfermera / chofer).
    """
    q = _s(request.args.get('q'))

    # Paginación robusta
    try:
        page = int(request.args.get('page', 1) or 1)
    except Exception:
        page = 1
    page = max(1, page)

    try:
        per_page = int(request.args.get('per_page', 50) or 50)
    except Exception:
        per_page = 50
    per_page = max(10, min(per_page, 200))

    start_utc, _ = _utc_day_bounds()

    base_q = (
        Solicitud.query
        .options(
            joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new)
        )
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(or_(
            Solicitud.last_copiado_at.is_(None),
            Solicitud.last_copiado_at < start_utc
        ))
    )

    if q:
        like = f"%{q}%"
        filtros = []
        for col in (
            Solicitud.ciudad_sector,
            Solicitud.codigo_solicitud,
            Solicitud.rutas_cercanas,
            Solicitud.modalidad_trabajo
        ):
            filtros.append(col.ilike(like))
        filtros.append(cast(Solicitud.funciones, db.Text).ilike(like))
        base_q = base_q.filter(or_(*filtros))

    query_ordenada = base_q.order_by(
        desc(Solicitud.estado == 'reemplazo'),
        Solicitud.fecha_solicitud.desc()
    )

    total = query_ordenada.count()
    raw_sols = (
        query_ordenada
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Form temporal para leer choices y labels
    form = AdminSolicitudForm()

    FUNCIONES_CHOICES      = list(getattr(form, 'funciones',      None).choices or [])
    FUNCIONES_LABELS       = {k: v for k, v in FUNCIONES_CHOICES}

    NINERA_TAREAS_CHOICES  = list(getattr(form, 'ninera_tareas',  None).choices or [])
    NINERA_TAREAS_LABELS   = {k: v for k, v in NINERA_TAREAS_CHOICES}

    ENF_TAREAS_CHOICES     = list(getattr(form, 'enf_tareas',     None).choices or [])
    ENF_TAREAS_LABELS      = {k: v for k, v in ENF_TAREAS_CHOICES}

    ENF_MOV_CHOICES        = list(getattr(form, 'enf_movilidad',  None).choices or [])
    ENF_MOV_LABELS         = {k: v for k, v in ENF_MOV_CHOICES}

    solicitudes = []
    for s in raw_sols:
        if s.estado == 'reemplazo':
            reems = list(s.reemplazos or [])
        else:
            reems = [r for r in (s.reemplazos or []) if bool(getattr(r, 'oportunidad_nueva', False))]

        # ====================== FUNCIONES (ORDEN CORRECTO) ======================
        raw_codes = _unique_keep_order(_as_list(getattr(s, 'funciones', None)))
        raw_codes = [c for c in raw_codes if c != 'otro']

        funcs = []
        for code in raw_codes:
            label = FUNCIONES_LABELS.get(code)
            if label:
                funcs.append(label)

        custom_f = _s(getattr(s, 'funciones_otro', None))
        if custom_f:
            funcs.append(custom_f)

        # ====================== ADULTOS / NIÑOS ======================
        adultos_val = _s(getattr(s, 'adultos', None))
        ninos_line = ""
        ninos_raw = getattr(s, 'ninos', None)
        if ninos_raw not in (None, "", 0, "0"):
            ninos_line = f"Niños: {_s(ninos_raw)}"
            ed = _s(getattr(s, 'edades_ninos', None))
            if ed:
                ninos_line += f" ({ed})"

        # ====================== MODALIDAD ======================
        modalidad = _first_nonempty_attr(s, ['modalidad_trabajo', 'modalidad', 'tipo_modalidad'], '')
        modalidad_line = modalidad

        # ====================== HOGAR ======================
        hogar_partes_detalle = []
        habitaciones = getattr(s, 'habitaciones', None)
        if habitaciones not in (None, "", 0, "0"):
            hogar_partes_detalle.append(f"{_s(habitaciones)} habitaciones")
        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes_detalle.append(f"{banos_txt} baños")
        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes_detalle.append("2 pisos")

        areas = []
        for a in _as_list(getattr(s, 'areas_comunes', None)):
            areas.append(_norm_area(a))
        area_otro = _s(getattr(s, 'area_otro', None))
        if area_otro:
            areas.append(_norm_area(area_otro))
        if areas:
            hogar_partes_detalle.append(", ".join(areas))

        tipo_lugar = _s(getattr(s, 'tipo_lugar', None))
        # Solo imprimimos algo del hogar si hay detalles reales (habitaciones, baños o áreas).
        if hogar_partes_detalle:
            if tipo_lugar:
                hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes_detalle)}"
            else:
                hogar_descr = ", ".join(hogar_partes_detalle)
        else:
            hogar_descr = ""

        # ====================== MASCOTAS ======================
        mascota_val = _s(getattr(s, 'mascota', None))
        mascota_line = f"Mascotas: {mascota_val}" if mascota_val else ""

        # ====================== CAMPOS BASE ======================
        codigo         = _s(getattr(s, 'codigo_solicitud', None))
        ciudad_sector  = _s(getattr(s, 'ciudad_sector', None))
        rutas_cercanas = _s(getattr(s, 'rutas_cercanas', None))

        # Edad requerida
        edad_req_val = getattr(s, 'edad_requerida', None)
        if isinstance(edad_req_val, (list, tuple, set, dict, str)):
            edad_req = ", ".join([_s(x) for x in _as_list(edad_req_val)])
        else:
            edad_req = _s(edad_req_val)

        experiencia    = _s(getattr(s, 'experiencia', None))
        experiencia_it = f"*{experiencia}*" if experiencia else ""
        horario        = _s(getattr(s, 'horario', None))

        # Sueldo
        sueldo_final  = _format_money_usd(getattr(s, 'sueldo', None))
        pasaje_aporte = bool(getattr(s, 'pasaje_aporte', False))

        # Nota del cliente (al final, sin prefijo)
        nota_cli = _s(getattr(s, 'nota_cliente', None))

        # ====================== DETALLES SERVICIO (NIÑERA / ENFERMERA / CHOFER) ======================
        detalles = getattr(s, 'detalles_servicio', None) or {}
        ts_det   = detalles.get("tipo") or _s(getattr(s, 'tipo_servicio', None))

        ninera_block = ""
        enf_block    = ""
        chofer_block = ""

        # ---- NIÑERA ----
        if ts_det == 'NINERA':
            cant_ninos = detalles.get("cantidad_ninos") or detalles.get("cant_ninos")
            edades_n   = detalles.get("edades_ninos")   or detalles.get("edades")
            tareas_cd  = detalles.get("tareas") or []
            cond_esp   = detalles.get("condicion_especial") or detalles.get("condicion")

            lineas_nin = []

            if cant_ninos or edades_n:
                base = "Niños a cuidar: "
                if cant_ninos:
                    base += str(cant_ninos)
                if edades_n:
                    base += f" ({edades_n})"
                lineas_nin.append(base)

            if tareas_cd:
                etiquetas = []
                for code in _as_list(tareas_cd):
                    lbl = NINERA_TAREAS_LABELS.get(code)
                    if lbl:
                        etiquetas.append(lbl)
                    else:
                        etiquetas.append(str(code))
                lineas_nin.append("Tareas con los niños: " + ", ".join(etiquetas))

            if cond_esp:
                lineas_nin.append(f"Condición especial: {cond_esp}")

            ninera_block = "\n".join(lineas_nin) if lineas_nin else ""

        # ---- ENFERMERA / CUIDADORA ----
        elif ts_det == 'ENFERMERA':
            a_quien   = detalles.get("a_quien_cuida") or detalles.get("a_quien")
            cond_prin = detalles.get("condicion_principal") or detalles.get("condicion")
            movilidad = detalles.get("movilidad") or ""
            tareas_cd = detalles.get("tareas") or []

            lineas_enf = []
            if a_quien:
                lineas_enf.append(f"A quién cuida: {a_quien}")

            if movilidad:
                mov_lbl = ENF_MOV_LABELS.get(movilidad, movilidad)
                if mov_lbl:
                    lineas_enf.append(f"Movilidad: {mov_lbl}")

            if cond_prin:
                lineas_enf.append(f"Condición principal: {cond_prin}")

            if tareas_cd:
                etiquetas = []
                for code in _as_list(tareas_cd):
                    lbl = ENF_TAREAS_LABELS.get(code)
                    if lbl:
                        etiquetas.append(lbl)
                    else:
                        etiquetas.append(str(code))
                lineas_enf.append("Tareas de cuidado: " + ", ".join(etiquetas))

            enf_block = "\n".join(lineas_enf) if lineas_enf else ""

        # ---- CHOFER ----
        elif ts_det == 'CHOFER':
            vehiculo    = detalles.get("vehiculo")
            tipo_veh    = detalles.get("tipo_vehiculo")
            tipo_otro   = detalles.get("tipo_vehiculo_otro")
            rutas       = detalles.get("rutas")
            viajes_larg = detalles.get("viajes_largos")
            lic_det     = detalles.get("licencia_requisitos") or detalles.get("licencia_detalle")

            lineas_ch = []
            if vehiculo:
                if vehiculo == 'cliente':
                    lineas_ch.append("Vehículo: del cliente")
                elif vehiculo == 'empleado':
                    lineas_ch.append("Vehículo: propio del chofer")
                else:
                    lineas_ch.append(f"Vehículo: {vehiculo}")

            if tipo_veh or tipo_otro:
                tv = tipo_otro or tipo_veh
                lineas_ch.append(f"Tipo de vehículo: {tv}")

            if rutas:
                lineas_ch.append(f"Rutas habituales: {rutas}")

            if viajes_larg is not None:
                lineas_ch.append("Viajes largos / fuera de la ciudad: Sí" if viajes_larg else "Viajes largos / fuera de la ciudad: No")

            if lic_det:
                lineas_ch.append(f"Licencia / experiencia: {lic_det}")

            chofer_block = "\n".join(lineas_ch) if lineas_ch else ""

        # ===== Texto final =====
        cod_fmt = _fmt_codigo_solicitud(codigo) if codigo else ""
        header_block = "\n".join([
            f"Disponible ( {cod_fmt} )" if cod_fmt else "Disponible",
            f"📍 {ciudad_sector}" if ciudad_sector else "📍",
            f"Ruta más cercana: {rutas_cercanas}" if rutas_cercanas else "Ruta más cercana: ",
        ])

        info_lines = []
        if modalidad_line:
            info_lines.append(modalidad_line)
        if edad_req:
            info_lines.append("")
            info_lines.append(f"Edad: {edad_req}")
        info_lines.extend(["", "Dominicana", "Que sepa leer y escribir"])
        if experiencia_it:
            info_lines.append(f"Experiencia en: {experiencia_it}")
        if horario:
            info_lines.append(f"Horario: {horario}")
        info_block = "\n".join([x for x in info_lines])

        funciones_block = f"Funciones: {', '.join(funcs)}" if funcs else ""
        hogar_line      = hogar_descr

        familia_parts = []
        if adultos_val:
            familia_parts.append(f"Adultos: {adultos_val}")
        if ninos_line:
            familia_parts.append(ninos_line)
        if mascota_line:
            familia_parts.append(mascota_line)
        familia_block = "\n".join(familia_parts) if familia_parts else ""

        sueldo_block = ""
        if sueldo_final:
            sueldo_block = (
                f"Sueldo: {sueldo_final} mensual"
                + (", más ayuda del pasaje" if pasaje_aporte else ", pasaje incluido")
            )

        # Armamos el orden final SIN cambiar el modelo original,
        # solo metiendo los bloques de detalles donde corresponde.
        parts = [
            header_block,
            "",
            info_block.strip() if info_block.strip() else None,
            "",
            funciones_block if funciones_block else None,
            "",
            hogar_line if hogar_line else None,
            "",
            ninera_block if ninera_block else None,
            enf_block if enf_block else None,
            chofer_block if chofer_block else None,
            "" if (ninera_block or enf_block or chofer_block) else None,
            familia_block if familia_block else None,
            "",
            sueldo_block if sueldo_block else None,
            "",
            (nota_cli if nota_cli else None),
        ]

        cleaned = []
        for p in parts:
            if p is None:
                continue
            if p == "" and (not cleaned or cleaned[-1] == ""):
                continue
            cleaned.append(p)
        order_text = "\n".join(cleaned).rstrip()

        solicitudes.append({
            'id': s.id,
            'codigo_solicitud': codigo,
            'ciudad_sector': ciudad_sector,
            'direccion': getattr(s, 'direccion', None),
            'reemplazos': reems,
            'funcs': funcs,
            'modalidad': modalidad,
            'order_text': order_text
        })

    has_more = (page * per_page) < total
    return render_template(
        'admin/solicitudes_copiar.html',
        solicitudes=solicitudes,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more
    )




@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@staff_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)

    if s.estado not in ('activa', 'reemplazo'):
        flash('Esta solicitud no es copiable en su estado actual.', 'warning')
        return redirect(url_for('admin.copiar_solicitudes'))

    start_utc, _ = _utc_day_bounds()
    last = _to_naive_utc(getattr(s, 'last_copiado_at', None))
    if last is not None and last >= start_utc:
        flash('Esta solicitud ya fue marcada como copiada hoy.', 'info')
        return redirect(url_for('admin.copiar_solicitudes'))

    try:
        s.last_copiado_at = func.now()
        db.session.commit()
        flash(f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo marcar la solicitud como copiada.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al marcar como copiada.', 'danger')

    return redirect(url_for('admin.copiar_solicitudes'))


# =============================================================================
#                 VISTAS "EN PROCESO" Y RESUMEN DIARIO (MEJORADAS)
# =============================================================================

# Utilidades compartidas (si ya las definiste antes, no las dupliques):
def _now_utc() -> datetime:
    return datetime.utcnow()

def _utc_day_bounds(dt: datetime | None = None):
    """(inicio_día_utc, fin_día_utc) para dt (o hoy UTC)."""
    base = (dt or datetime.utcnow()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

from urllib.parse import urlparse, urljoin

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ('http', 'https')) and (ref.netloc == test.netloc)

# ---------------------------------------
# Clientes con solicitudes "en proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/clients')
@login_required
@staff_required
def listar_clientes_con_proceso():
    """
    Lista clientes con solicitudes en 'proceso' y el conteo de pendientes.
    Incluye paginación opcional: ?page=1&per_page=50 y búsqueda ?q=...
    """
    q = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (
        db.session.query(
            Cliente.id,
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('pendientes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.estado == 'proceso')
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
    )

    if q:
        like = f'%{q}%'
        base = base.filter(
            or_(
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                Cliente.telefono.ilike(like),
            )
        )

    total = base.count()
    resultados = (base
                  .order_by(Cliente.nombre_completo.asc())
                  .offset((page - 1) * per_page)
                  .limit(per_page)
                  .all())

    return render_template(
        'admin/solicitudes_proceso_clients.html',
        resultados=resultados,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Listado de solicitudes "en proceso" por cliente
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/<int:cliente_id>')
@login_required
@staff_required
def listar_solicitudes_de_cliente_proceso(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)

    # Paginación ligera por si hay muchas
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(cliente_id=cliente_id, estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    return render_template(
        'admin/solicitudes_proceso_list.html',
        cliente=c,
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Acciones rápidas sobre "proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/acciones')
@login_required
@staff_required
def acciones_solicitudes_proceso():
    # Paginación opcional
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    return render_template(
        'admin/solicitudes_proceso_acciones.html',
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Activar solicitud (de proceso -> activa)
# ---------------------------------------
@admin_bp.route('/solicitudes/<int:id>/activar', methods=['POST'])
@login_required
@staff_required
def activar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)
    try:
        if s.estado != 'proceso':
            flash(f'La solicitud {s.codigo_solicitud} no está en "proceso".', 'warning')
            return redirect(url_for('admin.acciones_solicitudes_proceso'))

        s.estado = 'activa'
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} marcada como activa.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo activar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al activar la solicitud.', 'danger')

    return redirect(url_for('admin.acciones_solicitudes_proceso'))

# -----------------------------------------------------------------------------
# Cancelación con confirmación (GET muestra formulario, POST ejecuta)
# URL: /admin/clientes/<cliente_id>/solicitudes/<id>/cancelar
# -----------------------------------------------------------------------------
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar', methods=['GET', 'POST'])
@login_required
@staff_required
def cancelar_solicitud(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id)

    if request.method == 'GET':
        # Idempotencia y reglas de estado
        if s.estado == 'cancelada':
            flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        if s.estado == 'pagada':
            flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url
        )

    # POST (confirma cancelación)
    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        flash('Indica un motivo de cancelación (mínimo 5 caracteres).', 'danger')
        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url,
            form={'motivo': {'errors': ['Indica un motivo válido.']}}
        )

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        s.estado = 'cancelada'
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = _now_utc()
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo cancelar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al cancelar la solicitud.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

# -----------------------------------------------------------------------------
# Cancelación directa (sin formulario)
# URL: /admin/solicitudes/<id>/cancelar_directo  (POST)
# -----------------------------------------------------------------------------
@admin_bp.route('/solicitudes/<int:id>/cancelar_directo', methods=['POST'])
@login_required
@admin_required
def cancelar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.acciones_solicitudes_proceso')

    if s.estado == 'cancelada':
        flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado == 'pagada':
        flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        s.estado = 'cancelada'
        s.fecha_cancelacion = _now_utc()
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        s.motivo_cancelacion = (request.form.get('motivo') or '').strip() or 'Cancelación directa (sin motivo)'
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo cancelar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al cancelar la solicitud.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

# ---------------------------------------
# Resumen diario por cliente (UTC)
# ---------------------------------------
@admin_bp.route('/clientes/resumen_diario')
@login_required
@admin_required
def resumen_diario_clientes():
    """
    Agrupa solo las solicitudes de HOY (UTC) por cliente.
    Evita usar func.date(...) → usamos rangos [start_utc, end_utc).
    """
    start_utc, end_utc = _utc_day_bounds()

    resumen = (
        db.session.query(
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('total_solicitudes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.fecha_solicitud >= start_utc,
                Solicitud.fecha_solicitud < end_utc)
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
        .order_by(func.count(Solicitud.id).desc(), Cliente.nombre_completo.asc())
        .all()
    )

    return render_template(
        'admin/clientes_resumen_diario.html',
        resumen=resumen,
        hoy=start_utc.date()  # mostramos la fecha UTC usada
    )

# =============================================================================
#                              COMPATIBILIDAD (ADMIN)
# =============================================================================

# Helpers robustos (si ya existen en tu archivo, no los dupliques)
def _as_iter(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return list(val)
    s = str(val)
    parts = [p.strip() for p in s.split(',') if p.strip()]
    return parts if parts else ([s.strip()] if s.strip() else [])

def _as_set(val):
    return {str(x).strip().lower() for x in _as_iter(val)}

def _first_nonempty(obj, aliases, default=None):
    for name in aliases:
        if hasattr(obj, name):
            v = getattr(obj, name)
            if v not in (None, '', [], {}, ()):
                return v
    return default

def _first_text(obj, aliases, default=''):
    v = _first_nonempty(obj, aliases, default=None)
    if v is None:
        return default
    try:
        return str(v).strip()
    except Exception:
        return default

def _first_int(obj, aliases, default=0):
    v = _first_nonempty(obj, aliases, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return default

def _match_text(haystack: str, needle: str) -> bool:
    return (needle or "").lower() in (haystack or "").lower()

# -------------------------
# Cálculo de compatibilidad
# -------------------------
def calc_score_compat(solicitud: Solicitud, candidata: Candidata):
    """
    Devuelve dict con breakdown y score final (0-100).
    """
    total = 0
    breakdown = []

    # Alias de campos
    CLI_NINOS_ALIASES   = ['ninos']
    CLI_MASCOTA_ALIASES = ['mascota']
    CLI_FUNC_ALIASES    = ['funciones']
    CLI_HORARIO_ALIASES = ['horario']
    CLI_EXPERI_ALIASES  = ['experiencia']  # informativo

    CAND_RITMO_ALIASES     = ['compat_ritmo_preferido']
    CAND_ESTILO_ALIASES    = ['compat_estilo_trabajo']
    CAND_NINOS_ALIASES     = ['compat_relacion_ninos']         # comoda|neutral|prefiere_evitar
    CAND_ANOS_EXP_ALIASES  = ['anos_experiencia']
    CAND_CALIF_ALIASES     = ['calificacion']                  # 1–5
    CAND_FORTS_ALIASES     = ['compat_fortalezas']             # ARRAY
    CAND_DISP_HOR_ALIASES  = ['compat_disponibilidad_horario'] # "mañana, tarde, interna"
    CAND_DISP_DIAS_ALIASES = ['compat_disponibilidad_dias']    # no usado
    CAND_LIMITES_ALIASES   = ['compat_limites_no_negociables'] # ARRAY; p.ej. 'no_mascotas'

    # 1) Ritmo (informativo)
    cand_ritmo = _first_text(candidata, CAND_RITMO_ALIASES, default='')
    breakdown.append(("Ritmo (sin dato para comparar)", +0))

    # 2) Estilo (informativo)
    cand_estilo = _first_text(candidata, CAND_ESTILO_ALIASES, default='')
    breakdown.append(("Estilo (sin dato para comparar)", +0))

    # 3) Niños (±15/−20)
    cant_ninos = _first_int(solicitud, CLI_NINOS_ALIASES, default=0)
    hay_ninos  = cant_ninos > 0
    rel_ninos  = _first_text(candidata, CAND_NINOS_ALIASES, default='').lower()
    if hay_ninos:
        if rel_ninos == 'comoda':
            total += 15; breakdown.append(("Cómoda con niños (solicitud con niños)", +15))
        elif rel_ninos == 'neutral':
            total += 7;  breakdown.append(("Neutral con niños (solicitud con niños)", +7))
        elif rel_ninos == 'prefiere_evitar':
            total -= 20; breakdown.append(("Prefiere evitar niños (solicitud con niños)", -20))
        else:
            breakdown.append(("Relación con niños (sin dato)", +0))
    else:
        breakdown.append(("Sin niños en la solicitud", +0))

    # 4) Mascotas (±20)
    sol_mascota_txt = _first_text(solicitud, CLI_MASCOTA_ALIASES, default='')
    hay_mascota     = bool(sol_mascota_txt)
    cand_limites    = _as_set(_first_nonempty(candidata, CAND_LIMITES_ALIASES, default=[]))
    if hay_mascota:
        if 'no_mascotas' in cand_limites:
            total -= 20; breakdown.append((f"No apta con mascota ({sol_mascota_txt})", -20))
        else:
            total += 15; breakdown.append((f"Apta con mascota ({sol_mascota_txt})", +15))
    else:
        breakdown.append(("Solicitud sin mascotas", +0))

    # 5) Años experiencia (0/5/10)
    anos_exp = _first_int(candidata, CAND_ANOS_EXP_ALIASES, default=0)
    total += 10 if anos_exp >= 3 else 5 if anos_exp >= 1 else 0
    breakdown.append(("Experiencia (años)", 10 if anos_exp >= 3 else 5 if anos_exp >= 1 else 0))

    # 6) Calificación (0–5)
    punt_raw = _first_nonempty(candidata, CAND_CALIF_ALIASES, default=0) or 0
    try:
        punt = int(float(str(punt_raw).strip()))
        punt_pts = max(0, min(5, punt))
    except Exception:
        punt_pts = 0
    total += punt_pts
    breakdown.append(("Puntualidad / calificación", punt_pts))

    # 7) Fortalezas vs funciones requeridas (hasta 20)
    fun_req   = _as_set(_first_nonempty(solicitud, CLI_FUNC_ALIASES, default=[]))
    fort_cand = _as_set(_first_nonempty(candidata, CAND_FORTS_ALIASES, default=[]))
    overlap   = len(fun_req & fort_cand)
    fort_pts  = min(20, overlap * 4)   # 5 matches → 20
    total += fort_pts
    breakdown.append((f"Coincidencias en funciones/fortalezas ({overlap})", fort_pts))

    # 8) Disponibilidad (hasta 10)
    sol_hor_str = _first_text(solicitud, CLI_HORARIO_ALIASES, default='').lower()
    cand_disp_h = _first_text(candidata, CAND_DISP_HOR_ALIASES, default='').lower()
    disp_tokens = _as_set(cand_disp_h)
    disp_pts = 0
    if 'interna' in sol_hor_str and ('interna' in disp_tokens or 'interna' in cand_disp_h):
        disp_pts = 10
    elif any(t in sol_hor_str for t in ('mañana', 'manana', 'tarde', 'noche')) and disp_tokens:
        disp_pts = 8 if any(t in sol_hor_str for t in disp_tokens) else 3
    total += disp_pts
    breakdown.append(("Disponibilidad/horario", disp_pts))

    score_final = max(0, min(100, int(round(total))))
    return {"score": score_final, "breakdown": breakdown}

# ---------------------------------
# VISTA: resumen HTML de compatibilidad
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>')
@login_required
@admin_required
def ver_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para calcular compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = calc_score_compat(solicitud, candidata)

    return render_template(
        'admin/compat_resumen.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res
    )

# ---------------------------------
# VISTA: PDF de compatibilidad (WeasyPrint)
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>/pdf')
@login_required
@admin_required
def pdf_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para PDF de compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = calc_score_compat(solicitud, candidata)

    html_str = render_template(
        'admin/compat_pdf.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res,
        generado_en=_now_utc()
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=request.host_url).write_pdf()
        filename = f"compat_{cliente.codigo or cliente.id}_{candidata.fila}.pdf"
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline; filename={filename}'}
        )
    except Exception:
        # Fallback/feature flag: no romper UX si WeasyPrint no está presente
        flash("WeasyPrint no está disponible. Mostrando versión HTML del reporte.", "warning")
        return html_str

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/link-publico', methods=['GET'])
@login_required
@admin_required
def generar_link_publico_solicitud(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)

    token = generar_token_publico_cliente(c)
    link = url_for('clientes.solicitud_publica', token=token, _external=True)

    return render_template(
        'admin/cliente_link_publico_solicitud.html',
        cliente=c,
        link_publico=link
    )
