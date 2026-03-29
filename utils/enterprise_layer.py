# -*- coding: utf-8 -*-
from __future__ import annotations

import traceback
import uuid
import os
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from flask import request
from sqlalchemy import func, case, text, or_

from config_app import db
from models import (
    StaffAuditLog,
    StaffUser,
    Solicitud,
    SolicitudCandidata,
    DomainOutbox,
    RequestIdempotencyKey,
    OperationalMetricSnapshot,
)
from utils.audit_logger import log_action
from utils.distributed_backplane import (
    BackplaneUnavailable,
    bp_add,
    bp_delete,
    bp_get,
    bp_healthcheck,
    bp_set,
)
from utils.matching_service import rank_candidates
from utils.secrets_manager import get_secret
from utils.ssrf_guard import OutboundURLBlocked, validate_external_url, build_no_redirect_opener
from utils.timezone import iso_utc_z, parse_iso_utc, utc_now_naive

LOCK_TTL_SECONDS = 120
LOCK_INDEX_KEY = "enterprise:locks:index"
LOCK_KEY_PREFIX = "enterprise:lock"

SESSION_TTL_SECONDS = 60 * 60 * 12
SESSION_INDEX_KEY = "enterprise:sessions:index"
SESSION_KEY_PREFIX = "enterprise:session"
SESSION_REV_KEY_PREFIX = "enterprise:session_rev"

ANOMALY_WINDOW_SEC = 60
ALERT_DEDUPE_DEFAULT_SEC = 180
TELEGRAM_CFG_CACHE_KEY = "enterprise:telegram:cfg"
O1_WINDOW_MINUTES_DEFAULT = 15
O1_COUNTER_TTL_SECONDS = 20 * 60
O2_SNAPSHOT_INTERVAL_MINUTES_DEFAULT = 5
O2_SNAPSHOT_RETENTION_HOURS_DEFAULT = 24 * 7
_LIVE_REGION_SANITIZE_RE = re.compile(r"[^a-z0-9:_-]+")


def _utcnow() -> datetime:
    return utc_now_naive()


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return iso_utc_z(dt)


def _parse_dt(value: str | None) -> datetime | None:
    dt = parse_iso_utc(value)
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _client_ip() -> str | None:
    try:
        return (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or request.remote_addr
            or None
        )
    except Exception:
        return None


def _user_agent() -> str | None:
    try:
        return (request.headers.get("User-Agent") or "")[:512] or None
    except Exception:
        return None


def _safe_cache_get(key: str, default=None):
    return bp_get(key, default=default, context="enterprise_safe_get")


def _safe_cache_set(key: str, value, timeout: int):
    return bp_set(
        key,
        value,
        timeout=timeout,
        context="enterprise_safe_set",
    )


def _safe_cache_delete(key: str):
    bp_delete(key, context="enterprise_safe_delete")


def _safe_cache_add(key: str, value, timeout: int) -> bool:
    try:
        return bp_add(
            key,
            value,
            timeout=timeout,
            context="enterprise_safe_add",
        )
    except Exception:
        # Para flujos best-effort (alert dedupe), preferimos no bloquear.
        return True


def _coord_get(key: str, default=None):
    return bp_get(key, default=default, strict=True, context="enterprise_coord_get")


def _coord_set(key: str, value, timeout: int) -> bool:
    return bp_set(
        key,
        value,
        timeout=timeout,
        strict=True,
        context="enterprise_coord_set",
    )


def _coord_add(key: str, value, timeout: int) -> bool:
    return bp_add(
        key,
        value,
        timeout=timeout,
        strict=True,
        context="enterprise_coord_add",
    )


def _append_index(index_key: str, item: str, timeout: int = 3600):
    idx = _coord_get(index_key, default=[]) or []
    idx = [x for x in idx if isinstance(x, str) and x]
    if item not in idx:
        idx.append(item)
    if len(idx) > 2000:
        idx = idx[-2000:]
    _coord_set(index_key, idx, timeout=timeout)


def telegram_channel_config() -> dict[str, Any]:
    cached = _safe_cache_get(TELEGRAM_CFG_CACHE_KEY, default={}) or {}
    token = str(cached.get("token") or get_secret("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(cached.get("chat_id") or get_secret("TELEGRAM_CHAT_ID") or "").strip()
    enabled_raw = cached.get("enabled")
    if enabled_raw is None:
        enabled_raw = os.getenv("TELEGRAM_ALERTS_ENABLED") or "1"
    enabled = str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "enabled": bool(enabled),
        "token": token,
        "chat_id": chat_id,
        "masked_token": (f"{token[:8]}...{token[-4:]}" if len(token) > 14 else ("***" if token else "")),
        "has_config": bool(token and chat_id),
    }


def save_telegram_channel_config(*, token: str, chat_id: str, enabled: bool, actor_username: str | None = None) -> None:
    payload = {
        "token": (token or "").strip(),
        "chat_id": (chat_id or "").strip(),
        "enabled": bool(enabled),
    }
    _safe_cache_set(TELEGRAM_CFG_CACHE_KEY, payload, timeout=60 * 60 * 24 * 7)
    log_action(
        action_type="ALERT_CHANNEL_CONFIG",
        entity_type="system",
        entity_id="telegram",
        summary="Canal Telegram actualizado",
        metadata={
            "channel": "telegram",
            "enabled": bool(enabled),
            "actor_username": actor_username,
            "chat_id_tail": (payload["chat_id"][-6:] if payload["chat_id"] else None),
        },
        success=True,
    )


def clear_telegram_channel_runtime_cache() -> None:
    """Forces next read to use current env/secrets manager values."""
    _safe_cache_delete(TELEGRAM_CFG_CACHE_KEY)


def _send_telegram_message(text: str) -> tuple[bool, str]:
    cfg = telegram_channel_config()
    if not cfg.get("enabled"):
        return False, "Canal desactivado"
    token = str(cfg.get("token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()
    if not token or not chat_id:
        return False, "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID"

    try:
        url = validate_external_url(
            f"https://api.telegram.org/bot{token}/sendMessage",
            allowed_hosts={"api.telegram.org"},
        )
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": (text or "")[:3900],
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        opener = build_no_redirect_opener()
        with opener.open(req, timeout=6) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            if 200 <= code < 300:
                return True, "ok"
        return False, "Respuesta no exitosa de Telegram"
    except OutboundURLBlocked:
        return False, "Destino externo bloqueado por política SSRF."
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if 300 <= code < 400:
            return False, "Redirección externa bloqueada por política SSRF."
        return False, "Proveedor externo respondió con error."
    except Exception:
        return False, "No se pudo contactar proveedor externo."


def send_telegram_test_message(*, actor_username: str | None = None) -> tuple[bool, str]:
    text = (
        "Prueba de alertas Telegram\n"
        f"Hora UTC: {_dt_iso(_utcnow())}\n"
        f"Actor: {actor_username or '-'}"
    )
    ok, detail = _send_telegram_message(text)
    log_action(
        action_type="ALERT_TEST",
        entity_type="system",
        entity_id="telegram",
        summary="Prueba de envio Telegram",
        metadata={"channel": "telegram", "sent_to": ("telegram" if ok else "none"), "detail": detail[:300]},
        success=bool(ok),
        error=None if ok else detail[:3000],
    )
    return ok, detail


def _emit_operational_alert(
    *,
    level: str,
    rule: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
    dedupe_seconds: int = ALERT_DEDUPE_DEFAULT_SEC,
    telegram: bool = True,
) -> bool:
    lvl = (level or "").strip().lower()
    action = "ALERT_CRITICAL" if lvl == "critical" else "ALERT_WARNING"
    et = (entity_type or "system").strip().lower()[:80]
    eid = str(entity_id or "global").strip()[:64]
    dedupe_key = f"enterprise:alert:{action}:{rule}:{et}:{eid}"
    if not _safe_cache_add(dedupe_key, 1, timeout=max(30, int(dedupe_seconds))):
        return False

    meta = dict(metadata or {})
    meta.update({"rule": rule, "level": action, "entity_type": et, "entity_id": eid})
    sent_to = []
    if telegram:
        tg_ok, tg_detail = _send_telegram_message(f"[{action}] {summary}")
        if tg_ok:
            sent_to.append("telegram")
        else:
            meta["telegram_error"] = (tg_detail or "")[:280]
    meta["sent_to"] = ",".join(sent_to) if sent_to else "none"

    log_action(
        action_type=action,
        entity_type=et,
        entity_id=eid,
        summary=(summary or "")[:255],
        metadata=meta,
        success=True,
    )
    return True


def emit_critical_alert(
    *,
    rule: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
    dedupe_seconds: int = ALERT_DEDUPE_DEFAULT_SEC,
    telegram: bool = True,
) -> bool:
    return _emit_operational_alert(
        level="critical",
        rule=rule,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        dedupe_seconds=dedupe_seconds,
        telegram=telegram,
    )


def emit_warning_alert(
    *,
    rule: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
    dedupe_seconds: int = ALERT_DEDUPE_DEFAULT_SEC,
    telegram: bool = True,
) -> bool:
    return _emit_operational_alert(
        level="warning",
        rule=rule,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        dedupe_seconds=dedupe_seconds,
        telegram=telegram,
    )


def _lock_cache_key(entity_type: str, entity_id: str) -> str:
    return f"{LOCK_KEY_PREFIX}:{entity_type}:{entity_id}"


def _lock_owner_payload(user: StaffUser, *, entity_type: str, entity_id: str, current_path: str = "") -> dict[str, Any]:
    now = _utcnow()
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "owner_user_id": int(user.id),
        "owner_username": (user.username or f"U{user.id}"),
        "owner_role": (user.role or "").strip().lower(),
        "current_path": (current_path or "")[:255],
        "acquired_at": _dt_iso(now),
        "last_seen": _dt_iso(now),
        "expires_at": _dt_iso(now + timedelta(seconds=LOCK_TTL_SECONDS)),
    }


def lock_ping(*, user: StaffUser, entity_type: str, entity_id: str, current_path: str = "") -> dict[str, Any]:
    et = (entity_type or "").strip().lower()
    eid = str(entity_id or "").strip()
    if et not in {"candidata", "solicitud"} or not eid:
        return {"ok": False, "error": "Entidad inválida."}

    key = _lock_cache_key(et, eid)
    now = _utcnow()
    try:
        current = _coord_get(key, default=None)
    except BackplaneUnavailable:
        return {
            "ok": False,
            "error": "distributed_backplane_unavailable",
            "message": "Lock temporalmente no disponible. Reintenta en unos segundos.",
        }

    if not isinstance(current, dict) or not current.get("owner_user_id"):
        payload = _lock_owner_payload(user, entity_type=et, entity_id=eid, current_path=current_path)
        try:
            _coord_set(key, payload, timeout=LOCK_TTL_SECONDS + 8)
            _append_index(LOCK_INDEX_KEY, key, timeout=LOCK_TTL_SECONDS * 40)
        except BackplaneUnavailable:
            return {
                "ok": False,
                "error": "distributed_backplane_unavailable",
                "message": "Lock temporalmente no disponible. Reintenta en unos segundos.",
            }
        return {"ok": True, "state": "owner", "lock": payload}

    owner_id = int(current.get("owner_user_id") or 0)
    last_seen = _parse_dt(current.get("last_seen")) or now
    age = max(0, int((now - last_seen).total_seconds()))

    if owner_id == int(user.id):
        current["last_seen"] = _dt_iso(now)
        current["expires_at"] = _dt_iso(now + timedelta(seconds=LOCK_TTL_SECONDS))
        current["current_path"] = (current_path or current.get("current_path") or "")[:255]
        try:
            _coord_set(key, current, timeout=LOCK_TTL_SECONDS + 8)
        except BackplaneUnavailable:
            return {
                "ok": False,
                "error": "distributed_backplane_unavailable",
                "message": "Lock temporalmente no disponible. Reintenta en unos segundos.",
            }
        return {"ok": True, "state": "owner", "lock": current}

    return {
        "ok": True,
        "state": "readonly",
        "lock": current,
        "message": f"Solo lectura: {current.get('owner_username') or 'otra usuaria'} está editando (hace {age}s)",
        "held_for_sec": age,
        "can_takeover": ((user.role or "").strip().lower() in {"owner", "admin"}),
    }


def lock_takeover(*, user: StaffUser, entity_type: str, entity_id: str, reason: str = "") -> dict[str, Any]:
    et = (entity_type or "").strip().lower()
    eid = str(entity_id or "").strip()
    if et not in {"candidata", "solicitud"} or not eid:
        return {"ok": False, "error": "Entidad inválida."}

    role = (user.role or "").strip().lower()
    if role not in {"owner", "admin"}:
        return {"ok": False, "error": "No autorizado para tomar control."}

    key = _lock_cache_key(et, eid)
    try:
        before = _coord_get(key, default=None)
    except BackplaneUnavailable:
        return {
            "ok": False,
            "error": "distributed_backplane_unavailable",
            "message": "Takeover no disponible mientras el backplane esté degradado.",
        }
    payload = _lock_owner_payload(user, entity_type=et, entity_id=eid)
    try:
        _coord_set(key, payload, timeout=LOCK_TTL_SECONDS + 8)
        _append_index(LOCK_INDEX_KEY, key, timeout=LOCK_TTL_SECONDS * 40)
    except BackplaneUnavailable:
        return {
            "ok": False,
            "error": "distributed_backplane_unavailable",
            "message": "Takeover no disponible mientras el backplane esté degradado.",
        }

    log_action(
        action_type="LOCK_TAKEOVER",
        entity_type=et,
        entity_id=eid,
        summary=f"Control de edición tomado para {et} {eid}",
        metadata={
            "motivo": (reason or "").strip()[:240] or "Sin motivo",
            "previous_owner_user_id": before.get("owner_user_id") if isinstance(before, dict) else None,
            "previous_owner_username": before.get("owner_username") if isinstance(before, dict) else None,
        },
        success=True,
    )
    emit_critical_alert(
        rule="lock_takeover",
        summary=f"Takeover lock: {user.username} tomo control de {et} {eid}",
        entity_type=et,
        entity_id=eid,
        metadata={
            "actor_user_id": int(user.id),
            "actor_username": user.username,
            "previous_owner_user_id": before.get("owner_user_id") if isinstance(before, dict) else None,
            "previous_owner_username": before.get("owner_username") if isinstance(before, dict) else None,
        },
        dedupe_seconds=180,
        telegram=True,
    )
    return {"ok": True, "state": "owner", "lock": payload}


def list_active_locks() -> list[dict[str, Any]]:
    try:
        idx = _coord_get(LOCK_INDEX_KEY, default=[]) or []
    except BackplaneUnavailable:
        return []
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    clean_idx: list[str] = []
    for key in idx:
        if not isinstance(key, str) or not key:
            continue
        try:
            payload = _coord_get(key, default=None)
        except BackplaneUnavailable:
            return []
        if not isinstance(payload, dict):
            continue
        clean_idx.append(key)
        last_seen = _parse_dt(payload.get("last_seen")) or now
        age = max(0, int((now - last_seen).total_seconds()))
        row = dict(payload)
        row["held_for_sec"] = age
        row["active"] = (age <= LOCK_TTL_SECONDS)
        rows.append(row)
    try:
        _coord_set(LOCK_INDEX_KEY, clean_idx[-2000:], timeout=LOCK_TTL_SECONDS * 40)
    except BackplaneUnavailable:
        return []
    rows.sort(key=lambda x: (not bool(x.get("active")), int(x.get("held_for_sec") or 0)))
    return rows


def _session_key(token: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{token}"


def _session_rev_key(user_id: int) -> str:
    return f"{SESSION_REV_KEY_PREFIX}:{int(user_id)}"


def touch_staff_session(*, user: StaffUser, flask_session, path: str) -> dict[str, Any]:
    if not user:
        return {"ok": False, "reason": "no_user"}

    uid = int(user.id)
    rev_key = _session_rev_key(uid)
    try:
        current_rev = int(_coord_get(rev_key, default=1) or 1)
    except BackplaneUnavailable:
        return {"ok": False, "reason": "backplane_unavailable"}
    token = str(flask_session.get("staff_session_token") or "").strip()
    session_rev = int(flask_session.get("staff_session_rev") or current_rev)

    if session_rev != current_rev:
        return {"ok": False, "reason": "revoked"}

    now = _utcnow()
    if not token:
        token = uuid.uuid4().hex
        flask_session["staff_session_token"] = token
        flask_session["staff_session_rev"] = current_rev
        flask_session["staff_session_created_at"] = _dt_iso(now)

    try:
        payload = _coord_get(_session_key(token), default={}) or {}
    except BackplaneUnavailable:
        return {"ok": False, "reason": "backplane_unavailable"}
    payload.update(
        {
            "token": token,
            "user_id": uid,
            "username": (user.username or f"U{uid}"),
            "role": (user.role or "").strip().lower(),
            "ip": (_client_ip() or "")[:64],
            "user_agent": (_user_agent() or "")[:512],
            "current_path": (path or "")[:255],
            "last_seen": _dt_iso(now),
            "created_at": payload.get("created_at") or flask_session.get("staff_session_created_at") or _dt_iso(now),
        }
    )
    try:
        _coord_set(_session_key(token), payload, timeout=SESSION_TTL_SECONDS)
    except BackplaneUnavailable:
        return {"ok": False, "reason": "backplane_unavailable"}
    _append_index(SESSION_INDEX_KEY, token, timeout=SESSION_TTL_SECONDS * 4)
    return {"ok": True, "token": token, "payload": payload}


def list_active_sessions() -> list[dict[str, Any]]:
    try:
        idx = _coord_get(SESSION_INDEX_KEY, default=[]) or []
    except BackplaneUnavailable:
        return []
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    clean: list[str] = []
    for token in idx:
        if not isinstance(token, str) or not token:
            continue
        try:
            payload = _coord_get(_session_key(token), default=None)
        except BackplaneUnavailable:
            return []
        if not isinstance(payload, dict):
            continue
        clean.append(token)
        last_seen = _parse_dt(payload.get("last_seen"))
        age = max(0, int((now - last_seen).total_seconds())) if last_seen else 999999
        row = dict(payload)
        row["last_seen_seconds"] = age
        rows.append(row)
    try:
        _coord_set(SESSION_INDEX_KEY, clean[-4000:], timeout=SESSION_TTL_SECONDS * 4)
    except BackplaneUnavailable:
        return []
    rows.sort(key=lambda x: (int(x.get("last_seen_seconds") or 999999), str(x.get("username") or "")))
    return rows


def close_user_sessions(*, actor: StaffUser, user_id: int, reason: str = "") -> None:
    uid = int(user_id)
    rev_key = _session_rev_key(uid)
    rev = int(_coord_get(rev_key, default=1) or 1) + 1
    _coord_set(rev_key, rev, timeout=SESSION_TTL_SECONDS * 8)
    log_action(
        action_type="SESSION_FORCED_LOGOUT",
        entity_type="staff_user",
        entity_id=uid,
        summary=f"Sesiones cerradas para usuaria {uid}",
        metadata={
            "motivo": (reason or "").strip()[:240] or "Cierre administrativo",
            "actor_id": int(actor.id) if actor else None,
            "actor_username": getattr(actor, "username", None),
        },
        success=True,
    )


def log_error_event(
    *,
    error_type: str,
    exc: Exception | str,
    route: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    request_id: str | None = None,
    status_code: int = 500,
) -> None:
    try:
        def _clip(value: Any, limit: int) -> str:
            return str(value or "")[: max(0, int(limit))]

        if isinstance(exc, BaseException):
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            err_text = str(exc)
        else:
            stack = str(exc or "")
            err_text = stack
        stack = (stack or "")[:4000]
        log_action(
            action_type="ERROR_EVENT",
            entity_type=(entity_type or "system"),
            entity_id=(str(entity_id)[:64] if entity_id is not None else None),
            summary=f"Error en operación ({error_type})",
            metadata={
                "error_type": (error_type or "SERVER_ERROR")[:60],
                "route": _clip(route or (request.path if request else ""), 255),
                "request_id": _clip(request_id or (request.headers.get("X-Request-ID") if request else ""), 120),
                "status_code": int(status_code or 500),
                "entity_type": (entity_type or "system"),
                "entity_id": (str(entity_id)[:64] if entity_id is not None else None),
            },
            success=False,
            error=stack or err_text,
        )
    except Exception:
        return


def _emit_security_alert(summary: str, metadata: dict[str, Any]) -> None:
    dedupe_key = f"enterprise:security_alert:{metadata.get('rule','unknown')}:{metadata.get('scope','global')}"
    if _safe_cache_get(dedupe_key, default=False):
        return
    _safe_cache_set(dedupe_key, 1, timeout=45)
    log_action(
        action_type="SECURITY_ALERT",
        entity_type="security",
        entity_id=(metadata.get("scope") or "global"),
        summary=summary[:255],
        metadata=metadata,
        success=False,
        error=(metadata.get("detail") or "Alerta de seguridad")[:3500],
    )


def evaluate_security_anomalies(
    *,
    action_type: str,
    success: bool,
    actor_user_id: int | None,
    route: str | None,
    changes: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> None:
    at = (action_type or "").strip().upper()
    if not at or at in {"SECURITY_ALERT", "ALERT_RESOLVED", "ALERT_CRITICAL", "ALERT_WARNING"}:
        return

    uid = int(actor_user_id or 0)
    ip = (_client_ip() or "no_ip")[:64]

    if (not success) and at in {
        "LOGIN",
        "ADMIN_LOGIN",
        "CLIENTE_LOGIN",
        "STAFF_LOGIN_FAIL",
        "CLIENTE_LOGIN_FAIL",
        "AUTH_LOGIN_FAIL",
    }:
        key = f"enterprise:anom:login_fail:{ip}:{uid or 'na'}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=10 * 60)
        if n >= 8:
            emit_critical_alert(
                rule="login_fail_burst",
                summary=f"Login fail burst: {n} intentos fallidos en 10 minutos (IP {ip})",
                entity_type="security",
                entity_id=f"ip:{ip}",
                metadata={"count": n, "window_sec": 600, "scope": f"ip:{ip}"},
                dedupe_seconds=180,
                telegram=True,
            )
        elif n >= 5:
            emit_warning_alert(
                rule="login_fail_burst",
                summary=f"Login fail warning: {n} intentos fallidos en 10 minutos (IP {ip})",
                entity_type="security",
                entity_id=f"ip:{ip}",
                metadata={"count": n, "window_sec": 600, "scope": f"ip:{ip}"},
                dedupe_seconds=180,
                telegram=True,
                )

    if at in {"AUTH_LOGIN_BLOCKED", "AUTH_LOGIN_RATE_LIMITED"}:
        key = f"enterprise:anom:login_throttle:{ip}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=10 * 60)
        if n >= 6:
            emit_critical_alert(
                rule="login_throttle_burst",
                summary=f"Bloqueos/rate limit de login repetidos: {n} en 10 minutos (IP {ip})",
                entity_type="security",
                entity_id=f"ip:{ip}",
                metadata={"count": n, "window_sec": 600, "scope": f"ip:{ip}", "event": at},
                dedupe_seconds=120,
                telegram=True,
            )
        elif n >= 3:
            emit_warning_alert(
                rule="login_throttle_burst",
                summary=f"Patrón de login sospechoso: {n} bloqueos/rate limit en 10 minutos (IP {ip})",
                entity_type="security",
                entity_id=f"ip:{ip}",
                metadata={"count": n, "window_sec": 600, "scope": f"ip:{ip}", "event": at},
                dedupe_seconds=120,
                telegram=True,
            )

    if at in {"AUTHZ_DENIED", "PERMISSION_DENIED"}:
        scope = f"{ip}:{uid or 'anon'}"
        key = f"enterprise:anom:authz_denied:{scope}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=5 * 60)
        if n >= 10:
            emit_critical_alert(
                rule="authz_denied_burst",
                summary=f"Accesos denegados repetidos: {n} en 5 minutos ({scope})",
                entity_type="security",
                entity_id=scope,
                metadata={"count": n, "window_sec": 300, "scope": scope, "route": route},
                dedupe_seconds=90,
                telegram=True,
            )
        elif n >= 5:
            emit_warning_alert(
                rule="authz_denied_burst",
                summary=f"Accesos denegados frecuentes: {n} en 5 minutos ({scope})",
                entity_type="security",
                entity_id=scope,
                metadata={"count": n, "window_sec": 300, "scope": scope, "route": route},
                dedupe_seconds=90,
                telegram=True,
            )

    if at == "ERROR_EVENT":
        err_type = str((metadata or {}).get("error_type") or "").strip().upper()
        status_code = int((metadata or {}).get("status_code") or 0)
        if err_type == "SAVE_ERROR":
            entity_type_v = str((metadata or {}).get("entity_type") or "operacion")
            entity_id_v = str((metadata or {}).get("entity_id") or "global")
            emit_critical_alert(
                rule="save_error",
                summary=f"SAVE_ERROR en {entity_type_v} ({entity_id_v})",
                entity_type=entity_type_v,
                entity_id=entity_id_v,
                metadata={"route": route, "status_code": status_code, "error_type": err_type},
                dedupe_seconds=120,
                telegram=True,
            )

        if status_code >= 500:
            key = "enterprise:anom:error500:5m"
            n = int(_safe_cache_get(key, default=0) or 0) + 1
            _safe_cache_set(key, n, timeout=5 * 60)
            if n >= 3 and (n == 3 or n % 3 == 0):
                emit_critical_alert(
                    rule="error_500_burst",
                    summary=f"Errores 500 repetidos: {n} en 5 minutos",
                    entity_type="system",
                    entity_id="500_burst",
                    metadata={"count": n, "window_sec": 300},
                    dedupe_seconds=60,
                    telegram=True,
                )

    if at in {"CANDIDATA_EDIT", "CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"}:
        key = f"enterprise:anom:changes_min:{uid or ip}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=ANOMALY_WINDOW_SEC)
        if n >= 30:
            _emit_security_alert(
                "Cambios inusualmente altos por minuto",
                {
                    "rule": "candidate_change_burst",
                    "scope": f"actor:{uid or ip}",
                    "count": n,
                    "window_sec": ANOMALY_WINDOW_SEC,
                    "detail": "Se detectó un volumen alto de cambios en candidatas en un minuto.",
                },
            )

    estado_changed = False
    try:
        if isinstance(changes, dict) and "estado" in changes:
            estado_changed = True
        if not estado_changed and isinstance(metadata, dict):
            estado_changed = bool(metadata.get("estado") or metadata.get("status"))
    except Exception:
        estado_changed = False

    if estado_changed or at in {"CANDIDATA_MARK_LISTA", "CANDIDATA_MARK_TRABAJANDO", "SOLICITUD_UPDATE"}:
        key = f"enterprise:anom:status_mass:{uid or ip}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=ANOMALY_WINDOW_SEC)
        if n >= 20:
            _emit_security_alert(
                "Cambios masivos de estado detectados",
                {
                    "rule": "mass_status_change",
                    "scope": f"actor:{uid or ip}",
                    "count": n,
                    "window_sec": ANOMALY_WINDOW_SEC,
                    "detail": "Se detectaron muchos cambios de estado en poco tiempo.",
                },
            )

    if at.startswith("LIVE_"):
        key = f"enterprise:anom:robot_nav:{uid or ip}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=ANOMALY_WINDOW_SEC)
        if n >= 100:
            _emit_security_alert(
                "Navegación automatizada sospechosa",
                {
                    "rule": "robot_navigation",
                    "scope": f"actor:{uid or ip}",
                    "count": n,
                    "window_sec": ANOMALY_WINDOW_SEC,
                    "detail": "Se detectó un patrón de navegación con frecuencia no humana.",
                },
                )

    if at in {"STAFF_USER_CREATE", "STAFF_USER_DELETE", "ROLE_CHANGED", "STAFF_PASSWORD_CHANGED"} and success:
        actor_scope = str(uid or ip)
        key = f"enterprise:anom:admin_sensitive:{actor_scope}"
        n = int(_safe_cache_get(key, default=0) or 0) + 1
        _safe_cache_set(key, n, timeout=10 * 60)
        if n >= 8:
            emit_warning_alert(
                rule="admin_sensitive_burst",
                summary=f"Actividad admin sensible alta: {n} eventos en 10 minutos (actor {actor_scope})",
                entity_type="security",
                entity_id=actor_scope,
                metadata={"count": n, "window_sec": 600, "scope": actor_scope, "event": at},
                dedupe_seconds=120,
                telegram=True,
            )


def resolve_alert(alert_id: int, actor: StaffUser | None = None) -> None:
    log_action(
        action_type="ALERT_RESOLVED",
        entity_type="alert",
        entity_id=str(int(alert_id)),
        summary=f"Alerta {int(alert_id)} marcada como resuelta",
        metadata={
            "alert_id": int(alert_id),
            "actor_username": getattr(actor, "username", None),
        },
        success=True,
    )


def _resolved_alert_ids(limit: int = 2000) -> set[int]:
    rows = (
        StaffAuditLog.query
        .filter(StaffAuditLog.action_type == "ALERT_RESOLVED")
        .order_by(StaffAuditLog.id.desc())
        .limit(max(50, int(limit)))
        .all()
    )
    out: set[int] = set()
    for row in rows:
        meta = dict(getattr(row, "metadata_json", {}) or {})
        raw = meta.get("alert_id") or row.entity_id
        try:
            out.add(int(raw))
        except Exception:
            continue
    return out


def get_alert_items(limit: int = 20, scope: str = "all", include_resolved: bool = True) -> list[dict[str, Any]]:
    q = StaffAuditLog.query
    if scope == "security":
        q = q.filter(StaffAuditLog.action_type == "SECURITY_ALERT")
    elif scope == "error":
        q = q.filter(StaffAuditLog.action_type == "ERROR_EVENT")
    elif scope == "critical":
        q = q.filter(StaffAuditLog.action_type.in_(["ALERT_CRITICAL", "ALERT_WARNING"]))
    else:
        q = q.filter(StaffAuditLog.action_type.in_(["SECURITY_ALERT", "ERROR_EVENT", "ALERT_CRITICAL", "ALERT_WARNING"]))

    rows = q.order_by(StaffAuditLog.id.desc()).limit(max(20, limit * 5)).all()
    resolved = _resolved_alert_ids() if not include_resolved else set()

    actor_map: dict[int, str] = {}
    actor_ids = sorted({int(r.actor_user_id) for r in rows if r.actor_user_id is not None})
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        actor_map = {int(u.id): (u.username or f"U{u.id}") for u in users}

    items: list[dict[str, Any]] = []
    for row in rows:
        if (not include_resolved) and int(row.id) in resolved:
            continue
        meta = dict(getattr(row, "metadata_json", {}) or {})
        err_type = (meta.get("error_type") or "")
        items.append(
            {
                "id": int(row.id),
                "created_at": _dt_iso(row.created_at),
                "action_type": row.action_type,
                "error_type": err_type,
                "route": row.route,
                "summary": row.summary,
                "actor_username": actor_map.get(int(row.actor_user_id)) if row.actor_user_id else None,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "severity": ("critical" if row.action_type == "ALERT_CRITICAL" else ("warning" if row.action_type == "ALERT_WARNING" else ("error" if row.action_type == "ERROR_EVENT" else "security"))),
                "metadata": meta,
                "error_message": (row.error_message or "")[:4000],
                "is_resolved": int(row.id) in resolved,
            }
        )
        if len(items) >= limit:
            break
    return items


def health_payload() -> dict[str, Any]:
    now = _utcnow()
    db_ok = True
    db_error = None
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    # No hay cola dedicada en este sistema; se reporta stream/cache como infraestructura en vivo.
    stream_ok = bool(bp_healthcheck(strict=False))

    err_since = now - timedelta(minutes=15)
    last_errors = (
        StaffAuditLog.query
        .filter(StaffAuditLog.action_type == "ERROR_EVENT", StaffAuditLog.created_at >= err_since)
        .count()
    )

    return {
        "generated_at": _dt_iso(now),
        "db": {"ok": bool(db_ok), "error": db_error},
        "stream": {"ok": bool(stream_ok)},
        "errors_last_15m": int(last_errors),
    }


def _metric_status(value: float | int | None, *, warn_at: float, crit_at: float, lower_is_better: bool = True) -> str:
    if value is None:
        return "unknown"
    try:
        n = float(value)
    except Exception:
        return "unknown"
    if lower_is_better:
        if n >= float(crit_at):
            return "critical"
        if n >= float(warn_at):
            return "warning"
    else:
        if n <= float(crit_at):
            return "critical"
        if n <= float(warn_at):
            return "warning"
    return "ok"


def _o1_counter_key(name: str) -> str:
    safe = (name or "").strip().lower()
    safe = _LIVE_REGION_SANITIZE_RE.sub("_", safe)
    return f"o1:ops:{safe}"


_LIVE_REGION_INDEX_KEY = _o1_counter_key("live:regions:index")


def _safe_bp_incr_counter(name: str, *, delta: int = 1, timeout: int = O1_COUNTER_TTL_SECONDS) -> int:
    try:
        return int(bp_incr(_o1_counter_key(name), delta=int(delta), timeout=max(60, int(timeout)), strict=False, context="o1_counter_incr"))
    except Exception:
        return 0


def _safe_bp_get_counter(name: str, default: int = 0) -> int:
    try:
        return int(bp_get(_o1_counter_key(name), default=default, strict=False, context="o1_counter_get") or default)
    except Exception:
        return int(default)


def _live_region_slug(region: str | None) -> str:
    raw = (region or "").strip().lower()
    if not raw:
        return "unknown"
    return _LIVE_REGION_SANITIZE_RE.sub("_", raw).strip("_") or "unknown"


def bump_operational_counter(name: str, *, delta: int = 1, timeout: int = O1_COUNTER_TTL_SECONDS) -> int:
    return _safe_bp_incr_counter(name, delta=delta, timeout=timeout)


def ingest_live_observability_event(payload: dict[str, Any] | None, *, user_id: int | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    event = str(data.get("event") or "").strip().lower()
    region = _live_region_slug(data.get("region"))
    try:
        duration_ms = int(data.get("duration_ms") or 0)
    except Exception:
        duration_ms = 0
    duration_ms = max(0, min(duration_ms, 120000))
    ok_flag = bool(data.get("ok", True))

    accepted = False
    if event == "fallback_entered":
        _safe_bp_incr_counter("live:fallback_entered_count")
        accepted = True
    elif event == "sse_open":
        _safe_bp_incr_counter("live:sse_open_count")
        accepted = True
    elif event == "refetch_region":
        _safe_bp_incr_counter(f"live:refetch:{region}:count")
        if duration_ms > 0:
            _safe_bp_incr_counter(f"live:refetch:{region}:sum_ms", delta=duration_ms)
        if not ok_flag:
            _safe_bp_incr_counter(f"live:refetch:{region}:fail_count")
        try:
            known = bp_get(_LIVE_REGION_INDEX_KEY, default=[], strict=False, context="o1_regions_idx_get") or []
            known = [str(x).strip() for x in known if str(x).strip()]
            if region not in known:
                known.append(region)
                known = known[-50:]
            bp_set(_LIVE_REGION_INDEX_KEY, known, timeout=O1_COUNTER_TTL_SECONDS, strict=False, context="o1_regions_idx_set")
        except Exception:
            pass
        accepted = True

    return {
        "ok": True,
        "accepted": bool(accepted),
        "event": event,
        "region": region,
        "user_id": int(user_id) if user_id is not None else None,
    }


O2_TREND_METRIC_KEYS = (
    "outbox_backlog_pending",
    "outbox_oldest_pending_age_seconds",
    "outbox_quarantined_total",
    "outbox_quarantined_last_15m",
    "relay_fail_rate_pct_15m",
    "relay_retry_rate_pct_15m",
    "live_polling_fallback_pct_15m",
    "live_poll_degraded_outbox_fallback_count_15m",
    "concurrency_idempotency_conflicts_15m",
    "critical_5xx_endpoints_15m",
)


def _o2_int_env(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except Exception:
        return default
    return max(min_value, min(val, max_value))


def o2_snapshot_policy() -> dict[str, int]:
    interval_m = _o2_int_env(
        "O2_SNAPSHOT_INTERVAL_MINUTES",
        O2_SNAPSHOT_INTERVAL_MINUTES_DEFAULT,
        min_value=1,
        max_value=60,
    )
    retention_h = _o2_int_env(
        "O2_SNAPSHOT_RETENTION_HOURS",
        O2_SNAPSHOT_RETENTION_HOURS_DEFAULT,
        min_value=24,
        max_value=24 * 30,
    )
    return {
        "interval_minutes": int(interval_m),
        "retention_hours": int(retention_h),
    }


def _o2_pick_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in O2_TREND_METRIC_KEYS:
        out[key] = metrics.get(key)
    return out


def _o2_dir(current: Any, previous: Any) -> tuple[float | None, str]:
    if current is None or previous is None:
        return None, "unknown"
    try:
        c = float(current)
        p = float(previous)
    except Exception:
        return None, "unknown"
    delta = round(c - p, 2)
    if abs(delta) < 0.01:
        return delta, "flat"
    return delta, ("up" if delta > 0 else "down")


def _o2_baseline(snapshots: list[OperationalMetricSnapshot], since: datetime) -> OperationalMetricSnapshot | None:
    in_window = [row for row in snapshots if getattr(row, "captured_at", None) and row.captured_at >= since]
    if not in_window:
        return None
    return in_window[0]


def cleanup_operational_snapshots(*, retention_hours: int | None = None) -> int:
    policy = o2_snapshot_policy()
    retention_h = int(retention_hours or policy["retention_hours"])
    cutoff = _utcnow() - timedelta(hours=max(24, retention_h))
    try:
        deleted = (
            OperationalMetricSnapshot.query
            .filter(OperationalMetricSnapshot.captured_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.session.commit()
        return int(deleted or 0)
    except Exception:
        db.session.rollback()
        return 0


def operational_snapshot_capture(*, window_minutes: int = O1_WINDOW_MINUTES_DEFAULT, cleanup: bool = True) -> dict[str, Any]:
    payload = operational_semaphore_payload(window_minutes=window_minutes, include_trends=False)
    metrics = _o2_pick_metrics(payload.get("metrics") or {})
    now = _utcnow()
    row = OperationalMetricSnapshot(
        captured_at=now,
        window_minutes=max(5, min(int(window_minutes or O1_WINDOW_MINUTES_DEFAULT), 120)),
        metrics=metrics,
    )
    db.session.add(row)
    db.session.commit()
    pruned = 0
    if cleanup:
        pruned = cleanup_operational_snapshots()
    return {
        "ok": True,
        "snapshot_id": int(row.id),
        "captured_at": _dt_iso(row.captured_at),
        "window_minutes": int(row.window_minutes),
        "metrics": metrics,
        "pruned": int(pruned),
        "policy": o2_snapshot_policy(),
    }


def operational_trends_payload(*, current_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _utcnow()
    metrics = dict(current_metrics or {})
    if not metrics:
        metrics = _o2_pick_metrics((operational_semaphore_payload(include_trends=False).get("metrics") or {}))
    tracked = _o2_pick_metrics(metrics)

    since_24h = now - timedelta(hours=24)
    snapshots = (
        OperationalMetricSnapshot.query
        .filter(OperationalMetricSnapshot.captured_at >= since_24h)
        .order_by(OperationalMetricSnapshot.captured_at.asc(), OperationalMetricSnapshot.id.asc())
        .all()
    )
    last_snapshot = snapshots[-1] if snapshots else None
    prev_snapshot = snapshots[-2] if len(snapshots) >= 2 else None
    baseline_1h = _o2_baseline(snapshots, now - timedelta(hours=1))
    baseline_24h = _o2_baseline(snapshots, since_24h)

    out: dict[str, Any] = {}
    for key in O2_TREND_METRIC_KEYS:
        current_val = tracked.get(key)
        previous_val = None
        if last_snapshot is not None:
            previous_val = (dict(getattr(last_snapshot, "metrics", {}) or {})).get(key)
        delta_prev, direction = _o2_dir(current_val, previous_val)

        baseline_1h_val = None if baseline_1h is None else (dict(getattr(baseline_1h, "metrics", {}) or {})).get(key)
        baseline_24h_val = None if baseline_24h is None else (dict(getattr(baseline_24h, "metrics", {}) or {})).get(key)
        delta_1h, direction_1h = _o2_dir(current_val, baseline_1h_val)
        delta_24h, direction_24h = _o2_dir(current_val, baseline_24h_val)

        out[key] = {
            "current": current_val,
            "previous": previous_val,
            "delta": delta_prev,
            "direction": direction,
            "previous_captured_at": _dt_iso(last_snapshot.captured_at) if last_snapshot is not None else None,
            "windows": {
                "1h": {
                    "baseline": baseline_1h_val,
                    "delta": delta_1h,
                    "direction": direction_1h,
                    "captured_at": _dt_iso(baseline_1h.captured_at) if baseline_1h is not None else None,
                },
                "24h": {
                    "baseline": baseline_24h_val,
                    "delta": delta_24h,
                    "direction": direction_24h,
                    "captured_at": _dt_iso(baseline_24h.captured_at) if baseline_24h is not None else None,
                },
            },
        }

    return {
        "generated_at": _dt_iso(now),
        "metrics": out,
        "latest_snapshot_at": _dt_iso(last_snapshot.captured_at) if last_snapshot is not None else None,
        "previous_snapshot_at": _dt_iso(prev_snapshot.captured_at) if prev_snapshot is not None else None,
        "samples_last_24h": int(len(snapshots)),
    }


def _o1_build_alerts(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    backlog = int(metrics.get("outbox_backlog_pending") or 0)
    oldest_s = int(metrics.get("outbox_oldest_pending_age_seconds") or 0)
    fail_rate = float(metrics.get("relay_fail_rate_pct_15m") or 0.0)
    retry_rate = float(metrics.get("relay_retry_rate_pct_15m") or 0.0)
    fallback_pct = metrics.get("live_polling_fallback_pct_15m")
    fallback_pct_val = float(fallback_pct) if fallback_pct is not None else None
    quarantined_total = int(metrics.get("outbox_quarantined_total") or 0)
    quarantined_last_15m = int(metrics.get("outbox_quarantined_last_15m") or 0)

    if backlog >= 200 or oldest_s >= 900:
        alerts.append(
            {
                "code": "outbox_lag_high",
                "severity": "critical" if (backlog >= 500 or oldest_s >= 1800) else "warning",
                "message": (
                    f"Outbox atrasada: pendientes={backlog}, evento mas viejo={oldest_s}s."
                ),
                "condition": "backlog>=200 OR oldest_pending_age>=900s",
            }
        )
    if fail_rate >= 5.0 or retry_rate >= 20.0:
        alerts.append(
            {
                "code": "relay_reliability_degraded",
                "severity": "critical" if (fail_rate >= 10.0 or retry_rate >= 35.0) else "warning",
                "message": (
                    f"Relay degradado: fail_rate={fail_rate:.2f}% retry_rate={retry_rate:.2f}% en 15m."
                ),
                "condition": "fail_rate>=5% OR retry_rate>=20% (15m)",
            }
        )
    if fallback_pct_val is not None and fallback_pct_val >= 25.0:
        alerts.append(
            {
                "code": "live_polling_fallback_spike",
                "severity": "critical" if fallback_pct_val >= 50.0 else "warning",
                "message": f"Fallback a polling elevado: {fallback_pct_val:.2f}% en 15m.",
                "condition": "polling_fallback_pct>=25% (15m)",
            }
        )
    if quarantined_total > 0 or quarantined_last_15m > 0:
        alerts.append(
            {
                "code": "outbox_quarantine_growth",
                "severity": "critical" if (quarantined_last_15m >= 10 or quarantined_total >= 50) else "warning",
                "message": (
                    f"Eventos en cuarentena: total={quarantined_total}, nuevos_15m={quarantined_last_15m}."
                ),
                "condition": "quarantined_total>0 OR quarantined_last_15m>0",
            }
        )
    return alerts


def operational_semaphore_payload(*, window_minutes: int = O1_WINDOW_MINUTES_DEFAULT, include_trends: bool = True) -> dict[str, Any]:
    now = _utcnow()
    window_m = max(5, min(int(window_minutes or O1_WINDOW_MINUTES_DEFAULT), 120))
    since = now - timedelta(minutes=window_m)

    try:
        backlog_pending = int(
            DomainOutbox.query
            .filter(DomainOutbox.published_at.is_(None))
            .count()
        )
    except Exception:
        backlog_pending = 0
    try:
        quarantined_total = int(
            DomainOutbox.query
            .filter(DomainOutbox.relay_status == "quarantined")
            .count()
        )
    except Exception:
        quarantined_total = 0
    try:
        quarantined_last_15m = int(
            DomainOutbox.query
            .filter(DomainOutbox.relay_status == "quarantined", DomainOutbox.quarantined_at.isnot(None), DomainOutbox.quarantined_at >= since)
            .count()
        )
    except Exception:
        quarantined_last_15m = 0
    try:
        retrying_total = int(
            DomainOutbox.query
            .filter(DomainOutbox.relay_status == "retrying")
            .count()
        )
    except Exception:
        retrying_total = 0
    try:
        oldest_pending = (
            DomainOutbox.query
            .with_entities(func.min(DomainOutbox.created_at))
            .filter(DomainOutbox.published_at.is_(None))
            .scalar()
        )
    except Exception:
        oldest_pending = None
    oldest_pending_age_seconds = 0
    if oldest_pending is not None:
        try:
            oldest_pending_age_seconds = max(0, int((now - oldest_pending).total_seconds()))
        except Exception:
            oldest_pending_age_seconds = 0

    try:
        relay_published = int(
            DomainOutbox.query
            .filter(DomainOutbox.published_at.isnot(None), DomainOutbox.published_at >= since)
            .count()
        )
    except Exception:
        relay_published = 0
    try:
        relay_failed = int(
            DomainOutbox.query
            .filter(DomainOutbox.published_at.is_(None), DomainOutbox.last_attempt_at.isnot(None), DomainOutbox.last_attempt_at >= since)
            .filter(DomainOutbox.last_error.isnot(None))
            .count()
        )
    except Exception:
        relay_failed = 0
    try:
        relay_retried = int(
            DomainOutbox.query
            .filter(DomainOutbox.last_attempt_at.isnot(None), DomainOutbox.last_attempt_at >= since)
            .filter(DomainOutbox.published_attempts > 1)
            .count()
        )
    except Exception:
        relay_retried = 0
    relay_attempted_total = int(relay_published + relay_failed)
    relay_fail_rate_pct = round((relay_failed / relay_attempted_total) * 100.0, 2) if relay_attempted_total > 0 else 0.0
    relay_retry_rate_pct = round((relay_retried / relay_attempted_total) * 100.0, 2) if relay_attempted_total > 0 else 0.0
    relay_throughput_per_min = round(relay_published / float(window_m), 2)

    sse_open_count = _safe_bp_get_counter("live:sse_open_count", default=0)
    fallback_count = _safe_bp_get_counter("live:fallback_entered_count", default=0)
    degraded_outbox_fallback_count = _safe_bp_get_counter("live:poll:degraded_outbox_fallback_count", default=0)
    fallback_den = int(sse_open_count + fallback_count)
    fallback_pct = round((fallback_count / fallback_den) * 100.0, 2) if fallback_den > 0 else None

    latency_regions: dict[str, dict[str, Any]] = {}
    known_regions = bp_get(_LIVE_REGION_INDEX_KEY, default=[], strict=False, context="o1_regions_idx_get_payload") or []
    known_regions = [str(r).strip().lower() for r in known_regions if str(r).strip()]
    if not known_regions:
        known_regions = ["unknown"]
    for region in known_regions:
        count = _safe_bp_get_counter(f"live:refetch:{region}:count", default=0)
        if count <= 0:
            continue
        sum_ms = _safe_bp_get_counter(f"live:refetch:{region}:sum_ms", default=0)
        fail_count = _safe_bp_get_counter(f"live:refetch:{region}:fail_count", default=0)
        avg_ms = round((sum_ms / count), 2) if count > 0 else None
        latency_regions[region] = {
            "count": int(count),
            "avg_ms": avg_ms,
            "fail_count": int(fail_count),
            "fail_rate_pct": round((fail_count / count) * 100.0, 2) if count > 0 else 0.0,
        }

    try:
        idempotency_conflicts = int(
            RequestIdempotencyKey.query
            .filter(RequestIdempotencyKey.created_at >= since, RequestIdempotencyKey.response_status == 409)
            .count()
        )
    except Exception:
        idempotency_conflicts = 0
    concurrency_conflicts = _safe_bp_get_counter("concurrency_conflict_count", default=0)
    conflicts_total = int(idempotency_conflicts + concurrency_conflicts)

    critical_paths = (
        "/admin/solicitudes",
        "/admin/clientes",
        "/admin/live/invalidation",
        "/admin/monitoreo",
    )
    try:
        errors_5xx_critical = int(
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "ERROR_EVENT", StaffAuditLog.created_at >= since)
            .filter(or_(*[StaffAuditLog.route.like(f"{p}%") for p in critical_paths]))
            .count()
        )
    except Exception:
        errors_5xx_critical = 0

    metrics = {
        "window_minutes": int(window_m),
        "outbox_backlog_pending": backlog_pending,
        "outbox_oldest_pending_age_seconds": oldest_pending_age_seconds,
        "outbox_quarantined_total": quarantined_total,
        "outbox_quarantined_last_15m": quarantined_last_15m,
        "outbox_retrying_total": retrying_total,
        "relay_fail_rate_pct_15m": relay_fail_rate_pct,
        "relay_retry_rate_pct_15m": relay_retry_rate_pct,
        "relay_throughput_per_min_15m": relay_throughput_per_min,
        "relay_published_15m": relay_published,
        "relay_failed_15m": relay_failed,
        "live_polling_fallback_pct_15m": fallback_pct,
        "live_sse_open_count_15m": int(sse_open_count),
        "live_fallback_count_15m": int(fallback_count),
        "live_poll_degraded_outbox_fallback_count_15m": int(degraded_outbox_fallback_count),
        "live_refetch_latency_by_region_15m": latency_regions,
        "concurrency_idempotency_conflicts_15m": conflicts_total,
        "critical_5xx_endpoints_15m": errors_5xx_critical,
    }

    statuses = {
        "outbox_backlog_pending": _metric_status(backlog_pending, warn_at=120, crit_at=300),
        "outbox_oldest_pending_age_seconds": _metric_status(oldest_pending_age_seconds, warn_at=600, crit_at=1200),
        "outbox_quarantined_total": _metric_status(quarantined_total, warn_at=1, crit_at=25),
        "outbox_quarantined_last_15m": _metric_status(quarantined_last_15m, warn_at=1, crit_at=10),
        "relay_fail_rate_pct_15m": _metric_status(relay_fail_rate_pct, warn_at=5.0, crit_at=10.0),
        "relay_retry_rate_pct_15m": _metric_status(relay_retry_rate_pct, warn_at=20.0, crit_at=35.0),
        "live_polling_fallback_pct_15m": _metric_status(fallback_pct, warn_at=25.0, crit_at=50.0) if fallback_pct is not None else "unknown",
        "concurrency_idempotency_conflicts_15m": _metric_status(conflicts_total, warn_at=5, crit_at=15),
        "critical_5xx_endpoints_15m": _metric_status(errors_5xx_critical, warn_at=3, crit_at=8),
    }
    alerts = _o1_build_alerts(metrics)

    payload = {
        "generated_at": _dt_iso(now),
        "metrics": metrics,
        "statuses": statuses,
        "alerts": alerts,
        "snapshot_policy": o2_snapshot_policy(),
    }
    if include_trends:
        payload["trends"] = operational_trends_payload(current_metrics=metrics)
    return payload


def _range_bounds(period: str) -> tuple[datetime, datetime]:
    now = _utcnow()
    p = (period or "7d").strip().lower()
    if p in {"hoy", "today", "1d"}:
        start = datetime(now.year, now.month, now.day)
        return start, now
    if p in {"30d", "30", "mes", "month"}:
        return now - timedelta(days=30), now
    return now - timedelta(days=7), now


def metrics_secretarias(period: str = "7d") -> dict[str, Any]:
    start, end = _range_bounds(period)
    interview_actions = ["CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"]

    try:
        rows = (
            db.session.query(
                StaffAuditLog.actor_user_id.label("user_id"),
                StaffUser.username.label("username"),
                func.sum(case((StaffAuditLog.action_type == "MATCHING_SEND", 1), else_=0)).label("colocaciones"),
                func.sum(case((StaffAuditLog.action_type.in_(interview_actions), 1), else_=0)).label("entrevistas"),
                func.sum(case((StaffAuditLog.action_type == "CANDIDATA_EDIT", 1), else_=0)).label("ediciones"),
                func.sum(case((StaffAuditLog.action_type == "SOLICITUD_CREATE", 1), else_=0)).label("solicitudes"),
            )
            .join(StaffUser, StaffUser.id == StaffAuditLog.actor_user_id)
            .filter(StaffAuditLog.created_at >= start, StaffAuditLog.created_at <= end)
            .group_by(StaffAuditLog.actor_user_id, StaffUser.username)
            .order_by(func.sum(case((StaffAuditLog.action_type == "MATCHING_SEND", 1), else_=0)).desc(), StaffUser.username.asc())
            .all()
        )
    except Exception:
        rows = []

    items = []
    for r in rows:
        solicitudes = int(r.solicitudes or 0)
        colocaciones = int(r.colocaciones or 0)
        tasa = round((colocaciones / solicitudes) * 100.0, 2) if solicitudes > 0 else 0.0
        items.append(
            {
                "user_id": int(r.user_id) if r.user_id is not None else None,
                "username": r.username,
                "colocaciones": colocaciones,
                "entrevistas": int(r.entrevistas or 0),
                "ediciones": int(r.ediciones or 0),
                "solicitudes": solicitudes,
                "tasa_exito": tasa,
            }
        )
    return {"period": period, "start": _dt_iso(start), "end": _dt_iso(end), "items": items}


def metrics_solicitudes(period: str = "7d") -> dict[str, Any]:
    start, end = _range_bounds(period)

    try:
        pending_rows = (
            db.session.query(Solicitud.estado, func.count(Solicitud.id))
            .group_by(Solicitud.estado)
            .all()
        )
        pendientes = {str(e or "sin_estado"): int(c or 0) for e, c in pending_rows}
    except Exception:
        pendientes = {}

    # Tiempo promedio de colocación: desde creación de solicitud hasta primer envío matching.
    try:
        sent_rows = (
            db.session.query(Solicitud.fecha_solicitud, func.min(SolicitudCandidata.created_at).label("first_send"))
            .join(SolicitudCandidata, SolicitudCandidata.solicitud_id == Solicitud.id)
            .filter(Solicitud.fecha_solicitud.isnot(None), SolicitudCandidata.created_at.isnot(None))
            .filter(Solicitud.fecha_solicitud >= start, Solicitud.fecha_solicitud <= end)
            .group_by(Solicitud.id, Solicitud.fecha_solicitud)
            .all()
        )
    except Exception:
        sent_rows = []

    durations = []
    for fecha_s, first_send in sent_rows:
        if fecha_s and first_send and first_send >= fecha_s:
            durations.append((first_send - fecha_s).total_seconds())
    avg_hours = round((sum(durations) / len(durations)) / 3600.0, 2) if durations else 0.0

    try:
        log_base = StaffAuditLog.query.filter(StaffAuditLog.created_at >= start, StaffAuditLog.created_at <= end)
        ok_count = log_base.filter(StaffAuditLog.success.is_(True)).count()
        fail_count = log_base.filter(StaffAuditLog.success.is_(False)).count()
    except Exception:
        ok_count = 0
        fail_count = 0

    return {
        "period": period,
        "start": _dt_iso(start),
        "end": _dt_iso(end),
        "pendientes_por_estado": pendientes,
        "tiempo_promedio_colocacion_horas": avg_hours,
        "ratio_exito_fallo": {
            "exitos": int(ok_count),
            "fallos": int(fail_count),
        },
    }


def metrics_dashboard(period: str = "7d") -> dict[str, Any]:
    return {
        "secretarias": metrics_secretarias(period),
        "solicitudes": metrics_solicitudes(period),
    }


def feedback_weights() -> dict[str, int]:
    key = "enterprise:decision:weights"
    data = _safe_cache_get(key, default=None)
    if isinstance(data, dict):
        return {str(k): int(v) for k, v in data.items() if str(k)}
    base = {
        "ubicacion": 0,
        "horario": 0,
        "experiencia": 0,
        "documentacion": 0,
        "referencias": 0,
    }
    _safe_cache_set(key, base, timeout=60 * 60 * 24 * 7)
    return base


def apply_feedback_adjustment(reason_key: str, good: bool) -> dict[str, int]:
    key = (reason_key or "").strip().lower()
    w = feedback_weights()
    if key not in w:
        return w
    delta = 1 if good else -1
    w[key] = max(-10, min(10, int(w.get(key, 0)) + delta))
    _safe_cache_set("enterprise:decision:weights", w, timeout=60 * 60 * 24 * 7)
    return w


def deterministic_decision_score(base_score: int, reasons: list[str], weights: dict[str, int] | None = None) -> tuple[int, list[str]]:
    w = weights or feedback_weights()
    total = int(base_score or 0)
    txt = " ".join([str(r or "").lower() for r in (reasons or [])])
    applied: list[str] = []

    for key, label in (
        ("ubicacion", "ubicación"),
        ("horario", "horario"),
        ("experiencia", "experiencia"),
        ("documentacion", "documentos"),
        ("referencias", "referencias"),
    ):
        if label in txt or key in txt:
            val = int(w.get(key, 0))
            total += val
            if val != 0:
                applied.append(f"Ajuste por {key}: {val:+d}")

    return max(0, min(100, total)), applied


def intelligent_suggestions_for_solicitud(solicitud: Solicitud, top_k: int = 10) -> list[dict[str, Any]]:
    ranked = rank_candidates(solicitud, top_k=max(20, top_k))
    locks = {f"candidata:{x.get('entity_id')}": x for x in list_active_locks() if x.get("entity_type") == "candidata" and x.get("active")}

    out: list[dict[str, Any]] = []
    for item in ranked[: max(1, int(top_k))]:
        cand = item.get("candidate")
        if cand is None:
            continue
        reasons = [str(r) for r in (item.get("reasons") or []) if str(r).strip()]
        base_score = int(item.get("score") or 0)
        final_score, adjustments = deterministic_decision_score(base_score, reasons)

        ready_check = (item.get("breakdown_snapshot") or {}).get("ready_check") or {}
        docs = (ready_check.get("docs") or {}).get("flags") or {}
        alerts: list[str] = []
        if not bool(ready_check.get("has_interview")):
            alerts.append("Falta entrevista")
        if not bool(docs.get("depuracion")) or not bool(docs.get("perfil")):
            alerts.append("Faltan documentos")
        if not bool(ready_check.get("has_referencias_laboral")) or not bool(ready_check.get("has_referencias_familiares")):
            alerts.append("Sin referencias completas")

        lock = locks.get(f"candidata:{getattr(cand, 'fila', '')}")
        if lock and int(lock.get("owner_user_id") or 0) != int(getattr(current_user_like(), "id", 0) or 0):
            alerts.append(f"En edición por {lock.get('owner_username')}")

        top_reasons = reasons[:5]
        if adjustments:
            top_reasons.extend(adjustments[:2])

        out.append(
            {
                "candidata_id": int(getattr(cand, "fila", 0) or 0),
                "codigo": getattr(cand, "codigo", None),
                "nombre": getattr(cand, "nombre_completo", None),
                "telefono": getattr(cand, "numero_telefono", None),
                "score": final_score,
                "base_score": base_score,
                "razones": top_reasons,
                "alertas": alerts[:3],
            }
        )

    out.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
    return out[: max(1, int(top_k))]


def current_user_like():
    try:
        from flask_login import current_user

        if current_user and getattr(current_user, "is_authenticated", False):
            return current_user
    except Exception:
        pass
    return None


def register_decision_feedback(
    *,
    actor: StaffUser,
    solicitud_id: int,
    candidata_id: int,
    good: bool,
    reason_key: str,
    reason_text: str,
) -> dict[str, int]:
    weights = apply_feedback_adjustment(reason_key=reason_key, good=good)
    log_action(
        action_type="DECISION_FEEDBACK",
        entity_type="solicitud",
        entity_id=str(int(solicitud_id)),
        summary="Feedback de sugerencia registrado",
        metadata={
            "solicitud_id": int(solicitud_id),
            "candidata_id": int(candidata_id),
            "feedback": "buena" if good else "no_encajo",
            "reason_key": (reason_key or "").strip().lower()[:40],
            "reason_text": (reason_text or "").strip()[:200],
            "weights": weights,
            "actor": getattr(actor, "username", None),
        },
        success=True,
    )
    return weights
