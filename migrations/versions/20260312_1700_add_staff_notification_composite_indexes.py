"""Add composite indexes for staff notifications scalability

Revision ID: 20260312_1700
Revises: 20260312_1300
Create Date: 2026-03-12 17:00:00
"""

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260312_1700"
down_revision = "20260312_1300"
branch_labels = None
depends_on = None


def _index_names(bind, table_name: str) -> set:
    try:
        return {idx.get("name") for idx in inspect(bind).get_indexes(table_name) if idx.get("name")}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if insp.has_table("staff_notificaciones"):
        names = _index_names(bind, "staff_notificaciones")
        if "ix_staff_notificaciones_created_at_id" not in names:
            op.create_index(
                "ix_staff_notificaciones_created_at_id",
                "staff_notificaciones",
                ["created_at", "id"],
                unique=False,
            )

    if insp.has_table("staff_notificaciones_lecturas"):
        names = _index_names(bind, "staff_notificaciones_lecturas")
        if "ix_staff_notificaciones_lecturas_reader_key_notif_id" not in names:
            op.create_index(
                "ix_staff_notificaciones_lecturas_reader_key_notif_id",
                "staff_notificaciones_lecturas",
                ["reader_key", "notificacion_id"],
                unique=False,
            )


def downgrade():
    bind = op.get_bind()

    names_notif = _index_names(bind, "staff_notificaciones")
    if "ix_staff_notificaciones_created_at_id" in names_notif:
        op.drop_index("ix_staff_notificaciones_created_at_id", table_name="staff_notificaciones")

    names_reads = _index_names(bind, "staff_notificaciones_lecturas")
    if "ix_staff_notificaciones_lecturas_reader_key_notif_id" in names_reads:
        op.drop_index(
            "ix_staff_notificaciones_lecturas_reader_key_notif_id",
            table_name="staff_notificaciones_lecturas",
        )
