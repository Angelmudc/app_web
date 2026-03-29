"""create staff presence state table for control room live status

Revision ID: 20260329_1300
Revises: 20260329_1230
Create Date: 2026-03-29 13:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260329_1300"
down_revision = "20260329_1230"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(idx.get("name") or "") for idx in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    if _has_table(bind, "staff_presence_state"):
        return

    op.create_table(
        "staff_presence_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=False),
        sa.Column("session_id", sa.String(length=120), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False, server_default=sa.text("''")),
        sa.Column("route_label", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column("entity_type", sa.String(length=40), nullable=False, server_default=sa.text("''")),
        sa.Column("entity_id", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("entity_name", sa.String(length=160), nullable=False, server_default=sa.text("''")),
        sa.Column("entity_code", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("current_action", sa.String(length=80), nullable=False, server_default=sa.text("''")),
        sa.Column("action_label", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column("tab_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_idle", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_typing", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("has_unsaved_changes", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("modal_open", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("lock_owner", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column("client_status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("page_title", sa.String(length=160), nullable=False, server_default=sa.text("''")),
        sa.Column("last_interaction_at", sa.DateTime(), nullable=True),
        sa.Column("state_hash", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "session_id", name="uq_staff_presence_user_session"),
    )

    op.create_index("ix_staff_presence_state_user_id", "staff_presence_state", ["user_id"], unique=False)
    op.create_index("ix_staff_presence_state_session_id", "staff_presence_state", ["session_id"], unique=False)
    op.create_index("ix_staff_presence_state_last_seen_at", "staff_presence_state", ["last_seen_at"], unique=False)
    op.create_index("ix_staff_presence_state_updated_at", "staff_presence_state", ["updated_at"], unique=False)
    op.create_index("ix_staff_presence_state_started_at", "staff_presence_state", ["started_at"], unique=False)
    op.create_index("ix_staff_presence_state_client_status", "staff_presence_state", ["client_status"], unique=False)
    op.create_index("ix_staff_presence_state_tab_visible", "staff_presence_state", ["tab_visible"], unique=False)
    op.create_index("ix_staff_presence_state_is_idle", "staff_presence_state", ["is_idle"], unique=False)
    op.create_index("ix_staff_presence_state_is_typing", "staff_presence_state", ["is_typing"], unique=False)
    op.create_index(
        "ix_staff_presence_state_has_unsaved_changes",
        "staff_presence_state",
        ["has_unsaved_changes"],
        unique=False,
    )
    op.create_index("ix_staff_presence_state_modal_open", "staff_presence_state", ["modal_open"], unique=False)
    op.create_index("ix_staff_presence_state_state_hash", "staff_presence_state", ["state_hash"], unique=False)
    op.create_index("ix_staff_presence_user_last_seen", "staff_presence_state", ["user_id", "last_seen_at"], unique=False)
    op.create_index("ix_staff_presence_entity", "staff_presence_state", ["entity_type", "entity_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "staff_presence_state"):
        return

    names = _index_names(bind, "staff_presence_state")
    for name in (
        "ix_staff_presence_entity",
        "ix_staff_presence_user_last_seen",
        "ix_staff_presence_state_state_hash",
        "ix_staff_presence_state_modal_open",
        "ix_staff_presence_state_has_unsaved_changes",
        "ix_staff_presence_state_is_typing",
        "ix_staff_presence_state_is_idle",
        "ix_staff_presence_state_tab_visible",
        "ix_staff_presence_state_client_status",
        "ix_staff_presence_state_started_at",
        "ix_staff_presence_state_updated_at",
        "ix_staff_presence_state_last_seen_at",
        "ix_staff_presence_state_session_id",
        "ix_staff_presence_state_user_id",
    ):
        if name in names:
            op.drop_index(name, table_name="staff_presence_state")
    op.drop_table("staff_presence_state")
