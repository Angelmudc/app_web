"""outbox relay phase3 c1 hardening

Revision ID: 20260328_1600
Revises: 20260328_1200
Create Date: 2026-03-28 16:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260328_1600"
down_revision = "20260328_1200"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("domain_outbox", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("published_attempts", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("last_error", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("last_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(), nullable=True))

    op.create_index("ix_domain_outbox_correlation_id", "domain_outbox", ["correlation_id"])
    op.create_index("ix_domain_outbox_idempotency_key", "domain_outbox", ["idempotency_key"])
    op.create_index("ix_domain_outbox_next_retry_at", "domain_outbox", ["next_retry_at"])

    op.create_table(
        "outbox_consumer_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("consumer_name", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_outbox_consumer_event"),
    )
    op.create_index(
        "ix_outbox_consumer_receipts_consumer_name",
        "outbox_consumer_receipts",
        ["consumer_name"],
    )
    op.create_index(
        "ix_outbox_consumer_receipts_event_id",
        "outbox_consumer_receipts",
        ["event_id"],
    )
    op.create_index(
        "ix_outbox_consumer_receipts_processed_at",
        "outbox_consumer_receipts",
        ["processed_at"],
    )


def downgrade():
    op.drop_index("ix_outbox_consumer_receipts_processed_at", table_name="outbox_consumer_receipts")
    op.drop_index("ix_outbox_consumer_receipts_event_id", table_name="outbox_consumer_receipts")
    op.drop_index("ix_outbox_consumer_receipts_consumer_name", table_name="outbox_consumer_receipts")
    op.drop_table("outbox_consumer_receipts")

    op.drop_index("ix_domain_outbox_next_retry_at", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_idempotency_key", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_correlation_id", table_name="domain_outbox")

    with op.batch_alter_table("domain_outbox", schema=None) as batch_op:
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("published_attempts")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("correlation_id")
        batch_op.drop_column("schema_version")
