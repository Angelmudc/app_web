# -*- coding: utf-8 -*-

from unittest.mock import patch

from app import app as flask_app
from config_app import db, cache
from models import StaffAuditLog
from utils.audit_logger import log_action
from utils.enterprise_layer import emit_critical_alert


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def _clear_logs():
    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()


def test_secretaria_denied_sensitive_routes_and_audit_dedupe():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    cache.clear()
    _clear_logs()

    client = flask_app.test_client()
    assert _login(client, 'Karla', '9989').status_code in (302, 303)

    denied_paths = [
        '/admin/errores',
        '/admin/seguridad/locks',
        '/admin/roles',
        '/admin/alertas/canales',
    ]
    for p in denied_paths:
        resp = client.get(p, follow_redirects=False)
        assert resp.status_code == 403

    # Dedupe: mismo usuario + misma ruta no debe spamear logs inmediatos.
    resp2 = client.get('/admin/errores', follow_redirects=False)
    assert resp2.status_code == 403

    with flask_app.app_context():
        rows = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == 'PERMISSION_DENIED')
            .order_by(StaffAuditLog.id.asc())
            .all()
        )
        assert rows
        error_denies = [r for r in rows if (r.route or '') == '/admin/errores']
        assert len(error_denies) == 1


def test_admin_allowed_operational_but_owner_only_routes_blocked():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    assert client.get('/admin/errores', follow_redirects=False).status_code == 200
    assert client.get('/admin/seguridad/locks', follow_redirects=False).status_code == 200
    assert client.get('/admin/health', follow_redirects=False).status_code == 200

    assert client.get('/admin/roles', follow_redirects=False).status_code == 403
    assert client.get('/admin/alertas/canales', follow_redirects=False).status_code == 403


def test_owner_can_access_roles_and_alert_channels():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login(client, 'Owner', 'admin123').status_code in (302, 303)
    assert client.get('/admin/roles', follow_redirects=False).status_code in (302, 303)
    assert client.get('/admin/roles', follow_redirects=True).status_code == 200
    assert client.get('/admin/alertas/canales', follow_redirects=False).status_code == 200


def test_critical_alert_telegram_send_and_throttle():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    cache.clear()
    _clear_logs()

    with flask_app.app_context(), patch('utils.enterprise_layer._send_telegram_message', return_value=(True, 'ok')):
        with flask_app.test_request_context('/admin/monitoreo', method='GET'):
            first = emit_critical_alert(
                rule='test_rule',
                summary='Alerta de prueba',
                entity_type='candidata',
                entity_id='9001',
                metadata={'source': 'test'},
                dedupe_seconds=180,
                telegram=True,
            )
            second = emit_critical_alert(
                rule='test_rule',
                summary='Alerta de prueba',
                entity_type='candidata',
                entity_id='9001',
                metadata={'source': 'test'},
                dedupe_seconds=180,
                telegram=True,
            )
            assert first is True
            assert second is False

        rows = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == 'ALERT_CRITICAL')
            .order_by(StaffAuditLog.id.desc())
            .all()
        )
        assert len(rows) == 1
        meta = dict(rows[0].metadata_json or {})
        assert (meta.get('sent_to') or '') == 'telegram'


def test_error_500_burst_generates_single_summary_alert():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    cache.clear()
    _clear_logs()

    with flask_app.test_request_context('/admin/test-500', method='GET'), patch('utils.enterprise_layer._send_telegram_message', return_value=(True, 'ok')):
        for _ in range(3):
            log_action(
                action_type='ERROR_EVENT',
                entity_type='system',
                entity_id='x',
                summary='error 500',
                metadata={'error_type': 'SERVER_ERROR', 'status_code': 500},
                success=False,
                error='HTTP 500',
            )

    with flask_app.app_context():
        row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == 'ALERT_CRITICAL')
            .filter(StaffAuditLog.summary.ilike('%Errores 500 repetidos%'))
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
