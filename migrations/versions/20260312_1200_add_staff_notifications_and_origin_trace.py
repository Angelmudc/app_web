"""Add origin trace fields and staff notifications

Revision ID: 20260312_1200
Revises: 20260311_1500
Create Date: 2026-03-12 12:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260312_1200"
down_revision = "20260311_1500"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("candidatas", sa.Column("origen_registro", sa.String(length=32), nullable=True))
    op.add_column("candidatas", sa.Column("creado_por_staff", sa.String(length=100), nullable=True))
    op.add_column("candidatas", sa.Column("creado_desde_ruta", sa.String(length=120), nullable=True))
    op.create_index("ix_candidatas_origen_registro", "candidatas", ["origen_registro"], unique=False)

    op.add_column("reclutas_perfiles", sa.Column("origen_registro", sa.String(length=32), nullable=True))
    op.add_column("reclutas_perfiles", sa.Column("creado_desde_ruta", sa.String(length=120), nullable=True))
    op.create_index("ix_reclutas_perfiles_origen_registro", "reclutas_perfiles", ["origen_registro"], unique=False)

    op.create_table(
        "staff_notificaciones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("titulo", sa.String(length=180), nullable=False),
        sa.Column("mensaje", sa.String(length=300), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tipo", "entity_type", "entity_id", name="uq_staff_notif_tipo_entity"),
    )
    op.create_index("ix_staff_notificaciones_tipo", "staff_notificaciones", ["tipo"], unique=False)
    op.create_index("ix_staff_notificaciones_entity_type", "staff_notificaciones", ["entity_type"], unique=False)
    op.create_index("ix_staff_notificaciones_entity_id", "staff_notificaciones", ["entity_id"], unique=False)
    op.create_index("ix_staff_notificaciones_created_at", "staff_notificaciones", ["created_at"], unique=False)

    op.create_table(
        "staff_notificaciones_lecturas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notificacion_id", sa.Integer(), nullable=False),
        sa.Column("reader_key", sa.String(length=120), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["notificacion_id"], ["staff_notificaciones.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notificacion_id", "reader_key", name="uq_staff_notif_read_by_reader"),
    )
    op.create_index("ix_staff_notificaciones_lecturas_notificacion_id", "staff_notificaciones_lecturas", ["notificacion_id"], unique=False)
    op.create_index("ix_staff_notificaciones_lecturas_reader_key", "staff_notificaciones_lecturas", ["reader_key"], unique=False)
    op.create_index("ix_staff_notificaciones_lecturas_read_at", "staff_notificaciones_lecturas", ["read_at"], unique=False)


def downgrade():
    op.drop_index("ix_staff_notificaciones_lecturas_read_at", table_name="staff_notificaciones_lecturas")
    op.drop_index("ix_staff_notificaciones_lecturas_reader_key", table_name="staff_notificaciones_lecturas")
    op.drop_index("ix_staff_notificaciones_lecturas_notificacion_id", table_name="staff_notificaciones_lecturas")
    op.drop_table("staff_notificaciones_lecturas")

    op.drop_index("ix_staff_notificaciones_created_at", table_name="staff_notificaciones")
    op.drop_index("ix_staff_notificaciones_entity_id", table_name="staff_notificaciones")
    op.drop_index("ix_staff_notificaciones_entity_type", table_name="staff_notificaciones")
    op.drop_index("ix_staff_notificaciones_tipo", table_name="staff_notificaciones")
    op.drop_table("staff_notificaciones")

    op.drop_index("ix_reclutas_perfiles_origen_registro", table_name="reclutas_perfiles")
    op.drop_column("reclutas_perfiles", "creado_desde_ruta")
    op.drop_column("reclutas_perfiles", "origen_registro")

    op.drop_index("ix_candidatas_origen_registro", table_name="candidatas")
    op.drop_column("candidatas", "creado_desde_ruta")
    op.drop_column("candidatas", "creado_por_staff")
    op.drop_column("candidatas", "origen_registro")
