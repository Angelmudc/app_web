# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

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
    def __init__(self, fila=33):
        self.fila = fila
        self.estado = "trabajando"
        self.nombre_completo = "Ana Demo"
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
        self.assertIn('id="cancelModalShared"', html)
        self.assertNotIn('id="cancelModal10"', html)
        self.assertIn('class="dropdown-item js-open-cancel"', html)
        self.assertNotIn('data-bs-target="#cancelModalShared"', html)
        self.assertIn('<textarea class="form-control" name="motivo" rows="3" minlength="5" required></textarea>', html)

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
        self.assertIn('id="paidModalShared"', html)
        self.assertNotIn('id="paidModal10"', html)
        self.assertIn('class="dropdown-item text-success js-open-paid"', html)
        self.assertNotIn('data-bs-target="#paidModalShared"', html)
        self.assertIn('data-no-loader="true"', html)
        self.assertIn('id="paidModalSharedSearch"', html)
        self.assertIn('placeholder="Escribe nombre o ID..."', html)
        self.assertIn('class="alert d-none js-modal-feedback"', html)

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


if __name__ == "__main__":
    unittest.main()
