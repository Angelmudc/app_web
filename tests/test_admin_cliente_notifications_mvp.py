# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummySolicitud:
    def __init__(self, solicitud_id=10, cliente_id=7, codigo="SOL-010"):
        self.id = int(solicitud_id)
        self.cliente_id = int(cliente_id)
        self.codigo_solicitud = str(codigo)
        self.estado = "activa"


class _DummyNotifRow:
    def __init__(self, payload=None):
        self.id = 99
        self.payload = dict(payload or {})
        self.titulo = "old"
        self.cuerpo = "old"
        self.updated_at = datetime.utcnow()


class _NotifQuery:
    def __init__(self, existing=None):
        self.existing = existing

    def filter_by(self, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.existing


class AdminClienteNotificationsMVPTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True

    def test_status_change_creates_notification_for_relevant_state(self):
        solicitud = _DummySolicitud()
        transition = {"changed": True, "from": "activa", "to": "espera_pago"}

        with flask_app.app_context():
            with patch.object(admin_routes.ClienteNotificacion, "query", _NotifQuery(existing=None)), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.flush"), \
                 patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
                admin_routes._notify_cliente_status_change(solicitud, transition)

        notif_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], admin_routes.ClienteNotificacion)]
        self.assertEqual(len(notif_rows), 1)
        notif = notif_rows[0]
        self.assertEqual(notif.tipo, "solicitud_estado")
        self.assertIn("espera de pago", (notif.cuerpo or "").lower())
        outbox_mock.assert_called_once()
        self.assertEqual(outbox_mock.call_args.kwargs.get("event_type"), "CLIENTE_NOTIFICACION_CREATED")

    def test_status_change_dedupes_when_same_target_state_exists(self):
        solicitud = _DummySolicitud()
        existing = _DummyNotifRow(payload={"from": "activa", "to": "espera_pago"})
        transition = {"changed": True, "from": "activa", "to": "espera_pago"}

        with flask_app.app_context():
            with patch.object(admin_routes.ClienteNotificacion, "query", _NotifQuery(existing=existing)), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.flush"), \
                 patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
                admin_routes._notify_cliente_status_change(solicitud, transition)

        notif_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], admin_routes.ClienteNotificacion)]
        self.assertEqual(len(notif_rows), 0)
        outbox_mock.assert_not_called()
        self.assertIn("espera de pago", (existing.cuerpo or "").lower())

    def test_candidata_seleccionada_creates_notification(self):
        solicitud = _DummySolicitud()

        with flask_app.app_context():
            with patch.object(admin_routes.ClienteNotificacion, "query", _NotifQuery(existing=None)), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.flush"), \
                 patch("admin.routes._emit_domain_outbox_event"):
                admin_routes._notify_cliente_candidata_asignada(solicitud, candidata_id=501)

        notif_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], admin_routes.ClienteNotificacion)]
        self.assertEqual(len(notif_rows), 1)
        notif = notif_rows[0]
        self.assertEqual(notif.tipo, "candidata_seleccionada")
        self.assertEqual((notif.payload or {}).get("candidata_id"), 501)


if __name__ == "__main__":
    unittest.main()
