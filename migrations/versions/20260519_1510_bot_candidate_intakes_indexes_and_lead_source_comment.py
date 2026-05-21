"""add bot_candidate_intakes missing indexes and normalize lead_source comment

Revision ID: 20260519_1510
Revises: 20260515_1700
Create Date: 2026-05-19 15:10:00
"""

from alembic import op


revision = "20260519_1510"
down_revision = "20260515_1700"
branch_labels = None
depends_on = None


def upgrade():
    # Non-destructive hardening: create only missing indexes.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_approved_at "
        "ON bot_candidate_intakes (approved_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_approved_by "
        "ON bot_candidate_intakes (approved_by)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_candidate_id "
        "ON bot_candidate_intakes (candidate_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_completed_at "
        "ON bot_candidate_intakes (completed_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_conversation_id "
        "ON bot_candidate_intakes (conversation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_created_at "
        "ON bot_candidate_intakes (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_draft_id "
        "ON bot_candidate_intakes (draft_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_full_name "
        "ON bot_candidate_intakes (full_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_rejected_at "
        "ON bot_candidate_intakes (rejected_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_review_id "
        "ON bot_candidate_intakes (review_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_status "
        "ON bot_candidate_intakes (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bot_candidate_intakes_updated_at "
        "ON bot_candidate_intakes (updated_at)"
    )

    # Metadata-only change; does not rewrite rows.
    op.execute(
        "COMMENT ON COLUMN solicitudes.lead_source IS "
        "'Fuente de captación del lead: instagram, facebook, tiktok, google, direct.'"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_status")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_review_id")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_rejected_at")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_full_name")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_draft_id")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_created_at")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_completed_at")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_candidate_id")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_approved_by")
    op.execute("DROP INDEX IF EXISTS ix_bot_candidate_intakes_approved_at")

    op.execute("COMMENT ON COLUMN solicitudes.lead_source IS NULL")
