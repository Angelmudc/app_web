# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace

from utils.candidata_readiness import (
    candidata_docs_complete,
    candidata_has_interview,
    candidata_is_ready_to_send,
)


class _RelCount:
    def __init__(self, n: int):
        self.n = n

    def count(self):
        return self.n


def _build_candidata(**kwargs):
    base = dict(
        fila=1,
        estado="lista_para_trabajar",
        codigo="C-001",
        entrevista="",
        entrevistas_nuevas=_RelCount(0),
        depuracion=b"dep",
        perfil=b"perfil",
        cedula1=b"c1",
        cedula2=b"c2",
        foto_perfil=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class CandidataReadinessUnitTest(unittest.TestCase):
    def test_interview_legacy_ok(self):
        c = _build_candidata(entrevista="Texto legacy")
        self.assertTrue(candidata_has_interview(c))

    def test_interview_nueva_ok(self):
        c = _build_candidata(entrevista="", entrevistas_nuevas=_RelCount(1))
        self.assertTrue(candidata_has_interview(c))

    def test_interview_falla_sin_ambas(self):
        c = _build_candidata(entrevista="", entrevistas_nuevas=_RelCount(0))
        self.assertFalse(candidata_has_interview(c))

    def test_docs_completos_ok(self):
        c = _build_candidata()
        docs = candidata_docs_complete(c)
        self.assertTrue(docs["complete"])

    def test_docs_falla_si_falta_cedula2(self):
        c = _build_candidata(cedula2=None)
        docs = candidata_docs_complete(c)
        self.assertFalse(docs["complete"])
        self.assertIn("cedula2", docs["missing_required"])

        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("cedula2" in r for r in reasons))

    def test_codigo_vacio_falla(self):
        c = _build_candidata(codigo="", entrevista="ok")
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("código" in r.lower() for r in reasons))


if __name__ == "__main__":
    unittest.main()
