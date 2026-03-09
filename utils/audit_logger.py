# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Callable

from flask import g, has_request_context, request, session
from flask_login import current_user

from config_app import db
from models import StaffAuditLog, StaffUser
from utils.timezone import utc_now_naive


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SENSITIVE_KEY_TOKENS = {
    "password",
    "password_hash",
    "password_confirm",
    "clave",
    "token",
    "csrf",
    "secret",
    "telefono",
    "phone",
    "whatsapp",
    "celular",
    "movil",
    "cedula",
    "dni",
    "documento",
    "direccion",
    "address",
    "email",
    "correo",
}


def _looks_sensitive_key(key: str | None) -> bool:
    txt = str(key or "").strip().lower()
    if not txt:
        return False
    return any(token in txt for token in _SENSITIVE_KEY_TOKENS)


def _mask_email(value: str) -> str:
    raw = str(value or "").strip()
    if "@" not in raw:
        return "<redacted>"
    user, _, domain = raw.partition("@")
    if not user:
        return "<redacted>"
    return f"{user[:1]}***@{domain}"


def _mask_digits(value: str, keep: int = 2) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return "<redacted>"
    tail = digits[-keep:] if keep > 0 else ""
    return f"***{tail}"


def _mask_sensitive_value(key: str, value: Any) -> Any:
    k = str(key or "").lower()
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return [_mask_sensitive_value(k, v) for v in value]
    if isinstance(value, dict):
        return {str(sub_k): _mask_sensitive_value(str(sub_k), sub_v) for sub_k, sub_v in value.items()}

    raw = str(value)
    if any(x in k for x in ("password", "clave", "token", "csrf", "secret")):
        return "<hidden>"
    if any(x in k for x in ("email", "correo")):
        return _mask_email(raw)
    if any(x in k for x in ("cedula", "dni", "documento")):
        return _mask_digits(raw, keep=3)
    if any(x in k for x in ("telefono", "phone", "whatsapp", "celular", "movil")):
        return _mask_digits(raw, keep=2)
    if any(x in k for x in ("direccion", "address")):
        return "<redacted_address>"
    return "<redacted>"


def _sanitize_audit_payload(value: Any, *, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _looks_sensitive_key(key):
                out[key] = _mask_sensitive_value(key, v)
            else:
                out[key] = _sanitize_audit_payload(v, parent_key=key)
        return out

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_audit_payload(v, parent_key=parent_key) for v in value]

    if _looks_sensitive_key(parent_key):
        return _mask_sensitive_value(parent_key or "", value)
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def _actor_from_context() -> tuple[int | None, str | None]:
    user_id = None
    role = None
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            role = (getattr(current_user, "role", None) or getattr(current_user, "rol", None) or "").strip().lower() or None
            if isinstance(current_user, StaffUser):
                user_id = int(current_user.id)
            else:
                uid = str(current_user.get_id() or "").strip()
                if uid.startswith("staff:"):
                    raw = uid.split(":", 1)[1].strip()
                    if raw.isdigit():
                        user_id = int(raw)
    except Exception:
        user_id = None

    if role is None:
        role = (session.get("role") or "").strip().lower() or None

    return user_id, role


def is_staff_actor() -> bool:
    _, role = _actor_from_context()
    return role in {"owner", "admin", "secretaria"}


def log_action(
    action_type: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    if not action_type:
        return

    route = method = ip = user_agent = None
    if has_request_context():
        route = (request.path or "")[:255] or None
        method = (request.method or "")[:10] or None
        ip = (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Real-IP") or request.remote_addr or "")[:64] or None
        user_agent = (request.headers.get("User-Agent") or "")[:512] or None

    actor_user_id, actor_role = _actor_from_context()

    payload = {
        "created_at": utc_now_naive(),
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "action_type": (action_type or "")[:80],
        "entity_type": (entity_type or "")[:80] or None,
        "entity_id": str(entity_id)[:64] if entity_id is not None else None,
        "route": route,
        "method": method,
        "ip": ip,
        "user_agent": user_agent,
        "summary": (summary or "")[:255] or None,
        "metadata_json": _json_safe(_sanitize_audit_payload(metadata or {})),
        "changes_json": _json_safe(_sanitize_audit_payload(changes)) if changes is not None else None,
        "success": bool(success),
        "error_message": (error or "")[:4000] or None,
    }

    try:
        with db.engine.begin() as conn:
            conn.execute(StaffAuditLog.__table__.insert().values(**payload))
        if has_request_context():
            g._staff_audit_logged = True
        try:
            from utils.enterprise_layer import evaluate_security_anomalies

            evaluate_security_anomalies(
                action_type=payload.get("action_type") or "",
                success=bool(payload.get("success")),
                actor_user_id=payload.get("actor_user_id"),
                route=payload.get("route"),
                changes=payload.get("changes_json"),
                metadata=payload.get("metadata_json"),
            )
        except Exception:
            pass
    except Exception:
        return


def audit(action_type: str, entity_resolver: Callable[..., dict[str, Any] | None] | None = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                resp = fn(*args, **kwargs)
            except Exception as exc:
                data = entity_resolver(*args, **kwargs) if entity_resolver else {}
                data = data or {}
                log_action(
                    action_type=action_type,
                    entity_type=data.get("entity_type"),
                    entity_id=data.get("entity_id"),
                    summary=data.get("summary") or action_type,
                    metadata=data.get("metadata") or {},
                    changes=data.get("changes"),
                    success=False,
                    error=str(exc),
                )
                raise

            data = entity_resolver(*args, **kwargs) if entity_resolver else {}
            data = data or {}
            log_action(
                action_type=action_type,
                entity_type=data.get("entity_type"),
                entity_id=data.get("entity_id"),
                summary=data.get("summary") or action_type,
                metadata=data.get("metadata") or {},
                changes=data.get("changes"),
                success=True,
            )
            return resp

        return wrapper

    return deco


def log_staff_post_fallback(response):
    if not has_request_context():
        return response
    if request.method not in MUTATING_METHODS:
        return response
    if (request.path or "").startswith("/clientes/"):
        return response
    if request.path in {"/admin/login", "/admin/logout"}:
        return response
    if not is_staff_actor():
        return response
    if bool(getattr(g, "_staff_audit_logged", False)):
        return response

    status = int(getattr(response, "status_code", 0) or 0)
    log_action(
        action_type="STAFF_POST",
        summary=f"{request.method} {request.path} -> {status}",
        metadata={"status_code": status},
        success=(status < 400),
        error=None if status < 400 else f"HTTP {status}",
    )
    return response


def snapshot_model_fields(obj: Any, fields: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        value = getattr(obj, field, None)
        if isinstance(value, (bytes, bytearray)):
            continue
        out[field] = _json_safe(value)
    return out


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys):
        if before.get(key) != after.get(key):
            diff[key] = {"from": before.get(key), "to": after.get(key)}
    return diff
