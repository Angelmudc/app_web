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
    TiendaInteres,
    TiendaInteresItem,
)
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


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
            TiendaInteres,
            TiendaInteresItem,
        ],
        reset=False,
    )


def _seed_catalog(token: str, *, scope_mode: str = "all_available_store", active: bool = True, exp_days: int = 7, with_cliente: bool = False):
    cliente = None
    solicitud = None
    if with_cliente:
        cliente = Cliente(nombre_completo="Ana Cliente", telefono="8095551111", codigo="CLI-ANA", email="ana@example.com", role="cliente")
        db.session.add(cliente)
        db.session.flush()
        solicitud = Solicitud(cliente_id=cliente.id, codigo_solicitud="SOL-TIENDA-1", estado="proceso")
        db.session.add(solicitud)
        db.session.flush()

    cat = CatalogoPrivado(
        nombre="Tienda privada",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        scope_mode=scope_mode,
        is_active=active,
        expires_at=utc_now_naive() + timedelta(days=exp_days),
        cliente_id=(cliente.id if cliente else None),
        solicitud_id=(solicitud.id if solicitud else None),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.commit()
    return cat


def _seed_candidates(seed: int = 11):
    base = 998000 + seed * 10
    c1 = Candidata(fila=base + 1, nombre_completo="Ana Interna", cedula=f"{base+1:011d}", codigo=f"TOK-OK-{seed}", numero_telefono="8098880000", direccion_completa="Privada 1")
    c2 = Candidata(fila=base + 2, nombre_completo="Berta Interna", cedula=f"{base+2:011d}", codigo=f"TOK-OK2-{seed}")
    hidden = Candidata(fila=base + 3, nombre_completo="Oculta", cedula=f"{base+3:011d}", codigo=f"TOK-HID-{seed}")
    db.session.add_all([c1, c2, hidden])
    db.session.flush()
    db.session.add_all([
        CandidataWeb(candidata_id=c1.fila, visible=True, estado_publico="disponible", nombre_publico="Ana Perfil Publico", ciudad_publica="Santiago", modalidad_publica="Con dormida", tags_publicos="Limpieza", experiencia_resumen="Resumen", entrevista_publica_resumen="Revisada", disponible_inmediato=True),
        CandidataWeb(candidata_id=c2.fila, visible=True, estado_publico="disponible", nombre_publico="Berta Perfil Publico", ciudad_publica="Santo Domingo", modalidad_publica="Salida diaria", tags_publicos="Cocina"),
        CandidataWeb(candidata_id=hidden.fila, visible=False, estado_publico="disponible", nombre_publico="Oculta no ver"),
    ])
    db.session.commit()
    return int(c1.fila), int(c2.fila), int(hidden.fila)


def _login_owner(client):
    client.get("/admin/login", follow_redirects=False)
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_private_store_uses_private_base_and_not_public_navbar():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_private_nav")
        _seed_candidates(seed=21)

    resp = client.get("/tienda/tok_private_nav", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Acceso temporal" in html
    assert "Mi selección (" in html
    assert "Solicitar entrevistas" in html
    assert "Solicitar servicio" not in html
    assert "/servicios" not in html
    assert "ps-mobile-bottom-bar" in html
    assert "Selección (" in html
    assert "data-filter-open" in html
    assert "js/private_store.js" in html


def test_fixed_filters_rd_and_modalidad_options_present_without_db_dependence():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_filters_fixed")
        _seed_candidates(seed=22)

    html = client.get("/tienda/tok_filters_fixed", follow_redirects=False).get_data(as_text=True)
    assert "Pedernales" in html
    assert "Elías Piña" in html
    assert "Con dormida" in html
    assert "Salida diaria" in html


def test_selection_add_remove_clear_isolated_by_token():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cat1 = _seed_catalog("tok_sel_one")
        cat2 = _seed_catalog("tok_sel_two")
        cat1_id = int(cat1.id)
        cat2_id = int(cat2.id)
        c1, c2, _hidden = _seed_candidates(seed=23)

    add1 = client.post("/tienda/tok_sel_one/seleccion/agregar", data={"candidata_id": str(c1), "return_to": "/tienda/tok_sel_one"}, follow_redirects=False)
    assert add1.status_code in (302, 303)
    add2 = client.post("/tienda/tok_sel_two/seleccion/agregar", data={"candidata_id": str(c2), "return_to": "/tienda/tok_sel_two"}, follow_redirects=False)
    assert add2.status_code in (302, 303)

    with client.session_transaction() as sess:
        assert sess.get(f"tienda_sel_{cat1_id}") == [c1]
        assert sess.get(f"tienda_sel_{cat2_id}") == [c2]

    rm = client.post("/tienda/tok_sel_one/seleccion/quitar", data={"candidata_id": str(c1), "return_to": "/tienda/tok_sel_one/mi-seleccion"}, follow_redirects=False)
    assert rm.status_code in (302, 303)
    cl = client.post("/tienda/tok_sel_two/seleccion/limpiar", data={"return_to": "/tienda/tok_sel_two/mi-seleccion"}, follow_redirects=False)
    assert cl.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get(f"tienda_sel_{cat1_id}") == []
        assert sess.get(f"tienda_sel_{cat2_id}") == []


def test_checkout_requires_selection_and_post_creates_interes_items_and_admin_views():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cat = _seed_catalog("tok_checkout", with_cliente=True)
        cat_id = int(cat.id)
        c1, c2, _hidden = _seed_candidates(seed=24)

    empty_post = client.post("/tienda/tok_checkout/solicitar-entrevistas", data={"nombre_contacto": "Ana", "telefono_contacto": "8095551111"}, follow_redirects=False)
    assert empty_post.status_code in (302, 303)

    client.post("/tienda/tok_checkout/seleccion/agregar", data={"candidata_id": str(c1), "return_to": "/tienda/tok_checkout"}, follow_redirects=False)
    client.post("/tienda/tok_checkout/seleccion/agregar", data={"candidata_id": str(c2), "return_to": "/tienda/tok_checkout"}, follow_redirects=False)
    sel_page = client.get("/tienda/tok_checkout/mi-seleccion", follow_redirects=False)
    assert sel_page.status_code == 200
    assert "Ana Perfil Publico" in sel_page.get_data(as_text=True)

    checkout_get = client.get("/tienda/tok_checkout/solicitar-entrevistas", follow_redirects=False)
    assert checkout_get.status_code == 200
    get_html = checkout_get.get_data(as_text=True)
    assert "Ana Cliente" in get_html
    assert "8095551111" in get_html
    assert "Confirmado por la agencia" in get_html
    assert 'name="nombre_contacto"' not in get_html
    assert 'name="telefono_contacto"' not in get_html

    send = client.post(
        "/tienda/tok_checkout/solicitar-entrevistas",
        data={
            "nombre_contacto": "Ana Cliente Editada",
            "telefono_contacto": "8095559999",
            "comentario": "Coordinar por WhatsApp",
            "candidata_ids": [str(c1), str(c2)],
        },
        follow_redirects=False,
    )
    assert send.status_code in (200, 302, 303)
    send_html = send.get_data(as_text=True)
    assert "Solicitud enviada" in send_html

    with flask_app.app_context():
        interes = TiendaInteres.query.order_by(TiendaInteres.id.desc()).first()
        assert interes is not None
        assert interes.catalogo_id == cat_id
        assert interes.nombre_contacto == "Ana Cliente"
        assert interes.telefono_contacto == "8095551111"
        items = TiendaInteresItem.query.filter_by(interes_id=interes.id).order_by(TiendaInteresItem.orden.asc()).all()
        assert len(items) == 2

    _login_owner(client)
    admin_list = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert admin_list.status_code == 200
    list_html = admin_list.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in list_html
    assert "Ana Cliente" in list_html

    with flask_app.app_context():
        interes_id = int(TiendaInteres.query.order_by(TiendaInteres.id.desc()).first().id)
    admin_detail = client.get(f"/admin/tienda-intereses/{interes_id}", follow_redirects=False)
    assert admin_detail.status_code == 200
    det_html = admin_detail.get_data(as_text=True)
    assert "Candidatas seleccionadas" in det_html


def test_checkout_without_cliente_shows_editable_inputs_and_requires_them():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_checkout_no_cliente", with_cliente=False)
        c1, _c2, _hidden = _seed_candidates(seed=27)

    client.post("/tienda/tok_checkout_no_cliente/seleccion/agregar", data={"candidata_id": str(c1), "return_to": "/tienda/tok_checkout_no_cliente"}, follow_redirects=False)

    checkout_get = client.get("/tienda/tok_checkout_no_cliente/solicitar-entrevistas", follow_redirects=False)
    assert checkout_get.status_code == 200
    html = checkout_get.get_data(as_text=True)
    assert 'name="nombre_contacto"' in html
    assert 'name="telefono_contacto"' in html
    assert "Confirmado por la agencia" not in html

    missing = client.post(
        "/tienda/tok_checkout_no_cliente/solicitar-entrevistas",
        data={"comentario": "Sin contacto", "candidata_ids": [str(c1)]},
        follow_redirects=False,
    )
    assert missing.status_code == 400


def test_private_store_privacy_html():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_privacy")
        _seed_candidates(seed=25)

    html = client.get("/tienda/tok_privacy", follow_redirects=False).get_data(as_text=True).lower()
    forbidden = [
        "/admin", "/clientes", "/login", "cedula", "teléfono", "telefono",
        "direccion", "referencia", "notas internas", "score", "token_hash", "token_hint",
    ]
    for marker in forbidden:
        assert marker not in html


def test_manual_shortlist_mode_still_not_breaking_legacy_catalogo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cat = _seed_catalog("tok_manual_legacy2", scope_mode="manual_shortlist")
        c1, _c2, _h = _seed_candidates(seed=26)
        db.session.add(CatalogoPrivadoItem(catalogo_id=cat.id, candidata_id=c1, orden=1, is_visible=True))
        db.session.commit()

    resp = client.get("/tienda/tok_manual_legacy2", follow_redirects=False)
    assert resp.status_code in (301, 302, 303)
    assert "/catalogo/tok_manual_legacy2" in (resp.headers.get("Location") or "")
