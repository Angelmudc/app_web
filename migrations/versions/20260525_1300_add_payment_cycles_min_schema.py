"""add minimal payment cycle fields to solicitud and pagos_solicitud

Revision ID: 20260525_1300
Revises: 20260525_1200
Create Date: 2026-05-25 13:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525_1300"
down_revision = "20260525_1200"
branch_labels = None
depends_on = None


def _col_exists(inspector, table: str, col: str) -> bool:
    return col in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("pagos_solicitud") and not _col_exists(inspector, "pagos_solicitud", "ciclo_numero"):
        op.add_column("pagos_solicitud", sa.Column("ciclo_numero", sa.Integer(), nullable=True, server_default=sa.text("1")))
        op.execute("UPDATE pagos_solicitud SET ciclo_numero = 1 WHERE ciclo_numero IS NULL")
        op.alter_column("pagos_solicitud", "ciclo_numero", nullable=False, server_default=sa.text("1"))
        op.execute("CREATE INDEX IF NOT EXISTS ix_pagos_solicitud_ciclo_numero ON pagos_solicitud (ciclo_numero)")

    if inspector.has_table("solicitudes"):
        to_add = [
            ("payment_cycle_current", sa.Column("payment_cycle_current", sa.Integer(), nullable=False, server_default=sa.text("1"))),
            ("payment_cycle_plan", sa.Column("payment_cycle_plan", sa.String(length=50), nullable=True)),
            ("payment_cycle_precio_total", sa.Column("payment_cycle_precio_total", sa.Numeric(12, 2), nullable=True)),
            ("payment_cycle_abono_requerido", sa.Column("payment_cycle_abono_requerido", sa.Numeric(12, 2), nullable=True)),
            ("payment_cycle_estado", sa.Column("payment_cycle_estado", sa.String(length=20), nullable=False, server_default=sa.text("'pendiente'"))),
            ("payment_cycle_opened_at", sa.Column("payment_cycle_opened_at", sa.DateTime(), nullable=True)),
            ("payment_cycle_closed_at", sa.Column("payment_cycle_closed_at", sa.DateTime(), nullable=True)),
            ("payment_cycle_motivo_apertura", sa.Column("payment_cycle_motivo_apertura", sa.String(length=200), nullable=True)),
        ]
        for col_name, col in to_add:
            if not _col_exists(inspector, "solicitudes", col_name):
                op.add_column("solicitudes", col)

        op.execute(
            """
            UPDATE solicitudes
            SET payment_cycle_current = COALESCE(payment_cycle_current, 1),
                payment_cycle_plan = COALESCE(NULLIF(lower(tipo_plan), ''), payment_cycle_plan, 'basico'),
                payment_cycle_estado = COALESCE(NULLIF(payment_cycle_estado, ''), 'pendiente')
            """
        )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_pagos_solicitud_ciclo_numero")
    with op.batch_alter_table("pagos_solicitud") as bop:
        if hasattr(bop, "drop_column"):
            try:
                bop.drop_column("ciclo_numero")
            except Exception:
                pass

    for col in (
        "payment_cycle_motivo_apertura",
        "payment_cycle_closed_at",
        "payment_cycle_opened_at",
        "payment_cycle_estado",
        "payment_cycle_abono_requerido",
        "payment_cycle_precio_total",
        "payment_cycle_plan",
        "payment_cycle_current",
    ):
        try:
            op.drop_column("solicitudes", col)
        except Exception:
            pass
