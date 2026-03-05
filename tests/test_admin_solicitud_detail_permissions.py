# -*- coding: utf-8 -*-

import unittest
import os
from datetime import datetime
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _FakeSolicitud:
    id = 1
    cliente_id = 1
    codigo_solicitud = "SOL-001"
    estado = "activa"
    fecha_solicitud = datetime.utcnow()
    candidata = None
    reemplazos = []


class _FakeQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _FakeSolicitud()


class SolicitudDetailPermissionTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()

    def test_secretaria_can_access_admin_solicitud_detail(self):
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

        # Login como secretaria (fallback legacy).
        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

        fake_query = _FakeQuery()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", fake_query), \
                 patch("admin.routes.render_template", return_value="OK"):
                resp = self.client.get("/admin/clientes/1/solicitudes/1")

        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
