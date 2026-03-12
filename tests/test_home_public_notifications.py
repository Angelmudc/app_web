# -*- coding: utf-8 -*-

from app import app as flask_app
from config_app import db
from models import StaffNotificacion, StaffNotificacionLectura


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

    assert _login(client, "Owner", "8899").status_code in (302, 303)

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
    assert len(items) == 2
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
