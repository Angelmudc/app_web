"""Add MFA fields to staff_users

Revision ID: 20260319_1600
Revises: 20260312_1700
Create Date: 2026-03-19 16:00:00
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260319_1600"
down_revision = "20260312_1700"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set:
    try:
        return {col.get("name") for col in inspect(bind).get_columns(table_name)}
    except Exception:
        return set()


def _add_mfa_columns_without_introspection() -> None:
    op.add_column(
        "staff_users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("staff_users", sa.Column("mfa_secret", sa.String(length=512), nullable=True))
    op.add_column("staff_users", sa.Column("mfa_last_timestep", sa.Integer(), nullable=True))


def upgrade():
    if context.is_offline_mode():
        # En modo --sql no hay inspección disponible (MockConnection).
        _add_mfa_columns_without_introspection()
        return

    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table("staff_users"):
        return

    names = _column_names(bind, "staff_users")

    if "mfa_enabled" not in names:
        op.add_column(
            "staff_users",
            sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "mfa_secret" not in names:
        op.add_column("staff_users", sa.Column("mfa_secret", sa.String(length=512), nullable=True))

    if "mfa_last_timestep" not in names:
        op.add_column("staff_users", sa.Column("mfa_last_timestep", sa.Integer(), nullable=True))


def downgrade():
    if context.is_offline_mode():
        op.drop_column("staff_users", "mfa_last_timestep")
        op.drop_column("staff_users", "mfa_secret")
        op.drop_column("staff_users", "mfa_enabled")
        return

    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table("staff_users"):
        return

    names = _column_names(bind, "staff_users")

    if "mfa_last_timestep" in names:
        op.drop_column("staff_users", "mfa_last_timestep")
    if "mfa_secret" in names:
        op.drop_column("staff_users", "mfa_secret")
    if "mfa_enabled" in names:
        op.drop_column("staff_users", "mfa_enabled")
