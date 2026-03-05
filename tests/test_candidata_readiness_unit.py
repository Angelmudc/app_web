# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace

from utils.candidata_readiness import (
    candidata_docs_complete,
    candidata_has_interview,
    candidata_is_ready_to_send,
    candidata_referencias_complete,
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
        referencias_laboral="Ref laboral valida",
        referencias_familiares="Ref familiar valida",
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
        self.assertEqual(set(docs["flags"].keys()), {"depuracion", "perfil", "cedula1", "cedula2"})

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

    def test_codigo_espacios_falla(self):
        c = _build_candidata(codigo="   ", entrevista="ok")
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("código" in r.lower() for r in reasons))

    def test_codigo_none_falla(self):
        c = _build_candidata(codigo=None, entrevista="ok")
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("código" in r.lower() for r in reasons))

    def test_perfil_faltante_bloquea_ready(self):
        c = _build_candidata(entrevista="ok", perfil=None)
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("perfil" in r.lower() for r in reasons))

    def test_binarios_vacios_no_cuentan(self):
        c = _build_candidata(entrevista="ok", depuracion=b"", perfil=b"", cedula1=b"", cedula2=b"")
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("depuracion" in r.lower() for r in reasons))
        self.assertTrue(any("perfil" in r.lower() for r in reasons))
        self.assertTrue(any("cedula1" in r.lower() for r in reasons))
        self.assertTrue(any("cedula2" in r.lower() for r in reasons))

    def test_placeholders_entrevista_no_cuentan(self):
        c = _build_candidata(entrevista="node", entrevistas_nuevas=_RelCount(0))
        self.assertFalse(candidata_has_interview(c))
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("entrevista" in r.lower() for r in reasons))

    def test_placeholders_referencias_no_cuentan(self):
        c = _build_candidata(entrevista="ok", referencias_laboral="none", referencias_familiares="-")
        refs = candidata_referencias_complete(c)
        self.assertFalse(refs["referencias_laboral"])
        self.assertFalse(refs["referencias_familiares"])
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertFalse(ready)
        self.assertTrue(any("referencias_laboral" in r for r in reasons))
        self.assertTrue(any("referencias_familiares" in r for r in reasons))

    def test_readiness_base_completa_es_valida(self):
        c = _build_candidata(entrevista="ok")
        ready, reasons = candidata_is_ready_to_send(c)
        self.assertTrue(ready)
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
