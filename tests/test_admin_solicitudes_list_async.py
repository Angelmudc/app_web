# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect, request

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

    def test_accion_por_fila_async_poner_espera_pago(self):
        solicitud = _solicitud_stub(10, "SOL-010", "activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
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
        self.assertIn("/admin/solicitudes?page=1", data["redirect_url"])
        commit_mock.assert_called_once()

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
        commit_mock.assert_called_once()

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
        commit_mock.assert_called_once()

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
        self.assertIn("/admin/clientes/7#sol-10", data["redirect_url"])
        self.assertIsNone(data.get("replace_html"))
        commit_mock.assert_called_once()

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
