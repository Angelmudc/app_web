# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

from app import app as flask_app
import core.legacy_handlers as legacy_handlers


class _DummyCandidata:
    def __init__(self):
        self.fila = 1
        self.nombre_completo = "Candidata Demo"

        # Campos de compat para que hasattr(...) permita escritura
        self.compat_ritmo_preferido = None
        self.compat_estilo_trabajo = None
        self.compat_comunicacion = None
        self.compat_relacion_ninos = None
        self.compat_experiencia_nivel = None
        self.compat_puntualidad_1a5 = None
        self.compat_mascotas = None
        self.compat_mascotas_ok = None
        self.compat_fortalezas = []
        self.compat_tareas_evitar = []
        self.compat_limites_no_negociables = []
        self.compat_disponibilidad_dias = []
        self.compat_disponibilidad_horarios = []
        self.compat_disponibilidad_horario = None
        self.compat_observaciones = None
        self.compat_test_candidata_json = None
        self.compat_test_candidata_version = None
        self.compat_test_candidata_at = None


class _DummyQuery:
    def __init__(self, obj):
        self._obj = obj

    def get_or_404(self, _fila):
        return self._obj


class _DummyCandidataJsonOnly:
    def __init__(self):
        self.fila = 1
        self.nombre_completo = "Candidata JSON"
        self.compat_ritmo_preferido = "activo"
        self.compat_estilo_trabajo = "toma_iniciativa"
        self.compat_relacion_ninos = "comoda"
        self.compat_test_candidata_json = {
            "profile": {
                "comunicacion": "mixta",
                "experiencia_nivel": "alta",
                "puntualidad_1a5": 4,
            }
        }


class CompatCandidataHorariosTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()

    def test_secretaria_guardar_test_con_horarios_estandarizados(self):
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
                        "fortalezas": ["limpieza_general"],
                        "disponibilidad_dias": ["lun", "mar"],
                        "disponibilidad_horarios": ["9am-6pm", "fin_de_semana"],
                        "nota": "Prueba de horarios",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        commit_mock.assert_called()
        self.assertIsInstance(dummy.compat_test_candidata_json, dict)
        self.assertEqual(dummy.compat_test_candidata_json.get("profile", {}).get("comunicacion"), "mixta")
        self.assertEqual(dummy.compat_test_candidata_json.get("profile", {}).get("experiencia_nivel"), "alta")
        self.assertEqual(dummy.compat_test_candidata_json.get("profile", {}).get("puntualidad_1a5"), 4)
        self.assertEqual(
            dummy.compat_test_candidata_json.get("profile", {}).get("disponibilidad_horarios"),
            ["9am-6pm", "fin_de_semana"],
        )

    def test_secretaria_form_get_usa_fallback_desde_json_para_campos_sin_columna(self):
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

        dummy = _DummyCandidataJsonOnly()
        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", _DummyQuery(dummy)):
                resp = self.client.get("/secretarias/compat/candidata?fila=1", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn('id="comunicacion"', html)
        self.assertIn('value="mixta" selected', html)
        self.assertIn('id="experiencia_nivel"', html)
        self.assertIn('value="alta" selected', html)
        self.assertIn('name="puntualidad_1a5" value="4"', html)


if __name__ == "__main__":
    unittest.main()
