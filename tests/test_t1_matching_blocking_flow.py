# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    ClienteNotificacion,
    DomainOutbox,
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
            ClienteNotificacion,
            Candidata,
            Solicitud,
            SolicitudCandidata,
            RequestIdempotencyKey,
            DomainOutbox,
        ],
        reset=True,
    )
    # Matching persiste breakdown_snapshot como dict; en sqlite necesitamos JSON real.
    if str(db.engine.dialect.name).strip().lower() == "sqlite":
        SolicitudCandidata.__table__.c.breakdown_snapshot.type = db.JSON()
        SolicitudCandidata.__table__.drop(bind=db.engine, checkfirst=True)
        SolicitudCandidata.__table__.create(bind=db.engine, checkfirst=True)
        ClienteNotificacion.__table__.c.payload.type = db.JSON()
        ClienteNotificacion.__table__.drop(bind=db.engine, checkfirst=True)
        ClienteNotificacion.__table__.create(bind=db.engine, checkfirst=True)


def _async_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def _login_admin(client):
    resp = client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_matching_blocking_fixture() -> tuple[int, int, int]:
    token = secrets.token_hex(6)

    cliente_a = Cliente(
        codigo=f"T1C2-A-{token}",
        nombre_completo=f"Cliente A T1-C2 {token}",
        email=f"t1c2a_{token}@example.com",
        telefono=f"849{int(token[:6], 16) % 10**7:07d}",
    )
    cliente_b = Cliente(
        codigo=f"T1C2-B-{token}",
        nombre_completo=f"Cliente B T1-C2 {token}",
        email=f"t1c2b_{token}@example.com",
        telefono=f"829{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente_a)
    db.session.add(cliente_b)
    db.session.flush()

    candidata = Candidata(
        codigo=f"C-T1C2-{token[:8]}",
        nombre_completo=f"Candidata T1-C2 {token}",
        cedula=f"{int(token[:10], 16) % 10**11:011d}",
        numero_telefono="8091234567",
        estado="lista_para_trabajar",
        entrevista="Entrevista completada",
        referencias_laborales_texto="Referencia laboral validada",
        referencias_familiares_texto="Referencia familiar validada",
        depuracion=b"ok",
        perfil=b"ok",
        cedula1=b"ok",
        cedula2=b"ok",
    )
    db.session.add(candidata)
    db.session.flush()

    solicitud_a = Solicitud(
        cliente_id=int(cliente_a.id),
        codigo_solicitud=f"SOL-T1C2-A-{token}",
        estado="activa",
        sueldo="15000",
    )
    solicitud_b = Solicitud(
        cliente_id=int(cliente_b.id),
        codigo_solicitud=f"SOL-T1C2-B-{token}",
        estado="activa",
        sueldo="16000",
    )
    db.session.add(solicitud_a)
    db.session.add(solicitud_b)
    db.session.commit()
    return int(solicitud_a.id), int(solicitud_b.id), int(candidata.fila)


def _send_candidate_ui(client, *, solicitud_id: int, candidata_id: int, idempotency_key: str):
    return client.post(
        f"/admin/matching/solicitudes/{solicitud_id}/enviar/ui",
        data={
            "candidata_ids": [str(candidata_id)],
            "idempotency_key": idempotency_key,
        },
        headers=_async_headers(),
        follow_redirects=False,
    )


def _cancel_from_copy(client, *, solicitud_id: int, motivo: str, row_version: int, idempotency_key: str):
    return client.post(
        f"/admin/solicitudes/{solicitud_id}/cancelar_desde_copiar",
        data={
            "motivo": motivo,
            "row_version": str(row_version),
            "idempotency_key": idempotency_key,
            "next": "/admin/solicitudes/copiar?page=1",
        },
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        follow_redirects=False,
    )


def test_t1c2_happy_path_matching_bloqueo_liberacion_y_reenvio():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        solicitud_a_id, solicitud_b_id, candidata_id = _seed_matching_blocking_fixture()

    send_a = _send_candidate_ui(
        client,
        solicitud_id=solicitud_a_id,
        candidata_id=candidata_id,
        idempotency_key=f"t1c2-send-a-{secrets.token_hex(4)}",
    )
    assert send_a.status_code == 200
    payload_a = send_a.get_json() or {}
    assert payload_a.get("success") is True

    send_b_blocked = _send_candidate_ui(
        client,
        solicitud_id=solicitud_b_id,
        candidata_id=candidata_id,
        idempotency_key=f"t1c2-send-b-blocked-{secrets.token_hex(4)}",
    )
    assert send_b_blocked.status_code == 409
    payload_b_blocked = send_b_blocked.get_json() or {}
    assert payload_b_blocked.get("success") is False
    assert payload_b_blocked.get("error_code") == "blocked_other_client"

    with flask_app.app_context():
        solicitud_a = Solicitud.query.get(solicitud_a_id)
        assert solicitud_a is not None
        row_version_a = int(solicitud_a.row_version or 0)

    cancel_a = _cancel_from_copy(
        client,
        solicitud_id=solicitud_a_id,
        motivo="Liberacion T1-C2 happy path",
        row_version=row_version_a,
        idempotency_key=f"t1c2-cancel-a-{secrets.token_hex(4)}",
    )
    assert cancel_a.status_code == 200
    cancel_payload = cancel_a.get_json() or {}
    assert cancel_payload.get("ok") is True

    with flask_app.app_context():
        solicitud_a_end = Solicitud.query.get(solicitud_a_id)
        row_a = (
            SolicitudCandidata.query
            .filter_by(solicitud_id=solicitud_a_id, candidata_id=candidata_id)
            .first()
        )
        outbox_release = (
            DomainOutbox.query
            .filter_by(
                aggregate_type="Solicitud",
                aggregate_id=str(solicitud_a_id),
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
            )
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert solicitud_a_end is not None
        assert solicitud_a_end.estado == "cancelada"
        assert row_a is not None
        assert str(row_a.status or "").strip().lower() == "liberada"
        assert outbox_release is not None
        payload = dict(outbox_release.payload or {})
        assert int(payload.get("solicitud_id") or 0) == solicitud_a_id
        assert int(payload.get("count") or 0) >= 1
        assert candidata_id in [int(x) for x in (payload.get("candidata_ids") or [])]

    send_b_after_release = _send_candidate_ui(
        client,
        solicitud_id=solicitud_b_id,
        candidata_id=candidata_id,
        idempotency_key=f"t1c2-send-b-after-release-{secrets.token_hex(4)}",
    )
    assert send_b_after_release.status_code == 200
    payload_b_after = send_b_after_release.get_json() or {}
    assert payload_b_after.get("success") is True

    with flask_app.app_context():
        row_b = (
            SolicitudCandidata.query
            .filter_by(solicitud_id=solicitud_b_id, candidata_id=candidata_id)
            .first()
        )
        assert row_b is not None
        assert str(row_b.status or "").strip().lower() == "enviada"


def test_t1c2_negativo_conflicto_row_version_no_libera_y_sigue_bloqueada():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        solicitud_a_id, solicitud_b_id, candidata_id = _seed_matching_blocking_fixture()

    send_a = _send_candidate_ui(
        client,
        solicitud_id=solicitud_a_id,
        candidata_id=candidata_id,
        idempotency_key=f"t1c2-neg-send-a-{secrets.token_hex(4)}",
    )
    assert send_a.status_code == 200
    assert (send_a.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_a = Solicitud.query.get(solicitud_a_id)
        assert solicitud_a is not None
        current_version = int(solicitud_a.row_version or 0)
        before_release_events = (
            DomainOutbox.query
            .filter_by(
                aggregate_type="Solicitud",
                aggregate_id=str(solicitud_a_id),
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
            )
            .count()
        )

    stale_cancel = _cancel_from_copy(
        client,
        solicitud_id=solicitud_a_id,
        motivo="Liberacion T1-C2 stale conflict",
        row_version=max(0, current_version - 1),
        idempotency_key=f"t1c2-neg-cancel-stale-{secrets.token_hex(4)}",
    )
    assert stale_cancel.status_code == 409
    stale_payload = stale_cancel.get_json() or {}
    assert stale_payload.get("ok") is False
    assert stale_payload.get("error_code") == "conflict"

    with flask_app.app_context():
        solicitud_a_end = Solicitud.query.get(solicitud_a_id)
        row_a = (
            SolicitudCandidata.query
            .filter_by(solicitud_id=solicitud_a_id, candidata_id=candidata_id)
            .first()
        )
        after_release_events = (
            DomainOutbox.query
            .filter_by(
                aggregate_type="Solicitud",
                aggregate_id=str(solicitud_a_id),
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
            )
            .count()
        )
        assert solicitud_a_end is not None
        assert solicitud_a_end.estado == "activa"
        assert row_a is not None
        assert str(row_a.status or "").strip().lower() == "enviada"
        assert after_release_events == before_release_events

    send_b_still_blocked = _send_candidate_ui(
        client,
        solicitud_id=solicitud_b_id,
        candidata_id=candidata_id,
        idempotency_key=f"t1c2-neg-send-b-blocked-{secrets.token_hex(4)}",
    )
    assert send_b_still_blocked.status_code == 409
    blocked_payload = send_b_still_blocked.get_json() or {}
    assert blocked_payload.get("success") is False
    assert blocked_payload.get("error_code") == "blocked_other_client"
