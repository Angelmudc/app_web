"""create solicitudes_candidatas table for internal matching

Revision ID: 20260304_1930
Revises: 20260304_1700
Create Date: 2026-03-04 19:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260304_1930"
down_revision = "20260304_1700"
branch_labels = None
depends_on = None


status_enum = postgresql.ENUM(
    "sugerida",
    "enviada",
    "vista",
    "descartada",
    "seleccionada",
    name="solicitud_candidata_status_enum",
    create_type=False,
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'solicitud_candidata_status_enum'
              ) THEN
                CREATE TYPE solicitud_candidata_status_enum AS ENUM (
                  'sugerida',
                  'enviada',
                  'vista',
                  'descartada',
                  'seleccionada'
                );
              END IF;
            END$$;
            """
        )

    op.create_table(
        "solicitudes_candidatas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solicitud_id", sa.Integer(), nullable=False),
        sa.Column("candidata_id", sa.Integer(), nullable=False),
        sa.Column("score_snapshot", sa.Integer(), nullable=True),
        sa.Column("breakdown_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="sugerida"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["candidata_id"], ["candidatas.fila"]),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("solicitud_id", "candidata_id", name="uq_solicitudes_candidatas_sol_cand"),
    )
    op.create_index(op.f("ix_solicitudes_candidatas_solicitud_id"), "solicitudes_candidatas", ["solicitud_id"], unique=False)
    op.create_index(op.f("ix_solicitudes_candidatas_candidata_id"), "solicitudes_candidatas", ["candidata_id"], unique=False)
    op.create_index(op.f("ix_solicitudes_candidatas_created_at"), "solicitudes_candidatas", ["created_at"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_solicitudes_candidatas_created_at"), table_name="solicitudes_candidatas")
    op.drop_index(op.f("ix_solicitudes_candidatas_candidata_id"), table_name="solicitudes_candidatas")
    op.drop_index(op.f("ix_solicitudes_candidatas_solicitud_id"), table_name="solicitudes_candidatas")
    op.drop_table("solicitudes_candidatas")
    # No se elimina el TYPE para evitar romper otras dependencias en BD.
