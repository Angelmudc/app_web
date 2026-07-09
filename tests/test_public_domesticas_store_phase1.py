# -*- coding: utf-8 -*-

from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb], reset=False)


def _seed_public_store_data(seed: int) -> dict[str, int]:
    base = 993000 + int(seed) * 10
    c_ok_1 = Candidata(fila=base + 1, nombre_completo='Ana Interna Uno', cedula=f'{base + 1}00000', codigo=f'PUB-ANA-{seed}')
    c_ok_2 = Candidata(fila=base + 2, nombre_completo='Berta Interna Dos', cedula=f'{base + 2}00000', codigo=f'PUB-BER-{seed}')
    c_hidden = Candidata(fila=base + 3, nombre_completo='Oculta', cedula=f'{base + 3}00000', codigo=f'PUB-OCC-{seed}')
    c_res = Candidata(fila=base + 4, nombre_completo='Reservada', cedula=f'{base + 4}00000', codigo=f'PUB-RES-{seed}')
    c_no = Candidata(fila=base + 5, nombre_completo='No Disponible', cedula=f'{base + 5}00000', codigo=f'PUB-NOD-{seed}')
    db.session.add_all([c_ok_1, c_ok_2, c_hidden, c_res, c_no])
    db.session.flush()

    db.session.add_all([
        CandidataWeb(
            candidata_id=c_ok_1.fila,
            visible=True,
            estado_publico='disponible',
            nombre_publico='Ana Perfil Publico',
            edad_publica='36 años',
            ciudad_publica='Santiago',
            sector_publico='Centro',
            modalidad_publica='Salida diaria',
            sueldo_texto_publico='RD$ 22,000',
            experiencia_resumen='Experta en limpieza y cocina.',
            experiencia_detallada='Trabajó 6 años en hogares familiares.',
            entrevista_publica_resumen='Puntual, organizada y de buen trato.',
            tags_publicos='Limpieza, Cocina, Niños',
            disponible_inmediato=True,
            foto_publica_url='https://example.com/fotos/ana.jpg',
        ),
        CandidataWeb(
            candidata_id=c_ok_2.fila,
            visible=True,
            estado_publico='disponible',
            nombre_publico='Berta Perfil Publico',
            edad_publica='41 años',
            ciudad_publica='La Vega',
            sector_publico='Sur',
            modalidad_publica='Con dormida',
            sueldo_texto_publico='RD$ 24,000',
            experiencia_resumen='Fuerte en envejecientes y cocina.',
            experiencia_detallada='Experiencia en cuidado y apoyo del hogar.',
            entrevista_publica_resumen='Perfil calmado y responsable.',
            tags_publicos='Envejecientes, Cocina',
            disponible_inmediato=False,
            foto_publica_url='https://example.com/fotos/berta.jpg',
        ),
        CandidataWeb(candidata_id=c_hidden.fila, visible=False, estado_publico='disponible', nombre_publico='No Debe Verse'),
        CandidataWeb(candidata_id=c_res.fila, visible=True, estado_publico='reservada', nombre_publico='No Debe Verse Reservada'),
        CandidataWeb(candidata_id=c_no.fila, visible=True, estado_publico='no_disponible', nombre_publico='No Debe Verse No Disponible'),
    ])
    db.session.commit()

    return {
        'ok1': int(c_ok_1.fila),
        'ok2': int(c_ok_2.fila),
        'hidden': int(c_hidden.fila),
        'reserved': int(c_res.fila),
        'nodisp': int(c_no.fila),
    }


def test_domesticas_store_list_and_alias_and_visibility_rules():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_public_store_data(seed=1)

    alias_resp = client.get('/tienda-domesticas', follow_redirects=False)
    assert alias_resp.status_code in (301, 302, 303)
    assert alias_resp.headers.get('Location', '').endswith('/domesticas')

    resp = client.get('/domesticas', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'Ana Perfil Publico' in html
    assert 'Berta Perfil Publico' in html
    assert 'No Debe Verse' not in html
    assert 'No Debe Verse Reservada' not in html
    assert 'No Debe Verse No Disponible' not in html


def test_domesticas_store_detail_200_and_404_rules():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        ids = _seed_public_store_data(seed=2)

    ok_resp = client.get(f"/domesticas/{ids['ok1']}", follow_redirects=False)
    assert ok_resp.status_code == 200
    ok_html = ok_resp.get_data(as_text=True)
    assert 'Resumen de experiencia' in ok_html
    assert 'Entrevista pública' in ok_html

    hidden_resp = client.get(f"/domesticas/{ids['hidden']}", follow_redirects=False)
    assert hidden_resp.status_code == 404
    reserved_resp = client.get(f"/domesticas/{ids['reserved']}", follow_redirects=False)
    assert reserved_resp.status_code == 404
    nodisp_resp = client.get(f"/domesticas/{ids['nodisp']}", follow_redirects=False)
    assert nodisp_resp.status_code == 404


def test_domesticas_store_filters_and_public_html_hardening():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_public_store_data(seed=3)

    filtered = client.get('/domesticas?q=Ana&ciudad=Santiago&modalidad=Salida&tag=Cocina&disponible_inmediato=1', follow_redirects=False)
    assert filtered.status_code == 200
    html = filtered.get_data(as_text=True)

    assert 'Ana Perfil Publico' in html
    assert 'Berta Perfil Publico' not in html

    forbidden_markers = [
        '/admin',
        '/clientes',
        '/login',
        'cedula',
        'teléfono',
        'telefono',
        'referencias',
        'notas internas',
        'score',
        'token_hash',
        'token_hint',
    ]
    normalized = html.lower()
    for marker in forbidden_markers:
        assert marker not in normalized


def test_domesticas_store_filter_options_only_include_visible_available_distinct_values():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_public_store_data(seed=4)
        extra = Candidata(
            fila=993999,
            nombre_completo='Extra Publica',
            cedula='99399900000',
            codigo='PUB-EXTRA-4',
        )
        hidden = Candidata(
            fila=994000,
            nombre_completo='Extra Oculta',
            cedula='99400000000',
            codigo='PUB-HIDDEN-4',
        )
        db.session.add_all([extra, hidden])
        db.session.flush()
        db.session.add_all([
            CandidataWeb(
                candidata_id=extra.fila,
                visible=True,
                estado_publico='disponible',
                nombre_publico='Extra Visible',
                ciudad_publica=' Santiago ',
                modalidad_publica=' Con dormida ',
            ),
            CandidataWeb(
                candidata_id=hidden.fila,
                visible=False,
                estado_publico='disponible',
                nombre_publico='Extra Oculta',
                ciudad_publica='Puerto Plata',
                modalidad_publica='Por horas',
            ),
        ])
        db.session.commit()

    resp = client.get('/domesticas', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count('<option value="Santiago"') == 1
    assert html.count('<option value="Con dormida"') == 1
    assert 'Puerto Plata' not in html
    assert 'Por horas' not in html
