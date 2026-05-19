"""create internal bot tables (phase 1)

Revision ID: 20260508_1100
Revises: 0edd31f21421
Create Date: 2026-05-08 11:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260508_1100"
down_revision = "0edd31f21421"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(ix.get("name") or "") for ix in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()

    if not _has_table(bind, "bot_contact_identities"):
        op.create_table(
            "bot_contact_identities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("phone_e164", sa.String(length=20), nullable=False),
            sa.Column("identity_status", sa.String(length=30), nullable=False, server_default=sa.text("'unknown'")),
            sa.Column("is_client", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=True),
            sa.Column("is_candidate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidatas.fila"), nullable=True),
            sa.Column("is_new_contact", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
            sa.Column("last_identity_check_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "(is_client = false AND client_id IS NULL) OR (is_client = true AND client_id IS NOT NULL)",
                name="ck_bot_identity_client_consistency",
            ),
            sa.CheckConstraint(
                "(is_candidate = false AND candidate_id IS NULL) OR (is_candidate = true AND candidate_id IS NOT NULL)",
                name="ck_bot_identity_candidate_consistency",
            ),
            sa.UniqueConstraint("phone_e164", name="uq_bot_contact_identities_phone_e164"),
        )

    identity_indexes = _index_names(bind, "bot_contact_identities")
    for name, cols in (
        ("ix_bot_contact_identities_phone_e164", ["phone_e164"]),
        ("ix_bot_contact_identities_identity_status", ["identity_status"]),
        ("ix_bot_contact_identities_is_client", ["is_client"]),
        ("ix_bot_contact_identities_client_id", ["client_id"]),
        ("ix_bot_contact_identities_is_candidate", ["is_candidate"]),
        ("ix_bot_contact_identities_candidate_id", ["candidate_id"]),
        ("ix_bot_contact_identities_is_new_contact", ["is_new_contact"]),
    ):
        if name not in identity_indexes:
            op.create_index(name, "bot_contact_identities", cols, unique=False)

    if not _has_table(bind, "bot_conversations"):
        op.create_table(
            "bot_conversations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("uuid", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=20), nullable=False, server_default=sa.text("'whatsapp'")),
            sa.Column("phone_e164", sa.String(length=20), nullable=False),
            sa.Column("contact_name", sa.String(length=120), nullable=True),
            sa.Column("identity_id", sa.Integer(), sa.ForeignKey("bot_contact_identities.id"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'open'")),
            sa.Column("bot_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("bot_pause_reason", sa.String(length=255), nullable=True),
            sa.Column("last_inbound_at", sa.DateTime(), nullable=True),
            sa.Column("last_outbound_at", sa.DateTime(), nullable=True),
            sa.Column("last_message_at", sa.DateTime(), nullable=True),
            sa.Column("unread_count_admin", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("assigned_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("uuid", name="uq_bot_conversations_uuid"),
        )

    conversation_indexes = _index_names(bind, "bot_conversations")
    for name, cols in (
        ("ix_bot_conversations_uuid", ["uuid"]),
        ("ix_bot_conversations_channel", ["channel"]),
        ("ix_bot_conversations_phone_e164", ["phone_e164"]),
        ("ix_bot_conversations_identity_id", ["identity_id"]),
        ("ix_bot_conversations_status", ["status"]),
        ("ix_bot_conversations_bot_paused", ["bot_paused"]),
        ("ix_bot_conversations_last_inbound_at", ["last_inbound_at"]),
        ("ix_bot_conversations_last_outbound_at", ["last_outbound_at"]),
        ("ix_bot_conversations_last_message_at", ["last_message_at"]),
        ("ix_bot_conversations_assigned_staff_user_id", ["assigned_staff_user_id"]),
        ("ix_bot_conv_status_paused_last_msg", ["status", "bot_paused", "last_message_at"]),
        ("ix_bot_conv_identity_last_msg", ["identity_id", "last_message_at"]),
    ):
        if name not in conversation_indexes:
            op.create_index(name, "bot_conversations", cols, unique=False)

    if not _has_table(bind, "bot_messages"):
        op.create_table(
            "bot_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("uuid", sa.String(length=36), nullable=False),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
            sa.Column("direction", sa.String(length=10), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("message_type", sa.String(length=20), nullable=False, server_default=sa.text("'text'")),
            sa.Column("wa_message_id", sa.String(length=120), nullable=True),
            sa.Column("reply_to_wa_message_id", sa.String(length=120), nullable=True),
            sa.Column("text_body", sa.Text(), nullable=True),
            sa.Column("media_id", sa.String(length=120), nullable=True),
            sa.Column("media_mime_type", sa.String(length=120), nullable=True),
            sa.Column("media_sha256", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("status_detail", sa.String(length=255), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(length=50), nullable=True),
            sa.Column("error_message", sa.String(length=255), nullable=True),
            sa.Column("raw_payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("uuid", name="uq_bot_messages_uuid"),
            sa.UniqueConstraint("wa_message_id", name="uq_bot_messages_wa_message_id"),
            sa.CheckConstraint(
                "(direction = 'inbound' AND source = 'whatsapp_user') "
                "OR (direction = 'outbound' AND source IN ('admin_manual', 'bot_auto', 'system'))",
                name="ck_bot_msg_direction_source",
            ),
        )

    message_indexes = _index_names(bind, "bot_messages")
    for name, cols in (
        ("ix_bot_messages_uuid", ["uuid"]),
        ("ix_bot_messages_conversation_id", ["conversation_id"]),
        ("ix_bot_messages_direction", ["direction"]),
        ("ix_bot_messages_source", ["source"]),
        ("ix_bot_messages_wa_message_id", ["wa_message_id"]),
        ("ix_bot_messages_reply_to_wa_message_id", ["reply_to_wa_message_id"]),
        ("ix_bot_messages_status", ["status"]),
        ("ix_bot_messages_created_at", ["created_at"]),
        ("ix_bot_msg_conv_created", ["conversation_id", "created_at"]),
    ):
        if name not in message_indexes:
            op.create_index(name, "bot_messages", cols, unique=False)

    if not _has_table(bind, "bot_decision_logs"):
        op.create_table(
            "bot_decision_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("bot_messages.id"), nullable=True),
            sa.Column("decision_type", sa.String(length=40), nullable=False),
            sa.Column("decision_result", sa.String(length=40), nullable=False),
            sa.Column("rule_code", sa.String(length=60), nullable=False),
            sa.Column("reason_human", sa.String(length=255), nullable=False),
            sa.Column("facts_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("ai_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("ai_model", sa.String(length=80), nullable=True),
            sa.Column("ai_prompt_version", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    decision_indexes = _index_names(bind, "bot_decision_logs")
    for name, cols in (
        ("ix_bot_decision_logs_conversation_id", ["conversation_id"]),
        ("ix_bot_decision_logs_message_id", ["message_id"]),
        ("ix_bot_decision_logs_decision_type", ["decision_type"]),
        ("ix_bot_decision_logs_decision_result", ["decision_result"]),
        ("ix_bot_decision_logs_rule_code", ["rule_code"]),
        ("ix_bot_decision_logs_ai_used", ["ai_used"]),
        ("ix_bot_decision_logs_created_at", ["created_at"]),
        ("ix_bot_dec_conv_created", ["conversation_id", "created_at"]),
        ("ix_bot_dec_type_result_created", ["decision_type", "decision_result", "created_at"]),
    ):
        if name not in decision_indexes:
            op.create_index(name, "bot_decision_logs", cols, unique=False)

    if not _has_table(bind, "bot_settings"):
        op.create_table(
            "bot_settings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("value_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("updated_by_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("key", name="uq_bot_settings_key"),
        )

    settings_indexes = _index_names(bind, "bot_settings")
    for name, cols in (
        ("ix_bot_settings_key", ["key"]),
        ("ix_bot_settings_updated_by_staff_user_id", ["updated_by_staff_user_id"]),
    ):
        if name not in settings_indexes:
            op.create_index(name, "bot_settings", cols, unique=False)

    if not _has_table(bind, "bot_escalations"):
        op.create_table(
            "bot_escalations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
            sa.Column("trigger_message_id", sa.Integer(), sa.ForeignKey("bot_messages.id"), nullable=True),
            sa.Column("escalation_status", sa.String(length=30), nullable=False, server_default=sa.text("'open'")),
            sa.Column("reason_code", sa.String(length=60), nullable=False),
            sa.Column("reason_detail", sa.String(length=255), nullable=True),
            sa.Column("priority", sa.String(length=20), nullable=False, server_default=sa.text("'normal'")),
            sa.Column("assigned_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("ack_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    escalation_indexes = _index_names(bind, "bot_escalations")
    for name, cols in (
        ("ix_bot_escalations_conversation_id", ["conversation_id"]),
        ("ix_bot_escalations_trigger_message_id", ["trigger_message_id"]),
        ("ix_bot_escalations_escalation_status", ["escalation_status"]),
        ("ix_bot_escalations_reason_code", ["reason_code"]),
        ("ix_bot_escalations_priority", ["priority"]),
        ("ix_bot_escalations_assigned_staff_user_id", ["assigned_staff_user_id"]),
        ("ix_bot_esc_status_priority_created", ["escalation_status", "priority", "created_at"]),
        ("ix_bot_esc_assigned_status", ["assigned_staff_user_id", "escalation_status"]),
    ):
        if name not in escalation_indexes:
            op.create_index(name, "bot_escalations", cols, unique=False)


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "bot_escalations"):
        idx = _index_names(bind, "bot_escalations")
        for name in (
            "ix_bot_esc_assigned_status",
            "ix_bot_esc_status_priority_created",
            "ix_bot_escalations_assigned_staff_user_id",
            "ix_bot_escalations_priority",
            "ix_bot_escalations_reason_code",
            "ix_bot_escalations_escalation_status",
            "ix_bot_escalations_trigger_message_id",
            "ix_bot_escalations_conversation_id",
        ):
            if name in idx:
                op.drop_index(name, table_name="bot_escalations")
        op.drop_table("bot_escalations")

    if _has_table(bind, "bot_settings"):
        idx = _index_names(bind, "bot_settings")
        for name in ("ix_bot_settings_updated_by_staff_user_id", "ix_bot_settings_key"):
            if name in idx:
                op.drop_index(name, table_name="bot_settings")
        op.drop_table("bot_settings")

    if _has_table(bind, "bot_decision_logs"):
        idx = _index_names(bind, "bot_decision_logs")
        for name in (
            "ix_bot_dec_type_result_created",
            "ix_bot_dec_conv_created",
            "ix_bot_decision_logs_created_at",
            "ix_bot_decision_logs_ai_used",
            "ix_bot_decision_logs_rule_code",
            "ix_bot_decision_logs_decision_result",
            "ix_bot_decision_logs_decision_type",
            "ix_bot_decision_logs_message_id",
            "ix_bot_decision_logs_conversation_id",
        ):
            if name in idx:
                op.drop_index(name, table_name="bot_decision_logs")
        op.drop_table("bot_decision_logs")

    if _has_table(bind, "bot_messages"):
        idx = _index_names(bind, "bot_messages")
        for name in (
            "ix_bot_msg_conv_created",
            "ix_bot_messages_created_at",
            "ix_bot_messages_status",
            "ix_bot_messages_reply_to_wa_message_id",
            "ix_bot_messages_wa_message_id",
            "ix_bot_messages_source",
            "ix_bot_messages_direction",
            "ix_bot_messages_conversation_id",
            "ix_bot_messages_uuid",
        ):
            if name in idx:
                op.drop_index(name, table_name="bot_messages")
        op.drop_table("bot_messages")

    if _has_table(bind, "bot_conversations"):
        idx = _index_names(bind, "bot_conversations")
        for name in (
            "ix_bot_conv_identity_last_msg",
            "ix_bot_conv_status_paused_last_msg",
            "ix_bot_conversations_assigned_staff_user_id",
            "ix_bot_conversations_last_message_at",
            "ix_bot_conversations_last_outbound_at",
            "ix_bot_conversations_last_inbound_at",
            "ix_bot_conversations_bot_paused",
            "ix_bot_conversations_status",
            "ix_bot_conversations_identity_id",
            "ix_bot_conversations_phone_e164",
            "ix_bot_conversations_channel",
            "ix_bot_conversations_uuid",
        ):
            if name in idx:
                op.drop_index(name, table_name="bot_conversations")
        op.drop_table("bot_conversations")

    if _has_table(bind, "bot_contact_identities"):
        idx = _index_names(bind, "bot_contact_identities")
        for name in (
            "ix_bot_contact_identities_is_new_contact",
            "ix_bot_contact_identities_candidate_id",
            "ix_bot_contact_identities_is_candidate",
            "ix_bot_contact_identities_client_id",
            "ix_bot_contact_identities_is_client",
            "ix_bot_contact_identities_identity_status",
            "ix_bot_contact_identities_phone_e164",
        ):
            if name in idx:
                op.drop_index(name, table_name="bot_contact_identities")
        op.drop_table("bot_contact_identities")
