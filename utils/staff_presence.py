# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from config_app import db
from models import StaffPresenceState
from utils.timezone import parse_iso_utc, utc_now_naive

PRESENCE_TOUCH_MIN_SECONDS = 3
PRESENCE_RECENT_MAX_AGE_SECONDS = 65 * 3
_EXISTING_ROW_UNSET = object()


def _norm_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[: max(0, int(limit))]


def _norm_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    txt = str(value).strip().lower()
    if txt in {"1", "true", "yes", "on"}:
        return True
    if txt in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _norm_client_status(value: Any) -> str:
    txt = _norm_text(value, 20).lower()
    if txt in {"active", "idle", "hidden"}:
        return txt
    return "active"


def _norm_dt(value: Any) -> datetime | None:
    raw = _norm_text(value, 40)
    if not raw:
        return None
    dt = parse_iso_utc(raw)
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _norm_entity_type(value: Any) -> str:
    txt = _norm_text(value, 40).lower()
    if txt in {"candidatas", "candidate"}:
        return "candidata"
    if txt in {"solicitudes", "request"}:
        return "solicitud"
    if txt in {"clientes", "client"}:
        return "cliente"
    return txt


def _state_hash(payload: dict[str, Any]) -> str:
    interaction_raw = payload.get("last_interaction_at")
    if isinstance(interaction_raw, datetime):
        interaction_for_hash = interaction_raw.isoformat(timespec="seconds")
    else:
        interaction_for_hash = _norm_text(interaction_raw, 40)
    stable = {
        "route": payload.get("route", ""),
        "route_label": payload.get("route_label", ""),
        "entity_type": payload.get("entity_type", ""),
        "entity_id": payload.get("entity_id", ""),
        "entity_name": payload.get("entity_name", ""),
        "entity_code": payload.get("entity_code", ""),
        "current_action": payload.get("current_action", ""),
        "action_label": payload.get("action_label", ""),
        "tab_visible": bool(payload.get("tab_visible", True)),
        "is_idle": bool(payload.get("is_idle", False)),
        "is_typing": bool(payload.get("is_typing", False)),
        "has_unsaved_changes": bool(payload.get("has_unsaved_changes", False)),
        "modal_open": bool(payload.get("modal_open", False)),
        "lock_owner": payload.get("lock_owner", ""),
        "client_status": payload.get("client_status", "active"),
        "page_title": payload.get("page_title", ""),
        "last_interaction_at": interaction_for_hash,
    }
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_presence_snapshot(payload: dict[str, Any] | None, *, fallback_route: str = "") -> dict[str, Any]:
    data = dict(payload or {})
    client_status = _norm_client_status(data.get("client_status"))
    tab_visible = _norm_bool(data.get("tab_visible"), default=(client_status != "hidden"))
    is_idle = _norm_bool(data.get("is_idle"), default=(client_status == "idle"))

    snapshot = {
        "route": _norm_text(data.get("route") or data.get("current_path") or fallback_route, 255),
        "route_label": _norm_text(data.get("route_label"), 120),
        "entity_type": _norm_entity_type(data.get("entity_type")),
        "entity_id": _norm_text(data.get("entity_id"), 64),
        "entity_name": _norm_text(data.get("entity_name"), 160),
        "entity_code": _norm_text(data.get("entity_code"), 64),
        "current_action": _norm_text(data.get("current_action") or data.get("action_hint"), 80).lower(),
        "action_label": _norm_text(data.get("action_label"), 120),
        "tab_visible": tab_visible,
        "is_idle": is_idle,
        "is_typing": _norm_bool(data.get("is_typing"), default=False),
        "has_unsaved_changes": _norm_bool(data.get("has_unsaved_changes"), default=False),
        "modal_open": _norm_bool(data.get("modal_open"), default=False),
        "lock_owner": _norm_text(data.get("lock_owner"), 120),
        "client_status": client_status,
        "page_title": _norm_text(data.get("page_title"), 160),
        "last_interaction_at": _norm_dt(data.get("last_interaction_at")),
        "ip": _norm_text(data.get("ip"), 64),
        "user_agent": _norm_text(data.get("user_agent"), 255),
    }
    snapshot["state_hash"] = _state_hash(snapshot)
    return snapshot


def upsert_staff_presence_snapshot(
    *,
    user_id: int,
    session_id: str,
    snapshot: dict[str, Any],
    now: datetime | None = None,
    touch_min_seconds: int = PRESENCE_TOUCH_MIN_SECONDS,
    existing_row: StaffPresenceState | object = _EXISTING_ROW_UNSET,
) -> dict[str, Any]:
    ts = now or utc_now_naive()
    sid = _norm_text(session_id, 120)
    if int(user_id or 0) <= 0 or not sid:
        return {"ok": False, "write_kind": "error", "reason": "invalid_identity"}

    row: StaffPresenceState | None
    if existing_row is _EXISTING_ROW_UNSET:
        row = (
            StaffPresenceState.query
            .filter(
                StaffPresenceState.user_id == int(user_id),
                StaffPresenceState.session_id == sid,
            )
            .first()
        )
    else:
        row = existing_row if isinstance(existing_row, StaffPresenceState) else None
        if row is not None:
            row_user_id = int(getattr(row, "user_id", 0) or 0)
            row_session_id = _norm_text(getattr(row, "session_id", ""), 120)
            if row_user_id != int(user_id) or row_session_id != sid:
                row = None

    state_hash = _norm_text(snapshot.get("state_hash"), 64)
    write_kind = "noop"
    if row is None:
        row = StaffPresenceState(
            user_id=int(user_id),
            session_id=sid,
            started_at=ts,
            last_seen_at=ts,
            updated_at=ts,
        )
        write_kind = "insert"
    elif _norm_text(getattr(row, "state_hash", ""), 64) != state_hash:
        write_kind = "update"
    else:
        seen_at = getattr(row, "last_seen_at", None)
        should_touch = seen_at is None
        if seen_at is not None:
            delta = max(0, int((ts - seen_at).total_seconds()))
            should_touch = (delta >= max(1, int(touch_min_seconds or 1)))
        if should_touch:
            row.last_seen_at = ts
            write_kind = "touch"

    if write_kind in {"insert", "update"}:
        row.route = _norm_text(snapshot.get("route"), 255)
        row.route_label = _norm_text(snapshot.get("route_label"), 120)
        row.entity_type = _norm_entity_type(snapshot.get("entity_type"))
        row.entity_id = _norm_text(snapshot.get("entity_id"), 64)
        row.entity_name = _norm_text(snapshot.get("entity_name"), 160)
        row.entity_code = _norm_text(snapshot.get("entity_code"), 64)
        row.current_action = _norm_text(snapshot.get("current_action"), 80).lower()
        row.action_label = _norm_text(snapshot.get("action_label"), 120)
        row.tab_visible = _norm_bool(snapshot.get("tab_visible"), default=True)
        row.is_idle = _norm_bool(snapshot.get("is_idle"), default=False)
        row.is_typing = _norm_bool(snapshot.get("is_typing"), default=False)
        row.has_unsaved_changes = _norm_bool(snapshot.get("has_unsaved_changes"), default=False)
        row.modal_open = _norm_bool(snapshot.get("modal_open"), default=False)
        row.lock_owner = _norm_text(snapshot.get("lock_owner"), 120)
        row.client_status = _norm_client_status(snapshot.get("client_status"))
        row.page_title = _norm_text(snapshot.get("page_title"), 160)
        row.last_interaction_at = _norm_dt(snapshot.get("last_interaction_at")) if not isinstance(snapshot.get("last_interaction_at"), datetime) else snapshot.get("last_interaction_at")
        row.state_hash = state_hash
        row.ip = _norm_text(snapshot.get("ip"), 64)
        row.user_agent = _norm_text(snapshot.get("user_agent"), 255)
        row.last_seen_at = ts
        row.updated_at = ts

    if write_kind != "noop":
        db.session.add(row)
        db.session.commit()

    return {"ok": True, "write_kind": write_kind, "row": row}


def list_recent_staff_presence_states(*, max_age_seconds: int = PRESENCE_RECENT_MAX_AGE_SECONDS) -> list[StaffPresenceState]:
    horizon = utc_now_naive() - timedelta(seconds=max(1, int(max_age_seconds or 1)))
    return (
        StaffPresenceState.query
        .filter(StaffPresenceState.last_seen_at >= horizon)
        .order_by(StaffPresenceState.user_id.asc(), StaffPresenceState.last_seen_at.desc(), StaffPresenceState.id.desc())
        .all()
    )
