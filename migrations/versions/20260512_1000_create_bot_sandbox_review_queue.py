"""create bot sandbox review queue table

Revision ID: 20260512_1000
Revises: 20260511_1200
Create Date: 2026-05-12 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260512_1000"
down_revision = "20260511_1200"
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


def _constraint_names(bind, table_name: str) -> set[str]:
    try:
        return {str(c.get("name") or "") for c in inspect(bind).get_unique_constraints(table_name)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "bot_sandbox_review_queue"):
        op.create_table(
            "bot_sandbox_review_queue",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
            sa.Column("inbound_message_id", sa.Integer(), sa.ForeignKey("bot_messages.id"), nullable=False),
            sa.Column("outbound_message_id", sa.Integer(), sa.ForeignKey("bot_messages.id"), nullable=True),
            sa.Column("base_suggested_reply", sa.Text(), nullable=True),
            sa.Column("ai_suggested_reply", sa.Text(), nullable=True),
            sa.Column("final_suggested_reply", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'pending_review'")),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("edited_text", sa.Text(), nullable=True),
            sa.Column("rejection_reason", sa.String(length=255), nullable=True),
            sa.Column("safety_status", sa.String(length=30), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("fallback_reason", sa.String(length=120), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending_review','approved','rejected','edited','simulated_sent','blocked')",
                name="ck_bot_sandbox_review_status_allowed",
            ),
            sa.UniqueConstraint("inbound_message_id", name="uq_bot_sandbox_review_inbound_message_id"),
        )

    idx = _index_names(bind, "bot_sandbox_review_queue")
    for name, cols in (
        ("ix_bot_sandbox_review_queue_conversation_id", ["conversation_id"]),
        ("ix_bot_sandbox_review_queue_inbound_message_id", ["inbound_message_id"]),
        ("ix_bot_sandbox_review_queue_outbound_message_id", ["outbound_message_id"]),
        ("ix_bot_sandbox_review_queue_status", ["status"]),
        ("ix_bot_sandbox_review_queue_reviewer_id", ["reviewer_id"]),
        ("ix_bot_sandbox_review_queue_reviewed_at", ["reviewed_at"]),
        ("ix_bot_sandbox_review_queue_safety_status", ["safety_status"]),
        ("ix_bot_sandbox_review_queue_created_at", ["created_at"]),
        ("ix_bot_sandbox_review_queue_updated_at", ["updated_at"]),
        ("ix_bot_sandbox_review_status_created", ["status", "created_at"]),
        ("ix_bot_sandbox_review_conv_created", ["conversation_id", "created_at"]),
        ("ix_bot_sandbox_review_safety_status", ["safety_status", "created_at"]),
    ):
        if name not in idx:
            op.create_index(name, "bot_sandbox_review_queue", cols, unique=False)

    uniques = _constraint_names(bind, "bot_sandbox_review_queue")
    if "uq_bot_sandbox_review_inbound_message_id" not in uniques:
        op.create_unique_constraint(
            "uq_bot_sandbox_review_inbound_message_id",
            "bot_sandbox_review_queue",
            ["inbound_message_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "bot_sandbox_review_queue"):
        return

    idx = _index_names(bind, "bot_sandbox_review_queue")
    for name in (
        "ix_bot_sandbox_review_safety_status",
        "ix_bot_sandbox_review_conv_created",
        "ix_bot_sandbox_review_status_created",
        "ix_bot_sandbox_review_queue_updated_at",
        "ix_bot_sandbox_review_queue_created_at",
        "ix_bot_sandbox_review_queue_safety_status",
        "ix_bot_sandbox_review_queue_reviewed_at",
        "ix_bot_sandbox_review_queue_reviewer_id",
        "ix_bot_sandbox_review_queue_status",
        "ix_bot_sandbox_review_queue_outbound_message_id",
        "ix_bot_sandbox_review_queue_inbound_message_id",
        "ix_bot_sandbox_review_queue_conversation_id",
    ):
        if name in idx:
            op.drop_index(name, table_name="bot_sandbox_review_queue")

    op.drop_table("bot_sandbox_review_queue")
