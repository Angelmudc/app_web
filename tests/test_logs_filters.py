# -*- coding: utf-8 -*-

from datetime import datetime

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_logs_filters_by_action_type_and_user_id():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()

        admin = StaffUser.query.filter_by(username='Cruz').first()
        sec = StaffUser.query.filter_by(username='Karla').first()
        assert admin is not None and sec is not None

        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='MATCHING_SEND',
                entity_type='Solicitud',
                entity_id='111',
                summary='match-only',
                metadata_json={},
                success=True,
            )
        )
        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=admin.id,
                actor_role='admin',
                action_type='CANDIDATA_EDIT',
                entity_type='Candidata',
                entity_id='222',
                summary='edit-only',
                metadata_json={},
                success=True,
            )
        )
        db.session.commit()
        sec_id = sec.id

    login_resp = _login(client, 'Cruz', '8998')
    assert login_resp.status_code in (302, 303)

    resp = client.get(f'/admin/monitoreo/logs?action_type=MATCHING_SEND&user_id={sec_id}', follow_redirects=False)
    assert resp.status_code == 200

    html = resp.data.decode('utf-8', errors='ignore')
    assert 'match-only' in html
    assert 'edit-only' not in html
