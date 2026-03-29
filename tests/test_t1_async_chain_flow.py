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
    OutboxConsumerReceipt,
    RequestIdempotencyKey,
    Solicitud,
    StaffAuditLog,
    StaffNotificacion,
    StaffUser,
)
from utils.outbox_relay import (
    _consume_internal_operational_notification,
    _event_envelope,
    relay_pending_once,
)
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


class _RedisOkStub:
    def __init__(self):
        self.calls = []

    def xadd(self, stream, fields):
        self.calls.append((stream, fields))
        return "1-0"


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
            OutboxConsumerReceipt,
            StaffNotificacion,
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


def _seed_solicitud_activa() -> tuple[int, int]:
    token = secrets.token_hex(6)

    cliente = Cliente(
        codigo=f"T1C-{token}",
        nombre_completo=f"Cliente T1 Async {token}",
        email=f"t1c_{token}@example.com",
        telefono=f"849{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        codigo_solicitud=f"SOL-T1C-{token}",
        estado="activa",
        sueldo="15000",
    )
    db.session.add(solicitud)
    db.session.commit()

    return int(cliente.id), int(solicitud.id)


def test_t1c_happy_path_evento_real_relay_consumer_y_live_poll():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        _cliente_id, solicitud_id = _seed_solicitud_activa()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        before_cursor = int(db.session.query(db.func.max(DomainOutbox.id)).scalar() or 0)
        row_version = int(solicitud.row_version or 0)

    resp = client.post(
        f"/admin/solicitudes/{solicitud_id}/poner_espera_pago",
        data={
            "row_version": str(row_version),
            "idempotency_key": f"t1c-espera-{secrets.token_hex(4)}",
            "_async_target": "#solicitudOperativaCoreAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert (resp.get_json() or {}).get("success") is True

    with flask_app.app_context():
        row = (
            DomainOutbox.query
            .filter(DomainOutbox.id > before_cursor)
            .filter_by(event_type="SOLICITUD_ESTADO_CAMBIADO", aggregate_type="Solicitud", aggregate_id=str(solicitud_id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert row is not None
        event_id = str(row.event_id)
        row_id = int(row.id)

        redis_stub = _RedisOkStub()
        stats = relay_pending_once(redis_client=redis_stub, stream_key="sys:test:t1")
        assert stats["picked"] >= 1
        assert stats["published"] >= 1
        assert len(redis_stub.calls) >= 1

        refreshed = DomainOutbox.query.get(row_id)
        assert refreshed is not None
        assert refreshed.published_at is not None
        assert str(refreshed.relay_status or "") == "published"

        notif_count = (
            StaffNotificacion.query
            .filter(StaffNotificacion.payload.isnot(None))
            .filter(db.cast(StaffNotificacion.payload, db.String).contains(event_id))
            .count()
        )
        receipt_count = OutboxConsumerReceipt.query.filter_by(event_id=event_id).count()
        assert notif_count == 1
        assert receipt_count == 1

        _consume_internal_operational_notification(_event_envelope(refreshed), refreshed)
        db.session.commit()

        notif_count_after = (
            StaffNotificacion.query
            .filter(StaffNotificacion.payload.isnot(None))
            .filter(db.cast(StaffNotificacion.payload, db.String).contains(event_id))
            .count()
        )
        receipt_count_after = OutboxConsumerReceipt.query.filter_by(event_id=event_id).count()
        assert notif_count_after >= 1
        assert receipt_count_after == 1

    poll = client.get(
        f"/admin/live/invalidation/poll?after_id={before_cursor}&limit=25&view=solicitud_detail",
        follow_redirects=False,
    )
    assert poll.status_code == 200
    payload = poll.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("mode") in ("relay_published", "degraded_outbox_fallback")

    items = payload.get("items") or []
    target = next((item for item in items if str(item.get("event_id") or "") == event_id), None)
    assert target is not None
    assert (target.get("target") or {}).get("entity_type") == "solicitud"
    assert int((target.get("target") or {}).get("solicitud_id") or 0) == solicitud_id
    assert int(payload.get("next_after_id") or 0) >= row_id


def test_t1c_negativo_allowlist_excluye_evento_fuera_catalogo():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        _ensure_core_tables()
        token = secrets.token_hex(6)
        row = DomainOutbox(
            event_id=f"evt_t1c_excluded_{token}",
            event_type="CANDIDATA_ESTADO_CAMBIADO",
            aggregate_type="Candidata",
            aggregate_id="9991",
            aggregate_version=1,
            occurred_at=utc_now_naive(),
            actor_id="staff:1",
            region="admin",
            payload={"candidata_id": 9991, "from": "lista_para_trabajar", "to": "trabajando"},
            schema_version=1,
        )
        db.session.add(row)
        db.session.commit()
        excluded_id = int(row.id)

        stats = relay_pending_once(redis_client=_RedisOkStub(), stream_key="sys:test:t1")
        assert stats["picked"] == 0
        assert stats["published"] == 0

        refreshed = DomainOutbox.query.get(excluded_id)
        assert refreshed is not None
        assert refreshed.published_at is None
        assert str(refreshed.relay_status or "") == "pending"
