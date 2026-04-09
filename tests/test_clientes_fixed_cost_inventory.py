# -*- coding: utf-8 -*-

import unittest

from flask import make_response

import clientes.routes as clientes_routes
from app import app as flask_app


class ClientesFixedCostInventoryTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True

    def tearDown(self):
        flask_app.config.pop("CLIENTES_FIXED_COST_INVENTORY_ENABLED", None)

    def test_aplica_headers_fixed_cost_con_resumen_top(self):
        flask_app.config["CLIENTES_FIXED_COST_INVENTORY_ENABLED"] = True
        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            response = make_response("<html>ok</html>", 200)
            clientes_routes._clientes_fixed_cost_record("ctx.notif_unread", 2.5, meta={"query_executed": True})
            clientes_routes._clientes_fixed_cost_record("ctx.chat_unread", 7.1, meta={"query_executed": True})
            clientes_routes._clientes_fixed_cost_record("ctx.live_after_id", 1.3, meta={"query_executed": True})
            clientes_routes._clientes_fixed_cost_record("ctx.shell_assets", 0.2, meta={"total_count": 15})

            response = clientes_routes._apply_clientes_fixed_cost_headers(response)

        self.assertEqual(response.headers.get("X-Clientes-FixedCost-Items"), "4")
        self.assertIn("ctx.chat_unread:7.10", response.headers.get("X-Clientes-FixedCost-Top", ""))
        self.assertEqual(response.headers.get("X-Clientes-FixedCost-Chat-Unread-Ms"), "7.10")
        self.assertEqual(response.headers.get("X-Clientes-FixedCost-Notif-Unread-Ms"), "2.50")
        self.assertEqual(response.headers.get("X-Clientes-FixedCost-Live-After-Id-Ms"), "1.30")
        self.assertEqual(response.headers.get("X-Clientes-FixedCost-Shell-Assets-Ms"), "0.20")

    def test_no_aplica_headers_si_flag_esta_apagado(self):
        flask_app.config["CLIENTES_FIXED_COST_INVENTORY_ENABLED"] = False
        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            response = make_response("<html>ok</html>", 200)
            clientes_routes._clientes_fixed_cost_record("ctx.chat_unread", 5.0)
            response = clientes_routes._apply_clientes_fixed_cost_headers(response)

        self.assertNotIn("X-Clientes-FixedCost-Items", response.headers)
        self.assertNotIn("X-Clientes-FixedCost-Top", response.headers)

    def test_inventario_assets_shell_total_esperado(self):
        assets = clientes_routes._clientes_shell_assets_inventory()
        self.assertEqual(int(assets.get("external_count") or 0), 8)
        self.assertEqual(int(assets.get("local_count") or 0), 7)
        self.assertEqual(int(assets.get("total_count") or 0), 15)
        self.assertIn("clientes/js/clientes.js", set(assets.get("local_js") or []))
        self.assertIn("clientes/css/clientes.css", set(assets.get("local_css") or []))


if __name__ == "__main__":
    unittest.main()
