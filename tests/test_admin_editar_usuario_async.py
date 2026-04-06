# -*- coding: utf-8 -*-

import uuid
from typing import Dict, Optional

from app import app as flask_app
from config_app import db
from models import StaffUser


def _login_owner(client):
    return client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)


def _create_staff_user(*, role: str = "secretaria", email: Optional[str] = None) -> StaffUser:
    suffix = uuid.uuid4().hex[:8]
    username = f"staff_edit_async_{suffix}"
    row = StaffUser(
        username=username,
        email=email or f"{username}@example.com",
        role=role,
        is_active=True,
    )
    row.set_password("Pass12345")
    db.session.add(row)
    db.session.commit()
    return row


def _get_staff_user(user_id: int) -> Optional[StaffUser]:
    with flask_app.app_context():
        return StaffUser.query.get(int(user_id))


def _async_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def test_editar_usuario_async_ok_local_success_without_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _create_staff_user(role="secretaria")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    unique_email = "edit.async.ok.{0}@example.com".format(uuid.uuid4().hex[:8])
    resp = client.post(
        f"/admin/usuarios/{user_id}/editar",
        data={
            "email": unique_email,
            "role": "owner",
            "new_password": "NuevaPass123",
            "new_password_confirm": "NuevaPass123",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("redirect_url") in (None, "")
    assert payload.get("update_target") == "#editarUsuarioAsyncRegion"
    assert "Usuario actualizado correctamente." in (payload.get("message") or "")
    assert "editarUsuarioAsyncRegion" in (payload.get("replace_html") or "")

    updated = _get_staff_user(user_id)
    assert updated is not None
    assert updated.email == unique_email
    assert updated.role == "secretaria"
    assert updated.check_password("NuevaPass123") is True


def test_editar_usuario_async_new_password_without_confirm_fails_and_keeps_hash():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _create_staff_user(role="admin")
        user_id = int(user.id)
        original_hash = str(user.password_hash or "")

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    resp = client.post(
        f"/admin/usuarios/{user_id}/editar",
        data={
            "email": "missing.confirm.{0}@example.com".format(uuid.uuid4().hex[:8]),
            "role": "owner",
            "new_password": "NuevaPass123",
            "new_password_confirm": "",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("error_code") == "invalid_input"
    html = payload.get("replace_html") or ""
    assert "Confirma la nueva contraseña." in html

    updated = _get_staff_user(user_id)
    assert updated is not None
    assert str(updated.password_hash or "") == original_hash
    assert updated.check_password("Pass12345") is True


def test_editar_usuario_async_validation_error_returns_inline_errors():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _create_staff_user(role="admin")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    resp = client.post(
        f"/admin/usuarios/{user_id}/editar",
        data={
            "email": "correo-invalido",
            "role": "secretaria",
            "new_password": "123",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("error_code") == "invalid_input"
    html = payload.get("replace_html") or ""
    assert "Correo inválido." in html
    assert "is-invalid" in html


def test_editar_usuario_async_email_conflict_returns_409_and_inline_error():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    conflict_email = "dup.staff.{0}@example.com".format(uuid.uuid4().hex[:8])
    with flask_app.app_context():
        user_a = _create_staff_user(role="secretaria")
        user_b = _create_staff_user(role="admin", email=conflict_email)
        user_a_id = int(user_a.id)
        _ = int(user_b.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    resp = client.post(
        f"/admin/usuarios/{user_a_id}/editar",
        data={
            "email": conflict_email,
            "role": "owner",
            "new_password": "",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("error_code") == "conflict"
    assert "email ya está en uso" in ((payload.get("message") or "").lower())
    assert "El email ya está en uso por otro usuario." in (payload.get("replace_html") or "")


def test_editar_usuario_fallback_clasico_still_redirects_and_saves():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _create_staff_user(role="admin")
        user_id = int(user.id)
        original_hash = str(user.password_hash or "")

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    fallback_email = "fallback.classic.{0}@example.com".format(uuid.uuid4().hex[:8])
    resp = client.post(
        f"/admin/usuarios/{user_id}/editar",
        data={
            "email": fallback_email,
            "role": "admin",
            "new_password": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/admin/usuarios" in (resp.headers.get("Location") or "")

    updated = _get_staff_user(user_id)
    assert updated is not None
    assert updated.email == fallback_email
    assert updated.role == "admin"
    assert str(updated.password_hash or "") == original_hash
    assert updated.check_password("Pass12345") is True


def test_editar_usuario_async_ignores_role_change_even_if_post_is_tampered():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        user = _create_staff_user(role="secretaria")
        user_id = int(user.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    tampered_email = "role.tampered.{0}@example.com".format(uuid.uuid4().hex[:8])
    resp = client.post(
        f"/admin/usuarios/{user_id}/editar",
        data={
            "email": tampered_email,
            "role": "owner",
            "new_password": "",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True

    updated = _get_staff_user(user_id)
    assert updated is not None
    assert updated.email == tampered_email
    assert updated.role == "secretaria"
