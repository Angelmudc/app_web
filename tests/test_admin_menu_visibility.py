# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_owner_sees_create_user_link_and_can_open_route():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "8899").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert b'href="/admin/usuarios"' in home.data
    assert "Usuarios y roles".encode("utf-8") in home.data
    assert "Registrar candidata".encode("utf-8") in home.data

    users_module = client.get("/admin/usuarios", follow_redirects=False)
    assert users_module.status_code == 200
    create_user = client.get("/admin/usuarios/nuevo", follow_redirects=False)
    assert create_user.status_code == 200


def test_admin_sees_register_candidate_link_and_can_open_route():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Registrar candidata".encode("utf-8") in home.data
    assert "Usuarios y roles".encode("utf-8") not in home.data

    reg_candidata = client.get("/registro_interno/", follow_redirects=False)
    assert reg_candidata.status_code == 200


def test_secretaria_does_not_see_admin_menu_links():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Karla", "9989").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Usuarios y roles".encode("utf-8") not in home.data
    assert "Registrar candidata".encode("utf-8") not in home.data

    # Seguridad existente: crear usuario es owner-only.
    denied = client.get("/admin/usuarios/nuevo", follow_redirects=False)
    assert denied.status_code == 403
