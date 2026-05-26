# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    DomainOutbox,
    PagoSolicitud,
    Reemplazo,
    RequestIdempotencyKey,
    Solicitud,
    SolicitudCandidata,
    StaffAuditLog,
    StaffUser,
    TareaCliente,
)
from services.payment_ledger import get_payment_summary
from tests.t1_testkit import ensure_sqlite_compat_tables

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright
expect = playwright.expect


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


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


def _seed_paid_cycle_case() -> dict[str, int]:
    token = secrets.token_hex(5)

    cliente = Cliente(
        codigo=f"E2E-PLAN-{token}",
        nombre_completo=f"Cliente E2E Plan {token}",
        email=f"e2e_plan_{token}@test.local",
        telefono=f"809{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente)
    db.session.flush()

    candidata = Candidata(
        nombre_completo=f"Candidata E2E Plan {token}",
        cedula=f"{int(token, 16) % 10**11:011d}",
        numero_telefono="8095550101",
        estado="lista_para_trabajar",
    )
    db.session.add(candidata)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        codigo_solicitud=f"QA-REEMP-SOL-{int(token[:3], 16)}",
        estado="activa",
        tipo_plan="premium",
        abono="0.00",
    )
    db.session.add(solicitud)
    db.session.flush()

    db.session.add(
        PagoSolicitud(
            solicitud_id=int(solicitud.id),
            cliente_id=int(cliente.id),
            monto="5000.00",
            tipo_pago="pago",
            ciclo_numero=1,
            origen="seed",
            origen_id=f"e2e-plan-paid-{token}",
        )
    )
    solicitud.payment_cycle_current = 1
    solicitud.payment_cycle_plan = "premium"
    solicitud.payment_cycle_precio_total = "5000.00"
    solicitud.payment_cycle_abono_requerido = "2500.00"
    solicitud.payment_cycle_estado = "pagado"
    solicitud.estado = "activa"
    db.session.commit()

    return {
        "cliente_id": int(cliente.id),
        "solicitud_id": int(solicitud.id),
        "candidata_id": int(candidata.fila),
    }


@pytest.fixture()
def gestionar_plan_live_env(tmp_path: Path):
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        _ensure_core_tables()
        seeded = _seed_paid_cycle_case()

    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.7)
            if r.status_code in (200, 404):
                break
        except Exception:
            time.sleep(0.08)

    yield {
        "base_url": base_url,
        "cliente_id": seeded["cliente_id"],
        "solicitud_id": seeded["solicitud_id"],
        "artifacts_dir": tmp_path,
    }

    server.shutdown()
    thread.join(timeout=3)


@pytest.mark.e2e
def test_admin_gestionar_plan_muestra_boton_y_crea_nuevo_ciclo_live(gestionar_plan_live_env):
    base_url = gestionar_plan_live_env["base_url"]
    cliente_id = int(gestionar_plan_live_env["cliente_id"])
    solicitud_id = int(gestionar_plan_live_env["solicitud_id"])
    artifacts_dir: Path = gestionar_plan_live_env["artifacts_dir"]

    before_png = artifacts_dir / "gestionar_plan_before_create_cycle.png"
    after_png = artifacts_dir / "gestionar_plan_after_create_cycle.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1800})

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', "Cruz")
        page.fill('input[name="clave"]', "8998")
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        plan_url = f"{base_url}/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan"
        page.goto(plan_url, wait_until="domcontentloaded")

        paid_msg = page.get_by_text("Este ciclo ya está pagado", exact=False)
        create_btn = page.locator('[data-testid="create-new-payment-cycle"]')

        expect(paid_msg).to_be_visible()
        expect(create_btn).to_be_visible()
        expect(create_btn).to_be_enabled()

        page.screenshot(path=str(before_png), full_page=True)

        create_btn.click()

        detail_url = f"{base_url}/admin/clientes/{cliente_id}"
        page.wait_for_url(f"{detail_url}**", timeout=12000)

        with flask_app.app_context():
            solicitud = Solicitud.query.get(solicitud_id)
            assert solicitud is not None
            assert int(solicitud.payment_cycle_current or 0) == 2
            summary = get_payment_summary(solicitud)
            assert str(summary["total_pagado"]) == "0.00"
            assert float(summary["saldo_pendiente"]) > 0

        pay_btn = page.locator(f'[data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"]')
        expect(pay_btn).to_be_visible()
        expect(pay_btn).to_be_enabled()

        pay_btn.click()
        page.wait_for_url(f"**/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago**", timeout=12000)

        page.goto(plan_url, wait_until="domcontentloaded")
        expect(page.get_by_label("Plan del Cliente")).to_be_visible()
        expect(page.locator('option[value="basico"]')).to_be_attached()
        expect(page.locator('option[value="premium"]')).to_be_attached()
        expect(page.locator('option[value="vip"]')).to_be_attached()
        expect(page.get_by_text("Este ciclo ya está pagado", exact=False)).to_have_count(0)

        page.screenshot(path=str(after_png), full_page=True)

        browser.close()
