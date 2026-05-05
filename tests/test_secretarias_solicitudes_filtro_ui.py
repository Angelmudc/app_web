# -*- coding: utf-8 -*-

from flask import render_template

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


def test_secretarias_filtro_tabla_compacta_con_detalle_y_copiar_visible():
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        with flask_app.test_request_context():
            html = render_template(
                'secretarias_solicitudes_buscar.html',
                items=[{
                    'id': 99,
                    'codigo_solicitud': 'SOL-099',
                    'ciudad_sector': 'Santiago',
                    'modalidad': 'Con dormida',
                    'funciones_principales': 'Limpieza general, Cocinar',
                    'sueldo_valor': '25000',
                    'pasaje_label': 'Sí',
                    'estado': 'activa',
                    'copiada_ciclo': False,
                    'fecha_solicitud': '2026-05-05 09:00',
                    'ruta': '27 de Febrero',
                    'experiencia': 'niños',
                    'tipo_lugar': 'Casa',
                    'habitaciones': '3',
                    'banos': '2',
                    'pisos_label': '2 niveles',
                    'adultos': '2',
                    'ninos': '1',
                    'order_text': 'Texto listo para copiar',
                }],
                page=1,
                pages=1,
                total=1,
                per_page=20,
                q='',
                estado='',
                estados_opts=['proceso', 'activa', 'pagada', 'cancelada', 'reemplazo'],
                desde='',
                hasta='',
                modalidad='',
                mascota='',
                con_ninos='',
                page_links=[{'n': 1, 'url': '/secretarias/solicitudes/filtro', 'active': True}],
                prev_url=None,
                next_url=None,
                endpoint='secretarias_filtrar_solicitudes',
                empty_state_message='',
                filtros_aplicados=True,
                filtro_vals={},
                funciones_opts=[('limpieza', 'Limpieza general')],
            )

    assert 'Código' in html
    assert 'Ciudad / Sector' in html
    assert 'Modalidad' in html
    assert 'Funciones' in html
    assert 'Sueldo' in html
    assert 'Acción' in html

    assert 'Pasaje</th>' not in html
    assert 'Estado</th>' not in html
    assert 'Ciclo</th>' not in html
    assert 'Fecha solicitud</th>' not in html

    assert 'Ver detalles' in html
    assert 'Ruta:' in html
    assert 'Copiar' in html
