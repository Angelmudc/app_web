# -*- coding: utf-8 -*-

import os
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect, request
from werkzeug.exceptions import NotFound

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows, *, offset_n=0, limit_n=None):
        self._rows = list(rows)
        self._offset_n = offset_n
        self._limit_n = limit_n

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            offset_n=updates.get("offset_n", self._offset_n),
            limit_n=updates.get("limit_n", self._limit_n),
        )

    def options(self, *_args, **_kwargs):
        return self._clone()

    def filter(self, *_args, **_kwargs):
        return self._clone()

    def join(self, *_args, **_kwargs):
        return self._clone()

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

    def _filtered_rows(self):
        rows = list(self._rows)

        estado = (request.args.get("estado") or "").strip().lower()
        allowed_states = {"activa", "reemplazo", "proceso", "espera_pago", "pagada"}
        estados = {estado} if estado in allowed_states else allowed_states
        rows = [r for r in rows if (getattr(r, "estado", "") or "").strip().lower() in estados]

        q = (request.args.get("q") or "").strip().lower()
        if q:
            rows = [
                r for r in rows
                if q in (getattr(r, "codigo_solicitud", "") or "").lower()
                or q in (getattr(r, "ciudad_sector", "") or "").lower()
                or q in (getattr(getattr(r, "cliente", None), "nombre_completo", "") or "").lower()
            ]

        return rows

    def count(self):
        return len(self._filtered_rows())

    def all(self):
        rows = self._filtered_rows()
        rows = rows[self._offset_n:]
        if self._limit_n is not None:
            rows = rows[:self._limit_n]
        return rows

    def get_or_404(self, row_id):
        rid = int(row_id or 0)
        for row in self._rows:
            if int(getattr(row, "id", 0) or 0) == rid:
                return row
        raise NotFound()


def _solicitud_stub(sol_id: int, codigo: str, estado: str, dias_seguimiento: int, *, candidata_id=0, horas_actividad=24):
    now = datetime(2026, 3, 24, 10, 0, 0)
    return SimpleNamespace(
        id=sol_id,
        codigo_solicitud=codigo,
        cliente_id=7,
        cliente=SimpleNamespace(nombre_completo=f"Cliente {sol_id}", codigo=f"CL-{sol_id:03d}"),
        candidata=None,
        candidata_id=candidata_id,
        ciudad_sector="Santiago",
        estado=estado,
        fecha_solicitud=now - timedelta(days=30),
        estado_actual_desde=now - timedelta(days=dias_seguimiento),
        fecha_inicio_seguimiento=now - timedelta(days=dias_seguimiento),
        rutas_cercanas="Monumental",
        modalidad_trabajo="Con dormida",
        horario="L-V",
        estado_previo_espera_pago=None,
        fecha_cambio_espera_pago=None,
        usuario_cambio_espera_pago=None,
        fecha_seguimiento_manual=None,
        fecha_ultima_actividad=now - timedelta(hours=horas_actividad),
        fecha_ultima_modificacion=now - timedelta(hours=horas_actividad),
        reemplazos=[],
    )


class AdminSolicitudesPrioridadAsyncTest(unittest.TestCase):
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

    def test_filtros_async_devuelven_json_con_html_parcial(self):
        rows = [
            _solicitud_stub(10, "SOL-PRIO-10", "activa", 8, horas_actividad=6),
            _solicitud_stub(11, "SOL-PRIO-11", "proceso", 3, horas_actividad=90),
            _solicitud_stub(12, "SOL-OTRA-12", "reemplazo", 20, horas_actividad=90),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?q=PRIO&estado=activa&prioridad=atencion&page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#prioridadAsyncRegion")
        self.assertIn("SOL-PRIO-10", data["replace_html"])
        self.assertNotIn("SOL-PRIO-11", data["replace_html"])
        self.assertNotIn("SOL-OTRA-12", data["replace_html"])
        self.assertIn("Lleva 8 días en estado activo", data["replace_html"])
        self.assertIn('data-testid="metric-total"', data["replace_html"])

    def test_paginacion_async_devuelve_pagina_sin_recarga(self):
        rows = [_solicitud_stub(i, f"SOL-P-{i:03d}", "activa", 15, horas_actividad=48) for i in range(1, 26)]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["page"], 2)
        self.assertIn("SOL-P-011", data["replace_html"])
        self.assertNotIn("SOL-P-001", data["replace_html"])

    def test_filtro_estancadas_muestra_solo_rows_con_72h_o_mas(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "activa", 4, horas_actividad=8),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?estancadas=1",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn("SOL-P-010", html)
        self.assertNotIn("SOL-P-011", html)
        self.assertIn("Críticas", html)

    def test_badge_estancada_aparece_solo_cuando_aplica(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "activa", 3, horas_actividad=8),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn("SOL-P-010", html)
        self.assertIn("SOL-P-011", html)
        self.assertIn("Críticas", html)
        self.assertIn("NORMALES", html)

    def test_proximo_paso_render_por_estado_y_estancamiento(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "proceso", 15, horas_actividad=12),
            _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=8),
            _solicitud_stub(12, "SOL-P-012", "activa", 15, horas_actividad=90),
            _solicitud_stub(13, "SOL-P-013", "espera_pago", 15, horas_actividad=20),
            _solicitud_stub(14, "SOL-P-014", "reemplazo", 15, horas_actividad=50),
            _solicitud_stub(15, "SOL-P-015", "pagada", 15, horas_actividad=6),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn("La solicitud aún no está en operación activa.", html)
        self.assertIn("Lleva demasiados días activa sin cierre.", html)
        self.assertIn("El reemplazo ya cruza umbral urgente/crítico.", html)
        self.assertIn("Proceso finalizado sin acción operativa pendiente.", html)
        self.assertIn("data-testid=\"followup-today-badge\"", html)

    def test_badge_hoy_aparece_solo_cuando_requiere_seguimiento(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=8),    # media, no estancada
            _solicitud_stub(11, "SOL-P-011", "espera_pago", 15, horas_actividad=60),  # alta, no estancada
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertEqual(html.count('data-testid="followup-today-badge"'), 1)
        self.assertIn("SOL-P-010", html)
        self.assertIn("SOL-P-011", html)

    def test_resumen_responsable_agrupa_y_muestra_sin_responsable(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),        # Karla (alta+estancada)
            _solicitud_stub(11, "SOL-P-011", "espera_pago", 15, horas_actividad=20),   # Karla (alta)
            _solicitud_stub(12, "SOL-P-012", "reemplazo", 15, horas_actividad=80),     # Anyi (media+estancada)
            _solicitud_stub(13, "SOL-P-013", "activa", 15, horas_actividad=8),         # Sin responsable
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1, 11: 1, 12: 2}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla", 2: "Anyi"}):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="responsable-summary-block"', html)
        self.assertIn("Carga por responsable (última gestión)", html)
        self.assertIn("Basado en la última gestión registrada, no en asignación formal.", html)
        self.assertIn("Karla", html)
        self.assertIn("Anyi", html)
        self.assertIn("Sin responsable", html)
        self.assertIn(">2<", html)  # total Karla
        self.assertIn(">1<", html)  # Anyi y Sin responsable

    def test_resumen_responsable_respeta_filtros_activos(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "espera_pago", 15, horas_actividad=20),
            _solicitud_stub(12, "SOL-P-012", "reemplazo", 15, horas_actividad=80),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1, 11: 1, 12: 2}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla", 2: "Anyi"}):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?estado=espera_pago",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="responsable-summary-block"', html)
        self.assertIn("Karla", html)
        self.assertNotIn("Anyi", html)
        self.assertNotIn('data-testid="responsable-name">Sin responsable<', html)
        self.assertIn('data-testid="responsable-total">1<', html)

    def test_filtro_por_responsable_muestra_solo_sus_casos(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=80),
            _solicitud_stub(12, "SOL-P-012", "activa", 15, horas_actividad=80),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1, 11: 2}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla", 2: "Anyi"}):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?responsable=1",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn("SOL-P-010", html)
        self.assertNotIn("SOL-P-011", html)
        self.assertNotIn("SOL-P-012", html)
        self.assertIn('data-testid="metric-total">1<', html)
        self.assertIn("Karla", html)
        self.assertNotIn("Anyi", html)

    def test_filtro_sin_responsable_muestra_bloque_operativo(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=80),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla"}):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?responsable=none",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertNotIn("SOL-P-010", html)
        self.assertIn("SOL-P-011", html)
        self.assertIn('data-testid="sin-responsable-block"', html)
        self.assertIn('data-testid="metric-sin-responsable">1<', html)

    def test_resumen_responsable_incluye_vencidas_y_riesgo(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=8),
        ]
        rows[0].fecha_seguimiento_manual = date(2026, 3, 23)  # vencida
        rows[1].fecha_seguimiento_manual = date(2026, 3, 24)  # hoy
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes, "rd_today", return_value=date(2026, 3, 24)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1, 11: 1}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla"}):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="responsable-overdue">1<', html)
        self.assertIn('data-testid="responsable-stagnant">3<', html)

    def test_badge_seguimiento_manual_render_por_estado(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=8),
            _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=8),
            _solicitud_stub(12, "SOL-P-012", "activa", 15, horas_actividad=8),
            _solicitud_stub(13, "SOL-P-013", "activa", 15, horas_actividad=8),
        ]
        rows[0].fecha_seguimiento_manual = None
        rows[1].fecha_seguimiento_manual = date(2026, 3, 25)  # pendiente
        rows[2].fecha_seguimiento_manual = date(2026, 3, 24)  # hoy
        rows[3].fecha_seguimiento_manual = date(2026, 3, 23)  # vencida
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes, "rd_today", return_value=date(2026, 3, 24)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertEqual(html.count('data-testid="manual-followup-badge"'), 3)
        self.assertIn("Pendiente", html)
        self.assertIn(">Hoy<", html)
        self.assertIn("Vencida", html)

    def test_metricas_operativas_calculadas_correctamente(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 3, horas_actividad=10),
            _solicitud_stub(11, "SOL-P-011", "activa", 8, horas_actividad=20),
            _solicitud_stub(12, "SOL-P-012", "activa", 12, horas_actividad=90),
            _solicitud_stub(13, "SOL-P-013", "reemplazo", 16, horas_actividad=90),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="metric-total">4<', html)
        self.assertIn('data-testid="metric-activa">3<', html)
        self.assertIn('data-testid="metric-reemplazo">1<', html)
        self.assertIn('data-testid="metric-critica">1<', html)
        self.assertIn('data-testid="metric-urgente">1<', html)
        self.assertIn('data-testid="metric-atencion">1<', html)
        self.assertIn('data-testid="metric-normal">1<', html)

    def test_metricas_riesgo_operativo_incluyen_pago_prolongado_y_reemplazo_sin_seguimiento(self):
        rows = [
            _solicitud_stub(10, "SOL-PAGO-LARGO", "espera_pago", 8, horas_actividad=6),
            _solicitud_stub(11, "SOL-REEMP-SIN-SEG", "reemplazo", 6, horas_actividad=6),
        ]
        rows[1].reemplazos = [
            SimpleNamespace(
                id=501,
                fecha_inicio_reemplazo=datetime(2026, 3, 20, 8, 0, 0),
                fecha_fin_reemplazo=None,
                created_at=datetime(2026, 3, 20, 8, 0, 0),
            )
        ]
        rows[1].fecha_seguimiento_manual = None

        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes, "rd_today", return_value=date(2026, 3, 24)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="metric-espera-pago-prolongada">1<', html)
        self.assertIn('data-testid="metric-reemplazo-sin-seguimiento">1<', html)
        self.assertIn("Espera de pago prolongada", html)
        self.assertIn("Reemplazo sin seguimiento", html)

    def test_metricas_respetan_filtros_activos(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=10),
            _solicitud_stub(11, "SOL-P-011", "reemplazo", 15, horas_actividad=90),
            _solicitud_stub(12, "SOL-P-012", "pagada", 15, horas_actividad=90),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/prioridad?estado=reemplazo&estancadas=1",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html", "")
        self.assertIn('data-testid="metric-total">1<', html)
        self.assertIn('data-testid="metric-activa">0<', html)
        self.assertIn('data-testid="metric-reemplazo">1<', html)
        self.assertIn('data-testid="metric-critica">1<', html)
        self.assertIn('data-testid="metric-normal">0<', html)

    def test_orden_prioriza_score_mas_alto(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "espera_pago", 15, horas_actividad=2),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        pos_espera = html.find("SOL-P-011")
        pos_activa = html.find("SOL-P-010")
        self.assertTrue(pos_espera != -1 and pos_activa != -1)
        self.assertLess(pos_activa, pos_espera)

    def test_fallback_clasico_se_mantiene(self):
        solicitud = _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80)
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                page_resp = self.client.get("/admin/solicitudes/prioridad", follow_redirects=False)

        self.assertEqual(page_resp.status_code, 200)
        html = page_resp.get_data(as_text=True)
        self.assertIn('data-live-invalidation-view="solicitudes_prioridad_summary"', html)
        self.assertIn('id="prioridadAsyncRegion"', html)
        self.assertIn('id="prioridadSummaryAsyncRegion"', html)
        self.assertIn('id="prioridadResponsablesAsyncRegion"', html)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit"):
                action_resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes/prioridad?page=2"},
                    follow_redirects=False,
                )

        self.assertIn(action_resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/prioridad?page=2", action_resp.location)

    def test_fragmentos_livianos_summary_y_responsables(self):
        rows = [
            _solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80),
            _solicitud_stub(11, "SOL-P-011", "espera_pago", 15, horas_actividad=20),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch.object(admin_routes, "_resolve_solicitud_last_actor_user_ids", return_value={10: 1, 11: 1}), \
                 patch.object(admin_routes, "_staff_username_map", return_value={1: "Karla"}), \
                 patch.object(admin_routes, "_SolicitudPrioridadVM", side_effect=AssertionError("VM no debe construirse en fragments")) as vm_ctor:
                summary_resp = self.client.get("/admin/solicitudes/prioridad/_summary", follow_redirects=False)
                responsables_resp = self.client.get("/admin/solicitudes/prioridad/_responsables", follow_redirects=False)
        vm_ctor.assert_not_called()

        self.assertEqual(summary_resp.status_code, 200)
        summary_html = summary_resp.get_data(as_text=True)
        self.assertIn('id="prioridadSummaryAsyncRegion"', summary_html)
        self.assertIn('data-testid="metric-total">2<', summary_html)
        self.assertEqual(summary_resp.headers.get("X-Async-Fragment-Region"), "prioridadSummaryAsyncRegion")
        self.assertIn("X-P1C1-Perf-DB-Queries", summary_resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", summary_resp.headers)

        self.assertEqual(responsables_resp.status_code, 200)
        responsables_html = responsables_resp.get_data(as_text=True)
        self.assertIn('id="prioridadResponsablesAsyncRegion"', responsables_html)
        self.assertIn("Karla", responsables_html)
        self.assertEqual(responsables_resp.headers.get("X-Async-Fragment-Region"), "prioridadResponsablesAsyncRegion")
        self.assertIn("X-P1C1-Perf-DB-Queries", responsables_resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", responsables_resp.headers)

    def test_listado_prioridad_expone_headers_minimos_baseline(self):
        rows = [_solicitud_stub(10, "SOL-P-010", "activa", 15, horas_actividad=80)]
        with flask_app.app_context():
            with patch.object(admin_routes, "utc_now_naive", return_value=datetime(2026, 3, 24, 10, 0, 0)), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/prioridad", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("X-P1C1-Perf-Scope", resp.headers)
        self.assertIn("X-P1C1-Perf-Latency-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Time-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)

    def test_manejo_errores_async_conflicto_rate_limit_server_y_not_found(self):
        solicitud_conflict = _solicitud_stub(10, "SOL-P-010", "espera_pago", 15, horas_actividad=80)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud_conflict])):
                resp_conflict = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes/prioridad?page=1", "_async_target": "#prioridadAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )
        self.assertEqual(resp_conflict.status_code, 409)
        self.assertEqual(resp_conflict.get_json()["error_code"], "conflict")

        solicitud_rate = _solicitud_stub(11, "SOL-P-011", "activa", 15, horas_actividad=80)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud_rate])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=redirect("/admin/solicitudes/prioridad")):
                resp_rate = self.client.post(
                    "/admin/solicitudes/11/poner_espera_pago",
                    data={"next": "/admin/solicitudes/prioridad?page=1", "_async_target": "#prioridadAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )
        self.assertEqual(resp_rate.status_code, 429)
        self.assertEqual(resp_rate.get_json()["error_code"], "rate_limit")

        solicitud_server = _solicitud_stub(12, "SOL-P-012", "activa", 15, horas_actividad=80)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud_server])), \
                 patch("admin.routes.db.session.commit", side_effect=Exception("boom")), \
                 patch("admin.routes.db.session.rollback"):
                resp_server = self.client.post(
                    "/admin/solicitudes/12/poner_espera_pago",
                    data={"next": "/admin/solicitudes/prioridad?page=1", "_async_target": "#prioridadAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )
        self.assertEqual(resp_server.status_code, 500)
        self.assertEqual(resp_server.get_json()["error_code"], "server_error")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([])):
                resp_not_found = self.client.post(
                    "/admin/solicitudes/999/poner_espera_pago",
                    data={"next": "/admin/solicitudes/prioridad?page=1", "_async_target": "#prioridadAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )
        self.assertEqual(resp_not_found.status_code, 404)


if __name__ == "__main__":
    unittest.main()
