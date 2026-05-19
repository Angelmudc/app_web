"""Pipeline compartido para procesar inbound del bot con IA controlada."""

from __future__ import annotations

import os
from typing import Any, Callable

from config_app import db
from models import BotConversation, BotMessage
from services.bot_ai_service import classify_intent, generate_safe_reply, is_ai_enabled, is_autoreply_enabled
from services.bot_constants import (
    CONVERSATION_STATUS_BOT_PAUSED,
    CONVERSATION_STATUS_PENDING_HUMAN,
    DECISION_RESULT_ALLOW,
    DECISION_RESULT_ESCALATE,
    DECISION_RESULT_MANUAL_ONLY,
    DECISION_TYPE_AI_CLASSIFICATION,
    DECISION_TYPE_AUTO_REPLY,
    DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
    DECISION_TYPE_PROTOCOL_PENDING_CORRECTION,
    IDENTITY_STATUS_AMBIGUOUS,
    INTENT_FAQ_CONTACTO,
    INTENT_FAQ_ESTADO_GENERAL,
    INTENT_FAQ_HORARIOS,
    INTENT_FAQ_REQUISITOS,
    INTENT_FAQ_UBICACION,
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_SOURCE_ADMIN_MANUAL,
    MESSAGE_SOURCE_BOT_AUTO,
    MESSAGE_SOURCE_SYSTEM,
    MESSAGE_STATUS_OUTBOUND_FAILED,
    MESSAGE_STATUS_OUTBOUND_QUEUED,
    MESSAGE_STATUS_OUTBOUND_SENT,
)
from services.bot_decision_service import register_decision
from services.bot_ai_limits_service import get_ai_daily_usage_summary
from services.bot_conversation_service import get_protocol_state, set_current_step
from services.bot_protocol_service import (
    upsert_pending_correction,
    build_step_prompt,
    detect_pending_correction,
    detect_expected_answer,
    detect_out_of_step_answer,
    extract_step_entities,
    has_personal_data_signal,
    is_greeting_only,
    is_positive_confirmation,
    is_welcome_fastpath_denied,
    get_next_step,
    get_step,
)
from services.whatsapp_cloud_service import is_bot_dry_run, is_whatsapp_enabled
from services.whatsapp_cloud_service import send_text_message
from services.bot_sandbox_service import enqueue_sandbox_outbound, is_staging_offline_active


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_protocol_auto_advance_enabled() -> bool:
    return _is_true(os.getenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"), default=False)


def _process_protocol_auto_advance(*, conversation: BotConversation, inbound_message: BotMessage, message_type: str) -> dict[str, Any]:
    current_state = get_protocol_state(conversation)
    current_step_code = str(current_state.get("current_step_code") or "").strip().upper()
    protocol_version = str(current_state.get("protocol_version") or "")
    old_step = current_step_code
    facts_base = {
        "old_step": old_step,
        "new_step": old_step,
        "user_text": str(inbound_message.text_body or ""),
        "auto_advance_enabled": False,
        "protocol_version": protocol_version,
        "validation_result": {"matched": False, "reason": "disabled"},
        "entities_detected": {},
        "missing_fields": [],
    }

    if str(message_type or "").strip().lower() != "text":
        return {"enabled": False, "matched": False, "reason": "non_text", "current_step_code": old_step}
    text_body = str(inbound_message.text_body or "")
    # Guardrail duro: fuera de WELCOME, saludo/ruido jamás completa etapa ni avanza.
    if current_step_code != "WELCOME" and is_greeting_only(text_body):
        clarification = build_step_prompt(current_step_code)
        if current_step_code == "PERSONAL_CONFIRMATION":
            clarification = "Por favor responde únicamente SI o NO."
        facts = dict(facts_base)
        facts["auto_advance_enabled"] = bool(is_protocol_auto_advance_enabled())
        facts["validation_result"] = {"matched": False, "reason": "greeting_only_blocked"}
        facts["clarification_prompt"] = clarification
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_GRT_NOISE_BLOCKED",
            reason_human="Saludo/ruido fuera de WELCOME; se mantiene etapa actual",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {
            "enabled": True,
            "matched": False,
            "requires_human": False,
            "current_step_code": old_step,
            "missing_fields": [],
            "clarification_prompt": clarification,
        }

    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    protocol_entities = dict(metadata.get("protocol_entities") or {})
    pending_correction = detect_pending_correction(current_step_code, inbound_message.text_body or "", protocol_entities)
    if bool(pending_correction.get("has_correction")):
        conversation_meta = dict(getattr(conversation, "metadata_json", {}) or {})
        is_local_practice = str(conversation_meta.get("conversation_type") or "").strip().lower() == "local_practice"
        metadata, pending_item, pending_action = upsert_pending_correction(
            metadata,
            {
                "field": str(pending_correction.get("field") or ""),
                "old_value": pending_correction.get("old_value"),
                "new_value": pending_correction.get("new_value"),
                "source_message_id": int(inbound_message.id or 0),
                "suggested_step_code": str(pending_correction.get("suggested_step_code") or ""),
                "normalized_text": str(pending_correction.get("normalized_text") or ""),
                "original_text": str(pending_correction.get("original_text") or str(inbound_message.text_body or "")),
            },
        )
        # En práctica local seguimos avanzando el entrenamiento, pero sin ocultar
        # que hubo contradicción que requiere validación humana.
        if is_local_practice and str(pending_item.get("field") or "") == "work_type":
            entities = dict(metadata.get("protocol_entities") or {})
            new_value = str(pending_item.get("new_value") or "").strip()
            if new_value:
                entities["work_type"] = new_value
                metadata["protocol_entities"] = entities
        conversation.metadata_json = metadata
        facts = dict(facts_base)
        facts["auto_advance_enabled"] = bool(is_protocol_auto_advance_enabled())
        facts["pending_action"] = pending_action
        facts["pending_correction"] = {
            "id": pending_item.get("id"),
            "field": pending_item["field"],
            "old_value": pending_item["old_value"],
            "new_value": pending_item["new_value"],
            "suggested_step_code": pending_item["suggested_step_code"],
            "source_message_id": pending_item["source_message_id"],
            "requires_human": True,
            "normalized_text": pending_item["normalized_text"],
            "original_text": pending_item["original_text"],
        }
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_PENDING_CORRECTION,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_PENDING_CORRECTION_DETECTED",
            reason_human="Corrección detectada; requiere confirmación humana antes de aplicar cambios",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {
            "enabled": True,
            "matched": False,
            "requires_human": True,
            "current_step_code": old_step,
            "pending_correction": True,
            "pending_correction_item": pending_item,
        }

    conversation_meta = dict(getattr(conversation, "metadata_json", {}) or {})
    is_local_practice = str(conversation_meta.get("conversation_type") or "").strip().lower() == "local_practice"
    auto_enabled = is_protocol_auto_advance_enabled() or is_local_practice
    local_safe_mode = (bool(is_bot_dry_run()) and (not is_whatsapp_enabled())) or is_local_practice
    if not auto_enabled or not local_safe_mode:
        rule_code = "PROTOCOL_AUTO_ADVANCE_LOCAL_ONLY" if auto_enabled and not local_safe_mode else "PROTOCOL_AUTO_ADVANCE_DISABLED"
        facts = dict(facts_base)
        facts["auto_advance_enabled"] = bool(auto_enabled)
        validation_when_disabled = detect_expected_answer(current_step_code, inbound_message.text_body or "")
        facts["validation_result"] = validation_when_disabled
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code=rule_code,
            reason_human="Auto-avance de protocolo desactivado o fuera de modo local/dry-run",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {"enabled": False, "matched": False, "reason": "disabled_or_not_local_safe", "current_step_code": old_step}

    extraction = extract_step_entities(current_step_code, inbound_message.text_body or "", existing_entities=protocol_entities)
    new_entities = extraction.get("entities") or {}
    future_entities = extraction.get("future_entities") or {}
    merged_entities = extraction.get("merged_entities") or protocol_entities
    if new_entities:
        metadata["protocol_entities"] = merged_entities
        conversation.metadata_json = metadata
    if future_entities:
        existing_future = dict(metadata.get("protocol_future_entities") or {})
        for key, value in dict(future_entities).items():
            existing_future[str(key)] = {
                "value": value,
                "detected_from_step": current_step_code,
                "source_message_id": int(inbound_message.id or 0),
            }
        metadata["protocol_future_entities"] = existing_future
        conversation.metadata_json = metadata
    validation = detect_expected_answer(current_step_code, inbound_message.text_body or "")
    out_of_step = detect_out_of_step_answer(current_step_code, inbound_message.text_body or "")
    if extraction.get("schema_mode"):
        validation["matched"] = bool(extraction.get("matched"))
        validation["missing_fields"] = extraction.get("missing_fields") or []
        validation["entities"] = dict(new_entities)
        validation["required_fields"] = extraction.get("required_fields") or []
        validation["optional_fields"] = extraction.get("optional_fields") or []
        validation["requires_human"] = bool(extraction.get("requires_human", False))
        validation["merged_entities"] = dict(merged_entities)

    implicit_personal_confirmation = (
        current_step_code == "PERSONAL_CONFIRMATION"
        and bool(validation.get("matched"))
        and bool((extraction.get("checks") or {}).get("implicit_personal_data"))
        and has_personal_data_signal(inbound_message.text_body or "")
    )
    if implicit_personal_confirmation:
        # El mismo mensaje se vuelve a evaluar como BASIC_INFO para evitar loop "SI/NO".
        basic_existing = dict(metadata.get("protocol_entities") or {})
        basic_extraction = extract_step_entities("BASIC_INFO", inbound_message.text_body or "", existing_entities=basic_existing)
        if basic_extraction.get("schema_mode"):
            basic_new = dict(basic_extraction.get("entities") or {})
            basic_merged = dict(basic_extraction.get("merged_entities") or basic_existing)
            basic_future = dict(basic_extraction.get("future_entities") or {})
            if basic_new:
                metadata["protocol_entities"] = basic_merged
            if basic_future:
                existing_future = dict(metadata.get("protocol_future_entities") or {})
                for key, value in basic_future.items():
                    existing_future[str(key)] = {
                        "value": value,
                        "detected_from_step": "BASIC_INFO",
                        "source_message_id": int(inbound_message.id or 0),
                    }
                metadata["protocol_future_entities"] = existing_future
            conversation.metadata_json = metadata
            db.session.flush()
            if bool(basic_extraction.get("matched")):
                set_current_step(
                    conversation,
                    current_step_code="ADDRESS",
                    last_completed_step="BASIC_INFO",
                    autocommit=False,
                )
                facts = dict(facts_base)
                facts["auto_advance_enabled"] = True
                facts["validation_result"] = validation
                facts["entities_detected"] = dict(basic_new)
                facts["future_entities_detected"] = dict(basic_future)
                facts["missing_fields"] = []
                facts["partial_progress"] = False
                facts["required_complete"] = True
                facts["implicit_personal_confirmation"] = True
                facts["old_step"] = "PERSONAL_CONFIRMATION"
                facts["new_step"] = "ADDRESS"
                facts["next_step_title"] = str((get_step("ADDRESS") or {}).get("title") or "")
                facts["next_step_prompt"] = build_step_prompt("ADDRESS")
                register_decision(
                    conversation=conversation,
                    decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
                    decision_result=DECISION_RESULT_MANUAL_ONLY,
                    rule_code="PROTOCOL_AUTO_ADVANCE_LOCAL",
                    reason_human="Confirmación implícita por datos personales; se completó BASIC_INFO y avanzó a ADDRESS",
                    message=inbound_message,
                    facts_json=facts,
                    autocommit=False,
                )
                db.session.flush()
                return {
                    "enabled": True,
                    "matched": True,
                    "requires_human": False,
                    "old_step": "PERSONAL_CONFIRMATION",
                    "new_step": "ADDRESS",
                    "current_step_code": "ADDRESS",
                    "entities_detected": dict(basic_new),
                    "future_entities_detected": dict(basic_future),
                    "auto_advance_limited_to_one_step": False,
                    "missing_fields": [],
                }
            set_current_step(
                conversation,
                current_step_code="BASIC_INFO",
                last_completed_step="PERSONAL_CONFIRMATION",
                autocommit=False,
            )
            facts = dict(facts_base)
            facts["auto_advance_enabled"] = True
            facts["validation_result"] = validation
            facts["entities_detected"] = dict(basic_new)
            facts["future_entities_detected"] = dict(basic_future)
            facts["missing_fields"] = list(basic_extraction.get("missing_fields") or [])
            facts["partial_progress"] = bool(basic_new)
            facts["required_complete"] = False
            facts["implicit_personal_confirmation"] = True
            facts["old_step"] = "PERSONAL_CONFIRMATION"
            facts["new_step"] = "BASIC_INFO"
            facts["clarification_prompt"] = _build_missing_fields_prompt({"missing_fields": facts["missing_fields"], "fallback": "Para continuar, comparte tus datos básicos."})
            register_decision(
                conversation=conversation,
                decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
                decision_result=DECISION_RESULT_MANUAL_ONLY,
                rule_code="PROTOCOL_AUTO_ADVANCE_LOCAL",
                reason_human="Confirmación implícita por datos personales; pasó a BASIC_INFO con datos incompletos",
                message=inbound_message,
                facts_json=facts,
                autocommit=False,
            )
            db.session.flush()
            return {
                "enabled": True,
                "matched": False,
                "requires_human": False,
                "old_step": "PERSONAL_CONFIRMATION",
                "new_step": "BASIC_INFO",
                "current_step_code": "BASIC_INFO",
                "entities_detected": dict(basic_new),
                "future_entities_detected": dict(basic_future),
                "auto_advance_limited_to_one_step": False,
                "missing_fields": list(basic_extraction.get("missing_fields") or []),
                "clarification_prompt": str(facts.get("clarification_prompt") or ""),
            }
    # ADDRESS/WORK_TYPE/TRANSPORT_ROUTE are validated by rule-based checks (non-schema).
    # In those cases we still consolidate known entities and consume future placeholders.
    if (not extraction.get("schema_mode")) and bool(validation.get("matched")):
        entities_update: dict[str, Any] = {}
        normalized_step = str(current_step_code or "").strip().upper()
        future_map = dict(metadata.get("protocol_future_entities") or {})
        if normalized_step == "ADDRESS":
            # Reuse parser from BASIC_INFO for city extraction to keep consistency.
            basic_probe = extract_step_entities("BASIC_INFO", inbound_message.text_body or "", existing_entities=protocol_entities)
            detected_city = str(((basic_probe.get("future_entities") or {}).get("city") or "")).strip()
            if detected_city:
                entities_update["city"] = detected_city
            elif isinstance(future_map.get("city"), dict):
                v = str((future_map.get("city") or {}).get("value") or "").strip()
                if v:
                    entities_update["city"] = v
        elif normalized_step == "WORK_TYPE":
            basic_probe = extract_step_entities("BASIC_INFO", inbound_message.text_body or "", existing_entities=protocol_entities)
            detected_work_type = str(((basic_probe.get("future_entities") or {}).get("work_type") or "")).strip()
            if detected_work_type:
                entities_update["work_type"] = detected_work_type
            elif isinstance(future_map.get("work_type"), dict):
                v = str((future_map.get("work_type") or {}).get("value") or "").strip()
                if v:
                    entities_update["work_type"] = v
        elif normalized_step == "TRANSPORT_ROUTE":
            route_entry = future_map.get("route") or future_map.get("transport_route")
            if isinstance(route_entry, dict):
                v = str(route_entry.get("value") or "").strip()
                if v:
                    entities_update["route"] = v
        if entities_update:
            merged = dict(protocol_entities)
            merged.update(entities_update)
            metadata["protocol_entities"] = merged
            conversation.metadata_json = metadata

    step = get_step(current_step_code) or {}
    requires_human = bool(step.get("requires_human", False))
    if extraction.get("schema_mode"):
        requires_human = bool(validation.get("requires_human", False))
    allow_advance_with_human_alert = False
    human_alert_reason = ""
    if is_local_practice and bool(validation.get("matched")) and requires_human:
        allow_advance_with_human_alert = True
        human_alert_reason = f"{str(current_step_code or '').lower()}_requires_human_review"
    next_step = get_next_step(current_step_code)
    next_step_code = str((next_step or {}).get("step_code") or "").strip().upper() or None

    facts = dict(facts_base)
    facts["auto_advance_enabled"] = True
    facts["validation_result"] = validation
    facts["entities_detected"] = dict(new_entities)
    facts["future_entities_detected"] = dict(future_entities)
    facts["multi_entity_message"] = (len(new_entities) + len(future_entities)) > 1
    facts["auto_advance_limited_to_one_step"] = True
    facts["missing_fields"] = list(validation.get("missing_fields") or [])
    facts["partial_progress"] = bool(new_entities) and (not bool(validation.get("matched")))
    facts["required_complete"] = bool(validation.get("matched"))
    facts["out_of_step"] = bool(out_of_step.get("out_of_step"))
    facts["out_of_step_details"] = out_of_step if out_of_step.get("out_of_step") else {}

    if bool(out_of_step.get("out_of_step")) and not (
        bool(extraction.get("schema_mode")) and bool(validation.get("matched"))
    ):
        facts["detected_correction"] = True
        facts["correction_type"] = str(out_of_step.get("detected_topic") or "")
        facts["suggested_step"] = str(out_of_step.get("suggested_step_code") or "")
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_OUT_OF_STEP_ANSWER",
            reason_human="Respuesta fuera de la etapa actual; revisión manual recomendada",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {
            "enabled": True,
            "matched": False,
            "requires_human": False,
            "current_step_code": old_step,
            "out_of_step": True,
            "suggested_step_code": str(out_of_step.get("suggested_step_code") or ""),
        }
    if bool(out_of_step.get("out_of_step")) and bool(extraction.get("schema_mode")) and bool(validation.get("matched")):
        facts["out_of_step_ignored_due_to_valid_current_step"] = True

    if requires_human and not allow_advance_with_human_alert:
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_AUTO_ADVANCE_BLOCKED_HUMAN",
            reason_human="Etapa requiere revisión humana; no se auto-avanza",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {"enabled": True, "matched": False, "requires_human": True, "current_step_code": old_step}
    if allow_advance_with_human_alert:
        facts["requires_human_alert"] = True
        facts["requires_human_alert_reason"] = human_alert_reason

    if bool(validation.get("matched")):
        # Consume future entities once their stage is actually answered.
        consume_by_step = {
            "ADDRESS": {"city"},
            "WORK_TYPE": {"work_type"},
            "TRANSPORT_ROUTE": {"route", "transport_route"},
        }
        current_consume = consume_by_step.get(str(current_step_code or "").strip().upper(), set())
        if current_consume:
            existing_future = dict(metadata.get("protocol_future_entities") or {})
            changed = False
            for key in current_consume:
                if key in existing_future:
                    existing_future.pop(key, None)
                    changed = True
            if changed:
                metadata["protocol_future_entities"] = existing_future
                conversation.metadata_json = metadata

    if not bool(validation.get("matched")):
        facts["clarification_prompt"] = _build_missing_fields_prompt(validation)
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_AUTO_ADVANCE_INVALID_ANSWER",
            reason_human="Respuesta no cumple validación de la etapa actual",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {
            "enabled": True,
            "matched": False,
            "requires_human": False,
            "current_step_code": old_step,
            "entities_detected": dict(new_entities),
            "future_entities_detected": dict(future_entities),
            "missing_fields": list(validation.get("missing_fields") or []),
            "clarification_prompt": str(facts.get("clarification_prompt") or ""),
        }

    if not next_step_code:
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="PROTOCOL_AUTO_ADVANCE_LAST_STEP",
            reason_human="Etapa final alcanzada; no hay siguiente etapa",
            message=inbound_message,
            facts_json=facts,
            autocommit=False,
        )
        db.session.flush()
        return {"enabled": True, "matched": True, "requires_human": False, "current_step_code": old_step}

    set_current_step(
        conversation,
        current_step_code=next_step_code,
        last_completed_step=current_step_code,
        autocommit=False,
    )
    # Fast-path humano: un mensaje en WELCOME puede incluir saludo + confirmación
    # + datos. Si detectamos confirmación o señal personal, no forzamos turno extra.
    if (
        str(current_step_code or "").strip().upper() == "WELCOME"
        and str(next_step_code or "").strip().upper() == "PERSONAL_CONFIRMATION"
    ):
        raw_text = str(inbound_message.text_body or "")
        if is_welcome_fastpath_denied(raw_text):
            facts["welcome_fastpath_denied"] = True
            facts["welcome_fastpath_reason"] = "spam_or_unclear_identity"
        else:
            basic_existing = dict(metadata.get("protocol_entities") or {})
            basic_extraction = extract_step_entities("BASIC_INFO", raw_text, existing_entities=basic_existing)
            basic_new = dict(basic_extraction.get("entities") or {})
            basic_future = dict(basic_extraction.get("future_entities") or {})
            if (
                is_positive_confirmation(raw_text, step_code="PERSONAL_CONFIRMATION")
                or has_personal_data_signal(raw_text)
            ):
                if bool(basic_extraction.get("schema_mode")):
                    basic_new = dict(basic_extraction.get("entities") or {})
                    basic_merged = dict(basic_extraction.get("merged_entities") or basic_existing)
                    basic_future = dict(basic_extraction.get("future_entities") or {})
                    if basic_new:
                        metadata["protocol_entities"] = basic_merged
                    if basic_future.get("work_type"):
                        merged_entities = dict(metadata.get("protocol_entities") or {})
                        merged_entities["work_type"] = str(basic_future.get("work_type") or "").strip()
                        metadata["protocol_entities"] = merged_entities
                    if basic_future:
                        existing_future = dict(metadata.get("protocol_future_entities") or {})
                        for key, value in basic_future.items():
                            existing_future[str(key)] = {
                                "value": value,
                                "detected_from_step": "BASIC_INFO",
                                "source_message_id": int(inbound_message.id or 0),
                            }
                        metadata["protocol_future_entities"] = existing_future
                    conversation.metadata_json = metadata
                    set_current_step(
                        conversation,
                        current_step_code="ADDRESS" if bool(basic_extraction.get("matched")) else "BASIC_INFO",
                        last_completed_step="BASIC_INFO" if bool(basic_extraction.get("matched")) else "PERSONAL_CONFIRMATION",
                        autocommit=False,
                    )
                    if basic_future:
                        # set_current_step conserva metadata, pero regrabamos explícitamente
                        # los future entities para no perder señal en flujos de un solo mensaje.
                        refreshed = dict(getattr(conversation, "metadata_json", {}) or {})
                        refreshed_future = dict(refreshed.get("protocol_future_entities") or {})
                        for key, value in basic_future.items():
                            refreshed_future[str(key)] = {
                                "value": value,
                                "detected_from_step": "BASIC_INFO",
                                "source_message_id": int(inbound_message.id or 0),
                            }
                        refreshed["protocol_future_entities"] = refreshed_future
                        conversation.metadata_json = refreshed
                    next_step_code = "ADDRESS" if bool(basic_extraction.get("matched")) else "BASIC_INFO"
                    facts["entities_detected"] = dict(basic_new)
                    facts["future_entities_detected"] = dict(basic_future)
                    facts["implicit_personal_confirmation"] = True
                    facts["auto_advance_limited_to_one_step"] = False
    facts["new_step"] = next_step_code
    facts["next_step_title"] = str((next_step or {}).get("title") or "")
    facts["next_step_prompt"] = build_step_prompt(next_step_code)
    register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_PROTOCOL_AUTO_ADVANCE,
        decision_result=DECISION_RESULT_MANUAL_ONLY,
        rule_code="PROTOCOL_AUTO_ADVANCE_LOCAL",
        reason_human="Etapa completada automáticamente en modo local/dry-run",
        message=inbound_message,
        facts_json=facts,
        autocommit=False,
    )
    db.session.flush()
    return {
        "enabled": True,
        "matched": True,
        "requires_human": False,
        "old_step": old_step,
        "new_step": next_step_code,
        "current_step_code": next_step_code,
        "entities_detected": dict(new_entities),
        "future_entities_detected": dict(future_entities),
        "auto_advance_limited_to_one_step": True,
        "missing_fields": [],
    }


def _build_missing_fields_prompt(validation: dict[str, Any]) -> str:
    missing = [str(x) for x in (validation.get("missing_fields") or []) if str(x)]
    if not missing:
        return str(validation.get("fallback") or "Por favor confirma los datos faltantes.")
    labels = {"name": "tu nombre completo", "age": "tu edad"}
    if len(missing) == 1:
        return f"Perfecto. Ahora necesito {labels.get(missing[0], missing[0])}."
    needed = ", ".join(labels.get(x, x) for x in missing[:-1]) + f" y {labels.get(missing[-1], missing[-1])}"
    return f"Perfecto. Ahora necesito {needed}."


def _build_ai_history(conversation_id: int, *, current_inbound_message_id: int | None = None) -> list[dict]:
    rows = (
        BotMessage.query.filter_by(conversation_id=conversation_id)
        .order_by(BotMessage.created_at.desc(), BotMessage.id.desc())
        .limit(8)
        .all()
    )
    rows = list(reversed(rows))
    out = []
    for row in rows:
        if current_inbound_message_id and int(row.id or 0) == int(current_inbound_message_id):
            continue
        if row.source in {MESSAGE_SOURCE_ADMIN_MANUAL, MESSAGE_SOURCE_SYSTEM}:
            continue
        out.append({"role": "assistant" if row.direction == MESSAGE_DIRECTION_OUTBOUND else "user", "text": row.text_body or ""})
    return out[-3:]


def _safe_autoreply_intents() -> set[str]:
    return {
        INTENT_FAQ_HORARIOS,
        INTENT_FAQ_REQUISITOS,
        INTENT_FAQ_UBICACION,
        INTENT_FAQ_CONTACTO,
        INTENT_FAQ_ESTADO_GENERAL,
    }


def _build_protocol_ai_context(conversation: BotConversation) -> dict[str, Any]:
    state = get_protocol_state(conversation)
    current_step_code = str(state.get("current_step_code") or "").strip().upper()
    step = get_step(current_step_code) or {}
    step_prompt = build_step_prompt(current_step_code) if current_step_code else "Etapa no encontrada."
    return {
        "protocol_version": str(state.get("protocol_version") or ""),
        "current_step_code": current_step_code,
        "step_title": str(step.get("title") or ""),
        "step_prompt": str(step_prompt or ""),
        "expected_answers": [str(x) for x in (step.get("expected_answers") or []) if str(x).strip()],
        "validations": [str(x) for x in (step.get("validations") or []) if str(x).strip()],
        "requires_human": bool(step.get("requires_human", False)),
    }


def process_inbound_ai_pipeline(
    *,
    conversation: BotConversation,
    inbound_message: BotMessage,
    identity_status: str,
    message_type: str,
    phone_e164: str,
    allow_autoreply_send: bool,
    classify_intent_fn: Callable[..., dict[str, Any]] = classify_intent,
    generate_safe_reply_fn: Callable[[str], str] = generate_safe_reply,
    is_ai_enabled_fn: Callable[[], bool] = is_ai_enabled,
    is_autoreply_enabled_fn: Callable[[], bool] = is_autoreply_enabled,
    send_text_message_fn: Callable[[str, str], dict[str, Any]] = send_text_message,
) -> dict[str, Any]:
    identity_status_norm = str(identity_status or "").strip().lower()
    msg_type = str(message_type or "").strip().lower()
    protocol_auto = _process_protocol_auto_advance(
        conversation=conversation,
        inbound_message=inbound_message,
        message_type=msg_type,
    )

    hard_block_rule = None
    if conversation.status == CONVERSATION_STATUS_PENDING_HUMAN:
        hard_block_rule = "HARD_PENDING_HUMAN"
    elif conversation.status == CONVERSATION_STATUS_BOT_PAUSED or bool(conversation.bot_paused):
        hard_block_rule = "HARD_BOT_PAUSED"
    elif identity_status_norm == IDENTITY_STATUS_AMBIGUOUS:
        hard_block_rule = "HARD_IDENTITY_AMBIGUOUS"
        conversation.status = CONVERSATION_STATUS_PENDING_HUMAN
    elif msg_type != "text":
        hard_block_rule = "HARD_NON_TEXT_MESSAGE"
    elif not is_ai_enabled_fn():
        hard_block_rule = "HARD_AI_DISABLED"

    if hard_block_rule:
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_AI_CLASSIFICATION,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code=hard_block_rule,
            reason_human="IA no ejecutada por regla dura",
            message=inbound_message,
            facts_json={"identity_status": identity_status_norm, "message_type": msg_type},
            autocommit=False,
        )
        db.session.flush()
        return {"ok": True, "blocked": True, "protocol_auto_advance": protocol_auto}

    daily_summary = get_ai_daily_usage_summary()
    if bool(daily_summary.get("reached")):
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_AI_CLASSIFICATION,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="AI_DAILY_LIMIT_REACHED",
            reason_human="Límite diario de IA alcanzado; requiere revisión humana",
            message=inbound_message,
            facts_json={
                "identity_status": identity_status_norm,
                "message_type": msg_type,
                "ai_daily_used": int(daily_summary.get("used") or 0),
                "ai_daily_limit": int(daily_summary.get("limit") or 0),
            },
            autocommit=False,
        )
        db.session.flush()
        return {"ok": True, "blocked": True, "daily_limit_reached": True, "protocol_auto_advance": protocol_auto}

    try:
        protocol_ctx = _build_protocol_ai_context(conversation)
        ai_result = classify_intent_fn(
            inbound_message.text_body or "",
            context={
                "identity_role": identity_status_norm or "unknown",
                "history": _build_ai_history(conversation.id, current_inbound_message_id=inbound_message.id),
                "protocol_context": protocol_ctx,
            },
        )
    except Exception:
        protocol_ctx = _build_protocol_ai_context(conversation)
        ai_result = {
            "ok": False,
            "error_code": "ai_exception",
            "intent": "UNKNOWN",
            "confidence": 0.0,
            "requires_human": True,
            "answer_text": "",
            "prompt_version": "phase4_v1",
            "ai_model": "",
        }

    ai_intent = str(ai_result.get("intent") or "").strip().upper()
    ai_conf = float(ai_result.get("confidence") or 0)
    step_requires_human = bool(protocol_ctx.get("requires_human"))
    requires_human = bool(ai_result.get("requires_human", True)) or step_requires_human
    ai_answer = (ai_result.get("answer_text") or "").strip() or generate_safe_reply_fn(ai_intent)
    ai_rule_code = str(ai_result.get("escalation_reason") or ai_result.get("error_code") or "AI_UNKNOWN").strip()

    if not ai_result.get("ok"):
        requires_human = True
        ai_rule_code = ai_rule_code or "AI_ERROR"

    ai_facts = {
        "intent": ai_intent,
        "confidence": ai_conf,
        "requires_human": requires_human,
        "suggested_reply": ai_answer,
        "protocol_version": str(protocol_ctx.get("protocol_version") or ""),
        "current_step_code": str(protocol_ctx.get("current_step_code") or ""),
        "step_title": str(protocol_ctx.get("step_title") or ""),
        "step_requires_human": bool(protocol_ctx.get("requires_human", False)),
    }
    register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_AI_CLASSIFICATION,
        decision_result=DECISION_RESULT_ESCALATE if requires_human else DECISION_RESULT_ALLOW,
        rule_code=ai_rule_code,
        reason_human="Clasificacion IA controlada",
        message=inbound_message,
        facts_json=ai_facts,
        ai_used=True,
        ai_model=str(ai_result.get("ai_model") or ""),
        ai_prompt_version=str(ai_result.get("prompt_version") or ""),
        autocommit=False,
    )

    if requires_human or ai_intent not in _safe_autoreply_intents():
        conversation.status = CONVERSATION_STATUS_PENDING_HUMAN
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_AUTO_REPLY,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code="AUTOREPLY_BLOCKED_OR_ESCALATED",
            reason_human="Solo sugerencia IA; requiere humano o intent no seguro",
            message=inbound_message,
            facts_json=ai_facts,
            ai_used=True,
            ai_model=str(ai_result.get("ai_model") or ""),
            ai_prompt_version=str(ai_result.get("prompt_version") or ""),
            autocommit=False,
        )
        db.session.flush()
        return {"ok": True, "ai_result": ai_result, "manual_only": True, "protocol_auto_advance": protocol_auto}

    if (not allow_autoreply_send) or (not is_autoreply_enabled_fn()):
        rule = "AUTOREPLY_FORCED_MANUAL_ONLY" if not allow_autoreply_send else "AUTOREPLY_DISABLED"
        reason = "Simulación admin: solo sugerencia IA, sin envío automático" if not allow_autoreply_send else "Autorespuesta global desactivada; solo sugerencia IA"
        register_decision(
            conversation=conversation,
            decision_type=DECISION_TYPE_AUTO_REPLY,
            decision_result=DECISION_RESULT_MANUAL_ONLY,
            rule_code=rule,
            reason_human=reason,
            message=inbound_message,
            facts_json=ai_facts,
            ai_used=True,
            ai_model=str(ai_result.get("ai_model") or ""),
            ai_prompt_version=str(ai_result.get("prompt_version") or ""),
            autocommit=False,
        )
        db.session.flush()
        return {"ok": True, "ai_result": ai_result, "manual_only": True, "protocol_auto_advance": protocol_auto}

    send_result = send_text_message_fn(phone_e164, ai_answer)
    outbound_status = MESSAGE_STATUS_OUTBOUND_SENT if send_result.get("ok") else MESSAGE_STATUS_OUTBOUND_FAILED
    outbound = BotMessage(
        conversation_id=conversation.id,
        direction=MESSAGE_DIRECTION_OUTBOUND,
        source=MESSAGE_SOURCE_BOT_AUTO,
        message_type="text",
        text_body=ai_answer,
        status=outbound_status if send_result.get("ok") else MESSAGE_STATUS_OUTBOUND_QUEUED if send_result.get("skipped") else outbound_status,
        wa_message_id=send_result.get("wa_message_id"),
        error_code=None if send_result.get("ok") else str(send_result.get("error_code") or "")[:50] or None,
        error_message=None if send_result.get("ok") else str(send_result.get("error_message") or "")[:255] or None,
    )
    db.session.add(outbound)
    db.session.flush()
    if is_staging_offline_active():
        meta = dict(getattr(conversation, "metadata_json", {}) or {})
        if "sandbox_conversation" not in meta:
            meta["sandbox_conversation"] = True
            conversation.metadata_json = meta
        enqueue_sandbox_outbound(conversation=conversation, message=outbound, provider="fake")
    register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_AUTO_REPLY,
        decision_result=DECISION_RESULT_ALLOW if send_result.get("ok") or send_result.get("skipped") else DECISION_RESULT_ESCALATE,
        rule_code="AUTOREPLY_SENT" if send_result.get("ok") else "AUTOREPLY_DRY_RUN_OR_FAILED",
        reason_human="Autorespuesta FAQ segura procesada",
        message=outbound,
        facts_json={**ai_facts, "send_result": {"ok": bool(send_result.get("ok")), "skipped": bool(send_result.get("skipped"))}},
        ai_used=True,
        ai_model=str(ai_result.get("ai_model") or ""),
        ai_prompt_version=str(ai_result.get("prompt_version") or ""),
        autocommit=False,
    )
    db.session.flush()
    return {"ok": True, "ai_result": ai_result, "manual_only": False, "protocol_auto_advance": protocol_auto}
