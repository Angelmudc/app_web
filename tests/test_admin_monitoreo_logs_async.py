# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def test_monitoreo_logs_filtros_async_devuelve_json_con_parcial():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()

        admin = StaffUser.query.filter_by(username="Cruz").first()
        sec = StaffUser.query.filter_by(username="Karla").first()
        assert admin is not None and sec is not None

        db.session.add(StaffAuditLog(
            created_at=datetime.utcnow(),
            actor_user_id=sec.id,
            actor_role="secretaria",
            action_type="MATCHING_SEND",
            entity_type="Solicitud",
            entity_id="111",
            summary="match-only-async",
            metadata_json={},
            success=True,
        ))
        db.session.add(StaffAuditLog(
            created_at=datetime.utcnow(),
            actor_user_id=admin.id,
            actor_role="admin",
            action_type="CANDIDATA_EDIT",
            entity_type="Candidata",
            entity_id="222",
            summary="edit-only-async",
            metadata_json={},
            success=True,
        ))
        db.session.commit()
        sec_id = sec.id

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.get(
        f"/admin/monitoreo/logs?action_type=MATCHING_SEND&user_id={sec_id}",
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["update_target"] == "#monitoreoLogsAsyncRegion"
    assert "match-only-async" in data["replace_html"]
    assert "edit-only-async" not in data["replace_html"]


def test_monitoreo_logs_paginacion_async_sin_recarga():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()
        admin = StaffUser.query.filter_by(username="Cruz").first()
        assert admin is not None

        base = datetime(2026, 3, 1, 8, 0, 0)
        for i in range(1, 27):
            db.session.add(StaffAuditLog(
                created_at=base + timedelta(minutes=i),
                actor_user_id=admin.id,
                actor_role="admin",
                action_type="SOLICITUD_CREATE",
                entity_type="Solicitud",
                entity_id=str(i),
                summary=f"page-log-{i:02d}",
                metadata_json={},
                success=True,
            ))
        db.session.commit()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.get(
        "/admin/monitoreo/logs?page=2&per_page=10",
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["page"] == 2
    assert "page-log-16" in data["replace_html"]
    assert "page-log-26" not in data["replace_html"]
    assert "data-admin-async-link" in data["replace_html"]


def test_monitoreo_logs_fallback_clasico_sigue_intacto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.get("/admin/monitoreo/logs?action_type=MATCHING_SEND", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Logs de Monitoreo Staff" in html
    assert 'id="monitoreoLogsAsyncRegion"' in html
    assert 'data-admin-async-form' in html


def test_monitoreo_logs_manejo_errores_async_respuesta_limpia():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._logs_filtered_query", side_effect=RuntimeError("boom")):
        resp = client.get("/admin/monitoreo/logs", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 500
    data = resp.get_json()
    assert data["success"] is False
    assert data["error_code"] == "internal_error"
    assert "No se pudo actualizar el listado de logs" in data["message"]
