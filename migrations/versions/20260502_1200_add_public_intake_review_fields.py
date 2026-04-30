"""add public intake review fields on solicitudes

Revision ID: 20260502_1200
Revises: 20260502_0900
Create Date: 2026-05-02 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260502_1200"
down_revision = "20260502_0900"
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

    if "public_form_source" not in cols:
        op.add_column("solicitudes", sa.Column("public_form_source", sa.String(length=30), nullable=True))
    if "review_status" not in cols:
        op.add_column("solicitudes", sa.Column("review_status", sa.String(length=20), nullable=True))
    if "reviewed_at" not in cols:
        op.add_column("solicitudes", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    if "reviewed_by" not in cols:
        op.add_column("solicitudes", sa.Column("reviewed_by", sa.String(length=100), nullable=True))

    idx = _index_names(bind, "solicitudes")
    if "ix_solicitudes_public_form_source" not in idx:
        op.create_index("ix_solicitudes_public_form_source", "solicitudes", ["public_form_source"], unique=False)
    if "ix_solicitudes_review_status" not in idx:
        op.create_index("ix_solicitudes_review_status", "solicitudes", ["review_status"], unique=False)
    if "ix_solicitudes_reviewed_at" not in idx:
        op.create_index("ix_solicitudes_reviewed_at", "solicitudes", ["reviewed_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    idx = _index_names(bind, "solicitudes")
    if "ix_solicitudes_reviewed_at" in idx:
        op.drop_index("ix_solicitudes_reviewed_at", table_name="solicitudes")
    if "ix_solicitudes_review_status" in idx:
        op.drop_index("ix_solicitudes_review_status", table_name="solicitudes")
    if "ix_solicitudes_public_form_source" in idx:
        op.drop_index("ix_solicitudes_public_form_source", table_name="solicitudes")

    cols = _column_names(bind, "solicitudes")
    if "reviewed_by" in cols:
        op.drop_column("solicitudes", "reviewed_by")
    if "reviewed_at" in cols:
        op.drop_column("solicitudes", "reviewed_at")
    if "review_status" in cols:
        op.drop_column("solicitudes", "review_status")
    if "public_form_source" in cols:
        op.drop_column("solicitudes", "public_form_source")
