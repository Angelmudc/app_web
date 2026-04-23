# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

import admin.routes as admin_routes
from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser
from sqlalchemy import event
from unittest.mock import patch


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_monitoreo_json_endpoints_admin_only_and_filters():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client_admin = flask_app.test_client()
    client_sec = flask_app.test_client()

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()

        admin = StaffUser.query.filter_by(username='Cruz').first()
        sec = StaffUser.query.filter_by(username='Karla').first()
        assert admin is not None and sec is not None
        sec_id = int(sec.id)

        db.session.add(StaffAuditLog(created_at=datetime.utcnow(), actor_user_id=sec.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', entity_type='Candidata', entity_id='100', summary='edit-100', metadata_json={}, success=True))
        db.session.add(StaffAuditLog(created_at=datetime.utcnow(), actor_user_id=sec.id, actor_role='secretaria', action_type='MATCHING_SEND', entity_type='Solicitud', entity_id='200', summary='send-200', metadata_json={}, success=True))
        db.session.add(StaffAuditLog(created_at=datetime.utcnow(), actor_user_id=admin.id, actor_role='admin', action_type='SOLICITUD_CREATE', entity_type='Solicitud', entity_id='300', summary='create-300', metadata_json={}, success=True))
        db.session.commit()

    assert _login(client_sec, 'Karla', '9989').status_code in (302, 303)
    denied = client_sec.get('/admin/monitoreo/logs.json', follow_redirects=False)
    assert denied.status_code == 403

    assert _login(client_admin, 'Cruz', '8998').status_code in (302, 303)

    logs_resp = client_admin.get('/admin/monitoreo/logs.json?limit=2', follow_redirects=False)
    assert logs_resp.status_code == 200
    logs_data = logs_resp.get_json()
    assert len(logs_data['items']) <= 2

    max_seen = logs_data['last_id']
    since_resp = client_admin.get(f'/admin/monitoreo/logs.json?since_id={max_seen}&limit=50', follow_redirects=False)
    assert since_resp.status_code == 200
    since_data = since_resp.get_json()
    assert since_data['items'] == []

    filt_resp = client_admin.get(
        f'/admin/monitoreo/logs.json?action_type=MATCHING_SEND&actor_user_id={sec_id}&limit=50',
        follow_redirects=False,
    )
    assert filt_resp.status_code == 200
    filt_data = filt_resp.get_json()
    assert any(i['action_type'] == 'MATCHING_SEND' for i in filt_data['items'])
    assert all(i['action_type'] == 'MATCHING_SEND' for i in filt_data['items'])

    summary_resp = client_admin.get('/admin/monitoreo/summary.json', follow_redirects=False)
    assert summary_resp.status_code == 200
    summary = summary_resp.get_json()
    assert 'today' in summary and 'week' in summary and 'month' in summary
    assert 'top' in summary and 'presence' in summary


def test_window_metrics_payload_uses_single_aggregate_select():
    flask_app.config['TESTING'] = True

    seen_sql = []

    with flask_app.app_context():
        engine = db.engine

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            sql = str(statement or "")
            if "from staff_audit_log" in sql.lower():
                seen_sql.append(sql)

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
        try:
            payload = admin_routes._window_metrics_payload(datetime.utcnow() - timedelta(days=30))
        finally:
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)

    assert isinstance(payload, dict)
    assert set(payload.keys()) == {
        "total_actions",
        "solicitudes_creadas",
        "solicitudes_publicadas",
        "candidatas_editadas",
        "candidatas_enviadas",
    }
    assert len(seen_sql) == 1


def test_summary_payload_skip_activity_stream_when_flag_disabled():
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        with patch('admin.routes._build_activity_stream_payload', side_effect=AssertionError("activity stream should be skipped")):
            payload = admin_routes._build_monitoreo_summary_payload(include_activity_stream=False)
    assert "activity_stream" not in payload


def test_summary_payload_stream_profile_keeps_expected_shape_without_presence_keys():
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        payload = admin_routes._build_monitoreo_summary_payload(
            include_presence=False,
            include_activity_stream=False,
        )
    assert "presence" not in payload
    assert "activity_stream" not in payload
    assert "operations" in payload
    assert "presence_conflicts" in payload
