"""add pendiente_servicio to estado_solicitud_enum

Revision ID: 20260526_1100
Revises: 20260525_1300
Create Date: 2026-05-26 11:00:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260526_1100"
down_revision = "20260525_1300"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE estado_solicitud_enum ADD VALUE IF NOT EXISTS 'pendiente_servicio'")


def downgrade():
    # PostgreSQL no soporta remover valores de ENUM en forma directa y segura.
    # Se deja sin operación para evitar pérdida de datos o recreación destructiva del tipo.
    pass
