"""add scope_mode to catalogos_privados

Revision ID: 20260520_1700
Revises: 20260520_1500
Create Date: 2026-05-20 17:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260520_1700"
down_revision = "20260520_1500"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("catalogos_privados"):
        return

    cols = {c["name"] for c in inspector.get_columns("catalogos_privados")}
    if "scope_mode" not in cols:
        with op.batch_alter_table("catalogos_privados", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "scope_mode",
                    sa.String(length=32),
                    nullable=False,
                    server_default=sa.text("'manual_shortlist'"),
                )
            )

    # Backfill defensivo para filas legacy.
    op.execute(
        "UPDATE catalogos_privados "
        "SET scope_mode = 'manual_shortlist' "
        "WHERE scope_mode IS NULL OR scope_mode = ''"
    )

    # Índice defensivo (IF NOT EXISTS compatible en Postgres/SQLite modernos).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_catalogos_privados_scope_mode "
        "ON catalogos_privados (scope_mode)"
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("catalogos_privados"):
        return

    cols = {c["name"] for c in inspector.get_columns("catalogos_privados")}
    if "scope_mode" in cols:
        op.execute("DROP INDEX IF EXISTS ix_catalogos_privados_scope_mode")
        with op.batch_alter_table("catalogos_privados", schema=None) as batch_op:
            batch_op.drop_column("scope_mode")
