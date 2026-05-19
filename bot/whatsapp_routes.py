# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os

from flask import Response, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from config_app import csrf, db
from models import BotConversation, BotMessage, BotSandboxReviewQueue
from services.bot_ai_service import classify_intent, generate_safe_reply, is_ai_enabled, is_autoreply_enabled
from services.bot_constants import (
    CONVERSATION_STATUS_PENDING_HUMAN,
    DECISION_RESULT_ALLOW,
    DECISION_RESULT_ESCALATE,
    DECISION_TYPE_IDENTIFY_CONTACT,
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_SOURCE_WHATSAPP_USER,
    MESSAGE_STATUS_INBOUND_STORED,
)
from services.bot_conversation_service import get_or_create_manual_conversation
from services.bot_decision_service import register_decision
from services.bot_inbound_pipeline_service import process_inbound_ai_pipeline
from services.bot_identity_service import get_or_create_identity
from services.bot_observability_service import log_bot_event
from services.bot_sandbox_review_service import (
    ReviewTransitionError,
    approve_review,
    auto_approve_review_in_sandbox,
    reject_review,
)
from services.bot_sandbox_service import (
    is_sandbox_assistant_allowed,
    is_sandbox_auto_reply_active,
    is_sandbox_auto_reply_enabled,
    is_sandbox_auto_reply_paused,
    run_sandbox_worker_once,
)
from services.phone_identity_service import normalize_phone_to_e164
from services.whatsapp_cloud_service import send_text_message
from services.whatsapp_payload_parser import epoch_to_datetime_utc, parse_webhook_payload
from services.whatsapp_webhook_security import validate_whatsapp_signature, verify_webhook_token
from . import bot_bp


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _is_production_env() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    return env in {"production", "prod"}


def _allowed_real_numbers() -> set[str]:
    raw = str(os.getenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "") or "")
    rows = [x.strip() for x in raw.replace(";", ",").split(",")]
    return {r for r in rows if r.startswith("+") and len(r) >= 8}


def _sandbox_auto_reply_skip_reason(*, enabled: bool, paused: bool, owner_only: bool, provider: str, whatsapp_enabled: bool, dry_run: bool, simulate: bool, allowlisted: bool, app_env: str) -> str:
    if not enabled:
        return "disabled"
    if paused:
        return "paused"
    if not owner_only:
        return "owner_only_false"
    if provider != "meta_sandbox":
        return "provider_not_meta_sandbox"
    if not whatsapp_enabled:
        return "whatsapp_disabled"
    if dry_run:
        return "dry_run_true"
    if simulate:
        return "simulate_true"
    if not allowlisted:
        return "allowlist_blocked"
    if app_env in {"production", "prod"}:
        return "production_blocked"
    return "guard_not_active"


@bot_bp.route("/whatsapp/webhook", methods=["GET"])
def whatsapp_webhook_verify():
    ok, challenge = verify_webhook_token(
        mode=request.args.get("hub.mode"),
        token=request.args.get("hub.verify_token"),
        challenge=request.args.get("hub.challenge"),
        verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN"),
    )
    if not ok:
        return Response("forbidden", status=403)
    return Response(challenge, status=200, mimetype="text/plain")


@bot_bp.route("/whatsapp/webhook", methods=["POST"])
@csrf.exempt
def whatsapp_webhook_receive():
    raw_body = request.get_data(cache=True) or b""
    validate_signature = _is_true(os.getenv("WHATSAPP_VALIDATE_SIGNATURE"), default=_is_production_env())
    if not validate_signature and _is_production_env() and not current_app.testing:
        return jsonify({"ok": False, "error": "signature_validation_required"}), 503
    if validate_signature:
        app_secret = os.getenv("WHATSAPP_APP_SECRET")
        signature = request.headers.get("X-Hub-Signature-256")
        if not validate_whatsapp_signature(raw_body, signature, app_secret):
            return jsonify({"ok": False, "error": "invalid_signature"}), 403

    try:
        payload = request.get_json(silent=True)
        if payload is None and raw_body:
            payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = None

    parsed = parse_webhook_payload(payload)
    log_bot_event(
        "whatsapp_webhook_received",
        metadata={
            "payload_dict": isinstance(payload, dict),
            "messages_count": len(parsed.get("messages", [])),
            "statuses_count": len(parsed.get("statuses", [])),
            "parser_errors": list(parsed.get("errors") or []),
        },
    )
    pending_auto_reply: list[dict[str, int | str | None]] = []
    for msg in parsed.get("messages", []):
        try:
            with db.session.begin_nested():
                wa_message_id = msg.get("wa_message_id")
                log_bot_event(
                    "whatsapp_webhook_message_normalized",
                    metadata={
                        "wa_message_id": wa_message_id,
                        "from_phone_raw": str(msg.get("from_phone_e164") or ""),
                        "message_type": str(msg.get("message_type") or ""),
                        "text_body": str(msg.get("text_body") or ""),
                        "phone_number_id": str(msg.get("phone_number_id") or ""),
                    },
                )
                if wa_message_id:
                    existing = BotMessage.query.filter_by(wa_message_id=wa_message_id).first()
                    if existing:
                        log_bot_event(
                            "whatsapp_webhook_message_skipped",
                            metadata={"wa_message_id": wa_message_id, "reason": "duplicate_wa_message_id", "existing_message_id": int(existing.id)},
                        )
                        continue
                phone_e164 = normalize_phone_to_e164(msg.get("from_phone_e164"), default_country="DO")
                if not phone_e164:
                    log_bot_event(
                        "whatsapp_webhook_message_skipped",
                        metadata={"wa_message_id": wa_message_id, "reason": "invalid_phone_e164"},
                    )
                    continue
                allowlisted = phone_e164 in _allowed_real_numbers()
                existing_conv = BotConversation.query.filter_by(channel="whatsapp", phone_e164=phone_e164).first()
                conversation = get_or_create_manual_conversation(
                    phone_e164=phone_e164, contact_name=msg.get("profile_name"), autocommit=False
                )
                log_bot_event(
                    "whatsapp_webhook_conversation_resolved",
                    metadata={
                        "wa_message_id": wa_message_id,
                        "conversation_id": int(conversation.id),
                        "conversation_created": existing_conv is None,
                        "wa_id": phone_e164,
                        "allowlisted": bool(allowlisted),
                    },
                )
                created_at = epoch_to_datetime_utc(msg.get("timestamp_epoch"))
                inbound_kwargs = dict(
                    conversation_id=conversation.id,
                    direction=MESSAGE_DIRECTION_INBOUND,
                    source=MESSAGE_SOURCE_WHATSAPP_USER,
                    message_type=(msg.get("message_type") or "text"),
                    wa_message_id=wa_message_id,
                    text_body=msg.get("text_body"),
                    media_id=((msg.get("media") or {}).get("id")),
                    media_mime_type=((msg.get("media") or {}).get("mime_type")),
                    media_sha256=((msg.get("media") or {}).get("sha256")),
                    status=MESSAGE_STATUS_INBOUND_STORED,
                    raw_payload_json=msg.get("raw_message"),
                )
                if created_at is not None:
                    inbound_kwargs["created_at"] = created_at
                inbound = BotMessage(**inbound_kwargs)
                db.session.add(inbound)
                db.session.flush()
                log_bot_event(
                    "whatsapp_webhook_inbound_stored",
                    metadata={
                        "wa_message_id": wa_message_id,
                        "inbound_message_id": int(inbound.id),
                        "conversation_id": int(conversation.id),
                        "wa_id": phone_e164,
                        "message_type": str(inbound.message_type or ""),
                        "text_body": str(inbound.text_body or ""),
                    },
                )
                when = created_at
                if when is not None:
                    conversation.last_inbound_at = when
                    conversation.last_message_at = when
                conversation.unread_count_admin = int(conversation.unread_count_admin or 0) + 1
                if msg.get("profile_name") and not (conversation.contact_name or "").strip():
                    conversation.contact_name = msg.get("profile_name")
                resolution = {
                    "identity_status": "identity_check_failed",
                    "rule_code": "IDENTITY_RESOLUTION_FAILED",
                    "reason_human": "No se pudo resolver identidad; requiere revision manual",
                    "client_ids": [],
                    "candidate_ids": [],
                }
                try:
                    identity, resolved = get_or_create_identity(phone_e164)
                    resolution = resolved or resolution
                    conversation.identity_id = identity.id
                except Exception:
                    conversation.status = CONVERSATION_STATUS_PENDING_HUMAN
                register_decision(
                    conversation=conversation,
                    decision_type=DECISION_TYPE_IDENTIFY_CONTACT,
                    decision_result=(
                        DECISION_RESULT_ESCALATE
                        if resolution.get("identity_status") in {"ambiguous", "identity_check_failed"}
                        else DECISION_RESULT_ALLOW
                    ),
                    rule_code=str(resolution.get("rule_code") or "IDENTITY_RULE"),
                    reason_human=str(resolution.get("reason_human") or "Identidad resuelta"),
                    message=inbound,
                    facts_json={
                        "phone_e164": phone_e164,
                        "identity_status": resolution.get("identity_status"),
                        "client_ids": resolution.get("client_ids") or [],
                        "candidate_ids": resolution.get("candidate_ids") or [],
                    },
                    autocommit=False,
                )

                process_inbound_ai_pipeline(
                    conversation=conversation,
                    inbound_message=inbound,
                    identity_status=str(resolution.get("identity_status") or ""),
                    message_type=(msg.get("message_type") or "text"),
                    phone_e164=phone_e164,
                    allow_autoreply_send=True,
                    classify_intent_fn=classify_intent,
                    generate_safe_reply_fn=generate_safe_reply,
                    is_ai_enabled_fn=is_ai_enabled,
                    is_autoreply_enabled_fn=is_autoreply_enabled,
                    send_text_message_fn=send_text_message,
                )
                review_created = False
                review_reason = "sandbox_assistant_disabled"
                review = None
                try:
                    if is_sandbox_assistant_allowed():
                        from services.bot_sandbox_review_service import create_review_from_inbound

                        review = create_review_from_inbound(
                            conversation=conversation,
                            inbound_message=inbound,
                            identity_status=str(resolution.get("identity_status") or ""),
                        )
                        review_created = review is not None
                        review_reason = "created" if review_created else "unknown"
                        log_bot_event(
                            "whatsapp_webhook_review_created",
                            metadata={
                                "wa_message_id": wa_message_id,
                                "review_id": int(review.id) if review else None,
                                "conversation_id": int(conversation.id),
                                "inbound_message_id": int(inbound.id),
                            },
                        )
                    else:
                        review_reason = "sandbox_assistant_disabled"
                except Exception as exc:
                    review_reason = f"create_failed:{exc.__class__.__name__}"
                    current_app.logger.exception("BOT_WHATSAPP_REVIEW_CREATE_FAILED")
                if not review_created:
                    log_bot_event(
                        "whatsapp_webhook_review_skipped",
                        level="warning",
                        metadata={
                            "wa_message_id": wa_message_id,
                            "conversation_id": int(conversation.id),
                            "inbound_message_id": int(inbound.id),
                            "reason": review_reason,
                            "allowlisted": bool(allowlisted),
                            "wa_id": phone_e164,
                        },
                    )
                if review_created and review is not None:
                    env = str(os.getenv("APP_ENV", "development") or "development").strip().lower()
                    provider = str(os.getenv("BOT_REAL_WHATSAPP_PROVIDER", "fake") or "fake").strip().lower().replace("-", "_")
                    owner_only = _is_true(os.getenv("BOT_REAL_WHATSAPP_OWNER_ONLY"), default=True)
                    whatsapp_enabled = _is_true(os.getenv("WHATSAPP_ENABLED"), default=False)
                    dry_run = _is_true(os.getenv("BOT_DRY_RUN"), default=True)
                    simulate = _is_true(os.getenv("BOT_REAL_WHATSAPP_SIMULATE"), default=True)
                    enabled = bool(is_sandbox_auto_reply_enabled())
                    paused = bool(is_sandbox_auto_reply_paused())
                    active = bool(is_sandbox_auto_reply_active())
                    log_bot_event(
                        "sandbox_auto_reply_guard_checked",
                        metadata={
                            "enabled": bool(enabled),
                            "paused": bool(paused),
                            "owner_only": bool(owner_only),
                            "provider": provider,
                            "whatsapp_enabled": bool(whatsapp_enabled),
                            "dry_run": bool(dry_run),
                            "simulate": bool(simulate),
                            "allowlisted": bool(allowlisted),
                            "app_env": env,
                        },
                    )
                    if not active or not allowlisted:
                        log_bot_event(
                            "sandbox_auto_reply_skipped",
                            metadata={
                                "wa_message_id": wa_message_id,
                                "conversation_id": int(conversation.id),
                                "review_id": int(review.id),
                                "reason": _sandbox_auto_reply_skip_reason(
                                    enabled=bool(enabled),
                                    paused=bool(paused),
                                    owner_only=bool(owner_only),
                                    provider=provider,
                                    whatsapp_enabled=bool(whatsapp_enabled),
                                    dry_run=bool(dry_run),
                                    simulate=bool(simulate),
                                    allowlisted=bool(allowlisted),
                                    app_env=env,
                                ),
                            },
                        )
                    else:
                        pending_auto_reply.append(
                            {
                                "review_id": int(review.id),
                                "conversation_id": int(conversation.id),
                                "wa_message_id": str(wa_message_id or ""),
                            }
                        )
        except SQLAlchemyError:
            current_app.logger.exception("BOT_WHATSAPP_INBOUND_STORE_FAILED")
        except Exception:
            current_app.logger.exception("BOT_WHATSAPP_INBOUND_UNEXPECTED_FAILED")

    for status_item in parsed.get("statuses", []):
        try:
            with db.session.begin_nested():
                wa_message_id = (status_item.get("wa_message_id") or "").strip()
                if not wa_message_id:
                    continue
                message = BotMessage.query.filter_by(wa_message_id=wa_message_id).first()
                if not message:
                    continue
                status = (status_item.get("status") or "").strip().lower()
                when = epoch_to_datetime_utc(status_item.get("timestamp_epoch"))
                message.status = status or message.status
                message.raw_payload_json = status_item.get("raw_status")
                if status == "sent":
                    message.sent_at = when or message.sent_at
                elif status == "delivered":
                    message.delivered_at = when or message.delivered_at
                elif status == "read":
                    message.read_at = when or message.read_at
                elif status == "failed":
                    message.failed_at = when or message.failed_at
                    message.error_code = status_item.get("error_code")
                    message.error_message = status_item.get("error_message")
                db.session.flush()
        except SQLAlchemyError:
            current_app.logger.exception("BOT_WHATSAPP_STATUS_UPDATE_FAILED")
        except Exception:
            current_app.logger.exception("BOT_WHATSAPP_STATUS_UNEXPECTED_FAILED")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "store_failed"}), 200

    if pending_auto_reply:
        from models import BotSandboxOutbound

        for item in pending_auto_reply:
            review_id = int(item.get("review_id") or 0)
            if review_id <= 0:
                continue
            try:
                review = BotSandboxReviewQueue.query.get(review_id)
                if review is None:
                    continue
                log_bot_event(
                    "sandbox_auto_reply_started",
                    metadata={
                        "wa_message_id": str(item.get("wa_message_id") or ""),
                        "conversation_id": int(item.get("conversation_id") or 0),
                        "review_id": review_id,
                    },
                )
                _, outbound = auto_approve_review_in_sandbox(review=review)
                db.session.flush()
                outbox = None
                if outbound is not None:
                    outbox = BotSandboxOutbound.query.filter_by(bot_message_id=int(outbound.id)).first()
                log_bot_event(
                    "sandbox_auto_reply_outbox_created",
                    metadata={
                        "outbox_id": int(outbox.id) if outbox else None,
                        "outbound_message_id": int(outbound.id) if outbound else None,
                    },
                )
                log_bot_event(
                    "sandbox_auto_reply_meta_send_attempt",
                    metadata={"review_id": review_id, "outbox_id": int(outbox.id) if outbox else None},
                )
                stats = run_sandbox_worker_once(batch_size=1, review_id=review_id, outbox_id=(int(outbox.id) if outbox else None))
                if outbox is not None:
                    db.session.refresh(outbox)
                if outbound is not None:
                    db.session.refresh(outbound)
                raw_body = (outbox.outbound_response_raw if outbox is not None else None) or {}
                wamid = str((getattr(outbound, "wa_message_id", "") or "")) or str((dict((outbox.payload_json or {}).get("audit") or {}).get("provider_message_id") or ""))
                log_bot_event(
                    "sandbox_auto_reply_meta_send_response",
                    metadata={
                        "http_status": (int(outbox.outbound_http_status) if (outbox is not None and outbox.outbound_http_status is not None) else None),
                        "wamid": wamid or None,
                        "raw_body": raw_body,
                    },
                )
                if int(stats.get("sent", 0)) >= 1 and wamid:
                    log_bot_event(
                        "sandbox_auto_reply_sent",
                        metadata={"review_id": review_id, "outbox_id": int(outbox.id) if outbox else None, "wamid": wamid},
                    )
                else:
                    log_bot_event(
                        "sandbox_auto_reply_failed",
                        level="warning",
                        metadata={
                            "review_id": review_id,
                            "outbox_id": int(outbox.id) if outbox else None,
                            "stats": dict(stats or {}),
                            "state": str(outbox.state or "") if outbox is not None else "",
                            "failure_reason": str(outbox.failure_reason or "") if outbox is not None else "",
                        },
                    )
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                log_bot_event(
                    "sandbox_auto_reply_failed",
                    level="warning",
                    metadata={
                        "wa_message_id": str(item.get("wa_message_id") or ""),
                        "conversation_id": int(item.get("conversation_id") or 0),
                        "review_id": review_id,
                        "error": f"{exc.__class__.__name__}:{exc}",
                    },
                )
                current_app.logger.exception("BOT_SANDBOX_AUTO_REPLY_FAILED")

    return jsonify({"ok": True}), 200


@bot_bp.route("/sandbox/revision/pending", methods=["GET"])
def sandbox_revision_pending():
    rows = (
        BotSandboxReviewQueue.query.order_by(BotSandboxReviewQueue.created_at.asc(), BotSandboxReviewQueue.id.asc())
        .limit(200)
        .all()
    )
    items = []
    for row in rows:
        items.append(
            {
                "id": int(row.id),
                "conversation_id": int(row.conversation_id),
                "requires_human": True,
                "inbound_text": str((row.inbound_message.text_body if row.inbound_message else "") or ""),
                "final_suggested_reply": str(row.final_suggested_reply or ""),
                "safety_status": str(row.safety_status or "pending"),
                "fallback_reason": str(row.fallback_reason or ""),
                "edited": bool((row.edited_text or "").strip()),
                "rejection_reason": str(row.rejection_reason or ""),
                "events": list((dict(row.metadata_json or {})).get("review_events") or []),
            }
        )
    return jsonify({"ok": True, "items": items}), 200


@bot_bp.route("/sandbox/revision/<int:review_id>/approve", methods=["POST"])
@csrf.exempt
def sandbox_revision_approve_internal(review_id: int):
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    body = request.get_json(silent=True) or {}
    edited_text = str((body.get("edited_text") if isinstance(body, dict) else "") or "").strip() or None
    try:
        approve_review(review=review, reviewer_id=None, edited_text=edited_text)
        db.session.commit()
        return jsonify({"ok": True, "review_id": int(review.id), "status": str(review.status)}), 200
    except ReviewTransitionError as exc:
        db.session.commit()
        return (
            jsonify(
                {
                    "ok": False,
                    "error": exc.code,
                    "review_id": int(exc.review_id),
                    "current_status": exc.current_status,
                    "target_status": exc.target_status,
                }
            ),
            409,
        )
    except Exception as exc:
        db.session.rollback()
        log_bot_event("sandbox_review_approve_failed", level="warning", metadata={"review_id": review_id, "error": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 400


@bot_bp.route("/sandbox/revision/<int:review_id>/reject", methods=["POST"])
@csrf.exempt
def sandbox_revision_reject_internal(review_id: int):
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    body = request.get_json(silent=True) or {}
    reason = str((body.get("reason") if isinstance(body, dict) else "") or "").strip() or "rejected_by_reviewer"
    try:
        reject_review(review=review, reviewer_id=None, reason=reason)
        db.session.commit()
        return jsonify({"ok": True, "review_id": int(review.id), "status": str(review.status)}), 200
    except ReviewTransitionError as exc:
        db.session.commit()
        return (
            jsonify(
                {
                    "ok": False,
                    "error": exc.code,
                    "review_id": int(exc.review_id),
                    "current_status": exc.current_status,
                    "target_status": exc.target_status,
                }
            ),
            409,
        )
