# -*- coding: utf-8 -*-

import os
import re
import unittest
from datetime import datetime
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummyCliente:
    nombre_completo = "Cliente Demo"


class _DummySolicitud:
    id = 10
    codigo_solicitud = "SOL-010"
    cliente = _DummyCliente()
    estado = "activa"
    fecha_solicitud = datetime.utcnow()


class _DummyCandidata:
    def __init__(self, fila: int):
        self.fila = fila
        self.nombre_completo = f"Cand {fila}"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"


class _SolicitudQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _CandidataQuery:
    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def first(self):
        fila = int(self.kwargs.get("fila"))
        return _DummyCandidata(fila)


class _SolicitudCandidataQuery:
    def filter_by(self, **kwargs):
        return self

    def first(self):
        return None


class AdminMatchingRoutesTest(unittest.TestCase):
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

    def test_post_enviar_creates_relations(self):
        ranked = [
            {
                "candidate": _DummyCandidata(101),
                "score": 88,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "breakdown": [{"title": "Horario", "score": 23, "notes": "ok"}],
                "breakdown_snapshot": {
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: villa, maria, santiago",
                    "rutas_match": "Rutas: cienfuegos",
                    "modalidad_match": "Modalidad compatible",
                    "horario_match": "Horario compatible: 8am-5pm",
                    "skills_match": "Habilidades/funciones: limpieza",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +12",
                },
            },
            {
                "candidate": _DummyCandidata(102),
                "score": 75,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "breakdown": [{"title": "Horario", "score": 20, "notes": "ok"}],
                "breakdown_snapshot": {
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: santiago",
                    "rutas_match": "Rutas: av x",
                    "modalidad_match": "Modalidad parcialmente compatible",
                    "horario_match": "Horario compatible: 8am-5pm",
                    "skills_match": "Habilidades/funciones: limpieza",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +9",
                },
            },
        ]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101", "102"]},
                    follow_redirects=False,
                )

            self.assertEqual(resp.status_code, 302)
            self.assertEqual(add_mock.call_count, 2)
            commit_mock.assert_called_once()

            first_row = add_mock.call_args_list[0][0][0]
            self.assertIsNotNone(first_row.score_snapshot)
            self.assertGreater(first_row.score_snapshot, 0)
            self.assertIsInstance(first_row.breakdown_snapshot, dict)
            self.assertTrue(bool(first_row.breakdown_snapshot))
            self.assertIn("city_detectada", first_row.breakdown_snapshot)

    def test_post_enviar_with_csrf_token_returns_redirect_not_400(self):
        flask_app.config["WTF_CSRF_ENABLED"] = True

        ranked = [
            {
                "candidate": _DummyCandidata(101),
                "score": 88,
                "operational_score": 80,
                "bonus_test": 8,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "reasons": [],
                "breakdown": [{"title": "Modalidad", "score": 20, "notes": "ok"}],
                "breakdown_snapshot": {
                    "component_rows": [{"title": "Modalidad", "score": 20, "notes": "ok"}],
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: santiago",
                    "rutas_match": "Rutas: cienfuegos",
                    "modalidad_match": "Modalidad compatible",
                    "horario_match": "Horario compatible",
                    "skills_match": "Funciones compatibles",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +8",
                },
            }
        ]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                get_resp = self.client.get("/admin/matching/solicitudes/10")
                self.assertEqual(get_resp.status_code, 200)

                html = get_resp.get_data(as_text=True)
                m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
                self.assertIsNotNone(m, "No se encontró csrf_token en el HTML de matching_detalle")
                csrf_token = m.group(1)

                post_resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"csrf_token": csrf_token, "candidata_ids": ["101"]},
                    follow_redirects=False,
                )

            self.assertIn(post_resp.status_code, (302, 303))
            add_mock.assert_called_once()
            commit_mock.assert_called_once()

    def test_post_enviar_without_csrf_token_returns_400(self):
        flask_app.config["WTF_CSRF_ENABLED"] = True

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()):
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
