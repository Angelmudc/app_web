"""create bot_candidate_intakes

Revision ID: 20260515_1700
Revises: 20260515_1100
Create Date: 2026-05-15 17:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260515_1700"
down_revision = "20260515_1100"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_candidate_intakes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("bot_conversations.id"), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("bot_candidate_drafts.id"), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending_review'")),
        sa.Column("source", sa.String(length=50), nullable=False, server_default=sa.text("'whatsapp_bot'")),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("full_name", sa.String(length=160), nullable=True),
        sa.Column("age", sa.String(length=20), nullable=True),
        sa.Column("city_sector", sa.String(length=180), nullable=True),
        sa.Column("experience", sa.Text(), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column("availability", sa.String(length=180), nullable=True),
        sa.Column("references_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("quality_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quality_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("duplicate_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("duplicate_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicate_matches", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("ai_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("invalid_answers_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("help_requests_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("timeline", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("draft_id", name="uq_bot_candidate_intakes_draft_id"),
    )
    op.create_index("ix_bot_candidate_intakes_status_created", "bot_candidate_intakes", ["status", "created_at"])
    op.create_index("ix_bot_candidate_intakes_conversation", "bot_candidate_intakes", ["conversation_id", "created_at"])
    op.create_index("ix_bot_candidate_intakes_phone", "bot_candidate_intakes", ["phone"])
    op.create_index("ix_bot_candidate_intakes_duplicate_score", "bot_candidate_intakes", ["duplicate_score"])


def downgrade():
    op.drop_index("ix_bot_candidate_intakes_duplicate_score", table_name="bot_candidate_intakes")
    op.drop_index("ix_bot_candidate_intakes_phone", table_name="bot_candidate_intakes")
    op.drop_index("ix_bot_candidate_intakes_conversation", table_name="bot_candidate_intakes")
    op.drop_index("ix_bot_candidate_intakes_status_created", table_name="bot_candidate_intakes")
    op.drop_table("bot_candidate_intakes")
