# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app
from utils.candidate_registration import CandidateCreateState
from utils.robust_save import RobustSaveResult


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def _base_data(cedula: str = "001-1234567-8") -> dict:
    return {
        "nombre_completo": "Maria Registro Interno",
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


def test_registro_interno_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("registro_interno") == "/registro_interno/"
            assert url_for("candidatas_routes.registro_interno") == "/registro_interno/"

    assert flask_app.view_functions["registro_interno"].__module__ == "core.handlers.registro_interno_handlers"


def test_registro_interno_render_basico_formulario():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get("/registro_interno/", follow_redirects=False)
    assert resp.status_code == 200
    assert "registro_interno".encode("utf-8") in resp.data.lower()


def test_registro_interno_submit_valido_redirect_y_log_ok():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    fake_candidate = SimpleNamespace(fila=991, cedula="001-1234567-8", nombre_completo="Maria Registro Interno")
    with patch(
        "core.handlers.registro_interno_handlers.legacy_h.robust_create_candidata",
        return_value=(
            RobustSaveResult(ok=True, attempts=1, error_message=""),
            CandidateCreateState(candidate=fake_candidate, candidate_id=991),
        ),
    ) as robust_mock, patch(
        "core.handlers.registro_interno_handlers.legacy_h.find_duplicate_candidata_by_cedula",
        return_value=(None, "00112345678"),
    ), patch("core.handlers.registro_interno_handlers.legacy_h.log_candidate_create_ok") as ok_log_mock:
        resp = client.post("/registro_interno/", data=_base_data(), follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/registro_interno/")
    robust_mock.assert_called_once()
    ok_log_mock.assert_called_once()


def test_registro_interno_validacion_fallback_campos_faltantes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    payload = _base_data()
    payload["nombre_completo"] = ""

    with patch("core.handlers.registro_interno_handlers.legacy_h.log_candidate_create_fail") as fail_log_mock:
        resp = client.post("/registro_interno/", data=payload, follow_redirects=False)

    assert resp.status_code == 400
    assert "por favor completa".encode("utf-8") in resp.data.lower()
    fail_log_mock.assert_called_once()
