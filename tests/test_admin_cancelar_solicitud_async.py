# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows, *, by_id=None, by_cliente=None):
        self._rows = list(rows)
        self._by_id = by_id
        self._by_cliente = by_cliente

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            by_id=updates.get("by_id", self._by_id),
            by_cliente=updates.get("by_cliente", self._by_cliente),
        )

    def filter_by(self, **kwargs):
        return self._clone(
            by_id=kwargs.get("id", self._by_id),
            by_cliente=kwargs.get("cliente_id", self._by_cliente),
        )

    def _filtered(self):
        rows = list(self._rows)
        if self._by_id is not None:
            rows = [r for r in rows if int(getattr(r, "id", 0) or 0) == int(self._by_id)]
        if self._by_cliente is not None:
            rows = [r for r in rows if int(getattr(r, "cliente_id", 0) or 0) == int(self._by_cliente)]
        return rows

    def first_or_404(self):
        rows = self._filtered()
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada")


def _solicitud_stub(estado="activa"):
    return SimpleNamespace(
        id=10,
        cliente_id=7,
        codigo_solicitud="SOL-010",
        estado=estado,
        ciudad_sector="Santiago",
        modalidad_trabajo="Con dormida",
        fecha_cancelacion=None,
        motivo_cancelacion=None,
        fecha_ultima_actividad=datetime.utcnow(),
        fecha_ultima_modificacion=datetime.utcnow(),
    )


class AdminCancelarSolicitudAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_cancelar_solicitud_async_exito_devuelve_redirect_url(self):
        solicitud = _solicitud_stub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["redirect_url"], "/admin/clientes/7#sol-10")
        self.assertEqual(solicitud.estado, "cancelada")
        self.assertEqual(solicitud.motivo_cancelacion, "Cliente detuvo el proceso")
        commit_mock.assert_called_once()

    def test_cancelar_solicitud_async_error_validacion_inline(self):
        solicitud = _solicitud_stub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "abc",
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertEqual(data["update_target"], "#cancelarSolicitudAsyncRegion")
        self.assertIn("is-invalid", data.get("replace_html", ""))
        self.assertIn("Indica un motivo válido.", data.get("replace_html", ""))
        self.assertEqual(solicitud.estado, "activa")
        commit_mock.assert_not_called()

    def test_cancelar_solicitud_async_error_estado_inline(self):
        solicitud = _solicitud_stub(estado="pagada")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente cambió de plan",
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_state")
        self.assertEqual(data["update_target"], "#cancelarSolicitudAsyncRegion")
        self.assertIn("Esta solicitud no se puede cancelar en su estado actual.", data.get("replace_html", ""))
        self.assertEqual(solicitud.estado, "pagada")
        commit_mock.assert_not_called()

    def test_cancelar_solicitud_fallback_clasico_intacto(self):
        solicitud = _solicitud_stub(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/clientes/7#sol-10",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7#sol-10", resp.location)
        self.assertEqual(solicitud.estado, "cancelada")
        self.assertEqual(solicitud.motivo_cancelacion, "Cliente detuvo el proceso")
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
