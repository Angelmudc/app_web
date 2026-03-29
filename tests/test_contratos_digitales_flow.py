# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from sqlalchemy.dialects import postgresql

import contratos.routes as contratos_routes
import contratos.services as contract_services


_PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR4nGP4//8/AAX+Av5Bj7WkAAAAAElFTkSuQmCC"
)


def _dummy_contract(**kwargs):
    base = {
        "id": 10,
        "solicitud_id": 22,
        "cliente_id": 7,
        "version": 1,
        "estado": "enviado",
        "firmado_at": None,
        "anulado_at": None,
        "token_hash": "a" * 64,
        "token_version": 1,
        "token_expira_at": None,
        "primer_visto_at": None,
        "primera_ip": None,
        "primer_user_agent": None,
        "ultimo_visto_at": None,
        "pdf_final_bytea": None,
        "pdf_final_size_bytes": None,
        "snapshot_fijado_at": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_token_hash_storage_is_sha256_hex():
    h = contract_services.hash_token_storage("abc123")
    assert len(h) == 64
    assert h != contract_services.hash_token_storage("abc124")


def test_parse_signature_data_url_accepts_valid_png():
    data_url = f"data:image/png;base64,{_PNG_1X1_BASE64}"
    blob = contract_services.parse_signature_data_url(data_url)
    assert isinstance(blob, (bytes, bytearray))
    assert blob.startswith(b"\x89PNG\r\n\x1a\n")


def test_parse_signature_data_url_rejects_invalid_prefix():
    try:
        contract_services.parse_signature_data_url("data:text/plain;base64,AAAA")
    except contract_services.ContractValidationError as exc:
        assert str(exc) == "signature_invalid_format"
    else:
        raise AssertionError("expected ContractValidationError")


def test_public_contract_invalid_token_returns_400():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    bad = contract_services.TokenResolution(False, None, "invalid_signature")
    with patch("contratos.routes.resolver_token_publico", return_value=bad):
        resp = client.get("/contratos/f/tok")

    assert resp.status_code == 400
    assert "Enlace no válido" in resp.get_data(as_text=True)


def test_public_contract_signed_renders_readonly():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    signed_contract = _dummy_contract(estado="firmado", firmado_at=SimpleNamespace(isoformat=lambda: "2026-03-24T12:00:00"))
    ok = contract_services.TokenResolution(True, signed_contract, "")

    with patch("contratos.routes.resolver_token_publico", return_value=ok), \
         patch("contratos.routes.evento_contrato"), \
         patch("contratos.routes.db.session.commit"):
        resp = client.get("/contratos/f/tok")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "modo lectura" in html.lower()


def test_public_contract_humanizes_internal_values():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    snapshot = {
        "codigo_solicitud": "SOL-900",
        "tipo_servicio": "DOMESTICA_LIMPIEZA",
        "modalidad_trabajo": "salida_diaria",
        "tipo_lugar": "apto",
        "pasaje_aporte": True,
        "sueldo": "20000",
        "funciones": ["limpieza", "cocinar"],
        "rutas_cercanas": "circunvalacion_norte",
        "ninos": 2,
        "edades_ninos": "3 y 7",
    }
    c = _dummy_contract(estado="visto", contenido_snapshot_json=snapshot)
    ok = contract_services.TokenResolution(True, c, "")

    with patch("contratos.routes.resolver_token_publico", return_value=ok), \
         patch("contratos.routes.registrar_vista_publica"):
        resp = client.get("/contratos/f/tok")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Doméstica de limpieza" in html
    assert "Salida diaria" in html
    assert "Apartamento" in html
    assert "Sí" in html
    assert "RD$20,000" in html
    assert "DOMESTICA_LIMPIEZA" not in html
    assert "Datos generales de la solicitud" in html
    assert "Funciones del servicio" in html
    assert "Niños y condiciones especiales" in html
    assert html.find("Rutas cercanas") < html.find("Niños y condiciones especiales")


def test_public_sign_redirects_when_signature_ok():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_contract(estado="visto")
    ok = contract_services.TokenResolution(True, c, "")

    with patch("contratos.routes.resolver_token_publico", return_value=ok), \
         patch("contratos.routes._locked_contrato_for_signing_query", return_value=SimpleNamespace(first=lambda: c)), \
         patch("contratos.routes.firmar_contrato_atomico") as sign_mock:
        resp = client.post(
            "/contratos/f/tok/firmar",
            data={
                "signer_name": "Cliente Test",
                "signature_data": f"data:image/png;base64,{_PNG_1X1_BASE64}",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert "/contratos/f/tok" in (resp.location or "")
    sign_mock.assert_called_once()


def test_public_sign_when_already_signed_redirects_readonly():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_contract(estado="firmado")
    ok = contract_services.TokenResolution(True, c, "")

    with patch("contratos.routes.resolver_token_publico", return_value=ok), \
         patch("contratos.routes._locked_contrato_for_signing_query", return_value=SimpleNamespace(first=lambda: c)), \
         patch("contratos.routes.firmar_contrato_atomico", side_effect=contract_services.ContractValidationError("already_signed")):
        resp = client.post(
            "/contratos/f/tok/firmar",
            data={
                "signer_name": "Cliente Test",
                "signature_data": f"data:image/png;base64,{_PNG_1X1_BASE64}",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert "/contratos/f/tok" in (resp.location or "")


def test_sign_lock_query_postgres_for_update_without_joins():
    with flask_app.app_context():
        query = contratos_routes._locked_contrato_for_signing_query(123)
        sql = str(
            query.statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).upper()

    assert "FROM CONTRATOS_DIGITALES" in sql
    assert "FOR UPDATE OF CONTRATOS_DIGITALES" in sql
    assert " JOIN " not in sql
    assert "LEFT OUTER JOIN" not in sql


def test_public_sign_unexpected_error_stays_in_contract_flow_with_human_message():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_contract(estado="visto")
    ok = contract_services.TokenResolution(True, c, "")
    with patch("contratos.routes.resolver_token_publico", return_value=ok), \
         patch("contratos.routes._locked_contrato_for_signing_query", return_value=SimpleNamespace(first=lambda: c)), \
         patch("contratos.routes.firmar_contrato_atomico", side_effect=RuntimeError("db fail")):
        resp = client.post(
            "/contratos/f/tok/firmar",
            data={
                "signer_name": "Cliente Test",
                "signature_data": f"data:image/png;base64,{_PNG_1X1_BASE64}",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 503
    html = resp.get_data(as_text=True)
    assert "No se pudo completar la firma en este momento. Intenta nuevamente." in html
    assert "Contrato de Servicio de Colocación Laboral" in html
