# -*- coding: utf-8 -*-

import uuid
from unittest.mock import MagicMock, patch

from app import app as flask_app
from config_app import db
from models import (
    BotContactIdentity,
    CatalogoPrivado,
    CatalogoPrivadoItem,
    Cliente,
    ContratoDigital,
    PagoSolicitud,
    Solicitud,
    SolicitudCandidata,
    StaffUser,
    TiendaInteres,
    TiendaInteresItem,
)
from sqlalchemy.exc import SQLAlchemyError
from tests.t1_testkit import ensure_sqlite_compat_tables


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _ensure_staff_user(*, username: str, role: str, password: str) -> None:
    user = StaffUser.query.filter_by(username=username).first()
    if user is None:
        user = StaffUser(
            username=username,
            email=f"{username.lower()}@test.local",
            role=role,
            is_active=True,
            mfa_enabled=False,
        )
        db.session.add(user)
    user.role = role
    user.is_active = True
    user.set_password(password)
    db.session.commit()


def _login_owner(client):
    with flask_app.app_context():
        _ensure_staff_user(username="Owner", role="owner", password="admin123")
    return _login(client, "Owner", "admin123")


def _login_admin(client):
    with flask_app.app_context():
        _ensure_staff_user(username="Cruz", role="admin", password="8998")
    return _login(client, "Cruz", "8998")


def _login_secretaria(client):
    with flask_app.app_context():
        _ensure_staff_user(username="Karla", role="secretaria", password="9989")
    return _login(client, "Karla", "9989")


def _ensure_cliente_table():
    Cliente.__table__.create(bind=db.engine, checkfirst=True)


def _reset_sqlite_models(*models):
    ensure_sqlite_compat_tables(list(models), reset=True)
    from admin import routes as admin_routes

    admin_routes._TABLE_EXISTS_CACHE.clear()


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
    assert _login_owner(client).status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={"solicitud_ids": [], "summary": {}, "warnings": [], "blocked_issues": []},
    ):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None
        assert Cliente.query.get(survivor_id) is not None


def test_owner_can_delete_simple_cliente_without_mocked_plan():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="realplan_del")
        survivor = _new_cliente(prefix="realplan_keep")
        target_id = int(target.id)
        survivor_id = int(survivor.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "ELIMINAR"},
        follow_redirects=False,
    )
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
    assert _login_admin(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "ELIMINAR"},
        follow_redirects=False,
    )
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
    assert _login_secretaria(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "ELIMINAR"},
        follow_redirects=False,
    )
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
    assert _login_owner(client).status_code in (302, 303)

    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={
            "solicitud_ids": [101, 102, 103],
            "summary": {"solicitudes": 3},
            "warnings": [],
            "blocked_issues": ["Dependencia no gestionada detectada en tabla 'x'."],
        },
    ):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert "limpieza completa" in str(payload.get("message") or "").lower()

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_cannot_delete_cliente_when_has_associated_critical_data_even_if_plan_has_no_blocked_issues():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_tree_ok")
        other = _new_cliente(prefix="owner_tree_keep")
        target_id = int(target.id)
        other_id = int(other.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

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
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    delete_tree_mock.assert_not_called()

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None
        assert Cliente.query.get(other_id) is not None


def test_owner_delete_rolls_back_when_tree_delete_fails():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_tree_fail")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={"solicitud_ids": [], "summary": {}, "warnings": [], "blocked_issues": []},
    ), patch("admin.routes._delete_cliente_tree", side_effect=SQLAlchemyError("forced fail")):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert "No se pudo eliminar el cliente".encode("utf-8") in resp.data

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_delete_cliente_blocked_when_has_critical_solicitudes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_block_real")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={
            "solicitud_ids": [501],
            "summary": {"solicitudes": 1, "solicitudes_criticas": 1},
            "warnings": [],
            "blocked_issues": [
                "El cliente tiene solicitudes activas/pagadas/reemplazo/espera de pago y no puede eliminarse."
            ],
        },
    ):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert "limpieza completa" in str(payload.get("message") or "").lower()

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_delete_cliente_is_blocked_when_dependency_inspection_is_uncertain():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_uncertain")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={
            "solicitud_ids": [6201],
            "summary": {"solicitudes": 1, "solicitudes_criticas": -1, "tareas": -1},
            "warnings": ["No se pudo completar la inspección de dependencias no gestionadas."],
            "blocked_issues": [],
        },
    ), patch("admin.routes._delete_cliente_tree") as delete_tree_mock:
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert payload.get("error_code") == "dependency_inspection_failed"
    delete_tree_mock.assert_not_called()

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_collect_cliente_delete_plan_marks_chat_and_recommendation_tables_as_managed():
    from admin import routes as admin_routes

    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "chat_conversations",
        "chat_messages",
        "solicitud_recommendation_runs",
        "solicitud_recommendation_items",
        "solicitud_recommendation_selections",
    ]
    inspector.get_foreign_keys.return_value = [
        {"referred_table": "clientes", "constrained_columns": ["cliente_id"]}
    ]

    with patch("admin.routes._table_exists", return_value=False), patch(
        "admin.routes.sa_inspect",
        return_value=inspector,
    ):
        plan = admin_routes._collect_cliente_delete_plan(cliente_id=999)

    assert list(plan.get("blocked_issues") or []) == []


def test_owner_delete_is_blocked_when_confirmation_is_invalid():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_bad_confirm")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "BORRAR"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_owner_delete_accepts_cliente_code_as_confirmation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_code_confirm")
        target_id = int(target.id)
        target_code = str(target.codigo)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={"solicitud_ids": [], "summary": {}, "warnings": [], "blocked_issues": []},
    ):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": target_code},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None


def test_owner_delete_real_cliente_with_pago_requires_full_cleanup_confirmation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _reset_sqlite_models(Cliente, Solicitud, PagoSolicitud)
        target = Cliente(
            codigo="CL-REAL-PAGO-1",
            nombre_completo="Cliente Real Pago",
            email="real_pago@gmail.com",
            telefono="8095550001",
        )
        db.session.add(target)
        db.session.flush()

        solicitud = Solicitud(cliente_id=int(target.id), codigo_solicitud="SOL-REAL-PAGO-1", estado="pagada")
        db.session.add(solicitud)
        db.session.flush()

        pago = PagoSolicitud(
            solicitud_id=int(solicitud.id),
            cliente_id=int(target.id),
            monto=1500,
            tipo_pago="deposito",
            metodo_pago="transferencia",
        )
        db.session.add(pago)
        db.session.commit()
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "ELIMINAR"},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        follow_redirects=False,
    )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert "pagos_solicitud" not in str(payload)
    assert "limpieza completa" in str(payload.get("message") or "").lower()

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None
        assert Solicitud.query.filter_by(cliente_id=target_id).count() == 1
        assert PagoSolicitud.query.filter_by(cliente_id=target_id).count() == 1


def test_owner_full_cleanup_deletes_test_cliente_with_pago_and_related_records():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _reset_sqlite_models(
            Cliente,
            Solicitud,
            PagoSolicitud,
            ContratoDigital,
            CatalogoPrivado,
            CatalogoPrivadoItem,
            TiendaInteres,
            TiendaInteresItem,
            BotContactIdentity,
        )
        target = Cliente(
            codigo="CL-E2E-CLEAN-1",
            nombre_completo="Cliente Test Cleanup",
            email="cleanup@example.com",
            telefono="8095550002",
        )
        db.session.add(target)
        db.session.flush()

        solicitud = Solicitud(cliente_id=int(target.id), codigo_solicitud="SOL-E2E-CLEAN-1", estado="pagada")
        db.session.add(solicitud)
        db.session.flush()

        db.session.add(
            PagoSolicitud(
                solicitud_id=int(solicitud.id),
                cliente_id=int(target.id),
                monto=2500,
                tipo_pago="pago",
                metodo_pago="efectivo",
            )
        )
        db.session.add(
            ContratoDigital(
                id=1,
                solicitud_id=int(solicitud.id),
                cliente_id=int(target.id),
                contenido_snapshot_json={"ok": True},
            )
        )
        catalogo = CatalogoPrivado(
            nombre="Catalogo Cleanup",
            cliente_id=int(target.id),
            solicitud_id=int(solicitud.id),
            token_hash="a" * 64,
        )
        db.session.add(catalogo)
        db.session.flush()
        db.session.add(CatalogoPrivadoItem(catalogo_id=int(catalogo.id), candidata_id=1))

        interes = TiendaInteres(
            catalogo_id=int(catalogo.id),
            cliente_id=int(target.id),
            solicitud_id=int(solicitud.id),
            nombre_contacto="Cliente Test Cleanup",
            telefono_contacto="8095550002",
        )
        db.session.add(interes)
        db.session.flush()
        db.session.add(TiendaInteresItem(interes_id=int(interes.id), candidata_id=1))
        db.session.add(BotContactIdentity(phone_e164="+18095550002", is_client=True, client_id=int(target.id)))
        db.session.commit()
        target_id = int(target.id)
        target_code = str(target.codigo)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": target_code, "full_cleanup": "1"},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None
        assert Solicitud.query.filter_by(cliente_id=target_id).count() == 0
        assert PagoSolicitud.query.filter_by(cliente_id=target_id).count() == 0
        assert ContratoDigital.query.filter_by(cliente_id=target_id).count() == 0
        assert CatalogoPrivado.query.filter_by(cliente_id=target_id).count() == 0
        assert TiendaInteres.query.filter_by(cliente_id=target_id).count() == 0
        assert BotContactIdentity.query.filter_by(client_id=target_id).count() == 0


def test_owner_full_cleanup_allows_strong_owner_override_for_realish_cliente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _reset_sqlite_models(Cliente, Solicitud, PagoSolicitud)
        target = Cliente(
            codigo="2276",
            nombre_completo="Juan Perez",
            email="anfek@gmail.com",
            telefono="8296719912",
        )
        db.session.add(target)
        db.session.flush()
        solicitud = Solicitud(cliente_id=int(target.id), codigo_solicitud="SOL-OWNER-OVERRIDE-1", estado="pagada")
        db.session.add(solicitud)
        db.session.flush()
        db.session.add(
            PagoSolicitud(
                solicitud_id=int(solicitud.id),
                cliente_id=int(target.id),
                monto=999,
                tipo_pago="pago",
                metodo_pago="transferencia",
            )
        )
        db.session.commit()
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": "2276", "full_cleanup": "1"},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is None
        assert Solicitud.query.filter_by(cliente_id=target_id).count() == 0
        assert PagoSolicitud.query.filter_by(cliente_id=target_id).count() == 0


def test_admin_cannot_use_full_cleanup_even_with_direct_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _reset_sqlite_models(Cliente, Solicitud, PagoSolicitud)
        target = Cliente(
            codigo="CL-ADM-CLEANUP-1",
            nombre_completo="Cliente Admin Cleanup",
            email="cleanup_admin@example.com",
            telefono="8095550003",
        )
        db.session.add(target)
        db.session.flush()
        solicitud = Solicitud(cliente_id=int(target.id), codigo_solicitud="SOL-ADM-CLEANUP-1", estado="pagada")
        db.session.add(solicitud)
        db.session.flush()
        db.session.add(
            PagoSolicitud(
                solicitud_id=int(solicitud.id),
                cliente_id=int(target.id),
                monto=1200,
                tipo_pago="pago",
                metodo_pago="efectivo",
            )
        )
        db.session.commit()
        target_id = int(target.id)
        target_code = str(target.codigo)

    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)
    resp = client.post(
        f"/admin/clientes/{target_id}/eliminar",
        data={"confirm_delete": target_code, "full_cleanup": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    with flask_app.app_context():
        assert Cliente.query.get(target_id) is not None


def test_user_message_hides_technical_dependency_name():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_cliente_table()
        target = _new_cliente(prefix="owner_hidden_msg")
        target_id = int(target.id)

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)
    with patch(
        "admin.routes._collect_cliente_delete_plan",
        return_value={
            "solicitud_ids": [1],
            "summary": {"solicitudes": 1, "pagos": 1},
            "warnings": [],
            "blocked_issues": ["Dependencia no gestionada detectada en tabla 'pagos_solicitud'."],
        },
    ):
        resp = client.post(
            f"/admin/clientes/{target_id}/eliminar",
            data={"confirm_delete": "ELIMINAR"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"pagos_solicitud" not in resp.data
    assert "usa la opción de limpieza completa para datos de prueba".encode("utf-8") in resp.data


def test_cliente_detail_template_places_delete_in_owner_only_danger_zone():
    tpl_path = "templates/admin/_cliente_detail_summary_region.html"
    content = open(tpl_path, "r", encoding="utf-8").read()
    assert "{% if (current_user.role|default('')|lower) == 'owner' %}" in content
    assert "Zona de peligro" in content
    assert "Eliminar cliente" in content
    assert "Esta acción eliminará el cliente permanentemente. ¿Estás seguro?" in content


def test_delete_cliente_tree_deletes_chat_and_recommendation_artifacts():
    from admin import routes as admin_routes

    run_ids_query = MagicMock()
    run_ids_query.filter.return_value.all.return_value = [(1001,), (1002,)]
    item_ids_query = MagicMock()
    item_ids_query.filter.return_value.all.return_value = [(2001,)]
    chat_conv_ids_query = MagicMock()
    chat_conv_ids_query.filter.return_value.all.return_value = [(3001,)]

    enabled_tables = {
        "solicitud_recommendation_runs",
        "solicitud_recommendation_items",
        "solicitud_recommendation_selections",
        "chat_messages",
        "chat_conversations",
    }

    with patch(
        "admin.routes._table_exists",
        side_effect=lambda name: name in enabled_tables,
    ), patch(
        "admin.routes.db.session.query",
        side_effect=[run_ids_query, item_ids_query, chat_conv_ids_query],
    ), patch(
        "admin.routes.or_",
        return_value=MagicMock(name="or_clause"),
    ), patch("admin.routes.SolicitudRecommendationSelection") as selection_model, patch(
        "admin.routes.SolicitudRecommendationItem"
    ) as item_model, patch("admin.routes.SolicitudRecommendationRun") as run_model, patch(
        "admin.routes.ChatMessage"
    ) as chat_message_model, patch("admin.routes.ChatConversation") as chat_conv_model, patch(
        "admin.routes.Cliente"
    ) as cliente_model:
        selection_model.query.filter.return_value.delete.return_value = 3
        item_model.query.filter.return_value.delete.return_value = 5
        run_model.query.filter.return_value.delete.return_value = 2
        chat_message_model.query.filter.return_value.delete.return_value = 4
        chat_conv_model.query.filter.return_value.delete.return_value = 1
        cliente_model.query.filter.return_value.delete.return_value = 1

        deleted = admin_routes._delete_cliente_tree(321, solicitud_ids=[501, 502])

    assert int(deleted.get("recommendation_selections") or 0) == 3
    assert int(deleted.get("recommendation_items") or 0) == 5
    assert int(deleted.get("recommendation_runs") or 0) == 2
    assert int(deleted.get("chat_messages") or 0) == 4
    assert int(deleted.get("chat_conversations") or 0) == 1
    assert int(deleted.get("cliente") or 0) == 1


def test_delete_cliente_tree_chat_message_delete_includes_conversation_scope():
    from admin import routes as admin_routes

    chat_conv_ids_query = MagicMock()
    chat_conv_ids_query.filter.return_value.all.return_value = [(9001,), (9002,)]
    or_clause = MagicMock(name="or_clause")

    with patch(
        "admin.routes._table_exists",
        side_effect=lambda name: name in {"chat_messages", "chat_conversations"},
    ), patch(
        "admin.routes.db.session.query",
        side_effect=[chat_conv_ids_query],
    ), patch(
        "admin.routes.or_",
        return_value=or_clause,
    ) as or_mock, patch("admin.routes.ChatMessage") as chat_message_model, patch(
        "admin.routes.ChatConversation"
    ) as chat_conv_model, patch("admin.routes.Cliente") as cliente_model:
        chat_message_model.query.filter.return_value.delete.return_value = 7
        chat_conv_model.query.filter.return_value.delete.return_value = 2
        cliente_model.query.filter.return_value.delete.return_value = 1

        deleted = admin_routes._delete_cliente_tree(321, solicitud_ids=[501])

    assert or_mock.called
    chat_message_model.query.filter.assert_called_once_with(or_clause)
    assert int(deleted.get("chat_messages") or 0) == 7
    assert int(deleted.get("chat_conversations") or 0) == 2
