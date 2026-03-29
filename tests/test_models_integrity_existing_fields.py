# -*- coding: utf-8 -*-

import unittest

from models import Candidata, Solicitud


class ModelsIntegrityTest(unittest.TestCase):
    def test_existing_candidata_fields_still_present(self):
        for field in ("fila", "nombre_completo", "cedula", "numero_telefono", "estado"):
            self.assertTrue(hasattr(Candidata, field), f"Missing Candidata.{field}")

    def test_existing_solicitud_fields_still_present(self):
        for field in ("id", "codigo_solicitud", "cliente_id", "estado", "horario", "modalidad_trabajo", "fecha_seguimiento_manual"):
            self.assertTrue(hasattr(Solicitud, field), f"Missing Solicitud.{field}")


if __name__ == "__main__":
    unittest.main()
