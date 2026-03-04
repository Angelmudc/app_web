"""add search indexes for compat candidata

Revision ID: 20260304_1700
Revises: 20260304_1600
Create Date: 2026-03-04 17:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260304_1700"
down_revision = "20260304_1600"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_candidatas_cedula ON candidatas (cedula)")

    # CREATE INDEX CONCURRENTLY no puede correr dentro de transaccion.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_candidatas_nombre_completo_trgm "
            "ON candidatas USING GIN (nombre_completo gin_trgm_ops)"
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX IF EXISTS ix_candidatas_nombre_completo_trgm")
    op.execute("DROP INDEX IF EXISTS ix_candidatas_cedula")
