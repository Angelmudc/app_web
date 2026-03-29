# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_auto_actualizar_estados_endpoint_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("auto_actualizar_estados") == "/auto_actualizar_estados"
            assert url_for("procesos_routes.auto_actualizar_estados") == "/auto_actualizar_estados"

    assert (
        flask_app.view_functions["auto_actualizar_estados"].__module__
        == "core.handlers.procesos_automatizaciones_handlers"
    )


def test_auto_actualizar_estados_sin_cambios_contrato_json():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    c1 = SimpleNamespace(
        fila=1,
        estado="inscrita_incompleta",
        codigo="C-1",
        entrevista=None,
        referencias_laboral="ok",
        referencias_familiares="ok",
        perfil=b"x",
        cedula1=b"x",
        cedula2=b"x",
        depuracion=b"x",
    )
    q = SimpleNamespace(filter_by=lambda **_k: SimpleNamespace(all=lambda: [c1]))
    fake_candidata = SimpleNamespace(query=q)
    state = {"commits": 0, "rollbacks": 0}
    fake_session = SimpleNamespace(
        commit=lambda: state.__setitem__("commits", state["commits"] + 1),
        rollback=lambda: state.__setitem__("rollbacks", state["rollbacks"] + 1),
    )

    with patch("core.handlers.procesos_automatizaciones_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_automatizaciones_handlers.db", new=SimpleNamespace(session=fake_session)):
        resp = client.get("/auto_actualizar_estados", follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["conteo_actualizadas"] == 0
    assert data["filas_actualizadas"] == []
    assert state["commits"] == 0
    assert state["rollbacks"] == 0
    assert c1.estado == "inscrita_incompleta"


def test_auto_actualizar_estados_con_actualizaciones_preserva_side_effects():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    c_ok = SimpleNamespace(
        fila=2,
        estado="inscrita_incompleta",
        codigo="C-2",
        entrevista="ok",
        referencias_laboral="ok",
        referencias_familiares="ok",
        perfil=b"x",
        cedula1=b"x",
        cedula2=b"x",
        depuracion=b"x",
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )
    c_no = SimpleNamespace(
        fila=3,
        estado="inscrita_incompleta",
        codigo=None,
        entrevista="ok",
        referencias_laboral="ok",
        referencias_familiares="ok",
        perfil=b"x",
        cedula1=b"x",
        cedula2=b"x",
        depuracion=b"x",
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )
    q = SimpleNamespace(filter_by=lambda **_k: SimpleNamespace(all=lambda: [c_ok, c_no]))
    fake_candidata = SimpleNamespace(query=q)
    state = {"commits": 0}
    fake_session = SimpleNamespace(commit=lambda: state.__setitem__("commits", state["commits"] + 1), rollback=lambda: None)

    with patch("core.handlers.procesos_automatizaciones_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_automatizaciones_handlers.db", new=SimpleNamespace(session=fake_session)), \
         patch("core.handlers.procesos_automatizaciones_handlers.utc_now_naive", return_value="NOW"):
        resp = client.get("/auto_actualizar_estados", follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["conteo_actualizadas"] == 1
    assert data["filas_actualizadas"] == [2]
    assert state["commits"] == 1
    assert c_ok.estado == "lista_para_trabajar"
    assert c_ok.fecha_cambio_estado == "NOW"
    assert c_ok.usuario_cambio_estado == "sistema"
    assert c_no.estado == "inscrita_incompleta"


def test_auto_actualizar_estados_error_rollback_y_json_500():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    q = SimpleNamespace(filter_by=lambda **_k: (_ for _ in ()).throw(RuntimeError("db fail")))
    fake_candidata = SimpleNamespace(query=q)
    state = {"rollbacks": 0}
    fake_session = SimpleNamespace(commit=lambda: None, rollback=lambda: state.__setitem__("rollbacks", state["rollbacks"] + 1))

    with patch("core.handlers.procesos_automatizaciones_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_automatizaciones_handlers.db", new=SimpleNamespace(session=fake_session)):
        resp = client.get("/auto_actualizar_estados", follow_redirects=False)

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "No se pudo actualizar estados automáticamente" in data["error"]
    assert state["rollbacks"] == 1
