# -*- coding: utf-8 -*-

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_inscripcion_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("inscripcion") == "/inscripcion"
            assert url_for("procesos_routes.inscripcion") == "/inscripcion"

    assert flask_app.view_functions["inscripcion"].__module__ == "core.handlers.procesos_transacciones_handlers"


def test_inscripcion_render_basico_busqueda_y_seleccion():
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
        resp_a = client.get("/inscripcion?buscar=ana", follow_redirects=False)
        assert resp_a.status_code == 200
        assert captured["template"] == "inscripcion.html"
        assert captured["ctx"]["resultados"] == resultados
        assert captured["ctx"]["candidata"] is None

        resp_b = client.get("/inscripcion?candidata_seleccionada=22", follow_redirects=False)
        assert resp_b.status_code == 200
        assert captured["template"] == "inscripcion.html"
        assert captured["ctx"]["resultados"] == []
        assert captured["ctx"]["candidata"] is candidata


def test_inscripcion_submit_valido_genera_codigo_guarda_campos_y_estado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    candidata = SimpleNamespace(
        fila=30,
        codigo=None,
        medio_inscripcion=None,
        inscripcion=False,
        monto=None,
        fecha=None,
        estado=None,
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.legacy_h.generar_codigo_unico", return_value="C-777"), \
         patch("core.handlers.procesos_transacciones_handlers.parse_decimal", return_value=Decimal("1500.00")), \
         patch("core.handlers.procesos_transacciones_handlers.parse_date", return_value=date(2026, 3, 20)), \
         patch("core.handlers.procesos_transacciones_handlers.utc_now_naive", return_value=datetime(2026, 3, 27, 11, 30, 0)), \
         patch("core.handlers.procesos_transacciones_handlers.db.session.commit") as commit_mock, \
         patch("core.handlers.procesos_transacciones_handlers.maybe_update_estado_por_completitud") as update_mock, \
         patch("core.handlers.procesos_transacciones_handlers.render_template", side_effect=_fake_render):
        resp = client.post(
            "/inscripcion",
            data={
                "guardar_inscripcion": "1",
                "candidata_id": "30",
                "medio": "Vía Oficina",
                "estado": "si",
                "monto": "1500.00",
                "fecha": "2026-03-20",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    commit_mock.assert_called_once()
    update_mock.assert_called_once_with(candidata, actor="Karla")
    assert candidata.codigo == "C-777"
    assert candidata.medio_inscripcion == "Vía Oficina"
    assert candidata.inscripcion is True
    assert candidata.monto == Decimal("1500.00")
    assert candidata.fecha == date(2026, 3, 20)
    assert candidata.estado == "inscrita"
    assert candidata.fecha_cambio_estado == datetime(2026, 3, 27, 11, 30, 0)
    assert candidata.usuario_cambio_estado == "Karla"
    assert captured["template"] == "inscripcion.html"
    assert captured["ctx"]["candidata"] is candidata


def test_inscripcion_submit_estado_no_transiciona_a_proceso_inscripcion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    candidata = SimpleNamespace(
        fila=31,
        codigo="C-100",
        medio_inscripcion="Transferencia Bancaria",
        inscripcion=True,
        monto=Decimal("500"),
        fecha=date(2026, 3, 1),
        estado=None,
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.parse_decimal", return_value=None), \
         patch("core.handlers.procesos_transacciones_handlers.parse_date", return_value=None), \
         patch("core.handlers.procesos_transacciones_handlers.db.session.commit"), \
         patch("core.handlers.procesos_transacciones_handlers.maybe_update_estado_por_completitud"), \
         patch("core.handlers.procesos_transacciones_handlers.render_template", return_value="ok"):
        resp = client.post(
            "/inscripcion",
            data={
                "guardar_inscripcion": "1",
                "candidata_id": "31",
                "medio": "",
                "estado": "no",
                "monto": "",
                "fecha": "",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert candidata.estado == "proceso_inscripcion"
    assert candidata.monto == Decimal("500")
    assert candidata.fecha == date(2026, 3, 1)


def test_inscripcion_redirects_fallbacks_contrato_actual():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_invalid_id = client.post(
        "/inscripcion",
        data={"guardar_inscripcion": "1", "candidata_id": "abc"},
        follow_redirects=False,
    )
    assert resp_invalid_id.status_code in (302, 303)
    assert resp_invalid_id.headers["Location"].endswith("/inscripcion")

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=None):
        resp_not_found = client.post(
            "/inscripcion",
            data={"guardar_inscripcion": "1", "candidata_id": "99"},
            follow_redirects=False,
        )
    assert resp_not_found.status_code in (302, 303)
    assert resp_not_found.headers["Location"].endswith("/inscripcion")

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=SimpleNamespace(codigo=None)), \
         patch("core.handlers.procesos_transacciones_handlers.legacy_h.generar_codigo_unico", side_effect=RuntimeError("fail")):
        resp_code_fail = client.post(
            "/inscripcion",
            data={"guardar_inscripcion": "1", "candidata_id": "10"},
            follow_redirects=False,
        )
    assert resp_code_fail.status_code in (302, 303)
    assert resp_code_fail.headers["Location"].endswith("/inscripcion")
