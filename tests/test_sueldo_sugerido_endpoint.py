# -*- coding: utf-8 -*-

from app import app as flask_app


def _get(client, params: dict):
    return client.get("/clientes/api/sueldo-sugerido", query_string=params)


def test_endpoint_caso_sugerible_correcto():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(
        client,
        {
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "2",
            "funciones": ["limpieza", "cocinar", "lavar"],
            "adultos": "2",
            "sueldo": "18000",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_suggest"] is True
    assert data["base_salary"] == 18000
    assert data["suggested_min"] >= 16000
    assert data["suggested_max"] > data["suggested_min"]


def test_endpoint_caso_ambiguo_sin_sugerencia():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(
        client,
        {
            "modalidad_trabajo": "Salida diaria otro",
            "horario": "8 a 5",
            "tipo_lugar": "casa",
            "funciones": ["limpieza"],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_suggest"] is False
    assert "revision de servicio al cliente" in (data.get("message", "").lower())


def test_endpoint_caso_sueldo_bajo():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(
        client,
        {
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "8:00 PM",
            "tipo_lugar": "casa",
            "habitaciones": "4",
            "banos": "4",
            "funciones": ["limpieza", "cocinar", "lavar", "planchar"],
            "adultos": "6",
            "sueldo": "13000",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_suggest"] is True
    assert data["offer_status"] == "muy_baja"


def test_endpoint_caso_sueldo_competitivo():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(
        client,
        {
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "2",
            "funciones": ["limpieza", "cocinar", "lavar"],
            "adultos": "2",
            "sueldo": "20000",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_suggest"] is True
    assert data["offer_status"] == "competitiva"


def test_endpoint_caso_incompleto():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(client, {"modalidad_trabajo": "", "funciones": [], "tipo_lugar": "", "horario": ""})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_suggest"] is False
    assert "completa modalidad, horario y funciones" in data["message"].lower()


def test_endpoint_no_rompe_con_valores_raros_y_nunca_500():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    resp = _get(
        client,
        {
            "modalidad_trabajo": "\x00\x01<script>alert(1)</script>",
            "horario": "???",
            "horario_hora_entrada": "99:99 PM",
            "horario_hora_salida": "AA",
            "tipo_lugar": "oficina",
            "habitaciones": "NaN",
            "banos": "Infinity",
            "funciones": ["otro", "<bad>"],
            "adultos": "+++",
            "ninos": "-3",
            "sueldo": "RD$ --",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert data.get("can_suggest") is False
