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


class ClientesCentroAyudaTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        self.fake_user = SimpleNamespace(id=7, is_authenticated=True)
        self.target = _unwrap_cliente_view(clientes_routes.ayuda)

    def test_centro_ayuda_renderiza_secciones_clave(self):
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user):
                with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                    html = self.target()

        self.assertIn("Centro de ayuda", html)
        self.assertIn("Proceso", html)
        self.assertIn("Pagos", html)
        self.assertIn("Entrevistas", html)
        self.assertIn("Candidatas", html)
        self.assertIn("Reemplazos", html)
        self.assertIn("Contacto y chat", html)
        self.assertIn("centroAyudaAccordion", html)


if __name__ == "__main__":
    unittest.main()
