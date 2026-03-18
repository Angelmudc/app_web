# -*- coding: utf-8 -*-

from app import app as flask_app


def _login_admin(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _login_legacy(client, usuario: str, clave: str):
    return client.post("/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_owner_admin_login_accepts_real_password():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = _login_admin(client, "owner", "admin123")
    assert resp.status_code in (302, 303)
    assert client.get("/home", follow_redirects=False).status_code == 200


def test_owner_legacy_login_accepts_real_password():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = _login_legacy(client, "owner", "admin123")
    assert resp.status_code in (302, 303)
    assert client.get("/home", follow_redirects=False).status_code == 200
