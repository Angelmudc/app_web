# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import app as flask_app
import admin.routes as admin_routes


class _ClienteQueryStub:
    def __init__(self, cliente, *, duplicate_codigo=False, duplicate_email=False, duplicate_username=False):
        self._cliente = cliente
        self._duplicate_codigo = bool(duplicate_codigo)
        self._duplicate_email = bool(duplicate_email)
        self._duplicate_username = bool(duplicate_username)
        self._last_filter = ""

    def get_or_404(self, _cliente_id):
        return self._cliente

    def filter(self, *args, **_kwargs):
        self._last_filter = " ".join(str(a) for a in args)
        return self

    def first(self):
        key = (self._last_filter or "").lower()
        if "clientes.codigo" in key:
            return SimpleNamespace(id=999) if self._duplicate_codigo else None
        if "clientes.email" in key:
            return SimpleNamespace(id=999) if self._duplicate_email else None
        if "clientes.username" in key:
            return SimpleNamespace(id=999) if self._duplicate_username else None
        return None


def _cliente_stub(cliente_id=7):
    now = datetime(2026, 3, 1, 12, 0, 0)
    return SimpleNamespace(
        id=cliente_id,
        codigo="CLI-007",
        nombre_completo="Cliente Original",
        email="cliente@example.com",
        telefono="8091234567",
        username="cliente.original",
        password_hash="DISABLED_RESET_REQUIRED",
        ciudad="Santiago",
        sector="Centro",
        notas_admin="",
        fecha_ultima_actividad=now,
        updated_at=now,
    )


class AdminEditarClienteAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def _raw_view(self):
        view = admin_routes.editar_cliente
        for _ in range(3):
            view = view.__wrapped__
        return view

    def _invoke(self, *, method="POST", data=None, headers=None):
        with flask_app.test_request_context(
            "/admin/clientes/7/editar",
            method=method,
            data=(data or {}),
            headers=(headers or {}),
        ):
            rv = self._raw_view()(7)
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

    def test_guardado_async_exitoso_sin_recarga_total(self):
        cliente = _cliente_stub()
        query = _ClienteQueryStub(cliente)

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", query), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={
                        "codigo": "CLI-007",
                        "nombre_completo": "Cliente Editado",
                        "email": "Nuevo@Example.com",
                        "telefono": "809-555-1234",
                        "username": "cliente.editado",
                        "password": "",
                        "password_confirm": "",
                        "ciudad": "Santiago",
                        "sector": "Norte",
                        "notas_admin": "Actualizado",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#editarClienteAsyncRegion")
        self.assertIn("Cliente actualizado correctamente.", data["replace_html"])
        self.assertEqual(cliente.email, "nuevo@example.com")
        self.assertEqual(cliente.telefono, "8095551234")
        self.assertEqual(cliente.username, "cliente.editado")
        commit_mock.assert_called_once()

    def test_validacion_async_muestra_errores_inline(self):
        cliente = _cliente_stub()
        query = _ClienteQueryStub(cliente)

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", query), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={
                        "codigo": "CLI-007",
                        "nombre_completo": "Cliente Editado",
                        "email": "correo-invalido",
                        "telefono": "8095551234",
                        "username": "cliente.editado",
                        "password": "",
                        "password_confirm": "",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertIn("Correo inválido.", data["replace_html"])
        self.assertIn("is-invalid", data["replace_html"])
        commit_mock.assert_not_called()

    def test_conflicto_unicidad_prevalidado_re_renderiza_parcial(self):
        cliente = _cliente_stub()
        query = _ClienteQueryStub(cliente, duplicate_email=True)

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", query), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={
                        "codigo": "CLI-007",
                        "nombre_completo": "Cliente Editado",
                        "email": "duplicado@example.com",
                        "telefono": "8095551234",
                        "username": "",
                        "password": "",
                        "password_confirm": "",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")
        self.assertIn("Este email ya está registrado.", data["replace_html"])
        commit_mock.assert_not_called()

    def test_integrity_error_async_devuelve_409_y_asocia_username(self):
        cliente = _cliente_stub()
        query = _ClienteQueryStub(cliente)

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", query), \
                 patch(
                     "admin.routes.db.session.commit",
                     side_effect=IntegrityError(
                         "stmt",
                         "params",
                         Exception("duplicate key value violates unique constraint clientes_username_key"),
                     ),
                 ):
                resp = self._invoke(
                    data={
                        "codigo": "CLI-007",
                        "nombre_completo": "Cliente Editado",
                        "email": "cliente@example.com",
                        "telefono": "8095551234",
                        "username": "otro.usuario",
                        "password": "",
                        "password_confirm": "",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")
        self.assertIn("Este usuario ya está registrado.", " ".join(data.get("errors") or []))

    def test_fallback_clasico_post_valido_mantiene_redirect(self):
        cliente = _cliente_stub()
        query = _ClienteQueryStub(cliente)

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", query), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={
                        "codigo": "CLI-007",
                        "nombre_completo": "Cliente Editado",
                        "email": "cliente@example.com",
                        "telefono": "8095551234",
                        "username": "",
                        "password": "",
                        "password_confirm": "",
                    },
                    headers={},
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
