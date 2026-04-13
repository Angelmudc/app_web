# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import request

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudStub:
    def __init__(self, estado="activa"):
        self.id = 10
        self.cliente_id = 7
        self.estado = estado
        self.codigo_solicitud = "SOL-010"
        self.last_copiado_at = None
        self.fecha_solicitud = datetime.utcnow()
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.motivo_cancelacion = None
        self.fecha_cancelacion = None
        self.candidata_id = None
        self.monto_pagado = None
        self.reemplazos = []
        self.ciudad_sector = "Santiago"
        self.direccion = "Calle 1"
        self.modalidad_trabajo = "Con dormida"
        self.rutas_cercanas = "27 de Febrero"
        self.funciones = ["limpieza"]
        self.funciones_otro = ""
        self.tipo_lugar = "Casa"
        self.habitaciones = 3
        self.banos = 2
        self.dos_pisos = False
        self.areas_comunes = []
        self.area_otro = ""
        self.adultos = 2
        self.ninos = 0
        self.edades_ninos = ""
        self.mascota = ""
        self.edad_requerida = ["25-45"]
        self.experiencia = "Limpieza general"
        self.horario = "L-V"
        self.sueldo = "25000"
        self.pasaje_aporte = False
        self.nota_cliente = "Nota interna"
        self.detalles_servicio = {}
        self.tipo_servicio = "DOMESTICA_LIMPIEZA"


class _CandidataStub:
    def __init__(self, fila=33, nombre_completo="Ana Demo"):
        self.fila = fila
        self.estado = "trabajando"
        self.nombre_completo = nombre_completo
        self.nota_descalificacion = None
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None


class _QueryChain:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def count(self):
        return len(self.rows)

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class _DummyField:
    def __init__(self, choices):
        self.choices = choices


class _DummyForm:
    def __init__(self):
        self.funciones = _DummyField([("limpieza", "Limpieza")])
        self.ninera_tareas = _DummyField([])
        self.enf_tareas = _DummyField([])
        self.enf_movilidad = _DummyField([])


class _CandidataQueryChain:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _LookupCandidataQueryChain:
    def __init__(self, rows):
        self.rows = list(rows)
        self._q_applied = False

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        q = (request.args.get("q") or "").strip().lower()
        include_raw = (request.args.get("include_id") or "").strip()

        if q and not self._q_applied:
            self.rows = [
                r for r in self.rows
                if (q in (r.nombre_completo or "").lower()) or (q in str(r.fila))
            ]
            self._q_applied = True
            return self

        if include_raw and any("fila" in str(a) for a in args):
            try:
                include_id = int(include_raw)
            except Exception:
                return self
            self.rows = [r for r in self.rows if int(r.fila) == include_id]
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.rows = self.rows[:int(n)]
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class AdminCopiarActionsTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        self._p_lock = patch("admin.routes._admin_action_is_locked", return_value=False)
        self._p_reg = patch("admin.routes._admin_action_register", return_value=1)
        self._p_lock.start()
        self._p_reg.start()

    def tearDown(self):
        self._p_reg.stop()
        self._p_lock.stop()

    def _login(self, user, pwd):
        resp = self.client.post("/admin/login", data={"usuario": user, "clave": pwd}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def test_copiar_solicitudes_render_y_formato_base(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("data-order-text", html)
        self.assertIn("Dominicana", html)
        self.assertIn("Acciones", html)
        self.assertIn('data-no-loader="true"', html)
        self.assertIn('id="contextActionPanel"', html)
        self.assertIn('class="dropdown-item js-open-cancel"', html)
        self.assertIn("Cancelar solicitud", html)
        self.assertNotIn('data-bs-target="#cancelModalShared"', html)
        self.assertIn('id="cancelModalSharedForm" class="d-none" data-no-loader="true"', html)
        self.assertIn('<textarea class="form-control" name="motivo" rows="2" minlength="5" required></textarea>', html)
        self.assertIn('<button type="submit" class="btn btn-danger" data-no-loader="true">Confirmar cancelación</button>', html)
        self.assertIn('id="contextActionPanelClose"', html)

    def test_copiar_solicitudes_respeta_pasaje_texto_libre(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.pasaje_aporte = False
        solicitud.detalles_servicio = {"pasaje": {"mode": "otro", "text": "pasaje aparte por ruta"}}

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("pasaje aparte por ruta", html)

    def test_copiar_solicitudes_get_async_devuelve_reemplazo_parcial(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get(
                    "/admin/solicitudes/copiar?q=santiago&page=1",
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "X-Admin-Async": "1",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#copiarSolicitudesResults")
        self.assertIn("list-group-item", data["replace_html"])

    def test_build_resumen_cliente_solicitud_pasaje_usa_mensajes_claros(self):
        solicitud_false = _SolicitudStub(estado="activa")
        solicitud_false.pasaje_aporte = False
        solicitud_false.detalles_servicio = {}

        solicitud_true = _SolicitudStub(estado="activa")
        solicitud_true.pasaje_aporte = True
        solicitud_true.detalles_servicio = {}

        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen_false = admin_routes.build_resumen_cliente_solicitud(solicitud_false)
                resumen_true = admin_routes.build_resumen_cliente_solicitud(solicitud_true)

        self.assertIn("no incluye ayuda de pasaje", resumen_false)
        self.assertIn("incluye ayuda de pasaje", resumen_true)

    def test_pasaje_operativo_phrase_unifica_formato_con_copiado_interno(self):
        solicitud_incluido = _SolicitudStub(estado="activa")
        solicitud_incluido.pasaje_aporte = False
        solicitud_incluido.detalles_servicio = {}

        solicitud_aparte = _SolicitudStub(estado="activa")
        solicitud_aparte.pasaje_aporte = True
        solicitud_aparte.detalles_servicio = {}

        solicitud_otro = _SolicitudStub(estado="activa")
        solicitud_otro.pasaje_aporte = False
        solicitud_otro.detalles_servicio = {"pasaje": {"mode": "otro", "text": "Cliente cubre taxi nocturno"}}

        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_incluido),
            "Pasaje incluido",
        )
        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_aparte),
            "Más ayuda del pasaje",
        )
        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_otro),
            "Cliente cubre taxi nocturno",
        )

    def test_copiar_solicitudes_admin_usa_modal_pagado_compartido(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryChain([candidata])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="paidModalSharedForm"', html)
        self.assertIn('class="dropdown-item text-success js-open-paid"', html)
        self.assertNotIn('data-bs-target="#paidModalShared"', html)
        self.assertIn('data-no-loader="true"', html)
        self.assertIn('id="paidModalSharedSearch"', html)
        self.assertIn('placeholder="Escribe nombre o ID..."', html)
        self.assertIn('id="paidModalSharedSearchBtn"', html)
        self.assertIn('id="paidModalSharedClearBtn"', html)
        self.assertIn('id="paidModalSharedSearchStats"', html)
        self.assertIn('id="paidModalSharedSelected"', html)
        self.assertIn('class="alert d-none js-modal-feedback"', html)
        self.assertIn('id="paidModalSharedForm" class="d-none" data-lookup-url="/admin/solicitudes/copiar/candidatas_lookup" data-no-loader="true"', html)
        self.assertIn('<button type="submit" class="btn btn-success" data-no-loader="true">Guardar pago</button>', html)
        self.assertIn('data-lookup-url="/admin/solicitudes/copiar/candidatas_lookup"', html)
        self.assertIn("paidSearchBtn.addEventListener('click', () => lookupPaidCandidates(true, true, false));", html)
        self.assertIn('function clearUiLoaders()', html)

    def test_candidatas_lookup_copiar_busca_fuera_de_subconjunto_inicial(self):
        self._login("Cruz", "8998")
        rows = [_CandidataStub(fila=i, nombre_completo=f"Ana {i:03d}") for i in range(1, 401)]
        rows.append(_CandidataStub(fila=999, nombre_completo="Zoe Final"))

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _LookupCandidataQueryChain(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/copiar/candidatas_lookup?q=zoe&limit=50",
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["count"], 1)
        self.assertTrue(any("Zoe Final" in item["text"] for item in data["items"]))

    def test_candidatas_lookup_copiar_include_id_en_busqueda_vacia(self):
        self._login("Cruz", "8998")
        rows = [_CandidataStub(fila=450, nombre_completo="Candidata Incluida")]

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _LookupCandidataQueryChain(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/copiar/candidatas_lookup?include_id=450",
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["value"], "450")

    def test_copiar_solicitud_post_vuelve_misma_pantalla(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.last_copiado_at = datetime.utcnow() - timedelta(days=2)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/copiar",
                    data={"next": "/admin/solicitudes/copiar?page=2#sol-10"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=2#sol-10", resp.location)
        commit_mock.assert_called_once()

    def test_copiar_solicitud_ajax_devuelve_json_y_remueve_tarjeta(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.last_copiado_at = datetime.utcnow() - timedelta(days=2)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/copiar",
                    data={"next": "/admin/solicitudes/copiar?page=2#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["success"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertTrue(data["remove_card"])
        self.assertEqual(data["remove_element"], "#sol-10")
        self.assertIn("redirect_url", data)
        commit_mock.assert_called_once()

    def test_pausar_espera_perfil_ajax_devuelve_json_y_remueve_tarjeta(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/pausar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "espera_pago")
        self.assertTrue(data["remove_card"])
        self.assertEqual(data["remove_element"], "#sol-10")
        self.assertEqual(solicitud.estado, "espera_pago")
        commit_mock.assert_called_once()

    def test_reanudar_espera_perfil_ajax_usa_mismo_contrato_async(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="espera_pago")
        solicitud.estado_previo_espera_pago = "reemplazo"

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reanudar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["success"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "reemplazo")
        self.assertFalse(data["remove_card"])
        self.assertIsNone(data["remove_element"])
        self.assertEqual(solicitud.estado, "reemplazo")
        commit_mock.assert_called_once()

    def test_acciones_desde_copiar_cancelar_y_pausa_perfil(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_pause:
                resp_pause = self.client.post(
                    "/admin/solicitudes/10/pausar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_pause.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=1#sol-10", resp_pause.location)
        self.assertEqual(solicitud.estado, "espera_pago")
        commit_pause.assert_called_once()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp_cancel = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1#sol-10",
                    },
                    follow_redirects=False,
                )
        self.assertIn(resp_cancel.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=1#sol-10", resp_cancel.location)
        self.assertEqual(solicitud.estado, "cancelada")
        self.assertEqual(solicitud.motivo_cancelacion, "Cliente detuvo el proceso")
        commit_cancel.assert_called_once()

    def test_marcar_pagada_desde_copiar_se_queda_en_la_misma_ruta(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: candidata)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch("admin.routes._mark_candidata_estado") as mark_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50#sol-10",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?per_page=50#sol-10", resp.location)
        self.assertEqual(solicitud.estado, "pagada")
        self.assertEqual(solicitud.candidata_id, 33)
        self.assertEqual(solicitud.monto_pagado, "12000.00")
        sync_mock.assert_called_once()
        mark_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_cancelar_desde_copiar_ajax_devuelve_json(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertTrue(data["remove_card"])
        self.assertEqual(solicitud.estado, "cancelada")
        commit_cancel.assert_called_once()

    def test_cancelar_desde_copiar_estado_pagada_devuelve_409_con_mensaje_claro(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="pagada")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "state_conflict")
        self.assertIn("estado «pagada»", data.get("message", ""))
        commit_cancel.assert_not_called()

    def test_cancelar_desde_copiar_row_version_stale_devuelve_409_con_mensaje_claro(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.row_version = 9

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "8",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "conflict")
        self.assertIn("La solicitud cambió mientras trabajabas", data.get("message", ""))
        commit_cancel.assert_not_called()

    def test_pagado_desde_copiar_ajax_devuelve_json(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: candidata)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch("admin.routes._mark_candidata_estado") as mark_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "pagada")
        self.assertEqual(data["candidata_id"], 33)
        self.assertTrue(data["remove_card"])
        self.assertEqual(solicitud.estado, "pagada")
        self.assertEqual(solicitud.candidata_id, 33)
        sync_mock.assert_called_once()
        mark_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_pagado_desde_copiar_conflict_por_row_version_devuelve_409(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.row_version = 9

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "row_version": "8",
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "conflict")
        commit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
