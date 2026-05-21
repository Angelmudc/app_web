# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets

from app import app as flask_app
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

    _login_staff(client)

    resp_home = client.get("/home", follow_redirects=False)
    assert resp_home.status_code == 200
    assert "Panel de reemplazos" in resp_home.get_data(as_text=True)

    resp_clientes = client.get(f"/admin/clientes?q={token}&per_page=10", follow_redirects=False)
    assert resp_clientes.status_code == 200
    html = resp_clientes.get_data(as_text=True)
    assert "Gestionar reemplazos" in html
    assert f"/admin/reemplazos?cliente_id=" in html
