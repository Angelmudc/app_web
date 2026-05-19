"""add candidata telefono_e164 with safe backfill

Revision ID: 20260508_1600
Revises: 20260508_1100
Create Date: 2026-05-08 16:00:00
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_1600"
down_revision = "20260508_1100"
branch_labels = None
depends_on = None


_RD_AREA_CODES = {"809", "829", "849"}


def _has_column(bind, table_name: str, column_name: str) -> bool:
    try:
        cols = inspect(bind).get_columns(table_name)
    except Exception:
        return False
    return any((c.get("name") or "") == column_name for c in cols)


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(ix.get("name") or "") for ix in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def _sanitize_phone(raw_phone: str | None) -> str:
    return re.sub(r"\D+", "", str(raw_phone or ""))


def _normalize_phone_to_e164(raw_phone: str | None) -> str | None:
    digits = _sanitize_phone(raw_phone)
    if len(digits) < 10 or len(digits) > 15 or len(set(digits)) == 1:
        return None
    if len(digits) == 10 and digits[:3] in _RD_AREA_CODES:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1") and digits[1:4] in _RD_AREA_CODES:
        return f"+{digits}"
    return None


def upgrade():
    bind = op.get_bind()

    if not _has_column(bind, "candidatas", "telefono_e164"):
        op.add_column("candidatas", sa.Column("telefono_e164", sa.String(length=20), nullable=True))

    idx = _index_names(bind, "candidatas")
    if "ix_candidatas_telefono_e164" not in idx:
        op.create_index("ix_candidatas_telefono_e164", "candidatas", ["telefono_e164"], unique=False)

    rows = bind.execute(sa.text("SELECT fila, numero_telefono FROM candidatas")).fetchall()
    updated = 0
    ignored = 0
    for fila, numero_telefono in rows:
        phone_e164 = _normalize_phone_to_e164(numero_telefono)
        if not phone_e164:
            ignored += 1
            continue
        bind.execute(
            sa.text("UPDATE candidatas SET telefono_e164 = :phone_e164 WHERE fila = :fila"),
            {"fila": int(fila), "phone_e164": phone_e164},
        )
        updated += 1

    print(f"[alembic][20260508_1600] candidatas.telefono_e164 backfill updated={updated} ignored={ignored}")


def downgrade():
    bind = op.get_bind()
    idx = _index_names(bind, "candidatas")
    if "ix_candidatas_telefono_e164" in idx:
        op.drop_index("ix_candidatas_telefono_e164", table_name="candidatas")
    if _has_column(bind, "candidatas", "telefono_e164"):
        op.drop_column("candidatas", "telefono_e164")
