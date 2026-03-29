# -*- coding: utf-8 -*-
from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import DomainOutbox
from utils.timezone import utc_now_naive


def _rebuild_domain_outbox_table():
    DomainOutbox.__table__.drop(bind=db.engine, checkfirst=True)
    DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)


def _new_outbox(*, row_id: int, relay_status: str, event_type: str = "SOLICITUD_ESTADO_CAMBIADO") -> DomainOutbox:
    now = utc_now_naive()
    row = DomainOutbox(
        id=int(row_id),
        event_id=f"evt_q_cli_{row_id}",
        event_type=event_type,
        aggregate_type="Solicitud",
        aggregate_id=str(9000 + int(row_id)),
        aggregate_version=1,
        occurred_at=now,
        actor_id="staff:1",
        region="admin",
        payload={"solicitud_id": 9000 + int(row_id)},
        schema_version=1,
        published_attempts=(3 if relay_status == "quarantined" else 0),
        relay_status=relay_status,
        first_failed_at=(now if relay_status == "quarantined" else None),
        last_error=("redis_down" if relay_status == "quarantined" else None),
        last_attempt_at=(now if relay_status == "quarantined" else None),
        next_retry_at=None,
        quarantined_at=(now if relay_status == "quarantined" else None),
        quarantine_reason=("max_attempts_exhausted" if relay_status == "quarantined" else None),
        published_at=(now if relay_status == "published" else None),
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_outbox_quarantine_list_cli_shows_quarantined_rows():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        _new_outbox(row_id=1, relay_status="quarantined")
        _new_outbox(row_id=2, relay_status="pending")

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["outbox-relay", "quarantine", "list", "--limit", "10"])

    assert result.exit_code == 0
    assert "quarantined_count=1" in result.output
    assert "id=1" in result.output
    assert "event_id=evt_q_cli_1" in result.output
    assert "quarantine_reason=max_attempts_exhausted" in result.output
    assert "published_attempts=3" in result.output


def test_outbox_quarantine_requeue_cli_resets_to_pending():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        row = _new_outbox(row_id=10, relay_status="quarantined")
        row_id = int(row.id)

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["outbox-relay", "quarantine", "requeue", "--id", str(row_id)])

    assert result.exit_code == 0
    assert "ok=1 action=requeue" in result.output

    with flask_app.app_context():
        refreshed = DomainOutbox.query.get(row_id)
        assert refreshed is not None
        assert str(refreshed.relay_status or "") == "pending"
        assert int(refreshed.published_attempts or 0) == 0
        assert refreshed.first_failed_at is None
        assert refreshed.last_error is None
        assert refreshed.last_attempt_at is None
        assert refreshed.next_retry_at is None
        assert refreshed.quarantined_at is None
        assert refreshed.quarantine_reason is None


def test_outbox_quarantine_requeue_cli_rejects_non_quarantined_rows():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        row = _new_outbox(row_id=20, relay_status="pending")
        row_id = int(row.id)

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["outbox-relay", "quarantine", "requeue", "--id", str(row_id)])

    assert result.exit_code != 0
    assert "Solo se permite requeue de eventos en cuarentena" in result.output


def test_outbox_quarantine_requeue_cli_supports_event_id_selector():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        row = _new_outbox(row_id=30, relay_status="quarantined")
        event_id = str(row.event_id)

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["outbox-relay", "quarantine", "requeue", "--event-id", event_id])

    assert result.exit_code == 0
    assert "relay_status=pending" in result.output
