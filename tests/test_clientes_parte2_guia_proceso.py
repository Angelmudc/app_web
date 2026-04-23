# -*- coding: utf-8 -*-

import os
import re
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


class _CountShouldNotRunQuery:
    def filter_by(self, **kwargs):
        return self

    def count(self):
        raise AssertionError("count() no debe ejecutarse cuando total_solicitudes ya fue calculado")


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

    @staticmethod
    def _extract_kpi_value(html: str, label: str) -> int:
        pattern = rf"{re.escape(label)}</div>\s*<div class=\"dashboard-kpi-value\">(\d+)</div>"
        m = re.search(pattern, html)
        if not m:
            return -1
        return int(m.group(1))

    def test_1_sin_solicitudes_muestra_banner_inicio(self):
        html = self._render_dashboard(rows=[])
        self.assertIn("Lo más importante ahora", html)
        self.assertIn("Etapa general: Sin solicitudes", html)
        self.assertIn("Crear solicitud", html)
        self.assertEqual(self._extract_kpi_value(html, "Solicitudes activas"), 0)
        self.assertEqual(self._extract_kpi_value(html, "Pendientes de atención"), 0)
        self.assertEqual(self._extract_kpi_value(html, "En espera de pago"), 0)
        self.assertEqual(self._extract_kpi_value(html, "Cerradas"), 0)
        self.assertIn('data-guide-key="sin_solicitudes"', html)

    def test_2_proceso_o_activa_muestra_sugerencia_seguimiento(self):
        rows = [_SolicitudRow(10, 7, "proceso")]
        html = self._render_dashboard(rows=rows)
        self.assertIn("Lo más importante ahora", html)
        self.assertIn("Etapa general: En proceso", html)
        self.assertIn("Pronto recibirás candidatas según el perfil solicitado.", html)
        self.assertIn("Ver seguimiento", html)
        self.assertIn('data-guide-key="en_proceso"', html)
        self.assertIn("Cómo funciona", html)
        self.assertIn("Abrir chat", html)
        self.assertIn("Ver mis solicitudes", html)

        rows_activa = [_SolicitudRow(11, 7, "activa")]
        html_activa = self._render_dashboard(rows=rows_activa)
        self.assertIn("Etapa general: Activa", html_activa)
        self.assertIn("Ver seguimiento", html_activa)
        self.assertIn('data-guide-key="en_proceso"', html_activa)

    def test_3_espera_pago_muestra_sugerencia_pago(self):
        rows = [_SolicitudRow(20, 7, "espera_pago")]
        html = self._render_dashboard(rows=rows)
        self.assertIn("Lo más importante ahora", html)
        self.assertIn("Etapa general: En espera de pago", html)
        self.assertIn("Siguiente paso sugerido: completa el pago inicial para continuar.", html)
        self.assertIn('data-guide-key="espera_pago"', html)
        self.assertIn("espera de pago", html.lower())

    def test_3b_resumen_ejecutivo_cuenta_estados_reales(self):
        rows = [
            _SolicitudRow(101, 7, "proceso", datetime(2026, 4, 4, 10, 0, 0)),
            _SolicitudRow(102, 7, "activa", datetime(2026, 4, 3, 10, 0, 0)),
            _SolicitudRow(103, 7, "reemplazo", datetime(2026, 4, 2, 10, 0, 0)),
            _SolicitudRow(104, 7, "espera_pago", datetime(2026, 4, 5, 10, 0, 0)),
            _SolicitudRow(105, 7, "pagada", datetime(2026, 4, 1, 10, 0, 0)),
            _SolicitudRow(106, 7, "cancelada", datetime(2026, 3, 30, 10, 0, 0)),
        ]
        html = self._render_dashboard(rows=rows)
        self.assertEqual(self._extract_kpi_value(html, "Solicitudes activas"), 3)
        self.assertEqual(self._extract_kpi_value(html, "Pendientes de atención"), 3)
        self.assertEqual(self._extract_kpi_value(html, "En espera de pago"), 1)
        self.assertEqual(self._extract_kpi_value(html, "Cerradas"), 2)
        self.assertIn("Etapa general: En espera de pago", html)

    def test_3c_dashboard_pasa_total_solicitudes_calculado_a_guia(self):
        rows = [
            _SolicitudRow(201, 7, "proceso", datetime(2026, 4, 7, 10, 0, 0)),
            _SolicitudRow(202, 7, "espera_pago", datetime(2026, 4, 6, 10, 0, 0)),
            _SolicitudRow(203, 7, "pagada", datetime(2026, 4, 5, 10, 0, 0)),
        ]
        captured = {}

        def _fake_guia(cliente_id, recientes=None, total_solicitudes=None):
            captured["cliente_id"] = cliente_id
            captured["total_solicitudes"] = total_solicitudes
            captured["recientes_len"] = len(recientes or [])
            return {"stage_key": "general"}

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes.Solicitud, "query", _SolicitudQuery(rows)), \
                 patch("clientes.routes.db.session.query", return_value=_EstadoAggQuery(rows)), \
                 patch.object(clientes_routes, "_build_cliente_guia_inteligente", side_effect=_fake_guia):
                with flask_app.test_request_context("/clientes/dashboard", method="GET"):
                    self.dashboard_target()

        self.assertEqual(captured.get("cliente_id"), 7)
        self.assertEqual(captured.get("total_solicitudes"), 3)
        self.assertEqual(captured.get("recientes_len"), 3)

    def test_3d_guia_inteligente_no_recuenta_si_total_ya_existe(self):
        with flask_app.app_context():
            with patch.object(clientes_routes.Solicitud, "query", _CountShouldNotRunQuery()):
                with flask_app.test_request_context("/clientes/dashboard", method="GET"):
                    guia = clientes_routes._build_cliente_guia_inteligente(
                        cliente_id=7,
                        recientes=[],
                        total_solicitudes=0,
                    )
        self.assertEqual(guia.get("stage_key"), "sin_solicitudes")

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
