# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets

from app import app as flask_app
from config_app import db
from models import DomainOutbox
from utils.timezone import utc_now_naive


def _login(client, usuario: str = "Cruz", clave: str = "8998"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _new_outbox(event_type: str, payload: dict, *, row_id: int, published: bool = True) -> DomainOutbox:
    now = utc_now_naive()
    return DomainOutbox(
        id=int(row_id),
        event_id=f"test_f4_{secrets.token_hex(8)}",
        event_type=event_type,
        aggregate_type="Solicitud",
        aggregate_id=str(payload.get("solicitud_id") or "0"),
        aggregate_version=1,
        occurred_at=now,
        actor_id="staff:1",
        region="admin",
        payload=payload,
        schema_version=1,
        published_at=now if published else None,
    )


def test_live_invalidation_poll_filters_allowlist_and_shapes_payload():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)
        base_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
        allowed = _new_outbox(
            "SOLICITUD_ESTADO_CAMBIADO",
            {"solicitud_id": 701, "from": "proceso", "to": "activa"},
            row_id=base_id,
        )
        excluded = _new_outbox(
            "CANDIDATA_ESTADO_CAMBIADO",
            {"solicitud_id": 701, "candidata_id": 9},
            row_id=base_id + 1,
        )
        db.session.add(allowed)
        db.session.add(excluded)
        db.session.commit()
        allowed_event_id = allowed.event_id

    resp = client.get("/admin/live/invalidation/poll?after_id=0&limit=50", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert isinstance(payload.get("items"), list)

    items = payload.get("items") or []
    assert all((item.get("event_type") or "").strip().upper() != "CANDIDATA_ESTADO_CAMBIADO" for item in items)
    target_item = next((item for item in items if item.get("event_id") == allowed_event_id), None)
    assert target_item is not None
    assert target_item.get("event_type") == "SOLICITUD_ESTADO_CAMBIADO"
    assert (target_item.get("target") or {}).get("entity_type") == "solicitud"
    assert int((target_item.get("target") or {}).get("solicitud_id") or 0) == 701


def test_live_invalidation_poll_keeps_relay_mode_when_published_events_exist():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)
        base_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
        published_row = _new_outbox(
            "SOLICITUD_ESTADO_CAMBIADO",
            {"solicitud_id": 810, "from": "proceso", "to": "activa"},
            row_id=base_id,
            published=True,
        )
        unpublished_row = _new_outbox(
            "SOLICITUD_ESTADO_CAMBIADO",
            {"solicitud_id": 811, "from": "proceso", "to": "activa"},
            row_id=base_id + 1,
            published=False,
        )
        db.session.add(published_row)
        db.session.add(unpublished_row)
        db.session.commit()
        published_event_id = str(published_row.event_id)
        unpublished_event_id = str(unpublished_row.event_id)
        base_after_id = int(base_id) - 1

    resp = client.get(
        f"/admin/live/invalidation/poll?after_id={base_after_id}&limit=25&view=solicitud_detail",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("mode") == "relay_published"
    assert (resp.headers.get("X-Live-Invalidation-Mode") or "").strip() == "relay_published"

    items = payload.get("items") or []
    ids = {str(item.get("event_id") or "") for item in items}
    assert published_event_id in ids
    assert unpublished_event_id not in ids


def test_live_invalidation_poll_degraded_outbox_fallback_returns_unpublished_and_moves_cursor():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)
        base_id = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0) + 1
        unpublished_row = _new_outbox(
            "SOLICITUD_ESTADO_CAMBIADO",
            {"solicitud_id": 920, "from": "pendiente", "to": "activa"},
            row_id=base_id,
            published=False,
        )
        db.session.add(unpublished_row)
        db.session.commit()
        row_id = int(unpublished_row.id)
        row_event_id = str(unpublished_row.event_id)

    first = client.get(
        f"/admin/live/invalidation/poll?after_id={max(0, row_id - 1)}&limit=25&view=cliente_detail",
        follow_redirects=False,
    )
    assert first.status_code == 200
    payload = first.get_json() or {}
    assert payload.get("mode") == "degraded_outbox_fallback"
    assert (first.headers.get("X-Live-Invalidation-Mode") or "").strip() == "degraded_outbox_fallback"
    assert int(payload.get("next_after_id") or 0) >= row_id
    items = payload.get("items") or []
    assert any(str(item.get("event_id") or "") == row_event_id for item in items)

    second = client.get(
        f"/admin/live/invalidation/poll?after_id={int(payload.get('next_after_id') or 0)}&limit=25&view=cliente_detail",
        follow_redirects=False,
    )
    assert second.status_code == 200
    payload_second = second.get_json() or {}
    assert payload_second.get("mode") == "relay_published"
    assert all(str(item.get("event_id") or "") != row_event_id for item in (payload_second.get("items") or []))


def test_live_invalidation_poll_perf_headers_for_prioridad_view():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get(
        "/admin/live/invalidation/poll?after_id=0&limit=5&view=solicitudes_prioridad_summary",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "X-P1C1-Perf-Scope" in resp.headers
    assert "X-P1C1-Perf-Latency-Ms" in resp.headers
    assert "X-P1C1-Perf-DB-Queries" in resp.headers
    assert "X-P1C1-Perf-DB-Time-Ms" in resp.headers


def test_live_invalidation_stream_once_headers_and_heartbeat():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/live/invalidation/stream?once=1", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/event-stream" in (resp.headers.get("Content-Type") or "")
    body = (resp.get_data(as_text=True) or "")
    assert "event: heartbeat" in body


def test_live_invalidation_poll_reports_poll_only_when_sse_disabled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev = flask_app.config.get("ADMIN_LIVE_SSE_ENABLED", True)
    flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = False
    try:
        client = flask_app.test_client()
        assert _login(client).status_code in (302, 303)
        resp = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
    finally:
        flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = prev

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("mode") == "poll_only"
    assert (resp.headers.get("X-Live-Invalidation-Mode") or "").strip() == "poll_only"


def test_live_invalidation_stream_returns_poll_only_event_when_sse_disabled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev = flask_app.config.get("ADMIN_LIVE_SSE_ENABLED", True)
    flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = False
    try:
        client = flask_app.test_client()
        assert _login(client).status_code in (302, 303)
        resp = client.get("/admin/live/invalidation/stream", follow_redirects=False)
        body = (resp.get_data(as_text=True) or "")
    finally:
        flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = prev

    assert resp.status_code == 200
    assert "text/event-stream" in (resp.headers.get("Content-Type") or "")
    assert (resp.headers.get("X-Live-Invalidation-Mode") or "").strip() == "poll_only"
    assert "event: poll_only" in body


def test_live_invalidation_stream_probe_returns_poll_only_json_when_sse_disabled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev = flask_app.config.get("ADMIN_LIVE_SSE_ENABLED", True)
    flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = False
    try:
        client = flask_app.test_client()
        assert _login(client).status_code in (302, 303)
        resp = client.get(
            "/admin/live/invalidation/stream?probe=1",
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )
    finally:
        flask_app.config["ADMIN_LIVE_SSE_ENABLED"] = prev

    assert resp.status_code == 503
    payload = resp.get_json() or {}
    assert payload.get("mode") == "poll_only"
    assert (resp.headers.get("X-Live-Invalidation-Mode") or "").strip() == "poll_only"


def test_solicitudes_list_template_declares_live_invalidation_summary_scope():
    tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitudes_list.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert 'data-live-invalidation-scope="1"' in txt
    assert 'data-live-invalidation-view="solicitudes_summary"' in txt
    assert 'data-live-invalidation-stream-url="{{ url_for(\'admin.live_invalidation_stream\') }}"' in txt
    assert 'data-live-invalidation-poll-url="{{ url_for(\'admin.live_invalidation_poll\') }}"' in txt
    assert 'data-live-region-summary-url="{{ url_for(\'admin.solicitudes_summary_fragment\') }}"' in txt
    assert 'data-live-observability-url="{{ url_for(\'admin.live_observability_ingest\') }}"' in txt


def test_live_invalidation_js_supports_solicitudes_summary_mode():
    js_path = os.path.join(os.getcwd(), "static", "js", "core", "live_invalidation.js")
    with open(js_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert "view !== \"solicitud_detail\"" in txt
    assert "view !== \"cliente_detail\"" in txt
    assert "view !== \"solicitudes_summary\"" in txt
    assert "setRegion(\"#solicitudesSummaryAsyncRegion\", \"data-live-region-summary-url\")" in txt
    assert "url.searchParams.set(\"view\", view);" in txt
    assert "if (view === \"solicitudes_summary\") {" in txt
    assert "return [\"#solicitudesSummaryAsyncRegion\"];" in txt
    assert "meta[name=\"csrf-token\"]" in txt
    assert "\"X-CSRFToken\": csrfToken" in txt


def test_live_invalidation_poll_perf_headers_for_solicitudes_summary_view():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get(
        "/admin/live/invalidation/poll?after_id=0&limit=5&view=solicitudes_summary",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "X-P1C1-Perf-Scope" in resp.headers
    assert "X-P1C1-Perf-Latency-Ms" in resp.headers
    assert "X-P1C1-Perf-DB-Queries" in resp.headers
    assert "X-P1C1-Perf-DB-Time-Ms" in resp.headers


def test_live_invalidation_poll_perf_headers_for_solicitud_detail_view():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get(
        "/admin/live/invalidation/poll?after_id=0&limit=5&view=solicitud_detail",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "X-P1C1-Perf-Scope" in resp.headers
    assert "X-P1C1-Perf-Latency-Ms" in resp.headers
    assert "X-P1C1-Perf-DB-Queries" in resp.headers
    assert "X-P1C1-Perf-DB-Time-Ms" in resp.headers


def test_solicitudes_prioridad_template_declares_live_invalidation_summary_scope():
    tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitudes_prioridad.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert 'data-live-invalidation-scope="1"' in txt
    assert 'data-live-invalidation-view="solicitudes_prioridad_summary"' in txt
    assert 'data-live-invalidation-stream-url="{{ url_for(\'admin.live_invalidation_stream\') }}"' in txt
    assert 'data-live-invalidation-poll-url="{{ url_for(\'admin.live_invalidation_poll\') }}"' in txt
    assert "data-live-region-summary-url=\"{{ url_for('admin.solicitudes_prioridad_summary_fragment'" in txt
    assert "data-live-region-responsables-url=\"{{ url_for('admin.solicitudes_prioridad_responsables_fragment'" in txt
    assert 'data-live-observability-url="{{ url_for(\'admin.live_observability_ingest\') }}"' in txt


def test_live_invalidation_js_supports_solicitudes_prioridad_summary_mode():
    js_path = os.path.join(os.getcwd(), "static", "js", "core", "live_invalidation.js")
    with open(js_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert "view !== \"solicitudes_prioridad_summary\"" in txt
    assert "setRegion(\"#prioridadSummaryAsyncRegion\", \"data-live-region-summary-url\")" in txt
    assert "setRegion(\"#prioridadResponsablesAsyncRegion\", \"data-live-region-responsables-url\")" in txt
    assert "if (view === \"solicitudes_prioridad_summary\") {" in txt
    assert "return [\"#prioridadSummaryAsyncRegion\", \"#prioridadResponsablesAsyncRegion\"];" in txt
    assert "eventSource.addEventListener(\"poll_only\"" in txt
    assert "if (headerMode === \"poll_only\") {" in txt


def test_solicitud_detail_template_declares_live_fragment_urls():
    tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitud_detail.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert 'data-live-invalidation-view="solicitud_detail"' in txt
    assert "data-live-region-summary-url=\"{{ url_for('admin.solicitud_detail_summary_fragment'" in txt
    assert "data-live-region-operativa-url=\"{{ url_for('admin.solicitud_detail_operativa_core_fragment'" in txt


def test_cliente_detail_template_declares_live_fragment_urls():
    tpl_path = os.path.join(os.getcwd(), "templates", "admin", "cliente_detail.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        txt = f.read()

    assert 'data-live-invalidation-view="cliente_detail"' in txt
    assert "data-live-region-summary-url=\"{{ url_for('admin.cliente_detail_summary_fragment'" in txt
    assert "data-live-region-solicitudes-url=\"{{ url_for('admin.cliente_detail_solicitudes_fragment'" in txt
