# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import DomainOutbox
import clientes.routes as clientes_routes


class _NotifRow:
    def __init__(self, *, notif_id: int, cliente_id: int, solicitud_id: int, is_read: bool = False):
        self.id = notif_id
        self.cliente_id = cliente_id
        self.solicitud_id = solicitud_id
        self.tipo = "candidatas_enviadas"
        self.titulo = "Titulo"
        self.cuerpo = "Detalle"
        self.payload = {}
        self.is_read = is_read
        self.is_deleted = False
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()


class _NotifQuery:
    def __init__(self, rows):
        self.rows = list(rows or [])
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def first_or_404(self):
        for row in self.rows:
            ok = True
            for k, v in self.filters.items():
                if getattr(row, k, None) != v:
                    ok = False
                    break
            if ok:
                return row
        raise Exception("not_found")

    def count(self):
        total = 0
        for row in self.rows:
            ok = True
            for k, v in self.filters.items():
                if getattr(row, k, None) != v:
                    ok = False
                    break
            if ok:
                total += 1
        return total


def _client_user(cid: int = 7):
    return SimpleNamespace(id=cid, role="cliente", codigo=f"CL-{cid:03d}", nombre_completo=f"Cliente {cid}")


def _new_outbox(*, row_id: int, event_type: str, payload: dict) -> DomainOutbox:
    now = clientes_routes.utc_now_naive()
    return DomainOutbox(
        id=int(row_id),
        event_id=f"evt_client_live_{row_id}",
        event_type=event_type,
        aggregate_type="Solicitud",
        aggregate_id=str(payload.get("solicitud_id") or payload.get("cliente_id") or 0),
        aggregate_version=1,
        occurred_at=now,
        actor_id="staff:1",
        region="admin",
        payload=dict(payload or {}),
        schema_version=1,
        created_at=now,
    )


def _ensure_outbox_table():
    with flask_app.app_context():
        DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)


def test_client_live_poll_only_returns_events_for_current_cliente():
    flask_app.config["TESTING"] = True
    _ensure_outbox_table()
    with flask_app.app_context():
        db.session.query(DomainOutbox).delete()
        db.session.add(_new_outbox(row_id=1001, event_type="SOLICITUD_ESTADO_CAMBIADO", payload={"cliente_id": 7, "solicitud_id": 41, "to": "activa"}))
        db.session.add(_new_outbox(row_id=1002, event_type="SOLICITUD_ESTADO_CAMBIADO", payload={"cliente_id": 9, "solicitud_id": 77, "to": "cancelada"}))
        db.session.commit()

        target = clientes_routes.clientes_live_invalidation_poll
        for _ in range(2):
            target = target.__wrapped__

        with patch.object(clientes_routes, "current_user", _client_user(7)), \
             patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True):
            with flask_app.test_request_context("/clientes/live/invalidation/poll?after_id=0&limit=50", method="GET"):
                resp = target()

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    items = payload.get("items") or []
    assert len(items) == 1
    assert items[0]["target"]["cliente_id"] == 7
    assert items[0]["event_type"] == "cliente.solicitud.status_changed"
    assert int(payload.get("next_after_id") or 0) >= 1002


def test_client_live_poll_covers_created_updated_events_for_cliente_views():
    flask_app.config["TESTING"] = True
    _ensure_outbox_table()
    with flask_app.app_context():
        db.session.query(DomainOutbox).delete()
        db.session.add(_new_outbox(row_id=1101, event_type="CLIENTE_SOLICITUD_CREATED", payload={"cliente_id": 7, "solicitud_id": 501, "estado": "proceso"}))
        db.session.add(_new_outbox(row_id=1102, event_type="CLIENTE_SOLICITUD_UPDATED", payload={"cliente_id": 7, "solicitud_id": 501, "estado": "activa"}))
        db.session.commit()

        target = clientes_routes.clientes_live_invalidation_poll
        for _ in range(2):
            target = target.__wrapped__

        with patch.object(clientes_routes, "current_user", _client_user(7)), \
             patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True):
            with flask_app.test_request_context("/clientes/live/invalidation/poll?after_id=0&limit=50", method="GET"):
                resp = target()

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    items = payload.get("items") or []
    assert len(items) == 2
    types = [str(x.get("event_type") or "") for x in items]
    assert "cliente.solicitud.created" in types
    assert "cliente.solicitud.updated" in types
    for item in items:
        views = set(((item.get("invalidate") or {}).get("views") or []))
        assert "dashboard" in views
        assert "solicitudes_list" in views
        assert "solicitud_detail" in views


def test_client_live_rejects_payload_with_inconsistent_cliente_solicitud():
    row = _new_outbox(
        row_id=1201,
        event_type="CLIENTE_SOLICITUD_UPDATED",
        payload={"cliente_id": 7, "solicitud_id": 9991, "estado": "activa"},
    )
    with patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=False):
        normalized = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert normalized is None


def test_client_legacy_solicitudes_live_endpoint_is_gone():
    flask_app.config["TESTING"] = True
    target = clientes_routes.clientes_solicitudes_live
    for _ in range(2):
        target = target.__wrapped__
    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", _client_user(7)):
            with flask_app.test_request_context("/clientes/solicitudes/live", method="GET"):
                resp = target()
    assert resp.status_code == 410
    payload = resp.get_json() or {}
    assert payload.get("error") == "deprecated_endpoint"
    assert "/clientes/live/invalidation/poll" in str((payload.get("replaced_by") or {}).get("poll_url") or "")


def test_client_live_stream_once_has_sse_headers_and_heartbeat():
    flask_app.config["TESTING"] = True
    target = clientes_routes.clientes_live_invalidation_stream
    for _ in range(2):
        target = target.__wrapped__

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", _client_user(7)):
            with flask_app.test_request_context("/clientes/live/invalidation/stream?once=1", method="GET"):
                resp = target()
                body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "text/event-stream" in (resp.headers.get("Content-Type") or "")
    assert "event: heartbeat" in body


def test_client_live_stream_requires_authentication_redirects():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    resp = client.get("/clientes/live/invalidation/stream?once=1", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/clientes/login" in (resp.headers.get("Location") or "")


def test_notificacion_ver_emits_client_outbox_event():
    flask_app.config["TESTING"] = True
    notif = _NotifRow(notif_id=17, cliente_id=7, solicitud_id=3001, is_read=False)

    target = clientes_routes.notificacion_ver
    for _ in range(2):
        target = target.__wrapped__

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", _client_user(7)), \
             patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([notif])), \
             patch("clientes.routes.db.session.commit") as commit_mock, \
             patch("clientes.routes.db.session.add") as add_mock:
            with flask_app.test_request_context(
                "/clientes/notificaciones/17/ver",
                method="POST",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target(17)

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("unread_count") is not None
    outbox_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], DomainOutbox)]
    assert len(outbox_rows) >= 1
    assert any(str(getattr(r, "event_type", "")).upper() == "CLIENTE_NOTIFICACION_READ" for r in outbox_rows)
    commit_mock.assert_called_once()


def test_templates_wire_client_live_assets_and_scopes():
    base_path = os.path.join(os.getcwd(), "templates", "clientes", "base.html")
    dash_path = os.path.join(os.getcwd(), "templates", "clientes", "dashboard.html")
    list_path = os.path.join(os.getcwd(), "templates", "clientes", "solicitudes_list.html")
    detail_path = os.path.join(os.getcwd(), "templates", "clientes", "solicitud_detail.html")
    js_path = os.path.join(os.getcwd(), "static", "js", "core", "client_live_invalidation.js")

    with open(base_path, "r", encoding="utf-8") as f:
        base_txt = f.read()
    with open(dash_path, "r", encoding="utf-8") as f:
        dash_txt = f.read()
    with open(list_path, "r", encoding="utf-8") as f:
        list_txt = f.read()
    with open(detail_path, "r", encoding="utf-8") as f:
        detail_txt = f.read()
    with open(js_path, "r", encoding="utf-8") as f:
        js_txt = f.read()

    assert "data-client-live-stream-url" in base_txt
    assert "data-client-live-poll-url" in base_txt
    assert "client_live_invalidation.js" in base_txt
    assert 'data-client-live-view="dashboard"' in dash_txt
    assert 'data-client-live-view="solicitudes_list"' in list_txt
    assert 'data-client-live-view="solicitud_detail"' in detail_txt
    assert "new EventSource" in js_txt
    assert "pollOnce" in js_txt


def test_client_live_js_reconvergence_contract():
    js_path = os.path.join(os.getcwd(), "static", "js", "core", "client_live_invalidation.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_txt = f.read()

    assert "window.__CLIENT_LIVE_POLL_CONNECTED_MS" in js_txt
    assert "window.__CLIENT_LIVE_POLL_FALLBACK_MS" in js_txt
    assert "function setFallbackMode(enabled)" in js_txt
    assert "eventSource.onopen = function () {" in js_txt
    assert "if (pollTimer && pollIntervalMs === wait) return;" in js_txt
    assert "window.__clientLiveRuntime" in js_txt
    assert 'window.addEventListener("visibilitychange"' in js_txt
    assert "if (!fallbackMode) {" in js_txt
    assert 'markTransport("paused_hidden", "document_hidden")' in js_txt
