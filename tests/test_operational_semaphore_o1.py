# -*- coding: utf-8 -*-
from __future__ import annotations

import secrets
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import DomainOutbox
from utils.enterprise_layer import O2_TREND_METRIC_KEYS
from utils.timezone import utc_now_naive


def _login(client, usuario: str = "Owner", clave: str = "admin123"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _rebuild_domain_outbox_table():
    DomainOutbox.__table__.drop(bind=db.engine, checkfirst=True)
    DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)


def test_operational_health_json_exposes_o1_metrics():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/health/operational?format=json", follow_redirects=False)
    assert resp.status_code == 200
    data = resp.get_json() or {}

    assert "generated_at" in data
    metrics = data.get("metrics") or {}
    statuses = data.get("statuses") or {}
    assert "outbox_backlog_pending" in metrics
    assert "outbox_oldest_pending_age_seconds" in metrics
    assert "outbox_quarantined_total" in metrics
    assert "outbox_quarantined_last_15m" in metrics
    assert "outbox_retrying_total" in metrics
    assert "relay_fail_rate_pct_15m" in metrics
    assert "relay_retry_rate_pct_15m" in metrics
    assert "relay_throughput_per_min_15m" in metrics
    assert "live_polling_fallback_pct_15m" in metrics
    assert "live_poll_degraded_outbox_fallback_count_15m" in metrics
    assert "live_refetch_latency_by_region_15m" in metrics
    assert "concurrency_idempotency_conflicts_15m" in metrics
    assert "critical_5xx_endpoints_15m" in metrics
    assert "outbox_backlog_pending" in statuses
    assert isinstance(data.get("alerts"), list)
    assert "snapshot_policy" in data
    trends = data.get("trends") or {}
    trend_metrics = trends.get("metrics") or {}
    assert "outbox_backlog_pending" in trend_metrics
    assert "outbox_quarantined_total" in trend_metrics
    assert "relay_fail_rate_pct_15m" in trend_metrics

    trends_resp = client.get("/admin/health/operational/trends", follow_redirects=False)
    assert trends_resp.status_code == 200
    trends_payload = trends_resp.get_json() or {}
    assert "metrics" in trends_payload


def test_live_observability_ingest_updates_fallback_and_refetch_metrics():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    ping_fallback = client.post(
        "/admin/live/observability",
        json={"event": "fallback_entered"},
        follow_redirects=False,
    )
    assert ping_fallback.status_code == 200
    assert (ping_fallback.get_json() or {}).get("accepted") is True

    ping_open = client.post(
        "/admin/live/observability",
        json={"event": "sse_open"},
        follow_redirects=False,
    )
    assert ping_open.status_code == 200
    assert (ping_open.get_json() or {}).get("accepted") is True

    ping_refetch = client.post(
        "/admin/live/observability",
        json={
            "event": "refetch_region",
            "region": "prioridadSummaryAsyncRegion",
            "duration_ms": 275,
            "ok": True,
        },
        follow_redirects=False,
    )
    assert ping_refetch.status_code == 200
    refetch_payload = ping_refetch.get_json() or {}
    assert refetch_payload.get("accepted") is True
    assert refetch_payload.get("region") == "prioridadsummaryasyncregion"

    after = client.get("/admin/health/operational?format=json", follow_redirects=False).get_json() or {}
    after_metrics = after.get("metrics") or {}
    assert "live_fallback_count_15m" in after_metrics
    assert "live_sse_open_count_15m" in after_metrics

    latency = (after_metrics.get("live_refetch_latency_by_region_15m") or {})
    assert isinstance(latency, dict)
    row = latency.get("prioridadsummaryasyncregion") or {}
    if row:
        assert int(row.get("count") or 0) >= 1
        assert float(row.get("avg_ms") or 0.0) >= 1.0


def test_live_observability_ingest_keeps_working_when_csrf_enabled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    flask_app.config["WTF_CSRF_ENABLED"] = True
    resp = client.post(
        "/admin/live/observability",
        json={"event": "sse_open"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("ok") is True
    assert data.get("accepted") is True


def test_degraded_outbox_poll_updates_operational_counter():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        next_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
        now = utc_now_naive()
        db.session.add(
            DomainOutbox(
                id=next_id,
                event_id=f"test_o1_{secrets.token_hex(8)}",
                event_type="SOLICITUD_ESTADO_CAMBIADO",
                aggregate_type="Solicitud",
                aggregate_id="99901",
                aggregate_version=1,
                occurred_at=now,
                actor_id="staff:1",
                region="admin",
                payload={"solicitud_id": 99901, "from": "pendiente", "to": "activa"},
                schema_version=1,
                published_at=None,
            )
        )
        db.session.commit()

    with patch("admin.routes.bump_operational_counter") as bump_counter:
        poll = client.get(
            f"/admin/live/invalidation/poll?after_id={max(0, next_id - 1)}&limit=25&view=solicitud_detail",
            follow_redirects=False,
        )
    bump_counter.assert_called_with("live:poll:degraded_outbox_fallback_count")
    assert poll.status_code == 200
    assert (poll.get_json() or {}).get("mode") == "degraded_outbox_fallback"

    metrics = (client.get("/admin/health/operational?format=json", follow_redirects=False).get_json() or {}).get("metrics") or {}
    assert "live_poll_degraded_outbox_fallback_count_15m" in metrics


def test_operational_trends_endpoint_has_stable_shape():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/health/operational/trends", follow_redirects=False)
    assert resp.status_code == 200
    data = resp.get_json() or {}

    assert "generated_at" in data
    assert "metrics" in data
    assert "latest_snapshot_at" in data
    assert "previous_snapshot_at" in data
    assert "samples_last_24h" in data

    metrics = data.get("metrics") or {}
    assert sorted(metrics.keys()) == sorted(O2_TREND_METRIC_KEYS)

    for key in O2_TREND_METRIC_KEYS:
        row = metrics.get(key) or {}
        assert "current" in row
        assert "previous" in row
        assert "delta" in row
        assert "direction" in row
        assert "windows" in row
        windows = row.get("windows") or {}
        assert "1h" in windows
        assert "24h" in windows


def test_operational_health_alerts_quarantine_growth():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        _rebuild_domain_outbox_table()
        next_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
        now = utc_now_naive()
        db.session.add(
            DomainOutbox(
                id=next_id,
                event_id=f"test_o1_q_{secrets.token_hex(8)}",
                event_type="SOLICITUD_ESTADO_CAMBIADO",
                aggregate_type="Solicitud",
                aggregate_id="99902",
                aggregate_version=1,
                occurred_at=now,
                actor_id="staff:1",
                region="admin",
                payload={"solicitud_id": 99902},
                schema_version=1,
                published_at=None,
                relay_status="quarantined",
                quarantined_at=now,
                quarantine_reason="max_attempts_exhausted",
            )
        )
        db.session.commit()

    payload = client.get("/admin/health/operational?format=json", follow_redirects=False).get_json() or {}
    alerts = payload.get("alerts") or []
    metrics = payload.get("metrics") or {}
    assert int(metrics.get("outbox_quarantined_total") or 0) >= 1
    assert int(metrics.get("outbox_quarantined_last_15m") or 0) >= 1
    assert any(str(a.get("code") or "") == "outbox_quarantine_growth" for a in alerts)
