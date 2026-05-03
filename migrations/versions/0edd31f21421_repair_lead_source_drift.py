"""repair lead_source drift

Revision ID: 0edd31f21421
Revises: 03aae9badd7f
Create Date: 2026-05-02 21:54:04.910951

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '0edd31f21421'
down_revision = '03aae9badd7f'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {str(col.get("name") or "") for col in inspect(bind).get_columns("solicitudes")}
    if "lead_source" not in cols:
        op.add_column("solicitudes", sa.Column("lead_source", sa.String(length=30), nullable=True))

    idx = {str(item.get("name") or "") for item in inspect(bind).get_indexes("solicitudes")}
    if "ix_solicitudes_lead_source" not in idx:
        op.create_index("ix_solicitudes_lead_source", "solicitudes", ["lead_source"], unique=False)


def downgrade():
    bind = op.get_bind()
    idx = {str(item.get("name") or "") for item in inspect(bind).get_indexes("solicitudes")}
    if "ix_solicitudes_lead_source" in idx:
        op.drop_index("ix_solicitudes_lead_source", table_name="solicitudes")

    cols = {str(col.get("name") or "") for col in inspect(bind).get_columns("solicitudes")}
    if "lead_source" in cols:
        op.drop_column("solicitudes", "lead_source")
