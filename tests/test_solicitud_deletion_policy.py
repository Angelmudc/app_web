# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import app as flask_app
from sqlalchemy.exc import SQLAlchemyError


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _mock_solicitud(*, estado: str = "proceso", row_version: int = 1):
    return SimpleNamespace(
        id=777,
        cliente_id=321,
        codigo_solicitud="SOL-TEST-777",
        estado=estado,
        row_version=row_version,
    )


def _mock_cliente():
    return SimpleNamespace(id=321, total_solicitudes=2, fecha_ultima_actividad=None)


def test_admin_cannot_delete_solicitud_even_with_direct_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.post("/admin/clientes/321/solicitudes/777/eliminar", data={}, follow_redirects=False)
    assert resp.status_code == 403


def test_owner_delete_solicitud_blocked_for_pagada():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with patch("admin.routes.Solicitud") as solicitud_model:
        solicitud_model.query.filter_by.return_value.first_or_404.return_value = _mock_solicitud(estado="pagada")
        resp = client.post(
            "/admin/clientes/321/solicitudes/777/eliminar",
            data={},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)


def test_owner_can_delete_safe_solicitud():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    count_chain = MagicMock()
    count_chain.filter.return_value = count_chain
    count_chain.scalar.return_value = 1

    with patch("admin.routes.Solicitud") as solicitud_model, patch(
        "admin.routes._collect_solicitud_delete_plan",
        return_value={"summary": {}, "warnings": [], "blocked_issues": []},
    ), patch(
        "admin.routes._delete_solicitud_tree",
        return_value={
            "solicitudes_candidatas": 0,
            "notificaciones_solicitud": 0,
            "tokens_publicos_solicitud": 0,
            "tokens_cliente_nuevo_solicitud": 0,
            "solicitud": 1,
        },
    ), patch("admin.routes.Cliente") as cliente_model, patch(
        "admin.routes.db.session.query",
        return_value=count_chain,
    ):
        solicitud_model.query.filter_by.return_value.first_or_404.return_value = _mock_solicitud(estado="proceso")
        solicitud_model.id = 777
        cliente_model.query.get.return_value = _mock_cliente()
        resp = client.post(
            "/admin/clientes/321/solicitudes/777/eliminar",
            data={},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)


def test_owner_delete_solicitud_rolls_back_when_tree_delete_fails():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with patch("admin.routes.Solicitud") as solicitud_model, patch(
        "admin.routes._collect_solicitud_delete_plan",
        return_value={"summary": {}, "warnings": [], "blocked_issues": []},
    ), patch(
        "admin.routes._delete_solicitud_tree",
        side_effect=SQLAlchemyError("forced fail"),
    ):
        solicitud_model.query.filter_by.return_value.first_or_404.return_value = _mock_solicitud(estado="proceso")
        resp = client.post(
            "/admin/clientes/321/solicitudes/777/eliminar",
            data={},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
