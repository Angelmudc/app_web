# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    CandidataWeb,
    CatalogoPrivado,
    CatalogoPrivadoItem,
    Cliente,
    Solicitud,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaRespuesta,
)
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xa9\x9f\x16\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            Candidata,
            CandidataWeb,
            CatalogoPrivado,
            CatalogoPrivadoItem,
            Cliente,
            Solicitud,
            Entrevista,
            EntrevistaPregunta,
            EntrevistaRespuesta,
        ],
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
    ok = Candidata(
        fila=base + 1,
        nombre_completo='Ana Interna',
        cedula=f'{base + 1:011d}',
        codigo=f'PTA-OK-{seed}',
        numero_telefono='809-000-1111',
        direccion_completa='Calle X',
        perfil=_PNG_1X1,
        entrevista=(
            "Nombre completo: Ana Interna\n"
            "Nacionalidad: Dominicana\n"
            "Edad: 32\n"
            "Dirección: Calle 4, sector Naco, Santo Domingo\n"
            "Cómo se describe: Responsable y ordenada\n"
            "Modalidad: Salida diaria\n"
            "Labores desempeñadas: Limpieza general y cocina básica\n"
            "Referencias laborales: Sra. Marta 809-111-2222\n"
            "Referencias familiares: Tía Julia 849-333-4444"
        ),
    )
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


def test_private_store_list_pagination_premium_and_query_params_preserved():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_pagination', scope_mode='all_available_store')
        base = 998500
        for i in range(1, 17):
            cand = Candidata(
                fila=base + i,
                nombre_completo=f'Paginacion {i}',
                cedula=f'{base + i:011d}',
                codigo=f'PTA-PAG-{i}',
            )
            db.session.add(cand)
            db.session.flush()
            db.session.add(
                CandidataWeb(
                    candidata_id=cand.fila,
                    visible=True,
                    estado_publico='disponible',
                    nombre_publico=f'Perfil Paginacion {i}',
                    ciudad_publica='Santiago',
                    modalidad_publica='Salida diaria',
                    tags_publicos='Limpieza, Cocina',
                    experiencia_resumen='Perfil de prueba para paginación.',
                    entrevista_publica_resumen='Entrevista validada por agencia.',
                    disponible_inmediato=True,
                )
            )
        db.session.commit()

    resp = client.get(
        '/tienda/tok_store_pagination?ciudad=Santiago&modalidad=Salida+diaria&funciones=Limpieza+general&disponible_inmediato=1&per_page=12&page=2',
        follow_redirects=False,
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="ps-pagination"' in html
    assert 'class="ps-pager"' not in html
    assert '← Anterior' in html
    assert 'Siguiente →' in html
    assert 'Página 2 de 2' in html
    assert 'page=1' in html
    assert 'ciudad=Santiago' in html
    assert 'modalidad=Salida+diaria' in html or 'modalidad=Salida%20diaria' in html
    assert 'funciones=Limpieza+general' in html or 'funciones=Limpieza%20general' in html
    assert 'disponible_inmediato=1' in html
    assert 'per_page=12' in html


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


def test_private_store_detail_premium_sections_privacy_and_profile_image_route():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_detail_premium', scope_mode='all_available_store')
        ids = _seed_candidates(seed=8)

    resp = client.get(f"/tienda/tok_store_detail_premium/domesticas/{ids['ok']}", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Entrevista de la candidata" in html
    assert "Ver entrevista protegida" in html
    assert "Agregar a mi selección" in html
    assert "Ver mi selección" in html
    assert 'href="/domesticas/' not in html
    assert f"/tienda/tok_store_detail_premium/domesticas/{ids['ok']}/perfil" in html

    lowered = html.lower()
    for marker in ["cedula", "telefono", "dirección", "direccion", "referencias", "notas internas"]:
        assert marker not in lowered

    img = client.get(f"/tienda/tok_store_detail_premium/domesticas/{ids['ok']}/perfil", follow_redirects=False)
    assert img.status_code == 200
    assert (img.headers.get("Content-Type") or "").startswith("image/")


def test_private_store_detail_hides_interview_block_when_no_real_interview():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_no_interview', scope_mode='all_available_store')
        ids = _seed_candidates(seed=9)
        c = Candidata.query.get(ids['ok'])
        ficha = CandidataWeb.query.filter_by(candidata_id=ids['ok']).first()
        assert c is not None and ficha is not None
        c.perfil = None
        ficha.entrevista_publica_resumen = None
        c.entrevista = None
        db.session.commit()

    resp = client.get(f"/tienda/tok_store_no_interview/domesticas/{ids['ok']}", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Entrevista de la candidata" not in html
    assert "Entrevista aún no disponible." not in html
    assert "Puedes revisar la entrevista protegida" not in html
    assert "Perfil validado por la agencia" in html


def test_private_store_interview_protected_access_and_redaction():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_interview_protected', scope_mode='all_available_store')
        ids = _seed_candidates(seed=11)

    ok_url = f"/tienda/tok_store_interview_protected/domesticas/{ids['ok']}/entrevista"
    resp = client.get(ok_url, follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True).lower()
    assert "nombre completo" in html
    assert "dirección" in html or "direccion" in html
    assert "información protegida por la agencia" in html
    assert "calle 4" not in html
    assert "sra. marta" not in html
    assert "tía julia" not in html
    assert "/users/" not in html
    assert "/private/" not in html
    assert ".pdf" not in html

    invalid = client.get(f"/tienda/token-invalido/domesticas/{ids['ok']}/entrevista", follow_redirects=False)
    assert invalid.status_code == 404


def test_private_store_detail_shows_protected_button_with_structured_interview():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_structured', scope_mode='all_available_store')
        ids = _seed_candidates(seed=12)
        cand_id = ids["ok"]
        c = Candidata.query.get(cand_id)
        assert c is not None
        c.entrevista = None
        db.session.flush()

        e = Entrevista(candidata_id=cand_id, tipo="domestica", estado="completa")
        db.session.add(e)
        db.session.flush()
        p1 = EntrevistaPregunta(clave="domestica.nombre", texto="Nombre completo", tipo="texto", orden=1, activa=True)
        p2 = EntrevistaPregunta(clave="domestica.descripcion_personal", texto="¿Cómo te describes como persona?", tipo="texto_largo", orden=2, activa=True)
        p3 = EntrevistaPregunta(clave="domestica.revision_salida", texto="¿Puedes ser revisada a la salida?", tipo="radio", orden=3, activa=True)
        p4 = EntrevistaPregunta(clave="domestica.direccion", texto="Dirección", tipo="texto_largo", orden=4, activa=True)
        p5 = EntrevistaPregunta(clave="domestica.telefono", texto="Teléfono de contacto", tipo="texto", orden=5, activa=True)
        p6 = EntrevistaPregunta(clave="domestica.referencia_familiar", texto="Referencia familiar", tipo="texto", orden=6, activa=True)
        p7 = EntrevistaPregunta(clave="domestica.referencia_laboral", texto="Referencia laboral", tipo="texto", orden=7, activa=True)
        db.session.add_all([p1, p2, p3, p4, p5, p6, p7])
        db.session.flush()
        db.session.add_all([
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p1.id, respuesta="Ana Interna"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p2.id, respuesta="Organizada y puntual"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p3.id, respuesta="Sí"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p4.id, respuesta="Calle 4, Naco, Santo Domingo"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p5.id, respuesta="809-000-1212"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p6.id, respuesta="Tía Julia 829-123-4567"),
            EntrevistaRespuesta(entrevista_id=e.id, pregunta_id=p7.id, respuesta="Sra Rosa 849-222-3333"),
        ])
        db.session.commit()

    detail = client.get(f"/tienda/tok_store_structured/domesticas/{cand_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Ver entrevista protegida" in html

    interview = client.get(f"/tienda/tok_store_structured/domesticas/{cand_id}/entrevista", follow_redirects=False)
    assert interview.status_code == 200
    iv_html = interview.get_data(as_text=True).lower()
    assert "cómo te describes como persona" in iv_html or "como te describes como persona" in iv_html
    assert "información protegida por la agencia" in iv_html
    assert "809-000-1212" in iv_html
    assert "calle 4" not in iv_html
    assert "tía julia" not in iv_html
    assert "sra rosa" not in iv_html
    for forbidden in [
        "fortaleza principal",
        "tipo de hogar trabajado",
        "modalidad preferida",
    ]:
        assert forbidden not in iv_html

def test_private_store_filters_work():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog('tok_store_filter', scope_mode='all_available_store')
        _seed_candidates(seed=3)

    resp = client.get('/tienda/tok_store_filter?ciudad=Santiago&modalidad=Salida&funciones=Cocinar&disponible_inmediato=1', follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Ana Perfil Publico' in html
    assert 'Buscar por nombre' not in html
    assert 'name="q"' not in html
    assert 'name="tag"' not in html
    assert '¿Qué necesitas?' in html
    assert 'Modalidad' in html
    assert 'Ubicación' in html
    assert 'Disponibilidad' in html
    assert 'Limpieza general' in html
    assert 'Cocinar' in html
    assert 'Lavar' in html
    assert 'Planchar' in html
    assert 'Cuidar niños' in html
    assert 'Cuidar envejecientes' in html
    assert 'Con dormida' in html
    assert 'Salida diaria' in html


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
