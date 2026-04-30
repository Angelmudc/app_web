"""add cliente search indexes for admin lookup scalability

Revision ID: 20260502_0900
Revises: 20260501_1000
Create Date: 2026-05-02 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260502_0900"
down_revision = "20260501_1000"
branch_labels = None
depends_on = None


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(idx.get("name") or "") for idx in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    # B-Tree básicos útiles para exact/prefix lookups y ORDER BY/filters complementarios.
    names = _index_names(bind, "clientes")
    if "ix_clientes_nombre_completo" not in names:
        op.create_index("ix_clientes_nombre_completo", "clientes", ["nombre_completo"], unique=False)
    if "ix_clientes_telefono" not in names:
        op.create_index("ix_clientes_telefono", "clientes", ["telefono"], unique=False)

    # En PostgreSQL, la búsqueda usa mucho ILIKE '%texto%'; B-Tree no acelera ese patrón.
    # Trigram GIN sí acelera LIKE/ILIKE contains.
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_nombre_completo_trgm "
                "ON clientes USING GIN (nombre_completo gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_codigo_trgm "
                "ON clientes USING GIN (codigo gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_email_trgm "
                "ON clientes USING GIN (email gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_email_norm_trgm "
                "ON clientes USING GIN (email_norm gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_telefono_trgm "
                "ON clientes USING GIN (telefono gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_telefono_norm_trgm "
                "ON clientes USING GIN (telefono_norm gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_clientes_email_lower_trgm "
                "ON clientes USING GIN (lower(email) gin_trgm_ops)"
            )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX IF EXISTS ix_clientes_email_lower_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_telefono_norm_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_telefono_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_email_norm_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_email_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_codigo_trgm")
            op.execute("DROP INDEX IF EXISTS ix_clientes_nombre_completo_trgm")

    # Solo removemos los índices creados aquí si existen.
    names = _index_names(bind, "clientes")
    if "ix_clientes_telefono" in names:
        op.drop_index("ix_clientes_telefono", table_name="clientes")
    if "ix_clientes_nombre_completo" in names:
        op.drop_index("ix_clientes_nombre_completo", table_name="clientes")
