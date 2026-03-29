# -*- coding: utf-8 -*-

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_porciento_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("porciento") == "/porciento"
            assert url_for("procesos_routes.porciento") == "/porciento"

    assert flask_app.view_functions["porciento"].__module__ == "core.handlers.procesos_transacciones_handlers"


def test_porciento_render_basico_busqueda_y_seleccion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    resultados = [SimpleNamespace(fila=11, nombre_completo="Ana", cedula="001", numero_telefono="8090000001")]
    candidata = SimpleNamespace(fila=22, nombre_completo="Bea")

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_transacciones_handlers.search_candidatas_limited", return_value=resultados), \
         patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.render_template", side_effect=_fake_render):
        resp_a = client.get("/porciento?busqueda=ana", follow_redirects=False)
        assert resp_a.status_code == 200
        assert captured["template"] == "porciento.html"
        assert captured["ctx"]["resultados"] == resultados
        assert captured["ctx"]["candidata"] is None

        resp_b = client.get("/porciento?candidata=22", follow_redirects=False)
        assert resp_b.status_code == 200
        assert captured["template"] == "porciento.html"
        assert captured["ctx"]["resultados"] == []
        assert captured["ctx"]["candidata"] is candidata


def test_porciento_submit_sin_asignacion_activa_devuelve_redirect_conflict():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    candidata = SimpleNamespace(
        fila=33,
        fecha_de_pago=None,
        inicio=None,
        monto_total=None,
        porciento=None,
        estado=None,
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.parse_date", side_effect=[date(2026, 2, 10), date(2026, 2, 15)]), \
         patch("core.handlers.procesos_transacciones_handlers.parse_decimal", return_value=Decimal("1000.00")), \
         patch("core.handlers.procesos_transacciones_handlers.utc_now_naive", return_value=datetime(2026, 3, 27, 10, 0, 0)), \
         patch("core.handlers.procesos_transacciones_handlers.db.session.commit") as commit_mock, \
         patch("core.handlers.procesos_transacciones_handlers.render_template", side_effect=_fake_render):
        resp = client.post(
            "/porciento",
            data={
                "fila_id": "33",
                "fecha_pago": "2026-02-10",
                "fecha_inicio": "2026-02-15",
                "monto_total": "1000.00",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    commit_mock.assert_not_called()
    assert candidata.estado is None


def test_porciento_redirects_fallbacks_contratos_actuales():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_invalid_fila = client.post("/porciento", data={"fila_id": "abc"}, follow_redirects=False)
    assert resp_invalid_fila.status_code in (302, 303)
    assert resp_invalid_fila.headers["Location"].endswith("/porciento")

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=SimpleNamespace()):
        resp_invalid_data = client.post(
            "/porciento",
            data={"fila_id": "8", "fecha_pago": "", "fecha_inicio": "", "monto_total": ""},
            follow_redirects=False,
        )
    assert resp_invalid_data.status_code in (302, 303)
    assert resp_invalid_data.headers["Location"].endswith("/porciento?candidata=8")

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=None):
        resp_not_found = client.post("/porciento", data={"fila_id": "99"}, follow_redirects=False)
    assert resp_not_found.status_code in (302, 303)
    assert resp_not_found.headers["Location"].endswith("/porciento")
