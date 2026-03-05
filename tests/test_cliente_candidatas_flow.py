# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class _DummySolicitud:
    id = 10
    cliente_id = 7
    codigo_solicitud = "SOL-010"
    candidata_id = None
    candidata = None


class _DummyCandidata:
    def __init__(self, codigo=None):
        self.fila = 501
        self.codigo = codigo
        self.nombre_completo = "Ana Perez"
        self.edad = "31"
        self.modalidad_trabajo_preferida = "salida diaria"
        self.numero_telefono = "8091112222"
        self.cedula = "001-0000000-1"
        self.direccion_completa = "Calle Secreta 123, Santiago"


class _DummySolicitudCandidata:
    def __init__(self, status="enviada", codigo_candidata="C-501"):
        self.id = 44
        self.solicitud_id = 10
        self.candidata_id = 501
        self.status = status
        self.score_snapshot = 86
        self.breakdown_snapshot = {
            "city_detectada": "Ciudad detectada: Santiago",
            "tokens_match": "Tokens coinciden: villa, maria, santiago",
            "rutas_match": "Rutas: cienfuegos",
            "modalidad_match": "Modalidad compatible",
            "horario_match": "Horario compatible",
            "skills_match": ["limpieza", "cocina"],
            "edad_match": True,
            "mascota_penalty": "Sin penalizacion por mascotas",
        }
        self.candidata = _DummyCandidata(codigo=codigo_candidata)


class _SCListQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class ClienteCandidatasFlowTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def test_flow_lista_detalle_whatsapp_descartar(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        solicitud = _DummySolicitud()
        sc = _DummySolicitudCandidata(status="enviada", codigo_candidata=None)

        list_target = clientes_routes.solicitud_candidatas
        detail_target = clientes_routes.solicitud_candidata_detalle
        wa_target = clientes_routes.solicitar_entrevista_whatsapp
        desc_target = clientes_routes.descartar_candidata_enviada
        for _ in range(2):
            list_target = list_target.__wrapped__
            detail_target = detail_target.__wrapped__
            wa_target = wa_target.__wrapped__
            desc_target = desc_target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
                 patch.object(clientes_routes.SolicitudCandidata, "query", _SCListQuery([sc])):
                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas", method="GET"):
                    list_resp = list_target(10)

            self.assertIsInstance(list_resp, str)
            html = list_resp
            self.assertIn("Ana Perez", html)
            self.assertNotIn("8091112222", html)
            self.assertNotIn("001-0000000-1", html)

            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_cliente_sc_or_404", return_value=(solicitud, sc)), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas/44", method="GET"):
                    det_resp = detail_target(10, 44)
                self.assertIsInstance(det_resp, str)
                self.assertEqual(sc.status, "vista")
                commit_mock.assert_called_once()

                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas/44", method="GET"):
                    _ = detail_target(10, 44)
                self.assertEqual(commit_mock.call_count, 1)

            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_cliente_sc_or_404", return_value=(solicitud, sc)), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context(
                    "/clientes/solicitudes/10/candidatas/44/solicitar-entrevista",
                    method="POST",
                    data={"csrf_token": "ok"},
                ):
                    wa_resp = wa_target(10, 44)

                self.assertEqual(wa_resp.status_code, 302)
                self.assertEqual(sc.status, "seleccionada")
                commit_mock.assert_called_once()
                self.assertIn("wa.me/18094296892?text=", wa_resp.location)
                msg = unquote(wa_resp.location.split("text=", 1)[1])
                self.assertIn("Hola, quiero entrevistar a la candidata Ana Perez ((sin código)).", msg)
                self.assertIn("Solicitud SOL-010.", msg)

            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_cliente_sc_or_404", return_value=(solicitud, sc)), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context(
                    "/clientes/solicitudes/10/candidatas/44/descartar",
                    method="POST",
                    data={"csrf_token": "ok"},
                ):
                    desc_resp = desc_target(10, 44)

                self.assertEqual(desc_resp.status_code, 302)
                self.assertTrue(desc_resp.location.endswith("/clientes/solicitudes/10/candidatas"))
                self.assertEqual(sc.status, "descartada")
                self.assertEqual(sc.breakdown_snapshot.get("client_action"), "rechazada")
                self.assertIn("client_action_at", sc.breakdown_snapshot)
                commit_mock.assert_called_once()

    def test_descartada_no_aparece_en_listado_cliente(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        solicitud = _DummySolicitud()
        visible = _DummySolicitudCandidata(status="enviada", codigo_candidata="C-501")
        hidden = _DummySolicitudCandidata(status="descartada", codigo_candidata="C-999")
        hidden.candidata.nombre_completo = "Oculta"

        list_target = clientes_routes.solicitud_candidatas
        for _ in range(2):
            list_target = list_target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
                 patch.object(clientes_routes.SolicitudCandidata, "query", _SCListQuery([visible, hidden])):
                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas", method="GET"):
                    html = list_target(10)

        self.assertIn("Ana Perez", html)
        self.assertNotIn("Oculta", html)

    def test_liberada_no_aparece_y_si_hay_asignada_solo_muestra_asignada(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        solicitud = _DummySolicitud()
        solicitud.candidata_id = 501
        solicitud.candidata = _DummyCandidata(codigo="C-501")

        assigned = _DummySolicitudCandidata(status="seleccionada", codigo_candidata="C-501")
        liberated = _DummySolicitudCandidata(status="liberada", codigo_candidata="C-777")
        liberated.candidata.nombre_completo = "No Visible"

        list_target = clientes_routes.solicitud_candidatas
        for _ in range(2):
            list_target = list_target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
                 patch.object(clientes_routes.SolicitudCandidata, "query", _SCListQuery([assigned, liberated])):
                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas", method="GET"):
                    html = list_target(10)

        self.assertIn("Candidata asignada", html)
        self.assertIn("Ana Perez", html)
        self.assertNotIn("No Visible", html)

    def test_post_without_csrf_token_returns_400(self):
        flask_app.config["WTF_CSRF_ENABLED"] = True
        client = flask_app.test_client()
        resp = client.post(
            "/clientes/solicitudes/10/candidatas/44/solicitar-entrevista",
            data={},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
