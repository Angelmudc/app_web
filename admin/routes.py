# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
import time
import json
from urllib.parse import parse_qs, urlparse
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, flash, request, jsonify, abort, session, current_app, Response, stream_with_context
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash

from sqlalchemy import or_, func, cast, desc, case, inspect as sa_inspect, Table, MetaData, select as sa_select
from sqlalchemy.types import Numeric
from sqlalchemy.orm import joinedload, load_only, selectinload  # ➜ para evitar N+1 en copiar_solicitudes
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from functools import wraps  # si otros decoradores locales lo usan

from config_app import db, cache
from models import (
    Cliente,
    Solicitud,
    Candidata,
    Reemplazo,
    TareaCliente,
    StaffUser,
    SolicitudCandidata,
    ClienteNotificacion,
    Entrevista,
    StaffAuditLog,
    PublicSolicitudTokenUso,
    PublicSolicitudClienteNuevoTokenUso,
)
from admin.forms import (
    StaffUserCreateForm,
    StaffUserEditForm,
    AdminClienteForm,
    AdminSolicitudForm,
    AdminPagoForm,
    AdminReemplazoForm,
    AdminGestionPlanForm,
    AdminReemplazoFinForm,  # 🔹 NUEVO FORM PARA FINALIZAR REEMPLAZO
)
from utils.codigo_solicitud import compose_codigo_solicitud
from utils.compat_engine import compute_match, format_compat_result
from utils.guards import (
    assert_candidata_no_descalificada,
    candidata_esta_descalificada,
    candidatas_activas_filter,
)
from utils.candidata_readiness import candidata_is_ready_to_send
from utils.candidata_completitud_audit import (
    entrevista_ok,
    binario_ok,
    referencias_ok,
    candidata_tiene_codigo_valido,
    faltantes_desde_flags,
    es_incompleta,
    solo_criticos,
    solo_sin_documentos,
    solo_sin_referencias,
)
from utils.matching_service import rank_candidates
from utils.funciones_formatter import format_funciones
from utils.audit_labels import (
    humanize_audit_field,
    humanize_audit_value,
    humanize_change,
    summarize_changed_fields,
)
from utils.staff_auth import (
    breakglass_allowed_ip,
    get_request_ip,
    breakglass_username,
    build_breakglass_user,
    check_breakglass_password,
    is_breakglass_enabled,
    log_breakglass_attempt,
    set_breakglass_session,
    is_breakglass_user_obj,
    is_breakglass_session_valid,
    clear_breakglass_session,
)
from utils.audit_logger import log_action
from utils.enterprise_layer import (
    touch_staff_session,
    list_active_sessions,
    close_user_sessions,
    lock_ping,
    lock_takeover,
    list_active_locks,
    get_alert_items,
    resolve_alert,
    health_payload,
    metrics_dashboard,
    metrics_secretarias,
    metrics_solicitudes,
    intelligent_suggestions_for_solicitud,
    register_decision_feedback,
    log_error_event,
    emit_critical_alert,
    emit_warning_alert,
    telegram_channel_config,
    save_telegram_channel_config,
    send_telegram_test_message,
)
from utils.audit_entity import (
    candidata_entity_meta,
    log_candidata_action,
)
from utils.robust_save import execute_robust_save
from utils.rbac import (
    can as rbac_can,
    has_admin_access,
    log_permission_denied,
    normalize_role as normalize_staff_role,
    permission_required_for_path,
    role_for_user,
)
from utils.pasaje_mode import (
    apply_pasaje_to_solicitud,
    normalize_pasaje_mode_text,
    read_pasaje_mode_text,
    strip_pasaje_marker_from_note,
)
from utils.modalidad import canonicalize_modalidad_trabajo
from utils.timezone import (
    format_rd_datetime,
    iso_utc_z,
    now_rd,
    parse_iso_utc,
    rd_day_range_utc_naive,
    rd_today,
    to_rd,
    utc_now_naive,
    utc_timestamp,
)

from . import admin_bp
from .decorators import admin_required, staff_required

from clientes.routes import generar_token_publico_cliente, generar_token_publico_cliente_nuevo

def _is_true_env(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _staff_password_min_len() -> int:
    try:
        return max(8, int((os.getenv("STAFF_PASSWORD_MIN_LEN") or "8").strip()))
    except Exception:
        return 8


def _operational_rate_limits_enabled() -> bool:
    raw = (os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _admin_default_role() -> str:
    role = (os.getenv("ADMIN_DEFAULT_ROLE") or "secretaria").strip().lower()
    return role if role in ("owner", "admin", "secretaria") else "secretaria"


def _emergency_hide_prefix() -> str:
    return (os.getenv("EMERGENCY_ADMIN_HIDE_PREFIX") or "emergency_").strip().lower()


def _emergency_hide_username() -> str:
    return (os.getenv("EMERGENCY_ADMIN_USERNAME") or "").strip().lower()


def _is_hidden_emergency_username(username: str) -> bool:
    uname = (username or "").strip().lower()
    if not uname:
        return False
    env_user = _emergency_hide_username()
    if env_user and uname == env_user:
        return True
    pref = _emergency_hide_prefix()
    return bool(pref and uname.startswith(pref))


def _try_breakglass_login(usuario_norm: str, clave: str):
    if not is_breakglass_enabled():
        return None

    ip = get_request_ip()
    ua = request.headers.get("User-Agent") or ""

    if usuario_norm != breakglass_username().strip().lower():
        return None

    if not breakglass_allowed_ip(ip):
        log_breakglass_attempt(False, ip, ua)
        return False

    ok = check_breakglass_password(clave)
    log_breakglass_attempt(ok, ip, ua)
    return bool(ok)


def _audit_log(
    action_type: str,
    entity_type: str | None = None,
    entity_id=None,
    summary: str | None = None,
    metadata: dict | None = None,
    changes: dict | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    try:
        log_action(
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata=metadata,
            changes=changes,
            success=success,
            error=error,
        )
    except Exception:
        return


def _form_snapshot_payload() -> dict[str, object]:
    payload: dict[str, object] = {}
    sensitive_tokens = {
        "password",
        "password_confirm",
        "clave",
        "token",
        "csrf",
        "secret",
        "telefono",
        "phone",
        "whatsapp",
        "cedula",
        "documento",
        "direccion",
        "email",
        "correo",
    }
    try:
        form_keys = sorted((request.form or {}).keys())
        for idx, key in enumerate(form_keys):
            key_txt = str(key)
            key_low = key_txt.lower()
            if any(token in key_low for token in sensitive_tokens):
                payload[key_txt] = "<redacted>"
                continue
            vals = request.form.getlist(key)
            if len(vals) > 1:
                payload[key_txt] = [str(v)[:120] for v in vals[:10]]
            else:
                payload[key_txt] = str((vals[0] if vals else ""))[:120]
            if idx >= 60:
                payload["_truncated"] = True
                break
    except Exception:
        return {}
    return payload


def _verify_solicitud_saved(
    solicitud_id: int,
    *,
    expected_cliente_id: int | None = None,
    expected_codigo: str | None = None,
    expected_estado: str | None = None,
) -> bool:
    if int(solicitud_id or 0) <= 0:
        return False
    row = Solicitud.query.filter_by(id=int(solicitud_id)).first()
    if not row:
        return False
    if expected_cliente_id is not None and int(getattr(row, "cliente_id", 0) or 0) != int(expected_cliente_id):
        return False
    if expected_codigo is not None and str(getattr(row, "codigo_solicitud", "") or "") != str(expected_codigo):
        return False
    if expected_estado is not None and str(getattr(row, "estado", "") or "") != str(expected_estado):
        return False
    return True


def _execute_form_save(
    *,
    persist_fn,
    verify_fn,
    entity_type: str,
    entity_id,
    summary: str,
    metadata: dict | None = None,
):
    result = execute_robust_save(
        session=db.session,
        persist_fn=persist_fn,
        verify_fn=verify_fn,
        max_retries=2,
        retryable_exceptions=(OperationalError,),
    )
    base_metadata = {
        "fields_sent": _form_snapshot_payload(),
        "attempt_count": int(result.attempts),
    }
    if metadata:
        base_metadata.update(metadata)
    if result.ok:
        _audit_log(
            action_type="FORM_SAVE_OK",
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata=base_metadata,
            success=True,
        )
        return result
    _audit_log(
        action_type="FORM_SAVE_FAIL",
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        metadata={**base_metadata, "error_message": (result.error_message or "")[:400]},
        success=False,
        error=result.error_message or "No se pudo guardar correctamente.",
    )
    return result


def _current_staff_role() -> str:
    if not isinstance(current_user, StaffUser):
        return normalize_staff_role(session.get("role"))
    return role_for_user(current_user)


def _owner_only() -> None:
    role = _current_staff_role()
    if not rbac_can(role, "admin:roles"):
        log_permission_denied(user=current_user if isinstance(current_user, StaffUser) else None, required_permission="admin:roles")
        abort(403)


def _ensure_testing_staff_defaults() -> None:
    if not bool(current_app.config.get("TESTING")):
        return
    seed = [
        ("Owner", "owner", "8899"),
        ("Cruz", "admin", "8998"),
        ("Karla", "secretaria", "9989"),
        ("Anyi", "secretaria", "0931"),
    ]
    changed = False
    for username, role, password in seed:
        user = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
        if user is None:
            user = StaffUser(username=username, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            changed = True
            continue
        if (user.role or "").strip().lower() != role:
            user.role = role
            changed = True
        if not bool(user.is_active):
            user.is_active = True
            changed = True
        # Mantener credenciales determinísticas en tests.
        user.set_password(password)
        changed = True
    if changed:
        db.session.commit()



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
        return utc_timestamp() < float(locked_until)
    except Exception:
        return False


def _session_action_register(usuario_norm: str, bucket: str, mx: int, win: int, lock: int) -> int:
    key = _sess_action_key(usuario_norm, bucket)
    data = session.get(key) or {}

    now_ts = utc_timestamp()
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
        left = int(float(locked_until) - utc_timestamp())
        return max(0, left)
    except Exception:
        return 0


def _admin_action_is_locked(usuario_norm: str, bucket: str = "default") -> bool:
    if not _operational_rate_limits_enabled():
        return False
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
    if not _operational_rate_limits_enabled():
        return 0
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
            if not _operational_rate_limits_enabled():
                return fn(*args, **kwargs)
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

                    now_ts = utc_timestamp()
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
                role = role_for_user(current_user)
                if is_breakglass_user_obj(current_user):
                    return (role == "admin") and is_breakglass_session_valid(session)

                if isinstance(current_user, StaffUser):
                    return bool(current_user.is_active) and role in ("owner", "admin", "secretaria")
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

        sess_state = touch_staff_session(
            user=current_user,
            flask_session=session,
            path=(request.full_path or request.path or ""),
        )
        if not bool(sess_state.get("ok")) and sess_state.get("reason") == "revoked":
            try:
                logout_user()
            except Exception:
                pass
            try:
                session.clear()
            except Exception:
                pass
            flash("Tu sesión fue cerrada por administración.", "warning")
            return redirect(url_for("admin.login"))

        _touch_staff_presence(current_path=request.path, page_title=(request.endpoint or request.path))

        required_permission = permission_required_for_path(request.path or "")
        if required_permission:
            role = role_for_user(current_user)
            if not rbac_can(role, required_permission):
                log_permission_denied(user=current_user, required_permission=required_permission)
                abort(403)

        # Rate-limit solo para acciones que cambian cosas
        if _operational_rate_limits_enabled() and request.method in ("POST", "PUT", "PATCH", "DELETE"):
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
        return utc_timestamp() < float(locked_until)
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
    data["locked_until"] = utc_timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data


def _session_register_fail(usuario_norm: str) -> int:
    key = _sess_key(usuario_norm)
    data = session.get(key) or {}
    tries = int(data.get("tries") or 0) + 1
    data["tries"] = tries
    # lock cuando llega al máximo
    if tries >= _admin_login_max_intentos():
        data["locked_until"] = utc_timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data
    return tries


def _session_reset_fail(usuario_norm: str):
    try:
        session.pop(_sess_key(usuario_norm), None)
    except Exception:
        pass


def _admin_is_locked(usuario_norm: str) -> bool:
    """Chequea lock (cache si sirve, si no sesión)."""
    if not _operational_rate_limits_enabled():
        return False
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
    if not _operational_rate_limits_enabled():
        return 0
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

    funciones_txt = format_funciones(fun_labels, otros_fun)

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
    """Login admin: StaffUser en BD + breakglass."""
    error = None
    is_testing = bool(current_app.config.get("TESTING"))

    if request.method == 'POST':
        if is_testing:
            try:
                _ensure_testing_staff_defaults()
            except Exception:
                pass

        # Honeypot (opcional). Si el template no lo tiene, no afecta.
        if (request.form.get('website') or '').strip():
            return "", 400

        usuario_raw = (request.form.get('usuario') or '').strip()[:64]
        clave       = (request.form.get('clave') or '').strip()[:128]
        usuario_norm = (usuario_raw or '').strip().lower()

        # Si está bloqueado por IP+usuario
        if (not is_testing) and _admin_is_locked(usuario_norm):
            mins = _admin_login_lock_minutos()
            error = f'Has excedido el máximo de intentos. Intenta de nuevo en {mins} minutos.'
            return render_template('admin/login.html', error=error), 429

        auth_ok = False
        authenticated_user = None
        authenticated_username = usuario_raw

        # 1) Intento principal: staff_users (BD) por username o email
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

        if staff_user and staff_user.is_active and staff_user.check_password(clave):
            auth_ok = True
            authenticated_user = staff_user
            authenticated_username = staff_user.username

        # 2) Breakglass por ENV (emergencia)
        breakglass_ok = False
        if not auth_ok:
            bg = _try_breakglass_login(usuario_norm, clave)
            breakglass_ok = bool(bg is True)
            if breakglass_ok:
                auth_ok = True
                authenticated_user = build_breakglass_user()
                authenticated_username = breakglass_username()

        if auth_ok and authenticated_user is not None:
            # ✅ Login correcto
            try:
                session.clear()
            except Exception:
                pass

            try:
                session.permanent = True
            except Exception:
                pass

            login_user(authenticated_user, remember=False)

            # ✅ MARCAR ESTA SESIÓN COMO ADMIN (AISLAMIENTO REAL)
            try:
                session[_ADMIN_SESSION_MARKER] = True
                session["usuario"] = str(authenticated_username)
                session["role"] = (getattr(authenticated_user, "role", "") or "").strip().lower()
                if breakglass_ok:
                    set_breakglass_session(session)
                else:
                    clear_breakglass_session(session)
                session.modified = True
            except Exception:
                pass

            # Reset locks
            _admin_reset_fail(usuario_norm)
            _clear_security_layer_lock_admin(endpoint="/admin/login", usuario=str(authenticated_username))

            # Auditoría de último login para staff en BD
            if isinstance(authenticated_user, StaffUser):
                try:
                    authenticated_user.last_login_at = utc_now_naive()
                    authenticated_user.last_login_ip = _client_ip()
                    db.session.commit()
                    _audit_log(
                        action_type="STAFF_LOGIN_SUCCESS",
                        entity_type="StaffUser",
                        entity_id=authenticated_user.id,
                        summary=f"Login staff exitoso: {authenticated_user.username}",
                    )
                except Exception:
                    db.session.rollback()

            fallback = url_for('admin.listar_clientes')
            return redirect(_safe_next_url(fallback))

        # ❌ Login incorrecto
        if not is_testing:
            _admin_register_fail(usuario_norm)
        _audit_log(
            action_type="STAFF_LOGIN_FAIL",
            entity_type="StaffUser",
            entity_id=usuario_norm or None,
            summary=f"Intento fallido de login staff: {usuario_norm or 'sin_usuario'}",
            success=False,
            error="Credenciales inválidas",
        )
        error = 'Credenciales inválidas.'

    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout', methods=['POST'])
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


@admin_bp.route('/usuarios', methods=['GET'])
@admin_required
def listar_usuarios():
    _owner_only()
    q = (request.args.get('q') or '').strip()
    page = max(1, request.args.get('page', default=1, type=int) or 1)
    per_page = request.args.get('per_page', default=20, type=int) or 20
    per_page = max(10, min(per_page, 100))

    query = StaffUser.query
    hidden_username = _emergency_hide_username()
    hidden_prefix = _emergency_hide_prefix()
    if hidden_username:
        query = query.filter(func.lower(StaffUser.username) != hidden_username)
    if hidden_prefix:
        query = query.filter(~func.lower(StaffUser.username).like(f"{hidden_prefix}%"))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                StaffUser.username.ilike(like),
                StaffUser.email.ilike(like),
            )
        )

    total = query.count()
    usuarios = (
        query.order_by(StaffUser.created_at.desc(), StaffUser.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    last_page = max(1, (total + per_page - 1) // per_page)

    return render_template(
        'admin/usuarios_list.html',
        usuarios=usuarios,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        last_page=last_page,
        min_password_len=_staff_password_min_len(),
    )


@admin_bp.route('/usuarios/nuevo', methods=['GET', 'POST'])
@admin_required
def crear_usuario():
    _owner_only()
    form = StaffUserCreateForm()
    form.role.data = form.role.data or _admin_default_role()
    min_password_len = _staff_password_min_len()

    if form.validate_on_submit():
        username = (form.username.data or '').strip()
        email = (form.email.data or '').strip().lower() or None
        role = (form.role.data or '').strip().lower()
        password = (form.password.data or '')

        if role not in ('owner', 'admin', 'secretaria'):
            flash('Rol inválido.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        if len(password) < min_password_len:
            flash(f'La contraseña debe tener al menos {min_password_len} caracteres.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        exists_username = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
        if exists_username:
            flash('El username ya existe.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        if email:
            exists_email = StaffUser.query.filter(func.lower(StaffUser.email) == email).first()
            if exists_email:
                flash('El email ya existe.', 'danger')
                return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        try:
            u = StaffUser(username=username, email=email, role=role, is_active=True)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            flash('Usuario creado correctamente.', 'success')
            return redirect(url_for('admin.listar_usuarios'))
        except IntegrityError:
            db.session.rollback()
            flash('No se pudo crear el usuario: username o email duplicado.', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo crear el usuario por un error de base de datos.', 'danger')

    return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)


@admin_bp.route('/usuarios/<int:user_id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    form = StaffUserEditForm(obj=user)
    min_password_len = _staff_password_min_len()

    if form.validate_on_submit():
        email = (form.email.data or '').strip().lower() or None
        role = (form.role.data or '').strip().lower()
        new_password = (form.new_password.data or '')

        if role not in ('owner', 'admin', 'secretaria'):
            flash('Rol inválido.', 'danger')
            return render_template('admin/usuario_form.html', form=form, user=user, nuevo=False, min_password_len=min_password_len)

        if email:
            dup_email = StaffUser.query.filter(
                func.lower(StaffUser.email) == email,
                StaffUser.id != user.id
            ).first()
            if dup_email:
                flash('El email ya está en uso por otro usuario.', 'danger')
                return render_template('admin/usuario_form.html', form=form, user=user, nuevo=False, min_password_len=min_password_len)

        if new_password and len(new_password) < min_password_len:
            flash(f'La nueva contraseña debe tener al menos {min_password_len} caracteres.', 'danger')
            return render_template('admin/usuario_form.html', form=form, user=user, nuevo=False, min_password_len=min_password_len)

        try:
            user.email = email
            user.role = role
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            flash('Usuario actualizado correctamente.', 'success')
            return redirect(url_for('admin.listar_usuarios'))
        except IntegrityError:
            db.session.rollback()
            flash('No se pudo guardar: email duplicado.', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo guardar por un error de base de datos.', 'danger')

    return render_template('admin/usuario_form.html', form=form, user=user, nuevo=False, min_password_len=min_password_len)


@admin_bp.route('/usuarios/<int:user_id>/toggle-estado', methods=['POST'])
@admin_required
def toggle_usuario_estado(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    try:
        if isinstance(current_user, StaffUser) and int(current_user.id) == int(user.id):
            flash('No puedes desactivar tu propio usuario.', 'warning')
            return redirect(url_for('admin.listar_usuarios'))
    except Exception:
        pass

    try:
        user.is_active = not bool(user.is_active)
        db.session.commit()
        estado = "activado" if user.is_active else "desactivado"
        flash(f'Usuario {estado} correctamente.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo actualizar el estado del usuario.', 'danger')
    return redirect(url_for('admin.listar_usuarios'))


@admin_bp.route('/roles', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_roles():
    _owner_only()

    if request.method == 'POST':
        raw_user_id = (request.form.get("user_id") or "").strip()
        new_role = normalize_staff_role(request.form.get("role"))
        if not raw_user_id.isdigit():
            flash("Usuario inválido.", "danger")
            return redirect(url_for("admin.listar_usuarios"))
        if new_role not in {"owner", "admin", "secretaria"}:
            flash("Rol inválido.", "danger")
            return redirect(url_for("admin.listar_usuarios"))

        user = StaffUser.query.filter_by(id=int(raw_user_id)).first_or_404()
        old_role = normalize_staff_role(user.role)
        if old_role == new_role:
            flash("Ese usuario ya tiene ese rol.", "info")
            return redirect(url_for("admin.listar_usuarios"))

        if old_role == "owner" and new_role != "owner":
            owners = StaffUser.query.filter(func.lower(StaffUser.role) == "owner", StaffUser.is_active.is_(True)).count()
            if int(owners or 0) <= 1:
                flash("Debe existir al menos un Owner activo.", "warning")
                return redirect(url_for("admin.listar_usuarios"))

        user.role = new_role
        try:
            db.session.commit()
            _audit_log(
                action_type="ROLE_CHANGED",
                entity_type="StaffUser",
                entity_id=user.id,
                summary=f"Rol actualizado para {user.username}",
                metadata={"old_role": old_role, "new_role": new_role},
                success=True,
            )
            flash(f"Rol de {user.username} actualizado a {new_role}.", "success")
        except Exception:
            db.session.rollback()
            flash("No se pudo actualizar el rol.", "danger")

        return redirect(url_for("admin.listar_usuarios"))

    return redirect(url_for("admin.listar_usuarios"))


@admin_bp.route('/usuarios/<int:user_id>/eliminar', methods=['POST'])
@admin_required
def eliminar_usuario(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    try:
        if isinstance(current_user, StaffUser) and int(current_user.id) == int(user.id):
            flash('No puedes eliminar tu propio usuario.', 'warning')
            return redirect(url_for('admin.listar_usuarios'))
    except Exception:
        pass

    def _has_linked_history(staff_user: StaffUser) -> bool:
        try:
            has_audit = db.session.query(StaffAuditLog.id).filter(
                StaffAuditLog.actor_user_id == int(staff_user.id)
            ).first() is not None
            if has_audit:
                return True
        except Exception:
            return True

        try:
            username_norm = (getattr(staff_user, "username", "") or "").strip().lower()
            if username_norm:
                has_matching_activity = db.session.query(SolicitudCandidata.id).filter(
                    func.lower(SolicitudCandidata.created_by) == username_norm
                ).first() is not None
                if has_matching_activity:
                    return True
        except Exception:
            # Si la verificación secundaria falla, no bloqueamos; la fuente canónica
            # para impedir borrado es la auditoría ligada por actor_user_id.
            return False

        return False

    try:
        if _has_linked_history(user):
            user.is_active = False
            db.session.commit()
            flash('Este usuario tiene actividad registrada y no puede eliminarse. Solo puede desactivarse.', 'warning')
            return redirect(url_for('admin.listar_usuarios'))

        db.session.delete(user)
        db.session.commit()
        flash('Usuario eliminado definitivamente.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo eliminar el usuario.', 'danger')
    return redirect(url_for('admin.listar_usuarios'))


def _parse_monitoreo_date(raw: str, end_of_day: bool = False):
    txt = (raw or "").strip()
    if not txt:
        return None
    try:
        d = datetime.strptime(txt, "%Y-%m-%d").date()
        if end_of_day:
            next_day = d + timedelta(days=1)
            next_start, _ = rd_day_range_utc_naive(next_day)
            return next_start
        day_start, _ = rd_day_range_utc_naive(d)
        return day_start
    except Exception:
        return None


_PRESENCE_TTL_SECONDS = 65
_PRESENCE_ACTIVE_SECONDS = 30
_PRESENCE_INTERACTION_ACTIVE_SECONDS = 60
_PRESENCE_INDEX_KEY = "staff_presence:index"
_PRODUCTIVITY_ACTIONS = (
    "CANDIDATA_EDIT",
    "CANDIDATA_INTERVIEW_NEW_CREATE",
    "CANDIDATA_INTERVIEW_LEGACY_SAVE",
    "MATCHING_SEND",
    "CANDIDATA_UPLOAD_DOCS",
    "CANDIDATA_MARK_LISTA",
    "CANDIDATA_MARK_TRABAJANDO",
)
_LIVE_EVENT_PREFIX = "staff_live_event"
_HUMAN_ACTION_MAP = {
    "STAFF_POST": "Actualizo datos",
    "CANDIDATA_EDIT": "Editando candidata",
    "CANDIDATA_INTERVIEW_NEW_CREATE": "Guardo entrevista",
    "CANDIDATA_INTERVIEW_LEGACY_SAVE": "Guardo entrevista",
    "CANDIDATA_UPLOAD_DOCS": "Guardo documentos",
    "CANDIDATA_MARK_LISTA": "Marco candidata lista para trabajar",
    "CANDIDATA_MARK_TRABAJANDO": "Marco candidata trabajando",
    "MATCHING_SEND": "Envio candidata a solicitud",
    "SOLICITUD_CREATE": "Creo solicitud",
    "SOLICITUD_UPDATE": "Actualizo solicitud",
    "SOLICITUD_PUBLICAR": "Publico solicitud",
    "LIVE_PAGE_LOAD": "Abrio pantalla",
    "LIVE_HEARTBEAT": "Activo",
    "LIVE_TAB_FOCUS": "Volvio a la app",
    "LIVE_OPEN_ENTITY": "Abrio entidad",
    "LIVE_SUBMIT": "Envio formulario",
    "LIVE_INTENT_CHANGE": "Cambio de actividad",
}


def _normalize_entity_type(value: str | None) -> str:
    txt = (value or "").strip().lower()
    if txt in {"candidata", "candidatas", "candidate"}:
        return "candidata"
    if txt in {"solicitud", "solicitudes", "request"}:
        return "solicitud"
    if txt in {"cliente", "clientes", "client"}:
        return "cliente"
    return txt


def _human_entity_display(name: str | None, code: str | None) -> str | None:
    nm = (name or "").strip()
    if not nm:
        return None
    cd = (code or "").strip()
    return f"{nm} - {cd}" if cd else f"{nm} - (sin codigo)"


def _infer_action_hint_from_path(path: str | None) -> str:
    p = (path or "").strip().lower()
    if not p:
        return "browsing"
    if "matching" in p:
        return "matching"
    if "pago" in p:
        return "pagos"
    if "entrevistas/" in p or "/entrevistas" in p:
        return "interview"
    if "entrevista" in p:
        return "editing_interview"
    if "referencias/" in p or "/referencias" in p:
        return "references"
    if "referencia" in p:
        return "editing_references"
    if "solicitudes/" in p or "/solicitudes" in p:
        return "solicitudes"
    if "solicitud" in p:
        return "editing_request"
    if "editar" in p or "edit" in p:
        return "editing"
    if "buscar" in p:
        return "searching"
    return "browsing"


def _humanize_action(
    action_type: str | None,
    summary: str | None = None,
    metadata: dict | None = None,
    route: str | None = None,
    action_hint: str | None = None,
) -> str:
    at = (action_type or "").strip().upper()
    if at == "STAFF_POST":
        route_h = _humanize_route(route)
        return f"Actualizo datos en {route_h}"
    if at in _HUMAN_ACTION_MAP:
        return _HUMAN_ACTION_MAP[at]

    hint = (action_hint or "").strip().lower() or _infer_action_hint_from_path(route)
    hint_map = {
        "editing": "Editando",
        "editing_interview": "Editando entrevista",
        "editing_references": "Editando referencias",
        "editing_candidate": "Editando candidata",
        "editing_request": "Editando solicitud",
        "viewing_client": "Viendo cliente",
        "matching": "En Matching",
        "searching": "Buscando",
        "interview": "En Entrevista",
        "references": "En Referencias",
        "solicitudes": "En Solicitudes",
        "pagos": "En Pagos",
        "browsing": "Navegando en la app",
    }
    if hint in hint_map:
        return hint_map[hint]

    txt = (summary or "").strip()
    if txt and not txt.isupper():
        return txt[:120]
    return "Actividad en la app"


def _extract_entity_context(payload: dict | None, current_path: str | None = None) -> dict:
    src = dict(payload or {})
    ctx = {
        "entity_type": _normalize_entity_type(src.get("entity_type")),
        "entity_id": (src.get("entity_id") or "").strip(),
        "entity_name": (src.get("entity_name") or "").strip(),
        "entity_code": (src.get("entity_code") or "").strip(),
        "entity_label": (src.get("entity_label") or "").strip(),
    }
    if not ctx["entity_id"]:
        for key, etype in (
            ("candidata_id", "candidata"),
            ("solicitud_id", "solicitud"),
            ("cliente_id", "cliente"),
        ):
            value = str(src.get(key) or "").strip()
            if value:
                ctx["entity_type"] = etype
                ctx["entity_id"] = value
                break

    path = (current_path or "").strip()
    if not path:
        return ctx

    parsed = urlparse(path)
    q = parse_qs(parsed.query)
    if not ctx["entity_id"]:
        for key, etype in (
            ("candidata_id", "candidata"),
            ("solicitud_id", "solicitud"),
            ("cliente_id", "cliente"),
        ):
            value = (q.get(key) or [None])[0]
            value = str(value or "").strip()
            if value:
                ctx["entity_type"] = etype
                ctx["entity_id"] = value
                break

    path_only = (parsed.path or "").strip().lower()
    if (not ctx["entity_id"]) and path_only:
        m = re.search(r"/candidatas?/([a-z0-9_-]+)", path_only)
        if m:
            ctx["entity_type"] = "candidata"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/solicitudes?/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/clientes?/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "cliente"
            ctx["entity_id"] = m.group(1)

    if (not ctx["entity_id"]) and path_only:
        m = re.search(r"/clientes/\d+/solicitudes/([a-z0-9_-]+)", path_only)
        if m:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/matching/solicitudes/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)

    return ctx


def _humanize_presence_action(
    base_action: str | None,
    action_hint: str | None,
    entity_type: str | None,
    entity_display: str | None,
) -> str:
    base = (base_action or "").strip() or "Actividad en la app"
    hint = (action_hint or "").strip().lower()
    etype = _normalize_entity_type(entity_type)
    display = (entity_display or "").strip()

    if not display and etype:
        display = {
            "candidata": "candidata",
            "solicitud": "solicitud",
            "cliente": "cliente",
        }.get(etype, etype)

    if not display:
        return base[:120]

    if hint in {"editing_candidate", "editing", "editing_interview", "editing_references", "interview", "references"} and etype == "candidata":
        if hint in {"editing_interview", "interview"}:
            return f"Editando entrevista de {display}"[:120]
        if hint in {"editing_references", "references"}:
            return f"Editando referencias de {display}"[:120]
        return f"Editando candidata {display}"[:120]

    if hint in {"editing_request", "solicitudes"} and etype == "solicitud":
        return f"Revisando solicitud {display}"[:120]

    if hint in {"matching"} and etype == "solicitud":
        return f"Trabajando en matching de solicitud {display}"[:120]

    if hint in {"viewing_client"} and etype == "cliente":
        return f"Viendo cliente {display}"[:120]

    if "editar" in base.lower() and etype == "candidata":
        return f"Editando candidata {display}"[:120]
    if "solicitud" in base.lower() and etype == "solicitud":
        return f"Revisando solicitud {display}"[:120]
    if "cliente" in base.lower() and etype == "cliente":
        return f"Viendo cliente {display}"[:120]

    return f"{base} - {display}"[:120]


def _format_candidata_display(cand: Candidata | None) -> str | None:
    if cand is None:
        return None
    return _human_entity_display(cand.nombre_completo, cand.codigo)


def _format_cliente_display(cli: Cliente | None) -> str | None:
    if cli is None:
        return None
    return _human_entity_display(cli.nombre_completo, cli.codigo)


def _format_solicitud_display(sol: Solicitud | None) -> str | None:
    if sol is None:
        return None
    base = (sol.codigo_solicitud or f"Solicitud #{sol.id}")
    cli = _format_cliente_display(getattr(sol, "cliente", None))
    return f"{base} - {cli}" if cli else base


def _humanize_route(path: str | None) -> str:
    raw = (path or "").strip()
    if not raw:
        return "-"
    parsed = urlparse(raw)
    p = (parsed.path or "").strip().lower()

    route_map = (
        ("/entrevistas/buscar", "Entrevistas: buscar"),
        ("/admin/matching", "Matching"),
        ("/admin/solicitudes", "Solicitudes"),
        ("/referencias", "Referencias"),
        ("/admin/entrevista", "Entrevista"),
        ("/admin/monitoreo", "Control Room"),
        ("/admin/pagos", "Pagos"),
    )
    for prefix, label in route_map:
        if p.startswith(prefix):
            return label

    chunks = [c for c in p.split("/") if c]
    if not chunks:
        return "Inicio"
    tail = chunks[-1].replace("-", " ").replace("_", " ").strip()
    if tail:
        return tail[:1].upper() + tail[1:]
    return raw[:80]


def _humanize_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    now = now_rd()
    dt_rd = to_rd(dt)
    if dt_rd is None:
        return "-"
    delta = max(0, int((now - dt_rd).total_seconds()))
    if delta < 60:
        return f"Hace {delta} segundos"
    if delta < 3600:
        mins = max(1, delta // 60)
        return f"Hace {mins} minutos"
    if dt_rd.date() == now.date():
        return f"Hoy {dt_rd.strftime('%H:%M')}"
    return dt_rd.strftime("%d/%m %H:%M")


def _action_icon(action_type: str | None, success: bool = True) -> tuple[str, str]:
    at = (action_type or "").strip().upper()
    if not success:
        return ("bi-exclamation-triangle", "text-danger")
    if "CREATE" in at or "NEW" in at:
        return ("bi-plus-circle", "text-success")
    if "EDIT" in at or "UPDATE" in at or "INTERVIEW" in at:
        return ("bi-pencil-square", "text-primary")
    if "DELETE" in at or "ELIM" in at:
        return ("bi-trash", "text-danger")
    if "OPEN" in at or "VIEW" in at or "PAGE_LOAD" in at:
        return ("bi-eye", "text-info")
    if "MATCH" in at or "SEND" in at:
        return ("bi-send", "text-warning")
    return ("bi-activity", "text-secondary")


def _humanize_field_name(name: str | None) -> str:
    return humanize_audit_field(name)


def _humanize_value(value) -> str:
    return humanize_audit_value(value)


def _changes_to_human(changes) -> list[dict]:
    if not isinstance(changes, dict):
        return []
    rows: list[dict] = []
    for key, value in changes.items():
        before = after = None
        if isinstance(value, dict) and ("from" in value or "to" in value):
            before = value.get("from")
            after = value.get("to")
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            before, after = value[0], value[1]
        else:
            before, after = None, value
        human = humanize_change(str(key), before, after)
        rows.append(
            {
                "field": str(key),
                "label": human.get("label") or _humanize_field_name(key),
                "from": human.get("from") or _humanize_value(before),
                "to": human.get("to") or _humanize_value(after),
                "sentence": human.get("sentence") or f"{_humanize_field_name(key)}: {_humanize_value(before)} -> {_humanize_value(after)}",
                "sensitive": bool(human.get("sensitive")),
            }
        )
    return rows[:30]


def _metadata_human(meta: dict | None) -> list[dict]:
    src = dict(meta or {})
    skip = {
        "ip", "user_agent", "event_type", "scope",
        "entity_display", "entity_name", "entity_code",
        "action_hint", "action_label", "route_label", "status_code",
    }
    out = []
    for k, v in src.items():
        key = str(k or "").strip()
        if not key or key in skip:
            continue
        if key.endswith("_id") and str(v or "").strip().isdigit():
            continue
        out.append({"label": _humanize_field_name(key), "value": _humanize_value(v)})
    return out[:12]


def _humanize_summary(action_human: str, summary: str | None, route: str | None) -> str:
    txt = (summary or "").strip()
    if not txt:
        return action_human
    if txt.isupper() or "HTTP " in txt or txt.startswith("POST ") or txt.startswith("GET "):
        return f"{action_human} en {_humanize_route(route)}"
    return txt[:180]


def _build_entity_display_map(logs: list[StaffAuditLog] | None) -> dict[tuple[str, str], str]:
    logs = logs or []
    cand_ids: set[int] = set()
    cand_codes: set[str] = set()
    sol_ids: set[int] = set()
    sol_codes: set[str] = set()
    cli_ids: set[int] = set()
    cli_codes: set[str] = set()

    for log in logs:
        et = _normalize_entity_type(getattr(log, "entity_type", None))
        eid = str(getattr(log, "entity_id", "") or "").strip()
        if not et or not eid:
            continue
        if et == "candidata":
            if eid.isdigit():
                cand_ids.add(int(eid))
            else:
                cand_codes.add(eid)
        elif et == "solicitud":
            if eid.isdigit():
                sol_ids.add(int(eid))
            else:
                sol_codes.add(eid)
        elif et == "cliente":
            if eid.isdigit():
                cli_ids.add(int(eid))
            else:
                cli_codes.add(eid)

    out: dict[tuple[str, str], str] = {}
    if cand_ids or cand_codes:
        try:
            cand_q = Candidata.query
            filters = []
            if cand_ids:
                filters.append(Candidata.fila.in_(cand_ids))
            if cand_codes:
                filters.append(Candidata.codigo.in_(cand_codes))
            for cand in cand_q.filter(or_(*filters)).all():
                label = _format_candidata_display(cand)
                if not label:
                    continue
                out[("candidata", str(cand.fila))] = label
                if (cand.codigo or "").strip():
                    out[("candidata", str(cand.codigo).strip())] = label
        except Exception:
            pass

    if sol_ids or sol_codes:
        try:
            sol_q = Solicitud.query.options(joinedload(Solicitud.cliente))
            filters = []
            if sol_ids:
                filters.append(Solicitud.id.in_(sol_ids))
            if sol_codes:
                filters.append(Solicitud.codigo_solicitud.in_(sol_codes))
            for sol in sol_q.filter(or_(*filters)).all():
                label = _format_solicitud_display(sol)
                if not label:
                    continue
                out[("solicitud", str(sol.id))] = label
                if (sol.codigo_solicitud or "").strip():
                    out[("solicitud", str(sol.codigo_solicitud).strip())] = label
        except Exception:
            pass

    if cli_ids or cli_codes:
        try:
            cli_q = Cliente.query
            filters = []
            if cli_ids:
                filters.append(Cliente.id.in_(cli_ids))
            if cli_codes:
                filters.append(Cliente.codigo.in_(cli_codes))
            for cli in cli_q.filter(or_(*filters)).all():
                label = _format_cliente_display(cli)
                if not label:
                    continue
                out[("cliente", str(cli.id))] = label
                if (cli.codigo or "").strip():
                    out[("cliente", str(cli.codigo).strip())] = label
        except Exception:
            pass
    return out


def _entity_display_from_metadata(metadata: dict | None, entity_type: str | None, entity_id: str | None) -> str | None:
    meta = dict(metadata or {})
    direct = (meta.get("entity_display") or "").strip()
    if direct:
        return direct
    name = (meta.get("entity_name") or meta.get("nombre") or "").strip()
    code = (meta.get("entity_code") or meta.get("codigo") or "").strip()
    if name:
        return _human_entity_display(name, code)
    et = _normalize_entity_type(entity_type)
    eid = (entity_id or "").strip()
    if et and eid:
        return f"{et[:1].upper() + et[1:]} ID {eid}"
    return None


def _is_valid_live_action_type(raw: str) -> bool:
    txt = (raw or "").strip().upper()
    if not txt or len(txt) > 80:
        return False
    return re.fullmatch(r"[A-Z0-9_]+", txt) is not None


def _map_event_to_action_type(event_type: str | None) -> str:
    ev = (event_type or "").strip().lower()
    if ev == "page_load":
        return "LIVE_PAGE_LOAD"
    if ev == "tab_focus":
        return "LIVE_TAB_FOCUS"
    if ev == "open_entity":
        return "LIVE_OPEN_ENTITY"
    if ev == "submit":
        return "LIVE_SUBMIT"
    if ev == "intent_change":
        return "LIVE_INTENT_CHANGE"
    return "LIVE_HEARTBEAT"


def _should_log_live_event(user_id: int, event_type: str, path: str, action_hint: str, entity_id: str) -> bool:
    ev = (event_type or "").strip().lower() or "heartbeat"
    base = f"{_LIVE_EVENT_PREFIX}:{int(user_id)}:{ev}:{(path or '')[:120]}:{(action_hint or '')[:60]}:{(entity_id or '')[:40]}"
    timeout = 25 if ev == "heartbeat" else 2
    try:
        if cache.get(base):
            return False
        cache.set(base, 1, timeout=timeout)
    except Exception:
        return ev != "heartbeat"
    return True


def _presence_key(user_id: int) -> str:
    return f"staff_presence:{int(user_id)}"


def _parse_iso_utc(raw: str | None):
    dt = parse_iso_utc(raw)
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _normalize_client_status(raw: str | None) -> str:
    txt = (raw or "").strip().lower()
    if txt in {"active", "idle", "hidden"}:
        return txt
    return "active"


def _resolve_presence_status(last_seen_seconds: int, client_status: str) -> str:
    if int(last_seen_seconds or 0) >= _PRESENCE_ACTIVE_SECONDS:
        return "inactive"
    return _normalize_client_status(client_status)


def _touch_staff_presence(
    current_path: str | None = None,
    page_title: str | None = None,
    last_action_hint: str | None = None,
    event_type: str | None = None,
    action_type: str | None = None,
    action_hint: str | None = None,
    action_human: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_name: str | None = None,
    entity_code: str | None = None,
    last_interaction_at: str | None = None,
    client_status: str | None = None,
    route_label: str | None = None,
    action_label: str | None = None,
    preserve_entity_when_missing: bool = True,
    log_event: bool = False,
) -> None:
    try:
        if not bool(session.get("is_admin_session")):
            return
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return
        if not isinstance(current_user, StaffUser):
            return

        now = utc_now_naive()
        uid = int(current_user.id)
        prev = cache.get(_presence_key(uid)) or {}
        effective_path = (current_path or prev.get("current_path") or request.path or "")[:255]
        effective_hint = (action_hint or last_action_hint or prev.get("action_hint") or _infer_action_hint_from_path(effective_path))[:80]
        effective_action_type = (action_type or prev.get("action_type") or "LIVE_HEARTBEAT")[:80]
        incoming_entity_type = _normalize_entity_type(entity_type)
        incoming_entity_id = (entity_id or "")[:64]
        if preserve_entity_when_missing:
            effective_entity_type = _normalize_entity_type(incoming_entity_type or prev.get("entity_type"))
            effective_entity_id = (incoming_entity_id or prev.get("entity_id") or "")[:64]
        else:
            effective_entity_type = _normalize_entity_type(incoming_entity_type)
            effective_entity_id = incoming_entity_id

        effective_entity_display = _human_entity_display(entity_name, entity_code)
        if not effective_entity_display and preserve_entity_when_missing:
            effective_entity_display = prev.get("entity_display")
        if not effective_entity_id:
            effective_entity_display = ""
        if not effective_entity_display and effective_entity_type and effective_entity_id:
            fake_log = StaffAuditLog(entity_type=effective_entity_type, entity_id=effective_entity_id)
            mapped = _build_entity_display_map([fake_log])
            effective_entity_display = mapped.get((effective_entity_type, effective_entity_id))
        base_action_human = (
            action_label
            or action_human
            or (
                prev.get("current_action_human")
                if preserve_entity_when_missing else ""
            )
            or _humanize_action(effective_action_type, route=effective_path, action_hint=effective_hint)
        )[:120]
        effective_action_human = _humanize_presence_action(
            base_action_human,
            effective_hint,
            effective_entity_type,
            effective_entity_display,
        )[:120]
        has_client_status = bool((client_status or "").strip())
        interaction_dt = _parse_iso_utc(last_interaction_at) or (now if not has_client_status else (_parse_iso_utc(prev.get("last_interaction_at")) or now))
        interaction_iso = iso_utc_z(interaction_dt)
        normalized_client_status = _normalize_client_status(client_status) if has_client_status else "active"

        payload = {
            "user_id": uid,
            "username": (current_user.username or str(current_user.id)),
            "role": (current_user.role or "").strip().lower(),
            "current_path": effective_path,
            "page_title": (page_title or request.endpoint or request.path or "")[:160],
            "route_label": (route_label or prev.get("route_label") or _humanize_route(effective_path))[:120],
            "last_action_hint": (last_action_hint or "")[:120],
            "event_type": (event_type or prev.get("event_type") or "heartbeat")[:32],
            "action_type": effective_action_type,
            "action_hint": effective_hint,
            "current_action_human": effective_action_human,
            "action_label": (action_label or prev.get("action_label") or "")[:120],
            "entity_type": effective_entity_type,
            "entity_id": effective_entity_id,
            "entity_display": (effective_entity_display or "")[:200],
            "last_seen_at": iso_utc_z(now),
            "last_interaction_at": interaction_iso,
            "client_status": normalized_client_status,
            "ip": (_client_ip() or "")[:64],
            "user_agent": (request.headers.get("User-Agent") or "")[:255],
        }
        cache.set(_presence_key(uid), payload, timeout=_PRESENCE_TTL_SECONDS)

        idx = cache.get(_PRESENCE_INDEX_KEY) or []
        try:
            idx = [int(x) for x in idx]
        except Exception:
            idx = []
        if uid not in idx:
            idx.append(uid)
        idx = idx[-500:]
        cache.set(_PRESENCE_INDEX_KEY, idx, timeout=max(3600, _PRESENCE_TTL_SECONDS * 20))

        if log_event and _should_log_live_event(
            user_id=uid,
            event_type=(event_type or "heartbeat"),
            path=effective_path,
            action_hint=effective_hint,
            entity_id=effective_entity_id,
        ):
            meta = {
                "event_type": (event_type or "heartbeat"),
                "action_hint": effective_hint,
                "entity_display": effective_entity_display,
                "entity_name": (entity_name or "")[:120],
                "entity_code": (entity_code or "")[:60],
                "route_label": (route_label or "")[:120],
                "action_label": (action_label or "")[:120],
            }
            _audit_log(
                action_type=effective_action_type,
                entity_type=effective_entity_type or None,
                entity_id=effective_entity_id or None,
                summary=effective_action_human,
                metadata=meta,
                success=True,
            )
    except Exception:
        return


def _presence_rows() -> list[dict]:
    idx = cache.get(_PRESENCE_INDEX_KEY) or []
    try:
        user_ids = [int(x) for x in idx]
    except Exception:
        user_ids = []
    if not user_ids:
        return []

    now = utc_now_naive()
    presence_raw = []
    for uid in user_ids:
        row = cache.get(_presence_key(uid))
        if not row:
            continue
        presence_raw.append(row)
    if not presence_raw:
        presence_raw = []

    known_user_ids = {int(r.get("user_id")) for r in presence_raw if r.get("user_id") is not None}
    # Fallback robusto: si faltan entradas de presencia por cache, recuperar sesiones recientes.
    sessions_rows = list_active_sessions()
    for sess in sessions_rows:
        try:
            uid = int(sess.get("user_id") or 0)
        except Exception:
            uid = 0
        if uid <= 0 or uid in known_user_ids:
            continue
        seen_seconds = int(sess.get("last_seen_seconds") or 999999)
        if seen_seconds > (_PRESENCE_TTL_SECONDS * 3):
            continue
        now_minus_seen = now - timedelta(seconds=max(0, seen_seconds))
        presence_raw.append(
            {
                "user_id": uid,
                "username": sess.get("username"),
                "role": sess.get("role"),
                "current_path": (sess.get("current_path") or "")[:255],
                "page_title": "Sesion activa",
                "last_action_hint": "browsing",
                "event_type": "session_fallback",
                "action_type": "LIVE_HEARTBEAT",
                "action_hint": _infer_action_hint_from_path(sess.get("current_path")),
                "current_action_human": "Sesion activa",
                "entity_type": "",
                "entity_id": "",
                "entity_display": "",
                "last_seen_at": iso_utc_z(now_minus_seen),
                "last_interaction_at": iso_utc_z(now_minus_seen),
                "client_status": "active" if seen_seconds <= _PRESENCE_ACTIVE_SECONDS else "idle",
            }
        )
        known_user_ids.add(uid)
    if not presence_raw:
        return []

    ids = [int(r.get("user_id")) for r in presence_raw if r.get("user_id") is not None]
    last_action_map = {}
    if ids:
        latest_subq = (
            db.session.query(
                StaffAuditLog.actor_user_id.label("uid"),
                func.max(StaffAuditLog.id).label("max_id"),
            )
            .filter(StaffAuditLog.actor_user_id.in_(ids))
            .group_by(StaffAuditLog.actor_user_id)
            .subquery()
        )
        rows = (
            db.session.query(StaffAuditLog)
            .join(latest_subq, StaffAuditLog.id == latest_subq.c.max_id)
            .all()
        )
        for item in rows:
            last_action_map[int(item.actor_user_id)] = item

    last_serialized_map: dict[int, dict] = {}
    if last_action_map:
        logs = list(last_action_map.values())
        entity_map = _build_entity_display_map(logs)
        for item in logs:
            if item.actor_user_id is None:
                continue
            last_serialized_map[int(item.actor_user_id)] = _serialize_log_item(item, entity_display_map=entity_map)

    out = []
    for p in presence_raw:
        uid = int(p.get("user_id"))
        seen_at = _parse_iso_utc(p.get("last_seen_at"))
        if seen_at is None:
            continue
        interaction_at = _parse_iso_utc(p.get("last_interaction_at")) or seen_at
        delta = max(0, int((now - seen_at).total_seconds()))
        interaction_delta = max(0, int((now - interaction_at).total_seconds()))
        client_status = _normalize_client_status(p.get("client_status"))
        if client_status == "active" and interaction_delta > _PRESENCE_INTERACTION_ACTIVE_SECONDS:
            client_status = "idle"
        status = _resolve_presence_status(delta, client_status)
        last = last_action_map.get(uid)
        last_serialized = last_serialized_map.get(uid) or {}
        current_action_human = (p.get("current_action_human") or last_serialized.get("action_human") or _humanize_action(
            getattr(last, "action_type", None),
            getattr(last, "summary", None),
            getattr(last, "metadata_json", {}),
            route=p.get("current_path"),
            action_hint=p.get("action_hint"),
        ))[:120]
        entity_display = (p.get("entity_display") or last_serialized.get("entity_display") or "").strip()
        current_action_human = _humanize_presence_action(
            current_action_human,
            p.get("action_hint"),
            p.get("entity_type"),
            entity_display,
        )[:120]
        out.append(
            {
                "user_id": uid,
                "username": p.get("username"),
                "role": p.get("role"),
                "status": status,
                "current_path": p.get("current_path"),
                "route_human": _humanize_route(p.get("current_path")),
                "route_label": p.get("route_label") or _humanize_route(p.get("current_path")),
                "page_title": p.get("page_title"),
                "last_seen_seconds": delta,
                "last_interaction_at": iso_utc_z(interaction_at),
                "last_interaction_seconds": interaction_delta,
                "last_interaction_human": (
                    f"Hace {interaction_delta}s"
                    if interaction_delta < 60
                    else f"Hace {max(1, int(interaction_delta / 60))}m"
                ),
                "client_status": client_status,
                "last_action_hint": p.get("last_action_hint"),
                "action_type": p.get("action_type"),
                "action_hint": p.get("action_hint"),
                "action_label": p.get("action_label"),
                "current_action_human": current_action_human,
                "entity_type": p.get("entity_type"),
                "entity_id": p.get("entity_id"),
                "entity_display": entity_display,
                "last_action_type": getattr(last, "action_type", None),
                "last_action_summary": getattr(last, "summary", None),
                "last_action_at": iso_utc_z(getattr(last, "created_at", None)) if getattr(last, "created_at", None) else None,
            }
        )

    status_rank = {"active": 0, "idle": 1, "hidden": 2, "inactive": 3}
    out.sort(key=lambda x: (status_rank.get(str(x.get("status") or "").lower(), 99), x.get("last_seen_seconds", 999999)))
    return out


def _presence_active_rows(rows: list[dict] | None = None) -> list[dict]:
    src = rows if rows is not None else _presence_rows()
    return [r for r in (src or []) if (r.get("status") == "active")]


def _build_presence_conflicts(active_rows: list[dict] | None = None) -> list[dict]:
    active_rows = active_rows if active_rows is not None else _presence_active_rows()
    by_entity: dict[str, list[dict]] = {}
    for row in active_rows:
        if (row.get("entity_type") or "") != "candidata":
            continue
        hint = (row.get("action_hint") or "").strip().lower()
        if "edit" not in hint and hint not in {"interview", "references"}:
            continue
        key = str(row.get("entity_id") or "").strip()
        if not key:
            continue
        by_entity.setdefault(key, []).append(row)

    out: list[dict] = []
    for entity_id, rows in by_entity.items():
        usernames = sorted({str(r.get("username") or "") for r in rows if r.get("username")})
        if len(usernames) < 2:
            continue
        human_name = rows[0].get("entity_display") or f"Candidata ID {entity_id}"
        emit_critical_alert(
            rule="editing_conflict",
            summary=f"Conflicto: {' y '.join(usernames[:2])} estan editando {human_name}",
            entity_type="candidata",
            entity_id=str(entity_id),
            metadata={
                "users": usernames,
                "entity_display": human_name,
                "source": "control_room",
            },
            dedupe_seconds=180,
            telegram=True,
        )
        out.append(
            {
                "entity_type": "candidata",
                "entity_id": entity_id,
                "entity_display": human_name,
                "users": usernames,
                "message": "Dos usuarias editando la misma candidata",
            }
        )
    return out


def _build_operations_metrics_payload(active_rows: list[dict] | None = None) -> dict:
    active_rows = active_rows if active_rows is not None else _presence_active_rows()
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    active_secretarias = len([r for r in active_rows if (r.get("role") or "").lower() == "secretaria"])
    candidatas_editing = len(
        [
            r for r in active_rows
            if (r.get("entity_type") == "candidata")
            and ("edit" in (r.get("action_hint") or "").lower() or (r.get("action_hint") in {"interview", "references"}))
        ]
    )
    entrevistas_hoy = (
        StaffAuditLog.query
        .filter(StaffAuditLog.created_at >= day_start)
        .filter(StaffAuditLog.action_type.in_(["CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"]))
        .count()
    )
    matching_hoy = (
        StaffAuditLog.query
        .filter(StaffAuditLog.created_at >= day_start, StaffAuditLog.action_type == "MATCHING_SEND")
        .count()
    )
    try:
        solicitudes_en_proceso = Solicitud.query.filter(Solicitud.estado == "proceso").count()
    except Exception:
        solicitudes_en_proceso = 0
    return {
        "active_secretarias": int(active_secretarias),
        "candidatas_editing_now": int(candidatas_editing),
        "solicitudes_en_proceso": int(solicitudes_en_proceso),
        "entrevistas_hoy": int(entrevistas_hoy),
        "matching_hoy": int(matching_hoy),
    }


def _build_activity_stream_payload(limit: int = 20) -> list[dict]:
    logs = (
        StaffAuditLog.query
        .order_by(StaffAuditLog.id.desc())
        .limit(min(100, max(5, int(limit))))
        .all()
    )
    if not logs:
        return []
    logs = list(reversed(logs))
    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    return items[-limit:]


def _serialize_log_item(
    log: StaffAuditLog,
    username_map: dict[int, str] | None = None,
    entity_display_map: dict[tuple[str, str], str] | None = None,
) -> dict:
    username_map = username_map or {}
    entity_display_map = entity_display_map or {}
    metadata = dict(getattr(log, "metadata_json", {}) or {})
    changes = getattr(log, "changes_json", None)
    for key in ("telefono", "numero_telefono", "phone", "phone_number", "whatsapp"):
        metadata.pop(key, None)
    entity_type = _normalize_entity_type(log.entity_type)
    entity_id = str(log.entity_id or "").strip()
    entity_display = None
    if entity_type and entity_id:
        entity_display = entity_display_map.get((entity_type, entity_id))
    if not entity_display:
        entity_display = _entity_display_from_metadata(metadata, entity_type, entity_id)

    action_hint = (metadata.get("action_hint") or "").strip().lower()
    action_human = _humanize_action(
        log.action_type,
        summary=log.summary,
        metadata=metadata,
        route=log.route,
        action_hint=action_hint,
    )
    icon_name, icon_class = _action_icon(log.action_type, success=bool(log.success))
    changes_human = _changes_to_human(changes)
    metadata_human = _metadata_human(metadata)
    created_human = _humanize_datetime(log.created_at)
    summary_human = _humanize_summary(action_human, log.summary, log.route)
    if str(log.action_type or "").upper() == "CANDIDATA_EDIT" and changes_human:
        fields_summary = summarize_changed_fields(changes_human, max_items=4)
        action_human = f"Edito candidata ({fields_summary})"
        summary_human = f"Edito: {fields_summary}"
    return {
        "id": int(log.id),
        "created_at": iso_utc_z(log.created_at) if log.created_at else None,
        "created_at_human": created_human,
        "actor_user_id": log.actor_user_id,
        "actor_username": username_map.get(int(log.actor_user_id)) if log.actor_user_id else None,
        "actor_role": log.actor_role,
        "action_type": log.action_type,
        "action_icon": icon_name,
        "action_icon_class": icon_class,
        "entity_type": entity_type or log.entity_type,
        "entity_id": entity_id or log.entity_id,
        "entity_display": entity_display,
        "action_human": action_human,
        "summary": log.summary,
        "summary_human": summary_human,
        "route": log.route,
        "route_human": _humanize_route(log.route),
        "method": log.method,
        "success": bool(log.success),
        "metadata_json": metadata,
        "metadata_human": metadata_human,
        "changes_json": changes,
        "changes_human": changes_human,
    }


def _logs_filtered_query(args=None):
    args = args or request.args
    q = StaffAuditLog.query

    user_id = args.get("actor_user_id", type=int) or args.get("user_id", type=int)
    action_type = (args.get("action_type") or "").strip()
    entity_type = (args.get("entity_type") or "").strip()
    date_from = _parse_monitoreo_date(args.get("date_from"))
    date_to = _parse_monitoreo_date(args.get("date_to"), end_of_day=True)
    search = (args.get("search") or "").strip()[:100]
    success_raw = (args.get("success") or "").strip().lower()
    since_id = args.get("since_id", type=int)

    if user_id:
        q = q.filter(StaffAuditLog.actor_user_id == user_id)
    if action_type:
        q = q.filter(StaffAuditLog.action_type == action_type)
    if entity_type:
        if entity_type.lower() == "candidata":
            q = q.filter(func.lower(StaffAuditLog.entity_type).in_(["candidata"]))
        else:
            q = q.filter(StaffAuditLog.entity_type == entity_type)
    if date_from:
        q = q.filter(StaffAuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(StaffAuditLog.created_at < date_to)
    if since_id and since_id > 0:
        q = q.filter(StaffAuditLog.id > since_id)
    if success_raw in {"1", "true", "yes", "ok"}:
        q = q.filter(StaffAuditLog.success.is_(True))
    elif success_raw in {"0", "false", "no", "error"}:
        q = q.filter(StaffAuditLog.success.is_(False))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(StaffAuditLog.entity_id.ilike(like), StaffAuditLog.summary.ilike(like)))

    return q


def _activity_ranking(since_dt: datetime, until_dt: datetime | None = None, only_secretarias: bool = False):
    rows = (
        db.session.query(
            StaffAuditLog.actor_user_id,
            StaffUser.username,
            StaffUser.role,
            func.count(StaffAuditLog.id).label("total"),
        )
        .join(StaffUser, StaffUser.id == StaffAuditLog.actor_user_id)
        .filter(StaffAuditLog.created_at >= since_dt)
    )
    if until_dt:
        rows = rows.filter(StaffAuditLog.created_at < until_dt)
    if only_secretarias:
        rows = rows.filter(func.lower(StaffUser.role) == "secretaria")
    rows = (
        rows.group_by(StaffAuditLog.actor_user_id, StaffUser.username, StaffUser.role)
        .order_by(desc("total"), StaffUser.username.asc())
        .all()
    )
    return rows


def _window_metrics_payload(start_dt: datetime, end_dt: datetime | None = None) -> dict:
    base = StaffAuditLog.query.filter(StaffAuditLog.created_at >= start_dt)
    if end_dt:
        base = base.filter(StaffAuditLog.created_at < end_dt)
    return {
        "total_actions": base.count(),
        "solicitudes_creadas": base.filter(StaffAuditLog.action_type == "SOLICITUD_CREATE").count(),
        "solicitudes_publicadas": base.filter(StaffAuditLog.action_type == "SOLICITUD_PUBLICAR").count(),
        "candidatas_editadas": base.filter(StaffAuditLog.action_type == "CANDIDATA_EDIT").count(),
        "candidatas_enviadas": base.filter(StaffAuditLog.action_type == "MATCHING_SEND").count(),
    }


def _build_productivity_today_payload() -> dict:
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    interview_actions = ("CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE")

    rows = (
        db.session.query(
            StaffAuditLog.actor_user_id.label("user_id"),
            StaffUser.username.label("username"),
            StaffUser.role.label("role"),
            func.sum(case((StaffAuditLog.action_type == "CANDIDATA_EDIT", 1), else_=0)).label("edits"),
            func.sum(case((StaffAuditLog.action_type.in_(interview_actions), 1), else_=0)).label("interviews"),
            func.sum(case((StaffAuditLog.action_type == "MATCHING_SEND", 1), else_=0)).label("sent"),
            func.count(StaffAuditLog.id).label("total"),
        )
        .join(StaffUser, StaffUser.id == StaffAuditLog.actor_user_id)
        .filter(StaffAuditLog.created_at >= day_start)
        .filter(StaffAuditLog.actor_user_id.isnot(None))
        .filter(StaffAuditLog.action_type.in_(_PRODUCTIVITY_ACTIONS))
        .group_by(StaffAuditLog.actor_user_id, StaffUser.username, StaffUser.role)
        .order_by(desc("total"), StaffUser.username.asc())
        .all()
    )

    users = []
    for row in rows:
        users.append(
            {
                "user_id": int(row.user_id),
                "username": row.username,
                "role": row.role,
                "edits": int(row.edits or 0),
                "interviews": int(row.interviews or 0),
                "sent": int(row.sent or 0),
                "total": int(row.total or 0),
            }
        )
    return {"users": users}


def _build_monitoreo_summary_payload() -> dict:
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    top = _activity_ranking(month_start, only_secretarias=True)
    presence = _presence_rows()
    active_presence = _presence_active_rows(presence)
    conflicts = _build_presence_conflicts(active_presence)
    alerts = get_alert_items(limit=10, scope="critical", include_resolved=False)
    return {
        "generated_at": iso_utc_z(now),
        "today": _window_metrics_payload(day_start),
        "week": _window_metrics_payload(week_start),
        "month": _window_metrics_payload(month_start),
        "top": [
            {
                "user_id": int(r.actor_user_id),
                "username": r.username,
                "role": r.role,
                "total_actions": int(r.total or 0),
            }
            for r in top[:10]
        ],
        "presence": presence,
        "presence_active_count": len(active_presence),
        "presence_conflicts": conflicts,
        "productivity": _build_productivity_today_payload(),
        "operations": _build_operations_metrics_payload(active_presence),
        "activity_stream": _build_activity_stream_payload(limit=20),
        "alerts": alerts,
        "critical_alerts": alerts,
    }


def _resolve_candidata_from_entity_id(entity_id: str):
    val = (entity_id or "").strip()
    if not val:
        return None
    cand = None
    if val.isdigit():
        cand = Candidata.query.filter_by(fila=int(val)).first()
    if cand is None:
        cand = Candidata.query.filter(Candidata.codigo == val).first()
    return cand


def _sanitize_monitoreo_metadata(meta: dict | None) -> dict:
    out = dict(meta or {})
    for key in (
        "telefono",
        "numero_telefono",
        "phone",
        "phone_number",
        "whatsapp",
        "cedula",
        "dni",
        "documento",
        "direccion",
        "address",
        "email",
        "correo",
    ):
        out.pop(key, None)
    return out


def _candidata_logs_query(candidata_entity_id: str, filter_tag: str = ""):
    q = (
        StaffAuditLog.query
        .filter(func.lower(StaffAuditLog.entity_type) == "candidata")
        .filter(StaffAuditLog.entity_id == str(candidata_entity_id))
    )

    tag = (filter_tag or "").strip().lower()
    if tag == "edits":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_EDIT"]))
    elif tag == "entrevistas":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"]))
    elif tag == "docs":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_UPLOAD_DOCS"]))
    elif tag == "matching":
        q = q.filter(StaffAuditLog.action_type.in_(["MATCHING_SEND"]))
    elif tag == "fallos":
        q = q.filter(StaffAuditLog.success.is_(False))
    return q


@admin_bp.route('/monitoreo', methods=['GET'])
@login_required
@admin_required
def monitoreo_staff():
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    week_start = now - timedelta(days=7)

    summary = _build_monitoreo_summary_payload()
    total_today = summary["today"]["total_actions"]
    total_week = summary["week"]["total_actions"]
    metrics = summary["week"]

    per_day_rows = (
        db.session.query(
            func.date(StaffAuditLog.created_at).label("day"),
            func.count(StaffAuditLog.id).label("total"),
        )
        .filter(StaffAuditLog.created_at >= week_start)
        .group_by(func.date(StaffAuditLog.created_at))
        .order_by(func.date(StaffAuditLog.created_at).asc())
        .all()
    )

    latest_logs = (
        _logs_filtered_query()
        .order_by(StaffAuditLog.created_at.desc())
        .limit(30)
        .all()
    )
    users = StaffUser.query.order_by(StaffUser.username.asc()).all()
    user_map = {u.id: u for u in users}
    username_map = {int(u.id): u.username for u in users}
    latest_entity_map = _build_entity_display_map(latest_logs)
    latest_logs_items = [
        _serialize_log_item(log, username_map=username_map, entity_display_map=latest_entity_map)
        for log in latest_logs
    ]

    ranking_today = _activity_ranking(day_start)
    ranking_week = _activity_ranking(now - timedelta(days=7))
    ranking_month = _activity_ranking(now - timedelta(days=30))

    return render_template(
        "admin/monitoreo.html",
        now=now,
        total_today=total_today,
        total_week=total_week,
        metrics=metrics,
        per_day_rows=per_day_rows,
        latest_logs=latest_logs_items,
        users=users,
        user_map=user_map,
        ranking_today=ranking_today,
        ranking_week=ranking_week,
        ranking_month=ranking_month,
        summary_payload=summary,
        initial_last_id=int(latest_logs[0].id) if latest_logs else 0,
    )


@admin_bp.route('/monitoreo/logs', methods=['GET'])
@login_required
@admin_required
def monitoreo_logs():
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", default=25, type=int)))
    pagination = _logs_filtered_query().order_by(StaffAuditLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    users = StaffUser.query.order_by(StaffUser.username.asc()).all()
    action_types = [r[0] for r in db.session.query(StaffAuditLog.action_type).distinct().order_by(StaffAuditLog.action_type.asc()).all()]
    entity_types = [r[0] for r in db.session.query(StaffAuditLog.entity_type).filter(StaffAuditLog.entity_type.isnot(None)).distinct().order_by(StaffAuditLog.entity_type.asc()).all()]
    user_map = {u.id: u for u in users}
    username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(list(pagination.items))
    logs_items = [
        _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
        for log in pagination.items
    ]

    return render_template(
        "admin/monitoreo_logs.html",
        logs=logs_items,
        pagination=pagination,
        users=users,
        user_map=user_map,
        action_types=action_types,
        entity_types=entity_types,
        initial_last_id=int(pagination.items[0].id) if pagination.items else 0,
        has_active_filters=bool(
            request.args.get("user_id")
            or request.args.get("actor_user_id")
            or request.args.get("action_type")
            or request.args.get("entity_type")
            or request.args.get("date_from")
            or request.args.get("date_to")
            or request.args.get("search")
            or request.args.get("success")
        ),
    )


@admin_bp.route('/monitoreo/candidatas', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidatas_search():
    q = (request.args.get("q") or "").strip()[:128]
    limit = min(50, max(1, request.args.get("limit", default=20, type=int)))
    rows = []
    if q:
        like = f"%{q}%"
        digits = re.sub(r"\D+", "", q)
        filters = [
            Candidata.nombre_completo.ilike(like),
            Candidata.cedula.ilike(like),
            Candidata.codigo.ilike(like),
            cast(Candidata.fila, db.String).ilike(like),
        ]
        if digits:
            filters.append(Candidata.cedula_norm_digits.ilike(f"%{digits}%"))
        rows = (
            Candidata.query
            .filter(or_(*filters))
            .order_by(Candidata.fila.desc())
            .limit(limit)
            .all()
        )
    return render_template(
        "admin/monitoreo_candidatas_search.html",
        q=q,
        limit=limit,
        rows=rows,
    )


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_historial(candidata_entity_id: str):
    filter_tag = (request.args.get("filter") or "").strip().lower()
    cand = _resolve_candidata_from_entity_id(candidata_entity_id)
    logs = (
        _candidata_logs_query(candidata_entity_id, filter_tag)
        .order_by(StaffAuditLog.created_at.desc())
        .limit(300)
        .all()
    )
    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    for item in items:
        item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
    return render_template(
        "admin/monitoreo_candidata_historial.html",
        candidata_entity_id=str(candidata_entity_id),
        candidata=cand,
        candidata_meta=(candidata_entity_meta(cand) if cand else {}),
        logs=items,
        active_filter=filter_tag,
        initial_last_id=max([i["id"] for i in items], default=0),
    )


@admin_bp.route('/monitoreo/secretarias/<int:user_id>', methods=['GET'])
@login_required
@admin_required
def monitoreo_secretaria(user_id: int):
    user = StaffUser.query.get_or_404(user_id)
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", default=25, type=int)))

    date_from = _parse_monitoreo_date(request.args.get("date_from"))
    date_to = _parse_monitoreo_date(request.args.get("date_to"), end_of_day=True)

    q = StaffAuditLog.query.filter(StaffAuditLog.actor_user_id == user.id)
    if date_from:
        q = q.filter(StaffAuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(StaffAuditLog.created_at < date_to)
    q = q.order_by(StaffAuditLog.created_at.desc())

    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    username_map = {int(user.id): user.username}
    entity_display_map = _build_entity_display_map(list(pagination.items))
    logs_items = [
        _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
        for log in pagination.items
    ]

    now = utc_now_naive()
    since = now - timedelta(days=30)
    per_day_rows = (
        db.session.query(
            func.date(StaffAuditLog.created_at).label("day"),
            func.count(StaffAuditLog.id).label("total"),
        )
        .filter(StaffAuditLog.actor_user_id == user.id, StaffAuditLog.created_at >= since)
        .group_by(func.date(StaffAuditLog.created_at))
        .order_by(func.date(StaffAuditLog.created_at).asc())
        .all()
    )

    return render_template(
        "admin/monitoreo_secretaria.html",
        target_user=user,
        logs=logs_items,
        pagination=pagination,
        per_day_rows=per_day_rows,
    )


@admin_bp.route('/monitoreo/logs.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_logs_json():
    limit = min(200, max(1, request.args.get("limit", default=50, type=int)))
    query = _logs_filtered_query()
    since_id = request.args.get("since_id", type=int) or 0

    if since_id > 0:
        logs = query.order_by(StaffAuditLog.id.asc()).limit(limit).all()
    else:
        logs = query.order_by(StaffAuditLog.id.desc()).limit(limit).all()
        logs = list(reversed(logs))

    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        rows = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in rows}

    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    last_id = max([i["id"] for i in items], default=(since_id or 0))
    return jsonify({"items": items, "last_id": int(last_id)})


@admin_bp.route('/monitoreo/summary.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_summary_json():
    return jsonify(_build_monitoreo_summary_payload())


@admin_bp.route('/monitoreo/productividad.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_productividad_json():
    if not bool(session.get("is_admin_session")):
        abort(403)
    return jsonify(_build_productivity_today_payload())


@admin_bp.route('/monitoreo/presence.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_presence_json():
    return jsonify({"items": _presence_rows()})


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>/logs.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_logs_json(candidata_entity_id: str):
    since_id = request.args.get("since_id", type=int) or 0
    limit = min(300, max(1, request.args.get("limit", default=50, type=int)))
    filter_tag = (request.args.get("filter") or "").strip().lower()
    query = _candidata_logs_query(candidata_entity_id, filter_tag)
    if since_id > 0:
        query = query.filter(StaffAuditLog.id > since_id)
        logs = query.order_by(StaffAuditLog.id.asc()).limit(limit).all()
    else:
        logs = query.order_by(StaffAuditLog.id.desc()).limit(limit).all()
        logs = list(reversed(logs))

    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    for item in items:
        item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
    last_id = max([i["id"] for i in items], default=(since_id or 0))
    return jsonify({"items": items, "last_id": int(last_id)})


@admin_bp.route('/monitoreo/stream', methods=['GET'])
@login_required
@admin_required
def monitoreo_stream():
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    @stream_with_context
    def generate():
        try:
            if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
                snapshot = _presence_rows()
                yield _sse(
                    "active_snapshot",
                    {
                        "items": snapshot,
                        "active_count": len(_presence_active_rows(snapshot)),
                        "interval_sec": 1,
                    },
                )
                yield _sse("heartbeat", {"ts": iso_utc_z()})
                return

            last_id = request.args.get("last_id", type=int) or 0
            if last_id <= 0:
                max_id = db.session.query(func.max(StaffAuditLog.id)).scalar()
                last_id = int(max_id or 0)

            last_summary_at = 0.0
            last_presence_at = 0.0
            last_operations_at = 0.0
            last_activity_at = 0.0
            last_heartbeat_at = 0.0
            while True:
                now_ts = time.time()

                new_logs = (
                    StaffAuditLog.query
                    .filter(StaffAuditLog.id > last_id)
                    .order_by(StaffAuditLog.id.asc())
                    .limit(100)
                    .all()
                )
                if new_logs:
                    actor_ids = sorted({int(l.actor_user_id) for l in new_logs if l.actor_user_id is not None})
                    username_map = {}
                    if actor_ids:
                        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
                        username_map = {int(u.id): u.username for u in users}
                    entity_display_map = _build_entity_display_map(new_logs)
                    for log in new_logs:
                        item = _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
                        yield _sse("log", item)
                        last_id = max(last_id, int(log.id))

                if (now_ts - last_presence_at) >= 1.0:
                    presence = _presence_rows()
                    active = _presence_active_rows(presence)
                    conflicts = _build_presence_conflicts(active)
                    yield _sse(
                        "active_snapshot",
                        {
                            "items": presence,
                            "active_count": len(active),
                            "conflicts": conflicts,
                            "interval_sec": 1,
                        },
                    )
                    last_presence_at = now_ts

                if (now_ts - last_summary_at) >= 5.0:
                    summary = _build_monitoreo_summary_payload()
                    summary.pop("presence", None)
                    summary.pop("activity_stream", None)
                    yield _sse("summary", summary)
                    last_summary_at = now_ts

                if (now_ts - last_operations_at) >= 2.0:
                    active = _presence_active_rows()
                    yield _sse("operations", {"metrics": _build_operations_metrics_payload(active)})
                    last_operations_at = now_ts

                if (now_ts - last_activity_at) >= 2.0:
                    yield _sse("activity", {"items": _build_activity_stream_payload(limit=20)})
                    last_activity_at = now_ts

                if (now_ts - last_heartbeat_at) >= 15.0:
                    yield _sse("heartbeat", {"ts": iso_utc_z()})
                    last_heartbeat_at = now_ts

                time.sleep(1.0)
        except (GeneratorExit, ConnectionError, OSError):
            return

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>/stream', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_stream(candidata_entity_id: str):
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    @stream_with_context
    def generate():
        if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
            yield _sse("heartbeat", {"ts": iso_utc_z()})
            return

        last_id = request.args.get("last_id", type=int) or 0
        if last_id <= 0:
            max_id = (
                _candidata_logs_query(candidata_entity_id)
                .with_entities(func.max(StaffAuditLog.id))
                .scalar()
            )
            last_id = int(max_id or 0)

        last_heartbeat_at = 0.0
        while True:
            now_ts = time.time()
            new_logs = (
                _candidata_logs_query(candidata_entity_id)
                .filter(StaffAuditLog.id > last_id)
                .order_by(StaffAuditLog.id.asc())
                .limit(100)
                .all()
            )
            if new_logs:
                actor_ids = sorted({int(l.actor_user_id) for l in new_logs if l.actor_user_id is not None})
                username_map = {}
                if actor_ids:
                    users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
                    username_map = {int(u.id): u.username for u in users}
                entity_display_map = _build_entity_display_map(new_logs)
                for log in new_logs:
                    item = _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
                    item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
                    yield _sse("candidatelog", item)
                    last_id = max(last_id, int(log.id))

            if (now_ts - last_heartbeat_at) >= 15.0:
                yield _sse("heartbeat", {"ts": iso_utc_z()})
                last_heartbeat_at = now_ts
            time.sleep(2.0)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


@admin_bp.route('/monitoreo/presence/ping', methods=['POST'])
@login_required
def monitoreo_presence_ping():
    if not bool(session.get("is_admin_session")):
        abort(403)
    if not isinstance(current_user, StaffUser):
        abort(403)
    if not bool(getattr(current_user, "is_active", False)):
        abort(403)
    role = role_for_user(current_user)
    if role not in ("owner", "admin", "secretaria"):
        abort(403)

    payload = request.get_json(silent=True) or {}
    current_path = (payload.get("current_path") or request.path or "").strip()[:255]
    page_title = (payload.get("page_title") or request.endpoint or "").strip()[:160]
    event_type = (payload.get("event_type") or "heartbeat").strip().lower()[:32]
    if event_type not in {"page_load", "heartbeat", "tab_focus", "open_entity", "submit", "intent_change"}:
        event_type = "heartbeat"

    raw_action_type = (payload.get("action_type") or "").strip().upper()
    action_type = raw_action_type if _is_valid_live_action_type(raw_action_type) else _map_event_to_action_type(event_type)
    action_hint = (payload.get("action_hint") or payload.get("last_action_hint") or "").strip().lower()[:80]
    action_label = (payload.get("action_label") or "").strip()[:120]
    route_label = (payload.get("route_label") or "").strip()[:120]
    client_status = _normalize_client_status(payload.get("client_status"))
    last_interaction_at = (payload.get("last_interaction_at") or "").strip()[:40]
    if not action_hint:
        action_hint = _infer_action_hint_from_path(current_path)[:80]
    ctx = _extract_entity_context(payload, current_path=current_path)
    action_human = _humanize_action(action_type, route=current_path, action_hint=action_hint)
    if not action_label:
        action_label = action_human

    _touch_staff_presence(
        current_path=current_path,
        page_title=page_title,
        route_label=route_label,
        last_action_hint=action_hint,
        event_type=event_type,
        action_type=action_type,
        action_hint=action_hint,
        action_human=action_human,
        action_label=action_label,
        entity_type=ctx.get("entity_type"),
        entity_id=ctx.get("entity_id"),
        entity_name=ctx.get("entity_name"),
        entity_code=ctx.get("entity_code"),
        last_interaction_at=last_interaction_at,
        client_status=client_status,
        preserve_entity_when_missing=False,
        log_event=True,
    )
    return jsonify({"ok": True})


@admin_bp.route('/seguridad/locks', methods=['GET'])
@login_required
@admin_required
def seguridad_locks():
    rows = list_active_locks()
    return render_template("admin/seguridad_locks.html", locks=rows, now=utc_now_naive())


@admin_bp.route('/seguridad/locks/ping', methods=['POST'])
@login_required
@staff_required
def seguridad_locks_ping():
    payload = request.get_json(silent=True) or {}
    entity_type = (payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    current_path = (payload.get("current_path") or request.path or "").strip()[:255]
    if not isinstance(current_user, StaffUser):
        return jsonify({"ok": False, "error": "Sesión inválida."}), 403
    data = lock_ping(user=current_user, entity_type=entity_type, entity_id=entity_id, current_path=current_path)
    if not data.get("ok"):
        return jsonify(data), 400
    return jsonify(data)


@admin_bp.route('/seguridad/locks/takeover', methods=['POST'])
@login_required
@staff_required
def seguridad_locks_takeover():
    payload = request.get_json(silent=True) or {}
    entity_type = (payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    reason = (payload.get("reason") or "").strip()
    if not isinstance(current_user, StaffUser):
        return jsonify({"ok": False, "error": "Sesión inválida."}), 403
    data = lock_takeover(user=current_user, entity_type=entity_type, entity_id=entity_id, reason=reason)
    if not data.get("ok"):
        return jsonify(data), 403
    return jsonify(data)


@admin_bp.route('/seguridad/sesiones', methods=['GET'])
@login_required
@admin_required
def seguridad_sesiones():
    sessions_rows = list_active_sessions()
    return render_template("admin/seguridad_sesiones.html", sessions_rows=sessions_rows, now=utc_now_naive())


@admin_bp.route('/seguridad/sesiones/cerrar', methods=['POST'])
@login_required
@admin_required
def seguridad_sesiones_cerrar():
    raw_user_id = request.form.get("user_id") or (request.get_json(silent=True) or {}).get("user_id")
    reason = request.form.get("reason") or (request.get_json(silent=True) or {}).get("reason") or ""
    try:
        user_id = int(raw_user_id)
    except Exception:
        flash("Usuario inválido para cerrar sesión.", "danger")
        return redirect(url_for("admin.seguridad_sesiones"))
    close_user_sessions(actor=current_user, user_id=user_id, reason=reason)
    flash("Sesiones cerradas correctamente.", "success")
    return redirect(url_for("admin.seguridad_sesiones"))


@admin_bp.route('/seguridad/alertas', methods=['GET'])
@login_required
@admin_required
def seguridad_alertas():
    alerts = get_alert_items(limit=200, scope="security", include_resolved=True)
    return render_template("admin/seguridad_alertas.html", alerts=alerts)


@admin_bp.route('/alertas/<int:alert_id>/resolver', methods=['POST'])
@login_required
@admin_required
def resolver_alerta(alert_id: int):
    resolve_alert(alert_id, actor=current_user if isinstance(current_user, StaffUser) else None)
    flash("Alerta marcada como resuelta.", "success")
    nxt = request.form.get("next") or request.args.get("next")
    if nxt and str(nxt).startswith("/"):
        return redirect(nxt)
    return redirect(url_for("admin.monitoreo_staff"))


@admin_bp.route('/alertas/canales', methods=['GET', 'POST'])
@login_required
@admin_required
def alertas_canales():
    _owner_only()
    if request.method == "POST":
        token = (request.form.get("telegram_bot_token") or "").strip()
        chat_id = (request.form.get("telegram_chat_id") or "").strip()
        enabled = str(request.form.get("telegram_enabled") or "").strip().lower() in {"1", "true", "on", "yes"}

        save_telegram_channel_config(
            token=token,
            chat_id=chat_id,
            enabled=enabled,
            actor_username=getattr(current_user, "username", None),
        )
        flash("Canal de Telegram actualizado.", "success")
        return redirect(url_for("admin.alertas_canales"))

    cfg = telegram_channel_config()
    return render_template("admin/alertas_canales.html", cfg=cfg)


@admin_bp.route('/alertas/canales/probar', methods=['POST'])
@login_required
@admin_required
def alertas_canales_probar():
    _owner_only()
    ok, detail = send_telegram_test_message(actor_username=getattr(current_user, "username", None))
    if ok:
        flash("Mensaje de prueba enviado por Telegram.", "success")
    else:
        flash(f"No se pudo enviar el mensaje de prueba: {detail}", "danger")
    return redirect(url_for("admin.alertas_canales"))


@admin_bp.route('/errores', methods=['GET'])
@login_required
@admin_required
def errores_lista():
    errors = get_alert_items(limit=200, scope="error", include_resolved=True)
    return render_template("admin/errores_lista.html", errors=errors)


@admin_bp.route('/errores/<int:error_id>', methods=['GET'])
@login_required
@admin_required
def errores_detalle(error_id: int):
    row = StaffAuditLog.query.filter(StaffAuditLog.id == int(error_id), StaffAuditLog.action_type == "ERROR_EVENT").first_or_404()
    actor = None
    if row.actor_user_id:
        actor = StaffUser.query.filter_by(id=int(row.actor_user_id)).first()
    return render_template("admin/errores_detalle.html", error_row=row, actor=actor)


@admin_bp.route('/health', methods=['GET'])
@login_required
@admin_required
def admin_health():
    payload = health_payload()
    if (request.args.get("format") or "").strip().lower() == "json":
        return jsonify(payload)
    return render_template("admin/health.html", health=payload)


@admin_bp.route('/metricas', methods=['GET'])
@login_required
@admin_required
def metricas_dashboard():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_dashboard(period)
    return render_template("admin/metricas_dashboard.html", period=period, payload=payload)


@admin_bp.route('/metricas/secretarias', methods=['GET'])
@login_required
@admin_required
def metricas_secretarias_view():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_secretarias(period)
    return render_template("admin/metricas_secretarias.html", period=period, payload=payload)


@admin_bp.route('/metricas/solicitudes', methods=['GET'])
@login_required
@admin_required
def metricas_solicitudes_view():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_solicitudes(period)
    return render_template("admin/metricas_solicitudes.html", period=period, payload=payload)


@admin_bp.route('/solicitudes/<int:solicitud_id>/sugerencias', methods=['GET'])
@login_required
@staff_required
def sugerencias_solicitud(solicitud_id: int):
    solicitud = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter_by(id=solicitud_id)
        .first_or_404()
    )
    items = intelligent_suggestions_for_solicitud(solicitud, top_k=10)
    return render_template("admin/sugerencias_solicitud.html", solicitud=solicitud, items=items)


@admin_bp.route('/solicitudes/<int:solicitud_id>/sugerencias/feedback', methods=['POST'])
@login_required
@staff_required
def sugerencias_feedback(solicitud_id: int):
    solicitud = Solicitud.query.filter_by(id=solicitud_id).first_or_404()
    try:
        candidata_id = int(request.form.get("candidata_id") or "0")
    except Exception:
        candidata_id = 0
    feedback = (request.form.get("feedback") or "").strip().lower()
    reason_key = (request.form.get("reason_key") or "").strip().lower()[:40]
    reason_text = (request.form.get("reason_text") or "").strip()[:200]
    good = feedback in {"good", "buena", "si", "sí", "1"}
    if candidata_id <= 0:
        flash("Selecciona una candidata válida para guardar feedback.", "warning")
        return redirect(url_for("admin.sugerencias_solicitud", solicitud_id=solicitud.id))
    register_decision_feedback(
        actor=current_user,
        solicitud_id=solicitud.id,
        candidata_id=candidata_id,
        good=good,
        reason_key=reason_key or "experiencia",
        reason_text=reason_text,
    )
    flash("Feedback guardado. El motor ajustó sus pesos de reglas.", "success")
    return redirect(url_for("admin.sugerencias_solicitud", solicitud_id=solicitud.id))


@admin_bp.route('/matching/inteligente', methods=['GET'])
@login_required
@staff_required
def matching_inteligente():
    solicitudes = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter(Solicitud.estado.in_(("activa", "reemplazo", "proceso")))
        .order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        .limit(80)
        .all()
    )
    return render_template("admin/matching_inteligente.html", solicitudes=solicitudes)

# =============================================================================
#                 GUARD GLOBAL ADMIN (aislamiento real)
# =============================================================================

def _is_admin_identity_LEGACY() -> bool:
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
        return utc_timestamp() < float(until)
    except Exception:
        return False

def _session_action_register_LEGACY(usuario_norm: str, bucket: str, max_actions: int, window_sec: int) -> int:
    key = _sess_action_key_LEGACY(usuario_norm, bucket)
    now = utc_timestamp()
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
    now_utc = utc_now_naive()
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
        "last_updated": _dt_iso(last_ts) or _dt_iso(utc_now_naive()),
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
    resp = jsonify({"ok": True, "utc": iso_utc_z()})
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
    ('otro', 'Otro'),
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


def _normalize_modalidad_on_solicitud(solicitud_obj) -> None:
    try:
        if hasattr(solicitud_obj, "modalidad_trabajo"):
            txt = canonicalize_modalidad_trabajo(getattr(solicitud_obj, "modalidad_trabajo", ""))
            solicitud_obj.modalidad_trabajo = txt or None
    except Exception:
        return


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
    Genera un código único del tipo:
      - primera:  <CODCLI>
      - siguientes: <CODCLI> - <LETRA>  (B, C, ...)
    Usa un loop defensivo para evitar colisiones si hubo borrados o concurrencia.
    """
    prefix = (cliente.codigo or str(cliente.id)).strip()
    base_count = Solicitud.query.filter_by(cliente_id=cliente.id).count()
    intento = 0
    while True:
        code = compose_codigo_solicitud(prefix, base_count + intento)
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
            ahora = utc_now_naive()
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
                    if len(raw_pw) < 8:
                        if hasattr(form, 'password'):
                            form.password.errors.append("La contraseña debe tener al menos 8 caracteres.")
                        flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                        return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
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
                if len(raw_pw) < 8:
                    if hasattr(form, 'password'):
                        form.password.errors.append("La contraseña debe tener al menos 8 caracteres.")
                    flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False, cliente=c)
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
                c.fecha_ultima_actividad = utc_now_naive()
            if hasattr(c, 'updated_at'):
                c.updated_at = utc_now_naive()

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
_TABLE_EXISTS_CACHE: dict[str, bool] = {}


def _table_exists(table_name: str) -> bool:
    name = (table_name or "").strip()
    if not name:
        return False
    cached = _TABLE_EXISTS_CACHE.get(name)
    if cached is not None:
        return bool(cached)
    try:
        exists = bool(sa_inspect(db.engine).has_table(name))
    except Exception:
        exists = False
    _TABLE_EXISTS_CACHE[name] = exists
    return exists


def _safe_count(query) -> int:
    try:
        return int(query.scalar() or 0)
    except Exception:
        return -1


def _collect_cliente_delete_plan(cliente_id: int) -> dict[str, object]:
    cid = int(cliente_id or 0)
    solicitud_ids: list[int] = []
    summary: dict[str, int] = {
        "solicitudes": 0,
        "solicitudes_candidatas": 0,
        "reemplazos": 0,
        "notificaciones_cliente": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_cliente": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_cliente": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "tareas": 0,
    }
    warnings: list[str] = []

    if _table_exists("solicitudes"):
        try:
            rows = (
                db.session.query(Solicitud.id)
                .filter(Solicitud.cliente_id == cid)
                .all()
            )
            solicitud_ids = [int(r[0]) for r in rows]
            summary["solicitudes"] = len(solicitud_ids)
        except SQLAlchemyError:
            warnings.append("No se pudo leer solicitudes del cliente.")
            summary["solicitudes"] = -1

    if _table_exists("solicitudes_candidatas") and solicitud_ids:
        summary["solicitudes_candidatas"] = _safe_count(
            db.session.query(func.count(SolicitudCandidata.id))
            .filter(SolicitudCandidata.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("reemplazos") and solicitud_ids:
        summary["reemplazos"] = _safe_count(
            db.session.query(func.count(Reemplazo.id))
            .filter(Reemplazo.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("clientes_notificaciones"):
        summary["notificaciones_cliente"] = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(ClienteNotificacion.cliente_id == cid)
        )
        if solicitud_ids:
            summary["notificaciones_solicitud"] = _safe_count(
                db.session.query(func.count(ClienteNotificacion.id))
                .filter(ClienteNotificacion.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("public_solicitud_tokens_usados"):
        summary["tokens_publicos_cliente"] = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(PublicSolicitudTokenUso.cliente_id == cid)
        )
        if solicitud_ids:
            summary["tokens_publicos_solicitud"] = _safe_count(
                db.session.query(func.count(PublicSolicitudTokenUso.id))
                .filter(PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        summary["tokens_cliente_nuevo_cliente"] = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
        )
        if solicitud_ids:
            summary["tokens_cliente_nuevo_solicitud"] = _safe_count(
                db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
                .filter(PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("tareas_clientes"):
        summary["tareas"] = _safe_count(
            db.session.query(func.count(TareaCliente.id))
            .filter(TareaCliente.cliente_id == cid)
        )

    blocked_issues: list[str] = []
    if solicitud_ids and _table_exists("clientes_notificaciones"):
        mismatch = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(
                ClienteNotificacion.solicitud_id.in_(solicitud_ids),
                ClienteNotificacion.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen notificaciones cruzadas con otro cliente en las solicitudes."
            )

    if solicitud_ids and _table_exists("public_solicitud_tokens_usados"):
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(
                PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids),
                PublicSolicitudTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens públicos cruzados con otro cliente en las solicitudes."
            )

    if solicitud_ids and _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids),
                PublicSolicitudClienteNuevoTokenUso.cliente_id.isnot(None),
                PublicSolicitudClienteNuevoTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens de cliente nuevo cruzados con otro cliente en las solicitudes."
            )

    # Protección defensiva: si hay tablas con FK a clientes/solicitudes no gestionadas,
    # bloquear para evitar borrados parciales inesperados.
    managed_tables = {
        "clientes",
        "solicitudes",
        "solicitudes_candidatas",
        "reemplazos",
        "clientes_notificaciones",
        "public_solicitud_tokens_usados",
        "public_solicitud_cliente_nuevo_tokens_usados",
        "tareas_clientes",
    }
    try:
        inspector = sa_inspect(db.engine)
        for table_name in inspector.get_table_names():
            if table_name in managed_tables:
                continue
            fks = inspector.get_foreign_keys(table_name) or []
            has_ref = any((fk.get("referred_table") or "") in {"clientes", "solicitudes"} for fk in fks)
            if not has_ref:
                continue
            tbl = Table(table_name, MetaData(), autoload_with=db.engine)
            row_hits = 0
            for fk in fks:
                ref_table = (fk.get("referred_table") or "").strip()
                cols = fk.get("constrained_columns") or []
                if not cols:
                    continue
                col_name = cols[0]
                if col_name not in tbl.c:
                    continue
                col = tbl.c[col_name]
                if ref_table == "clientes":
                    row_hits += int(
                        db.session.execute(
                            sa_select(func.count()).select_from(tbl).where(col == cid)
                        ).scalar() or 0
                    )
                elif ref_table == "solicitudes" and solicitud_ids:
                    row_hits += int(
                        db.session.execute(
                            sa_select(func.count()).select_from(tbl).where(col.in_(solicitud_ids))
                        ).scalar() or 0
                    )
            if row_hits > 0:
                blocked_issues.append(
                    f"Dependencia no gestionada detectada en tabla '{table_name}'."
                )
    except Exception:
        warnings.append("No se pudo completar la inspección de dependencias no gestionadas.")

    return {
        "cliente_id": cid,
        "solicitud_ids": solicitud_ids,
        "summary": summary,
        "warnings": warnings,
        "blocked_issues": blocked_issues,
    }


def _delete_cliente_tree(cliente_id: int, solicitud_ids: list[int]) -> dict[str, int]:
    cid = int(cliente_id or 0)
    deleted: dict[str, int] = {
        "solicitudes_candidatas": 0,
        "reemplazos": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "solicitudes": 0,
        "tareas": 0,
        "notificaciones_cliente": 0,
        "tokens_publicos_cliente": 0,
        "tokens_cliente_nuevo_cliente": 0,
        "cliente": 0,
    }

    if solicitud_ids and _table_exists("solicitudes_candidatas"):
        deleted["solicitudes_candidatas"] = int(
            SolicitudCandidata.query
            .filter(SolicitudCandidata.solicitud_id.in_(solicitud_ids))
            .delete(synchronize_session=False)
            or 0
        )

    if solicitud_ids and _table_exists("reemplazos"):
        deleted["reemplazos"] = int(
            Reemplazo.query
            .filter(Reemplazo.solicitud_id.in_(solicitud_ids))
            .delete(synchronize_session=False)
            or 0
        )

    if solicitud_ids and _table_exists("clientes_notificaciones"):
        deleted["notificaciones_solicitud"] = int(
            ClienteNotificacion.query
            .filter(
                ClienteNotificacion.solicitud_id.in_(solicitud_ids),
                ClienteNotificacion.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if solicitud_ids and _table_exists("public_solicitud_tokens_usados"):
        deleted["tokens_publicos_solicitud"] = int(
            PublicSolicitudTokenUso.query
            .filter(
                PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids),
                PublicSolicitudTokenUso.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if solicitud_ids and _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        deleted["tokens_cliente_nuevo_solicitud"] = int(
            PublicSolicitudClienteNuevoTokenUso.query
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids),
                (
                    (PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
                    | (PublicSolicitudClienteNuevoTokenUso.cliente_id.is_(None))
                ),
            )
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("solicitudes"):
        if solicitud_ids:
            deleted["solicitudes"] = int(
                Solicitud.query
                .filter(Solicitud.id.in_(solicitud_ids), Solicitud.cliente_id == cid)
                .delete(synchronize_session=False)
                or 0
            )
        else:
            deleted["solicitudes"] = int(
                Solicitud.query
                .filter(Solicitud.cliente_id == cid)
                .delete(synchronize_session=False)
                or 0
            )

    if _table_exists("tareas_clientes"):
        deleted["tareas"] = int(
            TareaCliente.query
            .filter(TareaCliente.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("clientes_notificaciones"):
        deleted["notificaciones_cliente"] = int(
            ClienteNotificacion.query
            .filter(ClienteNotificacion.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_tokens_usados"):
        deleted["tokens_publicos_cliente"] = int(
            PublicSolicitudTokenUso.query
            .filter(PublicSolicitudTokenUso.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        deleted["tokens_cliente_nuevo_cliente"] = int(
            PublicSolicitudClienteNuevoTokenUso.query
            .filter(PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    deleted["cliente"] = int(
        Cliente.query
        .filter(Cliente.id == cid)
        .delete(synchronize_session=False)
        or 0
    )
    return deleted


@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="delete_cliente", max_actions=10, window_sec=60)
def eliminar_cliente(cliente_id):
    """🗑️ Eliminar un cliente definitivamente."""
    _owner_only()
    c = Cliente.query.get_or_404(cliente_id)
    cliente_pk = int(getattr(c, "id", 0) or 0)
    cliente_code = str((getattr(c, "codigo", "") or "")).strip() or str(cliente_pk)

    plan = _collect_cliente_delete_plan(cliente_pk)
    blocked_issues = list(plan.get("blocked_issues") or [])
    warnings = list(plan.get("warnings") or [])
    summary = dict(plan.get("summary") or {})
    solicitud_ids = list(plan.get("solicitud_ids") or [])

    if blocked_issues:
        msg = "Este cliente no puede eliminarse de forma segura: " + " | ".join(blocked_issues)
        _audit_log(
            action_type="CLIENTE_DELETE_BLOCKED",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Borrado bloqueado para cliente {cliente_code}",
            metadata={
                "blocked_issues": blocked_issues,
                "warnings": warnings,
                "dependency_summary": summary,
            },
            success=False,
            error=msg,
        )
        flash(msg, "warning")
        return redirect(url_for("admin.listar_clientes"))

    try:
        deleted_rows: dict[str, int] = {}
        with db.session.begin_nested():
            deleted_rows = _delete_cliente_tree(cliente_pk, solicitud_ids=solicitud_ids)
            if int(deleted_rows.get("cliente") or 0) != 1:
                raise SQLAlchemyError("No se pudo confirmar la eliminación del cliente.")
            db.session.flush()
        db.session.commit()
        _audit_log(
            action_type="CLIENTE_DELETE_OK",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Cliente eliminado {cliente_code}",
            metadata={
                "deleted_rows": deleted_rows,
                "dependency_summary": summary,
                "warnings": warnings,
            },
            success=True,
        )
        flash('Cliente eliminado correctamente.', 'success')
    except IntegrityError:
        db.session.rollback()
        msg = (
            "No se pudo eliminar el cliente por restricciones de integridad. "
            "No se aplicaron cambios."
        )
        _audit_log(
            action_type="CLIENTE_DELETE_FAIL",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Error de integridad al eliminar cliente {cliente_code}",
            success=False,
            error=msg,
        )
        flash(msg, "danger")
    except SQLAlchemyError:
        db.session.rollback()
        msg = 'No se pudo eliminar el cliente de forma segura. No se aplicaron cambios.'
        _audit_log(
            action_type="CLIENTE_DELETE_FAIL",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Fallo técnico al eliminar cliente {cliente_code}",
            success=False,
            error=msg,
        )
        flash(msg, 'danger')

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
    reemplazos_activos = {int(s.id): _active_reemplazo_for_solicitud(s) for s in (solicitudes or [])}
    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")

    return render_template(
        'admin/cliente_detail.html',
        cliente=cliente,
        solicitudes=solicitudes,
        kpi_cliente=kpi_cliente,
        timeline=timeline,
        tareas=tareas,
        reemplazos_activos=reemplazos_activos,
        is_admin_role=is_admin_role,
    )


@admin_bp.route('/tareas/pendientes')
@login_required
@staff_required
def tareas_pendientes():
    """
    Lista todas las tareas que NO están completadas, ordenadas por fecha de vencimiento.
    """
    hoy = rd_today()

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
    hoy = rd_today()

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

    hoy = rd_today()

    try:
        tarea = TareaCliente(
            cliente_id=cliente.id,
            titulo=titulo,
            fecha_creacion=utc_now_naive(),
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
    public_pasaje_mode = "aparte" if bool(getattr(form, "pasaje_aporte", type("x", (object,), {"data": False})).data) else "incluido"
    public_pasaje_otro = ""

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

    if request.method == "POST":
        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")

    # POST válido
    if form.validate_on_submit():
        state = {"solicitud_id": 0, "tipo_servicio": None}
        try:
            nuevo_codigo = _next_codigo_solicitud(c)
            base_total = int(c.total_solicitudes or 0)

            def _persist_solicitud_create(_attempt: int):
                s = Solicitud(
                    cliente_id=c.id,
                    fecha_solicitud=utc_now_naive(),
                    codigo_solicitud=nuevo_codigo,
                )
                form.populate_obj(s)
                _normalize_modalidad_on_solicitud(s)

                if hasattr(form, 'sueldo'):
                    try:
                        s.sueldo = _norm_numeric_str(form.sueldo.data)
                    except Exception:
                        pass
                if hasattr(form, 'tipo_servicio'):
                    s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None
                s.tipo_lugar = _map_tipo_lugar(
                    getattr(s, 'tipo_lugar', ''),
                    getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
                )
                s.edad_requerida = _map_edad_choices(
                    codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                    edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                    otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
                )
                if hasattr(form, 'mascota'):
                    s.mascota = (form.mascota.data or '').strip() or None

                selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
                extra_text = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
                if 'otro' not in selected_codes:
                    extra_text = ''
                if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                    valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                    s.funciones = [code for code in selected_codes if code in valid_codes and code != 'otro']
                else:
                    s.funciones = [code for code in selected_codes if code != 'otro']
                if hasattr(s, 'funciones_otro'):
                    s.funciones_otro = extra_text or None

                selected_areas = []
                if hasattr(form, 'areas_comunes'):
                    selected_areas = _normalize_areas_comunes_selected(
                        selected_vals=getattr(form, 'areas_comunes', type('x', (object,), {'data': []})).data,
                        choices=form.areas_comunes.choices
                    )
                s.areas_comunes = selected_areas
                if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                    area_otro_txt = (form.area_otro.data or '').strip()
                    s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None
                s.detalles_servicio = _build_detalles_servicio_from_form(form)
                if hasattr(s, 'nota_cliente'):
                    s.nota_cliente = strip_pasaje_marker_from_note(getattr(s, 'nota_cliente', ''))
                apply_pasaje_to_solicitud(
                    s,
                    mode_raw=public_pasaje_mode,
                    text_raw=public_pasaje_otro,
                    default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
                )

                db.session.add(s)
                db.session.flush()
                state["solicitud_id"] = int(getattr(s, "id", 0) or 0)
                state["tipo_servicio"] = s.tipo_servicio

                c.total_solicitudes = base_total + 1
                c.fecha_ultima_solicitud = utc_now_naive()
                c.fecha_ultima_actividad = utc_now_naive()

            result = _execute_form_save(
                persist_fn=_persist_solicitud_create,
                verify_fn=lambda: _verify_solicitud_saved(
                    int(state.get("solicitud_id") or 0),
                    expected_cliente_id=c.id,
                    expected_codigo=nuevo_codigo,
                ),
                entity_type="Solicitud",
                entity_id=state.get("solicitud_id"),
                summary=f"Guardar nueva solicitud cliente={c.id}",
                metadata={"cliente_id": c.id, "codigo_solicitud": nuevo_codigo},
            )

            if result.ok:
                _audit_log(
                    action_type="SOLICITUD_CREATE",
                    entity_type="Solicitud",
                    entity_id=state.get("solicitud_id"),
                    summary=f"Solicitud creada: {nuevo_codigo}",
                    metadata={"cliente_id": c.id, "tipo_servicio": state.get("tipo_servicio")},
                )
                flash(f'Solicitud {nuevo_codigo} creada.', 'success')
                return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

            log_error_event(
                error_type="SAVE_ERROR",
                exc=f"Error robusto al crear solicitud: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=state.get("solicitud_id"),
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
        except Exception:
            db.session.rollback()
            log_error_event(
                error_type="SAVE_ERROR",
                exc="Error inesperado al crear solicitud",
                route=request.path,
                entity_type="solicitud",
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        nuevo=True,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
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
    public_pasaje_mode = "aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido"
    public_pasaje_otro = ""

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
        public_pasaje_mode, public_pasaje_otro = read_pasaje_mode_text(
            pasaje_aporte=getattr(s, "pasaje_aporte", False),
            detalles_servicio=getattr(s, "detalles_servicio", None),
            nota_cliente=getattr(s, "nota_cliente", ""),
        )

        # Detalles específicos (JSONB)
        _populate_form_detalles_from_solicitud(form, s)

    if request.method == "POST":
        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")

    # ─────────────────────────────────────────
    # POST válido
    # ─────────────────────────────────────────
    if form.validate_on_submit():
        try:
            def _persist_solicitud_update(_attempt: int):
                form.populate_obj(s)
                _normalize_modalidad_on_solicitud(s)

                if hasattr(form, 'sueldo'):
                    try:
                        s.sueldo = _norm_numeric_str(form.sueldo.data)
                    except Exception:
                        pass
                if hasattr(form, 'tipo_servicio'):
                    s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None
                s.tipo_lugar = _map_tipo_lugar(
                    getattr(s, 'tipo_lugar', ''),
                    getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
                )
                s.edad_requerida = _map_edad_choices(
                    codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                    edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                    otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
                )
                if hasattr(form, 'mascota'):
                    s.mascota = (form.mascota.data or '').strip() or None

                selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
                extra_text = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
                if 'otro' not in selected_codes:
                    extra_text = ''
                if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                    valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                    s.funciones = [code for code in selected_codes if code in valid_codes and code != 'otro']
                else:
                    s.funciones = [code for code in selected_codes if code != 'otro']
                if hasattr(s, 'funciones_otro'):
                    s.funciones_otro = extra_text or None

                if hasattr(form, 'areas_comunes'):
                    s.areas_comunes = _normalize_areas_comunes_selected(
                        selected_vals=form.areas_comunes.data,
                        choices=form.areas_comunes.choices
                    )
                if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                    area_otro_txt = (form.area_otro.data or '').strip()
                    s.area_otro = (area_otro_txt if 'otro' in (s.areas_comunes or []) else '') or None

                s.fecha_ultima_modificacion = utc_now_naive()
                s.detalles_servicio = _build_detalles_servicio_from_form(form)
                if hasattr(s, 'nota_cliente'):
                    s.nota_cliente = strip_pasaje_marker_from_note(getattr(s, 'nota_cliente', ''))
                apply_pasaje_to_solicitud(
                    s,
                    mode_raw=public_pasaje_mode,
                    text_raw=public_pasaje_otro,
                    default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
                )

            result = _execute_form_save(
                persist_fn=_persist_solicitud_update,
                verify_fn=lambda: _verify_solicitud_saved(
                    int(s.id),
                    expected_cliente_id=cliente_id,
                    expected_codigo=str(getattr(s, "codigo_solicitud", "") or ""),
                ),
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Editar solicitud {s.id}",
                metadata={"cliente_id": s.cliente_id},
            )

            if result.ok:
                _audit_log(
                    action_type="SOLICITUD_EDIT",
                    entity_type="Solicitud",
                    entity_id=s.id,
                    summary=f"Solicitud editada: {s.codigo_solicitud or s.id}",
                    metadata={"cliente_id": s.cliente_id, "tipo_servicio": s.tipo_servicio},
                )
                flash(f'Solicitud {s.codigo_solicitud} actualizada.', 'success')
                return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

            log_error_event(
                error_type="SAVE_ERROR",
                exc=f"Error robusto al actualizar solicitud: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=s.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
        except Exception:
            db.session.rollback()
            log_error_event(
                error_type="SAVE_ERROR",
                exc="Error inesperado al actualizar solicitud",
                route=request.path,
                entity_type="solicitud",
                entity_id=s.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        nuevo=False,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
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
        .filter(candidatas_activas_filter(Candidata))
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
        c.fecha_ultima_actividad = utc_now_naive()

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
            now = utc_now_naive()

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
        query = Candidata.query.filter(candidatas_activas_filter(Candidata))
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
        blocked = assert_candidata_no_descalificada(
            cand,
            action="asignar a solicitud",
            redirect_endpoint="admin.registrar_pago",
            redirect_kwargs={"cliente_id": cliente_id, "id": id, "q": q},
        )
        if blocked is not None:
            return blocked

        s.candidata_id = cand.fila
        _sync_solicitud_candidatas_after_assignment(s, cand.fila)
        _mark_candidata_estado(cand, "trabajando")

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
                    cand.fecha_de_pago = rd_today()

                db.session.add(cand)
            except Exception:
                # Si el sueldo viene raro, no rompemos el pago
                pass

        s.estado = 'pagada'
        s.fecha_ultima_modificacion = utc_now_naive()

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
    reemplazo_activo = _active_reemplazo_for_solicitud(sol)
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    fallback_detail = url_for('admin.detalle_cliente', cliente_id=sol.cliente_id)

    # ✅ SIEMPRE usar la candidata asignada originalmente a la solicitud (por relación)
    assigned_id = getattr(sol, 'candidata_id', None)

    # Si no hay candidata asignada, no se puede iniciar reemplazo
    if not assigned_id or not getattr(sol, 'candidata', None):
        flash(
            'Esta solicitud no tiene candidata asignada. Primero asigna una candidata (por pago/asignación) antes de iniciar un reemplazo.',
            'danger'
        )
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback_detail)
    if reemplazo_activo:
        flash('Ya existe un reemplazo activo para esta solicitud.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback_detail)

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
                return redirect(next_url if _is_safe_redirect_url(next_url) else fallback_detail)

            descalificar = str(request.form.get('descalificar_candidata_fallida') or '').strip().lower() in ('1', 'true', 'on', 'yes')
            motivo_descalificacion = (request.form.get('motivo_descalificacion') or '').strip()
            if descalificar and not motivo_descalificacion:
                flash('Debes indicar el motivo de descalificación.', 'warning')
                return render_template('admin/reemplazo_inicio.html', form=form, solicitud=sol)

            r = Reemplazo(
                solicitud_id=sol.id,
                candidata_old_id=cand_old.fila,
                motivo_fallo=(form.motivo_fallo.data or '').strip(),
                estado_previo_solicitud=(sol.estado or '').strip().lower() or None,
            )

            ahora = utc_now_naive()
            r.fecha_fallo = ahora
            r.iniciar_reemplazo()
            if getattr(r, 'fecha_inicio_reemplazo', None) is None:
                r.fecha_inicio_reemplazo = ahora
                r.oportunidad_nueva = True

            sol.estado = 'reemplazo'
            sol.fecha_ultima_actividad = ahora
            sol.fecha_ultima_modificacion = ahora
            mark_lista_from_state = None

            if descalificar:
                _mark_candidata_estado(cand_old, 'descalificada', nota_descalificacion=motivo_descalificacion)
            else:
                ready_ok, reasons = candidata_is_ready_to_send(cand_old)
                blocking = [rr for rr in (reasons or []) if not str(rr).lower().startswith("advertencia:")]
                if ready_ok and not blocking:
                    mark_lista_from_state = (getattr(cand_old, "estado", None) or "").strip().lower()
                    _mark_candidata_estado(cand_old, 'lista_para_trabajar')
                elif blocking:
                    flash(
                        "La candidata que falló no pudo volver a lista para trabajar. Falta: "
                        + "; ".join(blocking[:4]),
                        "warning",
                    )

            db.session.add(r)
            db.session.commit()
            _audit_log(
                action_type="REEMPLAZO_ABRIR",
                entity_type="Solicitud",
                entity_id=sol.id,
                summary=f"Reemplazo iniciado para solicitud {sol.codigo_solicitud or sol.id}",
                metadata={"reemplazo_id": r.id, "candidata_old_id": cand_old.fila, "descalificar": bool(descalificar)},
            )
            log_candidata_action(
                action_type="REEMPLAZO_OPEN",
                candidata=cand_old,
                summary=f"Reemplazo abierto para candidata en solicitud {sol.codigo_solicitud or sol.id}",
                metadata={
                    "reemplazo_id": r.id,
                    "solicitud_id": sol.id,
                    "cliente_id": sol.cliente_id,
                    "descalificar": bool(descalificar),
                },
                success=True,
            )
            if not descalificar and (getattr(cand_old, "estado", None) or "").strip().lower() == "lista_para_trabajar":
                _log_lista_state_change(
                    cand_old,
                    source="auto",
                    faltantes=[],
                    from_state=mark_lista_from_state,
                )

            flash('Reemplazo iniciado correctamente.', 'success')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback_detail)

        except Exception:
            db.session.rollback()
            _audit_log(
                action_type="REEMPLAZO_ABRIR",
                entity_type="Solicitud",
                entity_id=sol.id,
                summary=f"Fallo iniciando reemplazo para solicitud {sol.id}",
                success=False,
                error="Error al iniciar reemplazo.",
            )
            log_candidata_action(
                action_type="REEMPLAZO_OPEN",
                candidata=cand_old if 'cand_old' in locals() else None,
                summary=f"Fallo iniciando reemplazo para solicitud {sol.id}",
                metadata={"solicitud_id": sol.id, "cliente_id": sol.cliente_id},
                success=False,
                error="Error al iniciar reemplazo.",
            )
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
            .filter(candidatas_activas_filter(Candidata))
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
            blocked = assert_candidata_no_descalificada(
                cand_new,
                action="asignar a solicitud",
                redirect_endpoint="admin.finalizar_reemplazo",
                redirect_kwargs={"s_id": s_id, "reemplazo_id": reemplazo_id, "q": q},
            )
            if blocked is not None:
                return blocked

            ahora = utc_now_naive()

            # Guardar reemplazo
            r.candidata_new_id = cand_new.fila

            if hasattr(form, 'nota_adicional'):
                r.nota_adicional = (form.nota_adicional.data or '').strip() or None

            if hasattr(r, 'fecha_fin_reemplazo'):
                r.fecha_fin_reemplazo = ahora
            elif hasattr(r, 'fecha_fin'):
                r.fecha_fin = ahora

            # Reasignar solicitud
            s.candidata_id = cand_new.fila
            estado_restore = (getattr(r, "estado_previo_solicitud", None) or "activa").strip().lower()
            if estado_restore == "reemplazo":
                estado_restore = "activa"
            s.estado = estado_restore
            _sync_solicitud_candidatas_after_assignment(s, cand_new.fila)
            _mark_candidata_estado(cand_new, "trabajando")

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
                        cand_new.fecha_de_pago = rd_today()

                    if hasattr(cand_new, 'fecha_ultima_modificacion'):
                        cand_new.fecha_ultima_modificacion = ahora

                    db.session.add(cand_new)
                except Exception:
                    # Si el sueldo viene raro, no rompemos el flujo
                    pass

            db.session.commit()
            _audit_log(
                action_type="REEMPLAZO_CERRAR",
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Reemplazo finalizado para solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila},
            )
            cand_old = Candidata.query.filter_by(fila=getattr(r, "candidata_old_id", None)).first()
            if cand_old is not None:
                log_candidata_action(
                    action_type="REEMPLAZO_CLOSE",
                    candidata=cand_old,
                    summary=f"Reemplazo cerrado (sale candidata) en solicitud {s.codigo_solicitud or s.id}",
                    metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id, "candidata_new_id": cand_new.fila},
                    success=True,
                )
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_new,
                summary=f"Reemplazo cerrado (entra candidata) en solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                success=True,
            )
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
            _audit_log(
                action_type="REEMPLAZO_CERRAR",
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Fallo finalizando reemplazo para solicitud {s.id}",
                metadata={"reemplazo_id": r.id},
                success=False,
                error=str(e),
            )
            if 'cand_new' in locals() and cand_new is not None:
                log_candidata_action(
                    action_type="REEMPLAZO_CLOSE",
                    candidata=cand_new,
                    summary=f"Fallo cerrando reemplazo para solicitud {s.id}",
                    metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                    success=False,
                    error=str(e),
                )
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


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/cancelar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="reemplazos", max_actions=15, window_sec=60)
def cancelar_reemplazo(s_id, reemplazo_id):
    s = Solicitud.query.filter_by(id=s_id).first_or_404()
    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    fallback = url_for("admin.detalle_solicitud", cliente_id=s.cliente_id, id=s.id)

    if getattr(r, "fecha_fin_reemplazo", None):
        flash("Este reemplazo ya está cerrado.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        r.cerrar_reemplazo()
        if hasattr(r, "oportunidad_nueva"):
            r.oportunidad_nueva = False

        estado_restore = (getattr(r, "estado_previo_solicitud", None) or "").strip().lower()
        if estado_restore not in ("proceso", "activa", "pagada", "cancelada"):
            estado_restore = "activa"
        s.estado = estado_restore
        if hasattr(s, "fecha_ultima_actividad"):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, "fecha_ultima_modificacion"):
            s.fecha_ultima_modificacion = utc_now_naive()

        db.session.commit()
        _audit_log(
            action_type="REEMPLAZO_CANCELAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Reemplazo cancelado para solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id},
        )
        flash("Reemplazo cancelado correctamente.", "success")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="REEMPLAZO_CANCELAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cancelando reemplazo para solicitud {s.id}",
            metadata={"reemplazo_id": r.id},
            success=False,
            error="No se pudo cancelar el reemplazo.",
        )
        flash("No se pudo cancelar el reemplazo.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/cerrar_asignando', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="reemplazos", max_actions=20, window_sec=60)
def cerrar_reemplazo_asignando(s_id, reemplazo_id):
    s = Solicitud.query.filter_by(id=s_id).first_or_404()
    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    fallback = url_for("admin.detalle_solicitud", cliente_id=s.cliente_id, id=s.id)

    if getattr(r, "fecha_fin_reemplazo", None):
        flash("Este reemplazo ya está cerrado.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        nueva_id = int((request.form.get("candidata_new_id") or "").strip())
    except Exception:
        nueva_id = 0
    if nueva_id <= 0:
        flash("Debes indicar la candidata nueva para cerrar el reemplazo.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    cand_new = Candidata.query.filter_by(fila=nueva_id).first()
    if not cand_new:
        flash("La candidata seleccionada no existe.", "danger")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    blocked = assert_candidata_no_descalificada(
        cand_new,
        action="asignar a solicitud",
        redirect_endpoint="admin.detalle_solicitud",
        redirect_kwargs={"cliente_id": s.cliente_id, "id": s.id},
    )
    if blocked is not None:
        return blocked

    try:
        r.cerrar_reemplazo(cand_new.fila)

        s.candidata_id = cand_new.fila
        estado_restore = (getattr(r, "estado_previo_solicitud", None) or "").strip().lower()
        if estado_restore in ("", "reemplazo", "cancelada"):
            estado_restore = "activa"
        s.estado = estado_restore

        _sync_solicitud_candidatas_after_assignment(s, cand_new.fila)
        _mark_candidata_estado(cand_new, "trabajando")
        if hasattr(s, "fecha_ultima_actividad"):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, "fecha_ultima_modificacion"):
            s.fecha_ultima_modificacion = utc_now_naive()

        db.session.commit()
        _audit_log(
            action_type="REEMPLAZO_CERRAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Reemplazo cerrado asignando candidata en solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila},
        )
        cand_old = Candidata.query.filter_by(fila=getattr(r, "candidata_old_id", None)).first()
        if cand_old is not None:
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_old,
                summary=f"Reemplazo cerrado (sale candidata) en solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id, "candidata_new_id": cand_new.fila},
                success=True,
            )
        log_candidata_action(
            action_type="REEMPLAZO_CLOSE",
            candidata=cand_new,
            summary=f"Reemplazo cerrado (entra candidata) en solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
            success=True,
        )
        flash("Reemplazo cerrado y nueva candidata asignada.", "success")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="REEMPLAZO_CERRAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cerrando reemplazo por asignación en solicitud {s.id}",
            metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila if cand_new else None},
            success=False,
            error="No se pudo cerrar el reemplazo.",
        )
        if cand_new is not None:
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_new,
                summary=f"Fallo cerrando reemplazo por asignación en solicitud {s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                success=False,
                error="No se pudo cerrar el reemplazo.",
            )
        flash("No se pudo cerrar el reemplazo.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>')
@login_required
@staff_required
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
    reemplazo_activo = _active_reemplazo_for_solicitud(s)
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
    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")

    return render_template(
        'admin/solicitud_detail.html',
        solicitud      = s,
        envios         = envios,
        cancelaciones  = cancelaciones,
        reemplazos     = reemplazos_ordenados,
        reemplazo_activo=reemplazo_activo,
        resumen_cliente=resumen_cliente,
        is_admin_role=is_admin_role,
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

    ahora = utc_now_naive()
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
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().lower()

    try:
        page = int(request.args.get('page', 1) or 1)
    except Exception:
        page = 1
    page = max(1, page)

    try:
        per_page = int(request.args.get('per_page', 25) or 25)
    except Exception:
        per_page = 25
    per_page = max(10, min(per_page, 200))

    allowed_states = ['proceso', 'activa', 'reemplazo', 'espera_pago', 'pagada', 'cancelada']

    query = (
        Solicitud.query
        .options(
            load_only(
                Solicitud.id,
                Solicitud.cliente_id,
                Solicitud.candidata_id,
                Solicitud.codigo_solicitud,
                Solicitud.ciudad_sector,
                Solicitud.estado,
                Solicitud.fecha_solicitud,
                Solicitud.last_copiado_at,
            ),
            joinedload(Solicitud.cliente),
            joinedload(Solicitud.candidata),
            selectinload(Solicitud.reemplazos).load_only(
                Reemplazo.id,
                Reemplazo.fecha_inicio_reemplazo,
                Reemplazo.fecha_fin_reemplazo,
                Reemplazo.created_at,
            ),
        )
    )

    if estado and estado in allowed_states:
        query = query.filter(Solicitud.estado == estado)
    else:
        estado = ''

    if q:
        like = f"%{q}%"
        query = (
            query
            .outerjoin(Cliente, Solicitud.cliente_id == Cliente.id)
            .outerjoin(Candidata, Solicitud.candidata_id == Candidata.fila)
            .filter(or_(
                Solicitud.codigo_solicitud.ilike(like),
                Solicitud.ciudad_sector.ilike(like),
                Solicitud.rutas_cercanas.ilike(like),
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                Cliente.telefono.ilike(like),
                Candidata.nombre_completo.ilike(like),
                Candidata.codigo.ilike(like),
                Candidata.cedula.ilike(like),
            ))
        )

    query = query.order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
    total = query.order_by(None).count()

    solicitudes = (
        query
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
        .all()
    )
    has_more = len(solicitudes) > per_page
    if has_more:
        solicitudes = solicitudes[:per_page]

    reemplazos_activos = {}
    for s in solicitudes:
        repl = _active_reemplazo_for_solicitud(s)
        if repl:
            reemplazos_activos[s.id] = repl

    proc_count = Solicitud.query.filter_by(estado='proceso').count()

    start_utc, _ = _today_utc_bounds()
    try:
        copiable_count = (
            Solicitud.query
            .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
            .filter(
                or_(
                    Solicitud.last_copiado_at.is_(None),
                    Solicitud.last_copiado_at < start_utc
                )
            )
            .count()
        )
    except SQLAlchemyError:
        db.session.rollback()
        copiable_count = Solicitud.query.filter(Solicitud.estado.in_(('activa', 'reemplazo'))).count()
        flash(
            "No se pudo aplicar el filtro de copia diaria; mostrando el total de solicitudes activas/reemplazo.",
            "warning",
        )

    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")

    return render_template(
        'admin/solicitudes_list.html',
        proc_count=proc_count,
        copiable_count=copiable_count,
        q=q,
        estado=estado,
        allowed_states=allowed_states,
        solicitudes=solicitudes,
        reemplazos_activos=reemplazos_activos,
        is_admin_role=is_admin_role,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )


def _matching_created_by() -> str:
    try:
        if getattr(current_user, "is_authenticated", False):
            username = getattr(current_user, "username", None) or getattr(current_user, "id", None)
            if username:
                return str(username)
    except Exception:
        pass
    return str(session.get("usuario") or "sistema")


_NOTIF_TIPO_CANDIDATAS_ENVIADAS = "candidatas_enviadas"
_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")
_ASSIGNMENT_CLOSEABLE_STATUS = ("sugerida", "enviada", "vista", "seleccionada")


def _upsert_cliente_notificacion_candidatas(solicitud: Solicitud, count: int) -> None:
    if not solicitud or not getattr(solicitud, "cliente_id", None) or int(count or 0) <= 0:
        return

    now = utc_now_naive()
    recent_from = now - timedelta(hours=24)
    titulo = "Candidatas enviadas"
    cuerpo = (
        f"La agencia te envio candidatas compatibles para la solicitud "
        f"{(getattr(solicitud, 'codigo_solicitud', None) or f'SOL-{solicitud.id}') }."
    )

    existing = (
        ClienteNotificacion.query
        .filter_by(
            cliente_id=solicitud.cliente_id,
            solicitud_id=solicitud.id,
            tipo=_NOTIF_TIPO_CANDIDATAS_ENVIADAS,
            is_read=False,
            is_deleted=False,
        )
        .filter(ClienteNotificacion.created_at >= recent_from)
        .order_by(ClienteNotificacion.id.desc())
        .first()
    )

    if existing:
        prev_payload = existing.payload if isinstance(existing.payload, dict) else {}
        prev_count = 0
        try:
            prev_count = int(prev_payload.get("count") or 0)
        except Exception:
            prev_count = 0
        existing.payload = {"count": max(0, prev_count) + int(count)}
        existing.titulo = titulo
        existing.cuerpo = cuerpo
        existing.updated_at = now
        return

    notif = ClienteNotificacion(
        cliente_id=solicitud.cliente_id,
        solicitud_id=solicitud.id,
        tipo=_NOTIF_TIPO_CANDIDATAS_ENVIADAS,
        titulo=titulo,
        cuerpo=cuerpo,
        payload={"count": int(count)},
        is_read=False,
        is_deleted=False,
    )
    db.session.add(notif)


def _matching_candidate_flags(solicitud: Solicitud, candidata_ids: list[int]) -> tuple[set[int], set[int]]:
    """Devuelve (bloqueadas_por_otro_cliente, rechazadas_por_mismo_cliente)."""
    blocked_ids: set[int] = set()
    rejected_ids: set[int] = set()
    if not solicitud or not candidata_ids:
        return blocked_ids, rejected_ids

    try:
        active_rows = (
            db.session.query(SolicitudCandidata.candidata_id)
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id.in_(candidata_ids),
                SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
                SolicitudCandidata.solicitud_id != solicitud.id,
                Solicitud.cliente_id != solicitud.cliente_id,
            )
            .all()
        )
        blocked_ids = {int(row[0]) for row in active_rows if row and row[0] is not None}
    except Exception:
        blocked_ids = set()

    try:
        rejected_rows = (
            db.session.query(SolicitudCandidata.candidata_id)
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id.in_(candidata_ids),
                SolicitudCandidata.status == "descartada",
                Solicitud.cliente_id == solicitud.cliente_id,
            )
            .all()
        )
        rejected_ids = {int(row[0]) for row in rejected_rows if row and row[0] is not None}
    except Exception:
        rejected_ids = set()

    return blocked_ids, rejected_ids


def _sync_solicitud_candidatas_after_assignment(solicitud: Solicitud, assigned_candidata_id: int, actor: str = "") -> None:
    """
    Al asignar una candidata en Solicitud:
    - candidata asignada => status 'seleccionada'
    - resto de candidatas de esa solicitud en estados abiertos => status 'liberada'
    """
    if not solicitud or not getattr(solicitud, "id", None) or not assigned_candidata_id:
        return

    now_iso = iso_utc_z(utc_now_naive())
    actor_value = actor or _matching_created_by()

    assigned_row = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=solicitud.id, candidata_id=int(assigned_candidata_id))
        .first()
    )
    if assigned_row:
        assigned_row.status = "seleccionada"
        assigned_row.created_by = actor_value
    else:
        assigned_row = SolicitudCandidata(
            solicitud_id=solicitud.id,
            candidata_id=int(assigned_candidata_id),
            status="seleccionada",
            created_by=actor_value,
        )
        db.session.add(assigned_row)

    rows = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=solicitud.id)
        .all()
    )
    for row in rows:
        if int(getattr(row, "candidata_id", 0) or 0) == int(assigned_candidata_id):
            continue
        if (getattr(row, "status", None) or "") not in _ASSIGNMENT_CLOSEABLE_STATUS:
            continue
        row.status = "liberada"
        snapshot = row.breakdown_snapshot if isinstance(row.breakdown_snapshot, dict) else {}
        snapshot["client_action"] = "liberada_por_asignacion"
        snapshot["client_action_at"] = now_iso
        snapshot["assigned_candidata_id"] = int(assigned_candidata_id)
        row.breakdown_snapshot = snapshot


def _staff_actor_name() -> str:
    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )
    return str(actor)[:100]


def _mark_candidata_estado(cand: Candidata, nuevo_estado: str, *, nota_descalificacion: str | None = None) -> None:
    if not cand:
        return
    cand.estado = str(nuevo_estado or "").strip().lower()
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        cand.usuario_cambio_estado = _staff_actor_name()
    if nota_descalificacion is not None and hasattr(cand, "nota_descalificacion"):
        cand.nota_descalificacion = (nota_descalificacion or "").strip() or None


def _log_lista_state_change(cand: Candidata, *, source: str, faltantes: list[str] | None = None, from_state: str | None = None) -> None:
    if not cand:
        return
    from_value = (from_state or "").strip().lower() or None
    to_value = (getattr(cand, "estado", None) or "").strip().lower() or "lista_para_trabajar"
    log_candidata_action(
        action_type="CANDIDATA_MARK_LISTA",
        candidata=cand,
        summary=f"Candidata marcada lista para trabajar: {cand.nombre_completo or cand.fila}",
        metadata={
            "reason": "readiness_ok",
            "faltantes": list(faltantes or []),
            "source": (source or "manual").strip().lower(),
        },
        changes={"estado": {"from": from_value, "to": to_value}},
        success=True,
    )


def _active_reemplazo_for_solicitud(solicitud: Solicitud):
    if not solicitud:
        return None
    activos = [
        r for r in (getattr(solicitud, "reemplazos", None) or [])
        if bool(getattr(r, "fecha_inicio_reemplazo", None)) and not bool(getattr(r, "fecha_fin_reemplazo", None))
    ]
    if not activos:
        return None
    return sorted(
        activos,
        key=lambda rr: getattr(rr, "fecha_inicio_reemplazo", None) or getattr(rr, "created_at", None) or datetime.min,
        reverse=True,
    )[0]


@admin_bp.route('/matching/solicitudes')
@login_required
@staff_required
def matching_solicitudes():
    solicitudes = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter(Solicitud.estado.in_(("activa", "reemplazo")))
        .order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        .limit(300)
        .all()
    )
    return render_template("admin/matching_solicitudes.html", solicitudes=solicitudes)


@admin_bp.route('/matching/solicitudes/<int:solicitud_id>')
@login_required
@staff_required
def matching_detalle_solicitud(solicitud_id: int):
    solicitud = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente), joinedload(Solicitud.reemplazos))
        .filter_by(id=solicitud_id)
        .first_or_404()
    )
    has_reemplazo_activo = _active_reemplazo_for_solicitud(solicitud) is not None
    ranked_candidates = rank_candidates(solicitud, top_k=30)
    ranked_candidate_ids = []
    for item in ranked_candidates:
        try:
            ranked_candidate_ids.append(int(item["candidate"].fila))
        except Exception:
            continue
    blocked_candidate_ids, rejected_candidate_ids = _matching_candidate_flags(solicitud, ranked_candidate_ids)
    disqualified_candidate_ids = {
        int(item["candidate"].fila)
        for item in ranked_candidates
        if candidata_esta_descalificada(item.get("candidate"))
    }
    sent_candidates = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=solicitud.id)
        .order_by(SolicitudCandidata.created_at.desc(), SolicitudCandidata.id.desc())
        .all()
    )
    return render_template(
        "admin/matching_detalle.html",
        solicitud=solicitud,
        ranked_candidates=ranked_candidates,
        sent_candidates=sent_candidates,
        blocked_candidate_ids=blocked_candidate_ids,
        rejected_candidate_ids=rejected_candidate_ids,
        disqualified_candidate_ids=disqualified_candidate_ids,
        has_reemplazo_activo=has_reemplazo_activo,
    )


@admin_bp.route('/matching/solicitudes/<int:solicitud_id>/enviar', methods=['POST'])
@login_required
@staff_required
def matching_enviar_candidatas(solicitud_id: int):
    solicitud = Solicitud.query.filter_by(id=solicitud_id).first_or_404()
    raw_ids = request.form.getlist("candidata_ids")
    candidata_ids = []
    for raw in raw_ids:
        try:
            val = int(str(raw).strip())
            if val > 0:
                candidata_ids.append(val)
        except Exception:
            continue
    candidata_ids = sorted(set(candidata_ids))

    if not candidata_ids:
        flash("Selecciona al menos una candidata para enviar.", "warning")
        return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))

    force_send = str(request.form.get("force_send") or "").strip() in ("1", "true", "on", "yes")
    blocked_candidate_ids, rejected_candidate_ids = _matching_candidate_flags(solicitud, candidata_ids)
    selected_blocked = sorted(set(candidata_ids) & blocked_candidate_ids)
    if selected_blocked:
        flash(
            "Esta candidata ya fue enviada a otro cliente. Solo puede enviarse a otro cuando sea rechazada.",
            "danger",
        )
        return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))

    selected_rejected = sorted(set(candidata_ids) & rejected_candidate_ids)
    if selected_rejected and not force_send:
        flash(
            "⚠️ Esta candidata fue rechazada por este cliente anteriormente. Marca 'Enviar de todas formas' para confirmar.",
            "warning",
        )
        return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))

    selected_disqualified = {
        int(c.fila)
        for c in Candidata.query.filter(Candidata.fila.in_(candidata_ids)).all()
        if candidata_esta_descalificada(c)
    }
    if selected_disqualified:
        abort(
            403,
            description=(
                "No se puede enviar una candidata descalificada al cliente."
            ),
        )

    selected_not_ready = {}
    for c in Candidata.query.filter(Candidata.fila.in_(candidata_ids)).all():
        ready_ok, reasons = candidata_is_ready_to_send(c)
        if ready_ok:
            continue
        selected_not_ready[int(c.fila)] = [
            r for r in (reasons or []) if not str(r).lower().startswith("advertencia:")
        ]
    if selected_not_ready:
        sample_id = sorted(selected_not_ready.keys())[0]
        reasons = selected_not_ready.get(sample_id) or ["Faltan requisitos de completitud."]
        details = "; ".join(reasons[:4])
        flash(f"Esta candidata no está lista para enviar: {details}", "danger")
        abort(400, description=f"Esta candidata no está lista para enviar: {details}")

    ranking_map = {item["candidate"].fila: item for item in rank_candidates(solicitud, top_k=30)}
    created_by = _matching_created_by()
    processed_candidates = []
    state = {"processed": 0, "processed_ids": []}

    try:
        def _persist_matching_send(_attempt: int):
            state["processed"] = 0
            state["processed_ids"] = []
            processed_candidates.clear()
            for candidata_id in candidata_ids:
                cand = Candidata.query.filter_by(fila=candidata_id).first()
                if not cand:
                    continue

                exists = (
                    SolicitudCandidata.query
                    .filter_by(solicitud_id=solicitud.id, candidata_id=candidata_id)
                    .first()
                )

                ranked_item = ranking_map.get(candidata_id) or {"score": 0, "breakdown_snapshot": {}}
                breakdown_snapshot = ranked_item.get("breakdown_snapshot") or {
                    "city_detectada": "Ciudad no detectada",
                    "tokens_match": "Tokens sin coincidencia fuerte",
                    "rutas_match": "Rutas sin coincidencia fuerte",
                    "modalidad_match": "Sin datos",
                    "horario_match": "Sin datos",
                    "skills_match": "Sin datos",
                    "mascota_penalty": "Sin datos",
                    "test_bonus": "Bonus test: +0",
                    "components": list(ranked_item.get("breakdown") or []),
                }
                if exists:
                    exists.score_snapshot = int(ranked_item.get("score") or 0)
                    exists.breakdown_snapshot = breakdown_snapshot
                    exists.status = "enviada"
                    exists.created_by = created_by
                else:
                    row = SolicitudCandidata(
                        solicitud_id=solicitud.id,
                        candidata_id=candidata_id,
                        score_snapshot=int(ranked_item.get("score") or 0),
                        breakdown_snapshot=breakdown_snapshot,
                        status="enviada",
                        created_by=created_by,
                    )
                    db.session.add(row)
                state["processed"] = int(state.get("processed", 0)) + 1
                state["processed_ids"].append(candidata_id)
                processed_candidates.append(cand)

            if int(state.get("processed", 0)) > 0:
                _upsert_cliente_notificacion_candidatas(solicitud, int(state.get("processed", 0)))

        def _verify_matching_rows() -> bool:
            if int(state.get("processed", 0)) <= 0:
                return False
            try:
                saved_count = (
                    SolicitudCandidata.query
                    .filter(
                        SolicitudCandidata.solicitud_id == solicitud.id,
                        SolicitudCandidata.candidata_id.in_(state.get("processed_ids") or []),
                        SolicitudCandidata.status == "enviada",
                    )
                    .count()
                )
                return int(saved_count) == len(state.get("processed_ids") or [])
            except Exception:
                return len(state.get("processed_ids") or []) == int(state.get("processed", 0))

        result = _execute_form_save(
            persist_fn=_persist_matching_send,
            verify_fn=_verify_matching_rows,
            entity_type="Solicitud",
            entity_id=solicitud.id,
            summary=f"Guardar envío matching solicitud {solicitud.id}",
            metadata={"candidata_ids": candidata_ids},
        )

        if int(state.get("processed", 0)) > 0 and result.ok:
            _audit_log(
                action_type="MATCHING_SEND",
                entity_type="Solicitud",
                entity_id=solicitud.id,
                summary=f"Envío de candidatas en matching para solicitud {solicitud.codigo_solicitud or solicitud.id}",
                metadata={"candidata_ids": candidata_ids, "processed": int(state.get("processed", 0))},
            )
            for cand in processed_candidates:
                log_candidata_action(
                    action_type="MATCHING_SEND",
                    candidata=cand,
                    summary=f"Candidata enviada en matching a solicitud {solicitud.codigo_solicitud or solicitud.id}",
                    metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                    success=True,
                )
            flash(f"Candidata enviada al cliente. Total procesadas: {int(state.get('processed', 0))}.", "success")
        elif not result.ok:
            log_error_event(
                error_type="MATCHING_ERROR",
                exc=f"Error robusto enviando candidatas en matching: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=solicitud.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            _audit_log(
                action_type="MATCHING_SEND",
                entity_type="Solicitud",
                entity_id=solicitud.id,
                summary=f"Fallo enviando candidatas en matching para solicitud {solicitud.id}",
                metadata={"candidata_ids": candidata_ids, "processed_ids": state.get("processed_ids") or []},
                success=False,
                error="No se pudieron enviar candidatas.",
            )
            for cand in processed_candidates:
                log_candidata_action(
                    action_type="MATCHING_SEND",
                    candidata=cand,
                    summary=f"Fallo enviando candidata en matching para solicitud {solicitud.id}",
                    metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                    success=False,
                    error="No se pudieron enviar candidatas.",
                )
            flash("No se pudo guardar correctamente. Intente nuevamente.", "danger")
        else:
            db.session.rollback()
            flash("No se encontraron candidatas válidas para enviar.", "warning")
    except Exception:
        db.session.rollback()
        log_error_event(
            error_type="MATCHING_ERROR",
            exc="Error enviando candidatas en matching",
            route=request.path,
            entity_type="solicitud",
            entity_id=solicitud.id,
            request_id=request.headers.get("X-Request-ID"),
            status_code=500,
        )
        _audit_log(
            action_type="MATCHING_SEND",
            entity_type="Solicitud",
            entity_id=solicitud.id,
            summary=f"Fallo enviando candidatas en matching para solicitud {solicitud.id}",
            metadata={"candidata_ids": candidata_ids},
            success=False,
            error="No se pudieron enviar candidatas.",
        )
        for cand in processed_candidates:
            log_candidata_action(
                action_type="MATCHING_SEND",
                candidata=cand,
                summary=f"Fallo enviando candidata en matching para solicitud {solicitud.id}",
                metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                success=False,
                error="No se pudieron enviar candidatas.",
            )
        flash("No se pudieron enviar candidatas. Intenta nuevamente.", "danger")

    return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))


def _blob_len_expr(col):
    dialect = ""
    try:
        bind = db.session.get_bind()
        if bind is not None and bind.dialect is not None:
            dialect = str(bind.dialect.name or "").lower()
    except Exception:
        dialect = ""

    if dialect == "postgresql":
        return func.coalesce(func.octet_length(col), 0)
    return func.coalesce(func.length(col), 0)


def _build_auditoria_completitud_rows(q: str = "") -> list[dict]:
    q = (q or "").strip()[:128]
    base = Candidata.query.options(
        load_only(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.cedula,
            Candidata.codigo,
            Candidata.estado,
            Candidata.entrevista,
            Candidata.referencias_laboral,
            Candidata.referencias_familiares,
        )
    ).filter(
        Candidata.codigo.isnot(None),
        Candidata.codigo != "",
        func.length(func.trim(Candidata.codigo)) > 0,
    )
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    entrevistas_subq = (
        db.session.query(
            Entrevista.candidata_id.label("candidata_id"),
            func.count(Entrevista.id).label("entrevistas_count"),
        )
        .group_by(Entrevista.candidata_id)
        .subquery()
    )

    rows = (
        base.outerjoin(entrevistas_subq, entrevistas_subq.c.candidata_id == Candidata.fila)
        .add_columns(
            func.coalesce(entrevistas_subq.c.entrevistas_count, 0).label("entrevistas_count"),
            _blob_len_expr(Candidata.depuracion).label("depuracion_len"),
            _blob_len_expr(Candidata.perfil).label("perfil_len"),
            _blob_len_expr(Candidata.cedula1).label("cedula1_len"),
            _blob_len_expr(Candidata.cedula2).label("cedula2_len"),
        )
        .order_by(Candidata.fila.desc())
        .all()
    )

    audits: list[dict] = []
    for cand, entrevistas_count, dep_len, perfil_len, ced1_len, ced2_len in rows:
        flags = {
            "entrevista": entrevista_ok(getattr(cand, "entrevista", None), entrevistas_count),
            "depuracion": binario_ok(dep_len),
            "perfil": binario_ok(perfil_len),
            "cedula1": binario_ok(ced1_len),
            "cedula2": binario_ok(ced2_len),
            "referencias_laboral": referencias_ok(getattr(cand, "referencias_laboral", None)),
            "referencias_familiares": referencias_ok(getattr(cand, "referencias_familiares", None)),
        }
        faltantes = faltantes_desde_flags(flags)
        audits.append(
            {
                "candidata": cand,
                "flags": flags,
                "faltantes": faltantes,
                "tiene": [k for k, ok in flags.items() if ok],
                "incompleta": es_incompleta(flags),
            }
        )
    return audits


def _links_completar_por_faltantes(candidata_id: int, faltantes: list[str]) -> list[dict]:
    faltantes_set = set(faltantes or [])
    links: list[dict] = []

    if faltantes_set.intersection({"depuracion", "perfil", "cedula1", "cedula2"}):
        links.append(
            {
                "label": "Documentos",
                "url": url_for("subir_fotos.subir_fotos", accion="subir", fila=candidata_id),
            }
        )
    if "entrevista" in faltantes_set:
        links.append(
            {
                "label": "Entrevista",
                "url": url_for("entrevistas_de_candidata", fila=candidata_id),
            }
        )
    if faltantes_set.intersection({"referencias_laboral", "referencias_familiares"}):
        links.append(
            {
                "label": "Referencias",
                "url": url_for("referencias", candidata=candidata_id),
            }
        )
    links.append(
        {
            "label": "Editar candidata",
            "url": url_for("buscar_candidata", candidata_id=candidata_id),
        }
    )
    return links


@admin_bp.route('/candidatas/auditoria-completitud', methods=['GET'])
@login_required
@staff_required
def candidatas_auditoria_completitud():
    q = (request.args.get("q") or "").strip()[:128]
    solo_criticas = (request.args.get("solo_criticas") or "").strip() in ("1", "true", "on")
    solo_docs = (request.args.get("solo_docs") or "").strip() in ("1", "true", "on")
    solo_refs = (request.args.get("solo_refs") or "").strip() in ("1", "true", "on")

    audits_all = _build_auditoria_completitud_rows(q=q)
    total_analizadas = len(audits_all)
    completas = sum(
        1
        for a in audits_all
        if candidata_tiene_codigo_valido(getattr(a.get("candidata"), "codigo", None)) and not a["incompleta"]
    )
    incompletas = [
        a for a in audits_all
        if candidata_tiene_codigo_valido(getattr(a.get("candidata"), "codigo", None)) and a["incompleta"]
    ]

    if solo_criticas:
        incompletas = [a for a in incompletas if solo_criticos(a["faltantes"])]
    if solo_docs:
        incompletas = [a for a in incompletas if solo_sin_documentos(a["faltantes"])]
    if solo_refs:
        incompletas = [a for a in incompletas if solo_sin_referencias(a["faltantes"])]

    labels = {
        "entrevista": "Entrevista",
        "depuracion": "Depuración",
        "perfil": "Perfil",
        "cedula1": "Cédula 1",
        "cedula2": "Cédula 2",
        "referencias_laboral": "Ref laboral",
        "referencias_familiares": "Ref familiar",
    }
    for row in incompletas:
        cand = row["candidata"]
        row["tiene_labels"] = [labels[k] for k in row["tiene"]]
        row["faltantes_labels"] = [labels[k] for k in row["faltantes"]]
        row["links_completar"] = _links_completar_por_faltantes(cand.fila, row["faltantes"])

    return render_template(
        "admin/candidatas_auditoria_completitud.html",
        q=q,
        total_analizadas=total_analizadas,
        total_completas=completas,
        total_incompletas=len(incompletas),
        auditorias=incompletas,
        solo_criticas=solo_criticas,
        solo_docs=solo_docs,
        solo_refs=solo_refs,
        labels=labels,
    )


@admin_bp.route('/candidatas/descalificacion', methods=['GET'])
@login_required
@staff_required
def candidatas_descalificacion():
    q = (request.args.get("q") or "").strip()[:128]
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", default=25, type=int)))

    base = Candidata.query
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    pagination = (
        base.order_by(Candidata.fila.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")
    return render_template(
        "admin/candidatas_descalificacion.html",
        q=q,
        candidatas=pagination.items,
        pagination=pagination,
        page=page,
        per_page=per_page,
        is_admin_role=is_admin_role,
    )


@admin_bp.route('/candidatas/<int:candidata_id>/descalificar', methods=['POST'])
@login_required
@admin_required
def descalificar_candidata(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    motivo = (request.form.get("motivo") or "").strip()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if not motivo:
        flash("Debes indicar el motivo de descalificación.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    cand.estado = "descalificada"
    if hasattr(cand, "nota_descalificacion"):
        cand.nota_descalificacion = motivo
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        cand.usuario_cambio_estado = str(actor)[:100]

    try:
        def _verify_descalificada() -> bool:
            try:
                return bool(Candidata.query.filter_by(fila=cand.fila, estado="descalificada").first())
            except Exception:
                return str(getattr(cand, "estado", "") or "") == "descalificada"

        result = _execute_form_save(
            persist_fn=lambda _attempt: None,
            verify_fn=_verify_descalificada,
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Descalificar candidata {cand.fila}",
            metadata={"motivo": motivo},
        )
        if result.ok:
            _audit_log(
                action_type="CANDIDATA_DESCALIFICAR",
                entity_type="Candidata",
                entity_id=cand.fila,
                summary=f"Candidata descalificada: {cand.nombre_completo or cand.fila}",
                metadata={"motivo": motivo},
                changes={"estado": {"from": "lista_para_trabajar", "to": "descalificada"}},
            )
            log_candidata_action(
                action_type="CANDIDATA_DESQUALIFY",
                candidata=cand,
                summary=f"Candidata descalificada: {cand.nombre_completo or cand.fila}",
                metadata={"motivo": motivo},
                changes={"estado": {"from": "lista_para_trabajar", "to": "descalificada"}},
                success=True,
            )
            flash("Candidata descalificada correctamente.", "success")
        else:
            flash("No se pudo guardar correctamente. Intente nuevamente.", "danger")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="CANDIDATA_DESCALIFICAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Fallo descalificando candidata {cand.fila}",
            metadata={"motivo": motivo},
            success=False,
            error="No se pudo descalificar la candidata.",
        )
        log_candidata_action(
            action_type="CANDIDATA_DESQUALIFY",
            candidata=cand,
            summary=f"Fallo descalificando candidata {cand.fila}",
            metadata={"motivo": motivo},
            success=False,
            error="No se pudo descalificar la candidata.",
        )
        flash("No se pudo descalificar la candidata.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/reactivar', methods=['POST'])
@login_required
@admin_required
def reactivar_candidata(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    # Estado operativo por defecto para volver a usar en matching/banco.
    cand.estado = "lista_para_trabajar"
    if hasattr(cand, "nota_descalificacion"):
        cand.nota_descalificacion = None
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        cand.usuario_cambio_estado = str(actor)[:100]

    try:
        def _verify_reactivada() -> bool:
            try:
                return bool(Candidata.query.filter_by(fila=cand.fila, estado="lista_para_trabajar").first())
            except Exception:
                return str(getattr(cand, "estado", "") or "") == "lista_para_trabajar"

        result = _execute_form_save(
            persist_fn=lambda _attempt: None,
            verify_fn=_verify_reactivada,
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Reactivar candidata {cand.fila}",
            metadata={},
        )
        if result.ok:
            _audit_log(
                action_type="CANDIDATA_REACTIVAR",
                entity_type="Candidata",
                entity_id=cand.fila,
                summary=f"Candidata reactivada: {cand.nombre_completo or cand.fila}",
                changes={"estado": {"from": "descalificada", "to": "lista_para_trabajar"}},
            )
            log_candidata_action(
                action_type="CANDIDATA_REACTIVATE",
                candidata=cand,
                summary=f"Candidata reactivada: {cand.nombre_completo or cand.fila}",
                changes={"estado": {"from": "descalificada", "to": "lista_para_trabajar"}},
                success=True,
            )
            flash("Candidata reactivada correctamente.", "success")
        else:
            flash("No se pudo guardar correctamente. Intente nuevamente.", "danger")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="CANDIDATA_REACTIVAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Fallo reactivando candidata {cand.fila}",
            success=False,
            error="No se pudo reactivar la candidata.",
        )
        log_candidata_action(
            action_type="CANDIDATA_REACTIVATE",
            candidata=cand,
            summary=f"Fallo reactivando candidata {cand.fila}",
            success=False,
            error="No se pudo reactivar la candidata.",
        )
        flash("No se pudo reactivar la candidata.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/marcar_trabajando', methods=['POST'])
@login_required
@staff_required
def marcar_candidata_trabajando(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if candidata_esta_descalificada(cand):
        flash("No se puede marcar como trabajando una candidata descalificada.", "danger")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    cand.estado = "trabajando"
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        cand.usuario_cambio_estado = str(actor)[:100]

    try:
        def _verify_trabajando() -> bool:
            try:
                return bool(Candidata.query.filter_by(fila=cand.fila, estado="trabajando").first())
            except Exception:
                return str(getattr(cand, "estado", "") or "") == "trabajando"

        result = _execute_form_save(
            persist_fn=lambda _attempt: None,
            verify_fn=_verify_trabajando,
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Marcar candidata trabajando {cand.fila}",
            metadata={},
        )
        if result.ok:
            _audit_log(
                action_type="CANDIDATA_ESTADO_TRABAJANDO",
                entity_type="Candidata",
                entity_id=cand.fila,
                summary=f"Candidata marcada trabajando: {cand.nombre_completo or cand.fila}",
                changes={"estado": {"from": "lista_para_trabajar", "to": "trabajando"}},
            )
            log_candidata_action(
                action_type="CANDIDATA_MARK_TRABAJANDO",
                candidata=cand,
                summary=f"Candidata marcada trabajando: {cand.nombre_completo or cand.fila}",
                changes={"estado": {"from": "lista_para_trabajar", "to": "trabajando"}},
                success=True,
            )
            flash("Candidata marcada como trabajando.", "success")
        else:
            flash("No se pudo guardar correctamente. Intente nuevamente.", "danger")
    except Exception:
        db.session.rollback()
        log_candidata_action(
            action_type="CANDIDATA_MARK_TRABAJANDO",
            candidata=cand,
            summary=f"Fallo marcando candidata trabajando {cand.fila}",
            success=False,
            error="No se pudo actualizar estado a trabajando.",
        )
        flash("No se pudo actualizar el estado a trabajando.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/marcar_lista_para_trabajar', methods=['POST'])
@login_required
@staff_required
def marcar_candidata_lista_para_trabajar(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if candidata_esta_descalificada(cand):
        flash("No se puede marcar como lista para trabajar una candidata descalificada.", "danger")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    ready_ok, reasons = candidata_is_ready_to_send(cand)
    blocking = [r for r in (reasons or []) if not str(r).lower().startswith("advertencia:")]
    if not ready_ok or blocking:
        flash(
            "No se puede pasar a lista para trabajar. Falta: "
            + "; ".join(blocking[:4]),
            "warning",
        )
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_previo = (getattr(cand, "estado", None) or "").strip().lower()
    cand.estado = "lista_para_trabajar"
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        cand.usuario_cambio_estado = str(actor)[:100]

    try:
        def _verify_lista() -> bool:
            try:
                return bool(Candidata.query.filter_by(fila=cand.fila, estado="lista_para_trabajar").first())
            except Exception:
                return str(getattr(cand, "estado", "") or "") == "lista_para_trabajar"

        result = _execute_form_save(
            persist_fn=lambda _attempt: None,
            verify_fn=_verify_lista,
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Marcar candidata lista_para_trabajar {cand.fila}",
            metadata={},
        )
        if result.ok:
            _audit_log(
                action_type="CANDIDATA_ESTADO_LISTA",
                entity_type="Candidata",
                entity_id=cand.fila,
                summary=f"Candidata marcada lista para trabajar: {cand.nombre_completo or cand.fila}",
                changes={"estado": {"from": estado_previo or None, "to": "lista_para_trabajar"}},
            )
            _log_lista_state_change(
                cand,
                source="manual",
                faltantes=[],
                from_state=estado_previo,
            )
            log_candidata_action(
                action_type="CANDIDATA_ESTADO_LISTA",
                candidata=cand,
                summary=f"Estado candidata actualizado a lista para trabajar: {cand.nombre_completo or cand.fila}",
                metadata={"source": "manual"},
                changes={"estado": {"from": estado_previo or None, "to": "lista_para_trabajar"}},
                success=True,
            )
            flash("Candidata marcada como lista para trabajar.", "success")
        else:
            flash("No se pudo guardar correctamente. Intente nuevamente.", "danger")
    except Exception:
        db.session.rollback()
        log_candidata_action(
            action_type="CANDIDATA_MARK_LISTA",
            candidata=cand,
            summary=f"Fallo marcando candidata lista para trabajar {cand.fila}",
            success=False,
            error="No se pudo actualizar estado a lista para trabajar.",
        )
        flash("No se pudo actualizar el estado a lista para trabajar.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


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
    hoy = rd_today()
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
    base = (dt or utc_now_naive()).date()
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
            load_only(
                Solicitud.id,
                Solicitud.estado,
                Solicitud.candidata_id,
                Solicitud.codigo_solicitud,
                Solicitud.fecha_solicitud,
                Solicitud.last_copiado_at,
                Solicitud.ciudad_sector,
                Solicitud.modalidad_trabajo,
                Solicitud.rutas_cercanas,
                Solicitud.funciones,
                Solicitud.funciones_otro,
                Solicitud.tipo_lugar,
                Solicitud.habitaciones,
                Solicitud.banos,
                Solicitud.dos_pisos,
                Solicitud.areas_comunes,
                Solicitud.area_otro,
                Solicitud.adultos,
                Solicitud.ninos,
                Solicitud.edades_ninos,
                Solicitud.mascota,
                Solicitud.edad_requerida,
                Solicitud.experiencia,
                Solicitud.horario,
                Solicitud.sueldo,
                Solicitud.pasaje_aporte,
                Solicitud.nota_cliente,
                Solicitud.detalles_servicio,
                Solicitud.tipo_servicio,
            ),
            selectinload(Solicitud.reemplazos).load_only(
                Reemplazo.id,
                Reemplazo.oportunidad_nueva,
                Reemplazo.candidata_new_id,
                Reemplazo.fecha_inicio_reemplazo,
                Reemplazo.fecha_fin_reemplazo,
            ).selectinload(Reemplazo.candidata_new).load_only(
                Candidata.fila,
                Candidata.nombre_completo,
            ),
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
            'estado': s.estado,
            'candidata_id': getattr(s, 'candidata_id', None),
            'direccion': getattr(s, 'direccion', None),
            'reemplazos': reems,
            'funcs': funcs,
            'modalidad': modalidad,
            'order_text': order_text
        })

    has_more = (page * per_page) < total
    is_admin_role = _current_staff_role() in ('admin', 'owner')
    return render_template(
        'admin/solicitudes_copiar.html',
        solicitudes=solicitudes,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more,
        is_admin_role=is_admin_role
    )




@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@staff_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado not in ('activa', 'reemplazo'):
        flash('Esta solicitud no es copiable en su estado actual.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    start_utc, _ = _utc_day_bounds()
    last = _to_naive_utc(getattr(s, 'last_copiado_at', None))
    if last is not None and last >= start_utc:
        flash('Esta solicitud ya fue marcada como copiada hoy.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        s.last_copiado_at = func.now()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_PUBLICAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud marcada como publicada/copiada: {s.codigo_solicitud or s.id}",
        )
        flash(f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo marcar la solicitud como copiada.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al marcar como copiada.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/solicitudes/<int:id>/pausar_espera_perfil', methods=['POST'])
@login_required
@staff_required
def pausar_espera_perfil_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado == 'espera_pago':
        flash('La solicitud ya está en pausa por espera de perfil.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada', 'pagada'):
        flash('No se puede pausar por espera de perfil en el estado actual.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        s.estado = 'espera_pago'
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PERFIL_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud en pausa por espera de perfil: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        flash('Solicitud pausada por espera de perfil.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo pausar la solicitud por espera de perfil.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/solicitudes/<int:id>/reanudar_espera_perfil', methods=['POST'])
@login_required
@staff_required
def reanudar_espera_perfil_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado != 'espera_pago':
        flash('La solicitud no está en pausa por espera de perfil.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada', 'pagada'):
            restore = 'activa'
        s.estado = restore
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PERFIL_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reanudada desde espera de perfil: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        flash(f'Solicitud reanudada y puesta en {restore}.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo reanudar la solicitud.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


def _copiar_wants_json() -> bool:
    try:
        accept = (request.headers.get('Accept') or '').lower()
        xrw = (request.headers.get('X-Requested-With') or '').lower()
        return bool(request.is_json or ('application/json' in accept) or (xrw == 'xmlhttprequest'))
    except Exception:
        return False


def _copiar_action_response(*, ok: bool, message: str, category: str, next_url: str, fallback: str, http_status: int = 200, extra=None):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    if _copiar_wants_json():
        payload = {
            "ok": bool(ok),
            "message": message,
            "category": category,
            "next": safe_next,
        }
        if extra:
            payload.update(extra)
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


@admin_bp.route('/solicitudes/copiar/candidatas_lookup', methods=['GET'])
@login_required
@admin_required
def candidatas_lookup_copiar():
    q = (request.args.get('q') or '').strip()
    include_raw = (request.args.get('include_id') or '').strip()
    try:
        limit = int(request.args.get('limit', 50) or 50)
    except Exception:
        limit = 50
    limit = max(1, min(limit, 100))

    base_q = (
        Candidata.query
        .options(load_only(Candidata.fila, Candidata.nombre_completo))
        .filter(candidatas_activas_filter(Candidata))
    )

    items = []
    seen = set()
    if q:
        like = f"%{q}%"
        rows = (
            base_q
            .filter(or_(
                Candidata.nombre_completo.ilike(like),
                cast(Candidata.fila, db.Text).ilike(like),
            ))
            .order_by(Candidata.nombre_completo.asc())
            .limit(limit)
            .all()
        )
        for cand in rows:
            seen.add(int(cand.fila))
            items.append({
                "value": str(cand.fila),
                "text": f"{cand.nombre_completo} (ID {cand.fila})",
            })

    include_id = None
    try:
        include_id = int(include_raw) if include_raw else None
    except Exception:
        include_id = None
    if include_id and include_id not in seen:
        include_c = (
            base_q
            .filter(Candidata.fila == include_id)
            .first()
        )
        if include_c:
            items.insert(0, {
                "value": str(include_c.fila),
                "text": f"{include_c.nombre_completo} (ID {include_c.fila})",
            })

    return jsonify({
        "ok": True,
        "q": q,
        "count": len(items),
        "items": items,
    })


@admin_bp.route('/solicitudes/<int:id>/cancelar_desde_copiar', methods=['POST'])
@login_required
@staff_required
def cancelar_solicitud_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')
    estado_actual = (s.estado or '').strip().lower()

    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        return _copiar_action_response(
            ok=False,
            message='Indica un motivo de cancelación (mínimo 5 caracteres).',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=400,
        )

    if estado_actual in ('cancelada', 'pagada'):
        return _copiar_action_response(
            ok=False,
            message=f'La solicitud {s.codigo_solicitud} no admite cancelación en su estado actual.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    if estado_actual not in ('proceso', 'activa', 'reemplazo', 'espera_pago'):
        return _copiar_action_response(
            ok=False,
            message=f'No se puede cancelar la solicitud en estado «{s.estado}».',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    try:
        s.estado = 'cancelada'
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = _now_utc()
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_CANCELAR_DESDE_COPIAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud cancelada desde copiar/publicar: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "cancelada"}},
            metadata={"motivo": motivo[:255]},
        )
        return _copiar_action_response(
            ok=True,
            message=f'Solicitud {s.codigo_solicitud} cancelada.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": "cancelada", "remove_card": True},
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo cancelar la solicitud.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='Ocurrió un error al cancelar la solicitud.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )


@admin_bp.route('/solicitudes/<int:id>/marcar_pagada_desde_copiar', methods=['POST'])
@login_required
@admin_required
def marcar_pagada_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada', 'pagada'):
        return _copiar_action_response(
            ok=False,
            message='Esta solicitud no admite marcarse como pagada en su estado actual.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    candidata_raw = (request.form.get('candidata_id') or '').strip()
    monto_raw = (request.form.get('monto_pagado') or '').strip()
    if not candidata_raw or not monto_raw:
        return _copiar_action_response(
            ok=False,
            message='Para marcar pagado debes indicar candidata y monto pagado.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=400,
        )

    try:
        candidata_id = int(candidata_raw)
    except Exception:
        return _copiar_action_response(
            ok=False,
            message='La candidata seleccionada no es válida.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=400,
        )

    cand = Candidata.query.get(candidata_id)
    if not cand:
        return _copiar_action_response(
            ok=False,
            message='La candidata seleccionada no existe.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=404,
        )

    if candidata_esta_descalificada(cand):
        return _copiar_action_response(
            ok=False,
            message='No se puede asignar una candidata descalificada.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    try:
        s.candidata_id = cand.fila
        _sync_solicitud_candidatas_after_assignment(s, cand.fila)
        _mark_candidata_estado(cand, "trabajando")
        s.monto_pagado = _parse_money_to_decimal_str(monto_raw)
        s.estado = 'pagada'
        s.fecha_ultima_modificacion = utc_now_naive()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_MARCAR_PAGADA_DESDE_COPIAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud marcada pagada desde copiar/publicar: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "pagada"}},
            metadata={"candidata_id": cand.fila, "monto_pagado": s.monto_pagado},
        )
        return _copiar_action_response(
            ok=True,
            message='Solicitud marcada como pagada.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": "pagada", "remove_card": True, "candidata_id": cand.fila},
        )
    except ValueError as e:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message=f'Monto pagado inválido: {e}',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=400,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo marcar la solicitud como pagada.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='Ocurrió un error al marcar la solicitud como pagada.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )


# =============================================================================
#                 VISTAS "EN PROCESO" Y RESUMEN DIARIO (MEJORADAS)
# =============================================================================

# Utilidades compartidas (si ya las definiste antes, no las dupliques):
def _now_utc() -> datetime:
    return utc_now_naive()

def _utc_day_bounds(dt: datetime | None = None):
    """(inicio_día_utc, fin_día_utc) para dt (o hoy UTC)."""
    base = (dt or utc_now_naive()).date()
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


@admin_bp.route('/solicitudes/<int:id>/poner_espera_pago', methods=['POST'])
@login_required
@staff_required
def poner_espera_pago_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)

    if s.estado == 'espera_pago':
        flash('La solicitud ya está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada',):
        flash('No se puede poner en espera de pago una solicitud cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        s.estado = 'espera_pago'
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud puesta en espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        flash('Solicitud marcada en espera de pago.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo cambiar la solicitud a espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/espera_pago/poner', methods=['POST'])
@login_required
@staff_required
def poner_espera_pago_solicitud_cliente(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id) + f"#sol-{s.id}"

    if s.estado == 'espera_pago':
        flash('La solicitud ya está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada',):
        flash('No se puede poner en espera de pago una solicitud cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        s.estado = 'espera_pago'
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud puesta en espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        flash('Solicitud marcada en espera de pago.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo cambiar la solicitud a espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/solicitudes/<int:id>/quitar_espera_pago', methods=['POST'])
@login_required
@staff_required
def quitar_espera_pago_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)

    if s.estado != 'espera_pago':
        flash('La solicitud no está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada'):
            restore = 'activa'
        s.estado = restore
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reactivada desde espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        flash(f'Solicitud reactivada desde espera de pago a {restore}.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo quitar espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/espera_pago/quitar', methods=['POST'])
@login_required
@staff_required
def quitar_espera_pago_solicitud_cliente(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id) + f"#sol-{s.id}"

    if s.estado != 'espera_pago':
        flash('La solicitud no está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada'):
            restore = 'activa'
        s.estado = restore
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reactivada desde espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        flash(f'Solicitud reactivada desde espera de pago a {restore}.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo quitar espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

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

def calc_score_compat(solicitud: Solicitud, candidata: Candidata):
    """
    DEPRECATED: conservar alias local mientras todo el sistema consume el engine único.
    """
    return format_compat_result(compute_match(solicitud, candidata))

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
    res = format_compat_result(compute_match(solicitud, candidata))

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
    res = format_compat_result(compute_match(solicitud, candidata))

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
    try:
        max_age_days = int((os.getenv("PUBLIC_SOLICITUD_TOKEN_MAX_AGE_DAYS") or "30").strip())
    except Exception:
        max_age_days = 30
    max_age_days = max(1, min(365, max_age_days))

    return render_template(
        'admin/cliente_link_publico_solicitud.html',
        cliente=c,
        link_publico=link,
        max_age_days=max_age_days,
    )


@admin_bp.route('/solicitudes/nueva-publica/link', methods=['GET'])
@login_required
@staff_required
def generar_link_publico_cliente_nuevo():
    token = generar_token_publico_cliente_nuevo(
        created_by=str(getattr(current_user, "username", "") or getattr(current_user, "id", "") or "")
    )
    link = url_for('clientes.solicitud_publica_nueva_token', token=token, _external=True)
    try:
        max_age_days = int((os.getenv("PUBLIC_SOLICITUD_NUEVA_TOKEN_MAX_AGE_DAYS") or "30").strip())
    except Exception:
        max_age_days = 30
    max_age_days = max(1, min(365, max_age_days))

    return render_template(
        'admin/cliente_nuevo_link_publico_solicitud.html',
        link_publico=link,
        max_age_days=max_age_days,
    )
