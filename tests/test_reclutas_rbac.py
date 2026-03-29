# -*- coding: utf-8 -*-

from contextlib import contextmanager
from unittest.mock import patch

from app import app as flask_app


class _FakeUser:
    def __init__(self, user_id: str, role: str):
        self._user_id = str(user_id)
        self.role = role
        self.rol = role
        self.is_active = True
        self.is_authenticated = True
        self.is_anonymous = False

    def get_id(self):
        return self._user_id


@contextmanager
def _session_user(client, user_obj: _FakeUser):
    login_manager = flask_app.login_manager
    previous_protection = login_manager.session_protection
    login_manager.session_protection = None

    with client.session_transaction() as sess:
        sess["_user_id"] = user_obj.get_id()
        sess["_fresh"] = True
        sess["_id"] = "rbac-test"

    try:
        with patch.object(login_manager, "_user_callback", side_effect=lambda uid: user_obj if str(uid) == user_obj.get_id() else None):
            yield
    finally:
        login_manager.session_protection = previous_protection


def test_reclutas_internal_requires_authentication():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/reclutas/nuevo", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_reclutas_internal_allows_admin():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with _session_user(client, _FakeUser("staff:101", "owner")):
        resp = client.get("/reclutas/nuevo", follow_redirects=False)
    assert resp.status_code == 200


def test_reclutas_internal_allows_secretaria():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with _session_user(client, _FakeUser("staff:102", "secretaria")):
        resp = client.get("/reclutas/nuevo", follow_redirects=False)
    assert resp.status_code == 200


def test_reclutas_internal_denies_authenticated_cliente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with _session_user(client, _FakeUser("2001", "cliente")):
        resp = client.get("/reclutas/nuevo", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_reclutas_mutation_denies_authenticated_cliente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with _session_user(client, _FakeUser("2002", "cliente")):
        resp = client.post("/reclutas/1/estado/aprobado", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location") or ""
    assert ("/admin/login" in location) or ("/clientes/politicas" in location)


def test_reclutas_public_routes_still_public():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert client.get("/reclutas/registro", follow_redirects=False).status_code == 200
    assert client.get("/reclutas/registro/gracias", follow_redirects=False).status_code == 200
