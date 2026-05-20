# -*- coding: utf-8 -*-

from __future__ import annotations

import re

from app import app as flask_app
from config_app import db
from models import Candidata, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables


_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return (m.group(1) if m else "").strip()


def _login_owner(client) -> None:
    page = client.get('/admin/login', follow_redirects=False)
    payload = {'usuario': 'Owner', 'clave': 'admin123'}
    token = _extract_csrf(page.get_data(as_text=True))
    if token:
        payload['csrf_token'] = token
    resp = client.post('/admin/login', data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud], reset=False)


def test_admin_catalogos_list_renderiza_y_nav_mas_incluye_enlaces_nuevos():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()
        cat = CatalogoPrivado(nombre='Cat Nav', token_hash='a' * 64, token_hint='aaaaaaaaaaaa', is_active=True)
        db.session.add(cat)
        db.session.commit()

    resp = client.get('/admin/catalogos-privados', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '/admin/catalogos-privados' in html
    assert '/admin/candidatas-web' in html


def test_admin_catalogo_detail_muestra_enlace_perfil_publico_solo_si_hay_candidata():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()
        cand = Candidata(
            fila=991001,
            nombre_completo='Candidata Nav',
            cedula='99100100000',
            codigo='CN-991001',
        )
        cat = CatalogoPrivado(nombre='Cat Detail', token_hash='b' * 64, token_hint='bbbbbbbbbbbb', is_active=True)
        db.session.add(cand)
        db.session.add(cat)
        db.session.flush()

        ok_item = CatalogoPrivadoItem(catalogo_id=cat.id, candidata_id=cand.fila, is_visible=True)
        missing_item = CatalogoPrivadoItem(catalogo_id=cat.id, candidata_id=991099, is_visible=True)
        db.session.add(ok_item)
        db.session.add(missing_item)
        db.session.commit()
        cat_id = int(cat.id)

    resp = client.get(f'/admin/catalogos-privados/{cat_id}', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '/admin/candidatas-web/991001' in html
    assert '/admin/candidatas-web/991099' not in html
