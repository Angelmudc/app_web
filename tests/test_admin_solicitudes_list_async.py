# -*- coding: utf-8 -*-

import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect, request
from sqlalchemy.exc import SQLAlchemyError

from app import app as flask_app
import admin.routes as admin_routes


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

    def options(self, *_args, **_kwargs):
        return self._clone()

    def filter(self, *_args, **_kwargs):
        return self._clone()

    def outerjoin(self, *_args, **_kwargs):
        return self._clone()

    def order_by(self, *_args, **_kwargs):
        return self._clone()

    def filter_by(self, **kwargs):
        return self._clone(estado_filter=kwargs.get("estado"))

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
        if self._estado_filter:
            rows = [r for r in rows if (getattr(r, "estado", "") or "") == self._estado_filter]

        estado_q = (request.args.get("estado") or "").strip().lower()
        if estado_q:
            rows = [r for r in rows if ((getattr(r, "estado", "") or "").strip().lower() == estado_q)]

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

    def first_or_404(self):
        rows = self._filtered_rows()
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada en stub")

    def get_or_404(self, _id):
        for row in self._rows:
            if int(getattr(row, "id", 0) or 0) == int(_id):
                return row
        raise AssertionError("Solicitud no encontrada en stub")


class _ReemplazoQueryStub:
    def __init__(self, repl):
        self._repl = repl

    def filter_by(self, **_kwargs):
        return self

    def first_or_404(self):
        return self._repl


def _solicitud_stub(sol_id: int, codigo: str, estado: str):
    return SimpleNamespace(
        id=sol_id,
        codigo_solicitud=codigo,
        cliente_id=7,
        cliente=SimpleNamespace(nombre_completo=f"Cliente {sol_id}"),
        estado=estado,
        candidata=None,
        candidata_id=None,
        fecha_solicitud=datetime(2026, 3, 1, 10, 0, 0),
        ciudad_sector="Santiago",
        reemplazos=[],
        last_copiado_at=None,
        rutas_cercanas="Monumental",
        estado_previo_espera_pago=None,
        fecha_cambio_espera_pago=None,
        usuario_cambio_espera_pago=None,
        fecha_ultima_actividad=datetime(2026, 3, 1, 10, 0, 0),
        fecha_ultima_modificacion=datetime(2026, 3, 1, 10, 0, 0),
    )


class AdminSolicitudesListAsyncTest(unittest.TestCase):
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

    def test_filtros_async_devuelven_json_con_html_parcial(self):
        rows = [
            _solicitud_stub(10, "SOL-A-10", "activa"),
            _solicitud_stub(11, "SOL-B-11", "proceso"),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes?q=SOL-A&estado=activa&page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#solicitudesAsyncRegion")
        self.assertIn("SOL-A-10", data["replace_html"])
        self.assertNotIn("SOL-B-11", data["replace_html"])
        self.assertIn('id="solicitudReemplazoActionsAsyncRegion-10"', data["replace_html"])
        self.assertIn('name="row_version"', data["replace_html"])
        self.assertIn('name="idempotency_key"', data["replace_html"])

    def test_listado_clasico_incluye_region_async_de_resumen(self):
        rows = [_solicitud_stub(10, "SOL-A-10", "activa")]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="solicitudesSummaryAsyncScope"', html)
        self.assertIn('id="solicitudesSummaryAsyncRegion"', html)
        self.assertIn("En proceso", html)
        self.assertIn("Copiables", html)

    def test_paginacion_async_devuelve_pagina_sin_recarga(self):
        rows = [_solicitud_stub(i, f"SOL-{i:03d}", "activa") for i in range(1, 26)]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes?page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["page"], 2)
        self.assertIn("SOL-011", data["replace_html"])
        self.assertNotIn("SOL-001", data["replace_html"])

    def test_triage_espera_pago_filtra_en_async(self):
        rows = [
            _solicitud_stub(10, "SOL-EP", "espera_pago"),
            _solicitud_stub(11, "SOL-RE", "reemplazo"),
            _solicitud_stub(12, "SOL-AC", "activa"),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes?triage=espera_pago",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json() or {}
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("triage"), "espera_pago")
        html = data.get("replace_html") or ""
        self.assertIn("SOL-EP", html)
        self.assertNotIn("SOL-RE", html)
        self.assertNotIn("SOL-AC", html)
        self.assertIn("Bloque en foco: <strong>Espera de pago</strong>", html)

    def test_triage_sql_parts_castea_estado_enum_antes_de_lower(self):
        with flask_app.app_context():
            parts = admin_routes._solicitudes_triage_sql_parts(
                now_dt=datetime(2026, 3, 10, 10, 0, 0),
                today_rd=date(2026, 3, 10),
            )
        clause_sql = str(parts["clauses"]["espera_pago"])
        self.assertIn("lower(", clause_sql.lower())
        self.assertIn("cast(", clause_sql.lower())

    def test_triage_sql_fallback_hace_rollback_y_no_rompe_request(self):
        sol_espera = _solicitud_stub(10, "SOL-ESPERA", "espera_pago")
        sol_espera.estado_actual_desde = datetime(2026, 3, 1, 10, 0, 0)
        sol_espera.fecha_ultima_actividad = datetime(2026, 3, 1, 10, 0, 0)

        sol_stale = _solicitud_stub(11, "SOL-STALE", "activa")
        sol_stale.estado_actual_desde = datetime(2026, 2, 20, 10, 0, 0)
        sol_stale.fecha_ultima_actividad = datetime(2026, 2, 20, 10, 0, 0)

        sol_otra = _solicitud_stub(12, "SOL-OTRA", "activa")
        sol_otra.estado_actual_desde = datetime(2026, 3, 10, 10, 0, 0)
        sol_otra.fecha_ultima_actividad = datetime(2026, 3, 10, 10, 0, 0)

        rows = [sol_espera, sol_stale, sol_otra]
        triage_cases = {
            "espera_pago": "SOL-ESPERA",
            "espera_pago_prolongada": "SOL-ESPERA",
            "sin_movimiento": "SOL-STALE",
            "sin_responsable": "SOL-ESPERA",
        }

        with flask_app.app_context():
            for triage_code, expected_codigo in triage_cases.items():
                with self.subTest(triage=triage_code):
                    with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                         patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                         patch("admin.routes._solicitudes_triage_options_sql", side_effect=SQLAlchemyError("boom triage sql")), \
                         patch("admin.routes._resolve_solicitud_last_actor_user_ids", return_value={11: 77, 12: 88}), \
                         patch("admin.routes._staff_username_map", return_value={77: "staff77", 88: "staff88"}), \
                         patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                         patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)), \
                         patch("admin.routes.db.session.rollback") as rollback_mock:
                        resp = self.client.get(
                            f"/admin/solicitudes?triage={triage_code}",
                            headers=self._async_headers(),
                            follow_redirects=False,
                        )

                    self.assertEqual(resp.status_code, 200)
                    payload = resp.get_json() or {}
                    self.assertTrue(payload.get("success"))
                    self.assertEqual(payload.get("triage"), triage_code)
                    html = payload.get("replace_html") or ""
                    self.assertIn(expected_codigo, html)
                    rollback_mock.assert_called()

    def test_triage_paridad_sql_vs_fallback_en_memoria(self):
        sol_espera = _solicitud_stub(10, "SOL-ESPERA", "espera_pago")
        sol_espera.estado_actual_desde = datetime(2026, 3, 1, 10, 0, 0)
        sol_espera.fecha_ultima_actividad = datetime(2026, 3, 1, 10, 0, 0)

        sol_stale = _solicitud_stub(11, "SOL-STALE", "activa")
        sol_stale.estado_actual_desde = datetime(2026, 2, 20, 10, 0, 0)
        sol_stale.fecha_ultima_actividad = datetime(2026, 2, 20, 10, 0, 0)

        sol_otra = _solicitud_stub(12, "SOL-OTRA", "activa")
        sol_otra.estado_actual_desde = datetime(2026, 3, 10, 10, 0, 0)
        sol_otra.fecha_ultima_actividad = datetime(2026, 3, 10, 10, 0, 0)

        rows = [sol_espera, sol_stale, sol_otra]
        triage_codes = [
            "sin_responsable",
            "espera_pago",
            "espera_pago_prolongada",
            "sin_movimiento",
            "urgentes",
            "activas_estables",
        ]

        with flask_app.app_context():
            for triage_code in triage_codes:
                with self.subTest(triage=triage_code):
                    with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                         patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                         patch("admin.routes._solicitudes_triage_options_sql", return_value=[]), \
                         patch("admin.routes._resolve_solicitud_last_actor_user_ids", return_value={11: 77, 12: 88}), \
                         patch("admin.routes._staff_username_map", return_value={77: "staff77", 88: "staff88"}), \
                         patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                         patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                        resp_sql = self.client.get(
                            f"/admin/solicitudes?triage={triage_code}",
                            headers=self._async_headers(),
                            follow_redirects=False,
                        )

                    with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                         patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                         patch("admin.routes._solicitudes_triage_options_sql", side_effect=SQLAlchemyError("boom triage sql")), \
                         patch("admin.routes._resolve_solicitud_last_actor_user_ids", return_value={11: 77, 12: 88}), \
                         patch("admin.routes._staff_username_map", return_value={77: "staff77", 88: "staff88"}), \
                         patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                         patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                        resp_mem = self.client.get(
                            f"/admin/solicitudes?triage={triage_code}",
                            headers=self._async_headers(),
                            follow_redirects=False,
                        )

                    self.assertEqual(resp_sql.status_code, 200)
                    self.assertEqual(resp_mem.status_code, 200)
                    html_sql = (resp_sql.get_json() or {}).get("replace_html") or ""
                    html_mem = (resp_mem.get_json() or {}).get("replace_html") or ""
                    for code in ("SOL-ESPERA", "SOL-STALE", "SOL-OTRA"):
                        self.assertEqual(code in html_sql, code in html_mem)

    def test_triage_sql_usa_count_de_options_y_evita_count_extra(self):
        rows = [
            _solicitud_stub(10, "SOL-EP", "espera_pago"),
            _solicitud_stub(11, "SOL-AC", "activa"),
        ]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                 patch("admin.routes._solicitudes_triage_options_sql", return_value=[
                     {"code": "", "label": "Todas", "count": 2, "active": False},
                     {"code": "espera_pago", "label": "Espera de pago", "count": 1, "active": True},
                 ]), \
                 patch("admin.routes._query_count_distinct_solicitudes", side_effect=AssertionError("count redundante")), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes?triage=espera_pago&page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self.assertTrue(payload.get("success"))
        self.assertEqual(int(payload.get("total", 0) or 0), 1)
        html = payload.get("replace_html") or ""
        self.assertIn("SOL-EP", html)
        self.assertNotIn("SOL-AC", html)

    def test_triage_fallback_construye_vm_solo_para_fila_de_pagina(self):
        rows = [_solicitud_stub(i, f"SOL-{i:03d}", "espera_pago") for i in range(1, 51)]
        for row in rows:
            row.estado_actual_desde = datetime(2026, 3, 1, 10, 0, 0)
            row.fecha_ultima_actividad = datetime(2026, 3, 1, 10, 0, 0)

        original_init = admin_routes._SolicitudOperativaListVM.__init__
        vm_build_count = 0

        def _counting_init(self, *args, **kwargs):
            nonlocal vm_build_count
            vm_build_count += 1
            return original_init(self, *args, **kwargs)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                 patch("admin.routes._solicitudes_triage_options_sql", side_effect=SQLAlchemyError("boom triage sql")), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)), \
                 patch.object(admin_routes._SolicitudOperativaListVM, "__init__", new=_counting_init):
                resp = self.client.get(
                    "/admin/solicitudes?triage=espera_pago&page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("triage"), "espera_pago")
        self.assertEqual(vm_build_count, 10)

    def test_triage_fallback_no_resuelve_reemplazo_por_relacion_fuera_de_pagina(self):
        rows = [_solicitud_stub(i, f"SOL-{i:03d}", "espera_pago") for i in range(1, 51)]
        for row in rows:
            row.estado_actual_desde = datetime(2026, 3, 1, 10, 0, 0)
            row.fecha_ultima_actividad = datetime(2026, 3, 1, 10, 0, 0)

        active_map = {int(r.id): SimpleNamespace(id=9000 + int(r.id), solicitud_id=int(r.id)) for r in rows}
        active_call_sizes = []
        page_repl_call_count = 0

        def _active_map_stub(solicitud_ids):
            active_call_sizes.append(len(list(solicitud_ids or [])))
            return active_map

        original_active_for = admin_routes._active_reemplazo_for_solicitud

        def _counting_active_for(solicitud):
            nonlocal page_repl_call_count
            page_repl_call_count += 1
            return original_active_for(solicitud)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                 patch("admin.routes._solicitudes_triage_options_sql", side_effect=SQLAlchemyError("boom triage sql")), \
                 patch("admin.routes._active_reemplazo_map_for_solicitudes", side_effect=_active_map_stub), \
                 patch("admin.routes._active_reemplazo_for_solicitud", side_effect=_counting_active_for), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes?triage=espera_pago&page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(active_call_sizes, [])
        self.assertEqual(page_repl_call_count, 10)

    def test_triage_fallback_no_resuelve_anchor_global_antes_de_paginar(self):
        rows = [_solicitud_stub(i, f"SOL-{i:03d}", "activa") for i in range(1, 51)]
        for row in rows:
            row.estado_actual_desde = None
            row.fecha_inicio_seguimiento = datetime(2026, 3, 1, 10, 0, 0)
            row.fecha_ultima_modificacion = datetime(2026, 3, 1, 10, 0, 0)
            row.fecha_ultima_actividad = datetime(2026, 3, 1, 10, 0, 0)

        resolve_call_count = 0
        original_resolve = admin_routes.resolve_solicitud_estado_priority_anchor

        def _counting_resolve(solicitud):
            nonlocal resolve_call_count
            resolve_call_count += 1
            return original_resolve(solicitud)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes._solicitudes_query_supports_sql_triage", return_value=True), \
                 patch("admin.routes._solicitudes_triage_options_sql", side_effect=SQLAlchemyError("boom triage sql")), \
                 patch("admin.routes.resolve_solicitud_estado_priority_anchor", side_effect=_counting_resolve), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes?triage=urgentes&page=2&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        # Debe evitar resolución global (50); solo se permite costo acotado al render de página.
        self.assertLessEqual(resolve_call_count, 20)

    def test_listado_muestra_accion_rapida_espera_pago_y_quitar_espera(self):
        rows = [
            _solicitud_stub(10, "SOL-ACTIVA", "activa"),
            _solicitud_stub(11, "SOL-ESPERA", "espera_pago"),
            _solicitud_stub(12, "SOL-CANCEL", "cancelada"),
        ]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertIn("Marcar espera de pago</button>", html)
        self.assertIn("Quitar espera de pago</button>", html)
        self.assertIn('data-collapse-open-label="Ocultar resumen"', html)
        self.assertIn('data-collapse-open-label="Ocultar acciones"', html)
        start_marker = 'id="sol-actions-10"'
        idx = html.find(start_marker)
        self.assertGreaterEqual(idx, 0)
        next_article_idx = html.find('<article id="sol-11"', idx)
        collapsed_section = html[idx:next_article_idx if next_article_idx > idx else len(html)]
        self.assertIn("Marcar espera de pago</button>", collapsed_section)
        self.assertNotIn("Quitar espera de pago</button>", collapsed_section)

    def test_paginacion_preserva_triage_en_links(self):
        rows = [_solicitud_stub(i, f"SOL-{i:03d}", "espera_pago") for i in range(1, 23)]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get(
                    "/admin/solicitudes?triage=espera_pago&page=1&per_page=10",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertIn("triage=espera_pago&amp;page=2", html)

    def test_estado_vacio_async_muestra_mensaje_y_limpiar_filtros(self):
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([])):
                resp = self.client.get(
                    "/admin/solicitudes?q=zzz&estado=activa",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("No hay solicitudes para mostrar", data["replace_html"])
        self.assertIn("Limpiar filtros", data["replace_html"])

    def test_senal_operativa_principal_vencida_tiene_precedencia(self):
        sol = _solicitud_stub(10, "SOL-010", "espera_pago")
        sol.fecha_seguimiento_manual = date(2026, 3, 4)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([sol])), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 5)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 5, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        html = data.get("replace_html") or ""
        self.assertIn("Vencida", html)
        self.assertNotIn("Esperando pago", html)

    def test_senal_operativa_detecta_espera_pago_prolongada(self):
        sol = _solicitud_stub(10, "SOL-PAGO-LARGO", "espera_pago")
        sol.fecha_solicitud = datetime(2026, 3, 1, 10, 0, 0)
        sol.estado_actual_desde = datetime(2026, 3, 1, 10, 0, 0)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([sol])), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertIn("Espera de pago prolongada", html)

    def test_senal_operativa_detecta_reemplazo_sin_seguimiento(self):
        sol = _solicitud_stub(10, "SOL-REEMP-SIN-SEG", "reemplazo")
        sol.reemplazos = [
            SimpleNamespace(
                id=99,
                fecha_inicio_reemplazo=datetime(2026, 3, 5, 10, 0, 0),
                fecha_fin_reemplazo=None,
                created_at=datetime(2026, 3, 5, 10, 0, 0),
            )
        ]
        sol.fecha_seguimiento_manual = None
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([sol])), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 10)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 10, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertIn("Reemplazo sin seguimiento", html)

    def test_listado_prioriza_arriba_lo_mas_critico(self):
        estable = _solicitud_stub(10, "SOL-ESTABLE", "activa")
        estable.fecha_seguimiento_manual = date(2026, 3, 7)
        estable.fecha_ultima_actividad = datetime(2026, 3, 5, 9, 0, 0)

        hoy = _solicitud_stub(11, "SOL-HOY", "activa")
        hoy.fecha_seguimiento_manual = date(2026, 3, 5)
        hoy.fecha_ultima_actividad = datetime(2026, 3, 5, 8, 0, 0)

        vencida = _solicitud_stub(12, "SOL-VENCIDA", "activa")
        vencida.fecha_seguimiento_manual = date(2026, 3, 4)
        vencida.fecha_ultima_actividad = datetime(2026, 3, 1, 8, 0, 0)

        rows = [estable, hoy, vencida]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 5)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 5, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertLess(html.find("SOL-VENCIDA"), html.find("SOL-HOY"))
        self.assertLess(html.find("SOL-HOY"), html.find("SOL-ESTABLE"))

    def test_usa_solo_una_senal_operativa_badge_y_seguimiento_como_texto(self):
        sol = _solicitud_stub(10, "SOL-010", "activa")
        sol.fecha_seguimiento_manual = date(2026, 3, 5)
        sol.fecha_ultima_actividad = datetime(2026, 3, 5, 9, 0, 0)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([sol])), \
                 patch("admin.routes.rd_today", return_value=date(2026, 3, 5)), \
                 patch("admin.routes.utc_now_naive", return_value=datetime(2026, 3, 5, 10, 0, 0)):
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("replace_html") or ""
        self.assertIn("Atención hoy", html)
        self.assertIn('<span class="sol-muted">Seguimiento:</span>', html)
        self.assertIn('title="Seguimiento manual programado para hoy.">Hoy</span>', html)

    def test_accion_por_fila_async_poner_espera_pago(self):
        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._solicitudes_summary_counts", return_value=(4, 7, "")) as summary_counts_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#solicitudesAsyncRegion")
        update_targets = data.get("update_targets") or []
        self.assertEqual(len(update_targets), 2)
        self.assertEqual(update_targets[0].get("target"), "#solicitudesAsyncRegion")
        self.assertTrue(update_targets[0].get("invalidate"))
        self.assertEqual(update_targets[1].get("target"), "#solicitudesSummaryAsyncRegion")
        self.assertTrue(update_targets[1].get("invalidate"))
        self.assertIn("/admin/solicitudes/_summary", update_targets[1].get("redirect_url") or "")
        self.assertIn("En proceso", update_targets[1].get("replace_html") or "")
        self.assertIn("Copiables", update_targets[1].get("replace_html") or "")
        self.assertIn("/admin/solicitudes?page=1", data["redirect_url"])
        self.assertEqual(data.get("focus_row_id"), 10)
        self.assertTrue(data.get("flash_row"))
        self.assertTrue(data.get("preserve_open_collapses"))
        summary_counts_mock.assert_called_once()
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_fallback_clasico_se_mantiene_en_accion_por_fila(self):
        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes?estado=activa&page=2"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes?estado=activa&page=2", resp.location)

    def test_accion_por_fila_async_quitar_espera_pago_refresca_lista_y_resumen(self):
        solicitud = _solicitud_stub(10, "SOL-010", "espera_pago")
        solicitud.estado_previo_espera_pago = "activa"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/quitar_espera_pago",
                    data={"next": "/admin/solicitudes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json() or {}
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("update_target"), "#solicitudesAsyncRegion")
        update_targets = data.get("update_targets") or []
        self.assertEqual(len(update_targets), 2)
        self.assertEqual(update_targets[0].get("target"), "#solicitudesAsyncRegion")
        self.assertEqual(update_targets[1].get("target"), "#solicitudesSummaryAsyncRegion")
        self.assertIn("/admin/solicitudes/_summary", update_targets[1].get("redirect_url") or "")
        self.assertEqual(data.get("focus_row_id"), 10)
        self.assertTrue(data.get("flash_row"))
        self.assertTrue(data.get("preserve_open_collapses"))
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_summary_fragment_devuelve_html_liviano_y_headers_baseline(self):
        rows = [_solicitud_stub(10, "SOL-A-10", "activa")]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes/_summary", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("En proceso", html)
        self.assertIn("Copiables", html)
        self.assertNotIn("Acciones rápidas", html)
        self.assertEqual(resp.headers.get("X-Async-Fragment-Region"), "solicitudesSummaryAsyncRegion")
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)

    def test_async_listado_no_calcula_summary_counts(self):
        rows = [_solicitud_stub(10, "SOL-A-10", "activa")]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)), \
                 patch("admin.routes._solicitudes_summary_counts") as summary_counts_mock:
                resp = self.client.get(
                    "/admin/solicitudes",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        summary_counts_mock.assert_not_called()

    def test_listado_solicitudes_expone_headers_baseline_minima(self):
        rows = [_solicitud_stub(10, "SOL-A-10", "activa")]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes", headers=self._async_headers(), follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("X-P1C1-Perf-Scope", resp.headers)
        self.assertIn("X-P1C1-Perf-Latency-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Queries", resp.headers)
        self.assertIn("X-P1C1-Perf-DB-Time-Ms", resp.headers)
        self.assertIn("X-P1C1-Perf-HTML-Bytes", resp.headers)

    def test_manejo_errores_async_conflicto_y_rate_limit(self):
        solicitud_conflict = _solicitud_stub(10, "SOL-010", "espera_pago")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud_conflict])):
                resp_conflict = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/solicitudes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp_conflict.status_code, 409)
        conflict_data = resp_conflict.get_json()
        self.assertFalse(conflict_data["success"])
        self.assertEqual(conflict_data["error_code"], "conflict")
        self.assertEqual(conflict_data.get("update_targets") or [], [])

        solicitud_rate = _solicitud_stub(11, "SOL-011", "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud_rate])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=redirect("/admin/solicitudes")):
                resp_rate = self.client.post(
                    "/admin/solicitudes/11/poner_espera_pago",
                    data={"next": "/admin/solicitudes?page=1"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp_rate.status_code, 429)
        rate_data = resp_rate.get_json()
        self.assertFalse(rate_data["success"])
        self.assertEqual(rate_data["error_code"], "rate_limit")
        self.assertEqual(rate_data.get("update_targets") or [], [])

    def test_cerrar_reemplazo_async_valida_estado_antes_de_ejecutar(self):
        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        repl = SimpleNamespace(id=99, solicitud_id=10, fecha_fin_reemplazo=None, candidata_old_id=1)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub(repl)):
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={
                        "candidata_new_id": "2",
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#clienteSolicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")
        self.assertEqual(data["update_target"], "#clienteSolicitudReemplazoActionsAsyncRegion-10")
        self.assertEqual(data.get("update_targets") or [], [])
        self.assertIn("replace_html", data)
        self.assertIn("Abrir reemplazo", data.get("replace_html") or "")

    def test_nuevo_reemplazo_async_bloqueado_no_rompe_por_fallback_y_respeta_target(self):
        self.client = flask_app.test_client()
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        solicitud.candidata = SimpleNamespace(fila=1, nombre_completo="Candidata 1", estado="trabajando")
        solicitud.candidata_id = 1
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=redirect("/admin/solicitudes")):
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={
                        "motivo_fallo": "No se presentó",
                        "next": "/admin/solicitudes?page=1",
                        "_async_target": "#solicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 429)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "rate_limit")
        self.assertEqual(data["update_target"], "#solicitudReemplazoActionsAsyncRegion-10")
        self.assertIn("replace_html", data)
        self.assertIn("Abrir reemplazo", data.get("replace_html") or "")

    def test_cancelar_reemplazo_async_respeta_target_cliente_detail(self):
        self.client = flask_app.test_client()
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        repl = SimpleNamespace(id=99, solicitud_id=10, fecha_fin_reemplazo=None, candidata_old_id=1)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub(repl)):
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#clienteSolicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")
        self.assertEqual(data["update_target"], "#clienteSolicitudReemplazoActionsAsyncRegion-10")
        self.assertEqual(data.get("update_targets") or [], [])
        self.assertIn("replace_html", data)
        self.assertIn("Abrir reemplazo", data.get("replace_html") or "")

    def test_cancelar_reemplazo_async_exitoso_refresca_region_padre_lista(self):
        self.client = flask_app.test_client()
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))
        solicitud = _solicitud_stub(10, "SOL-010", "reemplazo")

        class _ReplOk:
            id = 99
            solicitud_id = 10
            fecha_fin_reemplazo = None
            candidata_old_id = 1
            estado_previo_solicitud = "activa"

            def cerrar_reemplazo(self):
                self.fecha_fin_reemplazo = datetime(2026, 3, 27, 10, 0, 0)

        repl = _ReplOk()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub(repl)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={
                        "next": "/admin/solicitudes?page=1",
                        "_async_target": "#solicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#solicitudesAsyncRegion")
        update_targets = data.get("update_targets") or []
        self.assertEqual(len(update_targets), 2)
        self.assertEqual(update_targets[0].get("target"), "#solicitudesAsyncRegion")
        self.assertEqual(update_targets[1].get("target"), "#solicitudesSummaryAsyncRegion")
        self.assertIn("/admin/solicitudes?page=1", data["redirect_url"])
        self.assertIsNone(data.get("replace_html"))
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_cancelar_reemplazo_async_exitoso_refresca_region_padre_cliente(self):
        self.client = flask_app.test_client()
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))
        solicitud = _solicitud_stub(10, "SOL-010", "reemplazo")

        class _ReplOk:
            id = 99
            solicitud_id = 10
            fecha_fin_reemplazo = None
            candidata_old_id = 1
            estado_previo_solicitud = "activa"

            def cerrar_reemplazo(self):
                self.fecha_fin_reemplazo = datetime(2026, 3, 27, 10, 0, 0)

        repl = _ReplOk()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub(repl)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={
                        "next": "/admin/clientes/7#sol-10",
                        "_async_target": "#clienteSolicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#clienteSolicitudesAsyncRegion")
        update_targets = data.get("update_targets") or []
        self.assertEqual(len(update_targets), 2)
        self.assertEqual(update_targets[0].get("target"), "#clienteSolicitudesAsyncRegion")
        self.assertEqual(update_targets[1].get("target"), "#clienteSummaryAsyncRegion")
        self.assertIn("/admin/clientes/7/_solicitudes", update_targets[0].get("redirect_url") or "")
        self.assertIn("/admin/clientes/7/_summary", update_targets[1].get("redirect_url") or "")
        self.assertIn("/admin/clientes/7#sol-10", data["redirect_url"])
        self.assertIsNone(data.get("replace_html"))
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_quick_search_cierre_reemplazo_devuelve_items(self):
        rows = [
            SimpleNamespace(fila=2, nombre_completo="Ana Perez", codigo="COD-002", cedula="001-0000002-1"),
            SimpleNamespace(fila=3, nombre_completo="Maria Lopez", codigo="COD-003", cedula="001-0000003-1"),
        ]
        with patch("admin.routes._search_candidatas_reemplazo", return_value=rows):
            resp = self.client.get(
                "/admin/candidatas/reemplazo/quick-search?q=ana",
                headers=self._async_headers(),
                follow_redirects=False,
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["id"], 2)
        self.assertIn("Ana Perez", data["items"][0]["label"])
        self.assertIn("Código: COD-002", data["items"][0]["label"])
        self.assertIn("Cédula: 001-0000002-1", data["items"][0]["label"])

    def test_quick_search_cierre_reemplazo_q_corto_no_consulta(self):
        with patch("admin.routes._search_candidatas_reemplazo") as search_mock:
            resp = self.client.get(
                "/admin/candidatas/reemplazo/quick-search?q=a",
                headers=self._async_headers(),
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"], [])
        search_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
