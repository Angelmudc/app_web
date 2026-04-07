"""add staff assignment fields to chat conversations

Revision ID: 20260407_1300
Revises: 20260407_1200
Create Date: 2026-04-07 13:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260407_1300"
down_revision = "20260407_1200"
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
        return any(str(c.get("name") or "") == column_name for c in cols)
    except Exception:
        return False


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(ix.get("name") or "") for ix in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def _fk_names(bind, table_name: str) -> set[str]:
    try:
        return {str(fk.get("name") or "") for fk in inspect(bind).get_foreign_keys(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "chat_conversations"):
        return

    if not _has_column(bind, "chat_conversations", "assigned_staff_user_id"):
        op.add_column("chat_conversations", sa.Column("assigned_staff_user_id", sa.Integer(), nullable=True))
    if not _has_column(bind, "chat_conversations", "assigned_at"):
        op.add_column("chat_conversations", sa.Column("assigned_at", sa.DateTime(), nullable=True))

    fk_names = _fk_names(bind, "chat_conversations")
    if "fk_chat_conversations_assigned_staff_user_id" not in fk_names:
        op.create_foreign_key(
            "fk_chat_conversations_assigned_staff_user_id",
            "chat_conversations",
            "staff_users",
            ["assigned_staff_user_id"],
            ["id"],
        )

    idx_names = _index_names(bind, "chat_conversations")
    for name, cols in (
        ("ix_chat_conversations_assigned_staff_user_id", ["assigned_staff_user_id"]),
        ("ix_chat_conversations_assigned_at", ["assigned_at"]),
        ("ix_chat_conv_assigned_staff_last_msg", ["assigned_staff_user_id", "last_message_at"]),
    ):
        if name not in idx_names:
            op.create_index(name, "chat_conversations", cols, unique=False)


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "chat_conversations"):
        return

    idx_names = _index_names(bind, "chat_conversations")
    for name in (
        "ix_chat_conv_assigned_staff_last_msg",
        "ix_chat_conversations_assigned_at",
        "ix_chat_conversations_assigned_staff_user_id",
    ):
        if name in idx_names:
            op.drop_index(name, table_name="chat_conversations")

    fk_names = _fk_names(bind, "chat_conversations")
    if "fk_chat_conversations_assigned_staff_user_id" in fk_names:
        op.drop_constraint("fk_chat_conversations_assigned_staff_user_id", "chat_conversations", type_="foreignkey")

    if _has_column(bind, "chat_conversations", "assigned_at"):
        op.drop_column("chat_conversations", "assigned_at")
    if _has_column(bind, "chat_conversations", "assigned_staff_user_id"):
        op.drop_column("chat_conversations", "assigned_staff_user_id")
