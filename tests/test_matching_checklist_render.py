# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummyCliente:
    nombre_completo = "Cliente Demo"


class _DummySolicitud:
    id = 10
    cliente_id = 7
    codigo_solicitud = "SOL-010"
    cliente = _DummyCliente()
    estado = "activa"
    fecha_solicitud = datetime.utcnow()
    reemplazos = []


class _DummyCandidate:
    def __init__(self):
        self.fila = 101
        self.nombre_completo = "Cand Completa"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = "C-101"
        self.entrevista = "ok"
        self.depuracion = b"dep-ok"
        self.perfil = b"perfil-ok"
        self.cedula1 = b"ced1-ok"
        self.cedula2 = b"ced2-ok"


class _SolicitudQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _SolicitudCandidataQuery:
    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class MatchingChecklistRenderTest(unittest.TestCase):
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

    def test_matching_checklist_renderiza_labels_y_clase_css(self):
        cand = _DummyCandidate()
        ranked = [
            {
                "candidate": cand,
                "score": 92,
                "operational_score": 88,
                "bonus_test": 4,
                "level": "alta",
                "summary": "ok",
                "reasons": [],
                "risks": [],
                "breakdown": [],
                "breakdown_snapshot": {
                    "ready_check": {
                        "ready": True,
                        "has_code": True,
                        "has_interview": True,
                        "has_referencias_laboral": True,
                        "has_referencias_familiares": True,
                        "docs": {
                            "flags": {
                                "depuracion": True,
                                "perfil": True,
                                "cedula1": True,
                                "cedula2": True,
                            },
                            "required": {},
                        },
                    }
                },
            }
        ]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes._active_reemplazo_for_solicitud", return_value=None):
                resp = self.client.get("/admin/matching/solicitudes/10")

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Checklist listo para enviar", html)
        self.assertIn("matching-checklist-card", html)
        self.assertNotIn("Foto perfil", html)

        for label in (
            "Código:",
            "Entrevista:",
            "Depuración:",
            "Perfil:",
            "Cédula 1:",
            "Cédula 2:",
            "Referencias (Laboral/Familiar):",
        ):
            self.assertIn(label, html)


if __name__ == "__main__":
    unittest.main()
