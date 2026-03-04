# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from utils.compat_engine import compute_match, normalize_horarios_tokens, persist_result_to_solicitud


class _DummySolicitud:
    def __init__(self):
        self.id = 101
        self.codigo_solicitud = "SOL-TEST-001"
        self.horario = "8:00 a.m. - 5:00 p.m."
        self.ninos = 2
        self.mascota = "si"
        self.funciones = ["ninos", "limpieza", "envejecientes"]
        self.compat_test_cliente_json = {
            "version": "v2.0",
            "timestamp": "2026-03-04T12:00:00",
            "profile": {
                "ritmo_hogar": "Activo",
                "direccion_trabajo": "Prefiere iniciativa",
                "horario_preferido": "8:00 a.m. - 5:00 p.m.",
                "prioridades": ["ninos", "envejecientes", "limpieza"],
                "no_negociables": ["No cocinar"],
                "ninos": 2,
                "mascota": "si",
                "nota_cliente_test": "Hogar dinámico.",
            },
        }
        self.compat_calc_score = None
        self.compat_calc_level = None
        self.compat_calc_summary = None
        self.compat_calc_risks = None
        self.compat_calc_at = None
        self.fecha_ultima_modificacion = None


class _DummyCandidata:
    def __init__(self):
        self.fila = 99
        self.compat_test_candidata_json = {
            "version": "v2.0",
            "timestamp": "2026-03-04T11:59:00",
            "profile": {
                "ritmo": "activo",
                "estilo": "toma_iniciativa",
                "relacion_ninos": "comoda",
                "fortalezas": ["cuidar_ninos", "limpieza"],
                "limites_no_negociables": ["no_planchar"],
                "disponibilidad_horarios": ["manana", "tarde"],
                "mascotas": "si",
                "nota": "Buena actitud.",
            },
        }
        self.compat_ritmo_preferido = "activo"
        self.compat_estilo_trabajo = "toma_iniciativa"
        self.compat_relacion_ninos = "comoda"
        self.compat_fortalezas = ["ninos", "limpieza"]
        self.compat_limites_no_negociables = ["no_planchar"]
        self.compat_disponibilidad_horario = "manana,tarde"
        self.compat_mascotas = "si"


class CompatEngineTest(unittest.TestCase):
    def test_compute_match_and_persist(self):
        solicitud = _DummySolicitud()
        candidata = _DummyCandidata()

        result = compute_match(solicitud, candidata)

        self.assertIn("score", result)
        self.assertIn("level", result)
        self.assertIn("summary", result)
        self.assertIn("risks", result)
        self.assertIn("breakdown", result)

        self.assertIsInstance(result["score"], int)
        self.assertIn(result["level"], {"alta", "media", "baja"})
        self.assertIsInstance(result["summary"], str)
        self.assertIsInstance(result["risks"], list)
        self.assertIsInstance(result["breakdown"], list)

        with patch("config_app.db.session.commit") as commit_mock:
            ok = persist_result_to_solicitud(solicitud, result)

        self.assertTrue(ok)
        commit_mock.assert_called_once()
        self.assertIsNotNone(solicitud.compat_calc_score)
        self.assertIn(solicitud.compat_calc_level, {"alta", "media", "baja"})

    def test_normalize_horarios_legacy_payload(self):
        out = normalize_horarios_tokens(["mañana", "noche"])
        self.assertEqual(out, {"8am-5pm", "noche_solo"})


if __name__ == "__main__":
    unittest.main()
