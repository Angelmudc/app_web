"""add liberada status to solicitud_candidata_status_enum

Revision ID: 20260305_1200
Revises: 20260305_0900
Create Date: 2026-03-05 12:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260305_1200"
down_revision = "20260305_0900"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE solicitud_candidata_status_enum ADD VALUE IF NOT EXISTS 'liberada'")


def downgrade():
    # PostgreSQL no soporta DROP VALUE simple de ENUM sin recrear tipo.
    pass
