"""add cedula_norm_digits with unique partial index

Revision ID: 20260305_1400
Revises: 20260305_1200
Create Date: 2026-03-05 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260305_1400"
down_revision = "20260305_1200"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "candidatas",
        sa.Column(
            "cedula_norm_digits",
            sa.String(length=11),
            nullable=True,
            comment="solo dígitos; usada para prevenir duplicados en nuevas altas",
        ),
    )

    ctx = op.get_context()
    if ctx.dialect.name == "postgresql":
        with ctx.autocommit_block():
            op.execute(
                """
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_candidatas_cedula_norm_digits
                ON candidatas (cedula_norm_digits)
                WHERE cedula_norm_digits IS NOT NULL
                """
            )
    else:
        op.create_index(
            "ux_candidatas_cedula_norm_digits",
            "candidatas",
            ["cedula_norm_digits"],
            unique=True,
        )


def downgrade():
    ctx = op.get_context()
    if ctx.dialect.name == "postgresql":
        with ctx.autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ux_candidatas_cedula_norm_digits")
    else:
        op.drop_index("ux_candidatas_cedula_norm_digits", table_name="candidatas")

    op.drop_column("candidatas", "cedula_norm_digits")
