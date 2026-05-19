"""Servicios base para mensajes del bot (Fase 1)."""

from __future__ import annotations

from config_app import db
from models import BotConversation, BotMessage
from services.bot_constants import (
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_DIRECTIONS,
    MESSAGE_SOURCE_ADMIN_MANUAL,
    MESSAGE_SOURCE_SYSTEM,
    MESSAGE_SOURCE_WHATSAPP_USER,
    MESSAGE_SOURCES,
    MESSAGE_STATUS_INBOUND_STORED,
    MESSAGE_STATUS_OUTBOUND_QUEUED,
    MESSAGE_STATUSES,
)
from utils.timezone import utc_now_naive


def create_manual_message(
    *,
    conversation: BotConversation,
    text_body: str,
    direction: str = MESSAGE_DIRECTION_OUTBOUND,
    source: str = MESSAGE_SOURCE_ADMIN_MANUAL,
    status: str | None = None,
) -> BotMessage:
    normalized_text = (text_body or "").strip()
    if not normalized_text:
        raise ValueError("text_body no puede estar vacío")

    normalized_direction = (direction or "").strip().lower()
    normalized_source = (source or "").strip().lower()

    if normalized_direction not in MESSAGE_DIRECTIONS:
        raise ValueError(f"Dirección de mensaje inválida: {direction}")
    if normalized_source not in MESSAGE_SOURCES:
        raise ValueError(f"Origen de mensaje inválido: {source}")
    if normalized_direction == MESSAGE_DIRECTION_INBOUND and normalized_source != MESSAGE_SOURCE_WHATSAPP_USER:
        raise ValueError("Mensajes inbound deben usar source=whatsapp_user")
    if normalized_direction == MESSAGE_DIRECTION_OUTBOUND and normalized_source == MESSAGE_SOURCE_WHATSAPP_USER:
        raise ValueError("Mensajes outbound no pueden usar source=whatsapp_user")

    effective_status = (status or "").strip().lower()
    if not effective_status:
        effective_status = (
            MESSAGE_STATUS_INBOUND_STORED if normalized_direction == MESSAGE_DIRECTION_INBOUND else MESSAGE_STATUS_OUTBOUND_QUEUED
        )
    if effective_status not in MESSAGE_STATUSES:
        raise ValueError(f"Estado de mensaje inválido: {effective_status}")

    message = BotMessage(
        conversation_id=conversation.id,
        direction=normalized_direction,
        source=normalized_source,
        message_type="text",
        text_body=normalized_text,
        status=effective_status,
    )
    db.session.add(message)

    now = utc_now_naive()
    conversation.last_message_at = now
    if normalized_direction == MESSAGE_DIRECTION_INBOUND:
        conversation.last_inbound_at = now
        conversation.unread_count_admin = int(conversation.unread_count_admin or 0) + 1
    else:
        conversation.last_outbound_at = now
    conversation.updated_at = now

    db.session.commit()
    return message


def create_system_message(*, conversation: BotConversation, text_body: str) -> BotMessage:
    return create_manual_message(
        conversation=conversation,
        text_body=text_body,
        direction=MESSAGE_DIRECTION_OUTBOUND,
        source=MESSAGE_SOURCE_SYSTEM,
        status=MESSAGE_STATUS_OUTBOUND_QUEUED,
    )
