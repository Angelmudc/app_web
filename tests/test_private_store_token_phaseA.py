# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud],
        reset=False,
    )


def _seed_catalog(token: str, *, scope_mode: str = "all_available_store", is_active: bool = True, expires_days: int = 7) -> CatalogoPrivado:
    cat = CatalogoPrivado(
        nombre="Store privada",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        scope_mode=scope_mode,
        is_active=is_active,
        expires_at=utc_now_naive() + timedelta(days=expires_days),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()
    return cat


def _seed_candidates(seed: int = 1) -> dict[str, int]:
    base = 997000 + seed * 10
    ok = Candidata(fila=base + 1, nombre_completo='Ana Interna', cedula=f'{base + 1:011d}', codigo=f'PTA-OK-{seed}', numero_telefono='809-000-1111', direccion_completa='Calle X')
    hidden = Candidata(fila=base + 2, nombre_completo='Oculta', cedula=f'{base + 2:011d}', codigo=f'PTA-HID-{seed}')
    reserved = Candidata(fila=base + 3, nombre_completo='Reservada', cedula=f'{base + 3:011d}', codigo=f'PTA-RES-{seed}')
    nodisp = Candidata(fila=base + 4, nombre_completo='No disponible', cedula=f'{base + 4:011d}', codigo=f'PTA-NOD-{seed}')
    db.session.add_all([ok, hidden, reserved, nodisp])
    db.session.flush()

    db.session.add_all([
        CandidataWeb(
            candidata_id=ok.fila,
            visible=True,
            estado_publico='disponible',
            nombre_publico='Ana Perfil Publico',
            ciudad_publica='Santiago',
            sector_publico='Centro',
            modalidad_publica='Salida diaria',
            experiencia_resumen='Fuerte en limpieza y cocina.',
            experiencia_detallada='Experiencia de 5 anos.',
            entrevista_publica_resumen='Entrevista validada por agencia.',
            tags_publicos='Limpieza, Cocina',
            disponible_inmediato=True,
        ),
        CandidataWeb(candidata_id=hidden.fila, visible=False, estado_publico='disponible', nombre_publico='No Debe Verse'),
        CandidataWeb(candidata_id=reserved.fila, visible=True, estado_publico='reservada', nombre_publico='Reservada No Debe Verse'),
        CandidataWeb(candidata_id=nodisp.fila, visible=True, estado_publico='no_disponible', nombre_publico='No Disponible No Debe Verse'),
    ])
    db.session.commit()
    return {"ok": int(ok.fila), "hidden": int(hidden.fila), "reserved": int(reserved.fila), "nodisp": int(nodisp.fila)}


def test_private_store_token_valido_all_available_200_and_privacy_html():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_ok', scope_mode='all_available_store')
        _seed_candidates(seed=1)

    resp = client.get('/tienda/tok_store_ok', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'Tienda privada de domésticas' in html
    assert 'Ana Perfil Publico' in html
    assert 'No Debe Verse' not in html
    assert 'Reservada No Debe Verse' not in html
    assert 'No Disponible No Debe Verse' not in html

    forbidden = ['/admin', '/clientes', '/login', 'cedula', 'telefono', 'direccion', 'referencia', 'notas internas', 'score', 'token_hash', 'token_hint']
    lowered = html.lower()
    for marker in forbidden:
        assert marker not in lowered


def test_private_store_token_invalido_404():
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()

    resp = client.get('/tienda/token-invalido', follow_redirects=False)
    assert resp.status_code == 404


def test_private_store_token_inactivo_o_expirado_410():
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_inactivo', scope_mode='all_available_store', is_active=False)
        _seed_catalog('tok_store_expirado', scope_mode='all_available_store', expires_days=-1)
        db.session.commit()

    assert client.get('/tienda/tok_store_inactivo', follow_redirects=False).status_code == 410
    assert client.get('/tienda/tok_store_expirado', follow_redirects=False).status_code == 410


def test_private_store_detail_ok_200_and_not_available_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_detail', scope_mode='all_available_store')
        ids = _seed_candidates(seed=2)

    assert client.get(f"/tienda/tok_store_detail/domesticas/{ids['ok']}", follow_redirects=False).status_code == 200
    assert client.get(f"/tienda/tok_store_detail/domesticas/{ids['hidden']}", follow_redirects=False).status_code == 404
    assert client.get(f"/tienda/tok_store_detail/domesticas/{ids['reserved']}", follow_redirects=False).status_code == 404
    assert client.get(f"/tienda/tok_store_detail/domesticas/{ids['nodisp']}", follow_redirects=False).status_code == 404


def test_private_store_filters_work():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_filter', scope_mode='all_available_store')
        _seed_candidates(seed=3)

    resp = client.get('/tienda/tok_store_filter?q=Ana&ciudad=Santiago&modalidad=Salida&funciones=Cocinar&disponible_inmediato=1', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Ana Perfil Publico' in html
    assert 'name="tag"' not in html
    assert 'Funciones que necesitas' in html
    assert 'Limpieza general' in html
    assert 'Cocinar' in html
    assert 'Lavar' in html
    assert 'Planchar' in html


def test_manual_shortlist_mode_does_not_break_legacy_catalogo():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cat = _seed_catalog('tok_manual_legacy', scope_mode='manual_shortlist')
        ids = _seed_candidates(seed=4)
        db.session.add(CatalogoPrivadoItem(catalogo_id=cat.id, candidata_id=ids['ok'], orden=1, is_visible=True))
        db.session.commit()

    # Nueva ruta de tienda redirige al flujo legacy para manual shortlist.
    store_resp = client.get('/tienda/tok_manual_legacy', follow_redirects=False)
    assert store_resp.status_code in (301, 302, 303)
    assert '/catalogo/tok_manual_legacy' in (store_resp.headers.get('Location') or '')

    # Legacy sigue funcionando.
    legacy_resp = client.get('/catalogo/tok_manual_legacy', follow_redirects=False)
    assert legacy_resp.status_code == 200
    assert 'Ana Perfil Publico' in legacy_resp.get_data(as_text=True)


def test_same_token_exact_works_in_catalogo_and_tienda():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cat = _seed_catalog('tok_same_exact', scope_mode='all_available_store')
        ids = _seed_candidates(seed=5)
        db.session.add(CatalogoPrivadoItem(catalogo_id=cat.id, candidata_id=ids['ok'], orden=1, is_visible=True))
        db.session.commit()

    legacy_resp = client.get('/catalogo/tok_same_exact', follow_redirects=False)
    store_resp = client.get('/tienda/tok_same_exact', follow_redirects=False)
    assert legacy_resp.status_code == 200
    assert store_resp.status_code == 200
