# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
from datetime import timedelta

from app import app as flask_app
from config_app import db
import admin.routes as admin_routes
from models import Candidata, Cliente, DomainOutbox, Reemplazo, Solicitud, StaffAuditLog, StaffUser, SeguimientoCandidataCaso
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [StaffUser, StaffAuditLog, Cliente, Candidata, Solicitud, Reemplazo, SeguimientoCandidataCaso, DomainOutbox],
        reset=True,
    )
    if StaffUser.query.get(1) is None:
        db.session.add(StaffUser(id=1, username="seedstaff", password_hash="x", role="admin"))
        db.session.commit()


def _login_staff(client):
    resp = client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_case(*, closed: bool) -> tuple[int, int]:
    token = secrets.token_hex(4)
    cliente = Cliente(codigo=f"RPL-{token}", nombre_completo=f"Cliente {token}", email=f"rpl_{token}@example.com", telefono="8091112222")
    db.session.add(cliente)
    db.session.flush()

    old = Candidata(nombre_completo=f"Old {token}", cedula=f"{int(token,16)%10**9:09d}", numero_telefono="8093334444", estado="trabajando")
    new = Candidata(nombre_completo=f"New {token}", cedula=f"{(int(token,16)+11)%10**9:09d}", numero_telefono="8093335555", estado="lista_para_trabajar")
    db.session.add_all([old, new])
    db.session.flush()

    solicitud = Solicitud(cliente_id=int(cliente.id), codigo_solicitud=f"SOL-RPL-{token}", estado="reemplazo", candidata_id=int(old.fila), ciudad_sector="Santo Domingo")
    db.session.add(solicitud)
    db.session.flush()

    repl = Reemplazo(
        solicitud_id=int(solicitud.id),
        candidata_old_id=int(old.fila),
        candidata_new_id=(int(new.fila) if closed else None),
        motivo_fallo="No se presentó",
        oportunidad_nueva=not closed,
    )
    repl.iniciar_reemplazo()
    if closed:
        repl.cerrar_reemplazo(int(new.fila))
    db.session.add(repl)
    db.session.flush()

    seg = SeguimientoCandidataCaso(
        public_id=f"CAS-{token}",
        candidata_id=int(old.fila),
        solicitud_id=int(solicitud.id),
        canal_origen="whatsapp",
        estado="en_gestion",
        prioridad="alta",
        created_by_staff_user_id=1,
        owner_staff_user_id=1,
        proxima_accion_tipo="contactar",
        due_at=(admin_routes.utc_now_naive() - timedelta(days=1)),
    )
    db.session.add(seg)
    db.session.commit()
    return int(repl.id), int(solicitud.id)


def test_reemplazos_dashboard_access_and_filters_and_detail():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        repl_active_id, solicitud_id = _seed_case(closed=False)
        _seed_case(closed=True)

    _login_staff(client)

    resp = client.get("/admin/reemplazos", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Panel de reemplazos" in html
    assert "No se presentó" in html

    resp_cliente = client.get("/admin/reemplazos?cliente_id=1", follow_redirects=False)
    assert resp_cliente.status_code == 200

    resp_activos = client.get("/admin/reemplazos?estado=activos", follow_redirects=False)
    assert resp_activos.status_code == 200
    assert f"/admin/reemplazos/{repl_active_id}" in resp_activos.get_data(as_text=True)

    resp_cerrados = client.get("/admin/reemplazos?estado=cerrados", follow_redirects=False)
    assert resp_cerrados.status_code == 200

    resp_detail = client.get(f"/admin/reemplazos/{repl_active_id}", follow_redirects=False)
    assert resp_detail.status_code == 200
    detail_html = resp_detail.get_data(as_text=True)
    assert "Reemplazo #" in detail_html
    assert "Cliente" in detail_html
    assert "Solicitud" in detail_html
    assert "Texto operativo" in detail_html
    assert "Disponible reemplazo" in detail_html

    resp_pub = client.get(f"/admin/reemplazos/{repl_active_id}/publicacion", follow_redirects=False)
    assert resp_pub.status_code == 200
    payload = resp_pub.get_json() or {}
    assert "Disponible reemplazo" in (payload.get("texto") or "")

    with flask_app.app_context():
        sol = Solicitud.query.get(solicitud_id)
        assert sol is not None
        create_resp = client.post(
            "/admin/reemplazos/nuevo",
            data={
                "cliente_id": str(sol.cliente_id),
                "solicitud_id": str(sol.id),
                "candidata_old_id": str(sol.candidata_id or ""),
                "motivo": "Incidencia operativa",
                "prioridad": "alta",
                "confirmar_duplicado": "1",
            },
            follow_redirects=False,
        )
        assert create_resp.status_code in (302, 303)

    with flask_app.app_context():
        repl = Reemplazo.query.get(repl_active_id)
        sol = Solicitud.query.get(solicitud_id)
        seg = SeguimientoCandidataCaso.query.filter_by(solicitud_id=solicitud_id).first()
        assert repl is not None and sol is not None and seg is not None
        assert admin_routes._reemplazo_operativo_estado(reemplazo=repl, solicitud=sol, seguimiento=seg) == "Vencido"
        assert admin_routes._reemplazo_prioridad_derivada(reemplazo=repl, solicitud=sol, seguimiento=seg) in {"alta", "critica"}
