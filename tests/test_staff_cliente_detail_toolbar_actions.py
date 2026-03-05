# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect

from app import app as flask_app
import admin.routes as admin_routes


class _DummyCandidata:
    def __init__(self, fila=1, estado="trabajando"):
        self.fila = fila
        self.estado = estado
        self.nombre_completo = f"Candidata {fila}"
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None
        self.nota_descalificacion = None


class _DummySolicitud:
    def __init__(self, estado="activa", candidata=None):
        self.id = 10
        self.cliente_id = 7
        self.estado = estado
        self.codigo_solicitud = "SOL-010"
        self.candidata = candidata
        self.candidata_id = getattr(candidata, "fila", None)
        self.fecha_solicitud = datetime.utcnow()
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.reemplazos = []


class _DummyReemplazo:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 99)
        self.solicitud_id = kwargs.get("solicitud_id", 10)
        self.candidata_old_id = kwargs.get("candidata_old_id", 1)
        self.motivo_fallo = kwargs.get("motivo_fallo", "")
        self.estado_previo_solicitud = kwargs.get("estado_previo_solicitud", "activa")
        self.fecha_inicio_reemplazo = None
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = False
        self.candidata_new_id = None
        self.fecha_fallo = None

    def iniciar_reemplazo(self):
        self.fecha_inicio_reemplazo = datetime.utcnow()
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = True

    def cerrar_reemplazo(self, candidata_nueva_id=None):
        self.fecha_fin_reemplazo = datetime.utcnow()
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


class _SolicitudQuery:
    def __init__(self, sol):
        self.sol = sol

    def options(self, *args, **kwargs):
        return self

    def get_or_404(self, *_args, **_kwargs):
        return self.sol

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.sol


class _ReemplazoQuery:
    def __init__(self, repl):
        self.repl = repl

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.repl


class _CandidataQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.cand


class _FakeReemplazoForm:
    def __init__(self, *args, **kwargs):
        self.motivo_fallo = SimpleNamespace(data="No se presentó")
        self.nota_adicional = SimpleNamespace(data="")
        self.candidata_old_id = SimpleNamespace(data="1")
        self.candidata_old_name = SimpleNamespace(data="Candidata 1")

    def validate_on_submit(self):
        return True


class ClienteDetailToolbarActionsTest(unittest.TestCase):
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

    def test_1_espera_pago_toggle_desde_cliente_detail(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="activa")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(filter_by=lambda **k: SimpleNamespace(first_or_404=lambda: sol))), \
                 patch("admin.routes.db.session.commit") as c1:
                r1 = self.client.post(
                    "/admin/clientes/7/solicitudes/10/espera_pago/poner",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(r1.status_code, (302, 303))
        self.assertEqual(sol.estado, "espera_pago")
        c1.assert_called_once()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(filter_by=lambda **k: SimpleNamespace(first_or_404=lambda: sol))), \
                 patch("admin.routes.db.session.commit") as c2:
                r2 = self.client.post(
                    "/admin/clientes/7/solicitudes/10/espera_pago/quitar",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(r2.status_code, (302, 303))
        self.assertEqual(sol.estado, "activa")
        c2.assert_called_once()

    def test_2_abrir_reemplazo_desde_modal_crea_activo(self):
        self._login("Cruz", "8998")
        old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=old)
        captured = {}

        def _capture_add(obj):
            captured["r"] = obj

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(True, [])), \
                 patch("admin.routes.db.session.add", side_effect=_capture_add), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={"next": "/admin/clientes/7#sol-10", "motivo_fallo": "No se presentó"},
                    follow_redirects=False,
                )
        r = captured.get("r")
        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNotNone(r)
        self.assertTrue(r.oportunidad_nueva)
        self.assertIsNotNone(r.fecha_inicio_reemplazo)
        self.assertEqual(sol.estado, "reemplazo")
        commit_mock.assert_called_once()

    def test_3_abrir_reemplazo_descalificando_old(self):
        self._login("Cruz", "8998")
        old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=old)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoForm), \
                 patch("admin.routes.Reemplazo", _DummyReemplazo), \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={
                        "next": "/admin/clientes/7#sol-10",
                        "motivo_fallo": "No se presentó",
                        "descalificar_candidata_fallida": "1",
                        "motivo_descalificacion": "Incumplimiento",
                    },
                    follow_redirects=False,
                )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(old.estado, "descalificada")
        self.assertEqual(old.nota_descalificacion, "Incumplimiento")
        commit_mock.assert_called_once()

    def test_4_cancelar_reemplazo_solo_admin(self):
        sol = _DummySolicitud(estado="reemplazo")
        repl = _DummyReemplazo(id=99, solicitud_id=10, estado_previo_solicitud="activa")
        repl.iniciar_reemplazo()

        self._login("Karla", "9989")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)):
                forbidden = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertEqual(forbidden.status_code, 403)

        self.client = flask_app.test_client()
        self._login("Cruz", "8998")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                ok = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(ok.status_code, (302, 303))
        self.assertIn("/admin/clientes/7#sol-10", ok.location)
        self.assertEqual(sol.estado, "activa")
        self.assertIsNotNone(repl.fecha_fin_reemplazo)
        commit_mock.assert_called_once()

    def test_5_culminar_reemplazo_asigna_nueva_y_trabajando(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="reemplazo", candidata=_DummyCandidata(fila=1, estado="trabajando"))
        repl = _DummyReemplazo(id=99, solicitud_id=10, estado_previo_solicitud="activa")
        repl.iniciar_reemplazo()
        new = _DummyCandidata(fila=2, estado="lista_para_trabajar")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(new)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"next": "/admin/clientes/7#sol-10", "candidata_new_id": "2"},
                    follow_redirects=False,
                )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(sol.candidata_id, 2)
        self.assertEqual(new.estado, "trabajando")
        commit_mock.assert_called_once()

    def test_6_bloquear_culminar_si_nueva_descalificada(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="reemplazo", candidata=_DummyCandidata(fila=1, estado="trabajando"))
        repl = _DummyReemplazo(id=99, solicitud_id=10, estado_previo_solicitud="activa")
        repl.iniciar_reemplazo()
        bad = _DummyCandidata(fila=3, estado="descalificada")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(bad)), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=redirect("/blocked")), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"next": "/admin/clientes/7#sol-10", "candidata_new_id": "3"},
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/blocked", resp.location)
        self.assertEqual(sol.candidata_id, 1)
        commit_mock.assert_not_called()

    def test_7_solicitud_detail_sin_forms_operativos_reemplazo(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitud_detail.html")
        with open(tpl_path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        self.assertNotIn("Abrir reemplazo", txt)
        self.assertNotIn("Volver a Lista para Trabajar", txt)
        self.assertNotIn("Cerrar reemplazo asignando nueva candidata", txt)


if __name__ == "__main__":
    unittest.main()
