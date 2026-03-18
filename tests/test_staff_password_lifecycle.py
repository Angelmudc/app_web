# -*- coding: utf-8 -*-

import uuid

import pytest

from app import app as flask_app
from models import StaffUser


def _login_admin(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _login_legacy(client, usuario: str, clave: str):
    return client.post("/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _logout_admin(client):
    return client.post("/admin/logout", data={}, follow_redirects=False)


def _get_staff(username: str):
    with flask_app.app_context():
        return StaffUser.query.filter_by(username=username).first()


@pytest.mark.parametrize("role", ["owner", "admin", "secretaria"])
def test_staff_password_lifecycle_create_edit_and_login(role: str):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    owner_client = flask_app.test_client()
    assert _login_admin(owner_client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_pw_{role}_{suffix}"
    created_password_raw = f"  Init{suffix}89  "
    created_password = created_password_raw.strip()

    create_resp = owner_client.post(
        "/admin/usuarios/nuevo",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "role": role,
            "password": created_password_raw,
        },
        follow_redirects=False,
    )
    assert create_resp.status_code in (302, 303)

    user = _get_staff(username)
    assert user is not None
    assert user.role == role
    assert user.check_password(created_password) is True

    _logout_admin(owner_client)

    created_client = flask_app.test_client()
    assert _login_admin(created_client, username, created_password).status_code in (302, 303)
    assert created_client.get("/home", follow_redirects=False).status_code == 200
    _logout_admin(created_client)

    owner_client_2 = flask_app.test_client()
    assert _login_admin(owner_client_2, "Owner", "admin123").status_code in (302, 303)

    updated_password_raw = f"  Next{suffix}76  "
    updated_password = updated_password_raw.strip()
    edit_resp = owner_client_2.post(
        f"/admin/usuarios/{user.id}/editar",
        data={
            "email": f"{username}@example.com",
            "role": role,
            "new_password": updated_password_raw,
        },
        follow_redirects=False,
    )
    assert edit_resp.status_code in (302, 303)
    _logout_admin(owner_client_2)

    old_login_client = flask_app.test_client()
    old_login = _login_admin(old_login_client, username, created_password)
    assert old_login.status_code == 200
    assert "Credenciales inválidas".encode("utf-8") in old_login.data

    new_login_client = flask_app.test_client()
    assert _login_admin(new_login_client, username, updated_password).status_code in (302, 303)
    _logout_admin(new_login_client)

    legacy_client = flask_app.test_client()
    assert _login_legacy(legacy_client, username, updated_password).status_code in (302, 303)
