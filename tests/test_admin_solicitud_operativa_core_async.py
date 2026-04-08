# -*- coding: utf-8 -*-

import os
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import render_template

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows, *, by_id=None, by_cliente=None, estado_filter=None, offset_n=0, limit_n=None):
        self._rows = list(rows)
        self._by_id = by_id
        self._by_cliente = by_cliente
        self._estado_filter = estado_filter
        self._offset_n = offset_n
        self._limit_n = limit_n

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            by_id=updates.get("by_id", self._by_id),
            by_cliente=updates.get("by_cliente", self._by_cliente),
            estado_filter=updates.get("estado_filter", self._estado_filter),
            offset_n=updates.get("offset_n", self._offset_n),
            limit_n=updates.get("limit_n", self._limit_n),
        )

    def options(self, *_args, **_kwargs):
        return self._clone()

    def filter_by(self, **kwargs):
        return self._clone(
            by_id=kwargs.get("id", self._by_id),
            by_cliente=kwargs.get("cliente_id", self._by_cliente),
            estado_filter=kwargs.get("estado", self._estado_filter),
        )

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

    def get_or_404(self, _id):
        for row in self._rows:
            if int(getattr(row, "id", 0) or 0) == int(_id):
                return row
        raise AssertionError("Solicitud no encontrada")

    def _filtered(self):
        rows = list(self._rows)
        if self._by_id is not None:
            rows = [r for r in rows if int(getattr(r, "id", 0) or 0) == int(self._by_id)]
        if self._by_cliente is not None:
            rows = [r for r in rows if int(getattr(r, "cliente_id", 0) or 0) == int(self._by_cliente)]
        if self._estado_filter is not None:
            rows = [r for r in rows if (getattr(r, "estado", "") or "") == self._estado_filter]
        return rows

    def first_or_404(self):
        rows = self._filtered()
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada")

    def first(self):
        rows = self._filtered()
        return rows[0] if rows else None

    def count(self):
        return len(self._filtered())

    def all(self):
        rows = self._filtered()
        rows = rows[self._offset_n:]
        if self._limit_n is not None:
            rows = rows[:self._limit_n]
        return rows


class _CandidataQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        try:
            n = int(value)
        except Exception:
            n = len(self._rows)
        self._rows = self._rows[:max(0, n)]
        return self

    def all(self):
        return list(self._rows)

    def get(self, fila):
        for cand in self._rows:
            if int(getattr(cand, "fila", 0) or 0) == int(fila):
                return cand
        return None


class _ContratoQueryStub:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def options(self, *_args, **_kwargs):
        return self

    def filter_by(self, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


def _solicitud_stub(sol_id=10, estado="activa", *, horas_actividad=24):
    now = datetime(2026, 3, 24, 10, 0, 0)
    return SimpleNamespace(
        id=sol_id,
        cliente_id=7,
        cliente=SimpleNamespace(nombre_completo="Cliente Demo"),
        codigo_solicitud=f"SOL-{sol_id:03d}",
        estado=estado,
        tipo_plan="Premium",
        abono="1500",
        monto_pagado=None,
        sueldo="12000",
        candidata_id=1,
        candidata=SimpleNamespace(fila=1, nombre_completo="Candidata Uno"),
        fecha_solicitud=datetime(2026, 3, 1, 10, 0, 0),
        fecha_ultima_actividad=now - timedelta(hours=horas_actividad),
        fecha_ultima_modificacion=now - timedelta(hours=horas_actividad),
        estado_previo_espera_pago=None,
        fecha_cambio_espera_pago=None,
        usuario_cambio_espera_pago=None,
        fecha_seguimiento_manual=None,
        reemplazos=[],
    )


class SolicitudOperativaCoreAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def _render_operativa_region(
        self,
        estado,
        *,
        horas_actividad=24,
        needs_followup_today=None,
        priority_label_operativa=None,
        fecha_seguimiento_manual=None,
    ):
        solicitud = _solicitud_stub(10, estado, horas_actividad=horas_actividad)
        solicitud.fecha_seguimiento_manual = fecha_seguimiento_manual
        if priority_label_operativa is None:
            _score, derived_label, _is_stagnant, _hours = admin_routes._solicitud_priority_snapshot(
                solicitud,
                now_dt=datetime(2026, 3, 24, 10, 0, 0),
            )
            priority_label_operativa = derived_label
        manual_followup = admin_routes._manual_followup_snapshot(
            fecha_seguimiento_manual,
            today_rd=date(2026, 3, 24),
        )
        if needs_followup_today is None:
            needs_followup_today = admin_routes._solicitud_needs_followup_today(
                is_stagnant=(horas_actividad >= 72),
                priority_label=priority_label_operativa,
            )
        with flask_app.app_context():
            with flask_app.test_request_context("/admin/clientes/7/solicitudes/10"):
                return render_template(
                    "admin/_solicitud_operativa_core_region.html",
                    solicitud=solicitud,
                    async_feedback=None,
                    now_utc=datetime(2026, 3, 24, 10, 0, 0),
                    needs_followup_today=needs_followup_today,
                    priority_label_operativa=priority_label_operativa,
                    manual_followup=manual_followup,
                )

    def test_cta_contextual_render_proceso(self):
        html = self._render_operativa_region("proceso")
        self.assertIn("Acción recomendada ahora", html)
        self.assertIn("Activar solicitud", html)
        self.assertIn('data-testid="cta-recomendada-action"', html)
        self.assertIn("/admin/solicitudes/10/activar", html)

    def test_cta_contextual_render_activa_y_espera_pago(self):
        html_activa = self._render_operativa_region("activa")
        self.assertIn("Registrar pago", html_activa)
        self.assertIn('data-testid="cta-recomendada-action"', html_activa)
        self.assertIn("/admin/clientes/7/solicitudes/10/pago", html_activa)
        self.assertIn('name="row_version"', html_activa)
        self.assertIn('name="idempotency_key"', html_activa)

        html_espera = self._render_operativa_region("espera_pago")
        self.assertIn("Registrar pago", html_espera)
        self.assertIn('data-testid="cta-recomendada-action"', html_espera)
        self.assertIn("/admin/clientes/7/solicitudes/10/pago", html_espera)

    def test_cta_contextual_render_reemplazo(self):
        html = self._render_operativa_region("reemplazo")
        self.assertIn("Gestionar reemplazo", html)
        self.assertIn('data-testid="cta-recomendada-action"', html)
        self.assertIn("/admin/solicitudes/10/reemplazos/nuevo", html)

    def test_cta_contextual_sin_principal_en_estados_cerrados(self):
        html_pagada = self._render_operativa_region("pagada")
        self.assertIn('data-testid="cta-recomendada-info"', html_pagada)
        self.assertIn("Sin acción operativa prioritaria en este estado.", html_pagada)
        self.assertNotIn('data-testid="cta-recomendada-action"', html_pagada)

        html_cancelada = self._render_operativa_region("cancelada")
        self.assertIn('data-testid="cta-recomendada-info"', html_cancelada)
        self.assertIn("Sin acción operativa prioritaria en este estado.", html_cancelada)
        self.assertNotIn('data-testid="cta-recomendada-action"', html_cancelada)

    def test_bloque_recuperacion_aparece_si_estancada(self):
        html = self._render_operativa_region("activa", horas_actividad=80)
        self.assertIn('data-testid="stagnation-recovery-panel"', html)
        self.assertIn("Esta solicitud lleva más de 72h sin avance.", html)
        self.assertIn("Se recomienda retomar contacto o tomar acción ahora.", html)
        self.assertIn('data-testid="stagnation-recovery-action"', html)
        self.assertIn("Registrar pago", html)

    def test_bloque_recuperacion_no_aparece_si_no_estancada(self):
        html = self._render_operativa_region("activa", horas_actividad=12)
        self.assertNotIn('data-testid="stagnation-recovery-panel"', html)
        self.assertNotIn('data-testid="stagnation-recovery-action"', html)

    def test_panel_atencion_hoy_aparece_si_necesita_seguimiento(self):
        html = self._render_operativa_region("activa", horas_actividad=80)
        self.assertIn('data-testid="followup-today-panel"', html)
        self.assertIn("Atención hoy", html)
        self.assertIn("Esta solicitud requiere acción hoy para no perder avance.", html)
        self.assertIn('data-testid="followup-today-action"', html)

    def test_panel_atencion_hoy_aparece_por_prioridad_alta_no_estancada(self):
        html = self._render_operativa_region(
            "espera_pago",
            horas_actividad=60,
            needs_followup_today=True,
            priority_label_operativa="alta",
        )
        self.assertIn('data-testid="followup-today-panel"', html)
        self.assertIn('data-testid="followup-today-action"', html)

    def test_panel_atencion_hoy_no_aparece_si_no_aplica(self):
        html = self._render_operativa_region("activa", horas_actividad=8, needs_followup_today=False, priority_label_operativa="media")
        self.assertNotIn('data-testid="followup-today-panel"', html)
        self.assertNotIn('data-testid="followup-today-action"', html)

    def test_panel_seguimiento_manual_render_estados(self):
        html_normal = self._render_operativa_region("activa", fecha_seguimiento_manual=None)
        self.assertIn('data-testid="manual-followup-panel"', html_normal)
        self.assertIn("Sin fecha", html_normal)
        self.assertIn('data-testid="manual-followup-input"', html_normal)

        html_pendiente = self._render_operativa_region("activa", fecha_seguimiento_manual=date(2026, 3, 25))
        self.assertIn("Pendiente", html_pendiente)

        html_hoy = self._render_operativa_region("activa", fecha_seguimiento_manual=date(2026, 3, 24))
        self.assertIn(">Hoy<", html_hoy)

        html_vencida = self._render_operativa_region("activa", fecha_seguimiento_manual=date(2026, 3, 23))
        self.assertIn("Vencida", html_vencida)

    def test_guardar_seguimiento_manual_desde_operativa_core_async(self):
        solicitud = _solicitud_stub(10, "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes, "rd_today", return_value=date(2026, 3, 24)), \
                 patch("admin.routes._touch_staff_presence", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/seguimiento_manual",
                    data={
                        "next": "/admin/clientes/7/solicitudes/10",
                        "_async_target": "#solicitudOperativaCoreAsyncRegion",
                        "fecha_seguimiento_manual": "2026-03-25",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#solicitudOperativaCoreAsyncRegion")
        self.assertIn("Seguimiento manual guardado.", data["message"])
        self.assertIn("Pendiente", data["replace_html"])
        self.assertEqual(solicitud.fecha_seguimiento_manual, date(2026, 3, 25))
        commit_mock.assert_called_once()

    def test_limpiar_seguimiento_manual_desde_operativa_core_async(self):
        solicitud = _solicitud_stub(10, "activa")
        solicitud.fecha_seguimiento_manual = date(2026, 3, 24)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes, "rd_today", return_value=date(2026, 3, 24)), \
                 patch("admin.routes._touch_staff_presence", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/seguimiento_manual",
                    data={
                        "next": "/admin/clientes/7/solicitudes/10",
                        "_async_target": "#solicitudOperativaCoreAsyncRegion",
                        "fecha_seguimiento_manual": "",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Seguimiento manual limpiado.", data["message"])
        self.assertIn("Sin fecha", data["replace_html"])
        self.assertIsNone(solicitud.fecha_seguimiento_manual)
        commit_mock.assert_called_once()

    def test_registrar_pago_get_async_devuelve_region(self):
        solicitud = _solicitud_stub(10, "activa")
        candidata = SimpleNamespace(fila=1, nombre_completo="Candidata Uno", cedula="001", codigo="C-1", numero_telefono="809")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryStub([candidata])):
                resp = self.client.get(
                    "/admin/clientes/7/solicitudes/10/pago?q=uno",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#registrarPagoAsyncRegion")
        self.assertIn("Registrar Pago", data["replace_html"])

    def test_registrar_pago_post_async_invalid_input_devuelve_200_con_region(self):
        solicitud = _solicitud_stub(10, "activa")
        candidata = SimpleNamespace(fila=1, nombre_completo="Candidata Uno", cedula="001", codigo="C-1", numero_telefono="809")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryStub([candidata])):
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/pago",
                    data={},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertEqual(data["update_target"], "#registrarPagoAsyncRegion")
        self.assertIn("Registrar Pago", data["replace_html"])

    def test_proceso_acciones_get_async_devuelve_region(self):
        solicitud = _solicitud_stub(10, "proceso")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self.client.get(
                    "/admin/solicitudes/proceso/acciones?page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#procesoAccionesAsyncRegion")
        self.assertIn("SOL-010", data["replace_html"])

    def test_espera_pago_desde_operativa_core_reemplaza_region_local(self):
        solicitud = _solicitud_stub(10, "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._touch_staff_presence", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={
                        "next": "/admin/clientes/7/solicitudes/10",
                        "_async_target": "#solicitudOperativaCoreAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#solicitudOperativaCoreAsyncRegion")
        self.assertIn("Solicitud Operativa Core", data["replace_html"])
        self.assertIn("Espera pago: Sí", data["replace_html"])
        update_targets = data.get("update_targets") or []
        self.assertEqual(len(update_targets), 1)
        self.assertEqual(update_targets[0].get("target"), "#solicitudSummaryAsyncRegion")
        self.assertIn("/admin/clientes/7/solicitudes/10/_summary", update_targets[0].get("redirect_url") or "")
        self.assertEqual(solicitud.estado, "espera_pago")
        commit_mock.assert_called_once()

    def test_espera_pago_desde_operativa_core_error_no_refresca_resumen(self):
        solicitud = _solicitud_stub(10, "espera_pago")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={
                        "next": "/admin/clientes/7/solicitudes/10",
                        "_async_target": "#solicitudOperativaCoreAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["update_target"], "#solicitudOperativaCoreAsyncRegion")
        self.assertIn("Solicitud Operativa Core", data["replace_html"])
        self.assertEqual(data.get("update_targets") or [], [])

    def test_solicitud_detail_tiene_region_async_de_resumen(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitud_detail.html")
        with open(tpl_path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        self.assertIn('id="solicitudSummaryAsyncScope"', txt)
        self.assertIn('id="solicitudSummaryAsyncRegion"', txt)

    def test_solicitud_detail_fragment_summary_headers_baseline(self):
        solicitud = _solicitud_stub(10, "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self.client.get("/admin/clientes/7/solicitudes/10/_summary", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Detalle de solicitud", html)
        self.assertNotIn("Resumen para enviar al cliente", html)
        self.assertEqual(resp.headers.get("X-Async-Fragment-Region"), "solicitudSummaryAsyncRegion")
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)

    def test_solicitud_detail_fragment_operativa_headers_baseline(self):
        solicitud = _solicitud_stub(10, "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self.client.get("/admin/clientes/7/solicitudes/10/_operativa_core", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Solicitud Operativa Core", html)
        self.assertNotIn("Resumen para enviar al cliente", html)
        self.assertEqual(resp.headers.get("X-Async-Fragment-Region"), "solicitudOperativaCoreAsyncRegion")
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)

    def test_solicitud_detail_full_expone_headers_baseline_minima(self):
        solicitud = _solicitud_stub(10, "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.ContratoDigital, "query", _ContratoQueryStub()), \
                 patch.object(admin_routes, "build_resumen_cliente_solicitud", return_value="Resumen"), \
                 patch.object(admin_routes, "_pasaje_copy_phrase_from_solicitud", return_value="No aplica"), \
                 patch.object(admin_routes, "render_template", return_value="<div>detalle</div>"):
                resp = self.client.get("/admin/clientes/7/solicitudes/10", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("X-P1C1-Perf-Scope", resp.headers)
        self.assertIn("X-P1C1-Perf-Latency-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Time-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)


if __name__ == "__main__":
    unittest.main()
