# -*- coding: utf-8 -*-

import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _ListQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _TaskQueryStub(_ListQueryStub):
    def get_or_404(self, _id):
        for row in self._rows:
            if int(getattr(row, "id", 0) or 0) == int(_id):
                return row
        raise AssertionError("Tarea no encontrada en stub")


class AdminTareasUnificadasTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def test_tareas_hoy_unifica_seguimientos_y_tareas(self):
        cliente = SimpleNamespace(id=7, nombre_completo="Cliente Unificado", codigo="CL-007")
        solicitud = SimpleNamespace(
            id=10,
            cliente_id=7,
            cliente=cliente,
            codigo_solicitud="SOL-U-10",
            fecha_seguimiento_manual=date(2026, 4, 7),
            estado="activa",
        )
        tarea = SimpleNamespace(
            id=20,
            cliente_id=7,
            cliente=cliente,
            titulo="Llamar para validar horario",
            fecha_vencimiento=date(2026, 4, 7),
            fecha_creacion=datetime(2026, 4, 6, 10, 0, 0),
            estado="pendiente",
            prioridad="media",
        )

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _ListQueryStub([solicitud])), \
                 patch.object(admin_routes.TareaCliente, "query", _TaskQueryStub([tarea])), \
                 patch("admin.routes.rd_today", return_value=date(2026, 4, 7)):
                resp = self.client.get("/admin/tareas/hoy", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Trabajo del día", html)
        self.assertIn("Control operativo diario", html)
        self.assertIn("Espera de pago", html)
        self.assertIn("Seguimientos vencidos", html)
        self.assertIn("Sin responsable", html)
        self.assertIn('triage=sin_responsable', html)
        self.assertIn("Seguimiento solicitud SOL-U-10", html)
        self.assertIn("Llamar para validar horario", html)
        self.assertIn("Tarea", html)
        self.assertIn("Seguimiento", html)

    def test_completar_tarea_cliente_marca_completada(self):
        tarea = SimpleNamespace(
            id=20,
            cliente_id=7,
            estado="pendiente",
            completada_at=None,
        )
        with flask_app.app_context():
            with patch.object(admin_routes.TareaCliente, "query", _TaskQueryStub([tarea])), \
                 patch("admin.routes.db.session.commit") as commit_mock, \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 4, 7, 11, 0, 0)):
                resp = self.client.post(
                    "/admin/tareas/20/completar",
                    data={"next": "/admin/tareas/hoy"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(tarea.estado, "completada")
        self.assertEqual(tarea.completada_at, datetime(2026, 4, 7, 11, 0, 0))
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_reprogramar_tarea_cliente_actualiza_fecha(self):
        tarea = SimpleNamespace(
            id=20,
            cliente_id=7,
            estado="pendiente",
            fecha_vencimiento=date(2026, 4, 7),
            completada_at=None,
        )
        with flask_app.app_context():
            with patch.object(admin_routes.TareaCliente, "query", _TaskQueryStub([tarea])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/tareas/20/reprogramar",
                    data={"next": "/admin/tareas/hoy", "fecha_vencimiento": "2026-04-10"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(tarea.fecha_vencimiento, date(2026, 4, 10))
        self.assertEqual(tarea.estado, "pendiente")
        self.assertGreaterEqual(commit_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
