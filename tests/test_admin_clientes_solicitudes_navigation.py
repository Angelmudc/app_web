# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _ClienteQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _SolicitudQueryStub:
    def __init__(self, rows, *, estado_filter=None, offset_n=0, limit_n=None):
        self._rows = list(rows)
        self._estado_filter = estado_filter
        self._offset_n = offset_n
        self._limit_n = limit_n

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            estado_filter=updates.get("estado_filter", self._estado_filter),
            offset_n=updates.get("offset_n", self._offset_n),
            limit_n=updates.get("limit_n", self._limit_n),
        )

    def _filtered_rows(self):
        rows = list(self._rows)
        if self._estado_filter:
            rows = [r for r in rows if (getattr(r, "estado", "") or "") == self._estado_filter]
        return rows

    def options(self, *_args, **_kwargs):
        return self._clone()

    def filter(self, *_args, **_kwargs):
        return self._clone()

    def filter_by(self, **kwargs):
        return self._clone(estado_filter=kwargs.get("estado"))

    def outerjoin(self, *_args, **_kwargs):
        return self._clone()

    def order_by(self, *_args, **_kwargs):
        return self._clone()

    def offset(self, value):
        try:
            n = int(value)
        except Exception:
            n = 0
        return self._clone(offset_n=max(0, n))

    def limit(self, value):
        try:
            n = int(value)
        except Exception:
            n = None
        return self._clone(limit_n=n if (n is None or n >= 0) else None)

    def count(self):
        return len(self._filtered_rows())

    def all(self):
        rows = self._filtered_rows()
        rows = rows[self._offset_n:]
        if self._limit_n is not None:
            rows = rows[:self._limit_n]
        return rows


class AdminClientesSolicitudesNavigationTest(unittest.TestCase):
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

    def test_boton_solicitudes_desde_clientes_apunta_a_copiar_y_panel(self):
        with flask_app.app_context():
            with patch.object(admin_routes.Cliente, "query", _ClienteQueryStub([])):
                resp = self.client.get("/admin/clientes", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('href="/admin/solicitudes/copiar"', html)
        self.assertIn('href="/admin/solicitudes"', html)
        self.assertIn('href="/admin/solicitudes/proceso/acciones"', html)
        self.assertIn('href="/admin/solicitudes/prioridad"', html)
        self.assertIn('href="/admin/clientes/resumen_diario"', html)
        self.assertIn('href="/admin/solicitudes/nueva-publica/link"', html)

    def test_panel_solicitudes_carga_sin_error_generico(self):
        solicitud = SimpleNamespace(
            id=10,
            codigo_solicitud="SOL-010",
            cliente_id=7,
            cliente=SimpleNamespace(nombre_completo="Cliente Demo"),
            estado="proceso",
            candidata=None,
            candidata_id=None,
            fecha_solicitud=datetime(2026, 3, 1, 10, 0, 0),
            ciudad_sector="Santiago",
            reemplazos=[],
            last_copiado_at=None,
            rutas_cercanas="Monumental",
        )

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self.client.get("/admin/solicitudes", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Panel de Solicitudes", html)
        self.assertIn("SOL-010", html)
        self.assertIn('href="/admin/solicitudes/copiar"', html)
        self.assertIn('action="/admin/chat/open"', html)
        self.assertIn('name="solicitud_id" value="10"', html)
        self.assertIn('href="/admin/clientes/7"', html)
        self.assertNotIn("Ocurrió un problema", html)


if __name__ == "__main__":
    unittest.main()
