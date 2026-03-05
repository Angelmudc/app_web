"""Create staff audit logs table

Revision ID: 20260305_1900
Revises: 20260305_1800
Create Date: 2026-03-05 19:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260305_1900"
down_revision = "20260305_1800"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "staff_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=255), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("summary", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("changes_json", sa.JSON(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["staff_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_staff_audit_logs_created_at", "staff_audit_logs", ["created_at"], unique=False)
    op.create_index("ix_staff_audit_logs_actor_user_id", "staff_audit_logs", ["actor_user_id"], unique=False)
    op.create_index("ix_staff_audit_logs_action_type", "staff_audit_logs", ["action_type"], unique=False)
    op.create_index("ix_staff_audit_logs_entity_type", "staff_audit_logs", ["entity_type"], unique=False)
    op.create_index("ix_staff_audit_logs_entity_id", "staff_audit_logs", ["entity_id"], unique=False)
    op.create_index("ix_staff_audit_logs_success", "staff_audit_logs", ["success"], unique=False)


def downgrade():
    op.drop_index("ix_staff_audit_logs_success", table_name="staff_audit_logs")
    op.drop_index("ix_staff_audit_logs_entity_id", table_name="staff_audit_logs")
    op.drop_index("ix_staff_audit_logs_entity_type", table_name="staff_audit_logs")
    op.drop_index("ix_staff_audit_logs_action_type", table_name="staff_audit_logs")
    op.drop_index("ix_staff_audit_logs_actor_user_id", table_name="staff_audit_logs")
    op.drop_index("ix_staff_audit_logs_created_at", table_name="staff_audit_logs")
    op.drop_table("staff_audit_logs")
