# -*- coding: utf-8 -*-

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import app as flask_app
import registro.routes as registro_routes


def _base_data(cedula: str) -> dict:
    return {
        "nombre_completo": "Eva Test",
        "edad": "32",
        "numero_telefono": "8095551111",
        "direccion_completa": "Santiago",
        "modalidad_trabajo_preferida": "salida diaria",
        "rutas_cercanas": "Centro",
        "empleo_anterior": "Casa",
        "anos_experiencia": "5",
        "areas_experiencia": ["limpieza"],
        "sabe_planchar": "si",
        "contactos_referencias_laborales": "Ref laboral",
        "referencias_familiares_detalle": "Ref familiar",
        "acepta_porcentaje_sueldo": "1",
        "cedula": cedula,
    }


class AntiDuplicatesCedulaNormTest(unittest.TestCase):
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

    def test_block_duplicate_by_digits_equivalence(self):
        existing = SimpleNamespace(
            fila=77,
            cedula="123-4567890-1",
            estado="en_proceso",
            cedula_norm_digits="12345678901",
        )
        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")), \
                 patch("core.legacy_handlers.db.session.add") as add_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_data("123 4567890/1"),
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 400)
        add_mock.assert_not_called()

    def test_integrity_error_from_unique_index_returns_friendly_message(self):
        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(None, "12345678901")), \
                 patch("core.legacy_handlers.db.session.add"), \
                 patch("core.legacy_handlers.db.session.flush", side_effect=IntegrityError("insert", {}, Exception("duplicate key"))), \
                 patch("core.legacy_handlers.flash") as flash_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_data("123-4567890-1"),
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(any("aunque esté escrita diferente" in str(call.args[0]) for call in flash_mock.call_args_list))

    def test_existing_row_not_rewritten_and_no_backfill_attempt(self):
        existing = SimpleNamespace(
            fila=99,
            cedula="123 4567890/1",
            estado="en_proceso",
            cedula_norm_digits=None,
        )
        original = existing.cedula
        with flask_app.app_context():
            with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")), \
                 patch("core.legacy_handlers.db.session.add") as add_mock:
                resp = self.client.post(
                    "/registro_interno/",
                    data=_base_data("123-4567890-1"),
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 400)
        add_mock.assert_not_called()
        self.assertEqual(existing.cedula, original)
        self.assertIsNone(existing.cedula_norm_digits)

    def test_registro_publico_tambien_bloquea_duplicados(self):
        existing = SimpleNamespace(
            fila=45,
            cedula="123-4567890-1",
            estado="en_proceso",
            cedula_norm_digits="12345678901",
        )
        with flask_app.app_context():
            with patch.object(registro_routes, "find_duplicate_candidata_by_cedula", return_value=(existing, "12345678901")), \
                 patch.object(registro_routes.db.session, "add") as add_mock:
                resp = self.client.post(
                    "/registro/registro_publico/",
                    data=_base_data("123 4567890/1"),
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 400)
        add_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
