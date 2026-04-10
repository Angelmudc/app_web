"""create solicitud recommendation snapshot tables

Revision ID: 20260410_1200
Revises: 20260407_1300
Create Date: 2026-04-10 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260410_1200"
down_revision = "20260407_1300"
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

    if not _has_table(bind, "solicitud_recommendation_runs"):
        op.create_table(
            "solicitud_recommendation_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id"), nullable=False),
            sa.Column("trigger_source", sa.String(length=40), nullable=False, server_default=sa.text("'manual'")),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("fingerprint_hash", sa.String(length=64), nullable=False),
            sa.Column("model_version", sa.String(length=40), nullable=False, server_default=sa.text("'rec-v1'")),
            sa.Column("policy_version", sa.String(length=40), nullable=False, server_default=sa.text("'policy-v1'")),
            sa.Column("requested_by", sa.String(length=120), nullable=True),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.String(length=500), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("pool_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("eligible_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("hard_fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("soft_fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("items_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.CheckConstraint(
                "status IN ('pending','running','completed','error','stale')",
                name="ck_sol_rec_run_status",
            ),
        )

    run_indexes = _index_names(bind, "solicitud_recommendation_runs")
    for name, cols in (
        ("ix_solicitud_recommendation_runs_solicitud_id", ["solicitud_id"]),
        ("ix_solicitud_recommendation_runs_status", ["status"]),
        ("ix_solicitud_recommendation_runs_requested_at", ["requested_at"]),
        ("ix_solicitud_recommendation_runs_is_active", ["is_active"]),
        ("ix_sol_rec_runs_sol_status_req", ["solicitud_id", "status", "requested_at"]),
        ("ix_sol_rec_runs_sol_active_req", ["solicitud_id", "is_active", "requested_at"]),
        ("ix_sol_rec_runs_fingerprint_hash", ["fingerprint_hash"]),
    ):
        if name not in run_indexes:
            op.create_index(name, "solicitud_recommendation_runs", cols, unique=False)

    if not _has_table(bind, "solicitud_recommendation_items"):
        op.create_table(
            "solicitud_recommendation_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("solicitud_recommendation_runs.id"), nullable=False),
            sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id"), nullable=False),
            sa.Column("candidata_id", sa.Integer(), sa.ForeignKey("candidatas.fila"), nullable=False),
            sa.Column("rank_position", sa.Integer(), nullable=True),
            sa.Column("is_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("hard_fail", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("hard_fail_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("hard_fail_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("soft_fail_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("soft_fail_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("score_final", sa.Integer(), nullable=True),
            sa.Column("score_operational", sa.Integer(), nullable=True),
            sa.Column("confidence_band", sa.String(length=20), nullable=True),
            sa.Column("policy_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("breakdown_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "candidata_id", name="uq_sol_rec_item_run_candidata"),
            sa.CheckConstraint(
                "confidence_band IN ('alta','media','baja') OR confidence_band IS NULL",
                name="ck_sol_rec_item_confidence_band",
            ),
        )

    item_indexes = _index_names(bind, "solicitud_recommendation_items")
    for name, cols in (
        ("ix_solicitud_recommendation_items_run_id", ["run_id"]),
        ("ix_solicitud_recommendation_items_solicitud_id", ["solicitud_id"]),
        ("ix_solicitud_recommendation_items_candidata_id", ["candidata_id"]),
        ("ix_solicitud_recommendation_items_rank_position", ["rank_position"]),
        ("ix_solicitud_recommendation_items_is_eligible", ["is_eligible"]),
        ("ix_solicitud_recommendation_items_hard_fail", ["hard_fail"]),
        ("ix_solicitud_recommendation_items_created_at", ["created_at"]),
        ("ix_sol_rec_items_sol_run_rank", ["solicitud_id", "run_id", "rank_position"]),
        ("ix_sol_rec_items_sol_eligible_score", ["solicitud_id", "is_eligible", "score_final"]),
    ):
        if name not in item_indexes:
            op.create_index(name, "solicitud_recommendation_items", cols, unique=False)

    if not _has_table(bind, "solicitud_recommendation_selections"):
        op.create_table(
            "solicitud_recommendation_selections",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id"), nullable=False),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("solicitud_recommendation_runs.id"), nullable=False),
            sa.Column(
                "recommendation_item_id",
                sa.Integer(),
                sa.ForeignKey("solicitud_recommendation_items.id"),
                nullable=True,
            ),
            sa.Column("candidata_id", sa.Integer(), sa.ForeignKey("candidatas.fila"), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'pending_validation'")),
            sa.Column("validation_code", sa.String(length=80), nullable=True),
            sa.Column("validation_message", sa.String(length=300), nullable=True),
            sa.Column("validated_at", sa.DateTime(), nullable=True),
            sa.Column("selected_by", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.UniqueConstraint("solicitud_id", "run_id", "candidata_id", name="uq_sol_rec_sel_sol_run_cand"),
            sa.CheckConstraint(
                "status IN ('pending_validation','valid','invalidated','confirmed')",
                name="ck_sol_rec_sel_status",
            ),
        )

    sel_indexes = _index_names(bind, "solicitud_recommendation_selections")
    for name, cols in (
        ("ix_solicitud_recommendation_selections_solicitud_id", ["solicitud_id"]),
        ("ix_solicitud_recommendation_selections_run_id", ["run_id"]),
        ("ix_solicitud_recommendation_selections_recommendation_item_id", ["recommendation_item_id"]),
        ("ix_solicitud_recommendation_selections_candidata_id", ["candidata_id"]),
        ("ix_solicitud_recommendation_selections_status", ["status"]),
        ("ix_solicitud_recommendation_selections_created_at", ["created_at"]),
        ("ix_sol_rec_sel_sol_created", ["solicitud_id", "created_at"]),
    ):
        if name not in sel_indexes:
            op.create_index(name, "solicitud_recommendation_selections", cols, unique=False)


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "solicitud_recommendation_selections"):
        for name in (
            "ix_sol_rec_sel_sol_created",
            "ix_solicitud_recommendation_selections_created_at",
            "ix_solicitud_recommendation_selections_status",
            "ix_solicitud_recommendation_selections_candidata_id",
            "ix_solicitud_recommendation_selections_recommendation_item_id",
            "ix_solicitud_recommendation_selections_run_id",
            "ix_solicitud_recommendation_selections_solicitud_id",
        ):
            if name in _index_names(bind, "solicitud_recommendation_selections"):
                op.drop_index(name, table_name="solicitud_recommendation_selections")
        op.drop_table("solicitud_recommendation_selections")

    if _has_table(bind, "solicitud_recommendation_items"):
        for name in (
            "ix_sol_rec_items_sol_eligible_score",
            "ix_sol_rec_items_sol_run_rank",
            "ix_solicitud_recommendation_items_created_at",
            "ix_solicitud_recommendation_items_hard_fail",
            "ix_solicitud_recommendation_items_is_eligible",
            "ix_solicitud_recommendation_items_rank_position",
            "ix_solicitud_recommendation_items_candidata_id",
            "ix_solicitud_recommendation_items_solicitud_id",
            "ix_solicitud_recommendation_items_run_id",
        ):
            if name in _index_names(bind, "solicitud_recommendation_items"):
                op.drop_index(name, table_name="solicitud_recommendation_items")
        op.drop_table("solicitud_recommendation_items")

    if _has_table(bind, "solicitud_recommendation_runs"):
        for name in (
            "ix_sol_rec_runs_fingerprint_hash",
            "ix_sol_rec_runs_sol_active_req",
            "ix_sol_rec_runs_sol_status_req",
            "ix_solicitud_recommendation_runs_is_active",
            "ix_solicitud_recommendation_runs_requested_at",
            "ix_solicitud_recommendation_runs_status",
            "ix_solicitud_recommendation_runs_solicitud_id",
        ):
            if name in _index_names(bind, "solicitud_recommendation_runs"):
                op.drop_index(name, table_name="solicitud_recommendation_runs")
        op.drop_table("solicitud_recommendation_runs")
