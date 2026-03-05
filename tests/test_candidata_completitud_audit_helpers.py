# -*- coding: utf-8 -*-

import unittest

from utils.candidata_completitud_audit import (
    entrevista_ok,
    binario_ok,
    referencias_ok,
)


class CandidataCompletitudAuditHelpersTest(unittest.TestCase):
    def test_entrevista_vieja_llena_es_ok(self):
        self.assertTrue(entrevista_ok("Entrevista legacy", 0))

    def test_entrevista_nueva_count_mayor_que_cero_es_ok(self):
        self.assertTrue(entrevista_ok("   ", 1))

    def test_sin_entrevista_vieja_ni_nueva_falla(self):
        self.assertFalse(entrevista_ok("", 0))

    def test_binarios_none_vacio_y_con_bytes(self):
        self.assertFalse(binario_ok(None))
        self.assertFalse(binario_ok(b""))
        self.assertTrue(binario_ok(b"abc"))

    def test_referencias_placeholders_y_texto_util(self):
        self.assertFalse(referencias_ok("node"))
        self.assertFalse(referencias_ok("none"))
        self.assertFalse(referencias_ok("  "))
        self.assertTrue(referencias_ok("Recomendable, trabajó 2 años"))


if __name__ == "__main__":
    unittest.main()
