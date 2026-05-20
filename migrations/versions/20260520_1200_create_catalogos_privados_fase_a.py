"""create catalogos privados fase a

Revision ID: 20260520_1200
Revises: 20260519_1510
Create Date: 2026-05-20 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260520_1200"
down_revision = "20260519_1510"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalogos_privados",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=160), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("solicitud_id", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=12), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes.id"]),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"]),
        sa.UniqueConstraint("token_hash", name="uq_catalogos_privados_token_hash"),
    )
    op.create_index("ix_catalogos_privados_cliente_id", "catalogos_privados", ["cliente_id"])
    op.create_index("ix_catalogos_privados_solicitud_id", "catalogos_privados", ["solicitud_id"])
    op.create_index("ix_catalogos_privados_token_hash", "catalogos_privados", ["token_hash"])
    op.create_index("ix_catalogos_privados_is_active", "catalogos_privados", ["is_active"])
    op.create_index("ix_catalogos_privados_expires_at", "catalogos_privados", ["expires_at"])
    op.create_index("ix_catalogos_privados_created_at", "catalogos_privados", ["created_at"])
    op.create_index("ix_catalogos_privados_updated_at", "catalogos_privados", ["updated_at"])
    op.create_index("ix_catalogos_privados_last_seen_at", "catalogos_privados", ["last_seen_at"])

    op.create_table(
        "catalogos_privados_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("catalogo_id", sa.Integer(), nullable=False),
        sa.Column("candidata_id", sa.Integer(), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["catalogo_id"], ["catalogos_privados.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidata_id"], ["candidatas.fila"]),
        sa.UniqueConstraint("catalogo_id", "candidata_id", name="uq_catalogo_privado_item_catalogo_candidata"),
    )
    op.create_index("ix_catalogos_privados_items_catalogo_id", "catalogos_privados_items", ["catalogo_id"])
    op.create_index("ix_catalogos_privados_items_candidata_id", "catalogos_privados_items", ["candidata_id"])
    op.create_index("ix_catalogos_privados_items_orden", "catalogos_privados_items", ["orden"])
    op.create_index("ix_catalogos_privados_items_is_visible", "catalogos_privados_items", ["is_visible"])


def downgrade():
    op.drop_index("ix_catalogos_privados_items_is_visible", table_name="catalogos_privados_items")
    op.drop_index("ix_catalogos_privados_items_orden", table_name="catalogos_privados_items")
    op.drop_index("ix_catalogos_privados_items_candidata_id", table_name="catalogos_privados_items")
    op.drop_index("ix_catalogos_privados_items_catalogo_id", table_name="catalogos_privados_items")
    op.drop_table("catalogos_privados_items")

    op.drop_index("ix_catalogos_privados_last_seen_at", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_updated_at", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_created_at", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_expires_at", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_is_active", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_token_hash", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_solicitud_id", table_name="catalogos_privados")
    op.drop_index("ix_catalogos_privados_cliente_id", table_name="catalogos_privados")
    op.drop_table("catalogos_privados")
