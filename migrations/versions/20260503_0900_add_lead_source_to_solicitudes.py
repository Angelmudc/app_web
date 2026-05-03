"""add lead_source to solicitudes

Revision ID: 20260503_0900
Revises: 20260502_1400
Create Date: 2026-05-03 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260503_0900"
down_revision = "20260502_1400"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    try:
        return {str(col.get("name") or "") for col in inspect(bind).get_columns(table_name)}
    except Exception:
        return set()


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(idx.get("name") or "") for idx in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    cols = _column_names(bind, "solicitudes")
    if "lead_source" not in cols:
        op.add_column("solicitudes", sa.Column("lead_source", sa.String(length=30), nullable=True))

    idx = _index_names(bind, "solicitudes")
    if "ix_solicitudes_lead_source" not in idx:
        op.create_index("ix_solicitudes_lead_source", "solicitudes", ["lead_source"], unique=False)


def downgrade():
    bind = op.get_bind()
    idx = _index_names(bind, "solicitudes")
    if "ix_solicitudes_lead_source" in idx:
        op.drop_index("ix_solicitudes_lead_source", table_name="solicitudes")

    cols = _column_names(bind, "solicitudes")
    if "lead_source" in cols:
        op.drop_column("solicitudes", "lead_source")
