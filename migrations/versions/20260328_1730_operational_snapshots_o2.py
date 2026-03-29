"""operational snapshots o2 minimal retention

Revision ID: 20260328_1730
Revises: 20260328_1600
Create Date: 2026-03-28 17:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260328_1730"
down_revision = "20260328_1600"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default=sa.text("15")),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_operational_metric_snapshots_captured_at",
        "operational_metric_snapshots",
        ["captured_at"],
    )


def downgrade():
    op.drop_index("ix_operational_metric_snapshots_captured_at", table_name="operational_metric_snapshots")
    op.drop_table("operational_metric_snapshots")
