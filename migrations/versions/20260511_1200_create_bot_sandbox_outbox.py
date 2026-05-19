"""create bot sandbox outbox table

Revision ID: 20260511_1200
Revises: 20260509_1000
Create Date: 2026-05-11 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260511_1200"
down_revision = "20260509_1000"
branch_labels = None
depends_on = None


ALLOWED_STATES = ("queued", "processing", "simulated_sent", "blocked", "failed")


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


def _constraint_names(bind, table_name: str) -> set[str]:
    try:
        return {str(c.get("name") or "") for c in inspect(bind).get_unique_constraints(table_name)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "bot_sandbox_outbox"):
        op.create_table(
            "bot_sandbox_outbox",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
            sa.Column("bot_message_id", sa.Integer(), sa.ForeignKey("bot_messages.id"), nullable=False),
            sa.Column("phone_e164", sa.String(length=20), nullable=False),
            sa.Column("provider", sa.String(length=30), nullable=False, server_default=sa.text("'fake'")),
            sa.Column("state", sa.String(length=30), nullable=False, server_default=sa.text("'queued'")),
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("failure_reason", sa.String(length=255), nullable=True),
            sa.Column("queued_at", sa.DateTime(), nullable=True),
            sa.Column("processing_at", sa.DateTime(), nullable=True),
            sa.Column("simulated_sent_at", sa.DateTime(), nullable=True),
            sa.Column("blocked_at", sa.DateTime(), nullable=True),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(), nullable=True),
            sa.Column("last_transition_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("bot_message_id", name="uq_bot_sandbox_outbox_message_id"),
            sa.CheckConstraint(
                "state IN ('queued','processing','simulated_sent','blocked','failed')",
                name="ck_bot_sandbox_outbox_state_allowed",
            ),
            sa.CheckConstraint("retry_count >= 0", name="ck_bot_sandbox_outbox_retry_non_negative"),
        )

    idx = _index_names(bind, "bot_sandbox_outbox")
    for name, cols in (
        ("ix_bot_sandbox_outbox_conversation_id", ["conversation_id"]),
        ("ix_bot_sandbox_outbox_bot_message_id", ["bot_message_id"]),
        ("ix_bot_sandbox_outbox_phone_e164", ["phone_e164"]),
        ("ix_bot_sandbox_outbox_provider", ["provider"]),
        ("ix_bot_sandbox_outbox_state", ["state"]),
        ("ix_bot_sandbox_outbox_queued_at", ["queued_at"]),
        ("ix_bot_sandbox_outbox_processing_at", ["processing_at"]),
        ("ix_bot_sandbox_outbox_simulated_sent_at", ["simulated_sent_at"]),
        ("ix_bot_sandbox_outbox_blocked_at", ["blocked_at"]),
        ("ix_bot_sandbox_outbox_failed_at", ["failed_at"]),
        ("ix_bot_sandbox_outbox_next_retry_at", ["next_retry_at"]),
        ("ix_bot_sandbox_outbox_last_transition_at", ["last_transition_at"]),
        ("ix_bot_sandbox_outbox_created_at", ["created_at"]),
        ("ix_bot_sandbox_outbox_updated_at", ["updated_at"]),
        ("ix_bot_sandbox_outbox_state_retry", ["state", "retry_count", "next_retry_at"]),
        ("ix_bot_sandbox_outbox_conversation_created", ["conversation_id", "created_at"]),
        ("ix_bot_sandbox_outbox_state_created", ["state", "created_at"]),
    ):
        if name not in idx:
            op.create_index(name, "bot_sandbox_outbox", cols, unique=False)

    uniques = _constraint_names(bind, "bot_sandbox_outbox")
    if "uq_bot_sandbox_outbox_message_id" not in uniques:
        op.create_unique_constraint(
            "uq_bot_sandbox_outbox_message_id",
            "bot_sandbox_outbox",
            ["bot_message_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "bot_sandbox_outbox"):
        return

    idx = _index_names(bind, "bot_sandbox_outbox")
    for name in (
        "ix_bot_sandbox_outbox_state_created",
        "ix_bot_sandbox_outbox_conversation_created",
        "ix_bot_sandbox_outbox_state_retry",
        "ix_bot_sandbox_outbox_updated_at",
        "ix_bot_sandbox_outbox_created_at",
        "ix_bot_sandbox_outbox_last_transition_at",
        "ix_bot_sandbox_outbox_next_retry_at",
        "ix_bot_sandbox_outbox_failed_at",
        "ix_bot_sandbox_outbox_blocked_at",
        "ix_bot_sandbox_outbox_simulated_sent_at",
        "ix_bot_sandbox_outbox_processing_at",
        "ix_bot_sandbox_outbox_queued_at",
        "ix_bot_sandbox_outbox_state",
        "ix_bot_sandbox_outbox_provider",
        "ix_bot_sandbox_outbox_phone_e164",
        "ix_bot_sandbox_outbox_bot_message_id",
        "ix_bot_sandbox_outbox_conversation_id",
    ):
        if name in idx:
            op.drop_index(name, table_name="bot_sandbox_outbox")

    op.drop_table("bot_sandbox_outbox")
