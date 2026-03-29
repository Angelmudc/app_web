# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    DomainOutbox,
    Reemplazo,
    RequestIdempotencyKey,
    Solicitud,
    SolicitudCandidata,
    StaffAuditLog,
    StaffUser,
)
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_core_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            StaffUser,
            StaffAuditLog,
            Cliente,
            Candidata,
            Solicitud,
            Reemplazo,
            SolicitudCandidata,
            RequestIdempotencyKey,
            DomainOutbox,
        ],
        reset=True,
    )


def _async_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def _login_admin(client):
    resp = client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_reemplazo_fixture() -> tuple[int, int, int, int]:
    token = secrets.token_hex(6)

    cliente = Cliente(
        codigo=f"T1R-{token}",
        nombre_completo=f"Cliente T1 Reemplazo {token}",
        email=f"t1r_{token}@example.com",
        telefono=f"829{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente)
    db.session.flush()

    cand_old = Candidata(
        nombre_completo=f"Candidata Old {token}",
        cedula=f"{int(token[:10], 16) % 10**11:011d}",
        numero_telefono="8290002222",
        estado="trabajando",
    )
    db.session.add(cand_old)
    db.session.flush()

    cand_new = Candidata(
        nombre_completo=f"Candidata New {token}",
        cedula=f"{(int(token[:10], 16) + 111111) % 10**11:011d}",
        numero_telefono="8290003333",
        estado="lista_para_trabajar",
    )
    db.session.add(cand_new)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        codigo_solicitud=f"SOL-T1R-{token}",
        estado="activa",
        sueldo="14000",
        candidata_id=int(cand_old.fila),
    )
    db.session.add(solicitud)
    db.session.commit()
    return int(cliente.id), int(solicitud.id), int(cand_old.fila), int(cand_new.fila)


def test_t1b_happy_path_reemplazo_abre_y_cierra_asignando():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        _cliente_id, solicitud_id, cand_old_id, cand_new_id = _seed_reemplazo_fixture()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)

    resp_open = client.post(
        f"/admin/solicitudes/{solicitud_id}/reemplazos/nuevo",
        data={
            "motivo_fallo": "No se presentó al trabajo",
            "candidata_old_id": str(cand_old_id),
            "candidata_old_name": "old",
            "row_version": str(v1),
            "idempotency_key": f"t1b-open-{secrets.token_hex(4)}",
        },
        follow_redirects=False,
    )
    assert resp_open.status_code in (302, 303)

    with flask_app.app_context():
        solicitud_mid = Solicitud.query.get(solicitud_id)
        repl = (
            Reemplazo.query
            .filter_by(solicitud_id=solicitud_id)
            .order_by(Reemplazo.id.desc())
            .first()
        )
        assert solicitud_mid is not None
        assert repl is not None
        assert solicitud_mid.estado == "reemplazo"
        assert repl.fecha_inicio_reemplazo is not None
        v2 = int(solicitud_mid.row_version or 0)
        repl_id = int(repl.id)

    resp_close = client.post(
        f"/admin/solicitudes/{solicitud_id}/reemplazos/{repl_id}/cerrar_asignando",
        data={
            "candidata_new_id": str(cand_new_id),
            "row_version": str(v2),
            "idempotency_key": f"t1b-close-{secrets.token_hex(4)}",
            "_async_target": f"#solicitudReemplazoActionsAsyncRegion-{solicitud_id}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp_close.status_code == 200
    assert (resp_close.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        repl_end = Reemplazo.query.get(repl_id)
        cand_old_end = Candidata.query.get(cand_old_id)
        cand_new_end = Candidata.query.get(cand_new_id)

        assert solicitud_end is not None
        assert repl_end is not None
        assert cand_old_end is not None
        assert cand_new_end is not None

        assert solicitud_end.estado == "activa"
        assert int(solicitud_end.candidata_id or 0) == cand_new_id
        assert repl_end.fecha_fin_reemplazo is not None
        assert int(repl_end.candidata_new_id or 0) == cand_new_id

        assert int(solicitud_end.candidata_id or 0) != cand_old_id
        assert (cand_new_end.estado or "").strip().lower() == "trabajando"

        open_event = (
            DomainOutbox.query
            .filter_by(aggregate_type="Solicitud", aggregate_id=str(solicitud_id), event_type="REEMPLAZO_ABIERTO")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        close_event = (
            DomainOutbox.query
            .filter_by(aggregate_type="Solicitud", aggregate_id=str(solicitud_id), event_type="REEMPLAZO_CERRADO_ASIGNANDO")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert open_event is not None
        assert close_event is not None

        idem_close = (
            RequestIdempotencyKey.query
            .filter_by(scope="admin_reemplazo_close_assign", entity_type="Solicitud", entity_id=str(solicitud_id))
            .order_by(RequestIdempotencyKey.id.desc())
            .first()
        )
        assert idem_close is not None
        assert int(idem_close.response_status or 0) == 200


def test_t1b_negativo_conflicto_row_version_en_cierre_reemplazo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        _cliente_id, solicitud_id, cand_old_id, cand_new_id = _seed_reemplazo_fixture()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)

    open_resp = client.post(
        f"/admin/solicitudes/{solicitud_id}/reemplazos/nuevo",
        data={
            "motivo_fallo": "Incumplimiento de horario",
            "candidata_old_id": str(cand_old_id),
            "row_version": str(v1),
            "idempotency_key": f"t1b-open-neg-{secrets.token_hex(4)}",
        },
        follow_redirects=False,
    )
    assert open_resp.status_code in (302, 303)

    with flask_app.app_context():
        solicitud_mid = Solicitud.query.get(solicitud_id)
        repl = Reemplazo.query.filter_by(solicitud_id=solicitud_id).order_by(Reemplazo.id.desc()).first()
        assert solicitud_mid is not None
        assert repl is not None
        current_version = int(solicitud_mid.row_version or 0)

    close_resp = client.post(
        f"/admin/solicitudes/{solicitud_id}/reemplazos/{int(repl.id)}/cerrar_asignando",
        data={
            "candidata_new_id": str(cand_new_id),
            "row_version": str(max(0, current_version - 1)),
            "idempotency_key": f"t1b-close-neg-{secrets.token_hex(4)}",
            "_async_target": f"#solicitudReemplazoActionsAsyncRegion-{solicitud_id}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert close_resp.status_code == 409
    payload = close_resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("error_code") == "conflict"

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        repl_end = Reemplazo.query.get(int(repl.id))
        assert solicitud_end is not None
        assert repl_end is not None
        assert solicitud_end.estado == "reemplazo"
        assert repl_end.fecha_fin_reemplazo is None
