"""add terms acceptance evidence fields to solicitudes

Revision ID: 20260501_1300
Revises: 20260502_1200
Create Date: 2026-05-01 13:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260501_1300"
down_revision = "20260502_1200"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    try:
        return {str(col.get("name") or "") for col in inspect(bind).get_columns(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    cols = _column_names(bind, "solicitudes")

    if "terms_accepted" not in cols:
        op.add_column(
            "solicitudes",
            sa.Column("terms_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "terms_accepted_at" not in cols:
        op.add_column("solicitudes", sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))
    if "terms_version" not in cols:
        op.add_column("solicitudes", sa.Column("terms_version", sa.String(length=20), nullable=True))
    if "terms_ip" not in cols:
        op.add_column("solicitudes", sa.Column("terms_ip", sa.String(length=64), nullable=True))
    if "terms_user_agent" not in cols:
        op.add_column("solicitudes", sa.Column("terms_user_agent", sa.String(length=512), nullable=True))


def downgrade():
    bind = op.get_bind()
    cols = _column_names(bind, "solicitudes")

    if "terms_user_agent" in cols:
        op.drop_column("solicitudes", "terms_user_agent")
    if "terms_ip" in cols:
        op.drop_column("solicitudes", "terms_ip")
    if "terms_version" in cols:
        op.drop_column("solicitudes", "terms_version")
    if "terms_accepted_at" in cols:
        op.drop_column("solicitudes", "terms_accepted_at")
    if "terms_accepted" in cols:
        op.drop_column("solicitudes", "terms_accepted")
