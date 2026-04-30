"""add unique indexes for cliente normalized contacts

Revision ID: 20260501_1000
Revises: 20260501_0940
Create Date: 2026-05-01 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_1000"
down_revision = "20260501_0940"
branch_labels = None
depends_on = None


def _has_duplicates(conn) -> tuple[bool, int, int]:
    email_dups = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT email_norm
                FROM clientes
                WHERE email_norm IS NOT NULL AND email_norm <> ''
                GROUP BY email_norm
                HAVING COUNT(*) > 1
            ) q
            """
        )
    ).scalar() or 0
    phone_dups = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT telefono_norm
                FROM clientes
                WHERE telefono_norm IS NOT NULL AND telefono_norm <> ''
                GROUP BY telefono_norm
                HAVING COUNT(*) > 1
            ) q
            """
        )
    ).scalar() or 0
    return (int(email_dups) > 0 or int(phone_dups) > 0), int(email_dups), int(phone_dups)


def upgrade():
    conn = op.get_bind()
    has_dups, email_dups, phone_dups = _has_duplicates(conn)
    if has_dups:
        raise RuntimeError(
            f"No se pueden crear índices únicos: duplicados detectados (email_groups={email_dups}, telefono_groups={phone_dups})."
        )

    # Índices únicos parciales: ignoran NULL y vacío.
    op.create_index(
        "uq_clientes_email_norm_not_empty",
        "clientes",
        ["email_norm"],
        unique=True,
        postgresql_where=sa.text("email_norm IS NOT NULL AND email_norm <> ''"),
        sqlite_where=sa.text("email_norm IS NOT NULL AND email_norm <> ''"),
    )
    op.create_index(
        "uq_clientes_telefono_norm_not_empty",
        "clientes",
        ["telefono_norm"],
        unique=True,
        postgresql_where=sa.text("telefono_norm IS NOT NULL AND telefono_norm <> ''"),
        sqlite_where=sa.text("telefono_norm IS NOT NULL AND telefono_norm <> ''"),
    )


def downgrade():
    op.drop_index("uq_clientes_telefono_norm_not_empty", table_name="clientes")
    op.drop_index("uq_clientes_email_norm_not_empty", table_name="clientes")
