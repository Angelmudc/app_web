# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummySolicitud:
    id = 10
    cliente_id = 7
    codigo_solicitud = "SOL-010"
    estado = "activa"
    fecha_solicitud = datetime.utcnow()


class _SolicitudQuery:
    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _DummyCandidata:
    def __init__(self, fila=101):
        self.fila = fila
        self.nombre_completo = "Ana Perez"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = f"C-{fila}"
        self.estado = "lista_para_trabajar"


class _CandidataQuery:
    def __init__(self, cand):
        self.cand = cand
        self.kwargs = {}

    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def all(self):
        return [self.cand]

    def first(self):
        return self.cand


class _SolicitudCandidataQuery:
    def __init__(self):
        self.kwargs = {}

    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def first(self):
        return None


class _ClienteNotificacionQuery:
    def filter_by(self, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


class AdminSendBlocksNotReadyTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

    def test_enviar_sin_docs_devuelve_400(self):
        cand = _DummyCandidata(101)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(cand)), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(False, ["Falta documento requerido: depuracion."])):
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        body = resp.get_data(as_text=True).lower()
        self.assertIn("no está lista para enviar", body)

    def test_enviar_ready_crea_solicitud_candidata(self):
        cand = _DummyCandidata(101)
        ranked = [{"candidate": cand, "score": 90, "breakdown_snapshot": {"x": "y"}, "breakdown": []}]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery(cand)), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.candidata_is_ready_to_send", return_value=(True, [])), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_called_once()
        self.assertGreaterEqual(add_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
