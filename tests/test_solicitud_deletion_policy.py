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


def test_collect_solicitud_delete_plan_marks_recommendation_tables_as_managed():
    from admin import routes as admin_routes

    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "solicitud_recommendation_runs",
        "solicitud_recommendation_items",
        "solicitud_recommendation_selections",
    ]
    inspector.get_foreign_keys.return_value = [
        {"referred_table": "solicitudes", "constrained_columns": ["solicitud_id"]}
    ]

    with patch("admin.routes._table_exists", return_value=False), patch(
        "admin.routes.sa_inspect",
        return_value=inspector,
    ):
        plan = admin_routes._collect_solicitud_delete_plan(solicitud_id=734, cliente_id=621)

    assert list(plan.get("blocked_issues") or []) == []


def test_delete_solicitud_tree_deletes_recommendation_artifacts_safely():
    from admin import routes as admin_routes

    run_ids_query = MagicMock()
    run_ids_query.filter.return_value.all.return_value = [(501,), (502,)]
    item_ids_query = MagicMock()
    item_ids_query.filter.return_value.all.return_value = [(801,)]

    with patch(
        "admin.routes._table_exists",
        side_effect=lambda name: name
        in {
            "solicitud_recommendation_runs",
            "solicitud_recommendation_items",
            "solicitud_recommendation_selections",
        },
    ), patch(
        "admin.routes.db.session.query",
        side_effect=[run_ids_query, item_ids_query],
    ), patch(
        "admin.routes.or_",
        return_value=MagicMock(name="or_clause"),
    ), patch("admin.routes.SolicitudRecommendationSelection") as selection_model, patch(
        "admin.routes.SolicitudRecommendationItem"
    ) as item_model, patch("admin.routes.SolicitudRecommendationRun") as run_model, patch(
        "admin.routes.Solicitud"
    ) as solicitud_model:
        selection_model.query.filter.return_value.delete.return_value = 3
        item_model.query.filter.return_value.delete.return_value = 5
        run_model.query.filter.return_value.delete.return_value = 2
        solicitud_model.query.filter.return_value.delete.return_value = 1

        deleted = admin_routes._delete_solicitud_tree(solicitud_id=734, cliente_id=621)

    assert int(deleted.get("recommendation_selections") or 0) == 3
    assert int(deleted.get("recommendation_items") or 0) == 5
    assert int(deleted.get("recommendation_runs") or 0) == 2
    assert int(deleted.get("solicitud") or 0) == 1
