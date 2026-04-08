# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudStub:
    def __init__(self, estado="activa"):
        self.id = 10
        self.cliente_id = 7
        self.estado = estado
        self.codigo_solicitud = "SOL-010"
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.fecha_ultima_actividad = datetime.utcnow()
        self.fecha_ultima_modificacion = datetime.utcnow()


class EsperaPagoFromListTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        self._presence_patcher = patch("admin.routes._touch_staff_presence", return_value=None)
        self._presence_patcher.start()
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def tearDown(self):
        self._presence_patcher.stop()

    def test_post_poner_espera_pago_desde_listado(self):
        solicitud = _SolicitudStub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes?estado=activa&page=2"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes?estado=activa&page=2", resp.location)
        self.assertEqual(solicitud.estado, "espera_pago")
        self.assertEqual(solicitud.estado_previo_espera_pago, "activa")
        self.assertIsNotNone(solicitud.fecha_cambio_espera_pago)
        self.assertTrue(bool(solicitud.usuario_cambio_espera_pago))
        commit_mock.assert_called_once()

    def test_post_quitar_espera_pago_desde_listado_vuelve_estado_previo(self):
        solicitud = _SolicitudStub(estado="espera_pago")
        solicitud.estado_previo_espera_pago = "proceso"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/quitar_espera_pago",
                    data={"next": "/admin/solicitudes?estado=espera_pago&page=1"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes?estado=espera_pago&page=1", resp.location)
        self.assertEqual(solicitud.estado, "proceso")
        self.assertIsNotNone(solicitud.fecha_cambio_espera_pago)
        self.assertTrue(bool(solicitud.usuario_cambio_espera_pago))
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
