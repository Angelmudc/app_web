# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class ClientesContextProcessorsOptimizationTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True

    def test_tres_context_processors_reusan_un_solo_snapshot_por_request(self):
        FakeCliente = type("FakeCliente", (), {})
        fake_user = FakeCliente()
        fake_user.id = 7
        fake_user.is_authenticated = True

        with patch.object(clientes_routes, "Cliente", FakeCliente):
            with patch.object(clientes_routes, "current_user", fake_user):
                with patch.object(
                    clientes_routes,
                    "_query_client_fixed_metrics_combined",
                    return_value={
                        "notif_unread_count": 3,
                        "chat_unread_count": 5,
                        "client_live_after_id": 19,
                    },
                ) as combined_mock:
                    with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                        notif_ctx = clientes_routes._inject_client_notif_unread_count()
                        chat_ctx = clientes_routes._inject_client_chat_unread_count()
                        live_ctx = clientes_routes._inject_client_live_after_id()

        self.assertEqual(combined_mock.call_count, 1)
        self.assertEqual(notif_ctx.get("notif_unread_count"), 3)
        self.assertEqual(chat_ctx.get("chat_unread_count"), 5)
        self.assertEqual(live_ctx.get("client_live_after_id"), 19)

    def test_snapshot_anonimo_devuelve_ceros(self):
        fake_user = type("AnonUser", (), {"is_authenticated": False})()
        with patch.object(clientes_routes, "current_user", fake_user):
            with patch.object(clientes_routes, "_query_client_fixed_metrics_combined") as combined_mock:
                with flask_app.test_request_context("/clientes/login", method="GET"):
                    notif_ctx = clientes_routes._inject_client_notif_unread_count()
                    chat_ctx = clientes_routes._inject_client_chat_unread_count()
                    live_ctx = clientes_routes._inject_client_live_after_id()

        self.assertEqual(combined_mock.call_count, 0)
        self.assertEqual(notif_ctx.get("notif_unread_count"), 0)
        self.assertEqual(chat_ctx.get("chat_unread_count"), 0)
        self.assertEqual(live_ctx.get("client_live_after_id"), 0)


if __name__ == "__main__":
    unittest.main()
