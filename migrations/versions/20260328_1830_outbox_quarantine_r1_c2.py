"""outbox quarantine and retry cap r1 c2

Revision ID: 20260328_1830
Revises: 20260328_1730
Create Date: 2026-03-28 18:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260328_1830"
down_revision = "20260328_1730"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("domain_outbox", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("relay_status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'"))
        )
        batch_op.add_column(sa.Column("first_failed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("quarantined_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("quarantine_reason", sa.String(length=80), nullable=True))

    op.create_index("ix_domain_outbox_relay_status", "domain_outbox", ["relay_status"])
    op.create_index("ix_domain_outbox_quarantined_at", "domain_outbox", ["quarantined_at"])

    op.execute("UPDATE domain_outbox SET relay_status='published' WHERE published_at IS NOT NULL")
    op.execute("UPDATE domain_outbox SET relay_status='pending' WHERE published_at IS NULL AND (relay_status IS NULL OR relay_status='')")


def downgrade():
    op.drop_index("ix_domain_outbox_quarantined_at", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_relay_status", table_name="domain_outbox")

    with op.batch_alter_table("domain_outbox", schema=None) as batch_op:
        batch_op.drop_column("quarantine_reason")
        batch_op.drop_column("quarantined_at")
        batch_op.drop_column("first_failed_at")
        batch_op.drop_column("relay_status")
