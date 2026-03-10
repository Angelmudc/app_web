# -*- coding: utf-8 -*-

import uuid
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import Cliente


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
    assert _login(client, "Owner", "8899").status_code in (302, 303)
    with patch("admin.routes._cliente_deletion_critical_dependency_counts", return_value={}):
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
    assert _login(client, "Owner", "8899").status_code in (302, 303)

    with patch("admin.routes._cliente_deletion_critical_dependency_counts", return_value={"solicitudes": 3}):
        resp = client.post(f"/admin/clientes/{target_id}/eliminar", data={}, follow_redirects=True)

    assert resp.status_code == 200
    assert "no puede eliminarse".encode("utf-8") in resp.data

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None
