# -*- coding: utf-8 -*-

from datetime import datetime

from app import app as flask_app
from config_app import db, cache
from models import StaffAuditLog, StaffUser
from utils.enterprise_layer import metrics_secretarias, deterministic_decision_score


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_soft_lock_conflict_returns_readonly():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    cache.clear()

    c1 = flask_app.test_client()
    c2 = flask_app.test_client()
    assert _login(c1, 'Karla', '9989').status_code in (302, 303)
    assert _login(c2, 'Anyi', '0931').status_code in (302, 303)

    r1 = c1.post(
        '/admin/seguridad/locks/ping',
        json={'entity_type': 'candidata', 'entity_id': '2870', 'current_path': '/buscar?candidata_id=2870'},
        follow_redirects=False,
    )
    assert r1.status_code == 200
    assert (r1.get_json() or {}).get('state') == 'owner'

    r2 = c2.post(
        '/admin/seguridad/locks/ping',
        json={'entity_type': 'candidata', 'entity_id': '2870', 'current_path': '/buscar?candidata_id=2870'},
        follow_redirects=False,
    )
    assert r2.status_code == 200
    data = r2.get_json() or {}
    assert data.get('state') == 'readonly'
    assert 'Solo lectura' in (data.get('message') or '')


def test_takeover_creates_audit_event():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    cache.clear()

    c_sec = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c_sec, 'Anyi', '0931').status_code in (302, 303)
    assert _login(c_admin, 'Cruz', '8998').status_code in (302, 303)

    r1 = c_sec.post(
        '/admin/seguridad/locks/ping',
        json={'entity_type': 'candidata', 'entity_id': '2901', 'current_path': '/entrevista?candidata_id=2901'},
        follow_redirects=False,
    )
    assert r1.status_code == 200

    r2 = c_admin.post(
        '/admin/seguridad/locks/takeover',
        json={'entity_type': 'candidata', 'entity_id': '2901', 'reason': 'Urgencia operativa'},
        follow_redirects=False,
    )
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get('ok') is True

    with flask_app.app_context():
        row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == 'LOCK_TAKEOVER', StaffAuditLog.entity_id == '2901')
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
        assert row.success is True


def test_global_error_handler_creates_error_event():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    flask_app.config['PROPAGATE_EXCEPTIONS'] = False

    client = flask_app.test_client()
    resp = client.get('/_test/error', follow_redirects=False)
    assert resp.status_code == 500

    with flask_app.app_context():
        row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == 'ERROR_EVENT')
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
        meta = dict(row.metadata_json or {})
        assert meta.get('error_type') == 'SERVER_ERROR'


def test_metrics_secretarias_basic_counts_from_audit():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    uname = f"MetricUser_{int(datetime.utcnow().timestamp())}"
    with flask_app.app_context():
        u = StaffUser(username=uname, role='secretaria', is_active=True)
        u.set_password('12345678')
        db.session.add(u)
        db.session.commit()

        logs = [
            StaffAuditLog(actor_user_id=u.id, actor_role='secretaria', action_type='MATCHING_SEND', success=True),
            StaffAuditLog(actor_user_id=u.id, actor_role='secretaria', action_type='CANDIDATA_INTERVIEW_NEW_CREATE', success=True),
            StaffAuditLog(actor_user_id=u.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', success=True),
            StaffAuditLog(actor_user_id=u.id, actor_role='secretaria', action_type='SOLICITUD_CREATE', success=True),
        ]
        db.session.add_all(logs)
        db.session.commit()

    with flask_app.app_context():
        payload = metrics_secretarias('today')
    items = [x for x in (payload.get('items') or []) if x.get('username') == uname]
    assert items
    row = items[0]
    assert row.get('colocaciones', 0) >= 1
    assert row.get('entrevistas', 0) >= 1
    assert row.get('ediciones', 0) >= 1
    assert row.get('solicitudes', 0) >= 1


def test_decision_engine_score_is_deterministic():
    score, reasons = deterministic_decision_score(
        80,
        ['Ubicación compatible', 'Horario compatible', 'Experiencia declarada'],
        weights={'ubicacion': 2, 'horario': 1, 'experiencia': 3, 'documentacion': 0, 'referencias': 0},
    )
    assert score == 86
    assert any('ubicacion' in (r or '').lower() for r in reasons)
