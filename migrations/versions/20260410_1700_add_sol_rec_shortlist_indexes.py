"""add shortlist query indexes for recommendation items

Revision ID: 20260410_1700
Revises: 20260410_1200
Create Date: 2026-04-10 17:00:00
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260410_1700"
down_revision = "20260410_1200"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _index_names(bind, table_name: str) -> set[str]:
    try:
        return {str(ix.get("name") or "") for ix in inspect(bind).get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "solicitud_recommendation_items"):
        return

    names = _index_names(bind, "solicitud_recommendation_items")
    if "ix_sol_rec_items_run_sol_eligible_rank_id" not in names:
        op.create_index(
            "ix_sol_rec_items_run_sol_eligible_rank_id",
            "solicitud_recommendation_items",
            ["run_id", "solicitud_id", "is_eligible", "rank_position", "id"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "solicitud_recommendation_items"):
        return

    names = _index_names(bind, "solicitud_recommendation_items")
    if "ix_sol_rec_items_run_sol_eligible_rank_id" in names:
        op.drop_index("ix_sol_rec_items_run_sol_eligible_rank_id", table_name="solicitud_recommendation_items")
