"""create staff_users table

Revision ID: 20260304_1600
Revises: af1696bceec6
Create Date: 2026-03-04 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260304_1600"
down_revision = "af1696bceec6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "staff_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="secretaria"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_staff_users_username"), "staff_users", ["username"], unique=True)
    op.create_index(op.f("ix_staff_users_email"), "staff_users", ["email"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_staff_users_email"), table_name="staff_users")
    op.drop_index(op.f("ix_staff_users_username"), table_name="staff_users")
    op.drop_table("staff_users")
