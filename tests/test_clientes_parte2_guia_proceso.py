# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class _SolicitudRow:
    def __init__(self, solicitud_id: int, cliente_id: int, estado: str, fecha_solicitud=None):
        self.id = solicitud_id
        self.cliente_id = cliente_id
        self.estado = estado
        self.fecha_solicitud = fecha_solicitud or datetime(2026, 4, 1, 10, 0, 0)
        self.codigo_solicitud = f"SOL-{solicitud_id:03d}"
        self.ciudad_sector = "Santiago"
        self.modalidad_trabajo = "salida diaria"
        self.last_copiado_at = None


class _SolicitudQuery:
    def __init__(self, rows, limit_n=None):
        self._rows = list(rows)
        self._limit_n = limit_n

    def _clone(self, rows=None, limit_n=None):
        return _SolicitudQuery(
            self._rows if rows is None else rows,
            self._limit_n if limit_n is None else limit_n,
        )

    def filter_by(self, **kwargs):
        rows = self._rows
        for key, value in kwargs.items():
            rows = [r for r in rows if getattr(r, key, None) == value]
        return self._clone(rows=rows, limit_n=None)

    def order_by(self, *args, **kwargs):
        rows = sorted(self._rows, key=lambda r: getattr(r, "fecha_solicitud", datetime.min), reverse=True)
        return self._clone(rows=rows)

    def limit(self, n):
        return self._clone(limit_n=int(n or 0))

    def all(self):
        if self._limit_n is None:
            return list(self._rows)
        return list(self._rows[: self._limit_n])

    def count(self):
        return len(self._rows)

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class _EstadoAggQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        counts = {}
        for row in self._rows:
            estado = (getattr(row, "estado", None) or "sin_definir")
            counts[estado] = counts.get(estado, 0) + 1
        return list(counts.items())


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(2):  # login_required + cliente_required
        out = out.__wrapped__
    return out


class ClientesParte2GuiaProcesoTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.fake_user = SimpleNamespace(
            id=7,
            is_authenticated=True,
            nombre="Cliente Demo",
            nombre_completo="Cliente Demo",
            username="cliente.demo",
            email="cliente@example.com",
        )
        self.dashboard_target = _unwrap_cliente_view(clientes_routes.dashboard)
        self.proceso_target = _unwrap_cliente_view(clientes_routes.proceso_contratacion)

    def _render_dashboard(self, rows):
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes.Solicitud, "query", _SolicitudQuery(rows)), \
                 patch("clientes.routes.db.session.query", return_value=_EstadoAggQuery(rows)):
                with flask_app.test_request_context("/clientes/dashboard", method="GET"):
                    html = self.dashboard_target()
        return html

    def test_1_sin_solicitudes_muestra_banner_inicio(self):
        html = self._render_dashboard(rows=[])
        self.assertIn("Para comenzar, crea tu primera solicitud.", html)
        self.assertIn("Crear solicitud", html)
        self.assertIn('data-guide-key="sin_solicitudes"', html)

    def test_2_proceso_o_activa_muestra_sugerencia_seguimiento(self):
        rows = [_SolicitudRow(10, 7, "proceso")]
        html = self._render_dashboard(rows=rows)
        self.assertIn("Tu solicitud está en proceso.", html)
        self.assertIn("Pronto recibirás candidatas según el perfil solicitado.", html)
        self.assertIn("Ver seguimiento", html)
        self.assertIn('data-guide-key="en_proceso"', html)

        rows_activa = [_SolicitudRow(11, 7, "activa")]
        html_activa = self._render_dashboard(rows=rows_activa)
        self.assertIn("Tu solicitud está en proceso.", html_activa)
        self.assertIn("Ver seguimiento", html_activa)
        self.assertIn('data-guide-key="en_proceso"', html_activa)

    def test_3_espera_pago_muestra_sugerencia_pago(self):
        rows = [_SolicitudRow(20, 7, "espera_pago")]
        html = self._render_dashboard(rows=rows)
        self.assertIn("Tu proceso está en etapa de pago.", html)
        self.assertIn("Siguiente paso sugerido: completa el pago inicial para continuar.", html)
        self.assertIn('data-guide-key="espera_pago"', html)

    def test_4_ocultar_banner_misma_etapa_permanece_oculto_por_misma_key(self):
        rows = [_SolicitudRow(30, 7, "proceso")]
        html = self._render_dashboard(rows=rows)
        self.assertIn('data-guide-key="en_proceso"', html)
        self.assertIn("clientes.smart_guide.dismissed.' + stage", html)
        self.assertIn("sessionStorage.getItem(key) === '1'", html)
        self.assertIn("sessionStorage.setItem(key, '1')", html)

    def test_5_si_cambia_etapa_banner_reaparece_por_key_distinta(self):
        html_proceso = self._render_dashboard(rows=[_SolicitudRow(40, 7, "proceso")])
        html_pago = self._render_dashboard(rows=[_SolicitudRow(41, 7, "espera_pago")])
        self.assertIn('data-guide-key="en_proceso"', html_proceso)
        self.assertIn('data-guide-key="espera_pago"', html_pago)
        self.assertNotEqual("en_proceso", "espera_pago")

    def test_6_ruta_clientes_proceso_responde_200_y_contenido_clave(self):
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user):
                with flask_app.test_request_context("/clientes/proceso", method="GET"):
                    body = self.proceso_target()
                    resp = flask_app.make_response(body)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Cómo funciona la contratación de una empleada doméstica", html)
        self.assertIn("Información y solicitud", html)
        self.assertIn("Nota importante", html)
        self.assertIn("⚠️ No pagar pasajes por adelantado.", html)

    def test_7_menu_cliente_contiene_acceso_a_nueva_ruta(self):
        base_path = os.path.join(flask_app.root_path, "templates", "clientes", "base.html")
        with open(base_path, "r", encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("url_for('clientes.proceso_contratacion')", html)
        self.assertIn("Cómo funciona", html)
        self.assertIn("Proceso de contratación", html)


if __name__ == "__main__":
    unittest.main()
