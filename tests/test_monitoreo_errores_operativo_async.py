# -*- coding: utf-8 -*-

from datetime import datetime
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario='Cruz', clave='8998'):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def _async_headers():
    return {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Admin-Async': '1',
    }


def _ensure_user(username='Karla', role='secretaria', password='9989'):
    user = StaffUser.query.filter_by(username=username).first()
    if user is None:
        user = StaffUser(username=username, role=role, is_active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    return user


def test_monitoreo_secretaria_template_incluye_region_async():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        user = _ensure_user()

    assert _login(client).status_code in (302, 303)
    resp = client.get(f'/admin/monitoreo/secretarias/{user.id}', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="monitoreoSecretariaAsyncRegion"' in html
    assert 'data-admin-async-form' in html


def test_monitoreo_secretaria_get_async_devuelve_replace_html_con_target():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        user = _ensure_user()
        user_id = int(user.id)
        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=user.id,
                actor_role='secretaria',
                action_type='CANDIDATA_EDIT',
                entity_type='candidata',
                entity_id='901',
                summary='edicion monitoreo',
                metadata_json={},
                success=True,
            )
        )
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.get(f'/admin/monitoreo/secretarias/{user_id}?date_from=&date_to=', headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('update_target') == '#monitoreoSecretariaAsyncRegion'
    assert '/admin/monitoreo/secretarias/' in (data.get('redirect_url') or '')
    html = data.get('replace_html') or ''
    assert 'Timeline' in html
    assert 'data-admin-async-form' in html


def test_errores_lista_get_async_filtra_y_devuelve_region():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    rows = [
        {
            'id': 1,
            'created_at': '2026-03-24 10:00:00',
            'error_type': 'SERVER_ERROR',
            'route': '/admin/monitoreo',
            'summary': 'Timeout de proveedor externo',
            'is_resolved': False,
        },
        {
            'id': 2,
            'created_at': '2026-03-24 10:05:00',
            'error_type': 'VALIDATION_ERROR',
            'route': '/admin/solicitudes',
            'summary': 'Campo faltante',
            'is_resolved': True,
        },
    ]

    with patch('admin.routes.get_alert_items', return_value=rows):
        resp = client.get(
            '/admin/errores?q=timeout&status=pending&per_page=10',
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('update_target') == '#erroresListaAsyncRegion'
    assert '/admin/errores' in (data.get('redirect_url') or '')
    html = data.get('replace_html') or ''
    assert 'Timeout de proveedor externo' in html
    assert 'Campo faltante' not in html
    assert 'data-admin-async-form' in html
