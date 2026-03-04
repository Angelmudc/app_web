# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class _DummyCandidata:
    def __init__(self):
        self.fila = 99
        self.compat_test_candidata_json = {
            "version": "v2.0",
            "profile": {
                "ritmo": "activo",
                "estilo": "toma_iniciativa",
                "relacion_ninos": "comoda",
                "disponibilidad_horarios": ["8am-5pm", "medio_tiempo"],
                "mascotas": "si",
            },
        }
        self.compat_ritmo_preferido = "activo"
        self.compat_estilo_trabajo = "toma_iniciativa"
        self.compat_relacion_ninos = "comoda"
        self.compat_limites_no_negociables = []
        self.compat_disponibilidad_horario = "8am-5pm"
        self.compat_mascotas = "si"


class _DummySolicitud:
    def __init__(self):
        self.id = 1
        self.codigo_solicitud = "SOL-CLI-1"
        self.tipo_plan = "premium"
        self.horario = "8am-5pm"
        self.ninos = 1
        self.mascota = "si"
        self.funciones = ["limpieza", "ninos"]
        self.candidata_id = 99
        self.candidata = _DummyCandidata()


class CompatClienteHorariosTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def test_post_cliente_guarda_tokens_y_compute_no_crash(self):
        s = _DummySolicitud()
        captured = {}

        def _save_stub(_s, payload):
            captured["payload"] = payload
            return "db_json"

        def _persist_stub(_s, result):
            captured["result"] = result
            return True

        # Bypass decorators: login_required + cliente_required + politicas_requeridas
        target = clientes_routes.compat_test_cliente
        for _ in range(3):
            target = target.__wrapped__

        with flask_app.test_request_context(
            "/clientes/solicitudes/1/compat/test",
            method="POST",
            data={
                "ritmo_hogar": "Activo",
                "direccion_trabajo": "Prefiere iniciativa",
                "experiencia_deseada": "Alta",
                "puntualidad_1a5": "4",
                "comunicacion": "Mixta",
                "horario_tokens[]": ["8am-5pm", "medio_tiempo"],
                "prioridades[]": ["Limpieza", "Cuidado de niños"],
                "no_negociables[]": ["No cocinar"],
            },
        ):
            fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo", username="cliente", codigo="CL-007")
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=s), \
                 patch.object(clientes_routes, "_save_compat_cliente", side_effect=_save_stub), \
                 patch.object(clientes_routes, "persist_result_to_solicitud", side_effect=_persist_stub):
                resp = target(1)

        self.assertEqual(resp.status_code, 302)
        self.assertIn("payload", captured)
        self.assertEqual(captured["payload"]["profile"]["horario_tokens"], ["8am-5pm", "medio_tiempo"])
        self.assertIn("result", captured)
        self.assertIn("score", captured["result"])
        self.assertIn("level", captured["result"])


if __name__ == "__main__":
    unittest.main()
