# -*- coding: utf-8 -*-

from unittest.mock import patch

from app import app as flask_app


def _login_owner(client):
    return client.post('/admin/login', data={'usuario': 'Owner', 'clave': 'admin123'}, follow_redirects=False)


def _async_headers():
    return {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Admin-Async': '1',
    }


def test_seguridad_templates_incluyen_regiones_y_forms_async():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    resp_sesiones = client.get('/admin/seguridad/sesiones', follow_redirects=False)
    html_sesiones = resp_sesiones.get_data(as_text=True)
    assert resp_sesiones.status_code == 200
    assert 'id="seguridadSesionesAsyncRegion"' in html_sesiones
    assert 'data-admin-async-form' in html_sesiones

    resp_alertas = client.get('/admin/seguridad/alertas', follow_redirects=False)
    html_alertas = resp_alertas.get_data(as_text=True)
    assert resp_alertas.status_code == 200
    assert 'id="seguridadAlertasAsyncRegion"' in html_alertas

    resp_canales = client.get('/admin/alertas/canales', follow_redirects=False)
    html_canales = resp_canales.get_data(as_text=True)
    assert resp_canales.status_code == 200
    assert 'id="alertasCanalesAsyncRegion"' in html_canales
    assert html_canales.count('data-admin-async-form') >= 2

    resp_locks = client.get('/admin/seguridad/locks', follow_redirects=False)
    html_locks = resp_locks.get_data(as_text=True)
    assert resp_locks.status_code == 200
    assert 'id="seguridadLocksAsyncRegion"' in html_locks
    assert 'data-admin-async-link' in html_locks
    assert 'data-async-target="#seguridadLocksAsyncRegion"' in html_locks


def test_seguridad_locks_get_async_devuelve_replace_html_y_target():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch('admin.routes.list_active_locks', return_value=[]):
        resp = client.get(
            '/admin/seguridad/locks',
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('update_target') == '#seguridadLocksAsyncRegion'
    assert data.get('redirect_url') == '/admin/seguridad/locks'
    assert 'No hay locks activos.' in (data.get('replace_html') or '')


def test_seguridad_sesiones_cerrar_async_exito_devuelve_replace_html_y_redirect_url_safe_next():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch('admin.routes.close_user_sessions') as close_mock, \
         patch('admin.routes.list_active_sessions', return_value=[]):
        resp = client.post(
            '/admin/seguridad/sesiones/cerrar',
            data={'user_id': '7', 'reason': 'test', 'next': '/admin/monitoreo'},
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('redirect_url') == '/admin/monitoreo'
    assert data.get('update_target') == '#seguridadSesionesAsyncRegion'
    assert 'No hay sesiones activas.' in (data.get('replace_html') or '')
    close_mock.assert_called_once()


def test_seguridad_sesiones_cerrar_async_error_validacion_invalid_input():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    resp = client.post(
        '/admin/seguridad/sesiones/cerrar',
        data={'user_id': 'abc', 'next': 'https://malicioso.example'},
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is False
    assert data.get('error_code') == 'invalid_input'
    assert data.get('redirect_url') == '/admin/seguridad/sesiones'


def test_resolver_alerta_async_exito_devuelve_replace_html_y_redirect_url():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch('admin.routes.resolve_alert') as resolve_mock, \
         patch('admin.routes.get_alert_items', return_value=[]):
        resp = client.post(
            '/admin/alertas/44/resolver',
            data={'next': '/admin/seguridad/alertas'},
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('redirect_url') == '/admin/seguridad/alertas'
    assert data.get('update_target') == '#seguridadAlertasAsyncRegion'
    assert 'No hay alertas registradas.' in (data.get('replace_html') or '')
    resolve_mock.assert_called_once()


def test_alertas_canales_guardar_async_exito_devuelve_replace_html_y_redirect_url():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    cfg_before = {'enabled': False, 'token': '', 'chat_id': '', 'masked_token': '', 'has_config': False}
    cfg_after = {'enabled': True, 'token': 't', 'chat_id': '1234', 'masked_token': 't***', 'has_config': True}

    with patch('admin.routes.telegram_channel_config', side_effect=[cfg_before, cfg_after]), \
         patch('admin.routes.save_telegram_channel_config') as save_mock:
        resp = client.post(
            '/admin/alertas/canales',
            data={
                'next': '/admin/alertas/canales',
                'telegram_enabled': 'on',
                'telegram_bot_token': 'token-test',
                'telegram_chat_id': '98765',
            },
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is True
    assert data.get('redirect_url') == '/admin/alertas/canales'
    assert data.get('update_target') == '#alertasCanalesAsyncRegion'
    assert 'Token actual:' in (data.get('replace_html') or '')
    save_mock.assert_called_once()


def test_alertas_canales_guardar_async_error_validacion_invalid_input():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    cfg = {'enabled': False, 'token': '', 'chat_id': '', 'masked_token': '', 'has_config': False}
    with patch('admin.routes.telegram_channel_config', return_value=cfg):
        resp = client.post(
            '/admin/alertas/canales',
            data={'telegram_enabled': 'on', 'next': '/admin/alertas/canales'},
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get('success') is False
    assert data.get('error_code') == 'invalid_input'
    assert data.get('category') == 'warning'
    assert data.get('redirect_url') == '/admin/alertas/canales'
    assert data.get('update_target') == '#alertasCanalesAsyncRegion'


def test_alertas_canales_probar_async_error_negocio_devuelve_409():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch('admin.routes.send_telegram_test_message', return_value=(False, 'Canal desactivado')):
        resp = client.post(
            '/admin/alertas/canales/probar',
            data={'next': '/admin/alertas/canales'},
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 409
    data = resp.get_json() or {}
    assert data.get('success') is False
    assert data.get('error_code') == 'telegram_test_failed'
    assert data.get('redirect_url') == '/admin/alertas/canales'


def test_alertas_canales_probar_fallback_clasico_se_mantiene_redirect():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    with patch('admin.routes.send_telegram_test_message', return_value=(True, 'ok')):
        resp = client.post(
            '/admin/alertas/canales/probar',
            data={'next': '/admin/monitoreo'},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert '/admin/monitoreo' in (resp.location or '')
