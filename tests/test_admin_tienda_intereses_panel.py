# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud, TiendaInteres, TiendaInteresItem
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud, TiendaInteres, TiendaInteresItem],
        reset=False,
    )


def _login_owner(client):
    client.get("/admin/login", follow_redirects=False)
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_data(seed: int = 1):
    cat = CatalogoPrivado(
        nombre="Cat admin panel",
        token_hash=_token_hash(f"tok_admin_panel_{seed}"),
        token_hint=f"adm_pan_{seed:04d}"[-12:],
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=7),
        created_by="pytest",
    )
    cliente = Cliente(nombre_completo="Ana Cliente", telefono="8095551111", codigo=f"CLI-ADM-PANEL-{seed}", role="cliente")
    db.session.add_all([cat, cliente])
    db.session.flush()

    base = 912000 + seed * 10
    c1 = Candidata(fila=base + 1, nombre_completo="Interna A", cedula=f"{base + 1:011d}", codigo=f"ADM-A-{seed}")
    c2 = Candidata(fila=base + 2, nombre_completo="Interna B", cedula=f"{base + 2:011d}", codigo=f"ADM-B-{seed}")
    c3 = Candidata(fila=base + 3, nombre_completo="Interna C", cedula=f"{base + 3:011d}", codigo=f"ADM-C-{seed}")
    db.session.add_all([c1, c2, c3])
    db.session.flush()

    db.session.add_all([
        CandidataWeb(
            candidata_id=c1.fila,
            visible=True,
            estado_publico="disponible",
            nombre_publico="Publica A",
            ciudad_publica="Santiago",
            modalidad_publica="Con dormida",
            tags_publicos="Limpieza",
            disponible_inmediato=True,
        ),
        CandidataWeb(
            candidata_id=c2.fila,
            visible=True,
            estado_publico="reservada",
            nombre_publico="Publica B",
            ciudad_publica="Santo Domingo",
            modalidad_publica="Salida diaria",
            tags_publicos="Cocina",
            disponible_inmediato=False,
        ),
        CandidataWeb(
            candidata_id=c3.fila,
            visible=False,
            estado_publico="no_disponible",
            nombre_publico="Publica C",
            ciudad_publica="La Vega",
            modalidad_publica="Con dormida",
            tags_publicos="Planchar",
            disponible_inmediato=False,
        ),
    ])

    interes = TiendaInteres(
        catalogo_id=cat.id,
        cliente_id=cliente.id,
        nombre_contacto="Ana Cliente",
        telefono_contacto="809-555-2222",
        comentario="Coordinar por WhatsApp",
        estado="nuevo",
        token_hint_usado="tok_admin_pan",
    )
    db.session.add(interes)
    db.session.flush()

    db.session.add_all([
        TiendaInteresItem(interes_id=interes.id, candidata_id=c1.fila, orden=1),
        TiendaInteresItem(interes_id=interes.id, candidata_id=c2.fila, orden=2),
        TiendaInteresItem(interes_id=interes.id, candidata_id=c3.fila, orden=3),
    ])
    db.session.commit()
    return int(interes.id)


def test_admin_tienda_intereses_requires_staff_login():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    resp = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_admin_tienda_intereses_list_and_filters_and_panel_button():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_data(seed=1)

    _login_owner(client)

    resp = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in html
    assert "Ana Cliente" in html
    assert "Candidatas:" in html
    assert "Entrar al panel" in html

    filtered = client.get("/admin/tienda-intereses?estado=nuevo&solo_nuevos=1&q=809", follow_redirects=False)
    assert filtered.status_code == 200
    filtered_html = filtered.get_data(as_text=True)
    assert "Ana Cliente" in filtered_html


def test_admin_tienda_intereses_detail_status_visual_whatsapp_and_change_estado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        interes_id = _seed_data(seed=2)

    _login_owner(client)

    detail = client.get(f"/admin/tienda-intereses/{interes_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Datos del cliente" in html
    assert "td-card-dark" in html
    assert "td-card-client" in html
    assert "background:#fff" not in html
    assert "/domesticas/" not in html
    assert "Ver perfil público" not in html
    assert "Ver candidata" in html
    assert "Editar perfil público" in html
    assert "Marcar como revisada" in html
    assert "Cambiar a En gestión" in html
    assert "Copiar mensaje para WhatsApp" in html
    assert "Publica A" in html
    assert "Publica B" in html
    assert "Publica C" in html
    assert "Disponible para coordinar" in html
    assert "Reservada / revisar antes de ofrecer" in html
    assert "Oculta del catálogo" in html or "No disponible actualmente" in html
    assert "Copiar mensaje para WhatsApp" in html

    change = client.post(
        f"/admin/tienda-intereses/{interes_id}/estado",
        data={"estado": "contactado"},
        follow_redirects=False,
    )
    assert change.status_code in (302, 303)

    with flask_app.app_context():
        row = TiendaInteres.query.get(interes_id)
        item_id = int((row.items or [])[0].id)

    mark_reviewed = client.post(
        f"/admin/tienda-intereses/{interes_id}/items/{item_id}/revisar",
        data={},
        follow_redirects=False,
    )
    assert mark_reviewed.status_code in (302, 303)

    mark_state = client.post(
        f"/admin/tienda-intereses/{interes_id}/items/{item_id}/estado-publico",
        data={"estado_publico": "reservada"},
        follow_redirects=False,
    )
    assert mark_state.status_code in (302, 303)

    with flask_app.app_context():
        row = TiendaInteres.query.get(interes_id)
        assert row is not None
        assert row.estado == "contactado"
        assert "[revisada:" in str(row.comentario or "")
        ficha = CandidataWeb.query.filter_by(candidata_id=int(row.items[0].candidata_id)).first()
        assert ficha is not None
        assert str(ficha.estado_publico) == "reservada"
