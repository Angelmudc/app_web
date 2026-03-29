# -*- coding: utf-8 -*-

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_pagos_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("pagos") == "/pagos"
            assert url_for("procesos_routes.pagos") == "/pagos"

    assert flask_app.view_functions["pagos"].__module__ == "core.handlers.procesos_transacciones_handlers"


def test_pagos_render_basico_busqueda_y_seleccion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    search_rows = [
        SimpleNamespace(fila=11, nombre_completo="Ana", cedula="001", numero_telefono=None),
        SimpleNamespace(fila=12, nombre_completo="Bea", cedula="002", numero_telefono="8090000002"),
    ]
    candidata = SimpleNamespace(fila=22, nombre_completo="Carla")

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_transacciones_handlers.search_candidatas_limited", return_value=search_rows), \
         patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.render_template", side_effect=_fake_render):
        resp_a = client.get("/pagos?busqueda=ana", follow_redirects=False)
        assert resp_a.status_code == 200
        assert captured["template"] == "pagos.html"
        assert captured["ctx"]["resultados"][0]["nombre"] == "Ana"
        assert captured["ctx"]["resultados"][0]["telefono"] == "No especificado"
        assert captured["ctx"]["candidata"] is None

        resp_b = client.get("/pagos?candidata=22", follow_redirects=False)
        assert resp_b.status_code == 200
        assert captured["template"] == "pagos.html"
        assert captured["ctx"]["resultados"] == []
        assert captured["ctx"]["candidata"] is candidata


def test_pagos_submit_valido_descuenta_porciento_y_actualiza_campos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    candidata = SimpleNamespace(
        fila=30,
        porciento=Decimal("500.00"),
        calificacion=None,
        fecha_de_pago=None,
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
         patch("core.handlers.procesos_transacciones_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
         patch("core.handlers.procesos_transacciones_handlers.db.session.commit") as commit_mock, \
         patch("core.handlers.procesos_transacciones_handlers.render_template", side_effect=_fake_render):
        resp = client.post(
            "/pagos",
            data={"fila": "30", "monto_pagado": "100.50", "calificacion": "Pago completo"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    commit_mock.assert_called_once()
    assert candidata.porciento == Decimal("399.50")
    assert candidata.calificacion == "Pago completo"
    assert candidata.fecha_de_pago == date(2026, 3, 27)
    assert captured["template"] == "pagos.html"
    assert captured["ctx"]["resultados"] == []
    assert captured["ctx"]["candidata"] is candidata


def test_pagos_parsing_monto_formatos_relevantes_y_clamp():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    casos = [
        ("10000", Decimal("10000.00")),
        ("10,000", Decimal("10000.00")),
        ("10.000", Decimal("10000.00")),
        ("10,000.50", Decimal("10000.50")),
        ("10.000,50", Decimal("10000.50")),
        ("100,50", Decimal("100.50")),
    ]

    for monto_raw, esperado in casos:
        candidata = SimpleNamespace(fila=1, porciento=Decimal("20000.00"), calificacion=None, fecha_de_pago=None)
        with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata), \
             patch("core.handlers.procesos_transacciones_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
             patch("core.handlers.procesos_transacciones_handlers.db.session.commit"), \
             patch("core.handlers.procesos_transacciones_handlers.render_template", return_value="ok"):
            resp = client.post(
                "/pagos",
                data={"fila": "1", "monto_pagado": monto_raw, "calificacion": "Pago completo"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert candidata.porciento == (Decimal("20000.00") - esperado).quantize(Decimal("0.01"))

    candidata_clamp = SimpleNamespace(fila=1, porciento=Decimal("100.00"), calificacion=None, fecha_de_pago=None)
    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=candidata_clamp), \
         patch("core.handlers.procesos_transacciones_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
         patch("core.handlers.procesos_transacciones_handlers.db.session.commit"), \
         patch("core.handlers.procesos_transacciones_handlers.render_template", return_value="ok"):
        resp = client.post(
            "/pagos",
            data={"fila": "1", "monto_pagado": "150.00", "calificacion": "Pago completo"},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert candidata_clamp.porciento == Decimal("0.00")


def test_pagos_redirects_fallbacks_contrato_actual():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_invalid = client.post("/pagos", data={"fila": "1", "monto_pagado": "", "calificacion": ""}, follow_redirects=False)
    assert resp_invalid.status_code in (302, 303)
    assert resp_invalid.headers["Location"].endswith("/pagos")

    resp_monto = client.post("/pagos", data={"fila": "1", "monto_pagado": "abc", "calificacion": "Pago completo"}, follow_redirects=False)
    assert resp_monto.status_code in (302, 303)
    assert resp_monto.headers["Location"].endswith("/pagos")

    with patch("core.handlers.procesos_transacciones_handlers.get_candidata_by_id", return_value=None):
        resp_not_found = client.post(
            "/pagos",
            data={"fila": "99", "monto_pagado": "100", "calificacion": "Pago completo"},
            follow_redirects=False,
        )
    assert resp_not_found.status_code in (302, 303)
    assert resp_not_found.headers["Location"].endswith("/pagos")
