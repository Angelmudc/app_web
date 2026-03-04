# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class _DummyCandidata:
    def __init__(self):
        self.fila = 30
        self.compat_test_candidata_json = {
            "version": "v2.0",
            "profile": {
                "ritmo": "activo",
                "estilo": "toma_iniciativa",
                "relacion_ninos": "comoda",
                "disponibilidad_horarios": ["8am-5pm"],
                "mascotas": "no",
                "mascotas_importancia": "media",
            },
        }
        self.compat_ritmo_preferido = "activo"
        self.compat_estilo_trabajo = "toma_iniciativa"
        self.compat_relacion_ninos = "comoda"
        self.compat_limites_no_negociables = ["no_mascotas"]
        self.compat_disponibilidad_horario = "8am-5pm"
        self.compat_mascotas = "no"


class _DummySolicitud:
    def __init__(self):
        self.id = 10
        self.codigo_solicitud = "SOL-MASC-1"
        self.tipo_plan = "premium"
        self.horario = "8am-5pm"
        self.ninos = 0
        self.mascota = "si"
        self.funciones = ["limpieza"]
        self.candidata_id = 30
        self.candidata = _DummyCandidata()
        self.compat_test_cliente_json = None


class CompatClienteMascotasImportanciaTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def test_post_cliente_guarda_importancia_y_match_reporta_riesgo(self):
        s = _DummySolicitud()
        captured = {}

        def _save_stub(_s, payload):
            _s.compat_test_cliente_json = payload
            captured["payload"] = payload
            return "db_json"

        def _persist_stub(_s, result):
            captured["result"] = result
            return True

        target = clientes_routes.compat_test_cliente
        for _ in range(3):
            target = target.__wrapped__

        with flask_app.test_request_context(
            "/clientes/solicitudes/10/compat/test",
            method="POST",
            data={
                "ritmo_hogar": "Activo",
                "direccion_trabajo": "Prefiere iniciativa",
                "experiencia_deseada": "Alta",
                "puntualidad_1a5": "4",
                "comunicacion": "Mixta",
                "horario_tokens[]": ["8am-5pm"],
                "prioridades[]": ["Limpieza"],
                "mascotas": "si",
                "mascotas_importancia": "alta",
            },
        ):
            fake_user = SimpleNamespace(id=12, nombre_completo="Cliente Demo", username="cliente", codigo="CL-012")
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=s), \
                 patch.object(clientes_routes, "_save_compat_cliente", side_effect=_save_stub), \
                 patch.object(clientes_routes, "persist_result_to_solicitud", side_effect=_persist_stub):
                resp = target(10)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(captured["payload"]["profile"]["mascotas"], "si")
        self.assertEqual(captured["payload"]["profile"]["mascotas_importancia"], "alta")
        self.assertIn("result", captured)
        self.assertIn("Mascotas", [b.get("title") for b in captured["result"].get("breakdown", [])])
        self.assertTrue(any("importancia alta" in r.lower() for r in captured["result"].get("risks", [])))


if __name__ == "__main__":
    unittest.main()
