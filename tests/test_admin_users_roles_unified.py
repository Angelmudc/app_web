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


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


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
    assert "Credenciales incorrectas".encode("utf-8") in blocked_login.data

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


def test_async_cambio_rol_devuelve_replace_html_y_target_usuarios_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_async_role_{suffix}"
    with flask_app.app_context():
        user = StaffUser(username=username, email=f"{username}@example.com", role="secretaria", is_active=True)
        user.set_password("Pass12345")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    resp = client.post(
        "/admin/roles",
        data={
            "user_id": str(user_id),
            "role": "admin",
            "next": "/admin/usuarios?q=&page=1&per_page=20",
            "_async_target": "#usuariosAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("update_target") == "#usuariosAsyncRegion"
    assert isinstance(payload.get("replace_html"), str)
    assert username in (payload.get("replace_html") or "")


def test_async_toggle_estado_devuelve_replace_html_y_target_usuarios_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_async_toggle_{suffix}"
    with flask_app.app_context():
        user = StaffUser(username=username, email=f"{username}@example.com", role="secretaria", is_active=True)
        user.set_password("Pass12345")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    resp = client.post(
        f"/admin/usuarios/{user_id}/toggle-estado",
        data={
            "next": "/admin/usuarios?q=&page=1&per_page=20",
            "_async_target": "#usuariosAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("update_target") == "#usuariosAsyncRegion"
    assert isinstance(payload.get("replace_html"), str)
    assert username in (payload.get("replace_html") or "")


def test_async_eliminar_usuario_devuelve_replace_html_y_target_usuarios_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_async_delete_{suffix}"
    with flask_app.app_context():
        user = StaffUser(username=username, email=f"{username}@example.com", role="secretaria", is_active=True)
        user.set_password("Pass12345")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    resp = client.post(
        f"/admin/usuarios/{user_id}/eliminar",
        data={
            "next": "/admin/usuarios?q=&page=1&per_page=20",
            "_async_target": "#usuariosAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("update_target") == "#usuariosAsyncRegion"
    assert isinstance(payload.get("replace_html"), str)
    assert username not in (payload.get("replace_html") or "")


def test_fallback_clasico_y_permisos_owner_only_se_mantienen():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    owner_client = flask_app.test_client()
    assert _login(owner_client, "Owner", "admin123").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    username = f"staff_fallback_{suffix}"
    with flask_app.app_context():
        user = StaffUser(username=username, email=f"{username}@example.com", role="secretaria", is_active=True)
        user.set_password("Pass12345")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    fallback_resp = owner_client.post(
        f"/admin/usuarios/{user_id}/toggle-estado",
        data={"next": "/admin/usuarios?page=1"},
        follow_redirects=False,
    )
    assert fallback_resp.status_code in (302, 303)
    assert "/admin/usuarios?page=1" in (fallback_resp.location or "")

    secretaria_client = flask_app.test_client()
    assert _login(secretaria_client, "Karla", "9989").status_code in (302, 303)
    denied_secretaria = secretaria_client.post(
        "/admin/roles",
        data={"user_id": str(user_id), "role": "admin"},
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert denied_secretaria.status_code == 403

    admin_client = flask_app.test_client()
    assert _login(admin_client, "Cruz", "8998").status_code in (302, 303)
    denied_admin = admin_client.post(
        "/admin/roles",
        data={"user_id": str(user_id), "role": "admin"},
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert denied_admin.status_code == 403
