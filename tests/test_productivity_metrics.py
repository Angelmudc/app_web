# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_productividad_json_counts_and_order_desc():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client_admin = flask_app.test_client()
    assert _login(client_admin, 'Cruz', '8998').status_code in (302, 303)

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()

        karla = StaffUser.query.filter_by(username='Karla').first()
        anyi = StaffUser.query.filter_by(username='Anyi').first()
        assert karla is not None and anyi is not None

        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)

        rows = [
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', entity_type='Candidata', entity_id='1', summary='edit-1', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', entity_type='Candidata', entity_id='2', summary='edit-2', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='CANDIDATA_INTERVIEW_NEW_CREATE', entity_type='Candidata', entity_id='3', summary='int-new', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='CANDIDATA_INTERVIEW_LEGACY_SAVE', entity_type='Candidata', entity_id='4', summary='int-legacy', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='MATCHING_SEND', entity_type='Solicitud', entity_id='5', summary='send', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=karla.id, actor_role='secretaria', action_type='CANDIDATA_UPLOAD_DOCS', entity_type='Candidata', entity_id='6', summary='docs', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=anyi.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', entity_type='Candidata', entity_id='7', summary='edit-a', metadata_json={}, success=True),
            StaffAuditLog(created_at=now, actor_user_id=anyi.id, actor_role='secretaria', action_type='MATCHING_SEND', entity_type='Solicitud', entity_id='8', summary='send-a', metadata_json={}, success=True),
            StaffAuditLog(created_at=yesterday, actor_user_id=anyi.id, actor_role='secretaria', action_type='CANDIDATA_EDIT', entity_type='Candidata', entity_id='9', summary='edit-yesterday', metadata_json={}, success=True),
        ]
        db.session.add_all(rows)
        db.session.commit()

    resp = client_admin.get('/admin/monitoreo/productividad.json', follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json()
    users = payload.get('users') or []
    assert len(users) >= 2

    assert users[0]['username'] == 'Karla'
    assert users[0]['edits'] == 2
    assert users[0]['interviews'] == 2
    assert users[0]['sent'] == 1
    assert users[0]['total'] == 6

    anyi_row = next((u for u in users if u.get('username') == 'Anyi'), None)
    assert anyi_row is not None
    assert anyi_row['edits'] == 1
    assert anyi_row['interviews'] == 0
    assert anyi_row['sent'] == 1
    assert anyi_row['total'] == 2

    totals = [int(u.get('total') or 0) for u in users]
    assert totals == sorted(totals, reverse=True)


def test_productividad_json_forbidden_for_secretaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client_sec = flask_app.test_client()
    assert _login(client_sec, 'Karla', '9989').status_code in (302, 303)

    resp = client_sec.get('/admin/monitoreo/productividad.json', follow_redirects=False)
    assert resp.status_code == 403
