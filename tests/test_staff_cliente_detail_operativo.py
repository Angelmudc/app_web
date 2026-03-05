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
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.reemplazos = []


class _DummyReemplazo:
    def __init__(self):
        self.id = 99
        self.solicitud_id = 10
        self.fecha_inicio_reemplazo = datetime.utcnow()
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = True
        self.candidata_new_id = None
        self.estado_previo_solicitud = "activa"

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


class ClienteDetailOperativoTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    def _login(self, user, pwd):
        resp = self.client.post("/admin/login", data={"usuario": user, "clave": pwd}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def test_espera_pago_desde_cliente_detail_set_y_restore(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: sol)), \
                 patch("admin.routes.db.session.commit") as commit_1:
                resp_1 = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_1.status_code, (302, 303))
        self.assertIn("/admin/clientes/7#sol-10", resp_1.location)
        self.assertEqual(sol.estado, "espera_pago")
        commit_1.assert_called_once()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: sol)), \
                 patch("admin.routes.db.session.commit") as commit_2:
                resp_2 = self.client.post(
                    "/admin/solicitudes/10/quitar_espera_pago",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_2.status_code, (302, 303))
        self.assertIn("/admin/clientes/7#sol-10", resp_2.location)
        self.assertEqual(sol.estado, "activa")
        commit_2.assert_called_once()

    def test_abrir_reemplazo_desde_cliente_detail_con_descalificacion(self):
        self._login("Cruz", "8998")
        old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoForm), \
                 patch("admin.routes.Reemplazo", lambda **kwargs: _DummyReemplazo()), \
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
        self.assertIn("/admin/clientes/7#sol-10", resp.location)
        self.assertEqual(old.estado, "descalificada")
        self.assertEqual(old.nota_descalificacion, "Incumplimiento")
        commit_mock.assert_called_once()

    def test_abrir_reemplazo_desde_cliente_detail_sin_descalificar_readiness_ok(self):
        self._login("Cruz", "8998")
        old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoForm), \
                 patch("admin.routes.Reemplazo", lambda **kwargs: _DummyReemplazo()), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(True, [])), \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={"next": "/admin/clientes/7#sol-10", "motivo_fallo": "No se presentó"},
                    follow_redirects=False,
                )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(old.estado, "lista_para_trabajar")
        commit_mock.assert_called_once()

    def test_abrir_reemplazo_desde_cliente_detail_sin_descalificar_readiness_fail(self):
        self._login("Cruz", "8998")
        old = _DummyCandidata(fila=1, estado="trabajando")
        sol = _DummySolicitud(estado="activa", candidata=old)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch("admin.routes.AdminReemplazoForm", _FakeReemplazoForm), \
                 patch("admin.routes.Reemplazo", lambda **kwargs: _DummyReemplazo()), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(False, ["Falta codigo."])), \
                 patch("admin.routes.flash") as flash_mock, \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/nuevo",
                    data={"next": "/admin/clientes/7#sol-10", "motivo_fallo": "No se presentó"},
                    follow_redirects=False,
                )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(old.estado, "trabajando")
        self.assertTrue(any("Falta codigo" in str(c.args[0]) for c in flash_mock.call_args_list))
        commit_mock.assert_called_once()

    def test_cancelar_reemplazo_desde_cliente_detail_solo_admin(self):
        sol = _DummySolicitud(estado="reemplazo")
        repl = _DummyReemplazo()

        self._login("Karla", "9989")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)):
                resp_sec = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertEqual(resp_sec.status_code, 403)

        self.client = flask_app.test_client()
        self._login("Cruz", "8998")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp_admin = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cancelar",
                    data={"next": "/admin/clientes/7#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_admin.status_code, (302, 303))
        self.assertIn("/admin/clientes/7#sol-10", resp_admin.location)
        self.assertIsNotNone(repl.fecha_fin_reemplazo)
        self.assertEqual(sol.estado, "activa")
        commit_mock.assert_called_once()

    def test_culminar_reemplazo_desde_cliente_detail_ok_y_bloqueo_descalificada(self):
        self._login("Karla", "9989")
        sol = _DummySolicitud(estado="reemplazo", candidata=_DummyCandidata(fila=1, estado="trabajando"))
        repl = _DummyReemplazo()
        nueva = _DummyCandidata(fila=2, estado="lista_para_trabajar")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(nueva)), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes.db.session.commit") as commit_ok:
                resp_ok = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"next": "/admin/clientes/7#sol-10", "candidata_new_id": "2"},
                    follow_redirects=False,
                )
        self.assertIn(resp_ok.status_code, (302, 303))
        self.assertEqual(sol.candidata_id, 2)
        self.assertEqual(nueva.estado, "trabajando")
        commit_ok.assert_called_once()

        sol2 = _DummySolicitud(estado="reemplazo", candidata=_DummyCandidata(fila=1, estado="trabajando"))
        repl2 = _DummyReemplazo()
        bad = _DummyCandidata(fila=3, estado="descalificada")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol2)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQuery(repl2)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(bad)), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=redirect("/blocked")), \
                 patch("admin.routes.db.session.commit") as commit_block:
                resp_block = self.client.post(
                    "/admin/solicitudes/10/reemplazos/99/cerrar_asignando",
                    data={"next": "/admin/clientes/7#sol-10", "candidata_new_id": "3"},
                    follow_redirects=False,
                )
        self.assertEqual(resp_block.status_code, 302)
        self.assertIn("/blocked", resp_block.location)
        self.assertEqual(sol2.candidata_id, 1)
        commit_block.assert_not_called()


if __name__ == "__main__":
    unittest.main()
