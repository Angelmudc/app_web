"""Repair origin trace schema drift for candidatas/reclutas

Revision ID: 20260312_1300
Revises: 20260312_1200
Create Date: 2026-03-12 13:00:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260312_1300"
down_revision = "20260312_1200"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Reparacion idempotente: no elimina ni modifica datos existentes.
        op.execute("ALTER TABLE candidatas ADD COLUMN IF NOT EXISTS origen_registro VARCHAR(32)")
        op.execute("ALTER TABLE candidatas ADD COLUMN IF NOT EXISTS creado_por_staff VARCHAR(100)")
        op.execute("ALTER TABLE candidatas ADD COLUMN IF NOT EXISTS creado_desde_ruta VARCHAR(120)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_candidatas_origen_registro ON candidatas (origen_registro)")

        op.execute("ALTER TABLE reclutas_perfiles ADD COLUMN IF NOT EXISTS origen_registro VARCHAR(32)")
        op.execute("ALTER TABLE reclutas_perfiles ADD COLUMN IF NOT EXISTS creado_desde_ruta VARCHAR(120)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_reclutas_perfiles_origen_registro ON reclutas_perfiles (origen_registro)")


def downgrade():
    # Reparacion de drift: no se hace downgrade destructivo.
    pass
