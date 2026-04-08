# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudStub:
    def __init__(self):
        self.id = 10
        self.cliente_id = 7
        self.estado = "reemplazo"
        self.candidata_id = 1
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None


class _ReemplazoStub:
    def __init__(self):
        self.id = 99
        self.solicitud_id = 10
        self.fecha_inicio_reemplazo = datetime.utcnow()
        self.fecha_fin_reemplazo = None
        self.candidata_new_id = None
        self.oportunidad_nueva = True
        self.estado_previo_solicitud = "activa"

    def cerrar_reemplazo(self, candidata_nueva_id=None):
        self.fecha_fin_reemplazo = datetime.utcnow()
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


class _CandidataStub:
    def __init__(self, fila=2, estado="lista_para_trabajar"):
        self.fila = fila
        self.estado = estado
        self.nombre_completo = "Nueva"
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None


class _SolicitudQuery:
    def __init__(self, sol):
        self.sol = sol

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.sol


class _ReemplazoQuery:
    def __init__(self, repl):
        self.repl = repl

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.repl


class _CandidataQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.cand


class ReemplazoFromListTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        self._presence_patcher = patch("admin.routes._touch_staff_presence", return_value=None)
        self._presence_patcher.start()

    def tearDown(self):
        self._presence_patcher.stop()

    def _login(self, user, pwd):
        resp = self.client.post("/admin/login", data={"usuario": user, "clave": pwd}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_cancelar_reemplazo_desde_listado_solo_admin(self):
        sol = _SolicitudStub()
        repl = _ReemplazoStub()

        self._login("Karla", "9989")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)):
                resp_forbidden = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/solicitudes?estado=reemplazo&page=1"},
                    follow_redirects=False,
                )
        self.assertEqual(resp_forbidden.status_code, 403)

        self.client = flask_app.test_client()
        self._login("Cruz", "8998")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp_ok = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/solicitudes?estado=reemplazo&page=1"},
                    follow_redirects=False,
                )
        self.assertIn(resp_ok.status_code, (302, 303))
        self.assertIn("/admin/solicitudes?estado=reemplazo&page=1", resp_ok.location)
        self.assertIsNotNone(repl.fecha_fin_reemplazo)
        self.assertEqual(sol.estado, "activa")
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_cancelar_reemplazo_async_exitoso_eleva_a_region_padre_y_refresca_resumen(self):
        sol = _SolicitudStub()
        sol.codigo_solicitud = "SOL-010"
        repl = _ReemplazoStub()

        self._login("Cruz", "8998")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={
                        "next": "/admin/solicitudes?estado=reemplazo&page=1",
                        "_async_target": "#solicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("update_target"), "#solicitudesAsyncRegion")
        self.assertIsNone(payload.get("replace_html"))
        update_targets = payload.get("update_targets") or []
        self.assertEqual(len(update_targets), 2)
        self.assertEqual(update_targets[0].get("target"), "#solicitudesAsyncRegion")
        self.assertTrue(update_targets[0].get("invalidate"))
        self.assertEqual(update_targets[1].get("target"), "#solicitudesSummaryAsyncRegion")
        self.assertTrue(update_targets[1].get("invalidate"))

    def test_culminar_reemplazo_desde_listado_asigna_candidata_valida(self):
        self._login("Karla", "9989")
        sol = _SolicitudStub()
        repl = _ReemplazoStub()
        cand = _CandidataStub(fila=2, estado="lista_para_trabajar")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(cand)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch(
                     "admin.routes._mark_candidata_estado",
                     side_effect=lambda c, estado, **_kwargs: setattr(c, "estado", estado),
                 ), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"candidata_new_id": "2", "next": "/admin/solicitudes?estado=reemplazo"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes?estado=reemplazo", resp.location)
        self.assertEqual(repl.candidata_new_id, 2)
        self.assertEqual(sol.candidata_id, 2)
        self.assertEqual(sol.estado, "activa")
        self.assertEqual(cand.estado, "trabajando")
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_culminar_reemplazo_bloquea_si_candidata_descalificada(self):
        self._login("Karla", "9989")
        sol = _SolicitudStub()
        repl = _ReemplazoStub()
        cand = _CandidataStub(fila=2, estado="descalificada")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(cand)), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=redirect("/blocked")), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"candidata_new_id": "2", "next": "/admin/solicitudes?estado=reemplazo"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/blocked", resp.location)
        self.assertEqual(sol.candidata_id, 1)
        self.assertIsNone(repl.candidata_new_id)
        self.assertIn(commit_mock.call_count, (0, 1))

    def test_region_async_muestra_cierre_rapido_y_cta_cierre_completo(self):
        self._login("Karla", "9989")
        sol = _SolicitudStub()
        sol.codigo_solicitud = "SOL-010"
        repl = _ReemplazoStub()
        sol.reemplazos = [repl]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)):
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={
                        "candidata_new_id": "",
                        "next": "/admin/solicitudes?estado=reemplazo&page=1",
                        "_async_target": "#solicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("update_target"), "#solicitudReemplazoActionsAsyncRegion-10")
        html = payload.get("replace_html") or ""
        self.assertIn("Cierre rápido", html)
        self.assertIn("Cierre completo", html)
        self.assertIn("/admin/solicitudes/10/reemplazos/99/finalizar", html)


if __name__ == "__main__":
    unittest.main()
