"""add cliente normalized contact columns and backfill

Revision ID: 20260501_0900
Revises: 20260430_1100
Create Date: 2026-05-01 09:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0900"
down_revision = "20260430_1100"
branch_labels = None
depends_on = None


def _norm_phone_rd(value: str | None) -> str | None:
    import re

    raw = re.sub(r"\D+", "", value or "")
    if not raw:
        return None
    if len(raw) == 11 and raw.startswith("1") and raw[1:4] in {"809", "829", "849"}:
        return raw[1:]
    if len(raw) == 10 and raw[:3] in {"809", "829", "849"}:
        return raw
    return raw[:15]


def upgrade():
    op.add_column("clientes", sa.Column("email_norm", sa.String(length=100), nullable=True))
    op.add_column("clientes", sa.Column("telefono_norm", sa.String(length=32), nullable=True))
    op.create_index("ix_clientes_email_norm", "clientes", ["email_norm"], unique=False)
    op.create_index("ix_clientes_telefono_norm", "clientes", ["telefono_norm"], unique=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, email, telefono FROM clientes")).fetchall()
    for row in rows:
        cid = int(row[0])
        email_raw = (row[1] or "")
        tel_raw = (row[2] or "")
        email_norm = email_raw.strip().lower() or None
        tel_norm = _norm_phone_rd(tel_raw)
        conn.execute(
            sa.text(
                "UPDATE clientes SET email_norm = :email_norm, telefono_norm = :telefono_norm WHERE id = :id"
            ),
            {"id": cid, "email_norm": email_norm, "telefono_norm": tel_norm},
        )


def downgrade():
    op.drop_index("ix_clientes_telefono_norm", table_name="clientes")
    op.drop_index("ix_clientes_email_norm", table_name="clientes")
    op.drop_column("clientes", "telefono_norm")
    op.drop_column("clientes", "email_norm")
