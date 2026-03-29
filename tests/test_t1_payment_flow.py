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
    RequestIdempotencyKey,
    Solicitud,
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


def _seed_payment_fixture() -> tuple[int, int, int]:
    token = secrets.token_hex(6)

    cliente = Cliente(
        codigo=f"T1P-{token}",
        nombre_completo=f"Cliente T1 Pago {token}",
        email=f"t1p_{token}@example.com",
        telefono=f"809{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente)
    db.session.flush()

    candidata = Candidata(
        nombre_completo=f"Candidata T1 Pago {token}",
        cedula=f"{int(token[:10], 16) % 10**11:011d}",
        numero_telefono="8090001111",
        estado="lista_para_trabajar",
    )
    db.session.add(candidata)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        codigo_solicitud=f"SOL-T1P-{token}",
        estado="activa",
        sueldo="12000",
    )
    db.session.add(solicitud)
    db.session.commit()
    return int(cliente.id), int(candidata.fila), int(solicitud.id)


def test_t1a_happy_path_activa_espera_pago_y_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        cliente_id, candidata_id, solicitud_id = _seed_payment_fixture()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)

    resp_espera = client.post(
        f"/admin/solicitudes/{solicitud_id}/poner_espera_pago",
        data={
            "row_version": str(v1),
            "idempotency_key": f"t1a-espera-{secrets.token_hex(4)}",
            "_async_target": "#solicitudOperativaCoreAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp_espera.status_code == 200
    assert (resp_espera.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_mid = Solicitud.query.get(solicitud_id)
        assert solicitud_mid is not None
        assert solicitud_mid.estado == "espera_pago"
        v2 = int(solicitud_mid.row_version or 0)

    resp_pago = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "monto_pagado": "3000",
            "row_version": str(v2),
            "idempotency_key": f"t1a-pago-{secrets.token_hex(4)}",
            "_async_target": "#registrarPagoAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp_pago.status_code == 200
    assert (resp_pago.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        candidata_end = Candidata.query.get(candidata_id)
        assert solicitud_end is not None
        assert candidata_end is not None

        assert solicitud_end.estado == "pagada"
        assert int(solicitud_end.candidata_id or 0) == candidata_id
        assert (candidata_end.estado or "").strip().lower() == "trabajando"

        pago_outbox = (
            DomainOutbox.query
            .filter_by(aggregate_type="Solicitud", aggregate_id=str(solicitud_id), event_type="SOLICITUD_PAGO_REGISTRADO")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        espera_outbox = (
            DomainOutbox.query
            .filter_by(aggregate_type="Solicitud", aggregate_id=str(solicitud_id), event_type="SOLICITUD_ESTADO_CAMBIADO")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert pago_outbox is not None
        assert espera_outbox is not None

        idem_pago = (
            RequestIdempotencyKey.query
            .filter_by(scope="solicitud_pago", entity_type="Solicitud", entity_id=str(solicitud_id))
            .order_by(RequestIdempotencyKey.id.desc())
            .first()
        )
        assert idem_pago is not None
        assert int(idem_pago.response_status or 0) == 200


def test_t1a_negativo_conflicto_row_version_en_espera_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        _cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        current_version = int(solicitud.row_version or 0)
        before_outbox = DomainOutbox.query.filter_by(aggregate_id=str(solicitud_id)).count()

    resp = client.post(
        f"/admin/solicitudes/{solicitud_id}/poner_espera_pago",
        data={
            "row_version": str(max(0, current_version - 1)),
            "idempotency_key": f"t1a-conflict-{secrets.token_hex(4)}",
            "_async_target": "#solicitudOperativaCoreAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )

    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("error_code") == "conflict"

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        assert solicitud_end is not None
        assert solicitud_end.estado == "activa"
        after_outbox = DomainOutbox.query.filter_by(aggregate_id=str(solicitud_id)).count()
        assert after_outbox == before_outbox
