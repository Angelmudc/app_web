# -*- coding: utf-8 -*-

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import core.legacy_handlers as legacy_handlers
from utils.cedula_normalizer import (
    cedula_digits,
    format_cedula,
    normalize_cedula_for_compare,
    normalize_cedula_for_store,
)


def _base_ingreso_data(cedula: str) -> dict:
    return {
        "nombre_completo": "Maria Test",
        "edad": "30",
        "numero_telefono": "8091234567",
        "direccion_completa": "Santiago",
        "modalidad_trabajo_preferida": "salida diaria",
        "rutas_cercanas": "Centro",
        "empleo_anterior": "Casa",
        "anos_experiencia": "3",
        "areas_experiencia": ["limpieza"],
        "sabe_planchar": "si",
        "contactos_referencias_laborales": "Ref laboral",
        "referencias_familiares_detalle": "Ref familiar",
        "acepta_porcentaje_sueldo": "1",
        "cedula": cedula,
    }


class CedulaIngresoValidationTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()

        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))

    def test_cedula_normalizer_helpers(self):
        self.assertEqual(cedula_digits(" 123 - 4567890/1 "), "12345678901")
        self.assertEqual(format_cedula("12345678901"), "123-4567890-1")
        self.assertEqual(normalize_cedula_for_compare("cedula: 123.4567890.1"), "12345678901")
        self.assertEqual(normalize_cedula_for_store("123 4567890/1"), "123-4567890-1")
        self.assertEqual(normalize_cedula_for_store("ABC-123"), "ABC-123")

    def test_duplicado_detectado_por_formatos_distintos_bloquea(self):
        existing = SimpleNamespace(cedula="123-4567890-1", estado="en_proceso")

        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")) as find_mock, \
                 patch("core.legacy_handlers.db.session.add") as add_mock, \
                 patch("core.legacy_handlers.flash") as flash_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_ingreso_data("123 4567890/1"),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        find_mock.assert_called_once_with("123 4567890/1")
        add_mock.assert_not_called()
        self.assertTrue(any("Ya existe una candidata con esta cédula" in str(call.args[0]) for call in flash_mock.call_args_list))

    def test_duplicado_al_reves_tambien_bloquea(self):
        existing = SimpleNamespace(cedula="123 4567890/1", estado="en_proceso")

        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")) as find_mock, \
                 patch("core.legacy_handlers.db.session.add") as add_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_ingreso_data("123-4567890-1"),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        find_mock.assert_called_once_with("123-4567890-1")
        add_mock.assert_not_called()

    def test_duplicado_descalificada_mensaje_especial(self):
        existing = SimpleNamespace(cedula="123-4567890-1", estado="descalificada")

        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")), \
                 patch("core.legacy_handlers.db.session.add") as add_mock, \
                 patch("core.legacy_handlers.flash") as flash_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_ingreso_data("123 4567890/1"),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        add_mock.assert_not_called()
        self.assertTrue(any("descalificada" in str(call.args[0]).lower() for call in flash_mock.call_args_list))

    def test_alta_normaliza_guardado_en_creacion(self):
        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(None, "12345678901")), \
                 patch("core.legacy_handlers.db.session.flush"), \
                 patch("core.legacy_handlers.db.session.commit"), \
                 patch("core.legacy_handlers.db.session.add") as add_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_ingreso_data("123 4567890/1"),
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        nueva = add_mock.call_args[0][0]
        self.assertEqual(nueva.cedula, "123-4567890-1")

    def test_no_toca_datos_existentes_en_bloqueo(self):
        existing = SimpleNamespace(cedula="123 4567890/1", estado="en_proceso")
        original = existing.cedula

        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")), \
                 patch("core.legacy_handlers.db.session.add") as add_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_ingreso_data("123-4567890-1"),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        add_mock.assert_not_called()
        self.assertEqual(existing.cedula, original)


if __name__ == "__main__":
    unittest.main()
