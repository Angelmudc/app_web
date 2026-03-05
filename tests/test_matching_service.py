# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from utils import matching_service


class _DummySolicitud:
    def __init__(self):
        self.horario = "8:00 a.m. - 5:00 p.m."
        self.modalidad_trabajo = "salida diaria - lunes a viernes"
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
        self.entrevista = "entrevista ok"
        self.entrevistas_nuevas = []
        self.depuracion = b"dep"
        self.perfil = b"perfil"
        self.cedula1 = b"c1"
        self.cedula2 = b"c2"
        self.referencias_laboral = "Ref laboral"
        self.referencias_familiares = "Ref familiar"
        self.direccion_completa = "Avenida Nueva Stgo Villa Maria"
        self.rutas_cercanas = "Cienfuegos"
        self.modalidad_trabajo_preferida = "salida diaria"
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


class _PrefilterQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._state_eq = None
        self._state_in = None
        self._exclude_descalificadas = False

    def filter(self, *criteria):
        for crit in criteria:
            try:
                params = crit.compile().params
            except Exception:
                params = {}
            for val in params.values():
                if val == "descalificada":
                    self._exclude_descalificadas = True
                elif val in ("lista_para_trabajar", "inscrita"):
                    if " IN " in str(crit):
                        self._state_in = ("lista_para_trabajar", "inscrita")
                    else:
                        self._state_eq = val
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        out = list(self._rows)
        if self._exclude_descalificadas:
            out = [r for r in out if getattr(r, "estado", None) != "descalificada"]
        if self._state_eq is not None:
            out = [r for r in out if getattr(r, "estado", None) == self._state_eq]
        elif self._state_in is not None:
            out = [r for r in out if getattr(r, "estado", None) in set(self._state_in)]
        return out


class MatchingServiceTest(unittest.TestCase):
    def test_prefilter_excluye_descalificadas(self):
        solicitud = _DummySolicitud()
        solicitud.ciudad_sector = ""
        solicitud.rutas_cercanas = ""

        cand_ok = _DummyCandidate(201, "Activa")
        cand_ok.estado = "lista_para_trabajar"
        cand_bad = _DummyCandidate(202, "Descalificada")
        cand_bad.estado = "descalificada"

        rows = matching_service.candidate_query_prefilter(
            solicitud,
            base_query=_PrefilterQuery([cand_ok, cand_bad]),
        )

        ids = {c.fila for c in rows}
        self.assertIn(201, ids)
        self.assertNotIn(202, ids)

    def test_modalidad_con_dormida_vs_dormida_match_fuerte(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "con dormida 💤 lunes a viernes"
        cand = _DummyCandidate(101, "Dormida Match")
        cand.modalidad_trabajo_preferida = "dormida"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 20)
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["solicitud_modalidad_norm"], "dormida")
        self.assertEqual(snap["candidata_modalidad_norm"], "dormida")

    def test_modalidad_con_dormida_vs_salida_diaria_mismatch(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "con dormida 💤 salida quincenal"
        cand = _DummyCandidate(102, "Dormida Mismatch")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 0)
        self.assertFalse(snap["modalidad_match"])
        self.assertEqual(snap["solicitud_modalidad_norm"], "dormida")
        self.assertEqual(snap["candidata_modalidad_norm"], "salida_diaria")

    def test_modalidad_tres_dias_vs_salida_diaria_match(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "3 dias a la semana"
        cand = _DummyCandidate(103, "Dias Match")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 20)
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["solicitud_modalidad_norm"], "salida_diaria")

    def test_modalidad_salida_diaria_lunes_viernes_match(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "salida diaria - lunes a viernes"
        cand = _DummyCandidate(104, "Salida Diaria Match")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 20)
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["solicitud_modalidad_norm"], "salida_diaria")

    def test_modalidad_lunes_sabado_sin_dormida_match(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "lunes a sabado"
        cand = _DummyCandidate(105, "Lun Sab Match")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 20)
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["solicitud_modalidad_norm"], "salida_diaria")

    def test_modalidad_no_inferible_or_none_no_evaluable(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = None
        cand = _DummyCandidate(106, "No Evaluable")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        row = ranked[0]
        snap = row["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 0)
        self.assertIsNone(snap["modalidad_match"])
        self.assertIsNone(snap["solicitud_modalidad_norm"])

    def test_modalidad_gibberish_no_penaliza(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "vslafkvmaslfnva"
        cand = _DummyCandidate(112, "Dormida")
        cand.modalidad_trabajo_preferida = "dormida"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["components"]["modalidad_pts"], 0)
        self.assertIsNone(snap["modalidad_match"])
        self.assertEqual(snap["modalidad_reason"], "modalidad no evaluable: gibberish")
        self.assertIsNone(snap["solicitud_modalidad_norm"])

    def test_modalidad_con_dormir_lun_vie_detecta_dormida(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "con dormir lun-vie"
        cand = _DummyCandidate(107, "Con Dormir")
        cand.modalidad_trabajo_preferida = "dormida"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["solicitud_modalidad_norm"], "dormida")
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["components"]["modalidad_pts"], 20)

    def test_modalidad_interna_l_v_detecta_dormida(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "interna L-V"
        cand = _DummyCandidate(108, "Interna")
        cand.modalidad_trabajo_preferida = "dormida"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["solicitud_modalidad_norm"], "dormida")
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["components"]["modalidad_pts"], 20)

    def test_modalidad_l_v_sin_dormida_detecta_salida_diaria(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "L-V"
        cand = _DummyCandidate(109, "L a V")
        cand.modalidad_trabajo_preferida = "salida diaria"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["solicitud_modalidad_norm"], "salida_diaria")
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["components"]["modalidad_pts"], 20)

    def test_modalidad_candidata_sin_dormida_lun_sab_detecta_salida_diaria(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "lunes-sabado"
        cand = _DummyCandidate(110, "Sin Dormida")
        cand.modalidad_trabajo_preferida = "sin dormida 💤 lun-sab"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["solicitud_modalidad_norm"], "salida_diaria")
        self.assertEqual(snap["candidata_modalidad_norm"], "salida_diaria")
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["components"]["modalidad_pts"], 20)

    def test_modalidad_se_queda_a_dormir_detecta_dormida(self):
        solicitud = _DummySolicitud()
        solicitud.modalidad_trabajo = "se queda a dormir fin de semana"
        cand = _DummyCandidate(111, "Se Queda")
        cand.modalidad_trabajo_preferida = "con dormida"

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand]):
            ranked = matching_service.rank_candidates(solicitud, top_k=1)

        snap = ranked[0]["breakdown_snapshot"]
        self.assertEqual(snap["solicitud_modalidad_norm"], "dormida")
        self.assertEqual(snap["candidata_modalidad_norm"], "dormida")
        self.assertTrue(snap["modalidad_match"])
        self.assertEqual(snap["components"]["modalidad_pts"], 20)

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
