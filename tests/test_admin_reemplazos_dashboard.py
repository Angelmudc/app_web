# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
from datetime import timedelta
from unittest.mock import patch

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


def _seed_case(*, closed: bool, motivo: str = "No se presentó") -> tuple[int, int]:
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
        motivo_fallo=motivo,
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
    assert "Reemplazo activo" in detail_html or "Reemplazo cerrado" in detail_html
    assert "Cliente" in detail_html
    assert "Qué está pasando" in detail_html
    assert "Próximo paso" in detail_html
    assert "Candidata anterior" in detail_html
    assert "Nueva candidata" in detail_html
    assert "Finalizar reemplazo" in detail_html
    assert 'data-action="open-candidata-search"' in detail_html
    assert detail_html.count('data-action="open-candidata-search"') == 1
    assert 'id="nuevaCandidataResults"' in detail_html
    assert 'id="nuevaCandidataQuery"' in detail_html
    assert 'id="reemplazoCsrfToken"' in detail_html
    assert "openPanelAndFocus" in detail_html
    assert "credentials: 'same-origin'" in detail_html
    assert "Busca candidatas compatibles para este servicio." in detail_html
    assert "Próximo paso: Buscar candidatas compatibles para este servicio." not in detail_html
    assert "/admin/reemplazo_nuevo_panel" not in detail_html

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
        assert admin_routes._reemplazo_prioridad_derivada(reemplazo=repl, solicitud=sol, seguimiento=seg) in {"media", "alta", "urgente", "critica"}


def test_reemplazo_prioridad_derivada_por_dias_abiertos():
    sol = object()
    seg = None

    r1 = type("R", (), {"dias_en_reemplazo": 0})()
    r2 = type("R", (), {"dias_en_reemplazo": 1})()
    r3 = type("R", (), {"dias_en_reemplazo": 2})()
    r4 = type("R", (), {"dias_en_reemplazo": 6})()
    r5 = type("R", (), {"dias_en_reemplazo": 7})()
    r6 = type("R", (), {"dias_en_reemplazo": 13})()
    r7 = type("R", (), {"dias_en_reemplazo": 14})()

    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r1, solicitud=sol, seguimiento=seg) == "media"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r2, solicitud=sol, seguimiento=seg) == "media"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r3, solicitud=sol, seguimiento=seg) == "alta"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r4, solicitud=sol, seguimiento=seg) == "alta"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r5, solicitud=sol, seguimiento=seg) == "urgente"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r6, solicitud=sol, seguimiento=seg) == "urgente"
    assert admin_routes._reemplazo_prioridad_derivada(reemplazo=r7, solicitud=sol, seguimiento=seg) == "critica"


def test_reemplazos_dashboard_compacto_trunca_motivo_y_reduce_acciones():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        long_motivo = "No se adaptó al horario de entrada/salida y faltó coordinación con el cliente para el relevo"
        _seed_case(closed=False, motivo=long_motivo)

    _login_staff(client)
    resp = client.get("/admin/reemplazos", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Saliente" not in html
    assert "Entrante" not in html
    assert "<th>Solicitud</th>" not in html
    assert 'title="No se adaptó al horario de entrada/salida y faltó coordinación con el cliente para el relevo"' in html
    assert "📝 No se adaptó al horario de entrada/salida" in html
    assert "..." in html
    assert "📝" in html
    assert "dropdown-item" in html
    assert "⋮" in html


def test_reemplazo_detail_busqueda_y_seleccion_candidata():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        repl_id, _solicitud_id = _seed_case(closed=False)
        repl = Reemplazo.query.get(repl_id)
        assert repl is not None
        cand_old = Candidata.query.get(int(repl.candidata_old_id))
        assert cand_old is not None
        cand_pick = Candidata(
            nombre_completo="Maritza Prueba",
            cedula="11122233344",
            numero_telefono="8298881122",
            codigo="M-900",
            estado="lista_para_trabajar",
        )
        db.session.add(cand_pick)
        db.session.commit()
        cand_pick_id = int(cand_pick.fila)
        old_id = int(cand_old.fila)

    _login_staff(client)

    resp_short = client.get(f"/admin/reemplazos/candidatas-search?reemplazo_id={repl_id}&q=a", follow_redirects=False)
    assert resp_short.status_code == 200
    payload_short = resp_short.get_json() or {}
    assert payload_short.get("results") == []

    resp_search = client.get(f"/admin/reemplazos/candidatas-search?reemplazo_id={repl_id}&q=Maritza", follow_redirects=False)
    assert resp_search.status_code == 200
    payload = resp_search.get_json() or {}
    rows = payload.get("results") or []
    assert any(int(r.get("id") or 0) == cand_pick_id for r in rows)
    assert all(int(r.get("id") or 0) != old_id for r in rows)
    assert any((r.get("telefono_masked") or "") != "8298881122" for r in rows)

    resp_old = client.post(
        f"/admin/reemplazos/{repl_id}/seleccionar-candidata",
        json={"candidata_id": old_id},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp_old.status_code == 409

    resp_pick = client.post(
        f"/admin/reemplazos/{repl_id}/seleccionar-candidata",
        json={"candidata_id": cand_pick_id},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp_pick.status_code == 200
    data_pick = resp_pick.get_json() or {}
    assert data_pick.get("ok") is True

    with flask_app.app_context():
        repl_after = Reemplazo.query.get(repl_id)
        assert repl_after is not None
        assert int(repl_after.candidata_new_id or 0) == cand_pick_id
        outbox = (
            DomainOutbox.query
            .filter_by(aggregate_type="Solicitud", aggregate_id=str(repl_after.solicitud_id), event_type="REEMPLAZO_CANDIDATA_SELECCIONADA")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert outbox is not None
        audit = (
            StaffAuditLog.query
            .filter_by(entity_type="Reemplazo", entity_id=str(repl_id), action_type="REEMPLAZO_SELECCIONAR_CANDIDATA")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert audit is not None

    resp_detail = client.get(f"/admin/reemplazos/{repl_id}", follow_redirects=False)
    assert resp_detail.status_code == 200
    detail_html = resp_detail.get_data(as_text=True)
    assert "Buscar candidata por nombre, código, teléfono o ciudad" in detail_html
    assert "Cambiar candidata" in detail_html
    assert "Maritza Prueba" in detail_html
    assert "Finalizar reemplazo con esta candidata" in detail_html
    assert "/finalizar" not in detail_html
    assert f"/admin/reemplazos/{repl_id}/cerrar" in detail_html

    with patch("admin.routes.cerrar_reemplazo_asignando", return_value=("", 200)) as close_mock:
        resp_finalize = client.post(
            f"/admin/reemplazos/{repl_id}/cerrar",
            data={"resultado_final": "exitoso", "candidata_new_id": str(cand_pick_id)},
            follow_redirects=False,
        )
    assert resp_finalize.status_code == 200
    close_mock.assert_called_once()


def test_reemplazo_seleccionar_candidata_bloquea_cerrado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        repl_id, _solicitud_id = _seed_case(closed=True)
        cand = Candidata(
            nombre_completo="Candidata Cerrado",
            cedula="44455566677",
            numero_telefono="8091010101",
            estado="lista_para_trabajar",
        )
        db.session.add(cand)
        db.session.commit()
        cand_id = int(cand.fila)
    _login_staff(client)
    resp = client.post(
        f"/admin/reemplazos/{repl_id}/seleccionar-candidata",
        json={"candidata_id": cand_id},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 409


def test_reemplazo_seleccionar_candidata_form_data_y_missing_id():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        repl_id, _solicitud_id = _seed_case(closed=False)
        cand = Candidata(
            nombre_completo="Candidata Form",
            cedula="55566677788",
            numero_telefono="8092020202",
            estado="lista_para_trabajar",
        )
        db.session.add(cand)
        db.session.commit()
        cand_id = int(cand.fila)
    _login_staff(client)

    resp_missing = client.post(
        f"/admin/reemplazos/{repl_id}/seleccionar-candidata",
        data={},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp_missing.status_code == 400
    data_missing = resp_missing.get_json() or {}
    assert data_missing.get("ok") is False
    assert data_missing.get("error") == "missing_candidata_id"

    resp_ok = client.post(
        f"/admin/reemplazos/{repl_id}/seleccionar-candidata",
        data={"candidata_id": str(cand_id), "csrf_token": "dummy"},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp_ok.status_code == 200
    data_ok = resp_ok.get_json() or {}
    assert data_ok.get("ok") is True
    assert (data_ok.get("redirect_url") or "").endswith(f"/admin/reemplazos/{repl_id}")


def test_reemplazo_cerrar_exitoso_sin_candidata_devuelve_error_claro():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        repl_id, _solicitud_id = _seed_case(closed=False)
    _login_staff(client)

    resp = client.post(
        f"/admin/reemplazos/{repl_id}/cerrar",
        data={"resultado_final": "exitoso", "_async_target": "#solicitudesAsyncRegion"},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert "Debes indicar la candidata nueva" in (payload.get("message") or "")
