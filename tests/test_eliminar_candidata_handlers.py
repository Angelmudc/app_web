# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_admin(client):
    return client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _ScalarQueryStub:
    def __init__(self, value):
        self.value = value

    def filter(self, *_args, **_kwargs):
        return self

    def scalar(self):
        return self.value


class _DummyCandidata:
    def __init__(self, fila=1):
        self.fila = fila
        self.id = fila
        self.nombre_completo = "Ana Demo"
        self.edad = "30"
        self.numero_telefono = "8090000000"
        self.direccion_completa = "Calle A"
        self.modalidad_trabajo_preferida = "Dormida"
        self.rutas_cercanas = "Centro"
        self.empleo_anterior = "Casa X"
        self.anos_experiencia = "4"
        self.areas_experiencia = "Limpieza"
        self.contactos_referencias_laborales = "Ref Lab"
        self.referencias_familiares_detalle = "Ref Fam Detalle"
        self.referencias_laboral = "Ref Lab"
        self.referencias_familiares = "Ref Fam"
        self.cedula = "001-0000000-1"
        self.codigo = "CAN-1"
        self.solicitudes = []
        self.llamadas = []
        self.entrevista = ""
        self.cedula1 = None
        self.cedula2 = None
        self.perfil = None
        self.depuracion = None


def test_eliminar_candidata_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("eliminar_candidata") == "/candidatas/eliminar"
            assert url_for("candidatas_routes.eliminar_candidata") == "/candidatas/eliminar"

    assert flask_app.view_functions["eliminar_candidata"].__module__ == "core.handlers.eliminar_candidata_handlers"


def test_eliminar_candidata_requires_auth_redirects_to_admin_login():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with client.session_transaction() as sess:
        sess.clear()

    resp = client.get("/candidatas/eliminar", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_eliminar_candidata_basic_render_contract():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    resp = client.get("/candidatas/eliminar", follow_redirects=False)
    assert resp.status_code == 200
    assert "Eliminar Candidata" in resp.get_data(as_text=True)


def test_eliminar_candidata_secretaria_no_puede_confirmar():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata(fila=1)
    with patch("core.legacy_handlers.db.session.get", return_value=cand), \
         patch("core.legacy_handlers.db.session.delete") as delete_mock, \
         patch("core.legacy_handlers.db.session.commit") as commit_mock:
        resp = client.post(
            "/candidatas/eliminar",
            data={"confirmar_eliminacion": "1", "candidata_id": "1", "busqueda": "ana"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert "Solo admin puede confirmar la eliminaci" in resp.get_data(as_text=True)
    delete_mock.assert_not_called()
    commit_mock.assert_not_called()


def test_eliminar_candidata_guard_bloquea_si_tiene_historial():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    cand = _DummyCandidata(fila=7)
    cand.solicitudes = [SimpleNamespace(id=9)]
    cand.llamadas = []

    with patch("core.legacy_handlers.db.session.get", return_value=cand), \
         patch("core.legacy_handlers.db.session.delete") as delete_mock, \
         patch("core.legacy_handlers.db.session.commit") as commit_mock:
        resp = client.post(
            "/candidatas/eliminar",
            data={"confirmar_eliminacion": "1", "candidata_id": "7", "busqueda": "ana"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert "No se puede eliminar esta candidata porque tiene historial" in resp.get_data(as_text=True)
    delete_mock.assert_not_called()
    commit_mock.assert_not_called()


def test_eliminar_candidata_admin_sin_historial_elimina_y_redirige():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    cand = _DummyCandidata(fila=11)
    cand.solicitudes = []
    cand.llamadas = []

    with patch("core.legacy_handlers.db.session.get", return_value=cand), \
         patch("core.legacy_handlers.db.session.query", return_value=_ScalarQueryStub(0)), \
         patch("core.legacy_handlers.db.session.delete") as delete_mock, \
         patch("core.legacy_handlers.db.session.commit") as commit_mock:
        resp = client.post(
            "/candidatas/eliminar",
            data={"confirmar_eliminacion": "1", "candidata_id": "11", "busqueda": "ana"},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert (resp.headers.get("Location") or "").endswith("/candidatas/eliminar?busqueda=ana")
    delete_mock.assert_called_once_with(cand)
    commit_mock.assert_called_once()


def test_eliminar_candidata_fallback_id_invalido_no_rompe_flujo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    with patch("core.legacy_handlers.db.session.delete") as delete_mock:
        resp = client.post(
            "/candidatas/eliminar",
            data={"confirmar_eliminacion": "1", "candidata_id": "x", "busqueda": ""},
            follow_redirects=False,
        )

    body = resp.get_data(as_text=True).lower()
    assert resp.status_code == 200
    assert "id de candidata" in body
    assert "inv" in body
    delete_mock.assert_not_called()
