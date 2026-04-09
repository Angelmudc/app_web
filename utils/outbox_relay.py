# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from typing import Any

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from config_app import db
from models import DomainOutbox, OutboxConsumerReceipt
from utils.staff_notifications import create_staff_notification
from utils.timezone import iso_utc_z, utc_now_naive


OUTBOX_RELAY_ALLOWED_EVENT_TYPES = {
    "SOLICITUD_PAGO_REGISTRADO",
    "SOLICITUD_ESTADO_CAMBIADO",
    "REEMPLAZO_ABIERTO",
    "REEMPLAZO_FINALIZADO",
    "REEMPLAZO_CANCELADO",
    "REEMPLAZO_CERRADO_ASIGNANDO",
    "SOLICITUD_CANDIDATA_ASIGNADA",
    "SOLICITUD_CANDIDATAS_LIBERADAS",
    "CHAT_MESSAGE_CREATED",
    "CHAT_CONVERSATION_READ",
    "CHAT_CONVERSATION_STATUS_CHANGED",
    "CHAT_CONVERSATION_ASSIGNED",
    "CHAT_CONVERSATION_TYPING",
}

_INTERNAL_CONSUMER_NAME = "internal_operational_notifications_v1"
_OUTBOX_RELAY_STATUS_PENDING = "pending"
_OUTBOX_RELAY_STATUS_RETRYING = "retrying"
_OUTBOX_RELAY_STATUS_QUARANTINED = "quarantined"
_OUTBOX_RELAY_STATUS_PUBLISHED = "published"


def _redis_stream_key() -> str:
    cfg_key = str(current_app.config.get("OUTBOX_RELAY_STREAM_KEY") or "").strip()
    env_key = str(os.getenv("OUTBOX_RELAY_STREAM_KEY") or "").strip()
    return cfg_key or env_key or "sys:domain_events:v1"


def _redis_url() -> str:
    return (
        str(current_app.config.get("CACHE_REDIS_URL") or "").strip()
        or str(os.getenv("BACKPLANE_REDIS_URL") or "").strip()
        or str(os.getenv("REDIS_URL") or "").strip()
        or str(os.getenv("CACHE_REDIS_URL") or "").strip()
    )


def _redis_client():
    import redis

    url = _redis_url()
    if not url:
        raise RuntimeError("Redis URL no configurada para outbox relay.")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)


def _max_attempts() -> int:
    raw = (
        current_app.config.get("OUTBOX_RELAY_MAX_ATTEMPTS")
        or os.getenv("OUTBOX_RELAY_MAX_ATTEMPTS")
        or "8"
    )
    try:
        return max(1, int(raw))
    except Exception:
        return 8


def _maybe_assign_sqlite_pk(model_obj, model_cls) -> None:
    try:
        bind = db.session.get_bind()
        if str(getattr(getattr(bind, "dialect", None), "name", "")).strip().lower() != "sqlite":
            return
        if getattr(model_obj, "id", None):
            return
        max_id = db.session.query(db.func.max(model_cls.id)).scalar() or 0
        model_obj.id = int(max_id) + 1
    except Exception:
        return


def _event_envelope(row: DomainOutbox) -> dict[str, Any]:
    return {
        "schema_version": int(getattr(row, "schema_version", 1) or 1),
        "event_id": str(row.event_id or ""),
        "event_type": str(row.event_type or ""),
        "occurred_at": iso_utc_z(getattr(row, "occurred_at", None)),
        "recorded_at": iso_utc_z(getattr(row, "created_at", None)),
        "actor_id": str(getattr(row, "actor_id", "") or "") or None,
        "correlation_id": str(getattr(row, "correlation_id", "") or "") or None,
        "idempotency_key": str(getattr(row, "idempotency_key", "") or "") or None,
        "region": str(getattr(row, "region", "") or "") or None,
        "aggregate": {
            "type": str(getattr(row, "aggregate_type", "") or ""),
            "id": str(getattr(row, "aggregate_id", "") or ""),
            "version": getattr(row, "aggregate_version", None),
        },
        "payload": dict(getattr(row, "payload", None) or {}),
    }


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except Exception:
        return False


def _notification_text(event: dict[str, Any]) -> tuple[str, str]:
    event_type = str(event.get("event_type") or "").strip().upper()
    payload = event.get("payload") or {}
    sid = payload.get("solicitud_id") or event.get("aggregate", {}).get("id")
    rid = payload.get("reemplazo_id")
    mapping = {
        "SOLICITUD_PAGO_REGISTRADO": "Pago registrado en solicitud",
        "SOLICITUD_ESTADO_CAMBIADO": "Estado de solicitud actualizado",
        "REEMPLAZO_ABIERTO": "Reemplazo iniciado",
        "REEMPLAZO_FINALIZADO": "Reemplazo finalizado",
        "REEMPLAZO_CANCELADO": "Reemplazo cancelado",
        "REEMPLAZO_CERRADO_ASIGNANDO": "Reemplazo cerrado con asignación",
        "SOLICITUD_CANDIDATA_ASIGNADA": "Candidata asignada a solicitud",
        "SOLICITUD_CANDIDATAS_LIBERADAS": "Candidatas liberadas de solicitud",
        "CHAT_MESSAGE_CREATED": "Nuevo mensaje en chat cliente",
        "CHAT_CONVERSATION_READ": "Conversación de chat leída",
        "CHAT_CONVERSATION_STATUS_CHANGED": "Estado de conversación de chat actualizado",
        "CHAT_CONVERSATION_ASSIGNED": "Asignación de conversación de chat actualizada",
        "CHAT_CONVERSATION_TYPING": "Actividad de escritura en chat cliente",
    }
    titulo = mapping.get(event_type) or f"Evento crítico {event_type}"
    detalle = f"Solicitud {sid}" if sid is not None else "Solicitud"
    if rid is not None:
        detalle = f"{detalle} · Reemplazo {rid}"
    return titulo[:180], detalle[:300]


def _register_consumer_receipt(*, consumer_name: str, event_id: str) -> bool:
    try:
        with db.session.begin_nested():
            row = OutboxConsumerReceipt(
                consumer_name=(consumer_name or "")[:80],
                event_id=(event_id or "")[:64],
            )
            _maybe_assign_sqlite_pk(row, OutboxConsumerReceipt)
            db.session.add(row)
            db.session.flush()
        return True
    except IntegrityError:
        return False


def _consume_internal_operational_notification(event: dict[str, Any], row: DomainOutbox) -> None:
    event_type = str(event.get("event_type") or "").strip().upper()
    if event_type == "CHAT_CONVERSATION_TYPING":
        return
    event_id = str(event.get("event_id") or "")
    if not event_id:
        raise RuntimeError("Evento sin event_id para consumidor interno.")
    if not _register_consumer_receipt(consumer_name=_INTERNAL_CONSUMER_NAME, event_id=event_id):
        return

    payload = event.get("payload") or {}
    solicitud_id = payload.get("solicitud_id") or event.get("aggregate", {}).get("id")
    entity_id = int(row.id)
    if _is_int(solicitud_id):
        sid = int(solicitud_id)
        if sid > 0:
            entity_id = sid

    titulo, mensaje = _notification_text(event)
    notif_ok = create_staff_notification(
        tipo=(f"relay_{event_id[:40]}"),
        entity_type="solicitud",
        entity_id=entity_id,
        titulo=titulo,
        mensaje=mensaje,
        payload={
            "event_id": event_id,
            "event_type": event.get("event_type"),
            "correlation_id": event.get("correlation_id"),
            "aggregate": event.get("aggregate") or {},
        },
        session_commit=False,
    )
    if not notif_ok:
        raise RuntimeError("No se pudo crear notificación interna del relay.")


def _retry_delay_seconds(attempts: int, max_backoff_seconds: int) -> int:
    base = 5
    exp = base * (2 ** max(0, int(attempts) - 1))
    return max(5, min(int(max_backoff_seconds), int(exp)))


def _quarantine_reason() -> str:
    # R1-C2: motivo estable/corto; detalle técnico queda en last_error.
    return "max_attempts_exhausted"


def relay_pending_once(
    *,
    batch_size: int = 50,
    max_backoff_seconds: int = 300,
    max_attempts: int | None = None,
    redis_client=None,
    stream_key: str | None = None,
) -> dict[str, int]:
    now = utc_now_naive()
    stream = (stream_key or "").strip() or _redis_stream_key()
    client = redis_client or _redis_client()

    max_attempts = max(1, int(max_attempts or _max_attempts()))

    pending_query = (
        DomainOutbox.query
        .filter(DomainOutbox.published_at.is_(None))
        .filter(DomainOutbox.event_type.in_(sorted(OUTBOX_RELAY_ALLOWED_EVENT_TYPES)))
        .filter(or_(DomainOutbox.relay_status.is_(None), DomainOutbox.relay_status != _OUTBOX_RELAY_STATUS_QUARANTINED))
        .filter(or_(DomainOutbox.next_retry_at.is_(None), DomainOutbox.next_retry_at <= now))
        .order_by(DomainOutbox.created_at.asc(), DomainOutbox.id.asc())
        .limit(max(1, int(batch_size)))
    )

    # En PostgreSQL, evita que dos workers relay tomen las mismas filas al mismo tiempo.
    try:
        bind = db.session.get_bind()
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "")).strip().lower()
    except Exception:
        dialect = ""
    if dialect == "postgresql":
        pending_query = pending_query.with_for_update(skip_locked=True)

    pending_rows = pending_query.all()

    stats = {"picked": len(pending_rows), "published": 0, "failed": 0, "quarantined": 0}

    for row in pending_rows:
        row_now = utc_now_naive()
        try:
            envelope = _event_envelope(row)
            client.xadd(
                stream,
                {"event": json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))},
            )
            _consume_internal_operational_notification(envelope, row)

            attempts = int(getattr(row, "published_attempts", 0) or 0) + 1
            row.published_attempts = attempts
            row.relay_status = _OUTBOX_RELAY_STATUS_PUBLISHED
            row.last_attempt_at = row_now
            row.first_failed_at = None
            row.last_error = None
            row.next_retry_at = None
            row.quarantined_at = None
            row.quarantine_reason = None
            row.published_at = row_now
            db.session.add(row)
            db.session.commit()
            stats["published"] += 1
        except Exception as exc:
            db.session.rollback()
            fail_row = DomainOutbox.query.get(int(row.id))
            if fail_row is None:
                stats["failed"] += 1
                continue
            attempts = int(getattr(fail_row, "published_attempts", 0) or 0) + 1
            delay = _retry_delay_seconds(attempts, max_backoff_seconds=max_backoff_seconds)
            fail_row.published_attempts = attempts
            fail_row.last_attempt_at = row_now
            if getattr(fail_row, "first_failed_at", None) is None:
                fail_row.first_failed_at = row_now
            fail_row.last_error = str(exc)[:500]
            if attempts >= max_attempts:
                fail_row.relay_status = _OUTBOX_RELAY_STATUS_QUARANTINED
                fail_row.quarantined_at = row_now
                fail_row.quarantine_reason = _quarantine_reason()
                fail_row.next_retry_at = None
                stats["quarantined"] += 1
            else:
                fail_row.relay_status = _OUTBOX_RELAY_STATUS_RETRYING
                fail_row.quarantined_at = None
                fail_row.quarantine_reason = None
                fail_row.next_retry_at = row_now + timedelta(seconds=delay)
            db.session.add(fail_row)
            db.session.commit()
            stats["failed"] += 1

    return stats


def run_relay_loop(
    *,
    batch_size: int = 50,
    poll_seconds: float = 0.3,
    max_backoff_seconds: int = 300,
    max_attempts: int | None = None,
    once: bool = False,
    stream_key: str | None = None,
) -> dict[str, int]:
    summary = {"cycles": 0, "published": 0, "failed": 0, "picked": 0, "quarantined": 0}
    while True:
        stats = relay_pending_once(
            batch_size=batch_size,
            max_backoff_seconds=max_backoff_seconds,
            max_attempts=max_attempts,
            stream_key=stream_key,
        )
        summary["cycles"] += 1
        summary["published"] += int(stats.get("published", 0) or 0)
        summary["failed"] += int(stats.get("failed", 0) or 0)
        summary["picked"] += int(stats.get("picked", 0) or 0)
        summary["quarantined"] += int(stats.get("quarantined", 0) or 0)
        if once:
            return summary
        time.sleep(max(0.1, float(poll_seconds)))


def list_quarantined_events(
    *,
    limit: int = 25,
    event_type: str | None = None,
    aggregate_type: str | None = None,
) -> list[DomainOutbox]:
    q = (
        DomainOutbox.query
        .filter(DomainOutbox.relay_status == _OUTBOX_RELAY_STATUS_QUARANTINED)
        .order_by(DomainOutbox.quarantined_at.desc(), DomainOutbox.id.desc())
    )
    et = str(event_type or "").strip().upper()
    if et:
        q = q.filter(DomainOutbox.event_type == et)
    at = str(aggregate_type or "").strip()
    if at:
        q = q.filter(DomainOutbox.aggregate_type == at)
    return q.limit(max(1, min(int(limit), 200))).all()


def requeue_quarantined_event(*, row_id: int | None = None, event_id: str | None = None) -> DomainOutbox | None:
    rid = int(row_id or 0)
    eid = str(event_id or "").strip()
    row = None
    if rid > 0:
        row = DomainOutbox.query.get(rid)
    elif eid:
        row = DomainOutbox.query.filter(DomainOutbox.event_id == eid[:64]).first()
    else:
        raise ValueError("id o event_id requerido para requeue.")

    if row is None:
        return None
    if str(getattr(row, "relay_status", "") or "") != _OUTBOX_RELAY_STATUS_QUARANTINED:
        raise ValueError("Solo se permite requeue de eventos en cuarentena.")

    # Reset seguro para reiniciar ciclo de publicación controladamente.
    row.relay_status = _OUTBOX_RELAY_STATUS_PENDING
    row.published_attempts = 0
    row.first_failed_at = None
    row.last_error = None
    row.last_attempt_at = None
    row.next_retry_at = None
    row.quarantined_at = None
    row.quarantine_reason = None
    db.session.add(row)
    db.session.commit()
    return row


@click.group("outbox-relay")
def outbox_relay_cli():
    """Comandos del relay de outbox (Fase 3 C1)."""


@outbox_relay_cli.command("run")
@click.option("--once", is_flag=True, default=False, help="Ejecuta un solo ciclo de relay.")
@click.option("--batch-size", default=50, show_default=True, type=int, help="Cantidad máxima por ciclo.")
@click.option("--poll-seconds", default=0.3, show_default=True, type=float, help="Pausa entre ciclos.")
@click.option(
    "--max-backoff-seconds",
    default=300,
    show_default=True,
    type=int,
    help="Backoff máximo de reintentos por evento fallido.",
)
@click.option(
    "--max-attempts",
    default=8,
    show_default=True,
    type=int,
    help="Máximo de intentos antes de cuarentena por evento.",
)
@click.option(
    "--stream-key",
    default="",
    show_default=False,
    help="Redis Stream destino. Si se omite usa OUTBOX_RELAY_STREAM_KEY o valor por defecto.",
)
@with_appcontext
def outbox_relay_run_command(
    once: bool,
    batch_size: int,
    poll_seconds: float,
    max_backoff_seconds: int,
    max_attempts: int,
    stream_key: str,
):
    stats = run_relay_loop(
        batch_size=max(1, int(batch_size)),
        poll_seconds=max(0.1, float(poll_seconds)),
        max_backoff_seconds=max(5, int(max_backoff_seconds)),
        max_attempts=max(1, int(max_attempts)),
        once=bool(once),
        stream_key=(stream_key or "").strip() or None,
    )
    click.echo(
        f"relay_cycles={int(stats.get('cycles', 0))} "
        f"picked={int(stats.get('picked', 0))} "
        f"published={int(stats.get('published', 0))} "
        f"failed={int(stats.get('failed', 0))} "
        f"quarantined={int(stats.get('quarantined', 0))}"
    )


@outbox_relay_cli.group("quarantine")
def outbox_relay_quarantine_group():
    """Operación mínima de cuarentena para domain_outbox."""


@outbox_relay_quarantine_group.command("list")
@click.option("--limit", default=25, show_default=True, type=int, help="Cantidad máxima de filas.")
@click.option("--event-type", default="", show_default=False, help="Filtro por event_type.")
@click.option("--aggregate-type", default="", show_default=False, help="Filtro por aggregate_type.")
@with_appcontext
def outbox_relay_quarantine_list_command(limit: int, event_type: str, aggregate_type: str):
    rows = list_quarantined_events(
        limit=max(1, min(int(limit), 200)),
        event_type=(event_type or "").strip() or None,
        aggregate_type=(aggregate_type or "").strip() or None,
    )
    click.echo(f"quarantined_count={len(rows)}")
    for row in rows:
        err = str(getattr(row, "last_error", "") or "").replace("\n", " ").strip()
        if len(err) > 120:
            err = f"{err[:117]}..."
        click.echo(
            " ".join(
                [
                    f"id={int(getattr(row, 'id', 0) or 0)}",
                    f"event_id={str(getattr(row, 'event_id', '') or '')}",
                    f"event_type={str(getattr(row, 'event_type', '') or '')}",
                    f"aggregate_type={str(getattr(row, 'aggregate_type', '') or '')}",
                    f"aggregate_id={str(getattr(row, 'aggregate_id', '') or '')}",
                    f"published_attempts={int(getattr(row, 'published_attempts', 0) or 0)}",
                    f"quarantined_at={str(getattr(row, 'quarantined_at', None) or '')}",
                    f"quarantine_reason={str(getattr(row, 'quarantine_reason', '') or '')}",
                    f"last_error={err}",
                ]
            )
        )


@outbox_relay_quarantine_group.command("requeue")
@click.option("--id", "row_id", default=0, type=int, help="ID de domain_outbox en cuarentena.")
@click.option("--event-id", default="", help="event_id de domain_outbox en cuarentena.")
@with_appcontext
def outbox_relay_quarantine_requeue_command(row_id: int, event_id: str):
    rid = int(row_id or 0)
    eid = str(event_id or "").strip()
    if (rid > 0 and eid) or (rid <= 0 and not eid):
        raise click.ClickException("Debes indicar exactamente uno: --id o --event-id.")
    try:
        row = requeue_quarantined_event(
            row_id=(rid if rid > 0 else None),
            event_id=(eid or None),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))
    if row is None:
        raise click.ClickException("Evento no encontrado.")

    payload = {
        "action": "outbox_quarantine_requeue_manual",
        "row_id": int(getattr(row, "id", 0) or 0),
        "event_id": str(getattr(row, "event_id", "") or ""),
        "event_type": str(getattr(row, "event_type", "") or ""),
        "aggregate_type": str(getattr(row, "aggregate_type", "") or ""),
        "aggregate_id": str(getattr(row, "aggregate_id", "") or ""),
    }
    current_app.logger.info("[r1-c3] %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))
    click.echo(
        f"ok=1 action=requeue id={payload['row_id']} "
        f"event_id={payload['event_id']} relay_status=pending"
    )
