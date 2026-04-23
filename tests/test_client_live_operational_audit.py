# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import clientes.routes as clientes_routes
from app import app as flask_app


def _row(*, row_id: int, event_type: str, payload: dict):
    return SimpleNamespace(
        id=int(row_id),
        event_id=f"evt_{row_id}",
        event_type=event_type,
        payload=dict(payload or {}),
        aggregate_id=str(payload.get("solicitud_id") or payload.get("cliente_id") or "0"),
        occurred_at=None,
        created_at=None,
    )


def test_status_change_event_invalidates_dashboard_list_and_detail():
    row = _row(
        row_id=10,
        event_type="SOLICITUD_ESTADO_CAMBIADO",
        payload={"cliente_id": 7, "solicitud_id": 44, "from": "proceso", "to": "activa"},
    )
    with patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True):
        evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is not None
    views = set((evt.get("invalidate") or {}).get("views") or [])
    assert "dashboard" in views
    assert "solicitudes_list" in views
    assert "solicitud_detail" in views
    assert evt.get("event_type") == "cliente.solicitud.status_changed"


def test_notification_event_invalidates_notifications_live():
    row = _row(
        row_id=11,
        event_type="CLIENTE_NOTIFICACION_CREATED",
        payload={"cliente_id": 7, "notificacion_id": 33, "tipo": "candidatas_enviadas"},
    )
    with patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True):
        evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is not None
    views = set((evt.get("invalidate") or {}).get("views") or [])
    assert "notifications" in views
    assert evt.get("event_type") == "cliente.notificacion.created"


def test_event_is_dropped_when_cliente_mismatch():
    row = _row(
        row_id=12,
        event_type="CLIENTE_SOLICITUD_UPDATED",
        payload={"cliente_id": 99, "solicitud_id": 55},
    )
    evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is None


def test_event_without_cliente_id_resolves_through_solicitud_lookup():
    row = _row(
        row_id=13,
        event_type="SOLICITUD_CANDIDATA_ASIGNADA",
        payload={"solicitud_id": 88, "candidata_id": 501},
    )
    with patch("clientes.routes._cliente_live_resolve_target", return_value=(7, 88)), \
         patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True):
        evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is not None
    assert (evt.get("target") or {}).get("cliente_id") == 7
    assert (evt.get("target") or {}).get("solicitud_id") == 88


def test_event_without_cliente_and_unresolved_solicitud_is_dropped():
    row = _row(
        row_id=14,
        event_type="SOLICITUD_CANDIDATA_ASIGNADA",
        payload={"solicitud_id": 999999, "candidata_id": 501},
    )
    with patch("clientes.routes._cliente_live_resolve_target", return_value=(0, 999999)):
        evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is None


def test_normalize_dedupes_ownership_validation_when_payload_and_target_match():
    row = _row(
        row_id=15,
        event_type="CLIENTE_SOLICITUD_UPDATED",
        payload={"cliente_id": 7, "solicitud_id": 501, "estado": "activa"},
    )
    with patch("clientes.routes._cliente_live_resolve_target", return_value=(7, 501)), \
         patch("clientes.routes._cliente_live_target_matches_solicitud", return_value=True) as match_mock:
        evt = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)
    assert evt is not None
    assert match_mock.call_count == 1


def test_normalize_reuses_request_cache_for_repeated_solicitud_owner_lookup():
    row_a = _row(
        row_id=16,
        event_type="CLIENTE_SOLICITUD_UPDATED",
        payload={"solicitud_id": 777, "estado": "activa"},
    )
    row_b = _row(
        row_id=17,
        event_type="CLIENTE_SOLICITUD_STATUS_CHANGED",
        payload={"solicitud_id": 777, "to": "proceso"},
    )
    calls = {"scalar": 0}

    class _OwnerScalarQuery:
        def filter(self, *_a, **_k):
            return self

        def scalar(self):
            calls["scalar"] += 1
            return 7

    flask_app.config["TESTING"] = True
    with flask_app.test_request_context("/clientes/live/invalidation/poll?after_id=0&limit=2", method="GET"):
        with patch("clientes.routes.db.session.query", return_value=_OwnerScalarQuery()):
            evt_a = clientes_routes._normalize_cliente_live_event_from_outbox(row_a, current_cliente_id=7)
            evt_b = clientes_routes._normalize_cliente_live_event_from_outbox(row_b, current_cliente_id=7)

    assert evt_a is not None
    assert evt_b is not None
    assert calls["scalar"] == 1
