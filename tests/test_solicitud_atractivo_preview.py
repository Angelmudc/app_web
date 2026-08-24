# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from app import app as flask_app
from services.solicitud_atractivo_service import evaluate_solicitud_atractivo


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _enable_attractiveness_score_for_preview_tests():
    previous = flask_app.config.get("ENABLE_ATTRACTIVENESS_SCORE", False)
    flask_app.config["ENABLE_ATTRACTIVENESS_SCORE"] = True
    try:
        yield
    finally:
        flask_app.config["ENABLE_ATTRACTIVENESS_SCORE"] = previous


def test_preview_endpoint_desactivado_no_calcula_score():
    previous = flask_app.config.get("ENABLE_ATTRACTIVENESS_SCORE", False)
    flask_app.config["ENABLE_ATTRACTIVENESS_SCORE"] = False
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    try:
        resp = client.get(
            "/clientes/api/solicitud-atractivo-preview",
            query_string={"sueldo": "20000", "habitaciones": "2"},
        )
    finally:
        flask_app.config["ENABLE_ATTRACTIVENESS_SCORE"] = previous

    assert resp.status_code == 404
    data = resp.get_json()
    assert data["enabled"] is False
    assert "score" not in data


def test_preview_endpoint_devuelve_score_y_label():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = client.get(
        "/clientes/api/solicitud-atractivo-preview",
        follow_redirects=True,
        query_string={
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
            "tipo_lugar": "casa",
            "habitaciones": "3",
            "banos": "2",
            "adultos": "2",
            "funciones": ["limpieza"],
            "sueldo": "18000",
            "pasaje_mode": "incluido",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert isinstance(data.get("score"), float)
    assert isinstance(data.get("label"), str)
    assert isinstance(data.get("motivos"), list)
    assert isinstance(data.get("componentes"), dict)


def test_preview_endpoint_balanceado_devuelve_score_esperado():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = client.get(
        "/clientes/api/solicitud-atractivo-preview",
        follow_redirects=True,
        query_string={
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "4:00 PM",
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "2",
            "adultos": "2",
            "ninos": "0",
            "edades_ninos": "",
            "funciones": ["limpieza", "cocinar", "lavar"],
            "sueldo": "20000",
            "pasaje_mode": "aparte",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == 88.5
    assert data.get("componentes", {}).get("score_before_salary_excellence_cap") == 88.5
    assert data.get("componentes", {}).get("salary_excellence_cap_applied") is False


def test_preview_endpoint_salida_diaria_un_dia_distingue_sueldo_minimo_y_muy_alto():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    base_payload = {
        "modalidad_trabajo": "Salida diaria - 1 día a la semana",
        "horario": "Lunes, de 8:00 AM a 5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "pasaje_mode": "aparte",
    }

    salary_min = client.get(
        "/clientes/api/solicitud-atractivo-preview",
        follow_redirects=True,
        query_string=dict(base_payload, sueldo="5000"),
    )
    salary_far_above = client.get(
        "/clientes/api/solicitud-atractivo-preview",
        follow_redirects=True,
        query_string=dict(base_payload, sueldo="20000"),
    )

    assert salary_min.status_code == 200
    assert salary_far_above.status_code == 200
    data_min = salary_min.get_json()
    data_far_above = salary_far_above.get_json()

    assert data_min["score"] == 88.6
    assert data_far_above["score"] == 97.5
    assert data_far_above["score"] > data_min["score"]
    assert data_min["componentes"]["score_before_salary_excellence_cap"] == 88.6
    assert data_far_above["componentes"]["score_before_salary_excellence_cap"] == 100.45
    assert data_min["componentes"]["score_before_salary_excellence_cap"] != data_far_above["componentes"]["score_before_salary_excellence_cap"]
    assert data_far_above["componentes"]["salary_excellence_cap_applied"] is True
    assert data_far_above["componentes"]["salary_excellence_cap_value"] == 97.5
    assert data_min["componentes"]["salary_reference"]["reference_min"] == 5000
    assert data_min["componentes"]["salary_reference"]["reference_max"] == 7000
    assert data_far_above["componentes"]["salary_reference"]["reference_min"] == 5000
    assert data_far_above["componentes"]["salary_reference"]["reference_max"] == 7000

    expected = evaluate_solicitud_atractivo(dict(base_payload, sueldo="20000"))
    assert data_far_above["score"] == expected["score"]
    assert data_far_above["componentes"]["score_before_salary_excellence_cap"] == expected["componentes"]["score_before_salary_excellence_cap"]


def test_preview_endpoint_dormida_exacta_coincide_con_servicio_y_no_cachea():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Con dormida 💤 lunes a viernes",
        "horario": "Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        "horario_tipo": "con_dormida",
        "horario_dormida_entrada": "lunes 8:00 AM",
        "horario_dormida_salida": "viernes 4:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "2",
        "banos": "2",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "sueldo": "20000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(
        {
            "modalidad_trabajo": payload["modalidad_trabajo"],
            "horario": payload["horario"],
            "horario_tipo": payload["horario_tipo"],
            "dormida_entrada": payload["horario_dormida_entrada"],
            "dormida_salida": payload["horario_dormida_salida"],
            "tipo_lugar": payload["tipo_lugar"],
            "habitaciones": payload["habitaciones"],
            "banos": payload["banos"],
            "adultos": payload["adultos"],
            "ninos": payload["ninos"],
            "edades_ninos": payload["edades_ninos"],
            "funciones": payload["funciones"],
            "sueldo": payload["sueldo"],
            "pasaje_mode": payload["pasaje_mode"],
        }
    )
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"
    data = resp.get_json()
    assert float(data.get("score") or 0) >= 82.0
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert data.get("label") == expected["label"]


def test_preview_endpoint_casa_grande_no_critica_coincide_con_servicio_decimal():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "4",
        "banos": "4",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": [
            "sala",
            "comedor",
            "cocina",
            "salon_juegos",
            "terraza",
            "jardin",
            "estudio",
            "patio",
            "piscina",
            "marquesina",
        ],
        "sueldo": "25000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert 84.5 <= float(data.get("score") or 0) <= 85.0


def test_preview_endpoint_quincenal_25000_pasaje_aparte_no_muestra_sueldo_bajo():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Con dormida 💤 quincenal",
        "horario": "Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
        "horario_tipo": "con_dormida",
        "dormida_entrada": "lunes 8:00 AM",
        "dormida_salida": "segundo viernes 12:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "sueldo": "25000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)

    assert resp.status_code == 200
    data = resp.get_json()
    salary_item = next(item for item in data["componentes"]["items"] if item["key"] == "salario")
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert data["componentes"]["salary_reference"]["reference_min"] == 24000
    assert data["componentes"]["salary_reference"]["salary_position"] == "above_min"
    assert "por debajo del mínimo" not in salary_item["label"]
    assert salary_item["label"] == "El sueldo ofrecido está por encima del mínimo sugerido."


def test_shared_partial_incluye_barra_y_preview_backend():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert 'config.get("ENABLE_ATTRACTIVENESS_SCORE", False)' in partial
    assert "id=\"atractivoPreviewBox\"" in partial
    assert "id=\"atractivoPreviewSticky\"" in partial
    assert "id=\"atractivoPreviewStickyReason\"" in partial
    assert "id=\"atractivoPreviewBar\"" in partial
    assert "id=\"atractivoPreviewPercent\"" in partial
    assert "fetch('/clientes/api/solicitud-atractivo-preview?'" in partial
    assert "AbortController" in partial
    assert "activeRequestSeq += 1;" in partial
    assert "cache: 'no-store'" in partial
    assert "horario_dormida_entrada" in partial
    assert "horario_dormida_salida" in partial
    assert "parseFloat(result.score || 0)" in partial
    assert "toFixed(1)" in partial
    assert "if (score >= 90)" in partial
    assert "if (score >= 70)" in partial
    assert "if (score >= 60)" in partial
    assert "if (score >= 85)" not in partial
    assert "if (score >= 55)" not in partial
    assert "if (score >= 35)" not in partial


def test_detail_templates_render_atractivo_block():
    cliente_detail = _read("templates/clientes/solicitud_detail.html")
    admin_summary = _read("templates/admin/_solicitud_detail_summary_region.html")
    shared_block = _read("templates/clientes/_solicitud_atractivo_block.html")

    assert "{% include 'clientes/_solicitud_atractivo_block.html' %}" in cliente_detail
    assert "{% include 'clientes/_solicitud_atractivo_block.html' %}" in admin_summary
    assert 'config.get("ENABLE_ATTRACTIVENESS_SCORE", False)' in shared_block
    assert "Atractivo de la solicitud" in shared_block
    assert "'%d'|format(_score|int)" in shared_block
    assert "_score >= 90" in shared_block
    assert "_score >= 70" in shared_block
    assert "_score >= 60" in shared_block
    assert "_score >= 85" not in shared_block
    assert "_score >= 55" not in shared_block
    assert "_score >= 35" not in shared_block


def test_preview_endpoint_caso_5_5_coincide_con_servicio_decimal():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "5",
        "banos": "5",
        "pisos": "1",
        "adultos": "4",
        "ninos": "2",
        "edades_ninos": "",
        "observaciones": "Los niños solo viven en la casa y no requieren cuidado.",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": [
            "sala",
            "comedor",
            "cocina",
            "salon_juegos",
            "terraza",
            "jardin",
            "estudio",
            "patio",
            "piscina",
            "marquesina",
        ],
        "sueldo": "24000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 75.5


def test_preview_endpoint_caso_5_5_baja_ocupacion_21k_coincide_con_servicio_decimal():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "4:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "5",
        "banos": "5",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "",
        "observaciones": "Los niños solo viven en la casa y no requieren cuidado.",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": [
            "sala",
            "comedor",
            "cocina",
            "salon_juegos",
            "terraza",
            "jardin",
            "estudio",
            "patio",
            "piscina",
            "marquesina",
        ],
        "sueldo": "21000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 75.9


def test_preview_endpoint_ocupacion_total_ninos_sin_cuidado_coincide_con_servicio():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Con dormida 💤 lunes a sábado",
        "horario": "Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
        "horario_tipo": "con_dormida",
        "horario_dormida_entrada": "lunes 8:00 AM",
        "horario_dormida_salida": "sábado 1:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "4",
        "ninos": "3",
        "edades_ninos": "",
        "observaciones": "Los niños no requieren cuidado directo; solo viven en la casa.",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "sueldo": "22000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(
        {
            "modalidad_trabajo": payload["modalidad_trabajo"],
            "horario": payload["horario"],
            "horario_tipo": payload["horario_tipo"],
            "dormida_entrada": payload["horario_dormida_entrada"],
            "dormida_salida": payload["horario_dormida_salida"],
            "tipo_lugar": payload["tipo_lugar"],
            "habitaciones": payload["habitaciones"],
            "banos": payload["banos"],
            "pisos": payload["pisos"],
            "adultos": payload["adultos"],
            "ninos": payload["ninos"],
            "edades_ninos": payload["edades_ninos"],
            "observaciones": payload["observaciones"],
            "funciones": payload["funciones"],
            "sueldo": payload["sueldo"],
            "pasaje_mode": payload["pasaje_mode"],
        }
    )
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 79.2
    items = data.get("componentes", {}).get("items", [])
    assert any(item.get("key") == "ocupacion_total" and item.get("amount") == -2.0 for item in items)
    assert not any(item.get("key") == "combo_ninos_pequenos" for item in items)


def test_preview_endpoint_dormida_l_s_4h_4b_coincide_con_servicio_decimal():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Con dormida 💤 lunes a sábado",
        "horario": "Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
        "horario_tipo": "con_dormida",
        "horario_dormida_entrada": "lunes 8:00 AM",
        "horario_dormida_salida": "sábado 1:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "4",
        "banos": "4",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "",
        "observaciones": "Los niños no requieren cuidado directo; solo viven en la casa.",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "sueldo": "22000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(
        {
            "modalidad_trabajo": payload["modalidad_trabajo"],
            "horario": payload["horario"],
            "horario_tipo": payload["horario_tipo"],
            "dormida_entrada": payload["horario_dormida_entrada"],
            "dormida_salida": payload["horario_dormida_salida"],
            "tipo_lugar": payload["tipo_lugar"],
            "habitaciones": payload["habitaciones"],
            "banos": payload["banos"],
            "pisos": payload["pisos"],
            "adultos": payload["adultos"],
            "ninos": payload["ninos"],
            "edades_ninos": payload["edades_ninos"],
            "observaciones": payload["observaciones"],
            "funciones": payload["funciones"],
            "sueldo": payload["sueldo"],
            "pasaje_mode": payload["pasaje_mode"],
        }
    )
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 84.9
    items = data.get("componentes", {}).get("items", [])
    assert any(item.get("key") == "bonus_solicitud_normal_atractiva" and item.get("amount") == 9.55 for item in items)
    assert not any(item.get("key") == "combo_ninos_pequenos" for item in items)


def test_preview_endpoint_ninera_cocinar_lavar_sin_limpieza_coincide_con_servicio():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "2 y 3 años",
        "funciones": ["ninos", "cocinar", "lavar"],
        "areas_comunes": ["sala"],
        "sueldo": "18000",
        "pasaje_mode": "aparte",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 85.3
    items = data.get("componentes", {}).get("items", [])
    assert any(item.get("key") == "bonus_ninera_pura_atractiva" and item.get("amount") == 9.0 for item in items)
    assert not any(item.get("key") == "combo_ninos_pequenos" for item in items)


def test_preview_endpoint_payload_ui_3h_2b_supera_4h_2b_y_coincide_con_servicio():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    base_payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "banos": "2",
        "pisos": "1",
        "adultos": "4",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "lavar"],
        "areas_comunes": ["terraza", "patio"],
        "sueldo": "20000",
        "pasaje_mode": "aparte",
    }
    score_3_expected = evaluate_solicitud_atractivo(dict(base_payload, habitaciones="3"))
    score_4_expected = evaluate_solicitud_atractivo(dict(base_payload, habitaciones="4"))

    resp_3 = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=dict(base_payload, habitaciones="3"))
    resp_4 = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=dict(base_payload, habitaciones="4"))

    assert resp_3.status_code == 200
    assert resp_4.status_code == 200

    data_3 = resp_3.get_json()
    data_4 = resp_4.get_json()

    assert float(data_3.get("score") or 0) == float(score_3_expected["score"] or 0)
    assert float(data_4.get("score") or 0) == float(score_4_expected["score"] or 0)
    assert float(data_3.get("score") or 0) == 74.5
    assert float(data_4.get("score") or 0) == 73.8
    assert float(data_3.get("score") or 0) > float(data_4.get("score") or 0)


def test_preview_endpoint_3h_3b_coincide_con_servicio_y_mantiene_decimal():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "4:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "sueldo": "20000",
        "pasaje_mode": "incluido",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert float(data.get("score") or 0) == 85.6


def test_preview_endpoint_3h_3b_dos_ninos_pequenos_no_reproduce_cliff():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "4:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "2 y 3 años",
        "funciones": ["limpieza", "ninos"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "sueldo": "20000",
        "pasaje_mode": "incluido",
    }
    expected = evaluate_solicitud_atractivo(payload)
    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert float(data.get("score") or 0) == float(expected["score"] or 0)
    assert 76.0 <= float(data.get("score") or 0) <= 80.0


def test_preview_endpoint_serializa_ayuda_cuidado_ninos():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    base_payload = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "4:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "2 y 3 años",
        "funciones": ["limpieza", "ninos"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "sueldo": "20000",
        "pasaje_mode": "incluido",
    }
    sin_ayuda = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=dict(base_payload, ayuda_cuidado_ninos="sin_ayuda"))
    con_ayuda = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=dict(base_payload, ayuda_cuidado_ninos="con_ayuda"))

    assert sin_ayuda.status_code == 200
    assert con_ayuda.status_code == 200
    assert float(con_ayuda.get_json().get("score") or 0) > float(sin_ayuda.get_json().get("score") or 0)


def test_child_age_summary_endpoint_solo_confirma_pequenos_parseados():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    pequeno = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": "2 y 8 años"})
    mayor = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": "7 y 10 años"})
    mayor_8_10 = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": "8 y 10 años"})
    mayor_6_12 = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": "6 y 12 años"})
    incompleto = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": "2 años"})
    vacio = client.get("/clientes/api/child-age-summary", follow_redirects=True, query_string={"ninos": "2", "edades_ninos": ""})

    assert pequeno.status_code == 200
    assert pequeno.get_json()["has_confirmed_small_child"] is True
    assert pequeno.get_json()["small_child_count"] == 1
    assert pequeno.get_json()["supervision_count"] == 1
    assert mayor.get_json()["has_confirmed_small_child"] is False
    assert mayor_8_10.get_json()["has_confirmed_small_child"] is False
    assert mayor_8_10.get_json()["small_child_count"] == 0
    assert mayor_8_10.get_json()["supervision_count"] == 2
    assert mayor_6_12.get_json()["has_confirmed_small_child"] is False
    assert mayor_6_12.get_json()["small_child_count"] == 0
    assert mayor_6_12.get_json()["supervision_count"] == 2
    assert incompleto.get_json()["small_child_count"] == 1
    assert incompleto.get_json()["unknown_child_count"] == 1
    assert incompleto.get_json()["warnings"]
    assert vacio.get_json()["has_confirmed_small_child"] is False


def test_preview_dormida_lv_apto_dos_pequenos_con_ayuda_coincide_con_servicio():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    payload = {
        "modalidad_trabajo": "Con dormida 💤 lunes a viernes",
        "horario": "Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        "horario_tipo": "con_dormida",
        "horario_dormida_entrada": "lunes 8:00 AM",
        "horario_dormida_salida": "viernes 4:00 PM",
        "tipo_lugar": "apto",
        "habitaciones": "2",
        "banos": "2",
        "pisos": "1",
        "adultos": "2",
        "ninos": "2",
        "edades_ninos": "2 y 3 años",
        "funciones": ["limpieza", "ninos"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "ayuda_cuidado_ninos": "con_ayuda",
        "sueldo": "20000",
        "pasaje_mode": "incluido",
    }
    expected = evaluate_solicitud_atractivo(
        dict(
            payload,
            dormida_entrada=payload["horario_dormida_entrada"],
            dormida_salida=payload["horario_dormida_salida"],
        )
    )

    resp = client.get("/clientes/api/solicitud-atractivo-preview", follow_redirects=True, query_string=payload)
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["score"] == expected["score"] == 87.8
    assert data["componentes"]["score_before_salary_excellence_cap"] == expected["componentes"]["score_before_salary_excellence_cap"]
