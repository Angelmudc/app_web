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
        [Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud, TiendaInteres, TiendaInteresItem],
        reset=False,
    )


def _login_owner(client) -> None:
    client.get("/admin/login", follow_redirects=False)
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_flow(token: str = "tok_e2e_flow") -> dict[str, int | str]:
    cliente = Cliente(nombre_completo="Cliente E2E", telefono="8095557001", codigo="CLI-E2E-FLOW", role="cliente")
    db.session.add(cliente)
    db.session.flush()

    solicitud = Solicitud(cliente_id=cliente.id, codigo_solicitud="SOL-E2E-FLOW", estado="proceso")
    db.session.add(solicitud)
    db.session.flush()

    cat = CatalogoPrivado(
        nombre="Catalogo E2E",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=5),
        cliente_id=cliente.id,
        solicitud_id=solicitud.id,
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    base = 996500
    c1 = Candidata(
        fila=base + 1,
        nombre_completo="Interna Uno",
        cedula=f"{base + 1:011d}",
        codigo="E2E-CAND-1",
        direccion_completa="Calle privada 1",
    )
    c2 = Candidata(
        fila=base + 2,
        nombre_completo="Interna Dos",
        cedula=f"{base + 2:011d}",
        codigo="E2E-CAND-2",
    )
    db.session.add_all([c1, c2])
    db.session.flush()

    db.session.add_all([
        CandidataWeb(
            candidata_id=c1.fila,
            visible=True,
            estado_publico="disponible",
            nombre_publico="Perfil Publico Uno",
            ciudad_publica="Santiago",
            modalidad_publica="Con dormida",
            tags_publicos="Limpieza",
            experiencia_resumen="Experiencia validada",
            entrevista_publica_resumen="Perfil revisado por la agencia",
            disponible_inmediato=True,
        ),
        CandidataWeb(
            candidata_id=c2.fila,
            visible=True,
            estado_publico="disponible",
            nombre_publico="Perfil Publico Dos",
            ciudad_publica="Santo Domingo",
            modalidad_publica="Salida diaria",
            tags_publicos="Cocina",
            experiencia_resumen="Experiencia en cocina",
            entrevista_publica_resumen="Perfil revisado por la agencia",
            disponible_inmediato=False,
        ),
    ])
    db.session.commit()

    return {
        "cliente_id": int(cliente.id),
        "catalogo_id": int(cat.id),
        "cand1": int(c1.fila),
        "cand2": int(c2.fila),
        "token": token,
    }


def test_private_store_end_to_end_full_flow_and_admin_badge():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_flow(token="tok_e2e_ok")

    token = str(seeded["token"])
    cand1 = int(seeded["cand1"])
    cand2 = int(seeded["cand2"])

    store = client.get(f"/tienda/{token}", follow_redirects=False)
    assert store.status_code == 200
    store_html = store.get_data(as_text=True)
    assert "Perfil Publico Uno" in store_html
    assert "Perfil Publico Dos" in store_html

    forbidden = ["cedula", "dirección", "direccion", "referencias", "notas internas", "score", "/admin", "/clientes", "token_hash"]
    lowered = store_html.lower()
    for marker in forbidden:
        assert marker not in lowered

    add1 = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(cand1), "return_to": f"/tienda/{token}"},
        follow_redirects=False,
    )
    add2 = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(cand2), "return_to": f"/tienda/{token}"},
        follow_redirects=False,
    )
    assert add1.status_code in (302, 303)
    assert add2.status_code in (302, 303)

    selected = client.get(f"/tienda/{token}/mi-seleccion", follow_redirects=False)
    assert selected.status_code == 200
    selected_html = selected.get_data(as_text=True)
    assert "Perfil Publico Uno" in selected_html
    assert "Perfil Publico Dos" in selected_html

    checkout_get = client.get(f"/tienda/{token}/solicitar-entrevistas", follow_redirects=False)
    assert checkout_get.status_code == 200

    checkout_post = client.post(
        f"/tienda/{token}/solicitar-entrevistas",
        data={
            "nombre_contacto": "No debe persistir por cliente vinculado",
            "telefono_contacto": "8090000000",
            "comentario": "Llamar en la tarde",
            "candidata_ids": [str(cand1), str(cand2)],
        },
        follow_redirects=False,
    )
    assert checkout_post.status_code in (200, 302, 303)
    success_html = checkout_post.get_data(as_text=True)
    assert "Solicitud enviada" in success_html

    with flask_app.app_context():
        interes = TiendaInteres.query.order_by(TiendaInteres.id.desc()).first()
        assert interes is not None
        assert interes.estado == "nuevo"
        assert int(interes.cliente_id or 0) == int(seeded["cliente_id"])
        items = TiendaInteresItem.query.filter_by(interes_id=interes.id).order_by(TiendaInteresItem.orden.asc()).all()
        assert len(items) == 2
        interes_id = int(interes.id)

    _login_owner(client)

    admin_list = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert admin_list.status_code == 200
    assert "Cliente E2E" in admin_list.get_data(as_text=True)

    badge = client.get("/admin/tienda-intereses/badge.json", follow_redirects=False)
    assert badge.status_code == 200
    badge_data = badge.get_json() or {}
    assert badge_data.get("ok") is True
    before_nuevo_count = int(badge_data.get("nuevo_count") or 0)
    assert before_nuevo_count >= 1

    detail = client.get(f"/admin/tienda-intereses/{interes_id}", follow_redirects=False)
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert "Perfil Publico Uno" in detail_html
    assert "Perfil Publico Dos" in detail_html

    update_estado = client.post(
        f"/admin/tienda-intereses/{interes_id}/estado",
        data={"estado": "en_gestion"},
        follow_redirects=False,
    )
    assert update_estado.status_code in (302, 303)

    with flask_app.app_context():
        row = TiendaInteres.query.get(interes_id)
        assert row is not None
        assert row.estado == "en_gestion"

    badge_after = client.get("/admin/tienda-intereses/badge.json", follow_redirects=False)
    assert badge_after.status_code == 200
    badge_after_data = badge_after.get_json() or {}
    after_nuevo_count = int(badge_after_data.get("nuevo_count") or 0)
    assert after_nuevo_count <= max(0, before_nuevo_count - 1)


def test_private_store_token_inactivo_o_expirado_no_permite_flujo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        inactive = CatalogoPrivado(
            nombre="Cat inactivo",
            token_hash=_token_hash("tok_inactivo_e2e"),
            token_hint="inactivo",
            scope_mode="all_available_store",
            is_active=False,
            expires_at=utc_now_naive() + timedelta(days=2),
            created_by="pytest",
        )
        expired = CatalogoPrivado(
            nombre="Cat expirado",
            token_hash=_token_hash("tok_expirado_e2e"),
            token_hint="expirado",
            scope_mode="all_available_store",
            is_active=True,
            expires_at=utc_now_naive() - timedelta(days=1),
            created_by="pytest",
        )
        db.session.add_all([inactive, expired])
        db.session.commit()

    assert client.get("/tienda/tok_inactivo_e2e", follow_redirects=False).status_code == 410
    assert client.get("/tienda/tok_expirado_e2e", follow_redirects=False).status_code == 410

    add_inactive = client.post(
        "/tienda/tok_inactivo_e2e/seleccion/agregar",
        data={"candidata_id": "1"},
        follow_redirects=False,
    )
    add_expired = client.post(
        "/tienda/tok_expirado_e2e/seleccion/agregar",
        data={"candidata_id": "1"},
        follow_redirects=False,
    )
    assert add_inactive.status_code == 410
    assert add_expired.status_code == 410
