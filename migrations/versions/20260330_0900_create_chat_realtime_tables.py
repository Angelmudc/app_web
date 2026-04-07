"""create chat realtime tables for cliente staff inbox

Revision ID: 20260330_0900
Revises: 20260329_1300
Create Date: 2026-03-30 09:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260330_0900"
down_revision = "20260329_1300"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(ix.get("name") or "") for ix in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()

    if not _has_table(bind, "chat_conversations"):
        op.create_table(
            "chat_conversations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("scope_key", sa.String(length=80), nullable=False),
            sa.Column("conversation_type", sa.String(length=20), nullable=False, server_default=sa.text("'general'")),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'open'")),
            sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=False),
            sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id"), nullable=True),
            sa.Column("subject", sa.String(length=200), nullable=True),
            sa.Column("last_message_at", sa.DateTime(), nullable=True),
            sa.Column("last_message_preview", sa.String(length=240), nullable=True),
            sa.Column("last_message_sender_type", sa.String(length=20), nullable=True),
            sa.Column("cliente_unread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("staff_unread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("client_last_read_at", sa.DateTime(), nullable=True),
            sa.Column("staff_last_read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("scope_key", name="uq_chat_conversation_scope_key"),
        )

    conv_indexes = _index_names(bind, "chat_conversations")
    for name, cols in (
        ("ix_chat_conversations_scope_key", ["scope_key"]),
        ("ix_chat_conversations_conversation_type", ["conversation_type"]),
        ("ix_chat_conversations_status", ["status"]),
        ("ix_chat_conversations_cliente_id", ["cliente_id"]),
        ("ix_chat_conversations_solicitud_id", ["solicitud_id"]),
        ("ix_chat_conversations_last_message_at", ["last_message_at"]),
        ("ix_chat_conversations_created_at", ["created_at"]),
        ("ix_chat_conversations_updated_at", ["updated_at"]),
        ("ix_chat_conv_cliente_last_msg", ["cliente_id", "last_message_at"]),
        ("ix_chat_conv_staff_unread", ["staff_unread_count", "last_message_at"]),
        ("ix_chat_conv_cliente_unread", ["cliente_unread_count", "last_message_at"]),
    ):
        if name not in conv_indexes:
            op.create_index(name, "chat_conversations", cols, unique=False)

    if not _has_table(bind, "chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("chat_conversations.id"), nullable=False),
            sa.Column("sender_type", sa.String(length=20), nullable=False),
            sa.Column("sender_cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=True),
            sa.Column("sender_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    msg_indexes = _index_names(bind, "chat_messages")
    for name, cols in (
        ("ix_chat_messages_conversation_id", ["conversation_id"]),
        ("ix_chat_messages_sender_type", ["sender_type"]),
        ("ix_chat_messages_sender_cliente_id", ["sender_cliente_id"]),
        ("ix_chat_messages_sender_staff_user_id", ["sender_staff_user_id"]),
        ("ix_chat_messages_is_deleted", ["is_deleted"]),
        ("ix_chat_messages_created_at", ["created_at"]),
        ("ix_chat_msg_conv_created", ["conversation_id", "created_at"]),
        ("ix_chat_msg_conv_id_desc", ["conversation_id", "id"]),
    ):
        if name not in msg_indexes:
            op.create_index(name, "chat_messages", cols, unique=False)


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "chat_messages"):
        msg_indexes = _index_names(bind, "chat_messages")
        for name in (
            "ix_chat_msg_conv_id_desc",
            "ix_chat_msg_conv_created",
            "ix_chat_messages_created_at",
            "ix_chat_messages_is_deleted",
            "ix_chat_messages_sender_staff_user_id",
            "ix_chat_messages_sender_cliente_id",
            "ix_chat_messages_sender_type",
            "ix_chat_messages_conversation_id",
        ):
            if name in msg_indexes:
                op.drop_index(name, table_name="chat_messages")
        op.drop_table("chat_messages")

    if _has_table(bind, "chat_conversations"):
        conv_indexes = _index_names(bind, "chat_conversations")
        for name in (
            "ix_chat_conv_cliente_unread",
            "ix_chat_conv_staff_unread",
            "ix_chat_conv_cliente_last_msg",
            "ix_chat_conversations_updated_at",
            "ix_chat_conversations_created_at",
            "ix_chat_conversations_last_message_at",
            "ix_chat_conversations_solicitud_id",
            "ix_chat_conversations_cliente_id",
            "ix_chat_conversations_status",
            "ix_chat_conversations_conversation_type",
            "ix_chat_conversations_scope_key",
        ):
            if name in conv_indexes:
                op.drop_index(name, table_name="chat_conversations")
        op.drop_table("chat_conversations")
