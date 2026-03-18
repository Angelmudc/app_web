# -*- coding: utf-8 -*-

import uuid
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import Cliente
from sqlalchemy.exc import SQLAlchemyError


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _ensure_cliente_table():
    Cliente.__table__.create(bind=db.engine, checkfirst=True)


def _new_cliente(*, prefix: str = "cli_del") -> Cliente:
    suffix = uuid.uuid4().hex[:8]
    codigo = f"{prefix}_{suffix}"[:20]
    email = f"{prefix}_{suffix}@example.com"
    row = Cliente(
        codigo=codigo,
        nombre_completo=f"Cliente {suffix}",
        email=email,
        telefono="8090000000",
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_owner_can_delete_simple_cliente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_del")
        survivor = _new_cliente(prefix="owner_keep")
        target_id = int(target.id)
        survivor_id = int(survivor.id)

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={"solicitud_ids": [], "summary": {}, "warnings": [], "blocked_issues": []},
    ):
        resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None
        assert Cliente.query.get(survivor_id) is not None


def test_admin_cannot_delete_cliente_even_with_direct_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="admin_block")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=False)
    assert resp.status_code == 403

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_secretaria_cannot_delete_cliente_even_with_direct_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="sec_block")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)
    resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=False)
    assert resp.status_code == 403

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_delete_is_blocked_when_critical_dependencies_exist():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_critical")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={
            "solicitud_ids": [101, 102, 103],
            "summary": {"solicitudes": 3},
            "warnings": [],
            "blocked_issues": ["Dependencia no gestionada detectada en tabla 'x'."],
        },
    ):
        resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=True)

    assert resp.status_code == 200
    assert "no puede eliminarse".encode("utf-8") in resp.data

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_can_delete_cliente_with_related_test_solicitudes_when_plan_is_safe():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_tree_ok")
        other = _new_cliente(prefix="owner_tree_keep")
        target_id = int(target.id)
        other_id = int(other.id)

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    plan = {
        "solicitud_ids": [9001, 9002],
        "summary": {
            "solicitudes": 2,
            "solicitudes_candidatas": 3,
            "reemplazos": 1,
            "tokens_publicos_solicitud": 2,
        },
        "warnings": [],
        "blocked_issues": [],
    }

    def _fake_delete_tree(cliente_id: int, solicitud_ids: list[int]):
        Cliente.query.filter(Cliente.id == int(cliente_id)).delete(synchronize_session=False)
        return {
            "solicitudes_candidatas": 3,
            "reemplazos": 1,
            "notificaciones_solicitud": 0,
            "tokens_publicos_solicitud": 2,
            "tokens_cliente_nuevo_solicitud": 0,
            "solicitudes": 2,
            "tareas": 0,
            "notificaciones_cliente": 0,
            "tokens_publicos_cliente": 0,
            "tokens_cliente_nuevo_cliente": 0,
            "cliente": 1,
        }

    with patch("admin.routes._collect_cliente_delete_plan", return_value=plan), patch(
        "admin.routes._delete_cliente_tree",
        side_effect=_fake_delete_tree,
    ) as delete_tree_mock:
        resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    delete_tree_mock.assert_called_once_with(target_id, solicitud_ids=[9001, 9002])

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None
        assert Cliente.query.get(other_id) is not None


def test_owner_delete_rolls_back_when_tree_delete_fails():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_tree_fail")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={"solicitud_ids": [55], "summary": {"solicitudes": 1}, "warnings": [], "blocked_issues": []},
    ), patch("admin.routes._delete_cliente_tree", side_effect=SQLAlchemyError("forced fail")):
        resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=True)

    assert resp.status_code == 200
    assert "No se pudo eliminar el cliente".encode("utf-8") in resp.data

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None
