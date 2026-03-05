# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from utils import matching_service


class _DummySolicitud:
    def __init__(self):
        self.horario = "8:00 a.m. - 5:00 p.m."
        self.modalidad_trabajo = "Tiempo completo"
        self.ciudad_sector = "Villa Maria, Santiago"
        self.rutas_cercanas = "Cienfuegos"
        self.funciones = ["limpieza", "cocina", "cuidar_ninos"]
        self.funciones_otro = ""
        self.tipo_servicio = "DOMESTICA_LIMPIEZA"
        self.detalles_servicio = {}
        self.experiencia = "Minimo 2 anos"
        self.edad_requerida = ["25", "45"]
        self.mascota = "no"
        self.compat_test_cliente_json = None


class _DummyCandidate:
    def __init__(self, fila: int, nombre: str):
        self.fila = fila
        self.nombre_completo = nombre
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = f"C-{fila}"
        self.estado = "lista_para_trabajar"
        self.direccion_completa = "Avenida Nueva Stgo Villa Maria"
        self.rutas_cercanas = "Cienfuegos"
        self.modalidad_trabajo_preferida = "tiempo completo"
        self.compat_disponibilidad_horario = "8am-5pm"
        self.compat_disponibilidad_dias = ["lun", "mar", "mie", "jue", "vie"]
        self.compat_fortalezas = []
        self.compat_limites_no_negociables = []
        self.compat_relacion_ninos = "comoda"
        self.sabe_planchar = False
        self.areas_experiencia = ""
        self.anos_experiencia = "4"
        self.edad = "32"
        self.compat_test_candidata_json = None


class MatchingServiceTest(unittest.TestCase):
    def test_funciones_overlap_three_gives_20_and_score_over_75_without_bonus(self):
        solicitud = _DummySolicitud()
        cand = _DummyCandidate(1, "Operativa")
        cand.areas_experiencia = "Limpieza\nCocina\nNiñera"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["bonus_test"], 0)
        self.assertGreaterEqual(row["operational_score"], 75)
        self.assertEqual(row["breakdown_snapshot"]["components"]["funciones_pts"], 20)

    def test_envejecientes_missing_keeps_funciones_10_and_medium_score(self):
        solicitud = _DummySolicitud()
        solicitud.funciones = ["limpieza", "cuidar_envejecientes"]
        solicitud.horario = "noche"
        solicitud.modalidad_trabajo = "medio tiempo"

        cand = _DummyCandidate(2, "Parcial")
        cand.areas_experiencia = "Limpieza"
        cand.modalidad_trabajo_preferida = "dormida"
        cand.compat_disponibilidad_horario = "8am-5pm"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["components"]["funciones_pts"], 10)
        self.assertIn("Sin experiencia declarada con envejecientes", row["reasons"])
        self.assertGreaterEqual(row["score"], 50)
        self.assertLess(row["score"], 75)

    def test_dirty_routes_and_city_raise_location_points(self):
        solicitud = _DummySolicitud()
        cand = _DummyCandidate(3, "Cercana")
        cand.direccion_completa = "Avenida Nueva stgo Villa Maria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertGreaterEqual(row["breakdown_snapshot"]["components"]["ubicacion_pts"], 30)

    def test_edad_match_40_anos_vs_36_45_adds_5(self):
        solicitud = _DummySolicitud()
        solicitud.edad_requerida = ["36–45 años"]
        cand = _DummyCandidate(4, "Edad Ok")
        cand.edad = "40 años"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["edad_pts"], 5)
        self.assertTrue(row["breakdown_snapshot"]["edad_match"])

    def test_edad_22_vs_mayor_de_45_penalizes_minus_5(self):
        solicitud = _DummySolicitud()
        solicitud.edad_requerida = ["Mayor de 45"]
        cand = _DummyCandidate(5, "Edad Baja")
        cand.edad = "22"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["edad_pts"], -5)
        self.assertFalse(row["breakdown_snapshot"]["edad_match"])

    def test_edad_none_returns_zero_points(self):
        solicitud = _DummySolicitud()
        solicitud.edad_requerida = ["25 a 45 años"]
        cand = _DummyCandidate(6, "Edad ND")
        cand.edad = None

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["edad_pts"], 0)
        self.assertIsNone(row["breakdown_snapshot"]["edad_match"])

    def test_solicitud_rule_with_braces_25_en_adelante_matches_30(self):
        solicitud = _DummySolicitud()
        solicitud.edad_requerida = ['{"25 en adelante"}']
        cand = _DummyCandidate(7, "Edad 30")
        cand.edad = "30"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["edad_pts"], 5)
        self.assertTrue(row["breakdown_snapshot"]["edad_match"])

    def test_solicitud_rule_with_braces_31_45_matches_40(self):
        solicitud = _DummySolicitud()
        solicitud.edad_requerida = ["{31-45}"]
        cand = _DummyCandidate(8, "Edad 40")
        cand.edad = "40"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        self.assertEqual(row["breakdown_snapshot"]["edad_pts"], 5)
        self.assertTrue(row["breakdown_snapshot"]["edad_match"])


if __name__ == "__main__":
    unittest.main()
