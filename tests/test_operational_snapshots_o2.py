# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta
import os

from app import app as flask_app
from config_app import db
from models import OperationalMetricSnapshot
from utils.timezone import utc_now_naive
from utils.enterprise_layer import (
    O2_TREND_METRIC_KEYS,
    cleanup_operational_snapshots,
    operational_trends_payload,
)


def _reset_snapshots_table():
    OperationalMetricSnapshot.__table__.drop(bind=db.engine, checkfirst=True)
    OperationalMetricSnapshot.__table__.create(bind=db.engine, checkfirst=True)
    db.session.query(OperationalMetricSnapshot).delete()
    db.session.commit()


def test_operational_snapshots_capture_cli_creates_row():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        _reset_snapshots_table()

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["operational-snapshots", "capture", "--once"])

    assert result.exit_code == 0
    assert "snapshot_id=" in result.output
    assert "captured_at=" in result.output

    with flask_app.app_context():
        count = db.session.query(OperationalMetricSnapshot).count()
        assert count >= 1


def test_operational_snapshots_cleanup_cli_prunes_old_rows():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        _reset_snapshots_table()

        old_row = OperationalMetricSnapshot(
            captured_at=utc_now_naive() - timedelta(hours=30),
            window_minutes=15,
            metrics={"outbox_backlog_pending": 10},
        )
        fresh_row = OperationalMetricSnapshot(
            captured_at=utc_now_naive() - timedelta(hours=1),
            window_minutes=15,
            metrics={"outbox_backlog_pending": 5},
        )
        db.session.add(old_row)
        db.session.add(fresh_row)
        db.session.commit()

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["operational-snapshots", "cleanup", "--retention-hours", "24"])

    assert result.exit_code == 0
    assert "deleted=" in result.output

    with flask_app.app_context():
        rows = (
            OperationalMetricSnapshot.query
            .order_by(OperationalMetricSnapshot.captured_at.asc(), OperationalMetricSnapshot.id.asc())
            .all()
        )
        assert len(rows) == 1
        assert (rows[0].metrics or {}).get("outbox_backlog_pending") == 5


def test_operational_trends_without_snapshots_returns_unknowns_and_none():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        _reset_snapshots_table()
        payload = operational_trends_payload(
            current_metrics={
                "outbox_backlog_pending": 10,
            }
        )

    assert "generated_at" in payload
    assert payload.get("samples_last_24h") == 0
    assert payload.get("latest_snapshot_at") is None
    assert payload.get("previous_snapshot_at") is None

    metrics = payload.get("metrics") or {}
    assert sorted(metrics.keys()) == sorted(O2_TREND_METRIC_KEYS)
    row = metrics["outbox_backlog_pending"]
    assert row["current"] == 10
    assert row["previous"] is None
    assert row["delta"] is None
    assert row["direction"] == "unknown"
    assert row["windows"]["1h"]["baseline"] is None
    assert row["windows"]["1h"]["direction"] == "unknown"
    assert row["windows"]["24h"]["baseline"] is None
    assert row["windows"]["24h"]["direction"] == "unknown"


def test_operational_trends_direction_flat_and_delta_for_int_and_pct():
    flask_app.config["TESTING"] = True

    now = utc_now_naive()
    with flask_app.app_context():
        _reset_snapshots_table()
        db.session.add(
            OperationalMetricSnapshot(
                captured_at=now - timedelta(minutes=20),
                window_minutes=15,
                metrics={
                    "outbox_backlog_pending": 20,
                    "relay_fail_rate_pct_15m": 1.25,
                    "live_polling_fallback_pct_15m": 3.5,
                },
            )
        )
        db.session.commit()

        payload = operational_trends_payload(
            current_metrics={
                "outbox_backlog_pending": 20,
                "relay_fail_rate_pct_15m": 2.75,
                "live_polling_fallback_pct_15m": None,
            }
        )

    metrics = payload.get("metrics") or {}
    int_row = metrics["outbox_backlog_pending"]
    pct_row = metrics["relay_fail_rate_pct_15m"]
    none_row = metrics["live_polling_fallback_pct_15m"]

    assert int_row["delta"] == 0.0
    assert int_row["direction"] == "flat"

    assert pct_row["previous"] == 1.25
    assert pct_row["current"] == 2.75
    assert pct_row["delta"] == 1.5
    assert pct_row["direction"] == "up"

    assert none_row["current"] is None
    assert none_row["delta"] is None
    assert none_row["direction"] == "unknown"


def test_operational_trends_without_1h_baseline_but_with_24h_baseline():
    flask_app.config["TESTING"] = True
    now = utc_now_naive()

    with flask_app.app_context():
        _reset_snapshots_table()
        db.session.add(
            OperationalMetricSnapshot(
                captured_at=now - timedelta(hours=2),
                window_minutes=15,
                metrics={"critical_5xx_endpoints_15m": 2},
            )
        )
        db.session.commit()

        payload = operational_trends_payload(
            current_metrics={"critical_5xx_endpoints_15m": 4}
        )

    row = (payload.get("metrics") or {})["critical_5xx_endpoints_15m"]
    assert row["windows"]["1h"]["baseline"] is None
    assert row["windows"]["1h"]["direction"] == "unknown"
    assert row["windows"]["24h"]["baseline"] == 2
    assert row["windows"]["24h"]["delta"] == 2.0
    assert row["windows"]["24h"]["direction"] == "up"


def test_cleanup_operational_snapshots_respects_retention_override():
    flask_app.config["TESTING"] = True
    now = utc_now_naive()

    with flask_app.app_context():
        _reset_snapshots_table()
        prev = os.environ.get("O2_SNAPSHOT_RETENTION_HOURS")
        try:
            os.environ["O2_SNAPSHOT_RETENTION_HOURS"] = "168"
            db.session.add_all(
                [
                    OperationalMetricSnapshot(
                        captured_at=now - timedelta(hours=26),
                        window_minutes=15,
                        metrics={"outbox_backlog_pending": 1},
                    ),
                    OperationalMetricSnapshot(
                        captured_at=now - timedelta(hours=23),
                        window_minutes=15,
                        metrics={"outbox_backlog_pending": 2},
                    ),
                ]
            )
            db.session.commit()

            deleted = cleanup_operational_snapshots(retention_hours=24)
            rows = (
                OperationalMetricSnapshot.query
                .order_by(OperationalMetricSnapshot.captured_at.asc(), OperationalMetricSnapshot.id.asc())
                .all()
            )
        finally:
            if prev is None:
                os.environ.pop("O2_SNAPSHOT_RETENTION_HOURS", None)
            else:
                os.environ["O2_SNAPSHOT_RETENTION_HOURS"] = prev

    assert deleted == 1
    assert len(rows) == 1
    assert (rows[0].metrics or {}).get("outbox_backlog_pending") == 2


def test_cleanup_operational_snapshots_keeps_rows_within_retention():
    flask_app.config["TESTING"] = True
    now = utc_now_naive()

    with flask_app.app_context():
        _reset_snapshots_table()
        db.session.add(
            OperationalMetricSnapshot(
                captured_at=now - timedelta(hours=2),
                window_minutes=15,
                metrics={"outbox_backlog_pending": 9},
            )
        )
        db.session.commit()

        deleted = cleanup_operational_snapshots(retention_hours=24)
        rows = OperationalMetricSnapshot.query.all()

    assert deleted == 0
    assert len(rows) == 1
