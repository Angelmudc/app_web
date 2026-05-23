"""reemplazos fases operativas minimas

Revision ID: 20260522_1200
Revises: 20260521_1200
Create Date: 2026-05-22 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260522_1200"
down_revision = "20260521_1200"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("reemplazos"):
        return

    cols = {c["name"] for c in inspector.get_columns("reemplazos")}

    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        if "fase" not in cols:
            batch_op.add_column(sa.Column("fase", sa.String(length=30), nullable=True))
        if "fecha_entrada_programada" not in cols:
            batch_op.add_column(sa.Column("fecha_entrada_programada", sa.DateTime(), nullable=True))
        if "seguimiento_24h_at" not in cols:
            batch_op.add_column(sa.Column("seguimiento_24h_at", sa.DateTime(), nullable=True))
        if "seguimiento_7d_at" not in cols:
            batch_op.add_column(sa.Column("seguimiento_7d_at", sa.DateTime(), nullable=True))
        if "motivo_reemplazo_categoria" not in cols:
            batch_op.add_column(sa.Column("motivo_reemplazo_categoria", sa.String(length=50), nullable=True))

    op.execute("CREATE INDEX IF NOT EXISTS ix_reemplazos_fase ON reemplazos (fase)")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("reemplazos"):
        return

    cols = {c["name"] for c in inspector.get_columns("reemplazos")}

    op.execute("DROP INDEX IF EXISTS ix_reemplazos_fase")

    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        if "motivo_reemplazo_categoria" in cols:
            batch_op.drop_column("motivo_reemplazo_categoria")
        if "seguimiento_7d_at" in cols:
            batch_op.drop_column("seguimiento_7d_at")
        if "seguimiento_24h_at" in cols:
            batch_op.drop_column("seguimiento_24h_at")
        if "fecha_entrada_programada" in cols:
            batch_op.drop_column("fecha_entrada_programada")
        if "fase" in cols:
            batch_op.drop_column("fase")
