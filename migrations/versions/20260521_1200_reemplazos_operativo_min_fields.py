"""reemplazos operativo minimal tracking fields

Revision ID: 20260521_1200
Revises: 20260520_1830
Create Date: 2026-05-21 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260521_1200"
down_revision = "20260520_1830"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("reemplazos"):
        return

    cols = {c["name"] for c in inspector.get_columns("reemplazos")}

    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        if "motivo_reemplazo_code" not in cols:
            batch_op.add_column(sa.Column("motivo_reemplazo_code", sa.String(length=50), nullable=True))
        if "prioridad" not in cols:
            batch_op.add_column(sa.Column("prioridad", sa.String(length=20), nullable=True))
        if "resultado_final" not in cols:
            batch_op.add_column(sa.Column("resultado_final", sa.String(length=30), nullable=True))
        if "responsable_id" not in cols:
            batch_op.add_column(sa.Column("responsable_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_reemplazos_responsable_id_staff_users",
                "staff_users",
                ["responsable_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "fecha_reporte" not in cols:
            batch_op.add_column(sa.Column("fecha_reporte", sa.DateTime(), nullable=True))
        if "fecha_resolucion" not in cols:
            batch_op.add_column(sa.Column("fecha_resolucion", sa.DateTime(), nullable=True))

    op.execute("CREATE INDEX IF NOT EXISTS ix_reemplazos_motivo_reemplazo_code ON reemplazos (motivo_reemplazo_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reemplazos_prioridad ON reemplazos (prioridad)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reemplazos_resultado_final ON reemplazos (resultado_final)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reemplazos_responsable_id ON reemplazos (responsable_id)")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("reemplazos"):
        return

    cols = {c["name"] for c in inspector.get_columns("reemplazos")}
    fks = {fk.get("name") for fk in (inspector.get_foreign_keys("reemplazos") or [])}

    op.execute("DROP INDEX IF EXISTS ix_reemplazos_responsable_id")
    op.execute("DROP INDEX IF EXISTS ix_reemplazos_resultado_final")
    op.execute("DROP INDEX IF EXISTS ix_reemplazos_prioridad")
    op.execute("DROP INDEX IF EXISTS ix_reemplazos_motivo_reemplazo_code")

    with op.batch_alter_table("reemplazos", schema=None) as batch_op:
        if "fk_reemplazos_responsable_id_staff_users" in fks:
            batch_op.drop_constraint("fk_reemplazos_responsable_id_staff_users", type_="foreignkey")
        if "fecha_resolucion" in cols:
            batch_op.drop_column("fecha_resolucion")
        if "fecha_reporte" in cols:
            batch_op.drop_column("fecha_reporte")
        if "responsable_id" in cols:
            batch_op.drop_column("responsable_id")
        if "resultado_final" in cols:
            batch_op.drop_column("resultado_final")
        if "prioridad" in cols:
            batch_op.drop_column("prioridad")
        if "motivo_reemplazo_code" in cols:
            batch_op.drop_column("motivo_reemplazo_code")
