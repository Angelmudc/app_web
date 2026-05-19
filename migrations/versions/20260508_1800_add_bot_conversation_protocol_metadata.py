"""add protocol metadata_json to bot conversations

Revision ID: 20260508_1800
Revises: 20260508_1600
Create Date: 2026-05-08 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_1800"
down_revision = "20260508_1600"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _has_column(bind, table_name: str, column_name: str) -> bool:
    try:
        cols = inspect(bind).get_columns(table_name)
    except Exception:
        return False
    return any((c.get("name") or "") == column_name for c in cols)


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "bot_conversations"):
        return
    if not _has_column(bind, "bot_conversations", "metadata_json"):
        op.add_column(
            "bot_conversations",
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "bot_conversations"):
        return
    if _has_column(bind, "bot_conversations", "metadata_json"):
        op.drop_column("bot_conversations", "metadata_json")
