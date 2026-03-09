# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import app as flask_app
import utils.candidate_registration as candidate_registration
from utils.robust_save import RobustSaveResult


def _base_data(cedula: str) -> dict:
    return {
        "nombre_completo": "Maria Registro Hardening",
        "edad": "31",
        "numero_telefono": "8091234567",
        "direccion_completa": "Santiago Centro",
        "modalidad_trabajo_preferida": "Salida diaria",
        "rutas_cercanas": "Centro",
        "empleo_anterior": "Casa",
        "anos_experiencia": "3 años o más",
        "areas_experiencia": ["Limpieza", "Cocina"],
        "sabe_planchar": "si",
        "contactos_referencias_laborales": "Ref laboral 1",
        "referencias_familiares_detalle": "Ref familiar 1",
        "acepta_porcentaje_sueldo": "1",
        "cedula": cedula,
    }


class CandidateRegistrationHardeningTest(unittest.TestCase):
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

    def test_internal_registration_success_verified(self):
        cedula = "001-1234567-8"
        fake_candidate = type("Cand", (), {"fila": 9101, "cedula": cedula, "nombre_completo": "Maria Registro Hardening"})()
        with patch(
            "core.legacy_handlers.robust_create_candidata",
            return_value=(RobustSaveResult(ok=True, attempts=1, error_message=""), candidate_registration.CandidateCreateState(candidate=fake_candidate, candidate_id=9101)),
        ) as robust_mock, patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")), patch("core.legacy_handlers.log_candidate_create_ok") as ok_log_mock:
            resp = self.client.post(
                "/registro_interno/",
                data=_base_data(cedula),
                follow_redirects=False,
            )

        self.assertIn(resp.status_code, (302, 303))
        self.assertTrue(resp.headers.get("Location", "").endswith("/registro_interno/"))
        robust_mock.assert_called_once()
        ok_log_mock.assert_called_once()
        kwargs = robust_mock.call_args.kwargs
        self.assertEqual(kwargs.get("max_retries"), 2)
        self.assertEqual(kwargs["expected_fields"]["cedula"], cedula)

    def test_public_registration_success_verified(self):
        cedula = "001-7654321-9"
        fake_candidate = type("Cand", (), {"fila": 9202, "cedula": cedula, "nombre_completo": "Maria Registro Hardening"})()
        with patch(
            "registro.routes.robust_create_candidata",
            return_value=(RobustSaveResult(ok=True, attempts=1, error_message=""), candidate_registration.CandidateCreateState(candidate=fake_candidate, candidate_id=9202)),
        ) as robust_mock, patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00176543219")), patch("registro.routes.log_candidate_create_ok") as ok_log_mock:
            resp = self.client.post(
                "/registro/registro_publico/",
                data=_base_data(cedula),
                follow_redirects=False,
            )

        self.assertIn(resp.status_code, (302, 303))
        self.assertTrue(resp.headers.get("Location", "").endswith("/registro/registro_publico/gracias/"))
        robust_mock.assert_called_once()
        ok_log_mock.assert_called_once()
        kwargs = robust_mock.call_args.kwargs
        self.assertEqual(kwargs.get("max_retries"), 2)
        self.assertEqual(kwargs["expected_fields"]["cedula"], cedula)

    def test_registration_retry_on_transient_commit_failure(self):
        commit_side_effect = [OperationalError("insert", {}, Exception("transient")), None]
        created_ids = iter([1001, 1002, 1003])

        def _build_candidate(_attempt: int):
            cid = next(created_ids)
            return type("Cand", (), {"fila": cid})()

        with patch("utils.candidate_registration.db.session.flush") as flush_mock, \
             patch("utils.candidate_registration.db.session.commit", side_effect=commit_side_effect) as commit_mock, \
             patch("utils.candidate_registration.db.session.rollback") as rollback_mock, \
             patch("utils.candidate_registration.db.session.add") as add_mock, \
             patch("utils.candidate_registration.verify_candidata_saved", return_value=True):
            result, _state = candidate_registration.robust_create_candidata(
                build_candidate=_build_candidate,
                expected_fields={"cedula": "001-1111111-1"},
                max_retries=2,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(commit_mock.call_count, 2)
        self.assertEqual(flush_mock.call_count, 2)
        self.assertEqual(rollback_mock.call_count, 1)
        self.assertEqual(add_mock.call_count, 2)

    def test_registration_fail_shows_error_and_no_false_success(self):
        cedula = "001-8888888-8"
        with patch(
            "registro.routes.robust_create_candidata",
            return_value=(RobustSaveResult(ok=False, attempts=3, error_message="forced_failure"), candidate_registration.CandidateCreateState()),
        ), patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00188888888")), patch("registro.routes.log_candidate_create_fail") as fail_log_mock, patch("registro.routes.log_candidate_create_ok") as ok_log_mock:
            resp = self.client.post(
                "/registro/registro_publico/",
                data=_base_data(cedula),
                follow_redirects=False,
            )

        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("/registro/registro_publico/gracias/", resp.headers.get("Location", ""))
        self.assertIn("No se pudo verificar el registro guardado".encode("utf-8"), resp.data)
        ok_log_mock.assert_not_called()
        fail_log_mock.assert_called_once()
        self.assertEqual(fail_log_mock.call_args.kwargs.get("attempt_count"), 3)

    def test_duplicate_cedula_rejected_cleanly(self):
        cedula = "001-4444444-4"
        existing = type("Dup", (), {"fila": 33, "cedula": cedula, "estado": "en_proceso"})()
        with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(existing, "00144444444")), \
             patch("registro.routes.robust_create_candidata") as robust_mock, \
             patch("registro.routes.log_candidate_create_fail") as fail_log_mock:
            second = self.client.post(
                "/registro/registro_publico/",
                data=_base_data(cedula),
                follow_redirects=False,
            )
        self.assertEqual(second.status_code, 400)
        self.assertIn("Ya existe".encode("utf-8"), second.data)
        robust_mock.assert_not_called()
        fail_log_mock.assert_called_once()

    def test_form_field_mapping_is_correct(self):
        expected_names = {
            "nombre_completo",
            "edad",
            "numero_telefono",
            "direccion_completa",
            "modalidad_trabajo_preferida",
            "rutas_cercanas",
            "empleo_anterior",
            "anos_experiencia",
            "areas_experiencia",
            "sabe_planchar",
            "contactos_referencias_laborales",
            "referencias_familiares_detalle",
            "acepta_porcentaje_sueldo",
            "cedula",
        }

        paths = [
            "templates/registro_interno.html",
            "templates/registro/registro_publico.html",
        ]

        for path in paths:
            with open(path, "r", encoding="utf-8") as fh:
                html = fh.read()
            for field in expected_names:
                self.assertIn(f'name="{field}"', html, msg=f"Falta name={field} en {path}")


if __name__ == "__main__":
    unittest.main()
