# -*- coding: utf-8 -*-

import uuid

from app import app as flask_app
from config_app import db
from models import StaffUser


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _get_user(username: str):
    with flask_app.app_context():
        return StaffUser.query.filter_by(username=username).first()


def test_owner_can_open_unified_users_module():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    resp = client.get("/admin/usuarios", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Gestión de usuarios y roles" in html
    assert "Nuevo usuario" in html


def test_owner_can_create_user_and_change_role_and_toggle_status():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_unif_{suffix}"
    email = f"{username}@example.com"
    password = "SecurePass123"

    create_resp = client.post(
        "/admin/usuarios/nuevo",
        data={
            "username": username,
            "email": email,
            "role": "secretaria",
            "password": password,
        },
        follow_redirects=False,
    )
    assert create_resp.status_code in (302, 303)

    user = _get_user(username)
    assert user is not None
    assert user.role == "secretaria"
    assert bool(user.is_active) is True

    role_resp = client.post(
        "/admin/roles",
        data={"user_id": str(user.id), "role": "admin"},
        follow_redirects=False,
    )
    assert role_resp.status_code in (302, 303)

    user = _get_user(username)
    assert user is not None
    assert user.role == "admin"

    toggle_resp = client.post(f"/admin/usuarios/{user.id}/toggle-estado", data={}, follow_redirects=False)
    assert toggle_resp.status_code in (302, 303)
    user = _get_user(username)
    assert user is not None
    assert bool(user.is_active) is False

    # Usuario inactivo no debe poder iniciar sesión.
    blocked_client = flask_app.test_client()
    blocked_login = _login(blocked_client, username, password)
    assert blocked_login.status_code == 200
    assert "Credenciales inválidas".encode("utf-8") in blocked_login.data

    # Reactivar desde el mismo módulo.
    re_toggle_resp = client.post(f"/admin/usuarios/{user.id}/toggle-estado", data={}, follow_redirects=False)
    assert re_toggle_resp.status_code in (302, 303)
    user = _get_user(username)
    assert user is not None
    assert bool(user.is_active) is True

    # Limpieza segura para no afectar otras pruebas.
    with flask_app.app_context():
        row = StaffUser.query.filter_by(username=username).first()
        if row:
            row.is_active = False
            db.session.commit()


def test_secretaria_and_admin_cannot_open_unified_users_module():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    sec_client = flask_app.test_client()
    assert _login(sec_client, "Karla", "9989").status_code in (302, 303)
    assert sec_client.get("/admin/usuarios", follow_redirects=False).status_code == 403

    admin_client = flask_app.test_client()
    assert _login(admin_client, "Cruz", "8998").status_code in (302, 303)
    assert admin_client.get("/admin/usuarios", follow_redirects=False).status_code == 403

