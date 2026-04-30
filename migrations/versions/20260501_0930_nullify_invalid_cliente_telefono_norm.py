"""nullify invalid placeholder phone norms for clientes

Revision ID: 20260501_0930
Revises: 20260501_0900
Create Date: 2026-05-01 09:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0930"
down_revision = "20260501_0900"
branch_labels = None
depends_on = None


def _is_invalid_phone_placeholder(raw: str) -> bool:
    if not raw:
        return True
    if len(raw) < 10:
        return True
    if len(raw) == 10 and raw in {"0000000000", "1111111111", "1234567890"}:
        return True
    if len(raw) == 10 and len(set(raw)) == 1:
        return True
    return False


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, telefono_norm FROM clientes")).fetchall()
    for row in rows:
        cid = int(row[0])
        tel_norm = str(row[1] or "").strip()
        if _is_invalid_phone_placeholder(tel_norm):
            conn.execute(
                sa.text("UPDATE clientes SET telefono_norm = NULL WHERE id = :id"),
                {"id": cid},
            )


def downgrade():
    # No-op reversible: no restauramos placeholders por seguridad de datos.
    pass
