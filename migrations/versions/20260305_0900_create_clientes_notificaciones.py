"""create clientes_notificaciones inbox table

Revision ID: 20260305_0900
Revises: 20260304_1930
Create Date: 2026-03-05 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260305_0900"
down_revision = "20260304_1930"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "clientes_notificaciones" not in existing_tables:
        op.create_table(
            "clientes_notificaciones",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("cliente_id", sa.Integer(), nullable=False),
            sa.Column("solicitud_id", sa.Integer(), nullable=True),
            sa.Column("tipo", sa.String(length=80), nullable=False),
            sa.Column("titulo", sa.String(length=200), nullable=False),
            sa.Column("cuerpo", sa.Text(), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["cliente_id"], ["clientes.id"]),
            sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = {ix["name"] for ix in inspector.get_indexes("clientes_notificaciones")}
    if "ix_clientes_notificaciones_cliente_id" not in indexes:
        op.create_index("ix_clientes_notificaciones_cliente_id", "clientes_notificaciones", ["cliente_id"], unique=False)
    if "ix_clientes_notificaciones_solicitud_id" not in indexes:
        op.create_index("ix_clientes_notificaciones_solicitud_id", "clientes_notificaciones", ["solicitud_id"], unique=False)
    if "ix_clientes_notificaciones_tipo" not in indexes:
        op.create_index("ix_clientes_notificaciones_tipo", "clientes_notificaciones", ["tipo"], unique=False)
    if "ix_clientes_notificaciones_is_read" not in indexes:
        op.create_index("ix_clientes_notificaciones_is_read", "clientes_notificaciones", ["is_read"], unique=False)
    if "ix_clientes_notificaciones_is_deleted" not in indexes:
        op.create_index("ix_clientes_notificaciones_is_deleted", "clientes_notificaciones", ["is_deleted"], unique=False)
    if "ix_clientes_notificaciones_created_at" not in indexes:
        op.create_index("ix_clientes_notificaciones_created_at", "clientes_notificaciones", ["created_at"], unique=False)
    if "ix_clientes_notif_cliente_read_deleted" not in indexes:
        op.create_index(
            "ix_clientes_notif_cliente_read_deleted",
            "clientes_notificaciones",
            ["cliente_id", "is_read", "is_deleted"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "clientes_notificaciones" not in existing_tables:
        return

    indexes = {ix["name"] for ix in inspector.get_indexes("clientes_notificaciones")}
    for ix_name in (
        "ix_clientes_notif_cliente_read_deleted",
        "ix_clientes_notificaciones_created_at",
        "ix_clientes_notificaciones_is_deleted",
        "ix_clientes_notificaciones_is_read",
        "ix_clientes_notificaciones_tipo",
        "ix_clientes_notificaciones_solicitud_id",
        "ix_clientes_notificaciones_cliente_id",
    ):
        if ix_name in indexes:
            op.drop_index(ix_name, table_name="clientes_notificaciones")
    op.drop_table("clientes_notificaciones")
