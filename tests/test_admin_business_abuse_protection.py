# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudStub:
    def __init__(self, *, estado: str = "proceso"):
        self.id = 10
        self.cliente_id = 7
        self.codigo_solicitud = "SOL-010"
        self.estado = estado
        self.fecha_cancelacion = None
        self.motivo_cancelacion = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.fecha_ultima_actividad = datetime.utcnow()
        self.fecha_ultima_modificacion = datetime.utcnow()


class AdminBusinessAbuseGuardTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def test_admin_accion_legitima_activar_permitida(self):
        solicitud = _SolicitudStub(estado="proceso")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.enforce_business_limit", return_value=(False, 1)), \
                 patch("admin.routes.enforce_min_human_interval", return_value=(False, 3)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/activar", follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(solicitud.estado, "activa")
        commit_mock.assert_called_once()

    def test_admin_repeticion_rapida_bloqueada_en_activar(self):
        solicitud = _SolicitudStub(estado="proceso")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.enforce_business_limit", return_value=(True, 99)), \
                 patch("admin.routes._audit_log") as audit_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/activar", follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(solicitud.estado, "proceso")
        commit_mock.assert_not_called()
        blocked_logs = [c.kwargs for c in audit_mock.call_args_list if c.kwargs.get("action_type") == "BUSINESS_FLOW_BLOCKED"]
        self.assertTrue(blocked_logs)

    def test_admin_noop_repetido_bloqueado_en_activar(self):
        solicitud = _SolicitudStub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.enforce_min_human_interval", return_value=(False, 3)), \
                 patch("admin.routes.enforce_business_limit", side_effect=[(False, 1), (True, 2)]), \
                 patch("admin.routes._audit_log") as audit_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/activar", follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_not_called()
        blocked_logs = [c.kwargs for c in audit_mock.call_args_list if c.kwargs.get("error") == "admin_repeated_noop_blocked"]
        self.assertTrue(blocked_logs)

    def test_admin_flujo_invalido_bloqueado_en_quitar_espera_pago(self):
        solicitud = _SolicitudStub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.enforce_min_human_interval", return_value=(False, 3)), \
                 patch("admin.routes.enforce_business_limit", side_effect=[(False, 1), (False, 1)]), \
                 patch("admin.routes._audit_log") as audit_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/quitar_espera_pago", follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_not_called()
        invalid_logs = [c.kwargs for c in audit_mock.call_args_list if c.kwargs.get("error") == "solicitud_not_in_espera_pago"]
        self.assertTrue(invalid_logs)

    def test_admin_evento_auditado_en_bloqueo_cancelar_directo(self):
        admin_client = flask_app.test_client()
        login = admin_client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))
        solicitud = _SolicitudStub(estado="proceso")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.enforce_business_limit", return_value=(True, 88)), \
                 patch("admin.routes._audit_log") as audit_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = admin_client.post("/admin/solicitudes/10/cancelar_directo", data={"motivo": "test"}, follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_not_called()
        blocked_logs = [c.kwargs for c in audit_mock.call_args_list if c.kwargs.get("action_type") == "BUSINESS_FLOW_BLOCKED"]
        self.assertTrue(blocked_logs)


if __name__ == "__main__":
    unittest.main()
