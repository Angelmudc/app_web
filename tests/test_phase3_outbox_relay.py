# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import DomainOutbox, OutboxConsumerReceipt, StaffNotificacion
from utils.outbox_relay import (
    _consume_internal_operational_notification,
    _event_envelope,
    relay_pending_once,
)
from utils.timezone import utc_now_naive


class _RedisOkStub:
    def __init__(self):
        self.calls = []

    def xadd(self, stream, fields):
        self.calls.append((stream, fields))
        return "1-0"


class _RedisFailStub:
    def xadd(self, stream, fields):
        raise RuntimeError("redis_down")


def _ensure_tables():
    DomainOutbox.__table__.drop(bind=db.engine, checkfirst=True)
    DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)
    OutboxConsumerReceipt.__table__.create(bind=db.engine, checkfirst=True)
    StaffNotificacion.__table__.create(bind=db.engine, checkfirst=True)


def _reset_tables():
    db.session.query(OutboxConsumerReceipt).delete()
    db.session.query(StaffNotificacion).delete()
    db.session.query(DomainOutbox).delete()
    db.session.commit()


def _new_outbox(event_type: str, payload: dict | None = None) -> DomainOutbox:
    next_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
    row = DomainOutbox(
        id=next_id,
        event_id=f"evt_{event_type}_{utc_now_naive().timestamp()}",
        event_type=event_type,
        aggregate_type="Solicitud",
        aggregate_id="101",
        aggregate_version=7,
        occurred_at=utc_now_naive(),
        actor_id="staff:1",
        region="admin",
        payload=payload or {"solicitud_id": 101, "from": "proceso", "to": "activa"},
        schema_version=1,
        correlation_id="corr-101",
        idempotency_key="idem-101",
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_outbox_envelope_contract_v1():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("SOLICITUD_ESTADO_CAMBIADO")

        env = _event_envelope(row)

        assert env["schema_version"] == 1
        assert env["event_id"] == row.event_id
        assert env["event_type"] == "SOLICITUD_ESTADO_CAMBIADO"
        assert env["correlation_id"] == "corr-101"
        assert env["idempotency_key"] == "idem-101"
        assert env["aggregate"]["type"] == "Solicitud"
        assert env["aggregate"]["id"] == "101"
        assert env["aggregate"]["version"] == 7
        assert env["payload"]["solicitud_id"] == 101
        assert env["occurred_at"]
        assert env["recorded_at"]


def test_relay_success_marks_published_and_creates_internal_notification():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("SOLICITUD_PAGO_REGISTRADO", {"solicitud_id": 101, "estado": "pagada"})
        redis_stub = _RedisOkStub()

        stats = relay_pending_once(redis_client=redis_stub, stream_key="sys:test:v1")

        refreshed = DomainOutbox.query.get(row.id)
        assert stats["picked"] == 1
        assert stats["published"] == 1
        assert stats["failed"] == 0
        assert refreshed is not None
        assert refreshed.published_at is not None
        assert int(refreshed.published_attempts or 0) == 1
        assert str(getattr(refreshed, "relay_status", "") or "") == "published"
        assert refreshed.quarantined_at is None
        assert refreshed.quarantine_reason is None
        assert refreshed.last_error is None
        assert len(redis_stub.calls) == 1
        assert db.session.query(StaffNotificacion).count() == 1


def test_relay_failure_sets_retry_backoff_and_error():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("SOLICITUD_ESTADO_CAMBIADO")

        stats = relay_pending_once(
            redis_client=_RedisFailStub(),
            stream_key="sys:test:v1",
            max_backoff_seconds=60,
        )

        refreshed = DomainOutbox.query.get(row.id)
        assert stats["picked"] == 1
        assert stats["published"] == 0
        assert stats["failed"] == 1
        assert refreshed is not None
        assert refreshed.published_at is None
        assert int(refreshed.published_attempts or 0) == 1
        assert str(getattr(refreshed, "relay_status", "") or "") == "retrying"
        assert refreshed.first_failed_at is not None
        assert refreshed.quarantined_at is None
        assert refreshed.quarantine_reason is None
        assert "redis_down" in str(refreshed.last_error or "")
        assert refreshed.last_attempt_at is not None
        assert refreshed.next_retry_at is not None

        # Debe respetar next_retry_at y no reintentar inmediatamente.
        stats_retry = relay_pending_once(
            redis_client=_RedisFailStub(),
            stream_key="sys:test:v1",
            max_backoff_seconds=60,
        )
        assert stats_retry["picked"] == 0


def test_relay_quarantines_event_when_max_attempts_exhausted():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("SOLICITUD_ESTADO_CAMBIADO")

        first = relay_pending_once(
            redis_client=_RedisFailStub(),
            stream_key="sys:test:v1",
            max_attempts=2,
        )
        assert first["picked"] == 1
        assert first["failed"] == 1
        assert first["quarantined"] == 0

        retry_row = DomainOutbox.query.get(row.id)
        assert retry_row is not None
        retry_row.next_retry_at = utc_now_naive() - timedelta(seconds=1)
        db.session.add(retry_row)
        db.session.commit()

        second = relay_pending_once(
            redis_client=_RedisFailStub(),
            stream_key="sys:test:v1",
            max_attempts=2,
        )

        quarantined = DomainOutbox.query.get(row.id)
        assert second["picked"] == 1
        assert second["failed"] == 1
        assert second["quarantined"] == 1
        assert quarantined is not None
        assert quarantined.published_at is None
        assert int(quarantined.published_attempts or 0) == 2
        assert str(getattr(quarantined, "relay_status", "") or "") == "quarantined"
        assert quarantined.quarantined_at is not None
        assert str(getattr(quarantined, "quarantine_reason", "") or "") == "max_attempts_exhausted"
        assert quarantined.next_retry_at is None


def test_relay_skips_quarantined_rows_from_normal_pick():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("SOLICITUD_ESTADO_CAMBIADO")
        row.relay_status = "quarantined"
        row.quarantined_at = utc_now_naive()
        row.quarantine_reason = "max_attempts_exhausted"
        db.session.add(row)
        db.session.commit()

        stats = relay_pending_once(redis_client=_RedisOkStub(), stream_key="sys:test:v1")
        assert stats["picked"] == 0
        assert stats["published"] == 0
        assert stats["failed"] == 0


def test_internal_consumer_idempotent_by_event_id():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        row = _new_outbox("REEMPLAZO_CANCELADO", {"solicitud_id": 101, "reemplazo_id": 55})
        event = _event_envelope(row)

        _consume_internal_operational_notification(event, row)
        _consume_internal_operational_notification(event, row)
        db.session.commit()

        assert db.session.query(OutboxConsumerReceipt).count() == 1
        assert db.session.query(StaffNotificacion).count() == 1


def test_relay_excludes_events_outside_initial_catalog():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        allowed = _new_outbox("SOLICITUD_ESTADO_CAMBIADO")
        excluded_matching = _new_outbox("MATCHING_CANDIDATAS_ENVIADAS")
        excluded_candidate = _new_outbox("CANDIDATA_ESTADO_CAMBIADO", {"candidata_id": 1, "to": "trabajando"})
        redis_stub = _RedisOkStub()

        stats = relay_pending_once(redis_client=redis_stub, stream_key="sys:test:v1")

        a = DomainOutbox.query.get(allowed.id)
        m = DomainOutbox.query.get(excluded_matching.id)
        c = DomainOutbox.query.get(excluded_candidate.id)
        assert stats["picked"] == 1
        assert stats["published"] == 1
        assert a.published_at is not None
        assert m.published_at is None
        assert c.published_at is None
        assert len(redis_stub.calls) == 1
