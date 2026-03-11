"""Create share aliases for public solicitud links.

Revision ID: 20260311_1500
Revises: 20260311_1100
Create Date: 2026-03-11 15:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260311_1500"
down_revision = "20260311_1100"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "public_solicitud_share_aliases"

    if not inspector.has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=24), nullable=False),
            sa.Column("link_type", sa.String(length=24), nullable=False),
            sa.Column("token", sa.Text(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_by", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        )

    local_inspector = sa.inspect(bind)
    cols = {c["name"] for c in local_inspector.get_columns(table_name)}
    idx_by_name = {idx["name"]: idx for idx in local_inspector.get_indexes(table_name)}
    if "code" in cols and f"ix_{table_name}_code" not in idx_by_name:
        op.create_index(f"ix_{table_name}_code", table_name, ["code"], unique=True)
    if "link_type" in cols and f"ix_{table_name}_link_type" not in idx_by_name:
        op.create_index(f"ix_{table_name}_link_type", table_name, ["link_type"], unique=False)
    if "token_hash" in cols and f"ix_{table_name}_token_hash" not in idx_by_name:
        op.create_index(f"ix_{table_name}_token_hash", table_name, ["token_hash"], unique=False)
    if "created_at" in cols and f"ix_{table_name}_created_at" not in idx_by_name:
        op.create_index(f"ix_{table_name}_created_at", table_name, ["created_at"], unique=False)


def downgrade():
    table_name = "public_solicitud_share_aliases"
    op.drop_index(f"ix_{table_name}_created_at", table_name=table_name)
    op.drop_index(f"ix_{table_name}_token_hash", table_name=table_name)
    op.drop_index(f"ix_{table_name}_link_type", table_name=table_name)
    op.drop_index(f"ix_{table_name}_code", table_name=table_name)
    op.drop_table(table_name)
