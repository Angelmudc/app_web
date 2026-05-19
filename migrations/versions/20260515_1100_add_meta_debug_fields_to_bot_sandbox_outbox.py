"""add meta debug fields to bot sandbox outbox

Revision ID: 20260515_1100
Revises: 20260512_1000
Create Date: 2026-05-15 11:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260515_1100"
down_revision = "20260512_1000"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    try:
        return {str(c.get("name") or "") for c in inspect(bind).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    cols = _columns(bind, "bot_sandbox_outbox")
    if "outbound_http_status" not in cols:
        op.add_column("bot_sandbox_outbox", sa.Column("outbound_http_status", sa.Integer(), nullable=True))
    if "outbound_meta_error_code" not in cols:
        op.add_column("bot_sandbox_outbox", sa.Column("outbound_meta_error_code", sa.String(length=80), nullable=True))
    if "outbound_meta_error_message" not in cols:
        op.add_column("bot_sandbox_outbox", sa.Column("outbound_meta_error_message", sa.String(length=255), nullable=True))
    if "outbound_response_raw" not in cols:
        op.add_column("bot_sandbox_outbox", sa.Column("outbound_response_raw", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = _columns(bind, "bot_sandbox_outbox")
    for col in ("outbound_response_raw", "outbound_meta_error_message", "outbound_meta_error_code", "outbound_http_status"):
        if col in cols:
            op.drop_column("bot_sandbox_outbox", col)
