# -*- coding: utf-8 -*-

from app import app as flask_app
from config_app import db
from flask import url_for
from models import StaffNotificacion, StaffNotificacionLectura
from unittest.mock import patch


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _ensure_notification_tables():
    with flask_app.app_context():
        StaffNotificacion.__table__.create(bind=db.engine, checkfirst=True)
        StaffNotificacionLectura.__table__.create(bind=db.engine, checkfirst=True)
        db.session.query(StaffNotificacionLectura).delete()
        db.session.query(StaffNotificacion).delete()
        db.session.commit()


def test_home_public_notifications_count_list_and_mark_read():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_notification_tables()
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with flask_app.app_context():
        db.session.add(
            StaffNotificacion(
                tipo="publico_domestica_nueva",
                entity_type="candidata",
                entity_id=101,
                titulo="Nueva candidata por formulario público",
                mensaje="Ana Perez",
            )
        )
        db.session.add(
            StaffNotificacion(
                tipo="publico_empleo_general_nuevo",
                entity_type="recluta_perfil",
                entity_id=202,
                titulo="Nuevo recluta por formulario público",
                mensaje="Carlos Diaz",
            )
        )
        db.session.commit()

    count_resp = client.get("/home/notificaciones-publicas/count.json")
    assert count_resp.status_code == 200
    assert count_resp.get_json().get("unread") == 2

    list_resp = client.get("/home/notificaciones-publicas/list.json?limit=10")
    assert list_resp.status_code == 200
    payload = list_resp.get_json()
    items = payload.get("items") or []
    pending_items = payload.get("pending_items") or []
    reviewed_items = payload.get("reviewed_items") or []
    assert len(items) == 2
    assert len(pending_items) == 2
    assert len(reviewed_items) == 0
    assert int(payload.get("unread") or 0) == 2
    assert payload.get("has_more_pending") is False
    assert payload.get("has_more_reviewed") is False
    assert any((it.get("entity_type") == "candidata" and "/buscar?candidata_id=101" in (it.get("review_url") or "")) for it in items)
    assert any((it.get("entity_type") == "recluta_perfil" and "/reclutas/202" in (it.get("review_url") or "")) for it in items)

    target_id = int(items[0]["id"])
    read_resp = client.post(f"/home/notificaciones-publicas/{target_id}/leer")
    assert read_resp.status_code == 200
    assert read_resp.get_json().get("ok") is True
    assert read_resp.get_json().get("unread") == 1

    with flask_app.app_context():
        row = StaffNotificacionLectura.query.filter_by(notificacion_id=target_id).first()
        assert row is not None
        assert str(row.reader_key or "").startswith("staff:")


def test_home_public_notifications_list_limits_pending_and_reviewed():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_notification_tables()
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with flask_app.app_context():
        created_ids = []
        for idx in range(24):
            row = StaffNotificacion(
                tipo=f"publico_idx_{idx}",
                entity_type="candidata",
                entity_id=1000 + idx,
                titulo=f"Notif {idx}",
                mensaje=f"Mensaje {idx}",
            )
            db.session.add(row)
            db.session.flush()
            created_ids.append(int(row.id))
        db.session.commit()

    # Marcar 12 notificaciones como leídas para generar ambos buckets con "has_more".
    for notif_id in created_ids[:12]:
        mark_resp = client.post(f"/home/notificaciones-publicas/{notif_id}/leer")
        assert mark_resp.status_code == 200
        assert mark_resp.get_json().get("ok") is True

    list_resp = client.get("/home/notificaciones-publicas/list.json?limit=50")
    assert list_resp.status_code == 200
    payload = list_resp.get_json()

    pending_items = payload.get("pending_items") or []
    reviewed_items = payload.get("reviewed_items") or []
    items = payload.get("items") or []

    # Límite de escalabilidad: máximo 10 por grupo.
    assert len(pending_items) == 10
    assert len(reviewed_items) == 10
    assert int(payload.get("unread") or 0) == 12
    assert payload.get("has_more_pending") is True
    assert payload.get("has_more_reviewed") is True

    # Retrocompatibilidad para frontend legado.
    assert len(items) == 20
    assert all(it.get("is_read") is False for it in pending_items)
    assert all(it.get("is_read") is True for it in reviewed_items)


def test_home_public_notifications_list_derives_unread_without_count_when_no_overflow():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_notification_tables()
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with flask_app.app_context():
        for idx in range(2):
            db.session.add(
                StaffNotificacion(
                    tipo=f"publico_no_overflow_{idx}",
                    entity_type="candidata",
                    entity_id=2000 + idx,
                    titulo=f"Notif no overflow {idx}",
                    mensaje=f"Mensaje {idx}",
                )
            )
        db.session.commit()

    with patch(
        "core.handlers.home_notifications_handlers._staff_notifications_unread_count",
        side_effect=AssertionError("No debe consultarse unread_count cuando has_more_pending=False"),
    ):
        list_resp = client.get("/home/notificaciones-publicas/list.json?limit=10")

    assert list_resp.status_code == 200
    payload = list_resp.get_json() or {}
    assert payload.get("has_more_pending") is False
    assert int(payload.get("unread") or 0) == 2


def test_home_public_notifications_list_uses_count_when_pending_overflow():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_notification_tables()
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with flask_app.app_context():
        for idx in range(11):
            db.session.add(
                StaffNotificacion(
                    tipo=f"publico_overflow_{idx}",
                    entity_type="candidata",
                    entity_id=3000 + idx,
                    titulo=f"Notif overflow {idx}",
                    mensaje=f"Mensaje {idx}",
                )
            )
        db.session.commit()

    with patch(
        "core.handlers.home_notifications_handlers._staff_notifications_unread_count",
        return_value=11,
    ) as unread_mock:
        list_resp = client.get("/home/notificaciones-publicas/list.json?limit=10")

    assert list_resp.status_code == 200
    payload = list_resp.get_json() or {}
    assert payload.get("has_more_pending") is True
    assert int(payload.get("unread") or 0) == 11
    unread_mock.assert_called_once()


def test_home_public_notifications_endpoint_names_and_routes_stay_compatible():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("home_public_notifications_count") == "/home/notificaciones-publicas/count.json"
            assert url_for("home_public_notifications_list") == "/home/notificaciones-publicas/list.json"
            assert (
                url_for("home_public_notifications_mark_read", notificacion_id=9)
                == "/home/notificaciones-publicas/9/leer"
            )
            assert url_for("procesos_routes.home_public_notifications_count") == "/home/notificaciones-publicas/count.json"
            assert url_for("procesos_routes.home_public_notifications_list") == "/home/notificaciones-publicas/list.json"
            assert (
                url_for("procesos_routes.home_public_notifications_mark_read", notificacion_id=9)
                == "/home/notificaciones-publicas/9/leer"
            )

    assert flask_app.view_functions["home_public_notifications_count"].__module__ == "core.handlers.home_notifications_handlers"
    assert flask_app.view_functions["home_public_notifications_list"].__module__ == "core.handlers.home_notifications_handlers"
    assert flask_app.view_functions["home_public_notifications_mark_read"].__module__ == "core.handlers.home_notifications_handlers"
