# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import socket
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server
from werkzeug.security import generate_password_hash

from app import app as flask_app
from config_app import db
from models import Cliente, Candidata, Solicitud, StaffUser
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


def _ensure_staff_user(*, username: str, email: str, role: str, password: str) -> None:
    user = StaffUser.query.filter_by(username=username).first()
    if user is None:
        user = StaffUser(
            username=username,
            email=email,
            role=role,
            is_active=True,
            mfa_enabled=False,
        )
        db.session.add(user)
    else:
        user.email = email
        user.role = role
        user.is_active = True
        user.mfa_enabled = False
    user.set_password(password)


@pytest.fixture()
def registrar_pago_modal_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        mapped_models = []
        for mapper in db.Model.registry.mappers:
            cls = getattr(mapper, "class_", None)
            if cls is None or not hasattr(cls, "__table__"):
                continue
            mapped_models.append(cls)
        ensure_sqlite_compat_tables(mapped_models, reset=True)

        _ensure_staff_user(
            username="owner_e2e_pago_modal",
            email="owner_e2e_pago_modal@test.local",
            role="owner",
            password="Owner#12345",
        )

        cliente = Cliente(
            codigo="CL-E2E-PAGO-MODAL-001",
            nombre_completo="Cliente E2E Pago Modal",
            email="cliente_e2e_pago_modal@test.local",
            telefono="8095551203",
            username="cliente_e2e_pago_modal",
            password_hash=generate_password_hash("Cliente#12345", method="pbkdf2:sha256"),
            is_active=True,
            role="cliente",
            total_solicitudes=0,
        )
        db.session.add(cliente)
        db.session.flush()

        candidata = Candidata(
            nombre_completo="Candidata E2E Pago Modal",
            cedula="00112233445",
            numero_telefono="8095552203",
            estado="lista_para_trabajar",
        )
        db.session.add(candidata)
        db.session.flush()

        solicitud = Solicitud(
            cliente_id=int(cliente.id),
            codigo_solicitud="SOL-E2E-PAGO-001",
            estado="espera_pago",
            tipo_plan="basico",
            abono="0.00",
        )
        db.session.add(solicitud)
        db.session.commit()

        cliente_id = int(cliente.id)
        solicitud_id = int(solicitud.id)
        candidata_id = int(candidata.fila)

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
        "owner_user": "owner_e2e_pago_modal",
        "owner_pass": "Owner#12345",
        "cliente_id": cliente_id,
        "solicitud_id": solicitud_id,
        "candidata_id": candidata_id,
    }

    server.shutdown()
    thread.join(timeout=3)


@pytest.mark.e2e
def test_registrar_pago_modal_submit_hits_pago_endpoint_and_refreshes_client_detail(registrar_pago_modal_env):
    base_url = registrar_pago_modal_env["base_url"]
    owner_user = registrar_pago_modal_env["owner_user"]
    owner_pass = registrar_pago_modal_env["owner_pass"]
    cliente_id = int(registrar_pago_modal_env["cliente_id"])
    solicitud_id = int(registrar_pago_modal_env["solicitud_id"])
    candidata_id = int(registrar_pago_modal_env["candidata_id"])

    console_messages: list[dict[str, str]] = []
    failed_requests: list[dict[str, str]] = []
    all_requests: list[dict[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1600})
        page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
        page.on("requestfailed", lambda req: failed_requests.append({"method": req.method, "url": req.url, "failure": req.failure}))
        page.on("request", lambda req: all_requests.append({"method": req.method, "url": req.url}))

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', owner_user)
        page.fill('input[name="clave"]', owner_pass)
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        detail_url = f"{base_url}/admin/clientes/{cliente_id}"
        page.goto(detail_url, wait_until="domcontentloaded")
        pay_btn = page.locator(f'[data-testid="cliente-solicitud-registrar-pago-{solicitud_id}"]')
        expect(pay_btn).to_be_visible()
        expect(pay_btn).to_be_enabled()

        before_scroll_y = page.evaluate("window.scrollY")
        pay_btn.click()
        page.wait_for_selector("#registrarPagoModal.show", timeout=12000)
        page.wait_for_selector("#registrarPagoAsyncRegion form#pago-form", timeout=12000)

        form_diag = page.evaluate(
            """() => {
              const form = document.querySelector('#registrarPagoAsyncRegion form#pago-form');
              const button = form ? form.querySelector('button[type="submit"]') : null;
              return {
                exists: !!form,
                actionAttr: form ? form.getAttribute('action') : null,
                actionProp: form ? form.action : null,
                method: form ? form.method : null,
                buttonFormId: button && button.form ? button.form.id : null,
                buttonFormAction: button ? button.formAction : null,
                buttonType: button ? button.type : null,
                formsInModal: document.querySelectorAll('#registrarPagoModal form').length,
              };
            }"""
        )
        assert form_diag["exists"] is True, form_diag
        assert f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in str(form_diag["actionAttr"] or ""), form_diag
        assert f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in str(form_diag["actionProp"] or ""), form_diag
        assert str(form_diag["method"] or "").lower() == "post", form_diag
        assert form_diag["buttonFormId"] == "pago-form", form_diag
        assert f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in str(form_diag["buttonFormAction"] or ""), form_diag
        assert str(form_diag["buttonType"] or "").lower() == "submit", form_diag
        assert int(form_diag["formsInModal"] or 0) >= 1, form_diag

        page.select_option("#candidata_id", value=str(candidata_id))
        submit_btn = page.locator("#registrarPagoAsyncRegion form#pago-form button[type='submit']")
        with page.expect_request(
            lambda req: req.method == "POST" and f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in req.url
        ) as post_request_info:
            submit_btn.click()
        post_request = post_request_info.value
        assert "/admin/clientes/" in post_request.url
        assert f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in post_request.url

        page.wait_for_function(
            """() => {
              const modal = document.querySelector('#registrarPagoModal');
              return !!modal && !modal.classList.contains('show');
            }""",
            timeout=12000,
        )

        expect(page.locator(f'[data-testid="cliente-solicitud-registrar-pago-disabled-{solicitud_id}"]')).to_be_visible()

        current_path = page.evaluate("window.location.pathname")
        assert current_path == f"/admin/clientes/{cliente_id}"
        assert before_scroll_y == page.evaluate("window.scrollY")
        assert not any(req["method"] == "POST" and req["url"].rstrip("/") == f"{base_url}/admin/clientes/{cliente_id}" for req in all_requests)
        assert not any(item["type"] == "error" for item in console_messages), console_messages
        assert not failed_requests, failed_requests

        browser.close()
