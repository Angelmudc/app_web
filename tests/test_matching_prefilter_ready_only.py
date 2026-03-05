# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils import matching_service


class _DummySolicitud:
    def __init__(self):
        self.horario = "8:00 a.m. - 5:00 p.m."
        self.modalidad_trabajo = "salida diaria"
        self.ciudad_sector = ""
        self.rutas_cercanas = ""
        self.funciones = ["limpieza"]
        self.funciones_otro = ""
        self.tipo_servicio = "DOMESTICA_LIMPIEZA"
        self.detalles_servicio = {}
        self.experiencia = ""
        self.edad_requerida = []
        self.mascota = "no"
        self.compat_test_cliente_json = None


class _RelCount:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n


class _Cand:
    def __init__(self, fila, estado, entrevista="legacy", docs=True, codigo="C-1"):
        self.fila = fila
        self.nombre_completo = f"Cand {fila}"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = codigo
        self.estado = estado
        self.entrevista = entrevista
        self.entrevistas_nuevas = _RelCount(0)
        self.depuracion = b"dep" if docs else None
        self.perfil = b"perfil" if docs else None
        self.cedula1 = b"c1" if docs else None
        self.cedula2 = b"c2" if docs else None
        self.referencias_laboral = "Ref laboral"
        self.referencias_familiares = "Ref familiar"
        self.direccion_completa = "Santiago"
        self.rutas_cercanas = "Cienfuegos"
        self.modalidad_trabajo_preferida = "salida diaria"
        self.compat_disponibilidad_horario = "8am-5pm"
        self.compat_disponibilidad_dias = []
        self.compat_fortalezas = []
        self.compat_limites_no_negociables = []
        self.compat_relacion_ninos = None
        self.sabe_planchar = False
        self.areas_experiencia = ""
        self.anos_experiencia = "2"
        self.edad = "30"
        self.compat_test_candidata_json = None


class _PrefilterQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._state_eq = None
        self._state_in = None
        self._exclude_descalificadas = False
        self._exclude_trabajando = False

    def filter(self, *criteria):
        for crit in criteria:
            try:
                params = crit.compile().params
            except Exception:
                params = {}
            text_crit = str(crit)
            if "estado" in text_crit and "trabajando" in str(params.values()):
                self._exclude_trabajando = True
            for val in params.values():
                if val == "descalificada":
                    self._exclude_descalificadas = True
                elif isinstance(val, (tuple, list, set)) and {"lista_para_trabajar", "inscrita"}.issubset(set(val)):
                    self._state_in = ("lista_para_trabajar", "inscrita")
                    self._state_eq = None
                elif val in ("lista_para_trabajar", "inscrita"):
                    if " IN " in text_crit:
                        self._state_in = ("lista_para_trabajar", "inscrita")
                        self._state_eq = None
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
        if self._exclude_trabajando:
            out = [r for r in out if getattr(r, "estado", None) != "trabajando"]
        if self._state_eq is not None:
            out = [r for r in out if getattr(r, "estado", None) == self._state_eq]
        elif self._state_in is not None:
            out = [r for r in out if getattr(r, "estado", None) in set(self._state_in)]
        return out


class MatchingPrefilterReadyOnlyTest(unittest.TestCase):
    def test_candidate_query_prefilter_excluye_trabajando_y_descalificada(self):
        s = _DummySolicitud()
        rows = [
            _Cand(1, "lista_para_trabajar"),
            _Cand(2, "inscrita"),
            _Cand(3, "trabajando"),
            _Cand(4, "descalificada"),
        ]
        out = matching_service.candidate_query_prefilter(s, base_query=_PrefilterQuery(rows))
        ids = {c.fila for c in out}
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        self.assertNotIn(3, ids)
        self.assertNotIn(4, ids)

    def test_rank_muestra_solo_ready(self):
        s = _DummySolicitud()
        cand_a = _Cand(11, "lista_para_trabajar", entrevista="ok", docs=True, codigo="C-11")
        cand_b = _Cand(12, "lista_para_trabajar", entrevista="", docs=True, codigo="C-12")
        cand_c = _Cand(13, "inscrita", entrevista="ok", docs=True, codigo="C-13")
        cand_d = _Cand(14, "trabajando", entrevista="ok", docs=True, codigo="C-14")
        cand_e = _Cand(15, "descalificada", entrevista="ok", docs=True, codigo="C-15")

        with patch("utils.matching_service.candidate_query_prefilter", return_value=[cand_a, cand_b, cand_c, cand_d, cand_e]):
            ranked = matching_service.rank_candidates(s, top_k=10)

        ids = [item["candidate"].fila for item in ranked]
        self.assertIn(11, ids)   # lista + ready
        self.assertIn(13, ids)   # inscrita + ready (fallback/ranking)
        self.assertNotIn(12, ids)  # sin entrevista
        self.assertNotIn(14, ids)  # trabajando
        self.assertNotIn(15, ids)  # descalificada


if __name__ == "__main__":
    unittest.main()
