# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, solicitud):
        self._solicitud = solicitud

    def filter_by(self, **_kwargs):
        return self

    def first_or_404(self):
        return self._solicitud


def _solicitud_stub(*, sol_id=101, cliente_id=7, estado="proceso"):
    now = datetime(2026, 3, 1, 12, 0, 0)
    return SimpleNamespace(
        id=sol_id,
        cliente_id=cliente_id,
        codigo_solicitud=f"SOL-{sol_id}",
        tipo_plan="Básico",
        abono="1000.00",
        estado=estado,
        fecha_cancelacion=None,
        motivo_cancelacion=None,
        fecha_inicio_seguimiento=now,
        fecha_ultima_actividad=now,
        fecha_ultima_modificacion=now,
    )


class AdminGestionarPlanAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def _raw_view(self):
        view = admin_routes.gestionar_plan
        for _ in range(3):
            view = view.__wrapped__
        return view

    def _invoke(self, *, method="POST", data=None, headers=None, path="/admin/clientes/7/solicitudes/101/plan"):
        with flask_app.test_request_context(
            path,
            method=method,
            data=(data or {}),
            headers=(headers or {}),
        ):
            rv = self._raw_view()(7, 101)
            if isinstance(rv, tuple):
                resp = rv[0]
                resp.status_code = int(rv[1])
                return resp
            return rv

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_guardado_async_exitoso_reemplaza_region_local(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "Premium", "abono": "1,500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#gestionarPlanAsyncRegion")
        self.assertEqual(data["redirect_url"], "/admin/clientes/7")
        self.assertIsNone(data.get("replace_html"))
        commit_mock.assert_called_once()

    def test_validacion_async_re_renderiza_formulario_con_error_en_campo(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "VIP", "abono": ""},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertIn("Indica el abono.", data["replace_html"])
        self.assertIn("is-invalid", data["replace_html"])
        commit_mock.assert_not_called()

    def test_fallback_clasico_post_valido_mantiene_redirect(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "Premium", "abono": "1500"},
                    headers={},
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)
        commit_mock.assert_called_once()

    def test_fallback_clasico_post_valido_respeta_next_url(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "Premium", "abono": "1500"},
                    headers={},
                    path="/admin/clientes/7/solicitudes/101/plan?next=/admin/solicitudes/copiar?page=2",
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=2", resp.location)
        commit_mock.assert_called_once()

    def test_error_async_controlado_devuelve_500_json_limpio(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit", side_effect=SQLAlchemyError("db_down")):
                resp = self._invoke(
                    data={"tipo_plan": "Premium", "abono": "1500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "server_error")
        self.assertIn("Error de base de datos", data["message"])

    def test_async_conflicto_devuelve_409(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch(
                     "admin.routes.db.session.commit",
                     side_effect=IntegrityError("stmt", "params", Exception("dup")),
                 ):
                resp = self._invoke(
                    data={"tipo_plan": "Premium", "abono": "1500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")


if __name__ == "__main__":
    unittest.main()
