# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

from app import app as flask_app
import core.legacy_handlers as legacy_handlers


class _DummyQuery:
    def __init__(self, obj):
        self._obj = obj

    def get_or_404(self, _fila):
        return self._obj


class _DummyCandidata:
    def __init__(self):
        self.fila = 1
        self.nombre_completo = "Candidata Demo"
        self.compat_ritmo_preferido = None
        self.compat_estilo_trabajo = None
        self.compat_relacion_ninos = None
        self.compat_mascotas = None
        self.compat_mascotas_ok = None
        self.compat_fortalezas = []
        self.compat_tareas_evitar = []
        self.compat_limites_no_negociables = []
        self.compat_disponibilidad_dias = []
        self.compat_disponibilidad_horarios = []
        self.compat_observaciones = None
        self.compat_test_candidata_json = None


class CompatCandidataMascotasImportanciaTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()

    def test_secretaria_post_guarda_mascotas_importancia_en_json(self):
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

        dummy = _DummyCandidata()

        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", _DummyQuery(dummy)), \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/secretarias/compat/candidata",
                    data={
                        "accion": "guardar",
                        "fila": "1",
                        "ritmo": "activo",
                        "estilo": "toma_iniciativa",
                        "comunicacion": "mixta",
                        "relacion_ninos": "comoda",
                        "experiencia_nivel": "alta",
                        "puntualidad_1a5": "4",
                        "mascotas": "si",
                        "mascotas_importancia": "alta",
                        "fortalezas": ["limpieza_general"],
                        "disponibilidad_dias": ["lun"],
                        "disponibilidad_horarios": ["8am-5pm"],
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_called_once()
        self.assertEqual(dummy.compat_test_candidata_json.get("profile", {}).get("mascotas_importancia"), "alta")

    def test_secretaria_get_rehidrata_select_mascotas_importancia(self):
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

        dummy = _DummyCandidata()
        dummy.compat_test_candidata_json = {
            "profile": {
                "mascotas": "si",
                "mascotas_importancia": "media",
            }
        }

        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", _DummyQuery(dummy)):
                resp = self.client.get("/secretarias/compat/candidata?fila=1", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn('name="mascotas_importancia"', html)
        self.assertIn('value="media" selected', html)


if __name__ == "__main__":
    unittest.main()
