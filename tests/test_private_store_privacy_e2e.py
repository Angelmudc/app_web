# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, Cliente, TiendaInteres
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.catalogo_privado_tokens import catalogo_privado_token_hash
from utils.timezone import utc_now_naive


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [Candidata, CandidataWeb, CatalogoPrivado, Cliente, TiendaInteres],
        reset=False,
    )


def _seed_privacy_data(token: str = "tok_privacy_e2e") -> dict[str, int | str]:
    cliente = Cliente(
        nombre_completo="Cliente Privacy",
        telefono="8095557711",
        codigo="CLI-PRIV-E2E",
        role="cliente",
    )
    db.session.add(cliente)
    db.session.flush()

    cat = CatalogoPrivado(
        nombre="Catalogo Privacy",
        token_hash=catalogo_privado_token_hash(token),
        token_hint=token[-12:],
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=4),
        cliente_id=cliente.id,
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    cand = Candidata(
        fila=995991,
        nombre_completo="Privacidad Interna",
        codigo="PRIV-CAND-1",
        cedula="00112345678",
        numero_telefono="8099990000",
        direccion_completa="Direccion exacta interna",
    )
    db.session.add(cand)
    db.session.flush()

    db.session.add(
        CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico="disponible",
            nombre_publico="Perfil Privado Visible",
            ciudad_publica="Santiago",
            modalidad_publica="Con dormida",
            tags_publicos="Limpieza general",
            experiencia_resumen="Sin datos sensibles",
            entrevista_publica_resumen="Resumen publico",
        )
    )
    db.session.commit()

    return {"token": token, "candidata_id": int(cand.fila)}


def _login_owner(client) -> None:
    client.get("/admin/login", follow_redirects=False)
    resp = client.post(
        "/admin/login",
        data={"usuario": "Owner", "clave": "admin123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def _assert_private_surface(html: str) -> None:
    low = html.lower()
    for forbidden in [
        "cedula",
        "dirección exacta",
        "direccion exacta",
        "referencia interna",
        "notas internas",
        "score",
        "telefono de candidata",
        "token_hash",
        "/admin",
        "/clientes",
    ]:
        assert forbidden not in low


def test_private_store_privacy_across_public_token_flow_and_admin_scope():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        seeded = _seed_privacy_data()

    token = str(seeded["token"])
    candidata_id = int(seeded["candidata_id"])

    list_resp = client.get(f"/tienda/{token}", follow_redirects=False)
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)
    assert "Perfil Privado Visible" in list_html
    _assert_private_surface(list_html)

    detail_resp = client.get(f"/tienda/{token}/domesticas/{candidata_id}", follow_redirects=False)
    assert detail_resp.status_code == 200
    detail_html = detail_resp.get_data(as_text=True)
    assert "Perfil Privado Visible" in detail_html
    _assert_private_surface(detail_html)

    add_resp = client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(candidata_id), "return_to": f"/tienda/{token}"},
        follow_redirects=False,
    )
    assert add_resp.status_code in (302, 303)

    selection_resp = client.get(f"/tienda/{token}/mi-seleccion", follow_redirects=False)
    assert selection_resp.status_code == 200
    selection_html = selection_resp.get_data(as_text=True)
    assert "Perfil Privado Visible" in selection_html
    _assert_private_surface(selection_html)

    checkout_resp = client.get(f"/tienda/{token}/solicitar-entrevistas", follow_redirects=False)
    assert checkout_resp.status_code == 200
    checkout_html = checkout_resp.get_data(as_text=True)
    _assert_private_surface(checkout_html)

    submit = client.post(
        f"/tienda/{token}/solicitar-entrevistas",
        data={
            "nombre_contacto": "No aplica si linked cliente",
            "telefono_contacto": "8090000000",
            "comentario": "Privacidad end to end",
            "candidata_ids": [str(candidata_id)],
        },
        follow_redirects=False,
    )
    assert submit.status_code in (200, 302, 303)
    success_html = submit.get_data(as_text=True)
    assert "Solicitud enviada" in success_html
    _assert_private_surface(success_html)

    _login_owner(client)
    admin_list = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert admin_list.status_code == 200
    admin_list_html = admin_list.get_data(as_text=True)
    assert "Cliente Privacy" in admin_list_html

    with flask_app.app_context():
        interes = TiendaInteres.query.order_by(TiendaInteres.id.desc()).first()
        assert interes is not None
        interes_id = int(interes.id)

    admin_detail = client.get(f"/admin/tienda-intereses/{interes_id}", follow_redirects=False)
    assert admin_detail.status_code == 200
    admin_detail_html = admin_detail.get_data(as_text=True)
    assert "Cliente Privacy" in admin_detail_html
    assert "8095557711" in admin_detail_html

    update_estado = client.post(
        f"/admin/tienda-intereses/{interes_id}/estado",
        data={"estado": "en_gestion"},
        follow_redirects=False,
    )
    assert update_estado.status_code in (302, 303)
