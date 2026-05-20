# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlparse

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


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


def _seed_clientes_solicitudes() -> tuple[int, str, str, int, int]:
    c1 = Cliente(
        nombre_completo='Ana Cliente',
        telefono='8095551111',
        codigo='CLI-ANA-001',
        email='ana.catalogo@example.com',
        role='cliente',
    )
    c2 = Cliente(
        nombre_completo='Beto Cliente',
        telefono='8095552222',
        codigo='CLI-BETO-002',
        email='beto.catalogo@example.com',
        role='cliente',
    )
    db.session.add(c1)
    db.session.add(c2)
    db.session.flush()

    s1 = Solicitud(cliente_id=c1.id, codigo_solicitud='SOL-A-1', estado='proceso')
    s2 = Solicitud(cliente_id=c1.id, codigo_solicitud='SOL-A-2', estado='proceso')
    s_other = Solicitud(cliente_id=c2.id, codigo_solicitud='SOL-B-1', estado='proceso')
    db.session.add(s1)
    db.session.add(s2)
    db.session.add(s_other)
    db.session.commit()
    return int(c1.id), str(c1.codigo), str(c1.telefono), int(s1.id), int(s_other.id)


def _seed_candidata(fila: int = 992001) -> int:
    cand = Candidata(fila=fila, nombre_completo='Candidata Test', cedula='99200100000', codigo='CT-992001')
    db.session.add(cand)
    db.session.commit()
    return int(cand.fila)


def test_catalogo_form_flujo_cliente_solicitud_y_vencimiento_automatico():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    _login_owner(client)

    with flask_app.app_context():
        _ensure_tables()
        cliente_id, cliente_codigo, cliente_telefono, solicitud_ok_id, solicitud_other_id = _seed_clientes_solicitudes()
        candidata_id = _seed_candidata()

    # 1) Form nuevo sin fecha manual + texto informativo de 7 días
    new_resp = client.get('/admin/catalogos-privados/nuevo', follow_redirects=False)
    assert new_resp.status_code == 200
    new_html = new_resp.get_data(as_text=True)
    assert 'Se genera automáticamente al guardar' in new_html
    assert 'Busca y selecciona un cliente para ver sus solicitudes.' in new_html
    assert 'name="nombre"' not in new_html
    assert 'name="expires_at"' not in new_html

    # 2) Buscar cliente por nombre
    search_resp = client.get('/admin/catalogos-privados/nuevo?cliente_q=Ana', follow_redirects=False)
    assert search_resp.status_code == 200
    search_html = search_resp.get_data(as_text=True)
    assert 'Ana Cliente' in search_html
    assert 'Seleccionar' in search_html

    # 2.1) Buscar cliente por teléfono / código / id
    phone_resp = client.get(f'/admin/catalogos-privados/nuevo?cliente_q={cliente_telefono}', follow_redirects=False)
    assert phone_resp.status_code == 200
    assert 'Ana Cliente' in phone_resp.get_data(as_text=True)

    code_resp = client.get(f'/admin/catalogos-privados/nuevo?cliente_q={cliente_codigo}', follow_redirects=False)
    assert code_resp.status_code == 200
    assert 'Ana Cliente' in code_resp.get_data(as_text=True)

    id_resp = client.get(f'/admin/catalogos-privados/nuevo?cliente_q={cliente_id}', follow_redirects=False)
    assert id_resp.status_code == 200
    assert 'Ana Cliente' in id_resp.get_data(as_text=True)

    # 3) Seleccionar cliente y filtrar solicitudes solo de ese cliente
    selected_resp = client.get(f'/admin/catalogos-privados/nuevo?cliente_id={cliente_id}', follow_redirects=False)
    assert selected_resp.status_code == 200
    selected_html = selected_resp.get_data(as_text=True)
    assert f'value="{cliente_id}"' in selected_html
    assert f'value="{solicitud_ok_id}"' in selected_html
    assert 'SOL-A-1' in selected_html
    assert 'SOL-B-1' not in selected_html

    # 4) Envío inválido: solicitud no pertenece al cliente seleccionado
    bad_payload = {
        'cliente_id': str(cliente_id),
        'solicitud_id': str(solicitud_other_id),
        'candidata_ids': [str(candidata_id)],
    }
    bad_resp = client.post('/admin/catalogos-privados', data=bad_payload, follow_redirects=True)
    assert bad_resp.status_code == 200
    assert 'no pertenece al cliente elegido' in bad_resp.get_data(as_text=True).lower()
    with flask_app.app_context():
        bad_cat = CatalogoPrivado.query.filter_by(cliente_id=cliente_id, solicitud_id=solicitud_other_id).first()
        assert bad_cat is None

    # 4.1) Envío inválido: sin cliente seleccionado
    no_cliente_payload = {
        'solicitud_id': str(solicitud_ok_id),
        'candidata_ids': [str(candidata_id)],
    }
    no_cliente_resp = client.post('/admin/catalogos-privados', data=no_cliente_payload, follow_redirects=True)
    assert no_cliente_resp.status_code == 200
    assert 'debes seleccionar un cliente' in no_cliente_resp.get_data(as_text=True).lower()
    with flask_app.app_context():
        bad_no_cliente = CatalogoPrivado.query.filter_by(solicitud_id=solicitud_ok_id).order_by(CatalogoPrivado.id.desc()).first()
        assert bad_no_cliente is None or int(bad_no_cliente.cliente_id or 0) != 0

    # 5) Crear catálogo válido sin expires_at manual
    ok_payload = {
        'cliente_id': str(cliente_id),
        'solicitud_id': str(solicitud_ok_id),
        'descripcion': 'Mensaje visible demo',
        'candidata_ids': [str(candidata_id)],
    }
    before = utc_now_naive()
    ok_resp = client.post('/admin/catalogos-privados', data=ok_payload, follow_redirects=True)
    assert ok_resp.status_code == 200
    after = utc_now_naive()

    with flask_app.app_context():
        cat = CatalogoPrivado.query.filter_by(cliente_id=cliente_id, solicitud_id=solicitud_ok_id).order_by(CatalogoPrivado.id.desc()).first()
        assert cat is not None
        assert int(cat.cliente_id or 0) == cliente_id
        assert int(cat.solicitud_id or 0) == solicitud_ok_id
        assert cat.nombre == 'Catálogo para Ana Cliente - SOL-A-1'
        assert cat.expires_at is not None
        min_expected = before + timedelta(days=7) - timedelta(minutes=2)
        max_expected = after + timedelta(days=7) + timedelta(minutes=2)
        assert min_expected <= cat.expires_at <= max_expected

    # 6) Enlace público del detalle creado funciona
    html = ok_resp.get_data(as_text=True)
    m = re.search(r'https?://[^\s<]*/catalogo/[^\s<]+', html)
    assert m is not None
    public_url = m.group(0)
    parsed = urlparse(public_url)
    public_path = parsed.path
    pub_resp = client.get(public_path, follow_redirects=False)
    assert pub_resp.status_code == 200
