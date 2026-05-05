# -*- coding: utf-8 -*-

from app import app as flask_app


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def test_secretarias_filtro_ui_render_estado_vacio_y_campos():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    assert _login_secretaria(client).status_code in (302, 303)
    resp = client.get('/secretarias/solicitudes/filtro', follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Aplica filtros para ver resultados' in html
    assert 'name="ciudad_sector"' in html
    assert 'name="ruta"' in html
    assert 'name="funciones[]"' in html
    assert 'name="experiencia"' in html
    assert 'name="sueldo_min"' in html
    assert 'name="sueldo_max"' in html
    assert 'name="pasaje"' in html
    assert 'name="modalidad"' in html
    assert 'name="pisos"' in html
    assert 'name="tipo_casa"' in html
    assert '>Buscar<' in html
    assert 'Limpieza general' in html
    assert 'Cuidar niños' in html
    assert 'Cuidar envejeciente' in html


def test_home_muestra_enlace_buscar_solicitudes_para_staff_interno():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    assert _login_secretaria(client).status_code in (302, 303)
    resp = client.get('/home', follow_redirects=False)

    assert resp.status_code == 200
    assert b'href="/secretarias/solicitudes/filtro"' in resp.data
    assert 'Buscar solicitudes'.encode('utf-8') in resp.data
