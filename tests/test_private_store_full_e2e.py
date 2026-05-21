# -*- coding: utf-8 -*-

from __future__ import annotations

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
from utils.catalogo_privado_tokens import catalogo_privado_token_hash
from utils.timezone import utc_now_naive


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


def _login_owner(client) -> None:
    client.get("/admin/login", follow_redirects=False)
    resp = client.post(
        "/admin/login",
        data={"usuario": "Owner", "clave": "admin123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def _seed_full_flow(token: str = "tok_full_e2e") -> dict[str, int | str]:
    cliente = Cliente(
        nombre_completo="Cliente Full E2E",
        telefono="8095553333",
        codigo="CLI-FULL-E2E",
        role="cliente",
    )
    db.session.add(cliente)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=cliente.id,
        codigo_solicitud="SOL-FULL-E2E",
        estado="proceso",
    )
    db.session.add(solicitud)
    db.session.flush()

    catalogo = CatalogoPrivado(
        nombre="Catalogo Full E2E",
        token_hash=catalogo_privado_token_hash(token),
        token_hint=token[-12:],
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=7),
        cliente_id=cliente.id,
        solicitud_id=solicitud.id,
        created_by="pytest",
    )
    db.session.add(catalogo)
    db.session.flush()

    base = 995700
    c1 = Candidata(
        fila=base + 1,
        nombre_completo="Privada Uno",
        cedula=f"{base + 1:011d}",
        codigo="FULL-CAND-1",
        direccion_completa="Calle privada 1",
    )
    c2 = Candidata(
        fila=base + 2,
        nombre_completo="Privada Dos",
        cedula=f"{base + 2:011d}",
        codigo="FULL-CAND-2",
        direccion_completa="Calle privada 2",
    )
    c3 = Candidata(
        fila=base + 3,
        nombre_completo="Privada Tres Oculta",
        cedula=f"{base + 3:011d}",
        codigo="FULL-CAND-3",
    )
    db.session.add_all([c1, c2, c3])
    db.session.flush()

    db.session.add_all(
        [
            CandidataWeb(
                candidata_id=c1.fila,
                visible=True,
                estado_publico="disponible",
                nombre_publico="Perfil Full Uno",
                ciudad_publica="Santiago",
                modalidad_publica="Con dormida",
                tags_publicos="Limpieza general, Cuidar niños",
                experiencia_resumen="Experiencia en hogar con niños",
                entrevista_publica_resumen="Perfil revisado",
                disponible_inmediato=True,
            ),
            CandidataWeb(
                candidata_id=c2.fila,
                visible=True,
                estado_publico="disponible",
                nombre_publico="Perfil Full Dos",
                ciudad_publica="Santo Domingo",
                modalidad_publica="Salida diaria",
                tags_publicos="Cocinar",
                experiencia_resumen="Experiencia en cocina",
                entrevista_publica_resumen="Perfil revisado",
                disponible_inmediato=False,
            ),
            CandidataWeb(
                candidata_id=c3.fila,
                visible=False,
                estado_publico="no_disponible",
                nombre_publico="Perfil Full Tres",
                ciudad_publica="La Vega",
                modalidad_publica="Con dormida",
                tags_publicos="Limpieza general",
                experiencia_resumen="No deberia verse",
            ),
        ]
    )
    db.session.commit()

    return {
        "token": token,
        "cliente_id": int(cliente.id),
        "c1": int(c1.fila),
        "c2": int(c2.fila),
        "c3": int(c3.fila),
    }


def _json_headers() -> dict[str, str]:
    return {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


def test_private_store_full_end_to_end_client_and_admin_flow():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_full_flow("tok_full_complete")

    token = str(seeded["token"])
    c1 = int(seeded["c1"])
    c2 = int(seeded["c2"])

    store = client.get(f"/tienda/{token}", follow_redirects=False)
    assert store.status_code == 200
    html = store.get_data(as_text=True)
    html_low = html.lower()
    assert "Perfil Full Uno" in html
    assert "Perfil Full Dos" in html
    assert "Perfil Full Tres" not in html
    for forbidden in [
        "cedula",
        "direccion exacta",
        "direccion",
        "referencias",
        "notas internas",
        "score",
        "token_hash",
        "/admin",
        "/clientes",
    ]:
        assert forbidden not in html_low

    assert "Perfil Full Uno" in client.get(
        f"/tienda/{token}?funciones=Limpieza+general", follow_redirects=False
    ).get_data(as_text=True)
    assert "Perfil Full Dos" not in client.get(
        f"/tienda/{token}?funciones=Limpieza+general", follow_redirects=False
    ).get_data(as_text=True)

    assert "Perfil Full Dos" in client.get(
        f"/tienda/{token}?funciones=Cocinar", follow_redirects=False
    ).get_data(as_text=True)
    assert "Perfil Full Uno" not in client.get(
        f"/tienda/{token}?funciones=Cocinar", follow_redirects=False
    ).get_data(as_text=True)

    cuidar_html = client.get(f"/tienda/{token}?funciones=Cuidar+ni%C3%B1os", follow_redirects=False).get_data(as_text=True)
    assert "Perfil Full Uno" in cuidar_html
    assert "Perfil Full Dos" not in cuidar_html

    assert "Perfil Full Uno" in client.get(
        f"/tienda/{token}?modalidad=Con+dormida", follow_redirects=False
    ).get_data(as_text=True)
    assert "Perfil Full Dos" in client.get(
        f"/tienda/{token}?modalidad=Salida+diaria", follow_redirects=False
    ).get_data(as_text=True)

    assert "Perfil Full Uno" in client.get(
        f"/tienda/{token}?ciudad=Santiago", follow_redirects=False
    ).get_data(as_text=True)
    assert "Perfil Full Dos" not in client.get(
        f"/tienda/{token}?ciudad=Santiago", follow_redirects=False
    ).get_data(as_text=True)

    add1 = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(c1)},
        headers=_json_headers(),
    )
    assert add1.status_code == 200
    assert add1.get_json()["selection_count"] == 1

    add1_dup = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(c1)},
        headers=_json_headers(),
    )
    assert add1_dup.status_code == 200
    assert add1_dup.get_json()["selection_count"] == 1

    add2 = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(c2)},
        headers=_json_headers(),
    )
    assert add2.status_code == 200
    assert add2.get_json()["selection_count"] == 2

    selected_page = client.get(f"/tienda/{token}/mi-seleccion", follow_redirects=False)
    assert selected_page.status_code == 200
    selected_html = selected_page.get_data(as_text=True)
    assert "Perfil Full Uno" in selected_html
    assert "Perfil Full Dos" in selected_html

    remove_one = client.post(
        f"/tienda/{token}/seleccion/quitar",
        data={"candidata_id": str(c1)},
        headers=_json_headers(),
    )
    assert remove_one.status_code == 200
    assert remove_one.get_json()["selection_count"] == 1

    clear = client.post(f"/tienda/{token}/seleccion/limpiar", data={}, headers=_json_headers())
    assert clear.status_code == 200
    assert clear.get_json()["selection_count"] == 0

    client.post(f"/tienda/{token}/seleccion/agregar", data={"candidata_id": str(c1)}, headers=_json_headers())
    client.post(f"/tienda/{token}/seleccion/agregar", data={"candidata_id": str(c2)}, headers=_json_headers())

    state_before = client.get(f"/tienda/{token}/estado.json", headers=_json_headers())
    assert state_before.status_code == 200
    payload_before = state_before.get_json() or {}
    assert payload_before.get("ok") is True
    assert int(payload_before.get("selection_count") or 0) == 2
    assert set(payload_before.get("selected_ids") or []) == {c1, c2}
    assert c1 in (payload_before.get("available_ids") or [])
    assert c2 in (payload_before.get("available_ids") or [])
    assert isinstance(payload_before.get("stats"), dict)

    with flask_app.app_context():
        row = CandidataWeb.query.filter_by(candidata_id=c2).first()
        assert row is not None
        row.estado_publico = "no_disponible"
        db.session.commit()

    state_after = client.get(f"/tienda/{token}/estado.json", headers=_json_headers())
    assert state_after.status_code == 200
    payload_after = state_after.get_json() or {}
    assert payload_after.get("ok") is True
    assert int(payload_after.get("selection_count") or 0) == 1
    assert c2 not in (payload_after.get("selected_ids") or [])
    assert c2 in (payload_after.get("removed_unavailable_ids") or [])

    checkout_get = client.get(f"/tienda/{token}/solicitar-entrevistas", follow_redirects=False)
    assert checkout_get.status_code == 200
    checkout_html = checkout_get.get_data(as_text=True)
    assert "Cliente Full E2E" in checkout_html
    assert "8095553333" in checkout_html
    assert "Confirmado por la agencia" in checkout_html
    assert 'name="nombre_contacto"' not in checkout_html
    assert 'name="telefono_contacto"' not in checkout_html

    checkout_post = client.post(
        f"/tienda/{token}/solicitar-entrevistas",
        data={
            "nombre_contacto": "Intento de sobreescribir",
            "telefono_contacto": "8090000000",
            "comentario": "Quiero coordinar mañana",
            "candidata_ids": [str(c1), str(c2)],
        },
        follow_redirects=False,
    )
    assert checkout_post.status_code in (200, 302, 303)
    assert "Solicitud enviada" in checkout_post.get_data(as_text=True)

    with flask_app.app_context():
        interes = TiendaInteres.query.order_by(TiendaInteres.id.desc()).first()
        assert interes is not None
        assert interes.nombre_contacto == "Cliente Full E2E"
        assert interes.telefono_contacto == "8095553333"
        assert interes.comentario == "Quiero coordinar mañana"
        items = (
            TiendaInteresItem.query.filter_by(interes_id=interes.id)
            .order_by(TiendaInteresItem.orden.asc())
            .all()
        )
        assert len(items) == 1
        assert int(items[0].candidata_id) == c1
        interes_id = int(interes.id)

    _login_owner(client)

    list_resp = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in list_html
    assert "Cliente Full E2E" in list_html

    badge = client.get("/admin/tienda-intereses/badge.json", follow_redirects=False)
    assert badge.status_code == 200
    badge_payload = badge.get_json() or {}
    assert badge_payload.get("ok") is True
    assert int(badge_payload.get("nuevo_count") or 0) == 1

    detail = client.get(f"/admin/tienda-intereses/{interes_id}", follow_redirects=False)
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert "Cliente Full E2E" in detail_html
    assert "8095553333" in detail_html
    assert "Quiero coordinar mañana" in detail_html
    assert "Perfil Full Uno" in detail_html
    assert "No disponible actualmente" in detail_html or "Disponible para coordinar" in detail_html
    assert "WhatsApp" in detail_html

    state_update = client.post(
        f"/admin/tienda-intereses/{interes_id}/estado",
        data={"estado": "en_gestion"},
        follow_redirects=False,
    )
    assert state_update.status_code in (302, 303)

    with flask_app.app_context():
        row = TiendaInteres.query.get(interes_id)
        assert row is not None
        assert row.estado == "en_gestion"

    badge_after = client.get("/admin/tienda-intereses/badge.json", follow_redirects=False)
    assert badge_after.status_code == 200
    assert int((badge_after.get_json() or {}).get("nuevo_count") or 0) == 0

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Solicitudes de entrevistas" in home.get_data(as_text=True)

    clientes_page = client.get("/admin/clientes?q=CLI-FULL-E2E", follow_redirects=False)
    assert clientes_page.status_code == 200
    clientes_html = clientes_page.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in clientes_html
    assert "Ver solicitudes tienda" in clientes_html

    nav_page = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert "Solicitudes de entrevistas" in nav_page.get_data(as_text=True)


def test_private_store_invalid_inactive_expired_and_manual_shortlist_legacy():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        active = CatalogoPrivado(
            nombre="Catalogo activo",
            token_hash=catalogo_privado_token_hash("tok_invalid_checks_ok"),
            token_hint="oktok",
            scope_mode="all_available_store",
            is_active=True,
            expires_at=utc_now_naive() + timedelta(days=2),
            created_by="pytest",
        )
        inactive = CatalogoPrivado(
            nombre="Catalogo inactivo",
            token_hash=catalogo_privado_token_hash("tok_invalid_checks_inactive"),
            token_hint="inactive",
            scope_mode="all_available_store",
            is_active=False,
            expires_at=utc_now_naive() + timedelta(days=2),
            created_by="pytest",
        )
        expired = CatalogoPrivado(
            nombre="Catalogo expirado",
            token_hash=catalogo_privado_token_hash("tok_invalid_checks_expired"),
            token_hint="expired",
            scope_mode="all_available_store",
            is_active=True,
            expires_at=utc_now_naive() - timedelta(days=1),
            created_by="pytest",
        )
        manual = CatalogoPrivado(
            nombre="Catalogo legacy manual",
            token_hash=catalogo_privado_token_hash("tok_invalid_checks_manual"),
            token_hint="manual",
            scope_mode="manual_shortlist",
            is_active=True,
            expires_at=utc_now_naive() + timedelta(days=2),
            created_by="pytest",
        )
        db.session.add_all([active, inactive, expired, manual])
        db.session.commit()

    assert client.get("/tienda/token-falso", follow_redirects=False).status_code == 404
    assert client.get("/tienda/tok_invalid_checks_inactive", follow_redirects=False).status_code == 410
    assert client.get("/tienda/tok_invalid_checks_expired", follow_redirects=False).status_code == 410

    manual_resp = client.get("/tienda/tok_invalid_checks_manual", follow_redirects=False)
    assert manual_resp.status_code in (301, 302, 303)
    assert "/catalogo/tok_invalid_checks_manual" in (manual_resp.headers.get("Location") or "")
