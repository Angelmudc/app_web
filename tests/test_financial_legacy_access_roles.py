# -*- coding: utf-8 -*-

from app import app as flask_app


FINANCIAL_LEGACY_ROUTES = ("/pagos", "/porciento", "/reporte_pagos")


def _login_staff(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _set_cliente_session(client):
    with client.session_transaction() as sess:
        sess["usuario"] = "cliente_demo"
        sess["role"] = "cliente"
        sess["is_admin_session"] = True
        sess["mfa_verified"] = True
        sess["logged_at"] = "2026-05-05T10:00:00"


def test_financial_legacy_routes_allow_owner_and_admin():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998")):
        client = flask_app.test_client()
        assert _login_staff(client, usuario, clave).status_code in (302, 303)
        for route in FINANCIAL_LEGACY_ROUTES:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code == 200


def test_financial_legacy_routes_deny_secretaria_cliente_y_publico():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    secretaria = flask_app.test_client()
    assert _login_staff(secretaria, "Karla", "9989").status_code in (302, 303)
    for route in FINANCIAL_LEGACY_ROUTES:
        denied = secretaria.get(route, follow_redirects=False)
        assert denied.status_code == 403

    cliente = flask_app.test_client()
    _set_cliente_session(cliente)
    for route in FINANCIAL_LEGACY_ROUTES:
        denied = cliente.get(route, follow_redirects=False)
        assert denied.status_code in (302, 303, 403)

    publico = flask_app.test_client()
    for route in FINANCIAL_LEGACY_ROUTES:
        denied = publico.get(route, follow_redirects=False)
        assert denied.status_code in (302, 303)
