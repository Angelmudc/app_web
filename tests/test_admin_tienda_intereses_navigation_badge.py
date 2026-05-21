# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Cliente, TiendaInteres
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _login_owner(client) -> None:
    client.get("/admin/login", follow_redirects=False)
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _ensure_tables() -> None:
    from models import CatalogoPrivado, CatalogoPrivadoItem

    ensure_sqlite_compat_tables([Cliente, CatalogoPrivado, CatalogoPrivadoItem, TiendaInteres], reset=False)


def _seed_nav_data() -> int:
    from models import CatalogoPrivado

    cliente = Cliente(nombre_completo="Cliente Nav", telefono="8095558888", codigo="CLI-NAV-TI", role="cliente")
    db.session.add(cliente)
    db.session.flush()

    cat = CatalogoPrivado(
        nombre="Cat nav tienda",
        token_hash=_token_hash("tok_nav_tienda"),
        token_hint="navtienda",
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=5),
        cliente_id=cliente.id,
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    db.session.add(
        TiendaInteres(
            catalogo_id=cat.id,
            cliente_id=cliente.id,
            nombre_contacto="Cliente Nav",
            telefono_contacto="809-555-8888",
            comentario="Pendiente",
            estado="nuevo",
            token_hint_usado="navtienda",
        )
    )
    db.session.commit()
    return int(cliente.id)


def test_admin_nav_y_clientes_muestran_acceso_y_badge_tienda_intereses():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_nav_data()

    _login_owner(client)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    home_html = home.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in home_html
    assert "Ver solicitudes" in home_html

    nav_page = client.get("/admin/tienda-intereses", follow_redirects=False)
    assert nav_page.status_code == 200
    nav_html = nav_page.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in nav_html

    badge = client.get("/admin/tienda-intereses/badge.json", follow_redirects=False)
    assert badge.status_code == 200
    payload = badge.get_json() or {}
    assert payload.get("ok") is True
    assert int(payload.get("nuevo_count") or 0) >= 1

    clientes = client.get("/admin/clientes?q=CLI-NAV-TI", follow_redirects=False)
    assert clientes.status_code == 200
    clientes_html = clientes.get_data(as_text=True)
    assert "Solicitudes de entrevistas" in clientes_html
    assert "Ver solicitudes tienda" in clientes_html
    assert f"/admin/tienda-intereses?cliente_id={cliente_id}" in clientes_html
    assert "Ver solicitudes tienda (1)" in clientes_html
