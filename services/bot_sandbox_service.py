from __future__ import annotations

import hashlib
import os
import random
from datetime import timedelta
from typing import Any

from sqlalchemy import func

from config_app import db
from models import BotConversation, BotMessage, BotSandboxOutbound, BotSetting
from services.bot_constants import MESSAGE_STATUS_OUTBOUND_FAILED, MESSAGE_STATUS_OUTBOUND_SENT
from services.bot_observability_service import log_bot_blocked, log_bot_event
from utils.timezone import utc_now_naive


_TRUE_SET = {"1", "true", "yes", "on"}

OUTBOX_STATUS_QUEUED = "queued"
OUTBOX_STATUS_PROCESSING = "processing"
OUTBOX_STATUS_SIMULATED_SENT = "simulated_sent"
OUTBOX_STATUS_BLOCKED = "blocked"
OUTBOX_STATUS_FAILED = "failed"
OUTBOX_STATUSES = {
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_PROCESSING,
    OUTBOX_STATUS_SIMULATED_SENT,
    OUTBOX_STATUS_BLOCKED,
    OUTBOX_STATUS_FAILED,
}
_RETRY_BACKOFF_SECONDS = 0

REAL_SANDBOX_SETTING_KEY = "bot_real_whatsapp_sandbox_paused"
AUTO_REPLY_PAUSED_SETTING_KEY = "bot_sandbox_auto_reply_paused"


class SandboxSafetyError(RuntimeError):
    pass


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_SET


def _mask_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    if not raw:
        return ""
    digits = "".join([c for c in raw if c.isdigit()])
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"***{digits[-4:]}"


def _safe_int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        parsed = int(default)
    if parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def _safe_float_env(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        parsed = float(default)
    if parsed < minimum:
        parsed = minimum
    if parsed > maximum:
        parsed = maximum
    return parsed


def _retry_backoff_seconds() -> int:
    return _safe_int_env("BOT_SANDBOX_RETRY_BACKOFF_SECONDS", _RETRY_BACKOFF_SECONDS, minimum=0, maximum=300)


def _provider_name() -> str:
    provider = str(os.getenv("BOT_REAL_WHATSAPP_PROVIDER", "fake") or "fake").strip().lower()
    if provider in {"meta", "meta_sandbox", "meta-sandbox"}:
        return "meta_sandbox"
    if provider in {"twilio", "twilio_sandbox", "twilio-sandbox"}:
        return "twilio_sandbox"
    if provider in {"fake", "sandbox", "local"}:
        return "fake"
    return provider


def _allowed_numbers() -> set[str]:
    raw = str(os.getenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "") or "")
    rows = [x.strip() for x in raw.replace(";", ",").split(",")]
    return {r for r in rows if r.startswith("+") and len(r) >= 8}


def _provider_allowed_in_real_sandbox(provider: str) -> bool:
    return str(provider or "").strip().lower() in {"meta_sandbox", "twilio_sandbox", "fake"}


def _setting_bool(key: str, default: bool) -> bool:
    row = BotSetting.query.filter_by(key=str(key)).first()
    if row is None:
        return bool(default)
    val = row.value_json
    if isinstance(val, dict):
        if "value" in val:
            return _is_true(val.get("value"))
        if "enabled" in val:
            return _is_true(val.get("enabled"))
    return bool(default)


def set_real_sandbox_paused(*, paused: bool, actor_id: int | None = None) -> None:
    row = BotSetting.query.filter_by(key=REAL_SANDBOX_SETTING_KEY).first()
    if row is None:
        row = BotSetting(
            key=REAL_SANDBOX_SETTING_KEY,
            description="Kill switch instantaneo para BOT real sandbox (owner only).",
            value_json={"value": bool(paused)},
            updated_by_staff_user_id=actor_id,
        )
        db.session.add(row)
    else:
        row.value_json = {"value": bool(paused)}
        row.updated_by_staff_user_id = actor_id
    db.session.flush()


def is_real_sandbox_paused() -> bool:
    return _setting_bool(REAL_SANDBOX_SETTING_KEY, False)


def set_sandbox_auto_reply_paused(*, paused: bool, actor_id: int | None = None) -> None:
    row = BotSetting.query.filter_by(key=AUTO_REPLY_PAUSED_SETTING_KEY).first()
    if row is None:
        row = BotSetting(
            key=AUTO_REPLY_PAUSED_SETTING_KEY,
            description="Kill switch para auto-reply sandbox controlado.",
            value_json={"value": bool(paused)},
            updated_by_staff_user_id=actor_id,
        )
        db.session.add(row)
    else:
        row.value_json = {"value": bool(paused)}
        row.updated_by_staff_user_id = actor_id
    db.session.flush()


def is_sandbox_auto_reply_paused() -> bool:
    return _setting_bool(AUTO_REPLY_PAUSED_SETTING_KEY, False)


def is_staging_mode() -> bool:
    return _is_true(os.getenv("BOT_STAGING_MODE", "false"))


def is_sandbox_mode() -> bool:
    return _is_true(os.getenv("BOT_SANDBOX_MODE", "false"))


def is_staging_offline_active() -> bool:
    return is_staging_mode() and is_sandbox_mode()


def is_real_whatsapp_sandbox_enabled() -> bool:
    return _is_true(os.getenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "false"))


def is_real_whatsapp_sandbox_owner_only_active() -> bool:
    if not is_real_whatsapp_sandbox_enabled():
        return False
    if is_staging_offline_active():
        return False
    if _is_true(os.getenv("BOT_STAGING_MODE", "false")) and _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        return False
    if not _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        return False
    if not _real_review_required():
        return False
    if not _real_owner_only():
        return False
    return _provider_name() in {"meta_sandbox", "twilio_sandbox"}


def is_sandbox_auto_reply_enabled() -> bool:
    return _is_true(os.getenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "false"))


def is_sandbox_auto_reply_active() -> bool:
    if not is_sandbox_auto_reply_enabled():
        return False
    if is_sandbox_auto_reply_paused():
        return False
    if not is_real_whatsapp_sandbox_enabled():
        return False
    if not _real_owner_only():
        return False
    if _provider_name() != "meta_sandbox":
        return False
    if not _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        return False
    if _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE", "true")):
        return False
    if _is_true(os.getenv("BOT_DRY_RUN", "true")):
        return False
    env = str(os.getenv("APP_ENV", "development") or "development").strip().lower()
    if env in {"production", "prod"}:
        return False
    return True


def is_sandbox_assistant_allowed() -> bool:
    if is_staging_offline_active() and not _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        return True
    return is_real_whatsapp_sandbox_owner_only_active()


def _real_review_required() -> bool:
    return _is_true(os.getenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true"))


def _real_owner_only() -> bool:
    return _is_true(os.getenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true"))


def _kill_switch_reason() -> str | None:
    provider = _provider_name()
    if is_real_sandbox_paused():
        return "real_sandbox_paused"
    if not is_real_whatsapp_sandbox_enabled():
        return "real_sandbox_disabled"
    if not _real_review_required():
        return "manual_review_must_be_true"
    if not _real_owner_only():
        return "owner_only_must_be_true"
    if provider not in {"meta_sandbox", "twilio_sandbox", "fake"}:
        return "provider_mismatch"
    if provider != "fake" and not _provider_allowed_in_real_sandbox(provider):
        return "provider_not_sandbox"
    if _is_true(os.getenv("WHATSAPP_ENABLED", "false")) and is_staging_offline_active():
        return "offline_mode_blocks_real"
    if is_sandbox_auto_reply_enabled() and not _real_owner_only():
        return "auto_reply_requires_owner_only"
    if is_sandbox_auto_reply_enabled() and _provider_name() != "meta_sandbox":
        return "auto_reply_requires_meta_sandbox"
    return None


def assert_staging_offline_security(*, conversation: BotConversation | None = None, provider: str | None = None) -> None:
    if not is_staging_offline_active():
        return
    if _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        raise SandboxSafetyError("sandbox_security_block:whatsapp_enabled_true")
    if provider and str(provider).strip().lower() not in {"fake", "sandbox", "local"}:
        raise SandboxSafetyError("sandbox_security_block:real_provider_detected")
    if conversation is not None:
        meta = dict(getattr(conversation, "metadata_json", {}) or {})
        if not bool(meta.get("sandbox_conversation", False)):
            raise SandboxSafetyError("sandbox_security_block:conversation_not_sandbox")


def assert_no_real_outbound_allowed(*, conversation: BotConversation | None = None, provider: str | None = None) -> None:
    assert_staging_offline_security(conversation=conversation, provider=provider)
    if is_staging_offline_active():
        raise SandboxSafetyError("sandbox_security_block:real_outbound_attempted")


def _safe_phone(phone: str) -> bool:
    value = str(phone or "").strip()
    return value.startswith("+1999")


def _is_allowlisted_real_number(phone: str) -> bool:
    return str(phone or "").strip() in _allowed_numbers()


def _count_recent_real_attempts(phone: str, *, window_seconds: int = 60) -> int:
    since = utc_now_naive() - timedelta(seconds=max(1, int(window_seconds)))
    return int(
        db.session.query(func.count(BotSandboxOutbound.id))
        .filter(BotSandboxOutbound.phone_e164 == str(phone or "").strip())
        .filter(BotSandboxOutbound.created_at >= since)
        .scalar()
        or 0
    )


def mark_conversation_as_sandbox(conversation: BotConversation) -> None:
    meta = dict(getattr(conversation, "metadata_json", {}) or {})
    meta["sandbox_conversation"] = True
    conversation.metadata_json = meta


def _state_transition_allowed(current: str, nxt: str) -> bool:
    current = str(current or "").strip().lower()
    nxt = str(nxt or "").strip().lower()
    if current not in OUTBOX_STATUSES or nxt not in OUTBOX_STATUSES:
        return False
    if current == OUTBOX_STATUS_QUEUED:
        return nxt in {OUTBOX_STATUS_PROCESSING, OUTBOX_STATUS_BLOCKED, OUTBOX_STATUS_FAILED}
    if current == OUTBOX_STATUS_PROCESSING:
        return nxt in {OUTBOX_STATUS_SIMULATED_SENT, OUTBOX_STATUS_FAILED, OUTBOX_STATUS_BLOCKED, OUTBOX_STATUS_QUEUED}
    if current == OUTBOX_STATUS_FAILED:
        return nxt in {OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_BLOCKED}
    return False


def _normalize_corrupt_row(row: BotSandboxOutbound, *, now=None) -> bool:
    now = now or utc_now_naive()
    changed = False
    if row.state not in OUTBOX_STATUSES:
        row.state = OUTBOX_STATUS_FAILED
        row.failure_reason = "corrupt_state"
        row.failed_at = now
        row.last_transition_at = now
        changed = True
    if int(row.retry_count or 0) < 0:
        row.retry_count = 0
        changed = True
    if row.processing_at is not None and row.queued_at is not None and row.processing_at < row.queued_at:
        row.processing_at = row.queued_at
        changed = True
    if row.last_transition_at is not None and row.queued_at is not None and row.last_transition_at < row.queued_at:
        row.last_transition_at = row.queued_at
        changed = True
    if row.next_retry_at is not None and row.queued_at is not None and row.next_retry_at < row.queued_at:
        row.next_retry_at = row.queued_at
        changed = True
    return changed


def recover_orphan_processing_rows() -> int:
    timeout_seconds = _safe_int_env("BOT_SANDBOX_PROCESSING_TIMEOUT_SECONDS", 30, minimum=1, maximum=3600)
    threshold = utc_now_naive() - timedelta(seconds=timeout_seconds)
    rows = (
        BotSandboxOutbound.query.filter(BotSandboxOutbound.state == OUTBOX_STATUS_PROCESSING)
        .filter(
            (BotSandboxOutbound.processing_at.is_(None))
            | (BotSandboxOutbound.processing_at < threshold)
        )
        .all()
    )
    recovered = 0
    for row in rows:
        row.retry_count = int(row.retry_count or 0) + 1
        row.failed_at = utc_now_naive()
        row.failure_reason = "orphan_processing_recovered"
        row.state = OUTBOX_STATUS_FAILED
        row.last_transition_at = row.failed_at
        row.next_retry_at = row.failed_at + timedelta(seconds=_retry_backoff_seconds())
        recovered += 1
    if recovered:
        db.session.commit()
    return recovered


def enqueue_sandbox_outbound(
    *,
    conversation: BotConversation,
    message: BotMessage,
    provider: str = "fake",
    metadata: dict[str, Any] | None = None,
) -> BotSandboxOutbound:
    metadata = dict(metadata or {})
    mode = str(metadata.get("mode") or "offline").strip().lower()
    requested_provider = str(provider or "fake").strip().lower()
    if mode == "real_sandbox":
        reason = _kill_switch_reason()
        if reason:
            raise SandboxSafetyError(f"real_sandbox_kill_switch:{reason}")
        allowlisted = _is_allowlisted_real_number(conversation.phone_e164)
        if not allowlisted:
            raise SandboxSafetyError("real_sandbox_allowlist_blocked")
        if not str(message.text_body or "").strip():
            raise SandboxSafetyError("real_sandbox_empty_message")
        auto_send_allowed = bool(metadata.get("auto_send_allowed", False))
        auto_send_guard_ok = is_sandbox_auto_reply_active()
        if _real_review_required() and (not auto_send_allowed) and not bool(metadata.get("review_approved", False)):
            raise SandboxSafetyError("real_sandbox_review_required")
        review_id = int(metadata.get("review_id") or 0)
        approved_by = int(metadata.get("approved_by") or 0)
        reviewer = int(metadata.get("reviewer") or 0)
        if review_id <= 0:
            raise SandboxSafetyError("real_sandbox_review_id_required")
        if (not auto_send_allowed) and (approved_by <= 0 or reviewer <= 0):
            raise SandboxSafetyError("real_sandbox_reviewer_required")
        if (not auto_send_allowed) and (not bool(metadata.get("manual_review_required", False))):
            raise SandboxSafetyError("real_sandbox_manual_review_required")
        if not bool(metadata.get("owner_only", False)):
            raise SandboxSafetyError("real_sandbox_owner_only_required")
        if auto_send_allowed and not auto_send_guard_ok:
            raise SandboxSafetyError("real_sandbox_auto_send_guard_blocked")
        max_per_min = _safe_int_env("BOT_REAL_WHATSAPP_MAX_PER_MIN", 6, minimum=1, maximum=120)
        if _count_recent_real_attempts(conversation.phone_e164, window_seconds=60) >= max_per_min:
            raise SandboxSafetyError("real_sandbox_rate_limit")
        provider = _provider_name()
        if requested_provider not in {"", "fake", "meta_sandbox", "twilio_sandbox", provider}:
            raise SandboxSafetyError("real_sandbox_provider_mismatch")
    else:
        assert_staging_offline_security(conversation=conversation, provider=requested_provider)
        if not _safe_phone(conversation.phone_e164):
            raise SandboxSafetyError("sandbox_security_block:real_phone_detected")
        provider = "fake"

    log_bot_event(
        "outbound_enqueue_started",
        metadata={
            "conversation_id": int(conversation.id),
            "provider": requested_provider,
            "simulate": _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE", "true")),
            "phone_e164": str(conversation.phone_e164 or ""),
        },
    )
    existing = BotSandboxOutbound.query.filter_by(bot_message_id=message.id).first()
    if existing:
        return existing

    now = utc_now_naive()
    row = BotSandboxOutbound(
        conversation_id=conversation.id,
        bot_message_id=message.id,
        phone_e164=conversation.phone_e164,
        provider=provider,
        outbound_http_status=None,
        outbound_meta_error_code=None,
        outbound_meta_error_message=None,
        outbound_response_raw=None,
        state=OUTBOX_STATUS_QUEUED,
        payload_json={
            "text": str(message.text_body or ""),
            "metadata": metadata,
            "audit": {
                "request_payload": {"to_masked": _mask_phone(str(conversation.phone_e164 or "")), "text": str(message.text_body or "")},
                "response_payload": None,
                "delivery": {"status": "queued", "updates": []},
                "provider_message_id": None,
                "reviewer": metadata.get("reviewer"),
                "approved_by": metadata.get("approved_by"),
                "review_id": metadata.get("review_id"),
                "provider": provider,
                "sandbox_mode": bool(mode == "real_sandbox"),
                "allowlisted": bool(_is_allowlisted_real_number(conversation.phone_e164)),
                "production_mode": False,
                "real_public_send": False,
                "to_masked": _mask_phone(str(conversation.phone_e164 or "")),
                "fail_reason": None,
            },
        },
        queued_at=now,
        last_transition_at=now,
    )
    db.session.add(row)
    db.session.flush()
    log_bot_event(
        "outbound_enqueue_created",
        metadata={
            "outbox_id": int(row.id),
            "provider": provider,
            "simulate": _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE", "true")),
            "phone_e164": str(conversation.phone_e164 or ""),
        },
    )
    log_bot_event("sandbox_outbox.enqueued", metadata={"outbox_id": row.id, "conversation_id": conversation.id, "provider": provider})
    return row


def _set_state(row: BotSandboxOutbound, nxt: str, *, reason: str | None = None) -> None:
    if not _state_transition_allowed(str(row.state), nxt):
        raise SandboxSafetyError(f"sandbox_illegal_transition:{row.state}->{nxt}")
    row.state = nxt
    row.last_transition_at = utc_now_naive()
    if nxt == OUTBOX_STATUS_PROCESSING:
        row.processing_at = row.last_transition_at
    if nxt == OUTBOX_STATUS_SIMULATED_SENT:
        row.simulated_sent_at = row.last_transition_at
        row.failure_reason = None
    if nxt in {OUTBOX_STATUS_BLOCKED, OUTBOX_STATUS_FAILED}:
        row.failure_reason = str(reason or "unknown")[:255]
        if nxt == OUTBOX_STATUS_BLOCKED:
            row.blocked_at = row.last_transition_at
        if nxt == OUTBOX_STATUS_FAILED:
            row.failed_at = row.last_transition_at


def _payload_audit(row: BotSandboxOutbound) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    audit = dict(payload.get("audit") or {})
    payload["audit"] = audit
    row.payload_json = payload
    return audit


def _provider_send(provider: str, *, to_phone: str, text: str, timeout_seconds: int) -> dict[str, Any]:
    # Adapter controlado: no envia real si provider=fake.
    if provider == "fake":
        return {"ok": True, "status": "sent", "provider_message_id": f"fake-{hashlib.md5((to_phone + text).encode()).hexdigest()[:12]}", "raw": {"provider": "fake"}}
    if provider == "meta_sandbox":
        simulated = _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE", "true"))
        if simulated:
            return {"ok": True, "status": "sent", "provider_message_id": f"meta-sandbox-{hashlib.md5(text.encode()).hexdigest()[:10]}", "raw": {"provider": "meta_sandbox", "simulated": True}}
        from services.whatsapp_cloud_service import send_text_message

        log_bot_event(
            "meta_send_request_started",
            metadata={
                "endpoint": "delegated_to_whatsapp_cloud_service",
                "phone_number_id": str(os.getenv("WHATSAPP_PHONE_NUMBER_ID", "") or ""),
                "token_prefix_masked": str((os.getenv("WHATSAPP_ACCESS_TOKEN", "") or ""))[:8] + "***",
                "payload_masked": {"messaging_product": "whatsapp", "to": to_phone.lstrip("+"), "type": "text", "text": {"body": text}},
                "timeout": int(timeout_seconds),
                "headers_masked": {"Authorization": "Bearer ***", "Content-Type": "application/json"},
            },
        )
        resp = send_text_message(to_phone, text, timeout_seconds=timeout_seconds)
        if bool(resp.get("ok")):
            return {
                "ok": True,
                "status": "sent",
                "provider_message_id": str(resp.get("wa_message_id") or "") or None,
                "raw": {
                    "provider": "meta_sandbox",
                    "simulated": False,
                    "http_status": resp.get("http_status"),
                    "response": resp.get("raw_response"),
                },
            }
        reason = str(resp.get("error_code") or resp.get("reason") or "meta_sandbox_send_failed")
        return {
            "ok": False,
            "status": "failed",
            "error_code": reason,
            "error_kind": str(resp.get("error_kind") or ""),
            "error_message": str(resp.get("error_message") or reason),
            "raw": {
                "provider": "meta_sandbox",
                "simulated": False,
                "http_status": resp.get("http_status"),
                "response": resp.get("raw_response"),
            },
        }
    if provider == "twilio_sandbox":
        simulated = _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE", "true"))
        if simulated:
            return {"ok": True, "status": "sent", "provider_message_id": f"twilio-sandbox-{hashlib.md5(text.encode()).hexdigest()[:10]}", "raw": {"provider": "twilio_sandbox", "simulated": True}}
        return {"ok": False, "status": "failed", "error_code": "twilio_sandbox_network_disabled", "error_message": "Twilio sandbox call disabled in this environment"}
    return {"ok": False, "status": "blocked", "error_code": "provider_not_allowed", "error_message": provider}


def apply_delivery_webhook_update(
    *,
    provider_message_id: str,
    delivery_status: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = str(provider_message_id or "").strip()
    if not pid:
        return {"ok": False, "error": "provider_message_id_required", "updated": 0}
    rows = [
        r
        for r in BotSandboxOutbound.query.order_by(BotSandboxOutbound.id.desc()).limit(300).all()
        if str((dict(r.payload_json or {}).get("audit") or {}).get("provider_message_id") or "") == pid
    ]
    if not rows:
        return {"ok": False, "error": "not_found", "updated": 0}
    updated = 0
    status = str(delivery_status or "").strip().lower() or "queued"
    for row in rows:
        audit = _payload_audit(row)
        delivery = dict(audit.get("delivery") or {})
        updates = list(delivery.get("updates") or [])
        stamp = str(utc_now_naive())
        duplicate = any(str(u.get("status")) == status for u in updates)
        updates.append({"status": status, "ts": stamp, "duplicate": duplicate, "payload": dict(payload or {})})
        delivery["status"] = status
        delivery["updates"] = updates[-50:]
        delivery["last_webhook"] = stamp
        audit["delivery"] = delivery
        row.payload_json = dict(row.payload_json or {})
        row.payload_json["audit"] = audit
        msg = BotMessage.query.get(int(row.bot_message_id))
        if msg is not None:
            if status == "delivered":
                msg.delivered_at = utc_now_naive()
            elif status == "failed":
                msg.failed_at = utc_now_naive()
                msg.error_code = "provider_delivery_failed"
                msg.error_message = "delivery_failed"
        updated += 1
    db.session.flush()
    log_bot_event("real_sandbox_delivery_update", metadata={"provider_message_id": pid, "status": status, "updated": updated})
    return {"ok": True, "updated": updated}


def run_sandbox_worker_once(
    *,
    batch_size: int = 20,
    review_id: int | None = None,
    outbox_id: int | None = None,
) -> dict[str, int]:
    stats = {"picked": 0, "sent": 0, "failed": 0, "blocked": 0, "retried": 0, "recovered": 0, "skipped": 0}
    log_bot_event("outbound_worker_started", metadata={"batch_size": int(batch_size)})

    active_real = is_real_whatsapp_sandbox_enabled() and not is_staging_offline_active()
    if not is_staging_offline_active() and not active_real:
        return stats

    stats["recovered"] = recover_orphan_processing_rows()
    now = utc_now_naive()
    q = BotSandboxOutbound.query.filter(BotSandboxOutbound.state.in_([OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_FAILED])).filter(
        (BotSandboxOutbound.next_retry_at.is_(None)) | (BotSandboxOutbound.next_retry_at <= now)
    )
    if outbox_id and int(outbox_id) > 0:
        q = q.filter(BotSandboxOutbound.id == int(outbox_id))
    q = q.order_by(BotSandboxOutbound.id.asc()).limit(max(1, int(batch_size)))
    rows = q.all()
    if review_id and int(review_id) > 0:
        rid = int(review_id)
        rows = [r for r in rows if int((dict(r.payload_json or {}).get("metadata") or {}).get("review_id") or 0) == rid]
    stats["picked"] = len(rows)
    log_bot_event("outbound_worker_loaded_item", metadata={"picked": int(stats["picked"])})

    max_retries = _safe_int_env("BOT_SANDBOX_MAX_RETRIES", 4, minimum=1, maximum=100)
    fail_rate = _safe_float_env("BOT_SANDBOX_FAIL_RATE", 0.0)
    timeout_rate = _safe_float_env("BOT_SANDBOX_TIMEOUT_RATE", 0.0)
    malformed_rate = _safe_float_env("BOT_SANDBOX_MALFORMED_RATE", 0.0)
    timeout_seconds = _safe_int_env("BOT_REAL_WHATSAPP_PROVIDER_TIMEOUT_SECONDS", 8, minimum=1, maximum=60)

    for row in rows:
        if _normalize_corrupt_row(row):
            db.session.flush()
        if row.state == OUTBOX_STATUS_FAILED and int(row.retry_count or 0) >= max_retries:
            if _state_transition_allowed(row.state, OUTBOX_STATUS_BLOCKED):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="max_retries_exhausted")
                stats["blocked"] += 1
            continue

        if row.state == OUTBOX_STATUS_FAILED:
            _set_state(row, OUTBOX_STATUS_QUEUED)
            stats["retried"] += 1

        _set_state(row, OUTBOX_STATUS_PROCESSING)
        log_bot_event(
            "outbound_worker_dispatching",
            metadata={"outbox_id": int(row.id), "provider": str(row.provider or ""), "phone_e164": str(row.phone_e164 or "")},
        )
        msg = BotMessage.query.get(int(row.bot_message_id))
        if msg is not None and (
            str(msg.status or "") == MESSAGE_STATUS_OUTBOUND_SENT
            or msg.sent_at is not None
            or bool(str(msg.wa_message_id or "").strip())
        ):
            _set_state(row, OUTBOX_STATUS_SIMULATED_SENT)
            row.next_retry_at = None
            stats["skipped"] += 1
            log_bot_event(
                "sandbox_duplicate_send_prevented",
                metadata={
                    "outbox_id": int(row.id),
                    "outbound_message_id": int(row.bot_message_id),
                    "provider_message_id": str(msg.wa_message_id or ""),
                    "review_id": int((dict(row.payload_json or {}).get("metadata") or {}).get("review_id") or 0),
                },
            )
            continue

        mode = str((dict(row.payload_json or {}).get("metadata") or {}).get("mode") or "offline")
        if mode != "real_sandbox" and not _safe_phone(row.phone_e164):
            _set_state(row, OUTBOX_STATUS_BLOCKED, reason="real_phone_detected")
            stats["blocked"] += 1
            continue

        if mode == "real_sandbox":
            metadata = dict((dict(row.payload_json or {}).get("metadata") or {}))
            auto_send_allowed = bool(metadata.get("auto_send_allowed", False))
            reason = _kill_switch_reason()
            if reason:
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason=f"kill_switch:{reason}")
                stats["blocked"] += 1
                log_bot_event("real_sandbox_outbound_blocked", metadata={"outbox_id": row.id, "reason": reason})
                continue
            if not _is_allowlisted_real_number(row.phone_e164):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="allowlist_blocked")
                stats["blocked"] += 1
                log_bot_event("real_sandbox_outbound_blocked", metadata={"outbox_id": row.id, "reason": "allowlist_blocked"})
                continue
            text = str((dict(row.payload_json or {}).get("text") or "")).strip()
            if not text:
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="empty_message")
                stats["blocked"] += 1
                continue
            if (not auto_send_allowed) and (not bool(metadata.get("review_approved", False)) or int(metadata.get("review_id") or 0) <= 0):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="review_required")
                stats["blocked"] += 1
                continue
            if (not auto_send_allowed) and (int(metadata.get("approved_by") or 0) <= 0 or int(metadata.get("reviewer") or 0) <= 0):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="reviewer_required")
                stats["blocked"] += 1
                continue
            if (not auto_send_allowed) and (not bool(metadata.get("manual_review_required", False))):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="manual_review_required")
                stats["blocked"] += 1
                continue
            if not bool(metadata.get("owner_only", False)):
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="owner_only_required")
                stats["blocked"] += 1
                continue
            if auto_send_allowed and not is_sandbox_auto_reply_active():
                _set_state(row, OUTBOX_STATUS_BLOCKED, reason="auto_send_guard_blocked")
                stats["blocked"] += 1
                continue

            log_bot_event("real_sandbox_outbound_attempt", metadata={"outbox_id": row.id, "provider": row.provider, "to": _mask_phone(row.phone_e164)})
            result = _provider_send(str(row.provider or "fake"), to_phone=row.phone_e164, text=text, timeout_seconds=timeout_seconds)
            audit = _payload_audit(row)
            audit["response_payload"] = dict(result)
            audit["request_payload"] = {"to_masked": _mask_phone(row.phone_e164), "text": text}
            raw_blob = dict(result.get("raw") or {})
            meta_resp = dict(raw_blob.get("response") or {})
            meta_err = dict(meta_resp.get("error") or {}) if isinstance(meta_resp, dict) else {}
            row.outbound_http_status = raw_blob.get("http_status")
            row.outbound_response_raw = raw_blob
            row.outbound_meta_error_code = str(result.get("error_code") or meta_err.get("code") or "") or None
            row.outbound_meta_error_message = str(result.get("error_message") or meta_err.get("message") or "")[:255] or None
            log_bot_event(
                "real_sandbox_provider_response",
                metadata={
                    "outbox_id": row.id,
                    "provider": row.provider,
                    "ok": bool(result.get("ok")),
                    "status": str(result.get("status") or ""),
                    "error_code": str(result.get("error_code") or ""),
                    "http_status": (dict(result.get("raw") or {}).get("http_status")),
                    "raw": dict(result.get("raw") or {}),
                },
            )
            if bool(result.get("ok")):
                audit["provider_message_id"] = str(result.get("provider_message_id") or "")
                delivery = dict(audit.get("delivery") or {})
                delivery["status"] = "sent"
                delivery["last_webhook"] = delivery.get("last_webhook")
                audit["delivery"] = delivery
                row.payload_json = dict(row.payload_json or {})
                row.payload_json["audit"] = audit
                _set_state(row, OUTBOX_STATUS_SIMULATED_SENT)
                row.next_retry_at = None
                msg = BotMessage.query.get(int(row.bot_message_id))
                if msg is not None:
                    msg.status = MESSAGE_STATUS_OUTBOUND_SENT
                    msg.sent_at = row.simulated_sent_at
                    msg.wa_message_id = str(result.get("provider_message_id") or "") or msg.wa_message_id
                    msg.error_code = None
                    msg.error_message = None
                    try:
                        from services.bot_sandbox_review_service import mark_review_simulated_sent

                        mark_review_simulated_sent(outbound_message_id=int(msg.id))
                    except Exception:
                        pass
                row.outbound_meta_error_code = None
                row.outbound_meta_error_message = None
                log_bot_event(
                    "real_sandbox_outbound_sent",
                    metadata={
                        "review_id": int((dict(row.payload_json or {}).get("metadata") or {}).get("review_id") or 0),
                        "outbox_id": int(row.id),
                        "outbound_message_id": int(row.bot_message_id),
                        "provider_message_id": str(msg.wa_message_id if msg is not None else ""),
                        "provider": row.provider,
                    },
                )
                stats["sent"] += 1
            else:
                err = str(result.get("error_code") or "provider_failed")
                err_kind = str(result.get("error_kind") or "")
                if err_kind:
                    log_bot_event(err_kind, level="warning", metadata={"outbox_id": int(row.id), "error_code": err})
                row.retry_count = int(row.retry_count or 0) + 1
                audit["fail_reason"] = err
                row.payload_json = dict(row.payload_json or {})
                row.payload_json["audit"] = audit
                _set_state(row, OUTBOX_STATUS_FAILED, reason=err)
                row.next_retry_at = utc_now_naive() + timedelta(seconds=_retry_backoff_seconds())
                log_bot_event("real_sandbox_outbound_blocked", metadata={"outbox_id": row.id, "reason": err})
                stats["failed"] += 1
            continue

        p = random.random()
        if p < timeout_rate:
            row.retry_count = int(row.retry_count or 0) + 1
            _set_state(row, OUTBOX_STATUS_FAILED, reason="simulated_provider_timeout")
            row.next_retry_at = utc_now_naive() + timedelta(seconds=_retry_backoff_seconds())
            stats["failed"] += 1
            continue
        if p < timeout_rate + fail_rate:
            row.retry_count = int(row.retry_count or 0) + 1
            _set_state(row, OUTBOX_STATUS_FAILED, reason="simulated_provider_malformed_response")
            row.next_retry_at = utc_now_naive() + timedelta(seconds=_retry_backoff_seconds())
            stats["failed"] += 1
            continue
        if p < timeout_rate + fail_rate + malformed_rate:
            row.retry_count = int(row.retry_count or 0) + 1
            _set_state(row, OUTBOX_STATUS_FAILED, reason="simulated_provider_no_response")
            row.next_retry_at = utc_now_naive() + timedelta(seconds=_retry_backoff_seconds())
            stats["failed"] += 1
            continue

        _set_state(row, OUTBOX_STATUS_SIMULATED_SENT)
        row.next_retry_at = None
        msg = BotMessage.query.get(int(row.bot_message_id))
        if msg is not None:
            msg.status = MESSAGE_STATUS_OUTBOUND_SENT
            msg.sent_at = row.simulated_sent_at
            msg.error_code = None
            msg.error_message = None
            try:
                from services.bot_sandbox_review_service import mark_review_simulated_sent

                mark_review_simulated_sent(outbound_message_id=int(msg.id))
            except Exception:
                pass
        stats["sent"] += 1

    db.session.commit()
    log_bot_event("sandbox_worker.cycle", metadata=stats)
    return stats


def archive_old_sandbox_outbox(*, older_than_hours: int = 6, limit: int = 500) -> dict[str, int]:
    now = utc_now_naive()
    threshold = now - timedelta(hours=max(1, int(older_than_hours)))
    rows = (
        BotSandboxOutbound.query.filter(BotSandboxOutbound.state.in_([OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_FAILED]))
        .filter(BotSandboxOutbound.created_at <= threshold)
        .order_by(BotSandboxOutbound.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    archived = 0
    for row in rows:
        if _state_transition_allowed(str(row.state), OUTBOX_STATUS_BLOCKED):
            _set_state(row, OUTBOX_STATUS_BLOCKED, reason="archived_old_pending")
            archived += 1
    db.session.commit()
    return {"archived": int(archived)}


def cleanup_sandbox_outbox_terminal(*, older_than_days: int = 7, limit: int = 1000) -> dict[str, int]:
    now = utc_now_naive()
    threshold = now - timedelta(days=max(1, int(older_than_days)))
    rows = (
        BotSandboxOutbound.query.filter(BotSandboxOutbound.state.in_([OUTBOX_STATUS_SIMULATED_SENT, OUTBOX_STATUS_BLOCKED]))
        .filter(BotSandboxOutbound.updated_at <= threshold)
        .order_by(BotSandboxOutbound.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    deleted = len(rows)
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return {"deleted": int(deleted)}


def sandbox_metrics_snapshot() -> dict[str, Any]:
    total = db.session.query(func.count(BotSandboxOutbound.id)).scalar() or 0
    queued = db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_QUEUED).scalar() or 0
    processing = db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_PROCESSING).scalar() or 0
    blocked = db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_BLOCKED).scalar() or 0
    failed = db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_FAILED).scalar() or 0
    sent = db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_SIMULATED_SENT).scalar() or 0
    avg_duration = (
        db.session.query(func.avg(func.extract("epoch", BotSandboxOutbound.simulated_sent_at - BotSandboxOutbound.processing_at)))
        .filter(BotSandboxOutbound.simulated_sent_at.isnot(None))
        .filter(BotSandboxOutbound.processing_at.isnot(None))
        .scalar()
        or 0
    )
    retries = db.session.query(func.coalesce(func.sum(BotSandboxOutbound.retry_count), 0)).scalar() or 0
    max_retry = db.session.query(func.coalesce(func.max(BotSandboxOutbound.retry_count), 0)).scalar() or 0
    oldest_queued = db.session.query(func.min(BotSandboxOutbound.queued_at)).filter(BotSandboxOutbound.state == OUTBOX_STATUS_QUEUED).scalar()
    now = utc_now_naive()
    queue_latency = float((now - oldest_queued).total_seconds()) if oldest_queued else 0.0
    blocked_ratio = (float(blocked) / float(total)) if total else 0.0
    fail_ratio = (float(failed) / float(total)) if total else 0.0
    throughput_per_min = 0.0
    if sent:
        min_sent_at = db.session.query(func.min(BotSandboxOutbound.simulated_sent_at)).filter(BotSandboxOutbound.simulated_sent_at.isnot(None)).scalar()
        if min_sent_at:
            window_seconds = max(60.0, float((now - min_sent_at).total_seconds()))
            throughput_per_min = float(sent) / (window_seconds / 60.0)
    return {
        "total": int(total),
        "queued": int(queued),
        "processing": int(processing),
        "queue_depth": int(queued + processing + failed),
        "sent": int(sent),
        "blocked": int(blocked),
        "failed": int(failed),
        "retry_count": int(retries),
        "retry_histogram": {
            "max": int(max_retry),
            "ge_1": int(
                db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.retry_count >= 1).scalar() or 0
            ),
            "ge_3": int(
                db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.retry_count >= 3).scalar() or 0
            ),
            "ge_5": int(
                db.session.query(func.count(BotSandboxOutbound.id)).filter(BotSandboxOutbound.retry_count >= 5).scalar() or 0
            ),
        },
        "blocked_ratio": blocked_ratio,
        "fail_ratio": fail_ratio,
        "queue_oldest_age_seconds": queue_latency,
        "throughput_fake_per_min": throughput_per_min,
        "avg_processing_seconds": float(avg_duration),
        "real_sandbox_enabled": is_real_whatsapp_sandbox_enabled(),
        "real_sandbox_paused": is_real_sandbox_paused(),
        "real_provider": _provider_name(),
        "real_allowlist_count": len(_allowed_numbers()),
        "sandbox_auto_reply_enabled": is_sandbox_auto_reply_enabled(),
        "sandbox_auto_reply_paused": is_sandbox_auto_reply_paused(),
        "sandbox_auto_reply_active": is_sandbox_auto_reply_active(),
    }


def enqueue_blocked_outbound(*, conversation: BotConversation, message: BotMessage, reason: str) -> BotSandboxOutbound:
    row = enqueue_sandbox_outbound(conversation=conversation, message=message, provider="fake")
    if row.state == OUTBOX_STATUS_QUEUED and _state_transition_allowed(row.state, OUTBOX_STATUS_BLOCKED):
        _set_state(row, OUTBOX_STATUS_BLOCKED, reason=reason)
        db.session.commit()
        log_bot_blocked("sandbox_outbound", reason=reason, metadata={"outbox_id": row.id})
    return row


def force_fail_outbox_row(row_id: int, reason: str) -> None:
    row = BotSandboxOutbound.query.get(int(row_id))
    if row is None:
        return
    if row.state in {OUTBOX_STATUS_SIMULATED_SENT, OUTBOX_STATUS_BLOCKED}:
        return
    if row.state == OUTBOX_STATUS_PROCESSING:
        row.state = OUTBOX_STATUS_FAILED
    elif _state_transition_allowed(row.state, OUTBOX_STATUS_FAILED):
        _set_state(row, OUTBOX_STATUS_FAILED, reason=reason)
    row.retry_count = int(row.retry_count or 0) + 1
    row.next_retry_at = utc_now_naive() + timedelta(seconds=_retry_backoff_seconds())
    msg = BotMessage.query.get(int(row.bot_message_id))
    if msg is not None:
        msg.status = MESSAGE_STATUS_OUTBOUND_FAILED
        msg.failed_at = utc_now_naive()
        msg.error_code = "sandbox_forced_fail"
        msg.error_message = str(reason or "forced_fail")[:255]
    db.session.commit()


def assert_provider_fake(provider: str) -> None:
    if is_staging_offline_active() and str(provider or "").strip().lower() not in {"fake", "sandbox", "local"}:
        raise SandboxSafetyError("sandbox_security_block:provider_not_fake")
