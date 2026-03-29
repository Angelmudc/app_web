# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

from flask import url_for

from app import app as flask_app
import core.handlers.compat_candidata_handlers as compat_h
import core.legacy_handlers as legacy_handlers


class _DummyCandidata:
    def __init__(self):
        self.fila = 1
        self.nombre_completo = "Candidata Demo"
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


class CompatCandidataRouteMigrationTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()

    def _login_secretaria(self):
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))

    def test_get_base_muestra_buscador_compat(self):
        self._login_secretaria()
        resp = self.client.get("/secretarias/compat/candidata", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Test de compatibilidad", resp.data)

    def test_endpoint_y_ruta_siguen_intactos(self):
        view = flask_app.view_functions.get("compat_candidata")
        self.assertIs(view, compat_h.compat_candidata)
        with flask_app.test_request_context():
            self.assertEqual(url_for("compat_candidata"), "/secretarias/compat/candidata")

    def test_post_guardar_con_next_home_redirige_home(self):
        self._login_secretaria()
        dummy = _DummyCandidata()

        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", _DummyQuery(dummy)), \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/secretarias/compat/candidata",
                    data={
                        "accion": "guardar",
                        "fila": "1",
                        "next": "home",
                        "ritmo": "activo",
                        "estilo": "toma_iniciativa",
                        "comunicacion": "mixta",
                        "relacion_ninos": "comoda",
                        "experiencia_nivel": "alta",
                        "puntualidad_1a5": "4",
                        "mascotas": "si",
                        "mascotas_importancia": "media",
                        "fortalezas": ["limpieza_general"],
                        "disponibilidad_dias": ["lun", "mar"],
                        "disponibilidad_horarios": ["9am-6pm", "fin_de_semana"],
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        with flask_app.test_request_context():
            expected_home = url_for("home")
        self.assertTrue((resp.headers.get("Location") or "").endswith(expected_home))
        commit_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
