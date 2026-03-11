"""Add public recruitment fields to candidatas

Revision ID: 20260310_1730
Revises: 20260305_1900
Create Date: 2026-03-10 17:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260310_1730"
down_revision = "20260305_1900"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("candidatas", sa.Column("disponibilidad_inicio", sa.String(length=80), nullable=True))
    op.add_column("candidatas", sa.Column("trabaja_con_ninos", sa.Boolean(), nullable=True))
    op.add_column("candidatas", sa.Column("trabaja_con_mascotas", sa.Boolean(), nullable=True))
    op.add_column("candidatas", sa.Column("puede_dormir_fuera", sa.Boolean(), nullable=True))
    op.add_column("candidatas", sa.Column("sueldo_esperado", sa.String(length=80), nullable=True))
    op.add_column("candidatas", sa.Column("motivacion_trabajo", sa.String(length=350), nullable=True))


def downgrade():
    op.drop_column("candidatas", "motivacion_trabajo")
    op.drop_column("candidatas", "sueldo_esperado")
    op.drop_column("candidatas", "puede_dormir_fuera")
    op.drop_column("candidatas", "trabaja_con_mascotas")
    op.drop_column("candidatas", "trabaja_con_ninos")
    op.drop_column("candidatas", "disponibilidad_inicio")
