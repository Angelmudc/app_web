"""Add espera_pago state and minimal audit fields for staff flows

Revision ID: 20260305_1800
Revises: 20260305_1400
Create Date: 2026-03-05 18:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260305_1800"
down_revision = "20260305_1400"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TYPE estado_solicitud_enum ADD VALUE IF NOT EXISTS 'espera_pago'")

    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("estado_previo_espera_pago", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("fecha_cambio_espera_pago", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("usuario_cambio_espera_pago", sa.String(length=100), nullable=True))

    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        batch_op.add_column(sa.Column("estado_previo_solicitud", sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        batch_op.drop_column("estado_previo_solicitud")

    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.drop_column("usuario_cambio_espera_pago")
        batch_op.drop_column("fecha_cambio_espera_pago")
        batch_op.drop_column("estado_previo_espera_pago")

