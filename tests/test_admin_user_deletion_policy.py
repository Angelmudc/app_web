# -*- coding: utf-8 -*-

import uuid

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _new_staff_user(*, role: str = "secretaria", active: bool = True, username_prefix: str = "staff_del"):
    uname = f"{username_prefix}_{uuid.uuid4().hex[:8]}"
    row = StaffUser(username=uname, email=f"{uname}@example.com", role=role, is_active=active)
    row.set_password("Pass12345")
    db.session.add(row)
    db.session.commit()
    return row


def test_owner_can_delete_user_without_history():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _new_staff_user(username_prefix="nohist")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login(client, "Owner", "8899").status_code in (302, 303)
    resp = client.post(f"/admin/usuarios/{user_id}/eliminar", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert StaffUser.query.get(user_id) is None


def test_owner_cannot_hard_delete_user_with_history_only_deactivates():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _new_staff_user(username_prefix="withhist")
        user_id = int(user.id)
        db.session.add(
            StaffAuditLog(
                actor_user_id=user_id,
                actor_role=user.role,
                action_type="CANDIDATA_EDIT",
                entity_type="Candidata",
                entity_id="1",
                summary="historial",
                metadata_json={},
                success=True,
            )
        )
        db.session.commit()

    client = flask_app.test_client()
    assert _login(client, "Owner", "8899").status_code in (302, 303)
    resp = client.post(f"/admin/usuarios/{user_id}/eliminar", data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert "no puede eliminarse".encode("utf-8") in resp.data

    with flask_app.app_context():
        row = StaffUser.query.get(user_id)
        assert row is not None
        assert bool(row.is_active) is False


def test_admin_cannot_see_or_use_delete_user_action():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _new_staff_user(username_prefix="admblock")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    assert client.get("/admin/usuarios", follow_redirects=False).status_code == 403
    assert client.post(f"/admin/usuarios/{user_id}/eliminar", data={}, follow_redirects=False).status_code == 403


def test_secretaria_cannot_see_or_use_delete_user_action():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _new_staff_user(username_prefix="secblock")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)
    assert client.get("/admin/usuarios", follow_redirects=False).status_code == 403
    assert client.post(f"/admin/usuarios/{user_id}/eliminar", data={}, follow_redirects=False).status_code == 403


def test_inactive_user_cannot_login():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _new_staff_user(active=False, username_prefix="inactive")
        username = user.username

    client = flask_app.test_client()
    resp = _login(client, username, "Pass12345")
    assert resp.status_code == 200
    assert "Credenciales inválidas".encode("utf-8") in resp.data

