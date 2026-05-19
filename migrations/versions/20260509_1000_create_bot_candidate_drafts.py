"""create bot candidate drafts table

Revision ID: 20260509_1000
Revises: 20260508_1800
Create Date: 2026-05-09 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260509_1000"
down_revision = "20260508_1800"
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

    if not _has_table(bind, "bot_candidate_drafts"):
        op.create_table(
            "bot_candidate_drafts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("protocol_version", sa.String(length=50), nullable=True),
            sa.Column("draft_status", sa.String(length=30), nullable=False, server_default=sa.text("'draft'")),
            sa.Column("summary_status", sa.String(length=40), nullable=False, server_default=sa.text("'incomplete'")),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source_protocol_entities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source_pending_corrections_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("requires_human", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sensitive_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("conversation_id", name="uq_bot_candidate_draft_conversation_id"),
        )

    idx = _index_names(bind, "bot_candidate_drafts")
    for name, cols in (
        ("ix_bot_candidate_drafts_conversation_id", ["conversation_id"]),
        ("ix_bot_candidate_drafts_draft_status", ["draft_status"]),
        ("ix_bot_candidate_drafts_summary_status", ["summary_status"]),
        ("ix_bot_candidate_drafts_created_by", ["created_by"]),
        ("ix_bot_candidate_drafts_reviewed_by", ["reviewed_by"]),
        ("ix_bot_candidate_drafts_requires_human", ["requires_human"]),
        ("ix_bot_candidate_drafts_sensitive_detected", ["sensitive_detected"]),
        ("ix_bot_candidate_draft_status_updated", ["draft_status", "updated_at"]),
    ):
        if name not in idx:
            op.create_index(name, "bot_candidate_drafts", cols, unique=False)


def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "bot_candidate_drafts"):
        op.drop_table("bot_candidate_drafts")
