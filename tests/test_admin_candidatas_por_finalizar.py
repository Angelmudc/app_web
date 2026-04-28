# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_admin_candidatas_por_finalizar_view_renders_operational_rows():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    fake_row = {
        "candidata": SimpleNamespace(
            fila=101,
            nombre_completo="Ana Perez",
            cedula="00111122233",
            codigo=None,
        ),
        "estado_actual": "inscrita",
        "dias_sin_avance": 9,
        "faltantes_labels": ["Entrevista", "Código interno"],
        "urgencia": "critica",
        "urgencia_badge": "text-bg-danger",
        "siguiente_paso": {
            "label": "completar entrevista",
            "accion_label": "Completar entrevista",
            "url": "/entrevistas/candidata/101",
            "method": "get",
        },
        "links": [{"label": "Editar candidata", "url": "/buscar?candidata_id=101"}],
        "ready_real": False,
        "estado_inconsistente": False,
    }

    with patch("admin.routes._build_candidatas_por_finalizar_rows", return_value=[fake_row]):
        resp = client.get("/admin/candidatas/por-finalizar", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Candidatas por finalizar" in html
    assert "Ana Perez" in html
    assert "completar entrevista" in html.lower()
    assert "/entrevistas/candidata/101" in html


def test_admin_home_shows_por_finalizar_badge_when_count_positive():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    with patch("admin.routes._candidatas_por_finalizar_badge_count", return_value=5):
        resp = client.get("/home", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Por finalizar" in html
    assert ">5<" in html
