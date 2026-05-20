# -*- coding: utf-8 -*-

from __future__ import annotations

import re

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
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
    ensure_sqlite_compat_tables([Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud], reset=False)


def test_dashboard_muestra_accesos_visibles_catalogos_y_perfiles_publicos():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()

    resp = client.get('/admin/monitoreo', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Catálogos privados' in html
    assert 'Perfiles públicos' in html
    assert '/admin/catalogos-privados' in html
    assert '/admin/candidatas-web' in html


def test_clientes_list_muestra_links_catalogos_y_crear_catalogo_por_cliente():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()
        c = Cliente(nombre_completo='Ana Lista', telefono='8095553333', role='cliente')
        db.session.add(c)
        db.session.commit()
        cid = int(c.id)

    list_resp = client.get('/admin/clientes', follow_redirects=False)
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)
    assert '/admin/catalogos-privados' in list_html

    search_resp = client.get('/admin/clientes?q=Ana', follow_redirects=False)
    assert search_resp.status_code == 200
    search_html = search_resp.get_data(as_text=True)
    assert f'/admin/catalogos-privados/nuevo?cliente_id={cid}' in search_html
    assert 'Crear catálogo' in search_html


def test_catalogo_nuevo_preselecciona_cliente_y_filtra_solicitudes_por_querystring():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()
        c1 = Cliente(nombre_completo='Ana Auto', telefono='8095554444', role='cliente')
        c2 = Cliente(nombre_completo='Beto Auto', telefono='8095555555', role='cliente')
        db.session.add(c1)
        db.session.add(c2)
        db.session.flush()

        s1 = Solicitud(cliente_id=c1.id, codigo_solicitud='SOL-AUTO-1', estado='proceso')
        s2 = Solicitud(cliente_id=c2.id, codigo_solicitud='SOL-AUTO-2', estado='proceso')
        db.session.add(s1)
        db.session.add(s2)
        db.session.commit()
        c1_id = int(c1.id)

    selected_resp = client.get(f'/admin/catalogos-privados/nuevo?cliente_id={c1_id}', follow_redirects=False)
    assert selected_resp.status_code == 200
    selected_html = selected_resp.get_data(as_text=True)
    assert f'<option value="{c1_id}" selected' in selected_html
    assert 'SOL-AUTO-1' in selected_html
    assert 'SOL-AUTO-2' not in selected_html


def test_catalogo_nuevo_ignora_cliente_id_invalido_sin_romper_flujo():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()

    resp = client.get('/admin/catalogos-privados/nuevo?cliente_id=99999999', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<option value=""' in html
