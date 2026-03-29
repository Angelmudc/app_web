# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummyCandidata:
    def __init__(self, fila=1, estado="inscrita"):
        self.fila = fila
        self.estado = estado
        self.nombre_completo = "Ana Perez"
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None
        self.nota_descalificacion = None


class _CandidataAdminQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.cand


class AdminStateButtonsTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

    def test_marcar_trabajando_bloquea_si_no_hay_asignacion_activa(self):
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/candidatas/1/marcar_trabajando",
                    data={"next": "/buscar?candidata_id=1"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(cand.estado, "lista_para_trabajar")
        commit_mock.assert_not_called()

    def test_marcar_lista_para_trabajar_bloquea_si_no_ready(self):
        cand = _DummyCandidata(fila=1, estado="inscrita")

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(False, ["Falta documento requerido: cedula2."])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/candidatas/1/marcar_lista_para_trabajar",
                    data={"next": "/buscar?candidata_id=1"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(cand.estado, "inscrita")
        commit_mock.assert_not_called()

    def test_marcar_lista_para_trabajar_ok_si_ready(self):
        cand = _DummyCandidata(fila=1, estado="inscrita")

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(True, [])), \
                 patch("admin.routes.log_candidata_action") as log_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/candidatas/1/marcar_lista_para_trabajar",
                    data={"next": "/buscar?candidata_id=1"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(cand.estado, "lista_para_trabajar")
        self.assertIsNotNone(cand.fecha_cambio_estado)
        self.assertTrue(bool(cand.usuario_cambio_estado))
        self.assertGreaterEqual(commit_mock.call_count, 1)
        mark_logs = [c.kwargs for c in log_mock.call_args_list if c.kwargs.get("action_type") == "CANDIDATA_MARK_LISTA"]
        self.assertEqual(len(mark_logs), 1)
        self.assertEqual(mark_logs[0].get("metadata", {}).get("reason"), "readiness_ok")
        self.assertEqual(mark_logs[0].get("metadata", {}).get("source"), "manual")
        self.assertEqual(mark_logs[0].get("metadata", {}).get("faltantes"), [])


if __name__ == "__main__":
    unittest.main()
