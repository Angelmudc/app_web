# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
from pathlib import Path

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    DomainOutbox,
    PagoSolicitud,
    Reemplazo,
    RequestIdempotencyKey,
    SolicitudCandidata,
    Solicitud,
    TareaCliente,
    StaffAuditLog,
    StaffUser,
)
from services.payment_ledger import (
    calcular_saldo_pendiente,
    calcular_total_pagado,
    ensure_reactivation_cycle,
    get_current_payment_cycle,
    get_payment_summary,
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
            TareaCliente,
            SolicitudCandidata,
            PagoSolicitud,
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


def _seed_payment_fixture(*, tipo_plan: str = "basico", abono: str = "1750.00") -> tuple[int, int, int]:
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
        tipo_plan=tipo_plan,
        abono=abono,
    )
    db.session.add(solicitud)
    db.session.commit()
    return int(cliente.id), int(candidata.fila), int(solicitud.id)


def test_t1_pago_completo_marca_pagada_y_crea_movimiento():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        cliente_id, candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)

    resp_pago = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "auto_completo",
            "monto_pagado": "3500",
            "row_version": str(v1),
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
        assert solicitud_end is not None
        assert solicitud_end.estado == "pagada"
        assert str(solicitud_end.monto_pagado) == "3500.00"
        movimientos = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).all()
        assert len(movimientos) == 1


def test_t1_vip_ciclo_sin_pagos_registrar_completo_queda_pagada_y_cierra_ciclo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        cliente_id, candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="vip", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)
        ciclo_actual = int(solicitud.payment_cycle_current or 1)

    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "auto_completo",
            "row_version": str(v1),
            "idempotency_key": f"t1a-vip-full-{secrets.token_hex(4)}",
            "_async_target": "#registrarPagoAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert (resp.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        assert solicitud_end is not None
        assert solicitud_end.payment_cycle_estado == "pagado"
        assert solicitud_end.estado == "pagada"
        assert solicitud_end.payment_cycle_closed_at is not None
        assert int(solicitud_end.payment_cycle_current or 0) == ciclo_actual
        movs = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).order_by(PagoSolicitud.id.asc()).all()
        assert len(movs) == 1
        assert int(movs[0].ciclo_numero or 0) == ciclo_actual
        assert str(movs[0].monto) == "8000.00"


def test_t1_vip_abono_y_saldo_queda_pagada_sin_abrir_ciclo_nuevo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        cliente_id, candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="vip", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        ciclo_actual = int(solicitud.payment_cycle_current or 1)
        v1 = int(solicitud.row_version or 0)

    resp_abono = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "manual",
            "monto_pagado": "4000",
            "manual_reason": "abono inicial",
            "row_version": str(v1),
            "idempotency_key": f"t1a-vip-part1-{secrets.token_hex(4)}",
            "_async_target": "#registrarPagoAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp_abono.status_code == 200
    assert (resp_abono.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_mid = Solicitud.query.get(solicitud_id)
        assert solicitud_mid is not None
        assert solicitud_mid.payment_cycle_estado == "parcial"
        assert solicitud_mid.estado == "espera_pago"
        v2 = int(solicitud_mid.row_version or 0)

    resp_saldo = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "auto_saldo",
            "row_version": str(v2),
            "idempotency_key": f"t1a-vip-part2-{secrets.token_hex(4)}",
            "_async_target": "#registrarPagoAsyncRegion",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp_saldo.status_code == 200
    assert (resp_saldo.get_json() or {}).get("success") is True

    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        assert solicitud_end is not None
        assert solicitud_end.payment_cycle_estado == "pagado"
        assert solicitud_end.estado == "pagada"
        assert int(solicitud_end.payment_cycle_current or 0) == ciclo_actual
        movs = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).order_by(PagoSolicitud.id.asc()).all()
        assert len(movs) == 2
        assert int(movs[0].ciclo_numero or 0) == ciclo_actual
        assert int(movs[1].ciclo_numero or 0) == ciclo_actual
        assert str(movs[0].monto) == "4000.00"
        assert str(movs[1].monto) == "4000.00"


def test_t1_abono_cuenta_como_pago_parcial_en_ledger():
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="1750.00",
                tipo_pago="abono",
                origen="admin_manual",
                origen_id=f"test-abono:{solicitud_id}",
            )
        )
        db.session.commit()

        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        total_pagado = calcular_total_pagado(solicitud_id)
        saldo = calcular_saldo_pendiente(solicitud)
        assert str(total_pagado) == "1750.00"
        assert str(saldo) == "1750.00"


def test_t1_multiples_pagos_no_sobrescribe():
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")
        db.session.add_all(
            [
                PagoSolicitud(
                    solicitud_id=solicitud_id,
                    cliente_id=cliente_id,
                    monto="1000.00",
                    tipo_pago="abono",
                    origen="admin_manual",
                    origen_id=f"test-abono-1:{solicitud_id}",
                ),
                PagoSolicitud(
                    solicitud_id=solicitud_id,
                    cliente_id=cliente_id,
                    monto="2500.00",
                    tipo_pago="pago",
                    origen="admin_manual",
                    origen_id=f"test-pago-2:{solicitud_id}",
                ),
            ]
        )
        db.session.commit()

        total = calcular_total_pagado(solicitud_id)
        movimientos = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).all()
        assert str(total) == "3500.00"
        assert len(movimientos) == 2


def test_t1_marcar_pagada_desde_copiar_crea_movimiento():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()

    _login_admin(client)

    with flask_app.app_context():
        _cliente_id, candidata_id, solicitud_id = _seed_payment_fixture()

    resp = client.post(
        f"/admin/solicitudes/{solicitud_id}/marcar_pagada_desde_copiar",
        data={
            "candidata_id": str(candidata_id),
            "monto_pagado": "3500",
            "idempotency_key": f"t1a-mark-{secrets.token_hex(4)}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200

    with flask_app.app_context():
        movimientos = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).all()
        assert len(movimientos) == 1
        assert (movimientos[0].origen or "") == "marcar_pagada_desde_copiar_auto"


def test_t1_ui_detalle_muestra_resumen_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")

    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Precio del plan" in html
    assert "Total pagado" in html
    assert "Saldo pendiente" in html


def test_t1_registrar_abono_automatico_crea_movimiento_abono():
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
        v1 = int(solicitud.row_version or 0)
    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "auto_abono",
            "row_version": str(v1),
            "idempotency_key": f"t1a-abono-{secrets.token_hex(4)}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with flask_app.app_context():
        mov = PagoSolicitud.query.filter_by(solicitud_id=solicitud_id).order_by(PagoSolicitud.id.asc()).all()
        assert len(mov) == 1
        assert (mov[0].tipo_pago or "").lower() == "abono"


def test_t1_pago_manual_exige_motivo():
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
        v1 = int(solicitud.row_version or 0)
    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "manual",
            "monto_pagado": "1000",
            "row_version": str(v1),
            "idempotency_key": f"t1a-manual-{secrets.token_hex(4)}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert "motivo" in (payload.get("message") or "").lower()


def test_t1_ui_registrar_pago_no_muestra_monto_manual_como_principal():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert "Pago sugerido automático" in html
    assert "Registrar abono" in html
    assert "Registrar pago completo RD$ 3,500.00" in html
    assert 'id="manual-payment-fields"' in html
    assert 'id="manual-payment-fields" class="collapse mt-2"' in html
    assert "placeholder=\"Ej. 10000\"" not in html


def test_t1_ui_registrar_pago_despues_de_abono_muestra_solo_saldo_como_principal():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture()
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="1750.00",
                tipo_pago="abono",
                origen="admin_manual",
                origen_id=f"seed-abono:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert "Total pagado: <strong>RD$ 1,750.00</strong>" in html
    assert "Saldo pendiente: <strong>RD$ 1,750.00</strong>" in html
    assert "Registrar saldo RD$ 1,750.00" in html
    assert "Registrar pago completo RD$ 3,500.00" not in html
    assert "Pago manual" in html


def test_t1_ui_premium_con_abono_en_ledger_muestra_solo_saldo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="2500.00")
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="2500.00",
                tipo_pago="abono",
                origen="admin_manual",
                origen_id=f"seed-premium-abono:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert "Total pagado: <strong>RD$ 2,500.00</strong>" in html
    assert "Saldo pendiente: <strong>RD$ 2,500.00</strong>" in html
    assert "Registrar saldo RD$ 2,500.00" in html
    assert "Registrar abono RD$ 2,500.00" not in html
    assert "Registrar pago completo RD$ 5,000.00" not in html


def test_t1_ui_premium_sin_movimientos_muestra_abono_y_pago_completo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="0.00")
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert "Registrar abono RD$ 2,500.00" in html
    assert "Registrar pago completo RD$ 5,000.00" in html


def test_t1_ui_premium_pagada_completa_no_muestra_botones_principales():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="2500.00")
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                origen="admin_manual",
                origen_id=f"seed-premium-paid:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert "Esta solicitud ya está pagada." in html
    assert "Registrar abono RD$ 2,500.00" not in html
    assert "Registrar pago completo RD$ 5,000.00" not in html
    assert "Registrar saldo RD$ 2,500.00" not in html


def test_t1_payment_summary_usa_abono_legacy_si_no_hay_ledger():
    with flask_app.app_context():
        _ensure_core_tables()
        _cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="2500.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        summary = get_payment_summary(solicitud)
        assert str(summary["total_pagado"]) == "0.00"
        assert str(summary["saldo_pendiente"]) == "5000.00"
        assert summary["legacy_abono_fallback"] is False


def test_t1_reactivar_abre_ciclo_nuevo_y_permite_nuevo_abono():
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                ciclo_numero=1,
                origen="test",
                origen_id=f"cycle1-paid:{solicitud_id}",
            )
        )
        solicitud.estado = "pagada"
        db.session.commit()

        opened = ensure_reactivation_cycle(solicitud, motivo="test_reactivacion")
        db.session.commit()
        assert opened is True
        cycle = get_current_payment_cycle(solicitud)
        summary = get_payment_summary(solicitud)
        assert int(cycle["numero_ciclo"]) == 2
        assert str(summary["total_pagado"]) == "0.00"
        assert str(summary["saldo_pendiente"]) == "5000.00"


def test_t1_historico_solicitud_suma_todos_los_ciclos():
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="basico", abono="0.00")
        db.session.add_all(
            [
                PagoSolicitud(solicitud_id=solicitud_id, cliente_id=cliente_id, monto="1750.00", tipo_pago="abono", ciclo_numero=1, origen_id=f"h1:{solicitud_id}"),
                PagoSolicitud(solicitud_id=solicitud_id, cliente_id=cliente_id, monto="1750.00", tipo_pago="pago", ciclo_numero=1, origen_id=f"h2:{solicitud_id}"),
                PagoSolicitud(solicitud_id=solicitud_id, cliente_id=cliente_id, monto="1000.00", tipo_pago="abono", ciclo_numero=2, origen_id=f"h3:{solicitud_id}"),
            ]
        )
        db.session.commit()
        assert str(calcular_total_pagado(solicitud_id)) == "4500.00"


def test_t1_gestionar_plan_reactivada_abre_ciclo_y_permite_bajar_de_vip_a_basico():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="vip", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="8000.00",
                tipo_pago="pago",
                ciclo_numero=1,
                origen="seed",
                origen_id=f"vip-paid:{solicitud_id}",
            )
        )
        solicitud.payment_cycle_current = 1
        solicitud.payment_cycle_plan = "vip"
        solicitud.payment_cycle_precio_total = "8000.00"
        solicitud.payment_cycle_abono_requerido = "4000.00"
        solicitud.payment_cycle_estado = "pagado"
        solicitud.estado = "activa"
        db.session.commit()
    _login_admin(client)
    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan",
        data={"tipo_plan": "basico", "abono": "1750"},
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert "Este ciclo ya tiene pagos registrados" not in (payload.get("message") or "")
    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        assert solicitud_end is not None
        assert int(solicitud_end.payment_cycle_current or 0) == 2
        assert (solicitud_end.payment_cycle_plan or "") == "basico"
        assert str(solicitud_end.payment_cycle_precio_total) == "3500.00"
        assert str(solicitud_end.payment_cycle_abono_requerido) == "1750.00"


def test_t1_gestionar_plan_ciclo_actual_parcial_si_bloquea_sin_override():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.payment_cycle_current = 2
        solicitud.payment_cycle_plan = "premium"
        solicitud.payment_cycle_precio_total = "5000.00"
        solicitud.payment_cycle_abono_requerido = "2500.00"
        solicitud.payment_cycle_estado = "parcial"
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="1000.00",
                tipo_pago="abono",
                ciclo_numero=2,
                origen="seed",
                origen_id=f"partial-cycle2:{solicitud_id}",
            )
        )
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                ciclo_numero=1,
                origen="seed",
                origen_id=f"old-cycle1:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan",
        data={"tipo_plan": "basico"},
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 409
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert "Este ciclo ya tiene pagos registrados" in (payload.get("message") or "")


def test_t1_gestionar_plan_con_historico_pero_ciclo_actual_en_cero_permite_cambiar_libre():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="9999.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.payment_cycle_current = 2
        solicitud.payment_cycle_plan = "premium"
        solicitud.payment_cycle_precio_total = "5000.00"
        solicitud.payment_cycle_abono_requerido = "2500.00"
        solicitud.payment_cycle_estado = "pendiente"
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                ciclo_numero=1,
                origen="seed",
                origen_id=f"old-paid-only:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan",
        data={"tipo_plan": "vip"},
        headers=_async_headers(),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    with flask_app.app_context():
        solicitud_end = Solicitud.query.get(solicitud_id)
        assert solicitud_end is not None
        assert int(solicitud_end.payment_cycle_current or 0) == 2
        assert (solicitud_end.payment_cycle_plan or "") == "vip"
        assert str(solicitud_end.payment_cycle_precio_total) == "8000.00"
        assert str(solicitud_end.payment_cycle_abono_requerido) == "4000.00"


def test_t1_ui_pago_manual_muestra_bloque_manual_y_al_volver_auto_lo_oculta():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, candidata_id, solicitud_id = _seed_payment_fixture(abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        v1 = int(solicitud.row_version or 0)
    _login_admin(client)

    resp_manual = client.post(
        f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago",
        data={
            "candidata_id": str(candidata_id),
            "payment_mode": "manual",
            "monto_pagado": "1000",
            "row_version": str(v1),
            "idempotency_key": f"t1a-manual-ux-{secrets.token_hex(4)}",
        },
        headers=_async_headers(),
        follow_redirects=False,
    )
    payload_manual = resp_manual.get_json() or {}
    assert payload_manual.get("success") is False
    html_manual = payload_manual.get("replace_html") or ""
    assert 'id="mode_manual" value="manual" checked' in html_manual
    assert 'id="manual-payment-fields" class="collapse show mt-2"' in html_manual

    resp_auto = client.get(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago", follow_redirects=False)
    html_auto = resp_auto.get_data(as_text=True)
    assert 'id="manual-payment-fields" class="collapse mt-2"' in html_auto


def test_t1_js_admin_async_controla_toggle_de_campos_manual_pago():
    js_txt = Path("static/js/core/admin_async.js").read_text(encoding="utf-8")
    assert "function syncRegistrarPagoManualFields(root)" in js_txt
    assert "panel.classList.toggle(\"show\", isManual);" in js_txt
    assert "document.addEventListener(\"change\", onRegistrarPagoModeChange, true);" in js_txt
    assert "syncRegistrarPagoManualFields(container || document);" in js_txt


def test_t1_cliente_detail_espera_pago_con_saldo_pendiente_habilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="basico", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.estado = "espera_pago"
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"' in html


def test_t1_cliente_detail_espera_pago_ciclo_parcial_habilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="basico", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.estado = "espera_pago"
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="1000.00",
                tipo_pago="abono",
                ciclo_numero=int(solicitud.payment_cycle_current or 1),
                origen="seed",
                origen_id=f"partial-espera:{solicitud_id}",
            )
        )
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"' in html


def test_t1_cliente_detail_pagada_con_ciclo_pagado_deshabilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                ciclo_numero=int(solicitud.payment_cycle_current or 1),
                origen="seed",
                origen_id=f"paid-cycle:{solicitud_id}",
            )
        )
        solicitud.estado = "pagada"
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-disabled-{solicitud_id}"' in html


def test_t1_cliente_detail_reemplazo_con_saldo_pendiente_habilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="vip", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.estado = "reemplazo"
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"' in html


def test_t1_cliente_detail_reemplazo_con_ciclo_pagado_deshabilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="vip", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="8000.00",
                tipo_pago="pago",
                ciclo_numero=int(solicitud.payment_cycle_current or 1),
                origen="seed",
                origen_id=f"repl-paid-cycle:{solicitud_id}",
            )
        )
        solicitud.estado = "reemplazo"
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-disabled-{solicitud_id}"' in html


def test_t1_cliente_detail_reactivada_con_ciclo_nuevo_pendiente_habilita_boton_pago():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_core_tables()
        cliente_id, _candidata_id, solicitud_id = _seed_payment_fixture(tipo_plan="premium", abono="0.00")
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        db.session.add(
            PagoSolicitud(
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto="5000.00",
                tipo_pago="pago",
                ciclo_numero=1,
                origen="seed",
                origen_id=f"reactivation-paid-cycle1:{solicitud_id}",
            )
        )
        solicitud.estado = "pagada"
        db.session.commit()
        opened = ensure_reactivation_cycle(solicitud, motivo="test_cliente_detail_reactivada")
        assert opened is True
        solicitud.estado = "activa"
        db.session.commit()
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert f'data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"' in html
