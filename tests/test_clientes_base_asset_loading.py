# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(2):  # login_required + cliente_required
        out = out.__wrapped__
    return out


class ClientesBaseAssetLoadingTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        self.ayuda_target = _unwrap_cliente_view(clientes_routes.ayuda)

    def test_login_no_carga_assets_shell_ni_vendor_no_usado(self):
        with flask_app.test_request_context("/clientes/login", method="GET"):
            html = clientes_routes.login()

        self.assertIn("clientes/js/clientes.js", html)
        self.assertNotIn("css/client_notifications.css", html)
        self.assertNotIn("js/client_notifications.js", html)
        self.assertNotIn("js/core/client_live_invalidation.js", html)
        self.assertNotIn("code.jquery.com/jquery", html)
        self.assertNotIn("select2@4.1.0", html)

    def test_vista_shell_carga_assets_shell_y_omite_jquery_select2_global(self):
        fake_user = SimpleNamespace(id=7, is_authenticated=True, nombre_completo="Cliente Demo", email="demo@example.com")
        with flask_app.app_context():
            with patch("flask_login.utils._get_user", return_value=fake_user):
                with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                    html = self.ayuda_target()

        self.assertIn("css/client_notifications.css", html)
        self.assertIn("js/client_notifications.js", html)
        self.assertIn("js/core/client_live_invalidation.js", html)
        self.assertNotIn("code.jquery.com/jquery", html)
        self.assertNotIn("select2@4.1.0", html)


if __name__ == "__main__":
    unittest.main()
