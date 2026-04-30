"""force null invalid cliente telefono_norm placeholders

Revision ID: 20260501_0940
Revises: 20260501_0930
Create Date: 2026-05-01 09:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import re


revision = "20260501_0940"
down_revision = "20260501_0930"
branch_labels = None
depends_on = None


def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _is_invalid_placeholder(raw: str) -> bool:
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
    rows = conn.execute(sa.text("SELECT id, telefono, telefono_norm FROM clientes")).fetchall()
    for row in rows:
        cid = int(row[0])
        tel_raw = _digits(str(row[1] or ""))
        tel_norm = _digits(str(row[2] or ""))
        if _is_invalid_placeholder(tel_norm) or _is_invalid_placeholder(tel_raw):
            conn.execute(
                sa.text("UPDATE clientes SET telefono_norm = NULL WHERE id = :id"),
                {"id": cid},
            )


def downgrade():
    # No-op por seguridad (no reintroducir valores inválidos).
    pass
