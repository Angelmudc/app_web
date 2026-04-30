"""create seguimiento candidatas tables

Revision ID: 20260430_1100
Revises: 20260410_1700
Create Date: 2026-04-30 11:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260430_1100"
down_revision = "20260410_1700"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seguimiento_candidatas_contactos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telefono_norm", sa.String(length=32), nullable=True),
        sa.Column("nombre_reportado", sa.String(length=200), nullable=True),
        sa.Column("canal_preferido", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_seg_contacto_telefono_norm", "seguimiento_candidatas_contactos", ["telefono_norm"], unique=False)

    op.create_table(
        "seguimiento_candidatas_casos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("candidata_id", sa.Integer(), sa.ForeignKey("candidatas.fila", ondelete="SET NULL"), nullable=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contacto_id", sa.Integer(), sa.ForeignKey("seguimiento_candidatas_contactos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("nombre_contacto", sa.String(length=200), nullable=True),
        sa.Column("telefono_norm", sa.String(length=32), nullable=True),
        sa.Column("canal_origen", sa.String(length=20), nullable=False, server_default=sa.text("'otro'")),
        sa.Column("estado", sa.String(length=30), nullable=False, server_default=sa.text("'nuevo'")),
        sa.Column("prioridad", sa.String(length=20), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("owner_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=True),
        sa.Column("proxima_accion_tipo", sa.String(length=40), nullable=True),
        sa.Column("proxima_accion_detalle", sa.String(length=300), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("waiting_since_at", sa.DateTime(), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(), nullable=True),
        sa.Column("last_movement_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_by_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        sa.Column("merge_into_case_id", sa.Integer(), sa.ForeignKey("seguimiento_candidatas_casos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_merged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("public_id", name="uq_seg_caso_public_id"),
        sa.CheckConstraint(
            "estado IN ('nuevo','en_gestion','esperando_candidata','esperando_staff','programado','listo_para_enviar','enviado','cerrado_exitoso','cerrado_no_exitoso','duplicado')",
            name="ck_seg_caso_estado",
        ),
        sa.CheckConstraint("prioridad IN ('baja','normal','alta','urgente')", name="ck_seg_caso_prioridad"),
        sa.CheckConstraint(
            "canal_origen IN ('llamada','whatsapp','chat','presencial','referida','otro')",
            name="ck_seg_caso_canal_origen",
        ),
        sa.CheckConstraint("NOT (candidata_id IS NULL AND contacto_id IS NULL)", name="ck_seg_caso_identity_present"),
    )
    op.create_index("ix_seg_caso_public_id", "seguimiento_candidatas_casos", ["public_id"], unique=True)
    op.create_index("ix_seg_caso_telefono_norm", "seguimiento_candidatas_casos", ["telefono_norm"], unique=False)
    op.create_index("ix_seg_caso_estado_due_at", "seguimiento_candidatas_casos", ["estado", "due_at"], unique=False)
    op.create_index("ix_seg_caso_owner_estado", "seguimiento_candidatas_casos", ["owner_staff_user_id", "estado"], unique=False)
    op.create_index("ix_seg_caso_last_movement_at_desc", "seguimiento_candidatas_casos", [sa.text("last_movement_at DESC")], unique=False)

    op.create_table(
        "seguimiento_candidatas_eventos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("caso_id", sa.Integer(), sa.ForeignKey("seguimiento_candidatas_casos.id"), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("actor_staff_user_id", sa.Integer(), sa.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_seg_evento_caso_created", "seguimiento_candidatas_eventos", ["caso_id", "created_at"], unique=False)


def downgrade():
    op.drop_index("ix_seg_evento_caso_created", table_name="seguimiento_candidatas_eventos")
    op.drop_table("seguimiento_candidatas_eventos")

    op.drop_index("ix_seg_caso_last_movement_at_desc", table_name="seguimiento_candidatas_casos")
    op.drop_index("ix_seg_caso_owner_estado", table_name="seguimiento_candidatas_casos")
    op.drop_index("ix_seg_caso_estado_due_at", table_name="seguimiento_candidatas_casos")
    op.drop_index("ix_seg_caso_telefono_norm", table_name="seguimiento_candidatas_casos")
    op.drop_index("ix_seg_caso_public_id", table_name="seguimiento_candidatas_casos")
    op.drop_table("seguimiento_candidatas_casos")

    op.drop_index("ix_seg_contacto_telefono_norm", table_name="seguimiento_candidatas_contactos")
    op.drop_table("seguimiento_candidatas_contactos")
