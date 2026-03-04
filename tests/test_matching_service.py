# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from utils import matching_service


class _DummySolicitud:
    def __init__(self):
        self.horario = "8:00 a.m. - 5:00 p.m."
        self.modalidad_trabajo = "Tiempo completo"
        self.ninos = 1
        self.mascota = "no"
        self.funciones = ["limpieza"]
        self.compat_test_cliente_json = {
            "profile": {
                "horario_preferido": "8:00 a.m. - 5:00 p.m.",
                "ninos": 1,
                "mascota": "no",
            }
        }


class _DummyCandidate:
    def __init__(self, fila: int, nombre: str):
        self.fila = fila
        self.nombre_completo = nombre
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.modalidad_trabajo_preferida = "tiempo completo"
        self.compat_disponibilidad_horario = "8am-5pm"
        self.compat_test_candidata_json = {"profile": {"disponibilidad_horarios": ["8am-5pm"]}}
        self.compat_fortalezas = ["limpieza"]
        self.compat_limites_no_negociables = []
        self.compat_relacion_ninos = "comoda"
        self.compat_ritmo_preferido = "activo"
        self.compat_estilo_trabajo = "toma_iniciativa"
        self.estado = "lista_para_trabajar"


class _ChainQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class MatchingServiceTest(unittest.TestCase):
    def test_rank_candidates_returns_sorted_by_score(self):
        solicitud = _DummySolicitud()
        c1 = _DummyCandidate(1, "Ana")
        c2 = _DummyCandidate(2, "Bea")
        c3 = _DummyCandidate(3, "Ceci")

        with patch("utils.matching_service.candidate_query_prefilter", return_value=_ChainQuery([c1, c2, c3])), \
             patch("utils.matching_service.compute_match") as mock_compute:
            mock_compute.side_effect = [
                {"score": 55, "level": "media", "summary": "ok", "risks": [], "breakdown": []},
                {"score": 90, "level": "alta", "summary": "ok", "risks": [], "breakdown": []},
                {"score": 40, "level": "baja", "summary": "ok", "risks": [], "breakdown": []},
            ]
            ranked = matching_service.rank_candidates(solicitud, top_k=30)

        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[0]["candidate"].fila, 2)
        self.assertEqual(ranked[1]["candidate"].fila, 1)
        self.assertEqual(ranked[2]["candidate"].fila, 3)

    def test_build_solicitud_profile_uses_compat_engine(self):
        solicitud = _DummySolicitud()
        profile = matching_service.build_solicitud_profile(solicitud)
        self.assertIn("horario_tokens", profile)
        self.assertIn("mascotas", profile)


if __name__ == "__main__":
    unittest.main()
