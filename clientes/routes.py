# -*- coding: utf-8 -*-
from datetime import datetime, date, timedelta
from functools import wraps
import os
import re
import json
import hashlib
import hmac
import secrets
import time
import urllib.parse
from typing import Optional, Union  # ✅ PARA PYTHON 3.9

from flask import (
    render_template, redirect, url_for, flash,
    request, abort, g, session, current_app, jsonify, make_response, send_file, Response, stream_with_context
)
from flask_login import (
    login_required, current_user, login_user, logout_user
)
from werkzeug.security import check_password_hash
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, OperationalError

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from config_app import db, cache
try:
    from models import (
        Cliente,
        Solicitud,
        Candidata,
        CandidataWeb,
        SolicitudCandidata,
        ClienteNotificacion,
        ChatConversation,
        ChatMessage,
        StaffUser,
        PublicSolicitudTokenUso,
        PublicSolicitudClienteNuevoTokenUso,
        PublicSolicitudShareAlias,
        DomainOutbox,
    )
except Exception:
    from models import Cliente, Solicitud
    Candidata = None
    CandidataWeb = None
    SolicitudCandidata = None
    ClienteNotificacion = None
    ChatConversation = None
    ChatMessage = None
    StaffUser = None
    PublicSolicitudTokenUso = None
    PublicSolicitudClienteNuevoTokenUso = None
    PublicSolicitudShareAlias = None
    DomainOutbox = None

from utils.guards import candidata_esta_descalificada, candidatas_activas_filter
from utils.matching_explain import client_bullets_from_breakdown
from utils.audit_logger import log_action, log_auth_event
from utils.business_guard import enforce_business_limit, enforce_min_human_interval
from utils.distributed_backplane import bp_add, bp_delete, bp_get, bp_healthcheck, bp_set
from utils.robust_save import execute_robust_save
from services.candidata_invariants import (
    InvariantConflictError,
    release_solicitud_candidatas_on_cancel as invariant_release_solicitud_candidatas_on_cancel,
    transition_solicitud_candidata_status as invariant_transition_solicitud_candidata_status,
)
from services.solicitud_estado import set_solicitud_estado
from utils.pasaje_mode import (
    apply_pasaje_to_solicitud,
    normalize_pasaje_mode_text,
    read_pasaje_mode_text,
    strip_pasaje_marker_from_note,
)
from utils.modalidad import (
    canonicalize_modalidad_trabajo,
    split_modalidad_for_ui,
    should_preserve_existing_modalidad_on_edit,
)
from utils.codigo_solicitud import compose_codigo_solicitud
from utils.timezone import (
    iso_utc_z,
    rd_today,
    to_rd,
    utc_now_naive,
    utc_timestamp,
)
from utils.chat_e2e_guard import (
    E2EChatGuardError,
    chat_e2e_enabled,
    chat_e2e_scope_prefix,
    chat_e2e_scope_key,
    chat_e2e_run_id,
    chat_e2e_subject,
    chat_e2e_tag,
    enforce_e2e_cliente_id,
    enforce_e2e_conversation,
    enforce_e2e_conversation_id,
    enforce_e2e_solicitud_id,
    e2e_message_meta,
)

# ✅ IMPORTANTE: traemos también AREAS_COMUNES_CHOICES desde forms
from .forms import (
    AREAS_COMUNES_CHOICES,
    ClienteLoginForm,
    ClienteCancelForm,
    SolicitudForm,
    ClienteSolicitudForm,
    SolicitudPublicaForm,
    SolicitudClienteNuevoPublicaForm,
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
CLIENTE_CODIGO_PUBLICO_MIN = 2152
PUBLIC_SHARE_CODE_LENGTH = 10
PUBLIC_SHARE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BUSINESS_ACTIVE_SOLICITUD_STATES = {"proceso", "activa", "reemplazo", "espera_pago"}
BUSINESS_MAX_CLIENTE_CREACIONES_DIA = int((os.getenv("BUSINESS_MAX_CLIENTE_CREACIONES_DIA") or "6").strip() or 6)
BUSINESS_MAX_CLIENTE_ACTIVAS = int((os.getenv("BUSINESS_MAX_CLIENTE_ACTIVAS") or "4").strip() or 4)
BUSINESS_MAX_PUBLIC_IP_DIA = int((os.getenv("BUSINESS_MAX_PUBLIC_IP_DIA") or "20").strip() or 20)


# ─────────────────────────────────────────────────────────────
# 🔒 Anti fuerza bruta (clientes/login)  IP + identificador
# ─────────────────────────────────────────────────────────────
_CLIENTE_LOGIN_MAX_INTENTOS = int((os.getenv("CLIENTE_LOGIN_MAX_INTENTOS") or "10").strip() or 10)
_CLIENTE_LOGIN_LOCK_MINUTOS = int((os.getenv("CLIENTE_LOGIN_LOCK_MINUTOS") or "10").strip() or 10)
_CLIENTE_LOGIN_KEY_PREFIX   = "cliente_login"


def _operational_rate_limits_enabled() -> bool:
    raw = os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS")
    if raw is not None and str(raw).strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    return run_env in ("prod", "production")


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
    return bool(bp_healthcheck(strict=False))


def _cliente_is_locked(ident_norm: str) -> bool:
    if not _operational_rate_limits_enabled():
        return False
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        return bool(bp_get(keys["lock"], default=False, context="cliente_login_is_locked"))
    return False


def _cliente_register_fail(ident_norm: str) -> int:
    if not _operational_rate_limits_enabled():
        return 0
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        n = int(bp_get(keys["fail"], default=0, context="cliente_login_fail_get") or 0) + 1
        bp_set(
            keys["fail"],
            n,
            timeout=_CLIENTE_LOGIN_LOCK_MINUTOS * 60,
            context="cliente_login_fail_set",
        )

        if n >= _CLIENTE_LOGIN_MAX_INTENTOS:
            bp_set(
                keys["lock"],
                True,
                timeout=_CLIENTE_LOGIN_LOCK_MINUTOS * 60,
                context="cliente_login_lock_set",
            )
        return n

    return 1


def _cliente_reset_fail(ident_norm: str):
    if _cache_ok():
        keys = _cliente_login_keys(ident_norm)
        bp_delete(keys["fail"], context="cliente_login_fail_del")
        bp_delete(keys["lock"], context="cliente_login_lock_del")


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


def _cliente_active_solicitudes_count(cliente_id: int) -> int:
    try:
        cid = int(cliente_id or 0)
        if cid <= 0:
            return 0
        return (
            Solicitud.query
            .filter(
                Solicitud.cliente_id == cid,
                Solicitud.estado.in_(tuple(BUSINESS_ACTIVE_SOLICITUD_STATES)),
            )
            .count()
        )
    except Exception:
        return 0


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
        'clientes.solicitud_publica_short',
        'clientes.solicitud_publica_nueva',
        'clientes.solicitud_publica_nueva_token',
        'clientes.solicitud_publica_nueva_short',
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


def _norm_phone_digits(v: str) -> str:
    return re.sub(r"\D", "", (v or "").strip())


def _cliente_codigo_to_int(codigo: str) -> Optional[int]:
    raw = str(codigo or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", raw):
        return None
    try:
        value = int(raw.replace(",", ""))
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _format_cliente_codigo(value: int) -> str:
    return f"{int(value):,}"


def _next_cliente_codigo_publico() -> str:
    max_seen = int(CLIENTE_CODIGO_PUBLICO_MIN) - 1
    try:
        rows = db.session.query(Cliente.codigo).all()
    except Exception:
        rows = []
    for (codigo,) in rows:
        parsed = _cliente_codigo_to_int(codigo)
        if parsed is None:
            continue
        if parsed > max_seen:
            max_seen = parsed
    return _format_cliente_codigo(max_seen + 1)


def _find_cliente_contact_duplicate(email_norm: str, phone_raw: str):
    email_norm = _norm_email(email_norm)
    if email_norm:
        row = Cliente.query.filter(db.func.lower(Cliente.email) == email_norm).first()
        if row is not None:
            return row, "email"

    phone_digits = _norm_phone_digits(phone_raw)
    if not phone_digits:
        return None, ""

    try:
        phone_rows = Cliente.query.with_entities(Cliente.id, Cliente.telefono).all()
    except Exception:
        phone_rows = []
    for rid, tel in phone_rows:
        if _norm_phone_digits(tel or "") == phone_digits:
            row = Cliente.query.filter_by(id=int(rid)).first()
            if row is not None:
                return row, "telefono"
    return None, ""


def _public_link_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="clientes-solicitud-publica"
    )


def _public_link_max_age_seconds() -> int:
    raw_days = (os.getenv("PUBLIC_SOLICITUD_TOKEN_MAX_AGE_DAYS") or "30").strip()
    try:
        days = int(raw_days)
    except Exception:
        days = 30
    days = min(365, max(1, days))
    return int(timedelta(days=days).total_seconds())


def _public_link_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:16]


def _public_link_token_hash_storage(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _public_existing_short_external_url(token: str) -> str:
    """URL corta pública para solicitud de cliente existente."""
    return _public_external_url("clientes.solicitud_publica_short", token=token)


def _public_new_short_external_url(token: str) -> str:
    """URL corta pública para solicitud de cliente nuevo."""
    return _public_external_url("clientes.solicitud_publica_nueva_short", token=token)


def _public_share_external_url(code: str) -> str:
    """URL corporativa compartible para WhatsApp/preview."""
    return _public_external_url("public.solicitud_share_landing", code=code)


def _public_external_url(endpoint: str, **values) -> str:
    """
    Construye URLs absolutas para compartir previews.
    Si hay PUBLIC_BASE_URL configurado, se usa como host canónico.
    """
    base_raw = (
        (current_app.config.get("PUBLIC_BASE_URL") or "")
        or (os.getenv("PUBLIC_BASE_URL") or "")
        or "https://www.domesticadelcibao.com"
    ).strip()
    if base_raw:
        parsed = urllib.parse.urlparse(base_raw)
        if parsed.scheme and parsed.netloc:
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
            rel = url_for(endpoint, _external=False, **values).lstrip("/")
            return urllib.parse.urljoin(base.rstrip("/") + "/", rel)
    return url_for(endpoint, _external=True, **values)


_PUBLIC_TOKEN_USAGE_TABLE_READY = False
_PUBLIC_SHARE_ALIAS_TABLE_READY = False


def _ensure_public_share_alias_table() -> bool:
    global _PUBLIC_SHARE_ALIAS_TABLE_READY
    if _PUBLIC_SHARE_ALIAS_TABLE_READY:
        return True
    if PublicSolicitudShareAlias is None:
        return False
    try:
        PublicSolicitudShareAlias.__table__.create(bind=db.engine, checkfirst=True)
        _PUBLIC_SHARE_ALIAS_TABLE_READY = True
        return True
    except Exception:
        current_app.logger.exception("No se pudo asegurar la tabla de aliases publicos compartibles")
        return False


def _generate_public_share_code(length: int = PUBLIC_SHARE_CODE_LENGTH) -> str:
    size = max(8, min(20, int(length or PUBLIC_SHARE_CODE_LENGTH)))
    alphabet = PUBLIC_SHARE_CODE_ALPHABET
    return "".join(secrets.choice(alphabet) for _ in range(size))


def create_public_share_alias(
    *,
    token: str,
    link_type: str,
    created_by: str = "",
    max_attempts: int = 12,
):
    """
    Crea alias corto estable para compartir por WhatsApp sin exponer token largo.
    """
    token = str(token or "").strip()
    link_type = str(link_type or "").strip().lower()
    if not token:
        raise ValueError("missing_token")
    if link_type not in {"existente", "nuevo"}:
        raise ValueError("invalid_link_type")
    if not _ensure_public_share_alias_table():
        raise RuntimeError("share_alias_table_unavailable")
    if PublicSolicitudShareAlias is None:
        raise RuntimeError("share_alias_model_unavailable")

    token_hash = _public_link_token_hash_storage(token)
    existing = (
        PublicSolicitudShareAlias.query
        .filter_by(token_hash=token_hash, link_type=link_type, is_active=True)
        .order_by(PublicSolicitudShareAlias.id.desc())
        .first()
    )
    if existing is not None:
        return existing

    clean_created_by = str(created_by or "").strip()[:80] or None
    now_ref = utc_now_naive()
    for _ in range(max(1, int(max_attempts))):
        code = _generate_public_share_code()
        alias = PublicSolicitudShareAlias(
            code=code,
            link_type=link_type,
            token=token,
            token_hash=token_hash,
            is_active=True,
            created_by=clean_created_by,
            created_at=now_ref,
        )
        try:
            db.session.add(alias)
            db.session.commit()
            return alias
        except IntegrityError:
            db.session.rollback()
            continue
        except Exception:
            db.session.rollback()
            raise
    raise RuntimeError("share_alias_collision_exhausted")


def resolve_public_share_alias(code: str):
    code = (code or "").strip().upper()
    if not re.fullmatch(rf"[{PUBLIC_SHARE_CODE_ALPHABET}]{{8,20}}", code):
        return None
    if PublicSolicitudShareAlias is None:
        return None
    if not _ensure_public_share_alias_table():
        return None
    try:
        row = PublicSolicitudShareAlias.query.filter_by(code=code, is_active=True).first()
        if row is None:
            return None
        row.last_seen_at = utc_now_naive()
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        return None



def _ensure_public_token_usage_table() -> bool:
    global _PUBLIC_TOKEN_USAGE_TABLE_READY
    if _PUBLIC_TOKEN_USAGE_TABLE_READY:
        return True
    if PublicSolicitudTokenUso is None:
        return False
    if bool(current_app.config.get("TESTING")):
        _PUBLIC_TOKEN_USAGE_TABLE_READY = True
        return True
    try:
        PublicSolicitudTokenUso.__table__.create(bind=db.engine, checkfirst=True)
        _PUBLIC_TOKEN_USAGE_TABLE_READY = True
        return True
    except Exception:
        current_app.logger.exception("No se pudo asegurar la tabla de tokens publicos usados")
        return False


def _public_link_usage_by_hash(token_hash: str):
    if PublicSolicitudTokenUso is None or not token_hash:
        return None
    try:
        return PublicSolicitudTokenUso.query.filter_by(token_hash=token_hash).first()
    except Exception:
        return None


def _public_new_link_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="clientes-solicitud-publica-nueva"
    )


def _public_new_link_max_age_seconds() -> int:
    raw_days = (os.getenv("PUBLIC_SOLICITUD_NUEVA_TOKEN_MAX_AGE_DAYS") or "30").strip()
    try:
        days = int(raw_days)
    except Exception:
        days = 30
    days = min(365, max(1, days))
    return int(timedelta(days=days).total_seconds())


_PUBLIC_NEW_TOKEN_USAGE_TABLE_READY = False


def _ensure_public_new_token_usage_table() -> bool:
    global _PUBLIC_NEW_TOKEN_USAGE_TABLE_READY
    if _PUBLIC_NEW_TOKEN_USAGE_TABLE_READY:
        return True
    if PublicSolicitudClienteNuevoTokenUso is None:
        return False
    if bool(current_app.config.get("TESTING")):
        _PUBLIC_NEW_TOKEN_USAGE_TABLE_READY = True
        return True
    try:
        PublicSolicitudClienteNuevoTokenUso.__table__.create(bind=db.engine, checkfirst=True)
        _PUBLIC_NEW_TOKEN_USAGE_TABLE_READY = True
        return True
    except Exception:
        current_app.logger.exception("No se pudo asegurar la tabla de tokens publicos nuevos usados")
        return False


def _public_new_link_usage_by_hash(token_hash: str):
    if PublicSolicitudClienteNuevoTokenUso is None or not token_hash:
        return None
    try:
        return PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=token_hash).first()
    except Exception:
        return None


def generar_token_publico_cliente_nuevo(*, created_by: str = "") -> str:
    ser = _public_new_link_serializer()
    payload = {
        "v": 1,
        "purpose": "solicitud_publica_nueva",
        "nonce": secrets.token_urlsafe(18),
        "by": str(created_by or "")[:80],
    }
    return ser.dumps(payload)


def generar_link_publico_compartible_cliente_nuevo(*, created_by: str = "") -> str:
    token = generar_token_publico_cliente_nuevo(created_by=created_by)
    alias = create_public_share_alias(token=token, link_type="nuevo", created_by=created_by)
    return _public_share_external_url(alias.code)


def _resolve_public_new_link_token(token: str):
    metadata: dict = {}
    try:
        payload = _public_new_link_serializer().loads(token, max_age=_public_new_link_max_age_seconds())
    except SignatureExpired:
        return False, "expired", metadata
    except BadSignature:
        return False, "invalid_signature", metadata
    except Exception:
        return False, "invalid_payload", metadata

    if not isinstance(payload, dict):
        return False, "invalid_payload", metadata
    purpose = (payload.get("purpose") or "").strip().lower()
    if purpose != "solicitud_publica_nueva":
        return False, "invalid_purpose", metadata
    nonce = (payload.get("nonce") or "").strip()
    if not nonce:
        return False, "invalid_nonce", metadata
    metadata["issued_by"] = str(payload.get("by") or "")[:80]
    return True, "", metadata


def _cliente_public_link_fingerprint(cliente: Cliente) -> str:
    updated_at = getattr(cliente, "updated_at", None)
    updated_iso = updated_at.isoformat() if isinstance(updated_at, datetime) else ""
    raw = "|".join(
        [
            str(int(getattr(cliente, "id", 0) or 0)),
            str((getattr(cliente, "codigo", "") or "").strip().lower()),
            str((getattr(cliente, "email", "") or "").strip().lower()),
            "1" if bool(getattr(cliente, "is_active", False)) else "0",
            updated_iso,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _log_public_link_event(
    action_type: str,
    token: str,
    *,
    success: bool,
    reason: str = "",
    cliente_id: Optional[int] = None,
    metadata_extra: Optional[dict] = None,
) -> None:
    metadata = {
        "scope": "clientes_public_link",
        "token_hash": _public_link_token_hash(token),
        "reason": str(reason or "")[:120],
    }
    if cliente_id is not None:
        metadata["cliente_id"] = int(cliente_id)
    if metadata_extra:
        metadata.update(metadata_extra)
    try:
        log_action(
            action_type=action_type,
            entity_type="cliente_public_link",
            entity_id=str(cliente_id) if cliente_id is not None else None,
            summary=(action_type or "PUBLIC_LINK")[:255],
            metadata=metadata,
            success=bool(success),
            error=None if success else (str(reason or "public_link_fail")[:255]),
        )
    except Exception:
        return


def _resolve_public_link_token(token: str):
    metadata: dict = {"legacy_token": False}
    try:
        payload = _public_link_serializer().loads(token, max_age=_public_link_max_age_seconds())
    except SignatureExpired:
        return None, "expired", metadata
    except BadSignature:
        return None, "invalid_signature", metadata
    except Exception:
        return None, "invalid_payload", metadata

    if not isinstance(payload, dict):
        return None, "invalid_payload", metadata

    try:
        cliente_id = int(payload.get("cliente_id") or 0)
    except Exception:
        cliente_id = 0
    if cliente_id <= 0:
        return None, "invalid_cliente_id", metadata

    codigo_token = (payload.get("codigo") or "").strip()
    if not codigo_token:
        return None, "missing_codigo", metadata

    purpose = (payload.get("purpose") or "").strip().lower()
    if purpose and purpose != "solicitud_publica":
        return None, "invalid_purpose", metadata

    cliente = Cliente.query.filter_by(id=cliente_id).first()
    if not cliente:
        return None, "cliente_not_found", metadata
    if not bool(getattr(cliente, "is_active", True)):
        return None, "cliente_inactive", metadata
    if not hmac.compare_digest((cliente.codigo or "").strip(), codigo_token):
        return None, "codigo_mismatch", metadata

    token_fp = str(payload.get("fp") or "").strip()
    if token_fp:
        expected_fp = _cliente_public_link_fingerprint(cliente)
        if not hmac.compare_digest(token_fp, expected_fp):
            return None, "fingerprint_mismatch", metadata
    else:
        metadata["legacy_token"] = True

    return cliente, "", metadata


def _latest_solicitud_publica_cliente(cliente_id: int):
    return (
        Solicitud.query
        .filter_by(cliente_id=int(cliente_id))
        .order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        .first()
    )


def generar_token_publico_cliente(cliente: Cliente) -> str:
    ser = _public_link_serializer()
    payload = {
        "v": 2,
        "purpose": "solicitud_publica",
        "cliente_id": int(cliente.id),
        "codigo": str(cliente.codigo).strip(),
        "fp": _cliente_public_link_fingerprint(cliente),
        "nonce": secrets.token_urlsafe(18),
    }
    return ser.dumps(payload)


def generar_link_publico_compartible_cliente(cliente: Cliente, *, created_by: str = "") -> str:
    token = generar_token_publico_cliente(cliente)
    alias = create_public_share_alias(token=token, link_type="existente", created_by=created_by)
    return _public_share_external_url(alias.code)


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
            log_auth_event(
                event="CLIENTE_LOGIN_BLOCKED",
                status="fail",
                user_identifier=ident_norm or None,
                reason="cliente_login_lock_active",
                metadata={"path": "/clientes/login"},
            )
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
            log_auth_event(
                event="CLIENTE_LOGIN_FAIL",
                status="fail",
                user_identifier=ident_norm or None,
                reason="user_not_found",
                metadata={"path": "/clientes/login"},
            )
            flash('Credenciales incorrectas.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        if getattr(user, "password_hash", None) == "DISABLED_RESET_REQUIRED":
            _cliente_register_fail(ident_norm)
            log_auth_event(
                event="CLIENTE_LOGIN_FAIL",
                status="fail",
                user_identifier=ident_norm or None,
                reason="password_disabled",
                metadata={"path": "/clientes/login"},
            )
            flash('Credenciales incorrectas.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        if not hasattr(user, 'password_hash'):
            _cliente_register_fail(ident_norm)
            log_auth_event(
                event="CLIENTE_LOGIN_FAIL",
                status="fail",
                user_identifier=ident_norm or None,
                reason="password_hash_missing",
                metadata={"path": "/clientes/login"},
            )
            flash('Credenciales incorrectas.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        ok = False
        try:
            ok = check_password_hash(user.password_hash, password)
        except Exception:
            ok = False

        if not ok:
            _cliente_register_fail(ident_norm)
            log_auth_event(
                event="CLIENTE_LOGIN_FAIL",
                status="fail",
                user_identifier=ident_norm or None,
                reason="invalid_password",
                metadata={"path": "/clientes/login"},
            )
            flash('Credenciales incorrectas.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        if not getattr(user, "is_active", True):
            _cliente_register_fail(ident_norm)
            log_auth_event(
                event="CLIENTE_LOGIN_FAIL",
                status="fail",
                user_identifier=ident_norm or None,
                reason="inactive_user",
                metadata={"path": "/clientes/login"},
            )
            flash('Credenciales incorrectas.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        # ✅ Login correcto
        _cliente_reset_fail(ident_norm)

        try:
            session.clear()
        except Exception:
            pass

        login_user(user, remember=False)
        log_auth_event(
            event="CLIENTE_LOGIN_SUCCESS",
            status="success",
            user_id=getattr(user, "id", None),
            user_identifier=(getattr(user, "username", None) or getattr(user, "email", None) or ident_norm or None),
            metadata={"path": "/clientes/login"},
        )

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


@clientes_bp.app_context_processor
def _inject_client_chat_unread_count():
    unread = 0
    try:
        if (
            ChatConversation is not None
            and getattr(current_user, "is_authenticated", False)
            and isinstance(current_user, Cliente)
        ):
            unread = (
                ChatConversation.query
                .filter_by(cliente_id=current_user.id, status="open")
                .with_entities(db.func.coalesce(db.func.sum(ChatConversation.cliente_unread_count), 0))
                .scalar()
            )
    except Exception:
        unread = 0
    return {"chat_unread_count": int(unread or 0)}


@clientes_bp.app_context_processor
def _inject_client_live_after_id():
    after_id = 0
    try:
        if getattr(current_user, "is_authenticated", False) and isinstance(current_user, Cliente):
            after_id = _cliente_live_boot_after_id()
    except Exception:
        after_id = 0
    return {"client_live_after_id": int(after_id or 0)}


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
# Reset de contraseña cliente (deshabilitado por seguridad)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/reset-password', methods=['GET', 'POST'], endpoint='reset_password')
def reset_password():
    return (
        render_template('clientes/reset_password_disabled.html'),
        410,
        {"Cache-Control": "no-store"},
    )


def _build_cliente_guia_inteligente(cliente_id: int, recientes=None) -> dict:
    recientes = recientes if isinstance(recientes, list) else []
    total_solicitudes = 0
    ultima = None

    try:
        total_solicitudes = int(
            Solicitud.query.filter_by(cliente_id=cliente_id).count()
        )
    except Exception:
        total_solicitudes = 0

    if recientes:
        ultima = recientes[0]
    elif total_solicitudes > 0:
        q = Solicitud.query.filter_by(cliente_id=cliente_id)
        if hasattr(Solicitud, "fecha_solicitud"):
            q = q.order_by(Solicitud.fecha_solicitud.desc())
        else:
            q = q.order_by(Solicitud.id.desc())
        ultima = q.first()

    if total_solicitudes <= 0:
        return {
            "stage_key": "sin_solicitudes",
            "variant": "info",
            "icon": "bi-rocket-takeoff",
            "title": "Para comenzar, crea tu primera solicitud.",
            "message": "Este panel te irá guiando paso a paso según el avance de tu proceso.",
            "cta_label": "Crear solicitud",
            "cta_url": url_for("clientes.nueva_solicitud"),
            "secondary_label": "Cómo funciona el proceso",
            "secondary_url": url_for("clientes.proceso_contratacion"),
        }

    estado = (getattr(ultima, "estado", None) or "proceso").strip().lower()
    solicitud_id = getattr(ultima, "id", 0) or 0
    detalle_url = url_for("clientes.detalle_solicitud", id=solicitud_id) if solicitud_id else url_for("clientes.listar_solicitudes")

    if estado == "espera_pago":
        return {
            "stage_key": "espera_pago",
            "variant": "warning",
            "icon": "bi-credit-card",
            "title": "Tu proceso está en etapa de pago.",
            "message": "Siguiente paso sugerido: completa el pago inicial para continuar.",
            "cta_label": "Ver mi solicitud",
            "cta_url": detalle_url,
            "secondary_label": "Ver proceso completo",
            "secondary_url": url_for("clientes.proceso_contratacion"),
        }

    if estado in {"proceso", "activa"}:
        return {
            "stage_key": "en_proceso",
            "variant": "primary",
            "icon": "bi-hourglass-split",
            "title": "Tu solicitud está en proceso.",
            "message": "Pronto recibirás candidatas según el perfil solicitado.",
            "cta_label": "Ver seguimiento",
            "cta_url": detalle_url,
            "secondary_label": "Proceso de contratación",
            "secondary_url": url_for("clientes.proceso_contratacion"),
        }

    if estado in {"pagada", "reemplazo"}:
        return {
            "stage_key": estado,
            "variant": "success",
            "icon": "bi-check2-circle",
            "title": "Tu solicitud avanzó correctamente.",
            "message": "Puedes consultar el estado y próximos movimientos desde el detalle de tu solicitud.",
            "cta_label": "Ir al detalle",
            "cta_url": detalle_url,
            "secondary_label": "Ver proceso completo",
            "secondary_url": url_for("clientes.proceso_contratacion"),
        }

    return {
        "stage_key": "general",
        "variant": "info",
        "icon": "bi-compass",
        "title": "Aquí verás tus próximos pasos sugeridos.",
        "message": "Mantén tu solicitud actualizada para que el proceso fluya sin fricción.",
        "cta_label": "Mis solicitudes",
        "cta_url": url_for("clientes.listar_solicitudes"),
        "secondary_label": "Cómo funciona",
        "secondary_url": url_for("clientes.proceso_contratacion"),
    }


def _build_dashboard_ayuda_contextual(stage_key: Optional[str]) -> dict:
    key = (stage_key or "general").strip().lower() or "general"
    base = {
        "titulo": "Ayuda rápida para este momento",
        "items": [
            {
                "q": "¿Qué pasa en esta etapa?",
                "a": "Tu solicitud avanza por etapas y te mostramos solo el siguiente paso recomendado.",
                "icon": "bi-signpost",
            },
            {
                "q": "¿Qué debo hacer ahora?",
                "a": "Revisa tus solicitudes activas y entra al detalle para confirmar acciones pendientes.",
                "icon": "bi-check2-square",
            },
            {
                "q": "¿Cuánto tarda normalmente?",
                "a": "Depende del perfil y disponibilidad. Te notificamos cambios relevantes en el panel.",
                "icon": "bi-clock-history",
            },
            {
                "q": "¿Qué hago si necesito ayuda?",
                "a": "Usa el chat de soporte para asistencia sobre tu solicitud.",
                "icon": "bi-chat-left-text",
            },
        ],
    }
    if key == "sin_solicitudes":
        base["items"][0]["a"] = "Aun no tienes solicitudes. Al crear la primera, veras guia y seguimiento por etapa."
        base["items"][1]["a"] = "Completa una nueva solicitud con datos claros del hogar y modalidad."
    elif key == "espera_pago":
        base["items"][0]["a"] = "Tu solicitud quedo en espera de pago para avanzar a la siguiente fase."
        base["items"][1]["a"] = "Entra al detalle, valida el estado y contacta soporte si necesitas confirmar instrucciones."
        base["items"][2]["a"] = "Una vez confirmado el pago, el cambio de etapa se refleja en tu seguimiento."
    elif key == "en_proceso":
        base["items"][0]["a"] = "El equipo esta validando tu perfil para enviarte candidatas compatibles."
        base["items"][1]["a"] = "Mantente pendiente del detalle y del chat por si se requiere alguna confirmacion."
    elif key == "reemplazo":
        base["items"][0]["a"] = "Estamos gestionando opciones de reemplazo segun tu caso."
        base["items"][1]["a"] = "Revisa candidatas enviadas y usa el seguimiento para ver avances."
    return base


def _build_dashboard_resumen_ejecutivo(por_estado_dict: dict, guia_inteligente: Optional[dict]) -> dict:
    estados = {str(k or "").strip().lower(): int(v or 0) for k, v in (por_estado_dict or {}).items()}

    activas = int(estados.get("proceso", 0) + estados.get("activa", 0) + estados.get("reemplazo", 0))
    pendientes_atencion = int(estados.get("proceso", 0) + estados.get("reemplazo", 0) + estados.get("espera_pago", 0))
    en_espera_pago = int(estados.get("espera_pago", 0))
    cerradas = int(estados.get("pagada", 0) + estados.get("cancelada", 0))

    etapa_general = "Sin solicitudes"
    if en_espera_pago > 0:
        etapa_general = "En espera de pago"
    elif int(estados.get("reemplazo", 0)) > 0:
        etapa_general = "En reemplazo"
    elif int(estados.get("proceso", 0)) > 0:
        etapa_general = "En proceso"
    elif int(estados.get("activa", 0)) > 0:
        etapa_general = "Activa"
    elif int(estados.get("pagada", 0)) > 0:
        etapa_general = "Pagada"
    elif int(estados.get("cancelada", 0)) > 0:
        etapa_general = "Cancelada"

    prioridad = {
        "title": "Lo más importante ahora",
        "subtitle": etapa_general,
        "message": "Revisa tu panel para continuar con el siguiente paso recomendado.",
        "cta_label": "Revisar solicitudes",
        "cta_url": url_for("clientes.listar_solicitudes"),
        "secondary_label": "Proceso de contratación",
        "secondary_url": url_for("clientes.proceso_contratacion"),
        "variant": "info",
        "icon": "bi-compass",
    }
    if guia_inteligente:
        prioridad["subtitle"] = _estado_cliente_label((guia_inteligente or {}).get("stage_key"))
        prioridad["message"] = (guia_inteligente or {}).get("message") or prioridad["message"]
        prioridad["cta_label"] = (guia_inteligente or {}).get("cta_label") or prioridad["cta_label"]
        prioridad["cta_url"] = (guia_inteligente or {}).get("cta_url") or prioridad["cta_url"]
        prioridad["secondary_label"] = (guia_inteligente or {}).get("secondary_label") or prioridad["secondary_label"]
        prioridad["secondary_url"] = (guia_inteligente or {}).get("secondary_url") or prioridad["secondary_url"]
        prioridad["variant"] = (guia_inteligente or {}).get("variant") or prioridad["variant"]
        prioridad["icon"] = (guia_inteligente or {}).get("icon") or prioridad["icon"]

    return {
        "cards": {
            "activas": activas,
            "pendientes_atencion": pendientes_atencion,
            "espera_pago": en_espera_pago,
            "cerradas": cerradas,
        },
        "etapa_general": etapa_general,
        "prioridad": prioridad,
    }


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

    # OJO: fecha_solicitud puede no existir en algunos modelos viejos
    q_rec = Solicitud.query.filter_by(cliente_id=current_user.id)
    if hasattr(Solicitud, 'fecha_solicitud'):
        q_rec = q_rec.order_by(Solicitud.fecha_solicitud.desc())
    else:
        q_rec = q_rec.order_by(Solicitud.id.desc())

    recientes = q_rec.limit(5).all()
    guia_inteligente = _build_cliente_guia_inteligente(current_user.id, recientes=recientes)
    ayuda_contextual_dashboard = _build_dashboard_ayuda_contextual(
        (guia_inteligente or {}).get("stage_key")
    )
    resumen_ejecutivo = _build_dashboard_resumen_ejecutivo(
        por_estado_dict=por_estado_dict,
        guia_inteligente=guia_inteligente,
    )
    solicitud_draft = _cliente_solicitud_draft_meta(int(getattr(current_user, "id", 0) or 0))

    return render_template(
        'clientes/dashboard.html',
        total_solicitudes=total,
        por_estado=por_estado_dict,
        recientes=recientes,
        hoy=rd_today(),
        guia_inteligente=guia_inteligente,
        ayuda_contextual_dashboard=ayuda_contextual_dashboard,
        resumen_ejecutivo=resumen_ejecutivo,
        solicitud_draft=solicitud_draft,
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
    ayuda_secciones = [
        {
            "id": "proceso",
            "titulo": "Proceso",
            "icon": "bi-diagram-3",
            "items": [
                "El proceso inicia con tu solicitud y continua por etapas segun el estado.",
                "Puedes ver el avance desde el detalle y el timeline de cada solicitud.",
                f"Consulta la guia completa en {url_for('clientes.proceso_contratacion')}.",
            ],
        },
        {
            "id": "pagos",
            "titulo": "Pagos",
            "icon": "bi-credit-card",
            "items": [
                "Si tu solicitud esta en espera de pago, el siguiente paso se confirma desde seguimiento.",
                "Para dudas sobre instrucciones o validacion de pago, usa el chat de soporte.",
            ],
        },
        {
            "id": "entrevistas",
            "titulo": "Entrevistas",
            "icon": "bi-calendar-check",
            "items": [
                "Cuando recibes candidatas, puedes revisar perfiles y coordinar la siguiente accion.",
                "El estado de cada perfil enviado se refleja dentro de la solicitud.",
            ],
        },
        {
            "id": "candidatas",
            "titulo": "Candidatas",
            "icon": "bi-people",
            "items": [
                "En cada solicitud veras candidatas enviadas con detalles clave para decidir.",
                "Si no hay candidatas aun, revisa seguimiento para confirmar la etapa actual.",
            ],
        },
        {
            "id": "reemplazos",
            "titulo": "Reemplazos",
            "icon": "bi-arrow-repeat",
            "items": [
                "Cuando una solicitud entra a reemplazo, puedes seguir el caso desde el detalle.",
                "Las opciones de reemplazo se muestran en candidatas enviadas si ya estan disponibles.",
            ],
        },
        {
            "id": "contacto",
            "titulo": "Contacto y chat",
            "icon": "bi-chat-dots",
            "items": [
                "Para ayuda puntual de tu caso, usa el chat interno de cliente.",
                "Tambien puedes escribir por WhatsApp para soporte rapido.",
            ],
        },
    ]
    return render_template(
        'clientes/ayuda.html',
        whatsapp=whatsapp,
        ayuda_secciones=ayuda_secciones,
    )


@clientes_bp.route('/proceso')
@login_required
@cliente_required
def proceso_contratacion():
    return render_template('clientes/proceso.html')


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
_CLIENTE_LIVE_POLL_LIMIT_MAX = 80
_CLIENTE_LIVE_EVENT_TYPES = {
    "SOLICITUD_ESTADO_CAMBIADO",
    "SOLICITUD_PAGO_REGISTRADO",
    "SOLICITUD_CANDIDATA_ASIGNADA",
    "SOLICITUD_CANDIDATAS_LIBERADAS",
    "MATCHING_CANDIDATAS_ENVIADAS",
    "REEMPLAZO_ABIERTO",
    "REEMPLAZO_FINALIZADO",
    "REEMPLAZO_CANCELADO",
    "REEMPLAZO_CERRADO_ASIGNANDO",
    "CLIENTE_DASHBOARD_UPDATED",
    "CLIENTE_SOLICITUD_CREATED",
    "CLIENTE_SOLICITUD_UPDATED",
    "CLIENTE_SOLICITUD_STATUS_CHANGED",
    "CLIENTE_CANDIDATA_UPDATED",
    "CLIENTE_NOTIFICACION_CREATED",
    "CLIENTE_NOTIFICACION_UPDATED",
    "CLIENTE_NOTIFICACION_READ",
    "CLIENTE_NOTIFICACION_DELETED",
    "CHAT_MESSAGE_CREATED",
    "CHAT_CONVERSATION_READ",
    "CHAT_CONVERSATION_STATUS_CHANGED",
}
_CLIENTE_EVENT_CANONICAL_TYPE = {
    "SOLICITUD_ESTADO_CAMBIADO": "cliente.solicitud.status_changed",
    "SOLICITUD_PAGO_REGISTRADO": "cliente.solicitud.updated",
    "SOLICITUD_CANDIDATA_ASIGNADA": "cliente.candidata.updated",
    "SOLICITUD_CANDIDATAS_LIBERADAS": "cliente.candidata.updated",
    "MATCHING_CANDIDATAS_ENVIADAS": "cliente.candidata.updated",
    "REEMPLAZO_ABIERTO": "cliente.solicitud.updated",
    "REEMPLAZO_FINALIZADO": "cliente.solicitud.updated",
    "REEMPLAZO_CANCELADO": "cliente.solicitud.updated",
    "REEMPLAZO_CERRADO_ASIGNANDO": "cliente.solicitud.updated",
    "CLIENTE_DASHBOARD_UPDATED": "cliente.dashboard.updated",
    "CLIENTE_SOLICITUD_CREATED": "cliente.solicitud.created",
    "CLIENTE_SOLICITUD_UPDATED": "cliente.solicitud.updated",
    "CLIENTE_SOLICITUD_STATUS_CHANGED": "cliente.solicitud.status_changed",
    "CLIENTE_CANDIDATA_UPDATED": "cliente.candidata.updated",
    "CLIENTE_NOTIFICACION_CREATED": "cliente.notificacion.created",
    "CLIENTE_NOTIFICACION_UPDATED": "cliente.notificacion.updated",
    "CLIENTE_NOTIFICACION_READ": "cliente.notificacion.read",
    "CLIENTE_NOTIFICACION_DELETED": "cliente.notificacion.deleted",
    "CHAT_MESSAGE_CREATED": "cliente.chat.message_created",
    "CHAT_CONVERSATION_READ": "cliente.chat.read",
    "CHAT_CONVERSATION_STATUS_CHANGED": "cliente.chat.status_changed",
}
_CHAT_STATUS_OPEN = "open"
_CHAT_STATUS_PENDING = "pending"
_CHAT_STATUS_CLOSED = "closed"
_CHAT_STATUS_VALUES = {_CHAT_STATUS_OPEN, _CHAT_STATUS_PENDING, _CHAT_STATUS_CLOSED}


def _cliente_presence_key(cliente_id: int) -> str:
    return f"clientes_presence:{int(cliente_id)}"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _maybe_assign_sqlite_pk(model_obj, model_cls) -> None:
    try:
        bind = db.session.get_bind()
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "")).strip().lower()
        if dialect != "sqlite":
            return
        if getattr(model_obj, "id", None):
            return
        max_id = db.session.query(db.func.max(model_cls.id)).scalar() or 0
        model_obj.id = int(max_id) + 1
    except Exception:
        return


def _emit_cliente_outbox_event(
    *,
    event_type: str,
    payload: Optional[dict] = None,
    aggregate_type: str = "Cliente",
    aggregate_id: Optional[Union[int, str]] = None,
    aggregate_version: Optional[int] = None,
) -> None:
    if DomainOutbox is None:
        return
    ev = str(event_type or "").strip().upper()
    if not ev:
        return
    pid = dict(payload or {})
    if chat_e2e_enabled():
        pid.setdefault("e2e_tag", chat_e2e_tag())
        pid.setdefault("e2e_run_id", chat_e2e_run_id())
    agg_id = aggregate_id
    if agg_id is None:
        agg_id = pid.get("solicitud_id") or pid.get("cliente_id") or getattr(current_user, "id", None) or 0
    try:
        row = DomainOutbox(
            event_id=secrets.token_hex(16),
            event_type=ev[:80],
            aggregate_type=(aggregate_type or "Cliente")[:80],
            aggregate_id=str(agg_id)[:64],
            aggregate_version=aggregate_version,
            occurred_at=utc_now_naive(),
            actor_id=f"cliente:{int(getattr(current_user, 'id', 0) or 0)}",
            region="clientes",
            payload=pid,
            schema_version=1,
            correlation_id=(request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID") or "")[:64] or None,
            idempotency_key=(request.headers.get("Idempotency-Key") or "")[:128] or None,
        )
        _maybe_assign_sqlite_pk(row, DomainOutbox)
        db.session.add(row)
    except Exception:
        return


def _cliente_live_resolve_target(row) -> tuple[int, int]:
    payload = dict(getattr(row, "payload", None) or {})
    aggregate_type = str(getattr(row, "aggregate_type", "") or "").strip().lower()
    if aggregate_type == "chatconversation":
        solicitud_id = _safe_int(payload.get("solicitud_id"), default=0)
    else:
        solicitud_id = _safe_int(payload.get("solicitud_id") or getattr(row, "aggregate_id", None), default=0)
    cliente_id = _safe_int(payload.get("cliente_id"), default=0)
    if aggregate_type == "chatconversation":
        conversation_id = _safe_int(payload.get("conversation_id") or getattr(row, "aggregate_id", None), default=0)
    else:
        conversation_id = _safe_int(payload.get("conversation_id"), default=0)
    if (cliente_id <= 0 or solicitud_id <= 0) and conversation_id > 0 and ChatConversation is not None:
        try:
            conv_row = (
                ChatConversation.query
                .with_entities(ChatConversation.cliente_id, ChatConversation.solicitud_id)
                .filter(ChatConversation.id == int(conversation_id))
                .first()
            )
            if conv_row:
                if cliente_id <= 0:
                    cliente_id = _safe_int(getattr(conv_row, "cliente_id", 0), default=0)
                if solicitud_id <= 0:
                    solicitud_id = _safe_int(getattr(conv_row, "solicitud_id", 0), default=0)
        except Exception:
            pass
    if cliente_id > 0:
        return cliente_id, solicitud_id
    if solicitud_id <= 0:
        return 0, 0
    try:
        cliente_id = _safe_int(
            db.session.query(Solicitud.cliente_id).filter(Solicitud.id == int(solicitud_id)).scalar(),
            default=0,
        )
    except Exception:
        cliente_id = 0
    return cliente_id, solicitud_id


def _cliente_live_target_matches_solicitud(cliente_id: int, solicitud_id: int) -> bool:
    cid = _safe_int(cliente_id, default=0)
    sid = _safe_int(solicitud_id, default=0)
    if cid <= 0 or sid <= 0:
        return True
    try:
        owner_cliente_id = _safe_int(
            db.session.query(Solicitud.cliente_id).filter(Solicitud.id == int(sid)).scalar(),
            default=0,
        )
    except Exception:
        return False
    return owner_cliente_id > 0 and owner_cliente_id == cid


def _cliente_live_views_for_type(event_type: str, solicitud_id: int) -> list[str]:
    views = {"dashboard", "solicitudes_list"}
    ev = str(event_type or "").strip().lower()
    if solicitud_id > 0:
        views.add("solicitud_detail")
    if ev.startswith("cliente.chat."):
        views.add("chat")
    if ev.startswith("cliente.notificacion."):
        views.add("notifications")
    if ev.startswith("cliente.candidata.") or ev.startswith("cliente.solicitud."):
        views.add("notifications")
    return sorted(list(views))


def _normalize_cliente_live_event_from_outbox(row, *, current_cliente_id: int):
    raw_type = str(getattr(row, "event_type", "") or "").strip().upper()
    if not raw_type or raw_type not in _CLIENTE_LIVE_EVENT_TYPES:
        return None
    payload = dict(getattr(row, "payload", None) or {})
    payload_cliente_id = _safe_int(payload.get("cliente_id"), default=0)
    payload_solicitud_id = _safe_int(payload.get("solicitud_id"), default=0)
    if payload_cliente_id > 0 and payload_solicitud_id > 0:
        if not _cliente_live_target_matches_solicitud(payload_cliente_id, payload_solicitud_id):
            return None

    cliente_id, solicitud_id = _cliente_live_resolve_target(row)
    if int(cliente_id or 0) <= 0 or int(current_cliente_id or 0) <= 0:
        return None
    if int(cliente_id) != int(current_cliente_id):
        return None
    if int(solicitud_id or 0) > 0 and not _cliente_live_target_matches_solicitud(int(cliente_id), int(solicitud_id)):
        return None
    canonical_type = _CLIENTE_EVENT_CANONICAL_TYPE.get(raw_type) or "cliente.actualizado"
    views = _cliente_live_views_for_type(canonical_type, solicitud_id)
    return {
        "event_id": str(getattr(row, "event_id", "") or ""),
        "event_type": canonical_type,
        "outbox_id": int(getattr(row, "id", 0) or 0),
        "occurred_at": iso_utc_z(getattr(row, "occurred_at", None)),
        "recorded_at": iso_utc_z(getattr(row, "created_at", None)),
        "target": {
            "cliente_id": int(cliente_id),
            "solicitud_id": int(solicitud_id or 0) or None,
        },
        "invalidate": {"views": views},
        "payload": {
            "estado": payload.get("to") or payload.get("estado"),
            "status": payload.get("status") or payload.get("to") or payload.get("estado"),
            "from": payload.get("from"),
            "count": payload.get("count"),
            "candidata_id": payload.get("candidata_id"),
            "candidata_ids": payload.get("candidata_ids"),
            "notificacion_id": payload.get("notificacion_id"),
            "tipo": payload.get("tipo"),
            "conversation_id": payload.get("conversation_id"),
            "message_id": payload.get("message_id"),
            "sender_type": payload.get("sender_type"),
            "cliente_unread_count": payload.get("cliente_unread_count"),
            "staff_unread_count": payload.get("staff_unread_count"),
        },
    }


def _should_log_cliente_live_event(cliente_id: int, event_type: str, path: str) -> bool:
    event = (event_type or "").strip().lower() or "heartbeat"
    timeout = 30 if event == "heartbeat" else 6
    key = f"cliente_live_event:{int(cliente_id)}:{event}:{(path or '')[:140]}"
    if bp_get(key, default=0, context="cliente_live_log_dedupe_get"):
        return False
    if not bp_set(key, 1, timeout=timeout, context="cliente_live_log_dedupe_set"):
        return event != "heartbeat"
    return True


@clientes_bp.route('/ping', methods=['GET'])
@login_required
@cliente_required
def clientes_ping():
    """Endpoint liviano para saber si la sesión sigue activa."""
    return _json_no_cache({
        'ok': True,
        'server_time': iso_utc_z(),
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
        'last_seen_at': iso_utc_z(),
    }
    bp_set(
        _cliente_presence_key(cliente_id),
        presence_payload,
        timeout=_CLIENTE_PRESENCE_TTL_SECONDS,
        context="cliente_presence_payload_set",
    )
    idx = bp_get(_CLIENTE_PRESENCE_INDEX_KEY, default=[], context="cliente_presence_idx_get") or []
    try:
        idx = [int(x) for x in idx]
    except Exception:
        idx = []
    if cliente_id not in idx:
        idx.append(cliente_id)
    idx = idx[-2000:]
    bp_set(
        _CLIENTE_PRESENCE_INDEX_KEY,
        idx,
        timeout=max(3600, _CLIENTE_PRESENCE_TTL_SECONDS * 20),
        context="cliente_presence_idx_set",
    )

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
    return _json_no_cache(
        {
            "ok": False,
            "error": "deprecated_endpoint",
            "message": "Este endpoint fue retirado. Usa /clientes/live/invalidation/poll para realtime y /clientes/solicitudes para listado.",
            "replaced_by": {
                "poll_url": url_for("clientes.clientes_live_invalidation_poll"),
                "list_url": url_for("clientes.listar_solicitudes"),
            },
        },
        status=410,
    )


def _cliente_live_outbox_rows(after_id: int, limit: int):
    if DomainOutbox is None:
        return []
    lim = max(1, min(int(limit or 25), _CLIENTE_LIVE_POLL_LIMIT_MAX))
    cursor = max(0, int(after_id or 0))
    try:
        return (
            DomainOutbox.query
            .filter(DomainOutbox.id > cursor)
            .filter(DomainOutbox.event_type.in_(sorted(_CLIENTE_LIVE_EVENT_TYPES)))
            .order_by(DomainOutbox.id.asc())
            .limit(lim)
            .all()
        )
    except Exception:
        return []


def _cliente_live_boot_after_id() -> int:
    if DomainOutbox is None:
        return 0
    try:
        return int(
            db.session.query(db.func.max(DomainOutbox.id))
            .filter(DomainOutbox.event_type.in_(sorted(_CLIENTE_LIVE_EVENT_TYPES)))
            .scalar()
            or 0
        )
    except Exception:
        return 0


@clientes_bp.route('/live/invalidation/poll', methods=['GET'])
@login_required
@cliente_required
def clientes_live_invalidation_poll():
    after_id = max(0, _safe_int(request.args.get('after_id'), default=0))
    limit = max(1, min(_safe_int(request.args.get('limit'), default=25), _CLIENTE_LIVE_POLL_LIMIT_MAX))
    cliente_id = _safe_int(getattr(current_user, "id", 0), default=0)
    rows = _cliente_live_outbox_rows(after_id=after_id, limit=limit)
    items = []
    cursor = int(after_id)
    for row in (rows or []):
        rid = _safe_int(getattr(row, "id", 0), default=0)
        if rid > cursor:
            cursor = rid
        normalized = _normalize_cliente_live_event_from_outbox(row, current_cliente_id=cliente_id)
        if normalized is not None:
            items.append(normalized)

    return _json_no_cache({
        "ok": True,
        "mode": "outbox_poll",
        "items": items,
        "count": len(items),
        "next_after_id": int(cursor),
        "ts": iso_utc_z(),
    })


@clientes_bp.route('/live/invalidation/stream', methods=['GET'])
@login_required
@cliente_required
def clientes_live_invalidation_stream():
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @stream_with_context
    def generate():
        cliente_id = _safe_int(getattr(current_user, "id", 0), default=0)
        if cliente_id <= 0:
            yield _sse("error", {"error": "unauthorized"})
            return
        if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
            yield _sse("heartbeat", {"ts": iso_utc_z(), "cliente_id": cliente_id})
            return

        cursor = max(0, _safe_int(request.args.get("after_id"), default=0))
        heartbeat_every_sec = 15.0
        last_heartbeat_at = 0.0

        while True:
            emitted = False
            rows = _cliente_live_outbox_rows(after_id=cursor, limit=35)
            for row in (rows or []):
                rid = _safe_int(getattr(row, "id", 0), default=0)
                if rid > cursor:
                    cursor = rid
                normalized = _normalize_cliente_live_event_from_outbox(row, current_cliente_id=cliente_id)
                if normalized is None:
                    continue
                yield _sse("invalidation", normalized)
                emitted = True
            now_ts = time.time()
            if (not emitted) and (now_ts - last_heartbeat_at >= heartbeat_every_sec):
                yield _sse("heartbeat", {"ts": iso_utc_z(), "after_id": int(cursor)})
                last_heartbeat_at = now_ts
            time.sleep(1.2 if not emitted else 0.1)

    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(generate(), headers=headers)


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
    solicitud_draft = _cliente_solicitud_draft_meta(int(getattr(current_user, "id", 0) or 0))

    return render_template(
        'clientes/solicitudes_list.html',
        solicitudes=paginado.items,
        hoy=rd_today(),
        page=page, per_page=per_page, total=paginado.total, pages=paginado.pages,
        has_prev=paginado.has_prev, has_next=paginado.has_next,
        prev_num=getattr(paginado, 'prev_num', None),
        next_num=getattr(paginado, 'next_num', None),
        q=q, estado=estado, ciudad=ciudad, modalidad=modalidad,
        fecha_desde=fecha_desde_raw, fecha_hasta=fecha_hasta_raw,
        estados_disponibles=estados_disponibles,
        ciudades_disponibles=ciudades_disponibles,
        modalidades_disponibles=modalidades_disponibles,
        solicitud_draft=solicitud_draft,
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


def _normalize_modalidad_on_solicitud(solicitud_obj) -> None:
    try:
        if hasattr(solicitud_obj, "modalidad_trabajo"):
            txt = canonicalize_modalidad_trabajo(getattr(solicitud_obj, "modalidad_trabajo", ""))
            solicitud_obj.modalidad_trabajo = txt or None
    except Exception:
        return


def _resolve_modalidad_ui_context_from_request(form_obj, *, prefer_post: bool = False) -> tuple[str, str, str]:
    """Compone estado guiado de modalidad para re-render estable en GET/POST."""
    fallback_group = ""
    fallback_specific = ""
    fallback_other = ""
    try:
        modalidad_raw = form_obj.modalidad_trabajo.data if hasattr(form_obj, "modalidad_trabajo") else ""
        modalidad_ui = split_modalidad_for_ui(modalidad_raw)
        fallback_group = (modalidad_ui.get("group") or "").strip()
        fallback_specific = (modalidad_ui.get("specific") or "").strip()
        fallback_other = (modalidad_ui.get("other") or "").strip()
    except Exception:
        pass

    if not prefer_post:
        return fallback_group, fallback_specific, fallback_other

    post_group = (request.form.get("modalidad_grupo") or "").strip()
    post_specific = (request.form.get("modalidad_especifica") or "").strip()
    post_other = (request.form.get("modalidad_otro_text") or "").strip()
    return (
        post_group or fallback_group,
        post_specific or fallback_specific,
        post_other or fallback_other,
    )


def _allowed_codes_from_choices(choices):
    try:
        return {str(v).strip() for v, _ in (choices or []) if str(v).strip()}
    except Exception:
        return set()


def _normalize_areas_comunes_selected(selected_vals, choices):
    """
    Normaliza áreas comunes y usa 'todas_anteriores' solo como control.
    Nunca persiste 'todas_anteriores' como valor real.
    """
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
    return bool(bp_add(key, value, timeout=timeout, context="cliente_cache_add"))


def _cache_set(cache_obj, key: str, value, timeout: int) -> bool:
    return bool(bp_set(key, value, timeout=timeout, context="cliente_cache_set"))


def _cache_del(cache_obj, key: str) -> bool:
    return bool(bp_delete(key, context="cliente_cache_del"))


_CLIENTE_SOLICITUD_DRAFT_TTL_SECONDS = int((os.getenv("CLIENTE_SOLICITUD_DRAFT_TTL_SECONDS") or str(14 * 24 * 3600)).strip() or (14 * 24 * 3600))
_SOLICITUD_DRAFT_EXTRA_FIELDS = {
    "pasaje_mode",
    "pasaje_otro_text",
    "modalidad_grupo",
    "modalidad_especifica",
    "modalidad_otro_text",
    "pisos_selector",
    "wizard_step",
}
_SOLICITUD_DRAFT_DROP_FIELDS = {
    "csrf_token", "submit", "token", "codigo_solicitud", "id", "created_at", "updated_at",
    "save_draft", "discard_draft",
}


def _cliente_solicitud_draft_key(cliente_id: int) -> str:
    return f"cliente:solicitud:draft:{int(cliente_id or 0)}"


def _safe_trim_draft_value(value, *, max_len: int = 1200):
    if value is None:
        return ""
    txt = str(value).strip()
    if len(txt) > max_len:
        txt = txt[:max_len]
    return txt


def _extract_solicitud_draft_payload_from_request(form_obj) -> dict:
    payload = {}
    allowed = set(getattr(form_obj, "_fields", {}).keys()) | set(_SOLICITUD_DRAFT_EXTRA_FIELDS)
    for name in sorted(allowed):
        if name in _SOLICITUD_DRAFT_DROP_FIELDS:
            continue
        vals = request.form.getlist(name)
        if not vals:
            continue

        field = getattr(form_obj, "_fields", {}).get(name)
        field_type = str(getattr(field, "type", "") or "")
        if field_type == "SelectMultipleField":
            clean_vals = [_safe_trim_draft_value(v, max_len=180) for v in vals if _safe_trim_draft_value(v, max_len=180)]
            if clean_vals:
                payload[name] = clean_vals[:40]
            continue

        val = _safe_trim_draft_value(vals[-1], max_len=1600)
        if val:
            payload[name] = val
    return payload


def _draft_payload_has_content(payload: dict) -> bool:
    for _, v in (payload or {}).items():
        if isinstance(v, (list, tuple, set)):
            if any(str(x or "").strip() for x in v):
                return True
            continue
        if str(v or "").strip():
            return True
    return False


def _save_cliente_solicitud_draft(*, cliente_id: int, payload: dict) -> bool:
    cid = int(cliente_id or 0)
    if cid <= 0:
        return False
    if not _draft_payload_has_content(payload):
        return _clear_cliente_solicitud_draft(cliente_id=cid)
    envelope = {
        "saved_at": iso_utc_z(),
        "payload": payload,
    }
    key = _cliente_solicitud_draft_key(cid)
    raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    if _cache_ok():
        return bool(bp_set(key, raw, timeout=max(1800, int(_CLIENTE_SOLICITUD_DRAFT_TTL_SECONDS)), context="cliente_solicitud_draft_set"))
    try:
        bucket = dict(session.get("_cliente_solicitud_drafts") or {})
        bucket[str(cid)] = envelope
        session["_cliente_solicitud_drafts"] = bucket
        session.modified = True
        return True
    except Exception:
        return False


def _get_cliente_solicitud_draft(*, cliente_id: int) -> Optional[dict]:
    cid = int(cliente_id or 0)
    if cid <= 0:
        return None

    envelope = None
    if _cache_ok():
        key = _cliente_solicitud_draft_key(cid)
        raw = bp_get(key, default=None, context="cliente_solicitud_draft_get")
        if raw:
            try:
                envelope = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                envelope = None
    if envelope is None:
        try:
            bucket = dict(session.get("_cliente_solicitud_drafts") or {})
            envelope = bucket.get(str(cid))
        except Exception:
            envelope = None
    if not isinstance(envelope, dict):
        return None

    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict) or not _draft_payload_has_content(payload):
        return None
    return {
        "saved_at": envelope.get("saved_at"),
        "payload": payload,
    }


def _clear_cliente_solicitud_draft(*, cliente_id: int) -> bool:
    cid = int(cliente_id or 0)
    if cid <= 0:
        return False
    ok = False
    key = _cliente_solicitud_draft_key(cid)
    if _cache_ok():
        ok = bool(bp_delete(key, context="cliente_solicitud_draft_del"))
    try:
        bucket = dict(session.get("_cliente_solicitud_drafts") or {})
        if str(cid) in bucket:
            bucket.pop(str(cid), None)
            session["_cliente_solicitud_drafts"] = bucket
            session.modified = True
            ok = True
    except Exception:
        pass
    return ok


def _apply_solicitud_draft_to_form(form_obj, payload: dict):
    for name, field in (getattr(form_obj, "_fields", {}) or {}).items():
        if name in _SOLICITUD_DRAFT_DROP_FIELDS or name not in payload:
            continue
        value = payload.get(name)
        field_type = str(getattr(field, "type", "") or "")
        try:
            if field_type == "BooleanField":
                field.data = str(value or "").strip().lower() in {"1", "true", "on", "y", "yes", "si"}
            elif field_type == "SelectMultipleField":
                if isinstance(value, (list, tuple, set)):
                    field.data = [str(v) for v in value if str(v).strip()]
                else:
                    txt = str(value or "").strip()
                    field.data = [txt] if txt else []
            elif field_type == "IntegerField":
                txt = str(value or "").strip()
                field.data = int(txt) if txt else None
            elif field_type == "FloatField":
                txt = str(value or "").strip()
                field.data = float(txt) if txt else None
            else:
                if isinstance(value, (list, tuple, set)):
                    field.data = str(list(value)[-1]) if value else ""
                else:
                    field.data = value
        except Exception:
            continue


def _cliente_solicitud_draft_meta(cliente_id: int) -> Optional[dict]:
    draft = _get_cliente_solicitud_draft(cliente_id=cliente_id)
    if not draft:
        return None
    saved_at_raw = draft.get("saved_at")
    saved_dt = None
    if saved_at_raw:
        try:
            saved_dt = to_rd(saved_at_raw)
        except Exception:
            saved_dt = None
    return {
        "saved_at": saved_dt,
        "continue_url": url_for("clientes.nueva_solicitud", continuar=1),
    }


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
        now = int(utc_timestamp())
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
    public_modalidad_group = ""
    public_modalidad_specific = ""
    public_modalidad_other = ""
    draft_meta = None
    draft_restored = False
    draft_payload = {}
    initial_wizard_step = 1
    cliente_id = int(getattr(current_user, "id", 0) or 0)

    if request.method == "GET" and str(request.args.get("fresh") or "").strip() in {"1", "true", "yes"}:
        _clear_cliente_solicitud_draft(cliente_id=cliente_id)
        flash("Borrador descartado. Puedes iniciar una solicitud nueva.", "info")
        return redirect(url_for("clientes.nueva_solicitud"))

    if request.method == 'GET':
        existing_draft = _get_cliente_solicitud_draft(cliente_id=cliente_id)
        if existing_draft:
            draft_payload = existing_draft.get("payload") or {}
            _apply_solicitud_draft_to_form(form, draft_payload)
            draft_restored = True
            draft_meta = _cliente_solicitud_draft_meta(cliente_id)

        form.funciones.data       = form.funciones.data or []
        form.areas_comunes.data   = form.areas_comunes.data or []
        form.edad_requerida.data  = form.edad_requerida.data or []
        if form.dos_pisos.data is None:
            form.dos_pisos.data = False
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = False

    public_pasaje_mode = "aparte" if bool(getattr(form, "pasaje_aporte", type("x", (object,), {"data": False})).data) else "incluido"
    public_pasaje_otro = ""
    if request.method == "GET" and draft_payload:
        draft_pasaje_mode = str((draft_payload or {}).get("pasaje_mode") or "").strip().lower()
        if draft_pasaje_mode in {"incluido", "aparte", "otro"}:
            public_pasaje_mode = draft_pasaje_mode
        public_pasaje_otro = str((draft_payload or {}).get("pasaje_otro_text") or "").strip()[:120]
        public_modalidad_group = str((draft_payload or {}).get("modalidad_grupo") or "").strip()[:40]
        public_modalidad_specific = str((draft_payload or {}).get("modalidad_especifica") or "").strip()[:120]
        public_modalidad_other = str((draft_payload or {}).get("modalidad_otro_text") or "").strip()[:120]
        try:
            initial_wizard_step = int(str((draft_payload or {}).get("wizard_step") or "1").strip() or 1)
        except Exception:
            initial_wizard_step = 1
        if initial_wizard_step < 1:
            initial_wizard_step = 1

    if request.method == "POST":
        try:
            initial_wizard_step = int(str(request.form.get("wizard_step") or initial_wizard_step).strip() or 1)
        except Exception:
            initial_wizard_step = 1
        if initial_wizard_step < 1:
            initial_wizard_step = 1

        if "discard_draft" in request.form:
            _clear_cliente_solicitud_draft(cliente_id=cliente_id)
            flash("Borrador descartado.", "info")
            return redirect(url_for("clientes.nueva_solicitud"))

        if "save_draft" in request.form:
            draft_payload = _extract_solicitud_draft_payload_from_request(form)
            if _save_cliente_solicitud_draft(cliente_id=cliente_id, payload=draft_payload):
                flash("Borrador guardado. Puedes salir y continuar luego.", "success")
            else:
                flash("No se pudo guardar el borrador en este momento.", "warning")
            return redirect(url_for("clientes.nueva_solicitud", continuar=1))

        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=True)
    else:
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=False)

    is_valid_submit = form.validate_on_submit()
    if request.method == "POST" and request.form and not is_valid_submit:
        try:
            draft_payload = _extract_solicitud_draft_payload_from_request(form)
            _save_cliente_solicitud_draft(cliente_id=cliente_id, payload=draft_payload)
        except Exception:
            pass

    if is_valid_submit:
        actor_user = str(int(getattr(current_user, 'id', 0) or 0))
        actor_ip = _client_ip_for_security_layer() or "0.0.0.0"

        blocked_daily, _ = enforce_business_limit(
            cache_obj=cache,
            scope="cliente_solicitud_create_day",
            actor=actor_user,
            limit=BUSINESS_MAX_CLIENTE_CREACIONES_DIA,
            window_seconds=86400,
            reason="daily_create_limit",
            summary="Bloqueo por creación diaria de solicitudes (cliente)",
            metadata={"route": (request.path or ""), "ip": actor_ip},
        )
        if blocked_daily:
            flash('Superaste el límite diario de nuevas solicitudes. Intenta mañana o contacta soporte.', 'warning')
            return redirect(url_for('clientes.listar_solicitudes'))

        blocked_fast, _ = enforce_min_human_interval(
            cache_obj=cache,
            scope="cliente_solicitud_create_interval",
            actor=actor_user,
            min_seconds=8,
            reason="timing_too_fast",
            summary="Bloqueo por patrón no humano en creación de solicitudes (cliente)",
            metadata={"route": (request.path or ""), "ip": actor_ip},
        )
        if blocked_fast:
            flash('Espera unos segundos antes de enviar otra solicitud.', 'warning')
            return redirect(url_for('clientes.listar_solicitudes'))

        active_count = _cliente_active_solicitudes_count(int(getattr(current_user, 'id', 0) or 0))
        if active_count >= BUSINESS_MAX_CLIENTE_ACTIVAS:
            log_action(
                action_type="BUSINESS_FLOW_BLOCKED",
                entity_type="cliente",
                entity_id=int(getattr(current_user, 'id', 0) or 0),
                summary="Límite de solicitudes activas alcanzado",
                metadata={
                    "rule": "max_active_solicitudes",
                    "active_count": int(active_count),
                    "max_allowed": int(BUSINESS_MAX_CLIENTE_ACTIVAS),
                    "route": (request.path or ""),
                },
                success=False,
                error="max_active_solicitudes_reached",
            )
            flash('Tienes demasiadas solicitudes activas en este momento. Gestiona las actuales antes de crear otra.', 'warning')
            return redirect(url_for('clientes.listar_solicitudes'))

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
                if bp_get(dedupe_key, default=False, context="cliente_solicitud_dedupe_get"):
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
                codigo = compose_codigo_solicitud(str(current_user.codigo or ""), idx)
                existe = Solicitud.query.filter_by(codigo_solicitud=codigo).first()
                if not existe:
                    break
                idx += 1

            s = Solicitud(
                cliente_id=current_user.id,
                fecha_solicitud=utc_now_naive(),
                codigo_solicitud=codigo
            )
            form.populate_obj(s)
            _normalize_modalidad_on_solicitud(s)

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
            if funciones_otro_txt and 'otro' not in selected_funciones:
                selected_funciones.append('otro')
            funciones_otro_clean = funciones_otro_txt if 'otro' in selected_funciones else ''
            _set_attr_if_exists(s, 'funciones_otro', funciones_otro_clean or None)

            s.funciones      = _map_funciones(selected_funciones, funciones_otro_clean)
            areas_selected_raw = _clean_list(form.areas_comunes.data)
            area_otro_txt = (form.area_otro.data or '').strip() if hasattr(form, 'area_otro') else ''
            areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
            s.areas_comunes  = _normalize_areas_comunes_selected(
                areas_selected_raw,
                form.areas_comunes.choices,
            )
            edad_codes_selected = _clean_list(form.edad_requerida.data)
            edad_otro_txt = (getattr(form, 'edad_otro', None).data or '').strip() if hasattr(form, 'edad_otro') else ''
            if edad_otro_txt and 'otro' not in edad_codes_selected:
                edad_codes_selected.append('otro')
            s.edad_requerida = _map_edad_choices(
                edad_codes_selected,
                form.edad_requerida.choices,
                edad_otro_txt,
            )
            tipo_lugar_value = getattr(s, 'tipo_lugar', '')
            tipo_lugar_otro_txt = (getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
            if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                tipo_lugar_value = 'otro'
            s.tipo_lugar = _map_tipo_lugar(
                tipo_lugar_value,
                tipo_lugar_otro_txt,
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if areas_has_otro else '') or None
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = strip_pasaje_marker_from_note((form.nota_cliente.data or '').strip())
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            apply_pasaje_to_solicitud(
                s,
                mode_raw=public_pasaje_mode,
                text_raw=public_pasaje_otro,
                default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
            )
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = utc_now_naive()

            db.session.add(s)
            try:
                current_user.total_solicitudes = (current_user.total_solicitudes or 0) + 1
                current_user.fecha_ultima_solicitud = utc_now_naive()
                current_user.fecha_ultima_actividad = utc_now_naive()
            except Exception:
                pass

            # Flush para detectar problemas (y evitar que un error tarde dispare reintentos duplicados)
            db.session.flush()
            _emit_cliente_outbox_event(
                event_type="CLIENTE_SOLICITUD_CREATED",
                aggregate_type="Solicitud",
                aggregate_id=int(getattr(s, "id", 0) or 0),
                aggregate_version=int(getattr(s, "row_version", 0) or 0) + 1,
                payload={
                    "cliente_id": int(getattr(current_user, "id", 0) or 0),
                    "solicitud_id": int(getattr(s, "id", 0) or 0),
                    "codigo_solicitud": str(getattr(s, "codigo_solicitud", "") or "")[:40],
                    "estado": str(getattr(s, "estado", "") or "proceso"),
                },
            )
            _emit_cliente_outbox_event(
                event_type="CLIENTE_DASHBOARD_UPDATED",
                aggregate_type="Cliente",
                aggregate_id=int(getattr(current_user, "id", 0) or 0),
                payload={
                    "cliente_id": int(getattr(current_user, "id", 0) or 0),
                    "solicitud_id": int(getattr(s, "id", 0) or 0),
                    "reason": "solicitud_creada",
                },
            )
            db.session.commit()
            _clear_cliente_solicitud_draft(cliente_id=cliente_id)
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
    elif request.method == "GET":
        draft_meta = draft_meta or _cliente_solicitud_draft_meta(cliente_id)

    return render_template(
        'clientes/solicitud_form.html',
        form=form,
        nuevo=True,
        initial_wizard_step=initial_wizard_step,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        public_modalidad_group=public_modalidad_group,
        public_modalidad_specific=public_modalidad_specific,
        public_modalidad_other=public_modalidad_other,
        draft_meta=(draft_meta or _cliente_solicitud_draft_meta(cliente_id)),
        draft_restored=draft_restored,
    )


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
    public_pasaje_mode = "aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido"
    public_pasaje_otro = ""
    public_modalidad_group = ""
    public_modalidad_specific = ""
    public_modalidad_other = ""

    if request.method == 'GET':
        form.funciones.data      = _clean_list(s.funciones)
        form.areas_comunes.data  = _normalize_areas_comunes_selected(
            _clean_list(s.areas_comunes),
            form.areas_comunes.choices,
        )

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
            base_otro = (getattr(s, 'funciones_otro', '') or '').strip()
            if hasattr(form, 'funciones_otro'):
                form.funciones_otro.data = ', '.join(custom_fun) if custom_fun else base_otro
            if (custom_fun or base_otro) and hasattr(form, 'funciones_otro'):
                data = set(form.funciones.data or [])
                data.add('otro')
                form.funciones.data = list(data)
        except Exception:
            pass

        try:
            allowed_tl = {str(v) for v, _ in form.tipo_lugar.choices}
            if s.tipo_lugar and s.tipo_lugar not in allowed_tl and hasattr(form, 'tipo_lugar_otro'):
                form.tipo_lugar.data = 'otro'
                form.tipo_lugar_otro.data = s.tipo_lugar
        except Exception:
            pass

        try:
            if hasattr(form, 'areas_comunes') and hasattr(form, 'area_otro'):
                form.area_otro.data = (getattr(s, 'area_otro', '') or '').strip()
                if (form.area_otro.data or '').strip():
                    area_codes = set(_clean_list(form.areas_comunes.data))
                    area_codes.add('otro')
                    form.areas_comunes.data = list(area_codes)
        except Exception:
            pass

        if form.dos_pisos.data is None:
            form.dos_pisos.data = bool(getattr(s, 'dos_pisos', False))
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))
        public_pasaje_mode, public_pasaje_otro = read_pasaje_mode_text(
            pasaje_aporte=getattr(s, "pasaje_aporte", False),
            detalles_servicio=getattr(s, "detalles_servicio", None),
            nota_cliente=getattr(s, "nota_cliente", ""),
        )
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=False)

    if request.method == "POST":
        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=True)

    if form.validate_on_submit():
        actor_user = str(int(getattr(current_user, 'id', 0) or 0))
        blocked_edit_burst, _ = enforce_business_limit(
            cache_obj=cache,
            scope="cliente_solicitud_edit_hour",
            actor=actor_user,
            limit=30,
            window_seconds=3600,
            reason="edit_burst_limit",
            summary="Bloqueo por ediciones masivas de solicitudes (cliente)",
            metadata={"route": (request.path or ""), "solicitud_id": int(id)},
        )
        if blocked_edit_burst:
            flash('Demasiadas actualizaciones en poco tiempo. Intenta nuevamente más tarde.', 'warning')
            return redirect(url_for('clientes.detalle_solicitud', id=id))

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
            prev_modalidad = (getattr(s, "modalidad_trabajo", "") or "").strip()
            form.populate_obj(s)
            _normalize_modalidad_on_solicitud(s)
            submitted_modalidad = (
                (getattr(form, "modalidad_trabajo", None).data or "").strip()
                if hasattr(form, "modalidad_trabajo")
                else ""
            )
            if hasattr(s, "modalidad_trabajo") and should_preserve_existing_modalidad_on_edit(
                existing_value=prev_modalidad,
                submitted_value=submitted_modalidad,
                submitted_group=request.form.get("modalidad_grupo"),
                submitted_specific=request.form.get("modalidad_especifica"),
                submitted_other=request.form.get("modalidad_otro_text"),
            ):
                s.modalidad_trabajo = prev_modalidad or None

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
            if funciones_otro_txt and 'otro' not in selected_funciones:
                selected_funciones.append('otro')
            funciones_otro_clean = funciones_otro_txt if 'otro' in selected_funciones else ''
            _set_attr_if_exists(s, 'funciones_otro', funciones_otro_clean or None)

            s.funciones      = _map_funciones(selected_funciones, funciones_otro_clean)
            areas_selected_raw = _clean_list(form.areas_comunes.data)
            area_otro_txt = (form.area_otro.data or '').strip() if hasattr(form, 'area_otro') else ''
            areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
            s.areas_comunes  = _normalize_areas_comunes_selected(
                areas_selected_raw,
                form.areas_comunes.choices,
            )
            edad_codes_selected = _clean_list(form.edad_requerida.data)
            edad_otro_txt = (getattr(form, 'edad_otro', None).data or '').strip() if hasattr(form, 'edad_otro') else ''
            if edad_otro_txt and 'otro' not in edad_codes_selected:
                edad_codes_selected.append('otro')
            s.edad_requerida = _map_edad_choices(
                edad_codes_selected,
                form.edad_requerida.choices,
                edad_otro_txt,
            )
            tipo_lugar_value = getattr(s, 'tipo_lugar', '')
            tipo_lugar_otro_txt = (getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
            if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                tipo_lugar_value = 'otro'
            s.tipo_lugar = _map_tipo_lugar(
                tipo_lugar_value,
                tipo_lugar_otro_txt,
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if areas_has_otro else '') or None
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = strip_pasaje_marker_from_note((form.nota_cliente.data or '').strip())
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            apply_pasaje_to_solicitud(
                s,
                mode_raw=public_pasaje_mode,
                text_raw=public_pasaje_otro,
                default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
            )
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = utc_now_naive()

            db.session.flush()
            _emit_cliente_outbox_event(
                event_type="CLIENTE_SOLICITUD_UPDATED",
                aggregate_type="Solicitud",
                aggregate_id=int(getattr(s, "id", 0) or 0),
                aggregate_version=int(getattr(s, "row_version", 0) or 0) + 1,
                payload={
                    "cliente_id": int(getattr(current_user, "id", 0) or 0),
                    "solicitud_id": int(getattr(s, "id", 0) or 0),
                    "codigo_solicitud": str(getattr(s, "codigo_solicitud", "") or "")[:40],
                    "estado": str(getattr(s, "estado", "") or ""),
                },
            )
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

    return render_template(
        'clientes/solicitud_form.html',
        form=form,
        editar=True,
        solicitud=s,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        public_modalidad_group=public_modalidad_group,
        public_modalidad_specific=public_modalidad_specific,
        public_modalidad_other=public_modalidad_other,
    )


# ─────────────────────────────────────────────────────────────
# Detalle de solicitud
# ─────────────────────────────────────────────────────────────
_ESTADO_CLIENTE_LABELS = {
    "proceso": "En revision",
    "activa": "Activa",
    "espera_pago": "Pendiente de pago",
    "pagada": "Pagada",
    "cancelada": "Cancelada",
    "reemplazo": "Reemplazo",
}


def _estado_cliente_label(estado: Optional[str]) -> str:
    estado_norm = (estado or "").strip().lower()
    if not estado_norm:
        return "Sin estado"
    return _ESTADO_CLIENTE_LABELS.get(estado_norm, estado_norm.replace("_", " ").title())


def _pick_event_date(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _build_solicitud_timeline_simple(s: Solicitud, candidatas_enviadas: list) -> list:
    estado_norm = str(getattr(s, "estado", "") or "").strip().lower()
    total_enviadas = len(candidatas_enviadas or [])
    seleccionadas = [sc for sc in (candidatas_enviadas or []) if str(getattr(sc, "status", "") or "").strip().lower() == "seleccionada"]

    timeline = [
        {
            "id": "creada",
            "titulo": "Solicitud creada",
            "detalle": f"Codigo {getattr(s, 'codigo_solicitud', '') or ''}".strip(),
            "fecha": getattr(s, "fecha_solicitud", None),
            "tone": "primary",
        }
    ]

    if estado_norm in {"proceso", "activa"}:
        timeline.append({
            "id": "revision",
            "titulo": "En revision",
            "detalle": "Tu solicitud esta siendo evaluada por el equipo.",
            "fecha": _pick_event_date(getattr(s, "estado_actual_desde", None), getattr(s, "fecha_ultimo_estado", None)),
            "tone": "info",
        })

    if total_enviadas > 0:
        fecha_envio = _pick_event_date(
            getattr(candidatas_enviadas[0], "created_at", None),
            getattr(candidatas_enviadas[0], "updated_at", None),
        )
        timeline.append({
            "id": "candidatas_enviadas",
            "titulo": "Candidatas enviadas",
            "detalle": f"Se enviaron {total_enviadas} perfiles para revision.",
            "fecha": fecha_envio,
            "tone": "success",
        })

    if seleccionadas:
        sel = seleccionadas[0]
        timeline.append({
            "id": "entrevista",
            "titulo": "Entrevista coordinada",
            "detalle": "Confirmaste una candidata para entrevista.",
            "fecha": _pick_event_date(getattr(sel, "updated_at", None), getattr(sel, "created_at", None)),
            "tone": "success",
        })

    if estado_norm == "espera_pago":
        timeline.append({
            "id": "pendiente_pago",
            "titulo": "Pendiente de pago",
            "detalle": "Falta completar el pago para continuar el proceso.",
            "fecha": _pick_event_date(
                getattr(s, "fecha_cambio_espera_pago", None),
                getattr(s, "estado_actual_desde", None),
                getattr(s, "fecha_ultimo_estado", None),
            ),
            "tone": "warning",
        })

    if getattr(s, "candidata", None) is not None:
        timeline.append({
            "id": "candidata_elegida",
            "titulo": "Candidata elegida",
            "detalle": getattr(getattr(s, "candidata", None), "nombre_completo", None) or "Candidata asignada",
            "fecha": _pick_event_date(
                getattr(s, "fecha_ultimo_estado", None),
                getattr(s, "estado_actual_desde", None),
                getattr(s, "fecha_inicio_seguimiento", None),
            ),
            "tone": "success",
        })

    for idx, repl in enumerate(getattr(s, "reemplazos", []) or [], start=1):
        fecha_repl = _pick_event_date(getattr(repl, "fecha_inicio_reemplazo", None), getattr(repl, "created_at", None))
        if fecha_repl is None and not getattr(repl, "candidata_new", None):
            continue
        nombre_repl = getattr(getattr(repl, "candidata_new", None), "nombre_completo", None) or "En gestion"
        timeline.append({
            "id": f"reemplazo_{idx}",
            "titulo": f"Reemplazo #{idx}",
            "detalle": nombre_repl,
            "fecha": fecha_repl,
            "tone": "warning",
        })

    if estado_norm == "reemplazo":
        timeline.append({
            "id": "reemplazo_abierto",
            "titulo": "Reemplazo activo",
            "detalle": "Estamos gestionando nuevas opciones para tu solicitud.",
            "fecha": _pick_event_date(getattr(s, "estado_actual_desde", None), getattr(s, "fecha_ultimo_estado", None)),
            "tone": "warning",
        })

    if estado_norm == "pagada":
        timeline.append({
            "id": "proceso_finalizado",
            "titulo": "Proceso finalizado",
            "detalle": "Se registro el pago y el proceso esta en su etapa final.",
            "fecha": _pick_event_date(getattr(s, "fecha_ultimo_estado", None), getattr(s, "estado_actual_desde", None)),
            "tone": "success",
        })

    if estado_norm == "cancelada" and getattr(s, "fecha_cancelacion", None):
        timeline.append({
            "id": "cancelada",
            "titulo": "Solicitud cancelada",
            "detalle": getattr(s, "motivo_cancelacion", None) or "Cancelada por el cliente.",
            "fecha": getattr(s, "fecha_cancelacion", None),
            "tone": "secondary",
        })

    timeline.sort(key=lambda ev: ev.get("fecha") or datetime.min)
    return timeline


def _build_solicitud_que_sigue(s: Solicitud, candidatas_enviadas: list) -> dict:
    estado_norm = str(getattr(s, "estado", "") or "").strip().lower()
    total_enviadas = len(candidatas_enviadas or [])
    hay_seleccionada = any(
        str(getattr(sc, "status", "") or "").strip().lower() == "seleccionada"
        for sc in (candidatas_enviadas or [])
    )

    if estado_norm == "cancelada":
        return {
            "icon": "bi-stop-circle",
            "variant": "secondary",
            "titulo": "Esta solicitud fue cancelada",
            "mensaje": "El proceso se cerro. Si deseas continuar, puedes crear una nueva solicitud.",
        }
    if estado_norm == "espera_pago":
        return {
            "icon": "bi-credit-card",
            "variant": "warning",
            "titulo": "Ahora debes completar el pago pendiente",
            "mensaje": "Al registrar el pago, continuamos con la siguiente etapa del proceso.",
        }
    if estado_norm == "reemplazo":
        if total_enviadas > 0:
            msg = "Ya puedes revisar los perfiles enviados para reemplazo."
        else:
            msg = "Estamos buscando nuevas candidatas para cubrir el reemplazo."
        return {
            "icon": "bi-arrow-repeat",
            "variant": "warning",
            "titulo": "Solicitud en reemplazo",
            "mensaje": msg,
        }
    if getattr(s, "candidata_id", None) or hay_seleccionada:
        if estado_norm == "pagada":
            return {
                "icon": "bi-check2-circle",
                "variant": "success",
                "titulo": "Estamos cerrando el proceso",
                "mensaje": "Tu solicitud ya esta en etapa final de documentacion y entrega.",
            }
        return {
            "icon": "bi-calendar-check",
            "variant": "primary",
            "titulo": "Estamos coordinando el inicio",
            "mensaje": "Ya hay una candidata elegida y seguimos con los pasos finales.",
        }
    if total_enviadas > 0:
        return {
            "icon": "bi-people",
            "variant": "success",
            "titulo": "Ya puedes revisar los perfiles enviados",
            "mensaje": "Entra al listado de candidatas para evaluar y seleccionar.",
        }
    if estado_norm in {"proceso", "activa"}:
        return {
            "icon": "bi-hourglass-split",
            "variant": "info",
            "titulo": "Tu solicitud esta siendo evaluada",
            "mensaje": "Estamos validando el perfil para enviarte candidatas compatibles.",
        }
    return {
        "icon": "bi-compass",
        "variant": "info",
        "titulo": "Te avisaremos el siguiente paso",
        "mensaje": "Tu solicitud sigue activa y el equipo continuara el proceso.",
    }


def _build_solicitud_acciones_rapidas(
    s: Solicitud,
    candidatas_enviadas: list,
    *,
    chat_enabled: bool,
) -> list[dict]:
    estado_norm = str(getattr(s, "estado", "") or "").strip().lower()
    total_enviadas = len(candidatas_enviadas or [])
    acciones = []
    usados = set()

    def _add(accion_id: str, **payload):
        if not accion_id or accion_id in usados:
            return
        usados.add(accion_id)
        row = {"id": accion_id}
        row.update(payload)
        acciones.append(row)

    _add(
        "seguimiento",
        label="Ver seguimiento",
        hint="Linea de tiempo de esta solicitud.",
        icon="bi-clock-history",
        url=url_for("clientes.seguimiento_solicitud", id=s.id),
        btn_class="btn-outline-primary",
    )

    if total_enviadas > 0:
        _add(
            "candidatas",
            label="Ver candidatas enviadas",
            hint="Perfiles disponibles para revisar.",
            icon="bi-people",
            url=url_for("clientes.solicitud_candidatas", solicitud_id=s.id),
            btn_class="btn-outline-success",
            badge=str(total_enviadas),
        )

    if estado_norm == "reemplazo":
        if total_enviadas > 0:
            _add(
                "reemplazo",
                label="Revisar reemplazo",
                hint="Opciones enviadas durante el reemplazo.",
                icon="bi-arrow-repeat",
                url=url_for("clientes.solicitud_candidatas", solicitud_id=s.id),
                btn_class="btn-outline-warning",
            )
        else:
            _add(
                "reemplazo",
                label="Ver seguimiento del reemplazo",
                hint="Estado actual del reemplazo en curso.",
                icon="bi-arrow-repeat",
                url=url_for("clientes.seguimiento_solicitud", id=s.id),
                btn_class="btn-outline-warning",
            )

    if estado_norm == "espera_pago":
        _add(
            "pago",
            label="Ver estado de pago",
            hint="Confirma el avance de esta etapa.",
            icon="bi-credit-card",
            url=url_for("clientes.seguimiento_solicitud", id=s.id),
            btn_class="btn-outline-warning",
        )

    if estado_norm == "proceso":
        _add(
            "editar",
            label="Editar solicitud",
            hint="Ajusta datos antes de la asignacion.",
            icon="bi-pencil-square",
            url=url_for("clientes.editar_solicitud", id=s.id),
            btn_class="btn-outline-secondary",
        )

    if chat_enabled:
        chat_label = "Ir al chat de esta solicitud"
        chat_hint = "Soporte directo relacionado con este caso."
        if estado_norm == "espera_pago":
            chat_label = "Consultar pago por chat"
            chat_hint = "Contacta soporte para instrucciones de pago."
        _add(
            "chat",
            label=chat_label,
            hint=chat_hint,
            icon="bi-chat-left-text",
            url=url_for("clientes.chat_cliente", solicitud_id=s.id),
            btn_class="btn-outline-dark",
        )

    _add(
        "proceso",
        label="Ver como funciona el proceso",
        hint="Guia general de etapas y tiempos.",
        icon="bi-diagram-3",
        url=url_for("clientes.proceso_contratacion"),
        btn_class="btn-outline-info",
    )

    return acciones


def _build_solicitud_ayuda_contextual(
    s: Solicitud,
    candidatas_enviadas: list,
    *,
    que_sigue: Optional[dict] = None,
) -> dict:
    estado_norm = str(getattr(s, "estado", "") or "").strip().lower()
    total_enviadas = len(candidatas_enviadas or [])
    base = {
        "titulo": "Ayuda para esta solicitud",
        "items": [
            {
                "q": "¿Qué pasa en esta etapa?",
                "a": "Esta solicitud se mantiene actualizada por estado para que veas en que punto va.",
                "icon": "bi-signpost",
            },
            {
                "q": "¿Qué debo hacer ahora?",
                "a": (que_sigue or {}).get("mensaje") or "Revisa las acciones disponibles de esta solicitud.",
                "icon": "bi-check2-square",
            },
            {
                "q": "¿Cuánto tarda normalmente esta parte?",
                "a": "Puede variar segun disponibilidad y perfil. El timeline te mostrara cada avance registrado.",
                "icon": "bi-clock-history",
            },
            {
                "q": "¿Qué hago si necesito ayuda?",
                "a": "Puedes abrir el chat de esta solicitud para soporte directo.",
                "icon": "bi-chat-left-text",
            },
        ],
    }

    if estado_norm == "espera_pago":
        base["items"][0]["a"] = "La solicitud esta en espera de pago para continuar con el proceso."
        base["items"][1]["a"] = "Verifica el estado de pago y confirma por chat si necesitas instrucciones."
        base["items"][2]["a"] = "Al confirmar el pago, el estado cambia y podras seguir la siguiente etapa."
    elif estado_norm == "reemplazo":
        base["items"][0]["a"] = "Esta solicitud esta en reemplazo y el equipo gestiona nuevas opciones."
        if total_enviadas > 0:
            base["items"][1]["a"] = "Revisa las candidatas enviadas para reemplazo y confirma tu decision."
        else:
            base["items"][1]["a"] = "Da seguimiento al timeline mientras preparamos nuevas candidatas."
    elif estado_norm in {"proceso", "activa"}:
        if total_enviadas > 0:
            base["items"][0]["a"] = "Ya tienes candidatas enviadas para revisar en esta misma solicitud."
            base["items"][1]["a"] = "Evalua los perfiles enviados y usa chat si necesitas orientacion."
        else:
            base["items"][0]["a"] = "Tu solicitud esta en evaluacion para enviarte perfiles compatibles."
            base["items"][1]["a"] = "Mantente pendiente del detalle y del seguimiento para nuevos movimientos."
    elif estado_norm == "pagada":
        base["items"][0]["a"] = "Tu solicitud esta en etapa final tras la confirmacion de pago."
    elif estado_norm == "cancelada":
        base["items"][0]["a"] = "Esta solicitud fue cancelada y no tendra nuevos movimientos."
        base["items"][1]["a"] = "Si deseas continuar, crea una nueva solicitud desde tu panel."
    return base


def _build_solicitud_trust_signals(
    s: Solicitud,
    candidatas_enviadas: Optional[list] = None,
) -> list[dict]:
    estado_norm = str(getattr(s, "estado", "") or "").strip().lower()
    rows = list(candidatas_enviadas or [])
    has_selected = any(
        str(getattr(sc, "status", "") or "").strip().lower() == "seleccionada"
        for sc in rows
    )
    has_candidate = bool(
        getattr(s, "candidata_id", None)
        or getattr(s, "candidata", None)
        or has_selected
    )
    signals = []
    ids = set()

    def _push(sig_id: str, *, variant: str, icon: str, title: str, text: str) -> None:
        if not sig_id or sig_id in ids:
            return
        ids.add(sig_id)
        signals.append(
            {
                "id": sig_id,
                "variant": variant,
                "icon": icon,
                "title": title,
                "text": text,
            }
        )

    if estado_norm == "espera_pago":
        _push(
            "pago_modelo",
            variant="warning",
            icon="bi-credit-card-2-front",
            title="Pago claro en esta etapa",
            text="El proceso se activa con 50% inicial. El restante se completa al finalizar. La gestion es 25% sobre la primera quincena y no es un cobro recurrente.",
        )
        _push(
            "pago_avance",
            variant="info",
            icon="bi-signpost-split",
            title="Que cambia al confirmar pago",
            text="Al confirmar el pago, actualizamos el estado y continuamos con la etapa operativa siguiente.",
        )

    if estado_norm in {"proceso", "activa"}:
        _push(
            "proceso_activo",
            variant="info",
            icon="bi-hourglass-split",
            title="Evaluacion activa del equipo",
            text="Tu solicitud se esta evaluando activamente. Cada movimiento se refleja en tu timeline y en el chat de esta solicitud.",
        )

    if has_candidate and estado_norm not in {"pagada", "cancelada"}:
        _push(
            "candidata_elegida",
            variant="success",
            icon="bi-person-check",
            title="Candidata elegida: siguiente paso definido",
            text="Con candidata elegida, pasamos a coordinacion de inicio y validaciones finales segun el estado de pago.",
        )

    if estado_norm == "reemplazo":
        _push(
            "reemplazo_cobertura",
            variant="warning",
            icon="bi-shield-check",
            title="Reemplazo con cobertura de la agencia",
            text="Tu caso sigue cubierto durante el reemplazo. No reinicias desde cero: mantenemos seguimiento hasta cerrar una nueva opcion.",
        )

    return signals


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
    timeline_simple = _build_solicitud_timeline_simple(s, candidatas_enviadas)
    que_sigue = _build_solicitud_que_sigue(s, candidatas_enviadas)
    acciones_rapidas = _build_solicitud_acciones_rapidas(
        s,
        candidatas_enviadas,
        chat_enabled=_chat_enabled(),
    )
    ayuda_contextual = _build_solicitud_ayuda_contextual(
        s,
        candidatas_enviadas,
        que_sigue=que_sigue,
    )
    trust_signals = _build_solicitud_trust_signals(s, candidatas_enviadas)
    estado_legible = _estado_cliente_label(getattr(s, "estado", None))

    return render_template(
        'clientes/solicitud_detail.html',
        s=s,
        compat=compat_result,
        envios=envios,
        cancelaciones=cancelaciones,
        hoy=rd_today(),
        candidatas_enviadas=candidatas_enviadas,
        candidatas_enviadas_cards=candidatas_enviadas_cards,
        candidatas_enviadas_count=len(candidatas_enviadas),
        timeline_simple=timeline_simple,
        que_sigue=que_sigue,
        acciones_rapidas=acciones_rapidas,
        ayuda_contextual=ayuda_contextual,
        trust_signals=trust_signals,
        estado_legible=estado_legible,
    )


_CHAT_MESSAGE_MAX_LEN = 1800
_CHAT_CONV_PAGE_LIMIT = 30
_CHAT_MSG_PAGE_LIMIT = 50


def _chat_enabled() -> bool:
    return bool(ChatConversation is not None and ChatMessage is not None)


def _chat_scope_key(*, cliente_id: int, solicitud_id: Optional[int]) -> str:
    if chat_e2e_enabled():
        return chat_e2e_scope_key(cliente_id=int(cliente_id or 0), solicitud_id=solicitud_id)
    if int(solicitud_id or 0) > 0:
        return f"solicitud:{int(solicitud_id)}"
    return f"general:{int(cliente_id)}"


def _chat_subject_for_solicitud(solicitud: Optional[Solicitud]) -> str:
    if not solicitud:
        return chat_e2e_subject("Soporte general")
    codigo = str(getattr(solicitud, "codigo_solicitud", "") or "").strip()
    base = f"Soporte solicitud {codigo}" if codigo else f"Soporte solicitud #{int(getattr(solicitud, 'id', 0) or 0)}"
    return chat_e2e_subject(base)


def _chat_message_preview(body: str) -> str:
    txt = re.sub(r"\s+", " ", str(body or "")).strip()
    return txt[:220]


def _chat_get_or_create_conversation_for_cliente(*, cliente_id: int, solicitud_id: Optional[int] = None):
    if not _chat_enabled():
        abort(404)

    cid = int(cliente_id or 0)
    sid = int(solicitud_id or 0) or None
    if cid <= 0:
        abort(403)

    if chat_e2e_enabled():
        try:
            enforce_e2e_cliente_id(cid)
            enforce_e2e_solicitud_id(sid)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))

    solicitud = None
    if sid:
        solicitud = Solicitud.query.filter_by(id=sid, cliente_id=cid).first_or_404()

    scope_key = _chat_scope_key(cliente_id=cid, solicitud_id=sid)
    conv = ChatConversation.query.filter_by(scope_key=scope_key).first()
    if conv is not None:
        if chat_e2e_enabled():
            try:
                enforce_e2e_conversation(conv)
            except E2EChatGuardError as exc:
                abort(403, description=str(exc.reason))
        return conv

    now = utc_now_naive()
    conv = ChatConversation(
        scope_key=scope_key,
        conversation_type="solicitud" if sid else "general",
        status="open",
        cliente_id=cid,
        solicitud_id=sid,
        subject=_chat_subject_for_solicitud(solicitud),
        created_at=now,
        updated_at=now,
    )
    db.session.add(conv)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        conv = ChatConversation.query.filter_by(scope_key=scope_key).first()
        if conv is None:
            raise
    return conv


def _chat_cliente_conversation_or_404(conversation_id: int):
    if not _chat_enabled():
        abort(404)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation_id(int(conversation_id or 0))
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    cid = int(getattr(current_user, "id", 0) or 0)
    conv = (
        ChatConversation.query
        .filter_by(id=int(conversation_id), cliente_id=cid)
        .first_or_404()
    )
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    return conv


def _chat_cliente_conversation_for_update_or_404(conversation_id: int):
    if not _chat_enabled():
        abort(404)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation_id(int(conversation_id or 0))
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    cid = int(getattr(current_user, "id", 0) or 0)
    q = (
        ChatConversation.query
        .enable_eagerloads(False)
        .filter_by(id=int(conversation_id), cliente_id=cid)
    )
    try:
        conv = q.with_for_update(of=ChatConversation).first_or_404()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] with_for_update failed in client thread lookup; fallback unlocked query (conversation_id=%s, cliente_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            int(cid or 0),
            type(exc).__name__,
            str(exc),
        )
        conv = q.first_or_404()
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    return conv


def _chat_wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    xr = (request.headers.get("X-Requested-With") or "").lower()
    return ("application/json" in accept) or (xr == "xmlhttprequest") or (str(request.args.get("ajax") or "").strip() == "1")


def _chat_valid_status(raw: Optional[str], *, default: str = _CHAT_STATUS_OPEN) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _CHAT_STATUS_VALUES else str(default)


def _chat_serialize_conversation_for_cliente(conv) -> dict:
    solicitud = getattr(conv, "solicitud", None)
    solicitud_id = int(getattr(conv, "solicitud_id", 0) or 0) or None
    last_message_at = getattr(conv, "last_message_at", None)
    return {
        "id": int(conv.id),
        "conversation_type": str(getattr(conv, "conversation_type", "") or "general"),
        "subject": str(getattr(conv, "subject", "") or "Soporte"),
        "status": str(getattr(conv, "status", "") or "open"),
        "solicitud_id": solicitud_id,
        "solicitud_codigo": str(getattr(solicitud, "codigo_solicitud", "") or "") if solicitud is not None else "",
        "last_message_at": iso_utc_z(last_message_at) if last_message_at else None,
        "last_message_preview": str(getattr(conv, "last_message_preview", "") or ""),
        "last_message_sender_type": str(getattr(conv, "last_message_sender_type", "") or ""),
        "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
        "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
        "thread_url": url_for("clientes.chat_cliente", conversation_id=int(conv.id)),
    }


def _chat_serialize_message_for_cliente(msg) -> dict:
    sender_type = str(getattr(msg, "sender_type", "") or "")
    sender_name = "Soporte"
    if sender_type == "cliente":
        sender_name = "Tú"
    else:
        staff_row = getattr(msg, "sender_staff_user", None)
        if staff_row is not None:
            sender_name = str(getattr(staff_row, "username", "") or "Soporte")
    return {
        "id": int(getattr(msg, "id", 0) or 0),
        "conversation_id": int(getattr(msg, "conversation_id", 0) or 0),
        "sender_type": sender_type,
        "sender_name": sender_name,
        "body": str(getattr(msg, "body", "") or ""),
        "created_at": iso_utc_z(getattr(msg, "created_at", None)),
        "is_mine": sender_type == "cliente",
    }


def _chat_emit_event(*, event_type: str, conversation, message=None, reader_type: Optional[str] = None, extra_payload: Optional[dict] = None) -> None:
    if conversation is None:
        return
    payload = {
        "cliente_id": int(getattr(conversation, "cliente_id", 0) or 0),
        "solicitud_id": int(getattr(conversation, "solicitud_id", 0) or 0) or None,
        "conversation_id": int(getattr(conversation, "id", 0) or 0),
        "conversation_type": str(getattr(conversation, "conversation_type", "") or "general"),
        "status": _chat_valid_status(getattr(conversation, "status", None), default=_CHAT_STATUS_OPEN),
        "cliente_unread_count": int(getattr(conversation, "cliente_unread_count", 0) or 0),
        "staff_unread_count": int(getattr(conversation, "staff_unread_count", 0) or 0),
    }
    if message is not None:
        payload.update(
            {
                "message_id": int(getattr(message, "id", 0) or 0),
                "sender_type": str(getattr(message, "sender_type", "") or ""),
                "preview": _chat_message_preview(getattr(message, "body", "") or ""),
            }
        )
    if reader_type:
        payload["reader_type"] = str(reader_type)[:20]
    if isinstance(extra_payload, dict) and extra_payload:
        payload.update(dict(extra_payload))
    _emit_cliente_outbox_event(
        event_type=event_type,
        aggregate_type="ChatConversation",
        aggregate_id=int(getattr(conversation, "id", 0) or 0),
        payload=payload,
    )


@clientes_bp.route('/chat', methods=['GET'])
@login_required
@cliente_required
def chat_cliente():
    if not _chat_enabled():
        abort(404)

    cid = int(getattr(current_user, "id", 0) or 0)
    if chat_e2e_enabled():
        try:
            enforce_e2e_cliente_id(cid)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    conversation_id = _safe_int(request.args.get("conversation_id"), default=0)
    solicitud_id = _safe_int(request.args.get("solicitud_id"), default=0)

    if conversation_id > 0:
        selected = _chat_cliente_conversation_or_404(conversation_id)
    elif solicitud_id > 0:
        selected = _chat_get_or_create_conversation_for_cliente(cliente_id=cid, solicitud_id=solicitud_id)
        db.session.commit()
    else:
        query = ChatConversation.query.filter_by(cliente_id=cid)
        if chat_e2e_enabled():
            prefix = chat_e2e_scope_prefix()
            if prefix:
                query = query.filter(ChatConversation.scope_key.like(f"{prefix}%"))
        selected = (
            query
            .order_by(ChatConversation.last_message_at.desc().nullslast(), ChatConversation.id.desc())
            .first()
        )
        if selected is None:
            selected = _chat_get_or_create_conversation_for_cliente(cliente_id=cid, solicitud_id=None)
            db.session.commit()

    conversations_query = ChatConversation.query.filter_by(cliente_id=cid)
    if chat_e2e_enabled():
        prefix = chat_e2e_scope_prefix()
        if prefix:
            conversations_query = conversations_query.filter(ChatConversation.scope_key.like(f"{prefix}%"))
    conversations = (
        conversations_query
        .order_by(ChatConversation.last_message_at.desc().nullslast(), ChatConversation.id.desc())
        .limit(_CHAT_CONV_PAGE_LIMIT)
        .all()
    )

    messages = (
        ChatMessage.query
        .filter_by(conversation_id=int(selected.id), is_deleted=False)
        .order_by(ChatMessage.id.desc())
        .limit(_CHAT_MSG_PAGE_LIMIT)
        .all()
    )
    messages = list(reversed(messages or []))

    return render_template(
        "clientes/chat.html",
        chat_conversations=conversations,
        chat_selected=selected,
        chat_messages=messages,
        chat_message_max_len=_CHAT_MESSAGE_MAX_LEN,
    )


@clientes_bp.route('/chat/open', methods=['POST'])
@login_required
@cliente_required
def chat_cliente_open():
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    payload = request.get_json(silent=True) or {}
    solicitud_id = _safe_int(request.form.get("solicitud_id") or payload.get("solicitud_id"), default=0)
    cid = int(getattr(current_user, "id", 0) or 0)
    conv = _chat_get_or_create_conversation_for_cliente(
        cliente_id=cid,
        solicitud_id=(int(solicitud_id) if int(solicitud_id or 0) > 0 else None),
    )
    db.session.commit()
    target_url = url_for("clientes.chat_cliente", conversation_id=int(conv.id))
    if _chat_wants_json():
        return jsonify({"ok": True, "conversation_id": int(conv.id), "redirect_url": target_url})
    return redirect(target_url)


@clientes_bp.route('/chat/conversations.json', methods=['GET'])
@login_required
@cliente_required
def chat_cliente_conversations_json():
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    cid = int(getattr(current_user, "id", 0) or 0)
    if chat_e2e_enabled():
        try:
            enforce_e2e_cliente_id(cid)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    rows_query = ChatConversation.query.filter_by(cliente_id=cid)
    if chat_e2e_enabled():
        prefix = chat_e2e_scope_prefix()
        if prefix:
            rows_query = rows_query.filter(ChatConversation.scope_key.like(f"{prefix}%"))
    rows = (
        rows_query
        .order_by(ChatConversation.last_message_at.desc().nullslast(), ChatConversation.id.desc())
        .limit(_CHAT_CONV_PAGE_LIMIT)
        .all()
    )
    total_unread = int(sum(int(getattr(r, "cliente_unread_count", 0) or 0) for r in (rows or [])))
    return jsonify(
        {
            "ok": True,
            "items": [_chat_serialize_conversation_for_cliente(r) for r in (rows or [])],
            "unread_count": total_unread,
            "ts": iso_utc_z(),
        }
    )


@clientes_bp.route('/chat/conversations/<int:conversation_id>/messages.json', methods=['GET'])
@login_required
@cliente_required
def chat_cliente_messages_json(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    conv = _chat_cliente_conversation_or_404(conversation_id)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    before_id = _safe_int(request.args.get("before_id"), default=0)
    limit = max(1, min(_safe_int(request.args.get("limit"), default=_CHAT_MSG_PAGE_LIMIT), 80))

    q = (
        ChatMessage.query
        .filter_by(conversation_id=int(conv.id), is_deleted=False)
        .order_by(ChatMessage.id.desc())
    )
    if before_id > 0:
        q = q.filter(ChatMessage.id < int(before_id))
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    rows = list(reversed(rows or []))
    next_before_id = int(rows[0].id) if rows else int(before_id or 0)

    return jsonify(
        {
            "ok": True,
            "conversation": _chat_serialize_conversation_for_cliente(conv),
            "items": [_chat_serialize_message_for_cliente(m) for m in rows],
            "has_more": bool(has_more),
            "next_before_id": int(next_before_id),
            "ts": iso_utc_z(),
        }
    )


@clientes_bp.route('/chat/conversations/<int:conversation_id>/messages', methods=['POST'])
@login_required
@cliente_required
def chat_cliente_send_message(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404

    conv = _chat_cliente_conversation_or_404(conversation_id)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    body_raw = (request.form.get("body") or (request.get_json(silent=True) or {}).get("body") or "")
    body = re.sub(r"\s+", " ", str(body_raw or "")).strip()
    if not body:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    if len(body) > _CHAT_MESSAGE_MAX_LEN:
        return jsonify({"ok": False, "error": "message_too_long", "max_len": _CHAT_MESSAGE_MAX_LEN}), 400

    actor = f"cliente:{int(getattr(current_user, 'id', 0) or 0)}"
    blocked, _count = enforce_business_limit(
        cache_obj=cache,
        scope="chat_cliente_send",
        actor=actor,
        limit=20,
        window_seconds=60,
        reason="chat_rate_limit",
        summary="Rate limit en chat cliente",
        metadata={"conversation_id": int(conv.id)},
        alert_on_block=False,
    )
    if blocked:
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    blocked_fast, _elapsed = enforce_min_human_interval(
        cache_obj=cache,
        scope="chat_cliente_human_interval",
        actor=actor,
        min_seconds=1,
        reason="chat_too_fast",
        summary="Patrón de chat muy rápido cliente",
        metadata={"conversation_id": int(conv.id)},
    )
    if blocked_fast:
        return jsonify({"ok": False, "error": "too_fast"}), 429

    try:
        conv = _chat_cliente_conversation_for_update_or_404(int(conversation_id))
        now = utc_now_naive()
        msg = ChatMessage(
            conversation_id=int(conv.id),
            sender_type="cliente",
            sender_cliente_id=int(getattr(current_user, "id", 0) or 0),
            body=body,
            meta=e2e_message_meta({}),
            created_at=now,
        )
        db.session.add(msg)

        conv.last_message_at = now
        conv.last_message_preview = _chat_message_preview(body)
        conv.last_message_sender_type = "cliente"
        conv.staff_unread_count = int(getattr(conv, "staff_unread_count", 0) or 0) + 1
        prev_status = _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN)
        conv.status = _CHAT_STATUS_OPEN
        conv.updated_at = now
        db.session.add(conv)
        db.session.flush()
        _chat_emit_event(event_type="CHAT_MESSAGE_CREATED", conversation=conv, message=msg)
        if prev_status != _CHAT_STATUS_OPEN:
            _chat_emit_event(
                event_type="CHAT_CONVERSATION_STATUS_CHANGED",
                conversation=conv,
                reader_type="cliente",
                extra_payload={"from": prev_status, "to": _CHAT_STATUS_OPEN},
            )
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] client send message failed (conversation_id=%s, cliente_id=%s, sql_error=%s: %s)",
            int(conversation_id or 0),
            int(getattr(current_user, "id", 0) or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] client send message failed (conversation_id=%s, cliente_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            int(getattr(current_user, "id", 0) or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500

    payload = {
        "ok": True,
        "conversation": _chat_serialize_conversation_for_cliente(conv),
        "message": _chat_serialize_message_for_cliente(msg),
        "ts": iso_utc_z(),
    }
    return jsonify(payload)


@clientes_bp.route('/chat/conversations/<int:conversation_id>/read', methods=['POST'])
@login_required
@cliente_required
def chat_cliente_mark_read(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    try:
        conv = _chat_cliente_conversation_for_update_or_404(int(conversation_id))
        if chat_e2e_enabled():
            try:
                enforce_e2e_conversation(conv)
            except E2EChatGuardError as exc:
                return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
        changed = int(getattr(conv, "cliente_unread_count", 0) or 0) > 0
        now = utc_now_naive()
        conv.cliente_unread_count = 0
        conv.client_last_read_at = now
        conv.updated_at = now
        db.session.add(conv)
        if changed:
            _chat_emit_event(event_type="CHAT_CONVERSATION_READ", conversation=conv, reader_type="cliente")
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] client mark read failed (conversation_id=%s, cliente_id=%s, sql_error=%s: %s)",
            int(conversation_id or 0),
            int(getattr(current_user, "id", 0) or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] client mark read failed (conversation_id=%s, cliente_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            int(getattr(current_user, "id", 0) or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify(
        {
            "ok": True,
            "conversation_id": int(conv.id),
            "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
            "ts": iso_utc_z(),
        }
    )


_NOTIF_TIPO_CANDIDATAS_ENVIADAS = "candidatas_enviadas"
_NOTIF_TIPO_CANDIDATAS_DETALLE = {
    "candidatas_enviadas",
    "candidata_seleccionada",
}


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
        notif_tipo = str(getattr(notif, "tipo", "") or "").strip().lower()
        if notif_tipo in _NOTIF_TIPO_CANDIDATAS_DETALLE:
            return url_for("clientes.solicitud_candidatas", solicitud_id=solicitud_id) + "#candidatas-enviadas"
        return url_for("clientes.detalle_solicitud", id=solicitud_id)
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
    flash("La vista /clientes/notificaciones fue retirada. Usa la campana para ver y gestionar notificaciones.", "info")
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
                "created_at": iso_utc_z(n.created_at) if n.created_at else None,
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
        notif.updated_at = utc_now_naive()
        _emit_cliente_outbox_event(
            event_type="CLIENTE_NOTIFICACION_READ",
            aggregate_type="ClienteNotificacion",
            aggregate_id=int(getattr(notif, "id", 0) or 0),
            payload={
                "cliente_id": int(getattr(current_user, "id", 0) or 0),
                "notificacion_id": int(getattr(notif, "id", 0) or 0),
                "solicitud_id": int(getattr(notif, "solicitud_id", 0) or 0) or None,
                "tipo": str(getattr(notif, "tipo", "") or "")[:80],
            },
        )
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
        notif.updated_at = utc_now_naive()
        _emit_cliente_outbox_event(
            event_type="CLIENTE_NOTIFICACION_READ",
            aggregate_type="ClienteNotificacion",
            aggregate_id=int(getattr(notif, "id", 0) or 0),
            payload={
                "cliente_id": int(getattr(current_user, "id", 0) or 0),
                "notificacion_id": int(getattr(notif, "id", 0) or 0),
                "solicitud_id": int(getattr(notif, "solicitud_id", 0) or 0) or None,
                "tipo": str(getattr(notif, "tipo", "") or "")[:80],
            },
        )
        db.session.commit()
    if _notif_wants_json():
        unread_count = (
            ClienteNotificacion.query
            .filter_by(cliente_id=current_user.id, is_read=False, is_deleted=False)
            .count()
        )
        return jsonify({"ok": True, "marked_id": int(getattr(notif, "id", 0) or 0), "unread_count": int(unread_count or 0)})
    return redirect(url_for("clientes.notificaciones_list"))


@clientes_bp.route('/notificaciones/<int:notificacion_id>/eliminar', methods=['POST'])
@login_required
@cliente_required
def notificacion_eliminar(notificacion_id):
    notif = _get_cliente_notificacion_or_404(notificacion_id)
    notif.is_deleted = True
    notif.updated_at = utc_now_naive()
    _emit_cliente_outbox_event(
        event_type="CLIENTE_NOTIFICACION_DELETED",
        aggregate_type="ClienteNotificacion",
        aggregate_id=int(getattr(notif, "id", 0) or 0),
        payload={
            "cliente_id": int(getattr(current_user, "id", 0) or 0),
            "notificacion_id": int(getattr(notif, "id", 0) or 0),
            "solicitud_id": int(getattr(notif, "solicitud_id", 0) or 0) or None,
            "tipo": str(getattr(notif, "tipo", "") or "")[:80],
        },
    )
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
        try:
            invariant_transition_solicitud_candidata_status(
                solicitud_id=int(solicitud.id),
                sc_id=int(sc.id),
                to_status="vista",
                actor=str(int(getattr(current_user, "id", 0) or 0) or "cliente"),
            )
            db.session.commit()
        except InvariantConflictError:
            db.session.rollback()
        except Exception:
            db.session.rollback()
            sc.status = 'vista'
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

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
    if (sc.status or "") not in {"enviada", "vista"}:
        flash('Esta candidata ya fue procesada y no se puede solicitar entrevista nuevamente.', 'warning')
        return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))

    actor_user = str(int(getattr(current_user, "id", 0) or 0))
    blocked, _ = enforce_business_limit(
        cache_obj=cache,
        scope="cliente_candidata_select_hour",
        actor=actor_user,
        limit=20,
        window_seconds=3600,
        reason="candidate_selection_burst",
        summary="Bloqueo por ráfaga de selección de candidatas",
        metadata={"route": (request.path or ""), "solicitud_id": int(solicitud.id), "sc_id": int(sc.id)},
    )
    if blocked:
        flash('Demasiadas acciones seguidas sobre candidatas. Intenta nuevamente más tarde.', 'warning')
        return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))

    try:
        invariant_transition_solicitud_candidata_status(
            solicitud_id=int(solicitud.id),
            sc_id=int(sc.id),
            to_status="seleccionada",
            actor=actor_user,
        )
        db.session.commit()
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "No se pudo seleccionar esta candidata en este momento.", "warning")
        return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))
    except Exception:
        db.session.rollback()
        sc.status = 'seleccionada'
        snapshot = sc.breakdown_snapshot if isinstance(sc.breakdown_snapshot, dict) else {}
        snapshot["client_action"] = "solicitar_entrevista"
        snapshot["client_action_at"] = iso_utc_z()
        sc.breakdown_snapshot = snapshot
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('No se pudo seleccionar esta candidata en este momento.', 'warning')
            return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))

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
    actor_user = str(int(getattr(current_user, "id", 0) or 0))
    blocked, _ = enforce_business_limit(
        cache_obj=cache,
        scope="cliente_candidata_discard_hour",
        actor=actor_user,
        limit=25,
        window_seconds=3600,
        reason="candidate_discard_burst",
        summary="Bloqueo por ráfaga de descarte de candidatas",
        metadata={"route": (request.path or ""), "solicitud_id": int(solicitud.id), "sc_id": int(sc.id)},
    )
    if blocked:
        flash('Demasiadas acciones seguidas sobre candidatas. Intenta nuevamente más tarde.', 'warning')
        return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))

    reason = (request.form.get("client_reason") or "").strip()
    try:
        invariant_transition_solicitud_candidata_status(
            solicitud_id=int(solicitud.id),
            sc_id=int(sc.id),
            to_status="descartada",
            actor=actor_user,
            reason=reason,
        )
        db.session.commit()
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "No se pudo descartar la candidata en este momento.", "warning")
        return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))
    except Exception:
        db.session.rollback()
        sc.status = 'descartada'
        snapshot = sc.breakdown_snapshot if isinstance(sc.breakdown_snapshot, dict) else {}
        snapshot["client_action"] = "rechazada"
        snapshot["client_action_at"] = iso_utc_z()
        if reason:
            snapshot["client_reason"] = reason[:500]
        sc.breakdown_snapshot = snapshot
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('No se pudo descartar la candidata en este momento.', 'warning')
            return redirect(url_for('clientes.solicitud_candidatas', solicitud_id=solicitud.id))
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
    trust_signals = _build_solicitud_trust_signals(s)

    return render_template(
        'clientes/solicitud_seguimiento.html',
        s=s,
        timeline=timeline,
        trust_signals=trust_signals,
    )


# ─────────────────────────────────────────────────────────────
# Cancelar solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/cancelar', methods=['GET','POST'])
@login_required
@cliente_required
def cancelar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    if (s.estado or "") == "cancelada":
        flash('Esta solicitud ya está cancelada.', 'info')
        return redirect(url_for('clientes.detalle_solicitud', id=id))

    form = ClienteCancelForm()
    if form.validate_on_submit():
        prev_estado = str(getattr(s, "estado", "") or "").strip().lower()
        set_solicitud_estado(s, 'cancelada')
        s.fecha_cancelacion = utc_now_naive()
        s.motivo_cancelacion = form.motivo.data
        try:
            invariant_release_solicitud_candidatas_on_cancel(
                solicitud=s,
                actor=str(int(getattr(current_user, "id", 0) or 0) or "cliente"),
                motivo=(form.motivo.data or ""),
            )
        except Exception:
            db.session.rollback()
        _emit_cliente_outbox_event(
            event_type="CLIENTE_SOLICITUD_STATUS_CHANGED",
            aggregate_type="Solicitud",
            aggregate_id=int(getattr(s, "id", 0) or 0),
            aggregate_version=int(getattr(s, "row_version", 0) or 0) + 1,
            payload={
                "cliente_id": int(getattr(current_user, "id", 0) or 0),
                "solicitud_id": int(getattr(s, "id", 0) or 0),
                "from": prev_estado or None,
                "to": "cancelada",
                "motivo": str(form.motivo.data or "")[:255],
            },
        )
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
        current_user.fecha_acepto_politicas = utc_now_naive()
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
                s.compat_test_cliente_at = utc_now_naive()
            if hasattr(s, 'compat_test_cliente_version'):
                s.compat_test_cliente_version = COMPAT_TEST_VERSION
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = utc_now_naive()
            db.session.commit()
            return 'db_json'
        except Exception:
            db.session.rollback()

    if hasattr(s, 'compat_test_cliente'):
        try:
            s.compat_test_cliente = dumps(payload_dict, ensure_ascii=False)
            if hasattr(s, 'compat_test_cliente_at'):
                s.compat_test_cliente_at = utc_now_naive()
            if hasattr(s, 'compat_test_cliente_version'):
                s.compat_test_cliente_version = COMPAT_TEST_VERSION
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = utc_now_naive()
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
        "timestamp": iso_utc_z(),
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


@clientes_bp.route('/solicitudes/nueva-publica', methods=['GET', 'POST'])
def solicitud_publica_nueva():
    flash("Este formulario requiere un enlace seguro. Solicítalo a la agencia.", "warning")
    return render_template(
        'clientes/public_link_invalid.html',
        reason_key="invalid",
        status_code=404,
    ), 404


@clientes_bp.route('/solicitudes/nueva-publica/<token>', methods=['GET', 'POST'])
@clientes_bp.route('/n/<token>', methods=['GET', 'POST'], endpoint='solicitud_publica_nueva_short')
def solicitud_publica_nueva_token(token):
    share_url_override = (getattr(g, "public_share_url_override", "") or "").strip()
    share_code = (getattr(g, "public_share_code", "") or "").strip().upper()
    short_share_url = share_url_override or _public_new_short_external_url(token)
    if not _ensure_public_new_token_usage_table():
        flash("Este enlace no está disponible temporalmente. Solicita uno nuevo a la agencia.", "warning")
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key="invalid",
            status_code=503,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), 503

    token_hash_storage = _public_link_token_hash_storage(token)
    used_row = _public_new_link_usage_by_hash(token_hash_storage)
    if used_row is not None:
        success_state = session.get("public_new_solicitud_success") or {}
        show_success = (
            request.method == "GET"
            and (request.args.get("estado") or "").strip().lower() == "enviado"
            and bool(success_state)
            and hmac.compare_digest(str(success_state.get("token_hash") or ""), token_hash_storage)
        )
        if show_success:
            session.pop("public_new_solicitud_success", None)
            return render_template(
                'clientes/public_new_success.html',
                cliente_nombre=str(success_state.get("cliente_nombre") or ""),
                cliente_codigo=str(success_state.get("cliente_codigo") or ""),
                solicitud_codigo=str(success_state.get("solicitud_codigo") or ""),
                solicitud_id=int(success_state.get("solicitud_id") or 0) or None,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 200
        return render_template(
            'clientes/public_link_used.html',
            used_at=getattr(used_row, "used_at", None),
            solicitud=getattr(used_row, "solicitud", None),
            solicitud_id=getattr(used_row, "solicitud_id", None),
            status_code=410,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), 410

    token_ok, fail_reason, _token_meta = _resolve_public_new_link_token(token)
    if not token_ok:
        reason_key = "invalid"
        status_code = 404
        if fail_reason == "expired":
            reason_key = "expired"
            status_code = 410
        flash("Este enlace no es válido o expiró. Solicita uno nuevo a la agencia.", "warning")
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key=reason_key,
            status_code=status_code,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), status_code

    form = SolicitudClienteNuevoPublicaForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
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

    public_pisos_value = "2" if bool(getattr(form, "dos_pisos", type("x", (object,), {"data": False})).data) else "1"
    public_pasaje_mode = "aparte" if bool(getattr(form, "pasaje_aporte", type("x", (object,), {"data": False})).data) else "incluido"
    public_pasaje_otro = ""
    if request.method == "POST":
        pisos_post = (request.form.get("pisos_selector") or "").strip()
        if pisos_post in ("1", "2", "3+"):
            public_pisos_value = pisos_post
        pasaje_mode_post = (request.form.get("pasaje_mode") or "").strip().lower()
        if pasaje_mode_post in ("incluido", "aparte", "otro"):
            public_pasaje_mode = pasaje_mode_post
        public_pasaje_otro = (request.form.get("pasaje_otro_text") or "").strip()[:120]
        if hasattr(form, "dos_pisos"):
            form.dos_pisos.data = (public_pisos_value in ("2", "3+"))
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")

    if form.validate_on_submit():
        actor_ip = _client_ip_for_security_layer() or "0.0.0.0"
        blocked_ip_day, _ = enforce_business_limit(
            cache_obj=cache,
            scope="public_new_solicitud_ip_day",
            actor=actor_ip,
            limit=BUSINESS_MAX_PUBLIC_IP_DIA,
            window_seconds=86400,
            reason="public_ip_daily_limit",
            summary="Bloqueo por abuso de formulario público (cliente nuevo)",
            metadata={"route": (request.path or ""), "token_hash": token_hash_storage[:16]},
        )
        if blocked_ip_day:
            flash("Este formulario alcanzó su límite diario de envíos desde tu red. Intenta mañana.", "warning")
            return render_template(
                'clientes/solicitud_form_publica_nueva.html',
                form=form,
                nuevo=True,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                service_section_title="Seccion 2 - Datos de la solicitud",
                service_section_desc="Completa los detalles del servicio y la ubicacion especifica donde se trabajara.",
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429
        blocked_token_burst, _ = enforce_business_limit(
            cache_obj=cache,
            scope="public_new_solicitud_token_10m",
            actor=token_hash_storage,
            limit=5,
            window_seconds=600,
            reason="public_token_burst_limit",
            summary="Bloqueo por reintentos masivos en token público nuevo",
            metadata={"route": (request.path or ""), "ip": actor_ip},
        )
        if blocked_token_burst:
            flash("Detectamos demasiados reintentos con este enlace. Solicita un nuevo enlace.", "warning")
            return render_template(
                'clientes/public_link_invalid.html',
                reason_key="invalid",
                status_code=429,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429
        blocked_fast, _ = enforce_min_human_interval(
            cache_obj=cache,
            scope="public_new_solicitud_submit_interval",
            actor=actor_ip,
            min_seconds=2,
            reason="timing_too_fast",
            summary="Patrón no humano en formulario público (cliente nuevo)",
            metadata={"route": (request.path or ""), "token_hash": token_hash_storage[:16]},
        )
        if blocked_fast:
            flash("Espera un momento antes de reenviar el formulario.", "warning")
            return render_template(
                'clientes/solicitud_form_publica_nueva.html',
                form=form,
                nuevo=True,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                service_section_title="Seccion 2 - Datos de la solicitud",
                service_section_desc="Completa los detalles del servicio y la ubicacion especifica donde se trabajara.",
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429

        email_norm = (form.email_contacto.data or '').strip().lower()
        tel_raw = (form.telefono_contacto.data or '').strip()
        dup_row, dup_field = _find_cliente_contact_duplicate(email_norm, tel_raw)
        if dup_row is not None:
            if dup_field == "email":
                form.email_contacto.errors.append("Este correo ya está registrado.")
            if dup_field == "telefono":
                form.telefono_contacto.errors.append("Este teléfono ya está registrado.")
        else:
            now_ref = utc_now_naive()
            state = {
                "cliente_id": 0,
                "cliente_codigo": "",
                "cliente_nombre": "",
                "solicitud_id": 0,
                "solicitud_codigo": "",
            }

            def _persist_public_new(_attempt: int) -> None:
                codigo_cliente = _next_cliente_codigo_publico()
                state["cliente_codigo"] = codigo_cliente

                c = Cliente(
                    codigo=codigo_cliente,
                    nombre_completo=(form.nombre_completo.data or '').strip(),
                    email=email_norm,
                    telefono=tel_raw,
                    ciudad=(form.ciudad_cliente.data or '').strip(),
                    sector=(form.sector_cliente.data or '').strip(),
                    role='cliente',
                    is_active=True,
                    fecha_registro=now_ref,
                    created_at=now_ref,
                    updated_at=now_ref,
                    fecha_ultima_actividad=now_ref,
                    total_solicitudes=0,
                )
                db.session.add(c)
                db.session.flush()
                state["cliente_id"] = int(getattr(c, "id", 0) or 0)
                state["cliente_nombre"] = str(getattr(c, "nombre_completo", "") or "")

                idx = Solicitud.query.filter_by(cliente_id=c.id).count()
                while True:
                    codigo_solicitud = compose_codigo_solicitud(str(c.codigo or ""), idx)
                    existe = Solicitud.query.filter_by(codigo_solicitud=codigo_solicitud).first()
                    if not existe:
                        break
                    idx += 1
                state["solicitud_codigo"] = codigo_solicitud

                s = Solicitud(
                    cliente_id=c.id,
                    fecha_solicitud=now_ref,
                    codigo_solicitud=codigo_solicitud
                )
                form.populate_obj(s)
                _normalize_modalidad_on_solicitud(s)

                selected_funciones = _clean_list(getattr(form, 'funciones', type('x', (object,), {'data': []})).data)
                funciones_otro_raw = getattr(getattr(form, 'funciones_otro', None), 'data', '') if hasattr(form, 'funciones_otro') else ''
                if (funciones_otro_raw or '').strip() and 'otro' not in selected_funciones:
                    selected_funciones.append('otro')
                funciones_otro_clean = (funciones_otro_raw or '').strip() if 'otro' in selected_funciones else ''

                s.funciones = _map_funciones(selected_funciones, funciones_otro_clean)
                if hasattr(s, 'funciones_otro'):
                    s.funciones_otro = funciones_otro_clean or None

                areas_selected_raw = _clean_list(
                    getattr(form, 'areas_comunes', type('x', (object,), {'data': []})).data
                )
                area_otro_txt = (getattr(getattr(form, 'area_otro', None), 'data', '') or '').strip() if hasattr(form, 'area_otro') else ''
                areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
                s.areas_comunes = _normalize_areas_comunes_selected(
                    areas_selected_raw,
                    getattr(getattr(form, 'areas_comunes', None), 'choices', []) if hasattr(form, 'areas_comunes') else [],
                ) or []

                edad_codes_selected = _clean_list(
                    getattr(form, 'edad_requerida', type('x', (object,), {'data': []})).data
                )
                edad_otro_txt = (getattr(getattr(form, 'edad_otro', None), 'data', '') or '').strip() if hasattr(form, 'edad_otro') else ''
                if edad_otro_txt and 'otro' not in edad_codes_selected:
                    edad_codes_selected.append('otro')
                s.edad_requerida = _map_edad_choices(
                    edad_codes_selected,
                    getattr(getattr(form, 'edad_requerida', None), 'choices', []) if hasattr(form, 'edad_requerida') else [],
                    edad_otro_txt,
                ) or []

                tipo_lugar_value = getattr(s, 'tipo_lugar', '')
                tipo_lugar_otro_txt = (getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
                if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                    tipo_lugar_value = 'otro'
                s.tipo_lugar = _map_tipo_lugar(
                    tipo_lugar_value,
                    tipo_lugar_otro_txt,
                )

                if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                    s.mascota = (form.mascota.data or '').strip() or None

                if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                    area_otro_txt = (form.area_otro.data or '').strip()
                    s.area_otro = (area_otro_txt if areas_has_otro else '') or None

                if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                    s.nota_cliente = strip_pasaje_marker_from_note((form.nota_cliente.data or '').strip())
                    if public_pisos_value == "3+":
                        marker_pisos = "Pisos reportados: 3+."
                        if marker_pisos not in (s.nota_cliente or ""):
                            s.nota_cliente = (s.nota_cliente + ("\n" if s.nota_cliente else "") + marker_pisos).strip()
                apply_pasaje_to_solicitud(
                    s,
                    mode_raw=public_pasaje_mode,
                    text_raw=public_pasaje_otro,
                    default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
                )

                if hasattr(s, 'sueldo'):
                    s.sueldo = _money_sanitize(getattr(form, 'sueldo', type('x', (object,), {'data': None})).data)

                s.ciudad_sector = (form.ciudad_sector.data or '').strip()
                if hasattr(s, 'fecha_ultima_modificacion'):
                    s.fecha_ultima_modificacion = now_ref

                db.session.add(s)
                db.session.flush()
                state["solicitud_id"] = int(getattr(s, "id", 0) or 0)

                existing_usage = _public_new_link_usage_by_hash(token_hash_storage)
                if existing_usage is not None:
                    raise RuntimeError("token_already_used")
                db.session.add(
                    PublicSolicitudClienteNuevoTokenUso(
                        token_hash=token_hash_storage,
                        cliente_id=int(getattr(c, "id", 0) or 0) or None,
                        solicitud_id=int(getattr(s, "id", 0) or 0) or None,
                        used_at=now_ref,
                    )
                )

                c.total_solicitudes = (c.total_solicitudes or 0) + 1
                c.fecha_ultima_solicitud = now_ref
                c.fecha_ultima_actividad = now_ref

            def _verify_public_new() -> bool:
                cliente_id = int(state.get("cliente_id") or 0)
                solicitud_id = int(state.get("solicitud_id") or 0)
                if cliente_id <= 0 or solicitud_id <= 0:
                    return False
                c_row = Cliente.query.filter_by(id=cliente_id).first()
                if not c_row:
                    return False
                if str(getattr(c_row, "codigo", "") or "") != str(state.get("cliente_codigo") or ""):
                    return False
                s_row = Solicitud.query.filter_by(id=solicitud_id).first()
                if not s_row:
                    return False
                if int(getattr(s_row, "cliente_id", 0) or 0) != cliente_id:
                    return False
                if str(getattr(s_row, "codigo_solicitud", "") or "") != str(state.get("solicitud_codigo") or ""):
                    return False
                usage = _public_new_link_usage_by_hash(token_hash_storage)
                if usage is None:
                    return False
                return int(getattr(usage, "solicitud_id", 0) or 0) == int(getattr(s_row, "id", 0) or 0)

            result = execute_robust_save(
                session=db.session,
                persist_fn=_persist_public_new,
                verify_fn=_verify_public_new,
                max_retries=3,
                retryable_exceptions=(OperationalError, IntegrityError, SQLAlchemyError),
            )

            if result.ok:
                session["public_new_solicitud_success"] = {
                    "token_hash": token_hash_storage,
                    "cliente_nombre": str(state.get("cliente_nombre") or ""),
                    "cliente_codigo": str(state.get("cliente_codigo") or ""),
                    "solicitud_codigo": str(state.get("solicitud_codigo") or ""),
                    "solicitud_id": int(state.get("solicitud_id") or 0),
                }
                if share_code:
                    return redirect(url_for("public.solicitud_share_continue", code=share_code, estado="enviado"))
                if request.endpoint == "clientes.solicitud_publica_nueva_short":
                    return redirect(url_for('clientes.solicitud_publica_nueva_short', token=token, estado="enviado"))
                return redirect(url_for('clientes.solicitud_publica_nueva_token', token=token, estado="enviado"))

            usage_after_fail = _public_new_link_usage_by_hash(token_hash_storage)
            if usage_after_fail is not None:
                return render_template(
                    'clientes/public_link_used.html',
                    used_at=getattr(usage_after_fail, "used_at", None),
                    solicitud=getattr(usage_after_fail, "solicitud", None),
                    solicitud_id=getattr(usage_after_fail, "solicitud_id", None),
                    status_code=410,
                    og_url=short_share_url,
                    canonical_url=short_share_url,
                ), 410

            err = (result.error_message or '').lower()
            if "email" in err:
                form.email_contacto.errors.append("Este correo ya está registrado.")
            elif "telefono" in err:
                form.telefono_contacto.errors.append("Este teléfono ya está registrado.")
            elif "codigo" in err:
                flash("No se pudo completar por una colisión de código. Intenta de nuevo.", "warning")
            else:
                flash("No se pudo enviar la solicitud en este momento. Inténtalo de nuevo.", "danger")

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'clientes/solicitud_form_publica_nueva.html',
        form=form,
        nuevo=True,
        public_pisos_value=public_pisos_value,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        service_section_title="Seccion 2 - Datos de la solicitud",
        service_section_desc="Completa los detalles del servicio y la ubicacion especifica donde se trabajara.",
        og_url=short_share_url,
        canonical_url=short_share_url,
    )


@clientes_bp.route('/solicitudes/publica/<token>', methods=['GET', 'POST'])
@clientes_bp.route('/f/<token>', methods=['GET', 'POST'], endpoint='solicitud_publica_short')
def solicitud_publica(token):
    share_url_override = (getattr(g, "public_share_url_override", "") or "").strip()
    share_code = (getattr(g, "public_share_code", "") or "").strip().upper()
    short_share_url = share_url_override or _public_existing_short_external_url(token)
    if not _ensure_public_token_usage_table():
        flash("Este enlace no está disponible temporalmente. Solicita uno nuevo a la agencia.", "warning")
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key="invalid",
            status_code=503,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), 503

    token_hash_storage = _public_link_token_hash_storage(token)
    used_row = _public_link_usage_by_hash(token_hash_storage)
    if used_row is not None:
        success_state = session.get("public_solicitud_success") or {}
        show_success = (
            request.method == "GET"
            and (request.args.get("estado") or "").strip().lower() == "enviado"
            and bool(success_state)
            and hmac.compare_digest(str(success_state.get("token_hash") or ""), token_hash_storage)
        )
        if show_success:
            session.pop("public_solicitud_success", None)
            return render_template(
                'clientes/public_link_success.html',
                cliente_nombre=str(success_state.get("cliente_nombre") or ""),
                solicitud_codigo=str(success_state.get("solicitud_codigo") or ""),
                used_at=getattr(used_row, "used_at", None),
                solicitud=getattr(used_row, "solicitud", None),
                solicitud_id=getattr(used_row, "solicitud_id", None),
                status_code=200,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 200
        _log_public_link_event(
            "PUBLIC_LINK_VIEW_FAIL",
            token,
            success=False,
            reason="token_already_used",
            cliente_id=int(getattr(used_row, "cliente_id", 0) or 0) or None,
            metadata_extra={"method": request.method, "status_code": 410},
        )
        return render_template(
            'clientes/public_link_used.html',
            used_at=getattr(used_row, "used_at", None),
            solicitud=getattr(used_row, "solicitud", None),
            solicitud_id=getattr(used_row, "solicitud_id", None),
            status_code=410,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), 410

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

    cliente, fail_reason, token_meta = _resolve_public_link_token(token)
    if not cliente:
        reason_key = "invalid"
        status_code = 404
        user_message = "Este enlace no es válido."
        if fail_reason == "expired":
            reason_key = "expired"
            status_code = 410
            user_message = "Este enlace ha expirado."
        _log_public_link_event(
            "PUBLIC_LINK_VIEW_FAIL",
            token,
            success=False,
            reason=fail_reason or "invalid_token",
            metadata_extra={"method": request.method, "status_code": status_code},
        )
        flash(f"{user_message} Solicita uno nuevo a la agencia.", "warning")
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key=reason_key,
            status_code=status_code,
            og_url=short_share_url,
            canonical_url=short_share_url,
        ), status_code

    c = cliente
    public_pisos_value = "2" if bool(getattr(form, "dos_pisos", type("x",(object,),{"data":False})).data) else "1"
    public_pasaje_mode = "aparte" if bool(getattr(form, "pasaje_aporte", type("x",(object,),{"data":False})).data) else "incluido"
    public_pasaje_otro = ""
    if request.method == "POST":
        pisos_post = (request.form.get("pisos_selector") or "").strip()
        if pisos_post in ("1", "2", "3+"):
            public_pisos_value = pisos_post
        pasaje_mode_post = (request.form.get("pasaje_mode") or "").strip().lower()
        if pasaje_mode_post in ("incluido", "aparte", "otro"):
            public_pasaje_mode = pasaje_mode_post
        public_pasaje_otro = (request.form.get("pasaje_otro_text") or "").strip()[:120]

    _log_public_link_event(
        "PUBLIC_LINK_VIEW_OK",
        token,
        success=True,
        cliente_id=int(c.id),
        metadata_extra={
            "method": request.method,
            "legacy_token": bool(token_meta.get("legacy_token")),
        },
    )

    if hasattr(form, "email_cliente"):
        # Mantiene compatibilidad del form sin exponer email en UI pública.
        form.email_cliente.data = c.email or ''

    if form.validate_on_submit():
        actor_ip = _client_ip_for_security_layer() or "0.0.0.0"
        blocked_ip_day, _ = enforce_business_limit(
            cache_obj=cache,
            scope="public_existing_solicitud_ip_day",
            actor=actor_ip,
            limit=BUSINESS_MAX_PUBLIC_IP_DIA,
            window_seconds=86400,
            reason="public_ip_daily_limit",
            summary="Bloqueo por abuso de formulario público (cliente existente)",
            metadata={"route": (request.path or ""), "token_hash": token_hash_storage[:16]},
        )
        if blocked_ip_day:
            flash("Este formulario alcanzó su límite diario de envíos desde tu red. Intenta mañana.", "warning")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429
        blocked_token_burst, _ = enforce_business_limit(
            cache_obj=cache,
            scope="public_existing_solicitud_token_10m",
            actor=token_hash_storage,
            limit=5,
            window_seconds=600,
            reason="public_token_burst_limit",
            summary="Bloqueo por reintentos masivos en token público existente",
            metadata={"route": (request.path or ""), "ip": actor_ip, "cliente_id": int(c.id)},
        )
        if blocked_token_burst:
            flash("Detectamos demasiados reintentos con este enlace. Solicita un nuevo enlace.", "warning")
            return render_template(
                'clientes/public_link_invalid.html',
                reason_key="invalid",
                status_code=429,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429
        blocked_fast, _ = enforce_min_human_interval(
            cache_obj=cache,
            scope="public_existing_solicitud_submit_interval",
            actor=actor_ip,
            min_seconds=2,
            reason="timing_too_fast",
            summary="Patrón no humano en formulario público (cliente existente)",
            metadata={"route": (request.path or ""), "token_hash": token_hash_storage[:16], "cliente_id": int(c.id)},
        )
        if blocked_fast:
            flash("Espera un momento antes de reenviar el formulario.", "warning")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429

        blocked_cliente_day, _ = enforce_business_limit(
            cache_obj=cache,
            scope="cliente_public_solicitud_create_day",
            actor=str(int(c.id)),
            limit=BUSINESS_MAX_CLIENTE_CREACIONES_DIA,
            window_seconds=86400,
            reason="cliente_public_daily_limit",
            summary="Bloqueo por creación diaria de solicitudes vía enlace público",
            metadata={"route": (request.path or ""), "cliente_id": int(c.id), "ip": actor_ip},
        )
        if blocked_cliente_day:
            flash("Este cliente alcanzó su límite diario de nuevas solicitudes.", "warning")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429

        active_count = _cliente_active_solicitudes_count(int(c.id))
        if active_count >= BUSINESS_MAX_CLIENTE_ACTIVAS:
            log_action(
                action_type="BUSINESS_FLOW_BLOCKED",
                entity_type="cliente",
                entity_id=int(c.id),
                summary="Límite de solicitudes activas alcanzado vía enlace público",
                metadata={
                    "rule": "max_active_solicitudes",
                    "active_count": int(active_count),
                    "max_allowed": int(BUSINESS_MAX_CLIENTE_ACTIVAS),
                    "route": (request.path or ""),
                },
                success=False,
                error="max_active_solicitudes_reached",
            )
            flash("Este cliente ya tiene demasiadas solicitudes activas. Contacta a la agencia.", "warning")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 429

        pisos_selector = (request.form.get("pisos_selector") or "").strip()
        if hasattr(form, "dos_pisos"):
            form.dos_pisos.data = (pisos_selector in ("2", "3+"))

        pasaje_mode = (request.form.get("pasaje_mode") or "").strip().lower()
        pasaje_otro_text = (request.form.get("pasaje_otro_text") or "").strip()[:120]
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (pasaje_mode == "aparte")

        if hasattr(form, 'token'):
            if (form.token.data or '') != token:
                _log_public_link_event(
                    "PUBLIC_LINK_VIEW_FAIL",
                    token,
                    success=False,
                    reason="form_token_mismatch",
                    cliente_id=int(c.id),
                    metadata_extra={"method": request.method, "status_code": 400},
                )
                flash("Token inválido.", "danger")
                return render_template(
                    'clientes/public_link_invalid.html',
                    reason_key="invalid",
                    status_code=400,
                    og_url=short_share_url,
                    canonical_url=short_share_url,
                ), 400

        if _norm_text(getattr(form, 'codigo_cliente', type('x',(object,),{'data':''})) .data) != _norm_text(c.codigo):
            _log_public_link_event(
                "PUBLIC_LINK_VIEW_FAIL",
                token,
                success=False,
                reason="codigo_no_match",
                cliente_id=int(c.id),
                metadata_extra={"method": request.method, "status_code": 403},
            )
            flash("El código no coincide con este enlace.", "danger")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 403

        if _norm_text(getattr(form, 'nombre_cliente', type('x',(object,),{'data':''})).data) != _norm_text(c.nombre_completo):
            _log_public_link_event(
                "PUBLIC_LINK_VIEW_FAIL",
                token,
                success=False,
                reason="nombre_no_match",
                cliente_id=int(c.id),
                metadata_extra={"method": request.method, "status_code": 403},
            )
            flash("El nombre no coincide con ese código.", "danger")
            return render_template(
                'clientes/solicitud_form_publica.html',
                form=form,
                nuevo=True,
                cliente=c,
                public_pisos_value=public_pisos_value,
                public_pasaje_mode=public_pasaje_mode,
                public_pasaje_otro=public_pasaje_otro,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 403

        codigo_holder: dict[str, str] = {"value": ""}
        solicitud_id_holder: dict[str, int] = {"value": 0}
        now_ref = utc_now_naive()

        def _persist_public_solicitud(_attempt: int) -> None:
            idx = Solicitud.query.filter_by(cliente_id=c.id).count()
            while True:
                codigo = compose_codigo_solicitud(str(c.codigo or ""), idx)
                existe = Solicitud.query.filter_by(codigo_solicitud=codigo).first()
                if not existe:
                    break
                idx += 1
            codigo_holder["value"] = codigo

            s = Solicitud(
                cliente_id=c.id,
                fecha_solicitud=now_ref,
                codigo_solicitud=codigo
            )

            form.populate_obj(s)
            _normalize_modalidad_on_solicitud(s)

            selected_funciones = _clean_list(getattr(form, 'funciones', type('x',(object,),{'data':[]})).data)
            funciones_otro_raw = getattr(getattr(form, 'funciones_otro', None), 'data', '') if hasattr(form, 'funciones_otro') else ''
            if (funciones_otro_raw or '').strip() and 'otro' not in selected_funciones:
                selected_funciones.append('otro')
            funciones_otro_clean = (funciones_otro_raw or '').strip() if 'otro' in selected_funciones else ''

            s.funciones = _map_funciones(selected_funciones, funciones_otro_clean)
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = funciones_otro_clean or None

            areas_selected_raw = _clean_list(
                getattr(form, 'areas_comunes', type('x',(object,),{'data':[]})).data
            )
            area_otro_txt = (getattr(getattr(form, 'area_otro', None), 'data', '') or '').strip() if hasattr(form, 'area_otro') else ''
            areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
            s.areas_comunes = _normalize_areas_comunes_selected(
                areas_selected_raw,
                getattr(getattr(form, 'areas_comunes', None), 'choices', []) if hasattr(form, 'areas_comunes') else [],
            ) or []

            edad_codes_selected = _clean_list(
                getattr(form, 'edad_requerida', type('x',(object,),{'data':[]})).data
            )
            edad_otro_txt = (getattr(getattr(form, 'edad_otro', None), 'data', '') or '').strip() if hasattr(form, 'edad_otro') else ''
            if edad_otro_txt and 'otro' not in edad_codes_selected:
                edad_codes_selected.append('otro')
            s.edad_requerida = _map_edad_choices(
                edad_codes_selected,
                getattr(getattr(form, 'edad_requerida', None), 'choices', []) if hasattr(form, 'edad_requerida') else [],
                edad_otro_txt,
            ) or []

            tipo_lugar_value = getattr(s, 'tipo_lugar', '')
            tipo_lugar_otro_txt = (getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
            if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                tipo_lugar_value = 'otro'
            s.tipo_lugar = _map_tipo_lugar(
                tipo_lugar_value,
                tipo_lugar_otro_txt,
            )

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                area_otro_txt = (form.area_otro.data or '').strip()
                s.area_otro = (area_otro_txt if areas_has_otro else '') or None

            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = strip_pasaje_marker_from_note((form.nota_cliente.data or '').strip())
                if pisos_selector == "3+":
                    marker_pisos = "Pisos reportados: 3+."
                    if marker_pisos not in (s.nota_cliente or ""):
                        s.nota_cliente = (s.nota_cliente + ("\n" if s.nota_cliente else "") + marker_pisos).strip()
            apply_pasaje_to_solicitud(
                s,
                mode_raw=pasaje_mode,
                text_raw=pasaje_otro_text,
                default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
            )

            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(getattr(form, 'sueldo', type('x',(object,),{'data':None})).data)

            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = now_ref

            db.session.add(s)
            db.session.flush()
            solicitud_id_holder["value"] = int(getattr(s, "id", 0) or 0)

            existing_usage = _public_link_usage_by_hash(token_hash_storage)
            if existing_usage is not None:
                raise RuntimeError("token_already_used")
            db.session.add(
                PublicSolicitudTokenUso(
                    token_hash=token_hash_storage,
                    cliente_id=int(c.id),
                    solicitud_id=int(getattr(s, "id", 0) or 0) or None,
                    used_at=now_ref,
                )
            )

            c.total_solicitudes = (c.total_solicitudes or 0) + 1
            c.fecha_ultima_solicitud = now_ref
            c.fecha_ultima_actividad = now_ref

        def _verify_public_solicitud() -> bool:
            codigo = codigo_holder.get("value") or ""
            if not codigo:
                return False
            row = Solicitud.query.filter_by(codigo_solicitud=codigo, cliente_id=c.id).first()
            if not row:
                return False
            usage = _public_link_usage_by_hash(token_hash_storage)
            if usage is None:
                return False
            if int(getattr(usage, "cliente_id", 0) or 0) != int(c.id):
                return False
            if int(getattr(usage, "solicitud_id", 0) or 0) != int(getattr(row, "id", 0) or 0):
                return False
            return bool(getattr(c, "fecha_ultima_solicitud", None))

        result = execute_robust_save(
            session=db.session,
            persist_fn=_persist_public_solicitud,
            verify_fn=_verify_public_solicitud,
            max_retries=2,
            retryable_exceptions=(OperationalError, SQLAlchemyError),
        )

        if result.ok:
            _log_public_link_event(
                "PUBLIC_LINK_VIEW_OK",
                token,
                success=True,
                cliente_id=int(c.id),
                metadata_extra={
                    "method": request.method,
                    "action": "create_solicitud",
                    "solicitud_codigo": str(codigo_holder.get("value") or ""),
                    "attempts": int(result.attempts),
                },
            )
            flash(f"Solicitud {codigo_holder.get('value') or ''} enviada correctamente.", "success")
            session["public_solicitud_success"] = {
                "token_hash": token_hash_storage,
                "cliente_nombre": str(getattr(c, "nombre_completo", "") or ""),
                "solicitud_codigo": str(codigo_holder.get("value") or ""),
                "solicitud_id": int(solicitud_id_holder.get("value") or 0),
            }
            if share_code:
                return redirect(url_for("public.solicitud_share_continue", code=share_code, estado="enviado"))
            if request.endpoint == "clientes.solicitud_publica_short":
                return redirect(url_for('clientes.solicitud_publica_short', token=token, estado="enviado"))
            return redirect(url_for('clientes.solicitud_publica', token=token, estado="enviado"))
        usage_after_fail = _public_link_usage_by_hash(token_hash_storage)
        if usage_after_fail is not None:
            return render_template(
                'clientes/public_link_used.html',
                used_at=getattr(usage_after_fail, "used_at", None),
                solicitud=getattr(usage_after_fail, "solicitud", None),
                solicitud_id=getattr(usage_after_fail, "solicitud_id", None),
                status_code=410,
                og_url=short_share_url,
                canonical_url=short_share_url,
            ), 410
        _log_public_link_event(
            "PUBLIC_LINK_VIEW_FAIL",
            token,
            success=False,
            reason=result.error_message or "save_failed",
            cliente_id=int(c.id),
            metadata_extra={"method": request.method, "action": "create_solicitud", "attempts": int(result.attempts)},
        )
        current_app.logger.warning(
            "PUBLIC_LINK_SAVE_FAIL cliente_id=%s token_hash=%s attempts=%s error=%s",
            int(c.id),
            _public_link_token_hash(token),
            int(result.attempts),
            (result.error_message or "")[:300],
        )
        flash("No se pudo enviar la solicitud en este momento. Inténtalo de nuevo.", "danger")

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'clientes/solicitud_form_publica.html',
        form=form,
        nuevo=True,
        cliente=c,
        public_pisos_value=public_pisos_value,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        og_url=short_share_url,
        canonical_url=short_share_url,
    )


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
