"""create tienda_intereses and tienda_intereses_items

Revision ID: 20260520_1830
Revises: 20260520_1700
Create Date: 2026-05-20 18:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260520_1830"
down_revision = "20260520_1700"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tienda_intereses"):
        op.create_table(
            "tienda_intereses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("catalogo_id", sa.Integer(), nullable=False),
            sa.Column("cliente_id", sa.Integer(), nullable=True),
            sa.Column("solicitud_id", sa.Integer(), nullable=True),
            sa.Column("nombre_contacto", sa.String(length=200), nullable=False),
            sa.Column("telefono_contacto", sa.String(length=50), nullable=False),
            sa.Column("comentario", sa.Text(), nullable=True),
            sa.Column("estado", sa.String(length=20), nullable=False, server_default=sa.text("'nuevo'")),
            sa.Column("token_hint_usado", sa.String(length=12), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["catalogo_id"], ["catalogos_privados.id"]),
            sa.ForeignKeyConstraint(["cliente_id"], ["clientes.id"]),
            sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("tienda_intereses_items"):
        op.create_table(
            "tienda_intereses_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("interes_id", sa.Integer(), nullable=False),
            sa.Column("candidata_id", sa.Integer(), nullable=False),
            sa.Column("orden", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["interes_id"], ["tienda_intereses.id"]),
            sa.ForeignKeyConstraint(["candidata_id"], ["candidatas.fila"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("interes_id", "candidata_id", name="uq_tienda_interes_item_interes_candidata"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_catalogo_id ON tienda_intereses (catalogo_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_cliente_id ON tienda_intereses (cliente_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_solicitud_id ON tienda_intereses (solicitud_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_estado ON tienda_intereses (estado)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_created_at ON tienda_intereses (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_updated_at ON tienda_intereses (updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_items_interes_id ON tienda_intereses_items (interes_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tienda_intereses_items_candidata_id ON tienda_intereses_items (candidata_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_items_candidata_id")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_items_interes_id")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_created_at")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_estado")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_solicitud_id")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_cliente_id")
    op.execute("DROP INDEX IF EXISTS ix_tienda_intereses_catalogo_id")
    op.drop_table("tienda_intereses_items")
    op.drop_table("tienda_intereses")
