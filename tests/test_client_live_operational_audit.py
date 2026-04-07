# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import clientes.routes as clientes_routes


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
    with patch("clientes.routes._cliente_live_resolve_target", return_value=(7, 88)):
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
