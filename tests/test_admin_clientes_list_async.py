# -*- coding: utf-8 -*-

import os
import unittest
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect, request
from werkzeug.exceptions import NotFound
from sqlalchemy.exc import SQLAlchemyError

from app import app as flask_app
import admin.routes as admin_routes


class _ClienteQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def _clone(self):
        return _ClienteQueryStub(self._rows)

    def filter(self, *_args, **_kwargs):
        return self._clone()

    def order_by(self, *_args, **_kwargs):
        return self._clone()

    def _filtered_rows(self):
        rows = list(self._rows)
        q = (request.args.get("q") or "").strip().lower()
        if not q:
            return rows

        def _matches(row):
            row_id = int(getattr(row, "id", 0) or 0)
            codigo = (getattr(row, "codigo", "") or "").strip().lower()
            nombre = (getattr(row, "nombre_completo", "") or "").strip().lower()
            email = (getattr(row, "email", "") or "").strip().lower()
            telefono = (getattr(row, "telefono", "") or "").strip().lower()

            if q.isdigit() and row_id == int(q):
                return True
            if codigo == q:
                return True
            if len(q) >= 2 and q in codigo:
                return True
            if len(q) >= 2 and q in nombre:
                return True
            if len(q) >= 2 and q in telefono:
                return True
            if ("@" in q or "." in q or "gmail" in q) and q in email:
                return True
            if len(q) >= 2 and q in email:
                return True
            return False

        return [r for r in rows if _matches(r)]

    def all(self):
        return self._filtered_rows()

    def get_or_404(self, row_id):
        rid = int(row_id or 0)
        for row in self._rows:
            if int(getattr(row, "id", 0) or 0) == rid:
                return row
        raise NotFound()


def _cliente_stub(cid: int, codigo: str):
    return SimpleNamespace(
        id=cid,
        codigo=codigo,
        nombre_completo=f"Cliente {cid}",
        email=f"cliente{cid}@mail.com",
        telefono=f"809-555-{cid:04d}",
        total_solicitudes=cid % 3,
        fecha_registro=datetime(2026, 3, 1, 10, 0, 0),
    )


class AdminClientesListAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_busqueda_async_devuelve_parcial(self):
        rows = [
            _cliente_stub(1, "CL-ALFA"),
            _cliente_stub(2, "CL-BETA"),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub(rows)):
                resp = self.client.get(
                    "/admin/clientes?q=ALFA&page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#clientesAsyncRegion")
        self.assertIn("CL-ALFA", data["replace_html"])
        self.assertNotIn("CL-BETA", data["replace_html"])

    def test_paginacion_async_devuelve_pagina_sin_recarga(self):
        rows = [_cliente_stub(i, f"CL-{i:03d}") for i in range(1, 26)]
        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub(rows)):
                resp = self.client.get(
                    "/admin/clientes?page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["page"], 2)
        self.assertIn("CL-011", data["replace_html"])
        self.assertNotIn("CL-001", data["replace_html"])

    def test_accion_por_fila_async_eliminar_cliente(self):
        cliente = _cliente_stub(10, "CL-010")
        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([cliente])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes._collect_cliente_delete_plan", return_value={
                     "blocked_issues": [],
                     "warnings": [],
                     "summary": {},
                     "solicitud_ids": [],
                 }), \
                 patch("admin.routes._delete_cliente_tree", return_value={"cliente": 1}), \
                 patch("admin.routes.db.session.begin_nested", return_value=nullcontext()), \
                 patch("admin.routes.db.session.flush"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/10/eliminar",
                    data={"next": "/admin/clientes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#clientesAsyncRegion")
        self.assertIn("/admin/clientes?page=1", data["redirect_url"])
        commit_mock.assert_called_once()

    def test_fallback_clasico_se_mantiene_en_eliminar(self):
        cliente = _cliente_stub(10, "CL-010")
        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([cliente])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes._collect_cliente_delete_plan", return_value={
                     "blocked_issues": [],
                     "warnings": [],
                     "summary": {},
                     "solicitud_ids": [],
                 }), \
                 patch("admin.routes._delete_cliente_tree", return_value={"cliente": 1}), \
                 patch("admin.routes.db.session.begin_nested", return_value=nullcontext()), \
                 patch("admin.routes.db.session.flush"), \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/clientes/10/eliminar",
                    data={"next": "/admin/clientes?page=2"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes?page=2", resp.location)

    def test_manejo_errores_async_conflicto_rate_limit_y_server_error(self):
        cliente = _cliente_stub(10, "CL-010")

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([cliente])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes._collect_cliente_delete_plan", return_value={
                     "blocked_issues": ["Dependencias cruzadas"],
                     "warnings": [],
                     "summary": {},
                     "solicitud_ids": [],
                 }):
                resp_conflict = self.client.post(
                    "/admin/clientes/10/eliminar",
                    data={"next": "/admin/clientes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp_conflict.status_code, 409)
        conflict_data = resp_conflict.get_json()
        self.assertFalse(conflict_data["success"])
        self.assertEqual(conflict_data["error_code"], "conflict")

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([cliente])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=redirect("/admin/clientes")):
                resp_rate = self.client.post(
                    "/admin/clientes/10/eliminar",
                    data={"next": "/admin/clientes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp_rate.status_code, 429)
        rate_data = resp_rate.get_json()
        self.assertFalse(rate_data["success"])
        self.assertEqual(rate_data["error_code"], "rate_limit")

        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([cliente])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes._collect_cliente_delete_plan", return_value={
                     "blocked_issues": [],
                     "warnings": [],
                     "summary": {},
                     "solicitud_ids": [],
                 }), \
                 patch("admin.routes._delete_cliente_tree", side_effect=SQLAlchemyError("fail")), \
                 patch("admin.routes.db.session.begin_nested", return_value=nullcontext()), \
                 patch("admin.routes.db.session.rollback"):
                resp_server = self.client.post(
                    "/admin/clientes/10/eliminar",
                    data={"next": "/admin/clientes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp_server.status_code, 500)
        server_data = resp_server.get_json()
        self.assertFalse(server_data["success"])
        self.assertEqual(server_data["error_code"], "server_error")


if __name__ == "__main__":
    unittest.main()
