"""add estado_actual_desde to solicitudes with conservative backfill

Revision ID: 20260407_1200
Revises: 20260330_0900
Create Date: 2026-04-07 12:00:00
"""

from __future__ import annotations

from collections import defaultdict

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260407_1200"
down_revision = "20260330_0900"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _has_column(bind, table_name: str, column_name: str) -> bool:
    try:
        return any(str(c.get("name") or "") == column_name for c in inspect(bind).get_columns(table_name))
    except Exception:
        return False


def _pick_latest(values: list[sa.Row], key_name: str):
    if not values:
        return None
    best = None
    for row in values:
        val = getattr(row, key_name, None)
        if val is None:
            continue
        if best is None or val > best:
            best = val
    return best


def upgrade():
    bind = op.get_bind()

    if not _has_table(bind, "solicitudes"):
        return

    if not _has_column(bind, "solicitudes", "estado_actual_desde"):
        op.add_column("solicitudes", sa.Column("estado_actual_desde", sa.DateTime(), nullable=True))

    meta = sa.MetaData()
    solicitudes = sa.Table("solicitudes", meta, autoload_with=bind)

    rows = bind.execute(
        sa.select(
            solicitudes.c.id,
            solicitudes.c.estado,
            solicitudes.c.estado_actual_desde,
            solicitudes.c.fecha_inicio_seguimiento,
            solicitudes.c.fecha_ultima_modificacion,
            solicitudes.c.fecha_solicitud,
        )
    ).fetchall()

    if not rows:
        return

    ids_reemplazo = [int(r.id) for r in rows if str(r.estado or "").strip().lower() == "reemplazo"]
    ids_activa = [int(r.id) for r in rows if str(r.estado or "").strip().lower() == "activa"]

    reemplazo_inicio_by_solicitud: dict[int, object] = {}
    if ids_reemplazo and _has_table(bind, "reemplazos"):
        reemplazos = sa.Table("reemplazos", meta, autoload_with=bind)
        repl_rows = bind.execute(
            sa.select(
                reemplazos.c.solicitud_id,
                reemplazos.c.fecha_inicio_reemplazo,
                reemplazos.c.fecha_fin_reemplazo,
            )
            .where(reemplazos.c.solicitud_id.in_(ids_reemplazo))
            .where(reemplazos.c.fecha_inicio_reemplazo.is_not(None))
        ).fetchall()

        active_map: dict[int, list] = defaultdict(list)
        any_map: dict[int, list] = defaultdict(list)
        for row in repl_rows:
            sid = int(getattr(row, "solicitud_id", 0) or 0)
            if sid <= 0:
                continue
            any_map[sid].append(row)
            if getattr(row, "fecha_fin_reemplazo", None) is None:
                active_map[sid].append(row)

        for sid in ids_reemplazo:
            active_latest = _pick_latest(active_map.get(sid, []), "fecha_inicio_reemplazo")
            if active_latest is not None:
                reemplazo_inicio_by_solicitud[sid] = active_latest
                continue
            any_latest = _pick_latest(any_map.get(sid, []), "fecha_inicio_reemplazo")
            if any_latest is not None:
                reemplazo_inicio_by_solicitud[sid] = any_latest

    audit_activa_by_solicitud: dict[int, object] = {}
    if ids_activa and _has_table(bind, "staff_audit_logs"):
        audit_logs = sa.Table("staff_audit_logs", meta, autoload_with=bind)
        audit_rows = bind.execute(
            sa.select(
                audit_logs.c.entity_id,
                audit_logs.c.created_at,
                audit_logs.c.action_type,
                audit_logs.c.changes_json,
            )
            .where(sa.func.lower(audit_logs.c.entity_type) == "solicitud")
            .where(audit_logs.c.entity_id.in_([str(sid) for sid in ids_activa]))
        ).fetchall()

        candidates: dict[int, list] = defaultdict(list)
        for row in audit_rows:
            entity_id_raw = str(getattr(row, "entity_id", "") or "").strip()
            if not entity_id_raw.isdigit():
                continue
            sid = int(entity_id_raw)
            if sid not in set(ids_activa):
                continue

            action_type = str(getattr(row, "action_type", "") or "").strip().upper()
            changes = getattr(row, "changes_json", None)
            to_state = ""
            if isinstance(changes, dict):
                estado_change = changes.get("estado")
                if isinstance(estado_change, dict):
                    to_state = str(estado_change.get("to") or "").strip().lower()

            if action_type in {"SOLICITUD_ACTIVAR", "SOLICITUD_REACTIVAR"} or to_state == "activa":
                candidates[sid].append(row)

        for sid in ids_activa:
            latest = _pick_latest(candidates.get(sid, []), "created_at")
            if latest is not None:
                audit_activa_by_solicitud[sid] = latest

    updates = []
    for row in rows:
        sid = int(getattr(row, "id", 0) or 0)
        if sid <= 0:
            continue
        if getattr(row, "estado_actual_desde", None) is not None:
            continue

        estado = str(getattr(row, "estado", "") or "").strip().lower()
        picked = None

        if estado == "reemplazo":
            picked = reemplazo_inicio_by_solicitud.get(sid)
            if picked is None:
                picked = getattr(row, "fecha_ultima_modificacion", None) or getattr(row, "fecha_solicitud", None)
        elif estado == "activa":
            picked = audit_activa_by_solicitud.get(sid)
            if picked is None:
                picked = (
                    getattr(row, "fecha_inicio_seguimiento", None)
                    or getattr(row, "fecha_ultima_modificacion", None)
                    or getattr(row, "fecha_solicitud", None)
                )
        else:
            picked = getattr(row, "fecha_ultima_modificacion", None) or getattr(row, "fecha_solicitud", None)

        if picked is None:
            continue

        updates.append({"sid": sid, "estado_actual_desde": picked})

    if updates:
        bind.execute(
            solicitudes.update()
            .where(solicitudes.c.id == sa.bindparam("sid"))
            .values(estado_actual_desde=sa.bindparam("estado_actual_desde")),
            updates,
        )


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "solicitudes"):
        return
    if _has_column(bind, "solicitudes", "estado_actual_desde"):
        op.drop_column("solicitudes", "estado_actual_desde")
