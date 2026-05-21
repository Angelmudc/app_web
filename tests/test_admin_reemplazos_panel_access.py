# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets

from app import app as flask_app
import admin.routes as admin_routes
from config_app import db
from models import Candidata, Cliente, Reemplazo, Solicitud, StaffAuditLog, StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([StaffUser, StaffAuditLog, Cliente, Candidata, Solicitud, Reemplazo], reset=True)


def _login_staff(client):
    resp = client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_clientes_list_and_home_expose_reemplazos_panel_access():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        token = secrets.token_hex(4)
        cliente = Cliente(codigo=f"CR-{token}", nombre_completo=f"Cliente {token}", email=f"cr_{token}@example.com", telefono="8091112222")
        db.session.add(cliente)
        db.session.flush()
        cand = Candidata(nombre_completo=f"Cand {token}", cedula="12345678901", numero_telefono="8092223333", estado="trabajando")
        db.session.add(cand)
        db.session.flush()
        solicitud = Solicitud(cliente_id=int(cliente.id), codigo_solicitud=f"SOL-{token}", estado="reemplazo", candidata_id=int(cand.fila))
        db.session.add(solicitud)
        db.session.flush()
        repl = Reemplazo(solicitud_id=int(solicitud.id), candidata_old_id=int(cand.fila), motivo_fallo="Demo")
        repl.iniciar_reemplazo()
        db.session.add(repl)
        db.session.commit()
        cliente_id = int(cliente.id)

    _login_staff(client)

    resp_home = client.get("/home", follow_redirects=False)
    assert resp_home.status_code == 200
    assert "Panel de reemplazos" in resp_home.get_data(as_text=True)

    resp_clientes = client.get(f"/admin/clientes?q={token}&per_page=10", follow_redirects=False)
    assert resp_clientes.status_code == 200
    html = resp_clientes.get_data(as_text=True)
    assert "Panel de reemplazos" in html
    assert "Reemplazos" in html
    assert "Nuevo reemplazo" in html
    assert "activos /" in html
    assert "total" in html
    assert f"/admin/reemplazos?cliente_id={cliente_id}" in html
    assert f"/admin/reemplazos/nuevo?cliente_id={cliente_id}" in html

    resp_clientes_async = client.get(
        f"/admin/clientes?q={token}&per_page=10",
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        },
        follow_redirects=False,
    )
    assert resp_clientes_async.status_code == 200
    payload = resp_clientes_async.get_json() or {}
    partial_html = payload.get("replace_html") or ""
    assert "/admin/reemplazos?cliente_id=" in partial_html
    assert "/admin/reemplazos/nuevo?cliente_id=" in partial_html


def test_reemplazo_nuevo_panel_uses_search_and_single_reason_field():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)

    resp = client.get("/admin/reemplazos/nuevo", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Buscar cliente" in html
    assert "/admin/reemplazos/clientes-search" in html
    assert "/admin/reemplazos/cliente/" in html
    assert "name=\"motivo_reemplazo_code\"" in html
    assert "Motivo (código)" not in html
    assert "Fecha reporte" not in html
    assert "name=\"candidata_old_id\"" not in html
    assert "Selecciona cliente" not in html


def test_reemplazos_clientes_search_and_solicitudes_json_and_post_validation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        token = secrets.token_hex(4)
        c1 = Cliente(codigo=f"CODE-{token}", nombre_completo=f"Cliente {token}", email=f"a_{token}@x.com", telefono="8091112222")
        c2 = Cliente(codigo=f"OTRO-{token}", nombre_completo=f"Nombre {token}", email=f"b_{token}@x.com", telefono="8295556666")
        db.session.add_all([c1, c2])
        db.session.flush()
        cand = Candidata(nombre_completo=f"Cand {token}", cedula="12345678901", numero_telefono="8092223333", estado="trabajando")
        db.session.add(cand)
        db.session.flush()
        s1 = Solicitud(cliente_id=int(c1.id), codigo_solicitud=f"SOL-{token}-A", estado="activa", candidata_id=int(cand.fila), ciudad_sector="SD")
        s2 = Solicitud(cliente_id=int(c2.id), codigo_solicitud=f"SOL-{token}-B", estado="activa", candidata_id=int(cand.fila), ciudad_sector="STI")
        db.session.add_all([s1, s2])
        db.session.commit()
        c1_id = int(c1.id)
        c2_id = int(c2.id)
        s1_id = int(s1.id)
        s2_id = int(s2.id)

    _login_staff(client)
    by_code = client.get(f"/admin/reemplazos/clientes-search?q=CODE-{token}", follow_redirects=False)
    assert by_code.status_code == 200
    rows = (by_code.get_json() or {}).get("results") or []
    assert rows
    assert any(int(r.get("id") or 0) == c1_id for r in rows)

    by_phone = client.get("/admin/reemplazos/clientes-search?q=809111", follow_redirects=False)
    assert by_phone.status_code == 200
    rows_phone = (by_phone.get_json() or {}).get("results") or []
    assert any(int(r.get("id") or 0) == c1_id for r in rows_phone)
    assert len(rows_phone) <= 20

    sol_json = client.get(f"/admin/reemplazos/cliente/{c1_id}/solicitudes.json", follow_redirects=False)
    assert sol_json.status_code == 200
    sol_rows = (sol_json.get_json() or {}).get("results") or []
    assert any(int(r.get("id") or 0) == s1_id for r in sol_rows)
    assert all(int(r.get("id") or 0) != s2_id for r in sol_rows)

    bad_post = client.post(
        "/admin/reemplazos/nuevo",
        data={"cliente_id": str(c1_id), "solicitud_id": str(s2_id), "motivo_reemplazo_code": "inasistencia"},
        follow_redirects=False,
    )
    assert bad_post.status_code in (302, 303)

    ok_post = client.post(
        "/admin/reemplazos/nuevo",
        data={
            "cliente_id": str(c1_id),
            "solicitud_id": str(s1_id),
            "motivo_reemplazo_code": "inasistencia",
            "nota": "Prueba",
            "prioridad": "critica",
            "fecha_reporte": "2001-01-01",
        },
        follow_redirects=False,
    )
    assert ok_post.status_code in (302, 303)
    with flask_app.app_context():
        repl = Reemplazo.query.filter_by(solicitud_id=s1_id).order_by(Reemplazo.id.desc()).first()
        assert repl is not None
        assert int(repl.candidata_old_id or 0) > 0
        assert (repl.motivo_reemplazo_code or "") == "inasistencia"
        assert (repl.prioridad or "") == "media"
        now = admin_routes.utc_now_naive()
        assert abs((now - repl.fecha_reporte).total_seconds()) < 180
