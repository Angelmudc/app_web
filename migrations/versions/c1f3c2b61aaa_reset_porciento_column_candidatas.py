"""reset porciento column candidatas

Revision ID: c1f3c2b61aaa
Revises: c1a759793386
Create Date: 2026-01-15 15:43:50.810247

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1f3c2b61aaa'
down_revision = 'c1a759793386'
branch_labels = None
depends_on = None


def upgrade():
    # 1) borrar columna (si existe)
    op.execute("ALTER TABLE candidatas DROP COLUMN IF EXISTS porciento;")

    # 2) crear columna nueva limpia
    op.add_column("candidatas", sa.Column("porciento", sa.Numeric(8, 2), nullable=True))

    # opcional: default 0
    # op.execute("ALTER TABLE candidatas ALTER COLUMN porciento SET DEFAULT 0;")


def downgrade():
    # volver a dejarla como estaba (igual la recreamos)
    op.execute("ALTER TABLE candidatas DROP COLUMN IF EXISTS porciento;")
    op.add_column("candidatas", sa.Column("porciento", sa.Numeric(8, 2), nullable=True))