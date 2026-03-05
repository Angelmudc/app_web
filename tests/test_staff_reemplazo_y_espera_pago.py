# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect

from app import app as flask_app
import admin.routes as admin_routes


class _DummyField:
    def __init__(self, data):
        self.data = data


class _DummyCandidata:
    def __init__(self, fila=1, estado="lista_para_trabajar"):
        self.fila = fila
        self.estado = estado
        self.nombre_completo = f"Candidata {fila}"
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None
        self.nota_descalificacion = None
        self.codigo = f"C-{fila}"
        self.cedula = "001-0000000-1"
        self.numero_telefono = "8090000000"
        self.monto_total = None
        self.porciento = None
        self.fecha_de_pago = None


class _DummySolicitud:
    def __init__(self, estado="activa", candidata=None):
        self.id = 10
        self.cliente_id = 7
        self.codigo_solicitud = "SOL-010"
        self.estado = estado
        self.candidata = candidata
        self.candidata_id = getattr(candidata, "fila", None)
        self.fecha_solicitud = datetime.utcnow()
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None
        self.reemplazos = []
        self.sueldo = None
        self.monto_pagado = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None


class _DummyReemplazo:
    _id_seq = 100

    def __init__(self, **kwargs):
        _DummyReemplazo._id_seq += 1
        self.id = _DummyReemplazo._id_seq
        self.solicitud_id = kwargs.get("solicitud_id")
        self.candidata_old_id = kwargs.get("candidata_old_id")
        self.motivo_fallo = kwargs.get("motivo_fallo")
        self.nota_adicional = kwargs.get("nota_adicional")
        self.estado_previo_solicitud = kwargs.get("estado_previo_solicitud")
        self.fecha_fallo = kwargs.get("fecha_fallo")
        self.fecha_inicio_reemplazo = None
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = False
        self.candidata_new_id = None

    def iniciar_reemplazo(self):
        self.fecha_inicio_reemplazo = datetime.utcnow()
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = True

    def cerrar_reemplazo(self, candidata_nueva_id=None):
        self.fecha_fin_reemplazo = datetime.utcnow()
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


class _SolicitudQueryByObject:
    def __init__(self, solicitud):
        self.solicitud = solicitud

    def options(self, *args, **kwargs):
        return self

    def get_or_404(self, *_args, **_kwargs):
        return self.solicitud

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.solicitud


class _ReemplazoQueryByObject:
    def __init__(self, reemplazo):
        self.reemplazo = reemplazo

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.reemplazo


class _CandidataQueryPago:
    def __init__(self, cand):
        self.cand = cand

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return [self.cand]

    def get(self, _id):
        return self.cand

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.cand

    def first_or_404(self):
        return self.cand


class _FakePagoForm:
    def __init__(self, *args, **kwargs):
        self.candidata_id = _DummyField(1)
        self.monto_pagado = _DummyField("1000")

    def validate_on_submit(self):
        return True


class _FakeReemplazoOpenForm:
    def __init__(self, *args, **kwargs):
        self.motivo_fallo = _DummyField("No se presentó")
        self.nota_adicional = _DummyField("")
        self.candidata_old_id = _DummyField("1")
        self.candidata_old_name = _DummyField("Candidata 1")

    def validate_on_submit(self):
        return True


class _PublicableSolicitudQuery:
    def __init__(self, dataset):
        self.dataset = list(dataset)
        self._publicable_only = False

    def options(self, *args, **kwargs):
        return self

    def filter(self, *criteria):
        for crit in criteria:
            txt = str(crit)
            if "estado" in txt and "activa" in txt and "reemplazo" in txt:
                self._publicable_only = True
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return [s for s in self.dataset if getattr(s, "estado", None) in ("activa", "reemplazo")]


class StaffReemplazoEsperaPagoTest(unittest.TestCase):
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

    def _login(self, usuario, clave):
        resp = self.client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def test_asignar_candidata_en_solicitud_la_pasa_a_trabajando(self):
        self._login("Cruz", "8998")
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")
        sol = _DummySolicitud(estado="activa", candidata=None)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryPago(cand)), \
                 patch("admin.routes.AdminPagoForm", _FakePagoForm), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/clientes/7/solicitudes/10/pago", data={"csrf_token": "ok"}, follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(sol.candidata_id, 1)
        self.assertEqual(cand.estado, "trabajando")
        commit_mock.assert_called_once()

    def test_bloquear_asignacion_si_candidata_descalificada(self):
        self._login("Cruz", "8998")
        cand = _DummyCandidata(fila=1, estado="descalificada")
        sol = _DummySolicitud(estado="activa", candidata=None)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryPago(cand)), \
                 patch("admin.routes.AdminPagoForm", _FakePagoForm), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=redirect("/blocked")), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/clientes/7/solicitudes/10/pago", data={"csrf_token": "ok"}, follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/blocked", resp.location)
        self.assertEqual(cand.estado, "descalificada")
        commit_mock.assert_not_called()

    def test_abrir_reemplazo_crea_registro_y_lo_activa(self):
        self._login("Cruz", "8998")
        cand_old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=cand_old)
        captured = {}

        def _capture_add(obj):
            captured["reemplazo"] = obj

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoOpenForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.db.session.add", side_effect=_capture_add), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/reemplazos/nuevo", data={"motivo_fallo": "No se presentó"}, follow_redirects=False)

        r = captured.get("reemplazo")
        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNotNone(r)
        self.assertIsNotNone(r.fecha_inicio_reemplazo)
        self.assertTrue(r.oportunidad_nueva)
        self.assertEqual(sol.estado, "reemplazo")
        commit_mock.assert_called_once()

    def test_abrir_reemplazo_con_descalificacion_marca_old_descalificada(self):
        self._login("Cruz", "8998")
        cand_old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=cand_old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoOpenForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={
                        "motivo_fallo": "No se presentó",
                        "descalificar_candidata_fallida": "1",
                        "motivo_descalificacion": "Incumplimiento confirmado",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(cand_old.estado, "descalificada")
        self.assertEqual(cand_old.nota_descalificacion, "Incumplimiento confirmado")
        commit_mock.assert_called_once()

    def test_abrir_reemplazo_sin_descalificar_y_readiness_ok_pasa_a_lista(self):
        self._login("Cruz", "8998")
        cand_old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=cand_old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoOpenForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(True, [])), \
                 patch("admin.routes.log_candidata_action") as log_mock, \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_ok_mock:
                resp_ok = self.client.post("/admin/solicitudes/10/reemplazos/nuevo", data={"motivo_fallo": "No se presentó"}, follow_redirects=False)

        self.assertIn(resp_ok.status_code, (302, 303))
        self.assertEqual(cand_old.estado, "lista_para_trabajar")
        commit_ok_mock.assert_called_once()
        mark_logs = [c.kwargs for c in log_mock.call_args_list if c.kwargs.get("action_type") == "CANDIDATA_MARK_LISTA"]
        self.assertEqual(len(mark_logs), 1)
        self.assertEqual(mark_logs[0].get("metadata", {}).get("reason"), "readiness_ok")
        self.assertEqual(mark_logs[0].get("metadata", {}).get("source"), "auto")
        self.assertEqual(mark_logs[0].get("metadata", {}).get("faltantes"), [])

    def test_abrir_reemplazo_sin_descalificar_y_readiness_fail_no_cambia_estado(self):
        self._login("Cruz", "8998")
        cand_old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=cand_old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoOpenForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(False, ["Falta documento requerido: cedula2."])), \
                 patch("admin.routes.flash") as flash_mock, \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post("/admin/solicitudes/10/reemplazos/nuevo", data={"motivo_fallo": "No se presentó"}, follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(cand_old.estado, "trabajando")
        self.assertTrue(any("Falta documento requerido" in str(c.args[0]) for c in flash_mock.call_args_list))
        commit_mock.assert_called_once()

    def test_cancelar_reemplazo_lo_cierra_y_restaura_estado(self):
        self._login("Cruz", "8998")
        sol = _DummySolicitud(estado="reemplazo")
        r = _DummyReemplazo(solicitud_id=sol.id, candidata_old_id=1, motivo_fallo="x", estado_previo_solicitud="activa")
        r.iniciar_reemplazo()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(r)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(f"/admin/solicitudes/{sol.id}/reemplazos/{r.id}/cancelar", follow_redirects=False)

        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNotNone(r.fecha_fin_reemplazo)
        self.assertFalse(r.oportunidad_nueva)
        self.assertEqual(sol.estado, "activa")
        commit_mock.assert_called_once()

    def test_cerrar_reemplazo_asignando_nueva_candidata(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="reemplazo")
        old = _DummyCandidata(fila=1, estado="trabajando")
        new = _DummyCandidata(fila=2, estado="lista_para_trabajar")
        sol.candidata = old
        sol.candidata_id = old.fila
        r = _DummyReemplazo(solicitud_id=sol.id, candidata_old_id=old.fila, motivo_fallo="x", estado_previo_solicitud="proceso")
        r.iniciar_reemplazo()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(r)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryPago(new)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    f"/admin/solicitudes/{sol.id}/reemplazos/{r.id}/cerrar_asignando",
                    data={"candidata_new_id": "2"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(r.candidata_new_id, 2)
        self.assertEqual(sol.candidata_id, 2)
        self.assertEqual(sol.estado, "proceso")
        self.assertEqual(new.estado, "trabajando")
        commit_mock.assert_called_once()

    def test_admin_y_secretaria_pueden_poner_espera_pago(self):
        sol_admin = _DummySolicitud(estado="activa")
        self._login("Cruz", "8998")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: sol_admin)), \
                 patch("admin.routes.db.session.commit") as commit_admin:
                resp_admin = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/clientes/7/solicitudes/10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_admin.status_code, (302, 303))
        self.assertEqual(sol_admin.estado, "espera_pago")
        commit_admin.assert_called_once()

        sol_sec = _DummySolicitud(estado="activa")
        client_sec = flask_app.test_client()
        login_sec = client_sec.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login_sec.status_code, (302, 303))
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: sol_sec)), \
                 patch("admin.routes.db.session.commit") as commit_sec:
                resp_sec = client_sec.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/clientes/7/solicitudes/10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_sec.status_code, (302, 303))
        self.assertEqual(sol_sec.estado, "espera_pago")
        commit_sec.assert_called_once()

    def test_espera_pago_no_aparece_en_publicables_y_al_quitar_vuelve(self):
        self._login("Karla", "9989")
        s1 = SimpleNamespace(id=1, estado="activa", cliente=SimpleNamespace(nombre_completo="A"), codigo_solicitud="SOL-1")
        s2 = SimpleNamespace(id=2, estado="espera_pago", cliente=SimpleNamespace(nombre_completo="B"), codigo_solicitud="SOL-2")
        captured = {}

        def _render(_tpl, **ctx):
            captured["solicitudes"] = ctx.get("solicitudes") or []
            return "OK"

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _PublicableSolicitudQuery([s1, s2])), \
                 patch("admin.routes.render_template", side_effect=_render):
                resp_list = self.client.get("/admin/matching/solicitudes")

        self.assertEqual(resp_list.status_code, 200)
        ids = [x.id for x in captured.get("solicitudes", [])]
        self.assertIn(1, ids)
        self.assertNotIn(2, ids)

        sol = _DummySolicitud(estado="espera_pago")
        sol.estado_previo_espera_pago = "activa"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: sol)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp_restore = self.client.post(
                    "/admin/solicitudes/10/quitar_espera_pago",
                    data={"next": "/admin/clientes/7/solicitudes/10"},
                    follow_redirects=False,
                )

        self.assertIn(resp_restore.status_code, (302, 303))
        self.assertEqual(sol.estado, "activa")
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
