"""add entrevista_publica_resumen to candidatas_web

Revision ID: 20260520_1415
Revises: a214d9bb230b
Create Date: 2026-05-20 14:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260520_1415"
down_revision = "a214d9bb230b"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "candidatas_web" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("candidatas_web")}
    if "entrevista_publica_resumen" not in cols:
        op.add_column(
            "candidatas_web",
            sa.Column("entrevista_publica_resumen", sa.Text(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "candidatas_web" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("candidatas_web")}
    if "entrevista_publica_resumen" in cols:
        op.drop_column("candidatas_web", "entrevista_publica_resumen")
