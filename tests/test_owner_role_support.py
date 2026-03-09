# -*- coding: utf-8 -*-

import uuid

from app import app as flask_app
from models import StaffUser


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_owner_login_works():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = _login(client, "Owner", "8899")
    assert resp.status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200


def test_owner_has_admin_access():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "8899").status_code in (302, 303)

    crear_usuario = client.get("/admin/usuarios/nuevo", follow_redirects=False)
    monitoreo = client.get("/admin/monitoreo", follow_redirects=False)
    metricas = client.get("/admin/metricas", follow_redirects=False)

    assert crear_usuario.status_code == 200
    assert monitoreo.status_code == 200
    assert metricas.status_code == 200


def test_admin_and_secretaria_still_behave_correctly():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    admin_client = flask_app.test_client()
    assert _login(admin_client, "Cruz", "8998").status_code in (302, 303)
    assert admin_client.get("/admin/monitoreo", follow_redirects=False).status_code == 200
    assert admin_client.get("/admin/metricas", follow_redirects=False).status_code == 200
    assert admin_client.get("/admin/usuarios/nuevo", follow_redirects=False).status_code == 403

    sec_client = flask_app.test_client()
    assert _login(sec_client, "Karla", "9989").status_code in (302, 303)
    assert sec_client.get("/admin/monitoreo", follow_redirects=False).status_code == 403
    assert sec_client.get("/admin/metricas", follow_redirects=False).status_code == 403
    assert sec_client.get("/admin/usuarios/nuevo", follow_redirects=False).status_code == 403


def test_menu_visibility_for_owner():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    owner_client = flask_app.test_client()
    assert _login(owner_client, "Owner", "8899").status_code in (302, 303)
    owner_home = owner_client.get("/home", follow_redirects=False)
    assert owner_home.status_code == 200
    assert b'href="/admin/usuarios/nuevo"' in owner_home.data
    assert b'href="/admin/monitoreo"' in owner_home.data
    assert b'href="/admin/metricas"' in owner_home.data

    admin_client = flask_app.test_client()
    assert _login(admin_client, "Cruz", "8998").status_code in (302, 303)
    admin_home = admin_client.get("/home", follow_redirects=False)
    assert admin_home.status_code == 200
    assert b'href="/admin/monitoreo"' in admin_home.data
    assert b'href="/admin/metricas"' in admin_home.data
    assert b'href="/admin/usuarios/nuevo"' not in admin_home.data

    sec_client = flask_app.test_client()
    assert _login(sec_client, "Karla", "9989").status_code in (302, 303)
    sec_home = sec_client.get("/home", follow_redirects=False)
    assert sec_home.status_code == 200
    assert b'href="/admin/usuarios/nuevo"' not in sec_home.data
    assert b'href="/admin/monitoreo"' not in sec_home.data
    assert b'href="/admin/metricas"' not in sec_home.data


def test_role_normalization_accepts_owner():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    runner = flask_app.test_cli_runner()
    suffix = uuid.uuid4().hex[:8]
    username = f"owner_cli_{suffix}"
    email = f"{username}@example.com"
    result = runner.invoke(
        args=[
            "create-staff",
            "--username",
            username,
            "--role",
            "OWNER",
            "--password",
            "OwnerPass123",
            "--email",
            email,
        ]
    )

    assert result.exit_code == 0
    with flask_app.app_context():
        row = StaffUser.query.filter_by(username=username).first()
        assert row is not None
        assert row.role == "owner"
