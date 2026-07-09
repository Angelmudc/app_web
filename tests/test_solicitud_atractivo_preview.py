# -*- coding: utf-8 -*-

from pathlib import Path

from app import app as flask_app


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


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
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert isinstance(data.get("score"), int)
    assert isinstance(data.get("label"), str)
    assert isinstance(data.get("motivos"), list)
    assert isinstance(data.get("componentes"), dict)


def test_shared_partial_incluye_barra_y_preview_backend():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"atractivoPreviewBox\"" in partial
    assert "id=\"atractivoPreviewBar\"" in partial
    assert "id=\"atractivoPreviewPercent\"" in partial
    assert "fetch('/clientes/api/solicitud-atractivo-preview?'" in partial


def test_detail_templates_render_atractivo_block():
    cliente_detail = _read("templates/clientes/solicitud_detail.html")
    admin_summary = _read("templates/admin/_solicitud_detail_summary_region.html")
    shared_block = _read("templates/clientes/_solicitud_atractivo_block.html")

    assert "{% include 'clientes/_solicitud_atractivo_block.html' %}" in cliente_detail
    assert "{% include 'clientes/_solicitud_atractivo_block.html' %}" in admin_summary
    assert "Atractivo de la solicitud" in shared_block
