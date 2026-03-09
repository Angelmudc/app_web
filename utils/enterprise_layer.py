# -*- coding: utf-8 -*-
from __future__ import annotations

import traceback
import uuid
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from flask import request
from sqlalchemy import func, case, text

from config_app import cache, db
from models import StaffAuditLog, StaffUser, Solicitud, SolicitudCandidata
from utils.audit_logger import log_action
from utils.matching_service import rank_candidates
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
    try:
        return cache.get(key)
    except Exception:
        return default


def _safe_cache_set(key: str, value, timeout: int):
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception:
        return False


def _safe_cache_delete(key: str):
    try:
        cache.delete(key)
    except Exception:
        pass


def _safe_cache_add(key: str, value, timeout: int) -> bool:
    try:
        if cache.get(key):
            return False
        cache.set(key, value, timeout=timeout)
        return True
    except Exception:
        return True


def _append_index(index_key: str, item: str, timeout: int = 3600):
    idx = _safe_cache_get(index_key, default=[]) or []
    idx = [x for x in idx if isinstance(x, str) and x]
    if item not in idx:
        idx.append(item)
    if len(idx) > 2000:
        idx = idx[-2000:]
    _safe_cache_set(index_key, idx, timeout=timeout)


def telegram_channel_config() -> dict[str, Any]:
    cached = _safe_cache_get(TELEGRAM_CFG_CACHE_KEY, default={}) or {}
    token = str(cached.get("token") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(cached.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
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


def _send_telegram_message(text: str) -> tuple[bool, str]:
    cfg = telegram_channel_config()
    if not cfg.get("enabled"):
        return False, "Canal desactivado"
    token = str(cfg.get("token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()
    if not token or not chat_id:
        return False, "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID"

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": (text or "")[:3900],
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=6) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            if 200 <= code < 300:
                return True, "ok"
        return False, "Respuesta no exitosa de Telegram"
    except Exception as exc:
        return False, str(exc)


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
    current = _safe_cache_get(key, default=None)

    if not isinstance(current, dict) or not current.get("owner_user_id"):
        payload = _lock_owner_payload(user, entity_type=et, entity_id=eid, current_path=current_path)
        _safe_cache_set(key, payload, timeout=LOCK_TTL_SECONDS + 8)
        _append_index(LOCK_INDEX_KEY, key, timeout=LOCK_TTL_SECONDS * 40)
        return {"ok": True, "state": "owner", "lock": payload}

    owner_id = int(current.get("owner_user_id") or 0)
    last_seen = _parse_dt(current.get("last_seen")) or now
    age = max(0, int((now - last_seen).total_seconds()))

    if owner_id == int(user.id):
        current["last_seen"] = _dt_iso(now)
        current["expires_at"] = _dt_iso(now + timedelta(seconds=LOCK_TTL_SECONDS))
        current["current_path"] = (current_path or current.get("current_path") or "")[:255]
        _safe_cache_set(key, current, timeout=LOCK_TTL_SECONDS + 8)
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
    before = _safe_cache_get(key, default=None)
    payload = _lock_owner_payload(user, entity_type=et, entity_id=eid)
    _safe_cache_set(key, payload, timeout=LOCK_TTL_SECONDS + 8)
    _append_index(LOCK_INDEX_KEY, key, timeout=LOCK_TTL_SECONDS * 40)

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
    idx = _safe_cache_get(LOCK_INDEX_KEY, default=[]) or []
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    clean_idx: list[str] = []
    for key in idx:
        if not isinstance(key, str) or not key:
            continue
        payload = _safe_cache_get(key, default=None)
        if not isinstance(payload, dict):
            continue
        clean_idx.append(key)
        last_seen = _parse_dt(payload.get("last_seen")) or now
        age = max(0, int((now - last_seen).total_seconds()))
        row = dict(payload)
        row["held_for_sec"] = age
        row["active"] = (age <= LOCK_TTL_SECONDS)
        rows.append(row)
    _safe_cache_set(LOCK_INDEX_KEY, clean_idx[-2000:], timeout=LOCK_TTL_SECONDS * 40)
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
    current_rev = int(_safe_cache_get(rev_key, default=1) or 1)
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

    payload = _safe_cache_get(_session_key(token), default={}) or {}
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
    _safe_cache_set(_session_key(token), payload, timeout=SESSION_TTL_SECONDS)
    _append_index(SESSION_INDEX_KEY, token, timeout=SESSION_TTL_SECONDS * 4)
    return {"ok": True, "token": token, "payload": payload}


def list_active_sessions() -> list[dict[str, Any]]:
    idx = _safe_cache_get(SESSION_INDEX_KEY, default=[]) or []
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    clean: list[str] = []
    for token in idx:
        if not isinstance(token, str) or not token:
            continue
        payload = _safe_cache_get(_session_key(token), default=None)
        if not isinstance(payload, dict):
            continue
        clean.append(token)
        last_seen = _parse_dt(payload.get("last_seen"))
        age = max(0, int((now - last_seen).total_seconds())) if last_seen else 999999
        row = dict(payload)
        row["last_seen_seconds"] = age
        rows.append(row)
    _safe_cache_set(SESSION_INDEX_KEY, clean[-4000:], timeout=SESSION_TTL_SECONDS * 4)
    rows.sort(key=lambda x: (int(x.get("last_seen_seconds") or 999999), str(x.get("username") or "")))
    return rows


def close_user_sessions(*, actor: StaffUser, user_id: int, reason: str = "") -> None:
    uid = int(user_id)
    rev_key = _session_rev_key(uid)
    rev = int(_safe_cache_get(rev_key, default=1) or 1) + 1
    _safe_cache_set(rev_key, rev, timeout=SESSION_TTL_SECONDS * 8)
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
                "route": (route or request.path if request else "")[:255],
                "request_id": (request_id or request.headers.get("X-Request-ID") if request else "")[:120],
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

    if (not success) and at in {"LOGIN", "ADMIN_LOGIN", "CLIENTE_LOGIN", "STAFF_LOGIN_FAIL"}:
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
    stream_ok = True
    try:
        _safe_cache_set("enterprise:health_ping", 1, timeout=20)
        stream_ok = bool(_safe_cache_get("enterprise:health_ping", default=0))
    except Exception:
        stream_ok = False

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
