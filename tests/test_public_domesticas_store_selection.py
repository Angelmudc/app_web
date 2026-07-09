# -*- coding: utf-8 -*-

from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb], reset=False)


def _seed_dataset(seed: int, *, total_ok: int = 3) -> dict[str, list[int] | int]:
    base = 994000 + int(seed) * 100
    ok_ids = []
    for idx in range(1, total_ok + 1):
        fila = base + idx
        cand = Candidata(fila=fila, nombre_completo=f'Publica {seed}-{idx}', cedula=f'{fila}00000', codigo=f'SEL-{seed}-{idx}')
        db.session.add(cand)
        db.session.flush()
        db.session.add(CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico='disponible',
            nombre_publico=f'Perfil Seleccion {seed}-{idx}',
            ciudad_publica='Santiago',
            modalidad_publica='Salida diaria',
            tags_publicos='Limpieza, Cocina',
            disponible_inmediato=True,
        ))
        ok_ids.append(int(cand.fila))

    hidden_fila = base + 90
    hidden = Candidata(fila=hidden_fila, nombre_completo='Oculta', cedula=f'{hidden_fila}00000', codigo=f'SEL-HID-{seed}')
    db.session.add(hidden)
    db.session.flush()
    db.session.add(CandidataWeb(candidata_id=hidden.fila, visible=False, estado_publico='disponible', nombre_publico='Oculta'))

    reserved_fila = base + 91
    reserved = Candidata(fila=reserved_fila, nombre_completo='Reservada', cedula=f'{reserved_fila}00000', codigo=f'SEL-RES-{seed}')
    db.session.add(reserved)
    db.session.flush()
    db.session.add(CandidataWeb(candidata_id=reserved.fila, visible=True, estado_publico='reservada', nombre_publico='Reservada'))

    db.session.commit()
    return {'ok': ok_ids, 'hidden': int(hidden.fila), 'reserved': int(reserved.fila)}


def _post_add(client, candidata_id: int, return_to: str = '/domesticas'):
    return client.post('/mi-seleccion/agregar', data={
        'candidata_id': str(candidata_id),
        'return_to': return_to,
    }, follow_redirects=False)


def _post_remove(client, candidata_id: int, return_to: str = '/mi-seleccion'):
    return client.post('/mi-seleccion/quitar', data={
        'candidata_id': str(candidata_id),
        'return_to': return_to,
    }, follow_redirects=False)


def _post_clear(client, return_to: str = '/mi-seleccion'):
    return client.post('/mi-seleccion/limpiar', data={
        'return_to': return_to,
    }, follow_redirects=False)


def test_selection_add_no_duplicate_and_counter_visible():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=1, total_ok=2)

    ok1 = seeded['ok'][0]
    resp1 = _post_add(client, ok1)
    assert resp1.status_code in (302, 303)

    resp2 = _post_add(client, ok1)
    assert resp2.status_code in (302, 303)

    with client.session_transaction() as sess:
        ids = list(sess.get('mi_seleccion_candidatas') or [])
    assert ids == [ok1]

    list_resp = client.get('/domesticas', follow_redirects=False)
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)
    assert 'Mi selección (1)' in list_html
    assert 'Ya en selección' in list_html

    detail_resp = client.get(f'/domesticas/{ok1}', follow_redirects=False)
    assert detail_resp.status_code == 200
    detail_html = detail_resp.get_data(as_text=True)
    assert 'Mi selección (1)' in detail_html
    assert 'Ver mi selección' in detail_html


def test_selection_limit_max_20():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=2, total_ok=25)

    for fila in seeded['ok']:
        _post_add(client, fila)

    with client.session_transaction() as sess:
        ids = list(sess.get('mi_seleccion_candidatas') or [])
    assert len(ids) == 20


def test_selection_reject_hidden_or_not_available_and_remove():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=3, total_ok=2)

    ok1 = seeded['ok'][0]
    hidden = seeded['hidden']
    reserved = seeded['reserved']

    _post_add(client, hidden)
    _post_add(client, reserved)
    _post_add(client, ok1)

    with client.session_transaction() as sess:
        ids = list(sess.get('mi_seleccion_candidatas') or [])
    assert ids == [ok1]

    remove_resp = _post_remove(client, ok1)
    assert remove_resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        ids_after = list(sess.get('mi_seleccion_candidatas') or [])
    assert ids_after == []


def test_mi_seleccion_page_and_empty_state_and_privacy_html():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=4, total_ok=1)

    empty_resp = client.get('/mi-seleccion', follow_redirects=False)
    assert empty_resp.status_code == 200
    empty_html = empty_resp.get_data(as_text=True)
    assert 'Todavía no has seleccionado candidatas.' in empty_html
    assert 'Explorar domésticas' in empty_html

    ok1 = seeded['ok'][0]
    _post_add(client, ok1)
    selected_resp = client.get('/mi-seleccion', follow_redirects=False)
    assert selected_resp.status_code == 200
    html = selected_resp.get_data(as_text=True)
    assert 'Perfil Seleccion 4-1' in html
    assert 'Quitar' in html

    forbidden = [
        '/admin', '/clientes', '/login', 'telefono', 'teléfono', 'cedula', 'cédula',
        'referencia', 'notas internas', 'score', 'token_hash', 'token_hint'
    ]
    lowered = html.lower()
    for marker in forbidden:
        assert marker not in lowered


def test_selection_revalidates_unpublished_items_on_render():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=5, total_ok=1)
        ok1 = seeded['ok'][0]

    _post_add(client, ok1)

    with flask_app.app_context():
        ficha = CandidataWeb.query.filter_by(candidata_id=ok1).first()
        ficha.visible = False
        db.session.commit()

    page = client.get('/mi-seleccion', follow_redirects=False)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Todavía no has seleccionado candidatas.' in html
    with client.session_transaction() as sess:
        assert list(sess.get('mi_seleccion_candidatas') or []) == []


def test_selection_clear_empties_session_redirects_and_renders_empty_state():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_dataset(seed=6, total_ok=2)

    _post_add(client, seeded['ok'][0])
    _post_add(client, seeded['ok'][1])

    with client.session_transaction() as sess:
        assert list(sess.get('mi_seleccion_candidatas') or []) != []

    clear_resp = _post_clear(client, return_to='/domesticas?ciudad=Santiago')
    assert clear_resp.status_code in (302, 303)
    assert clear_resp.headers.get('Location', '').endswith('/domesticas?ciudad=Santiago')

    with client.session_transaction() as sess:
        assert list(sess.get('mi_seleccion_candidatas') or []) == []

    empty_resp = client.get('/mi-seleccion', follow_redirects=False)
    assert empty_resp.status_code == 200
    empty_html = empty_resp.get_data(as_text=True)
    assert 'Todavía no has seleccionado candidatas.' in empty_html
