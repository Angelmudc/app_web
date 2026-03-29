"""add fecha_seguimiento_manual to solicitudes

Revision ID: 20260327_1100
Revises: 20260324_1200
Create Date: 2026-03-27 11:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260327_1100"
down_revision = "20260324_1200"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fecha_seguimiento_manual", sa.Date(), nullable=True))
        batch_op.create_index("ix_solicitudes_fecha_seguimiento_manual", ["fecha_seguimiento_manual"], unique=False)


def downgrade():
    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.drop_index("ix_solicitudes_fecha_seguimiento_manual")
        batch_op.drop_column("fecha_seguimiento_manual")

