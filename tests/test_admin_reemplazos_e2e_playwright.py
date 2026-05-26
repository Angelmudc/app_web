# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import socket
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Reemplazo, Solicitud, StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables
from tests.test_t1_reemplazo_flow import _seed_reemplazo_fixture

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


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
    user.set_password(password)


@pytest.fixture()
def reemplazo_modal_env():
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
            username="owner_e2e_reemplazo",
            email="owner_e2e_reemplazo@test.local",
            role="owner",
            password="Owner#12345",
        )

        cliente_id, solicitud_id, cand_old_id, _cand_new_id = _seed_reemplazo_fixture()
        solicitud = Solicitud.query.get(solicitud_id)
        assert solicitud is not None
        solicitud.estado = "reemplazo"

        reemplazo = Reemplazo(
            solicitud_id=solicitud_id,
            candidata_old_id=cand_old_id,
            motivo_fallo="No se presentó",
            estado_previo_solicitud="activa",
        )
        reemplazo.iniciar_reemplazo()
        db.session.add(reemplazo)
        db.session.commit()

        reemplazo_id = int(reemplazo.id)

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
        "owner_user": "owner_e2e_reemplazo",
        "owner_pass": "Owner#12345",
        "cliente_id": cliente_id,
        "reemplazo_id": reemplazo_id,
        "solicitud_id": solicitud_id,
    }

    server.shutdown()
    thread.join(timeout=3)


@pytest.mark.e2e
def test_reemplazo_cancel_modal_allows_typing_and_cancel_flow(reemplazo_modal_env):
    base_url = reemplazo_modal_env["base_url"]
    owner_user = reemplazo_modal_env["owner_user"]
    owner_pass = reemplazo_modal_env["owner_pass"]
    cliente_id = int(reemplazo_modal_env["cliente_id"])
    reemplazo_id = int(reemplazo_modal_env["reemplazo_id"])
    solicitud_id = int(reemplazo_modal_env["solicitud_id"])

    motivo = "Cliente decidió pausar el reemplazo."

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', owner_user)
        page.fill('input[name="clave"]', owner_pass)
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        page.goto(f"{base_url}/admin/reemplazos/{reemplazo_id}", wait_until="domcontentloaded")
        page.click('button:has-text("Más acciones")')
        page.click('button.dropdown-item.text-danger:has-text("Cancelar reemplazo")')
        page.wait_for_selector("#reemplazoCancelModal.show", timeout=12000)

        textarea_sel = '#reemplazoCancelModal textarea[name="motivo_cancelacion"]'
        page.click(textarea_sel)
        page.fill(textarea_sel, motivo)
        page.screenshot(path="/private/tmp/reemplazo_cancel_modal_before_confirm.png", full_page=True)
        assert motivo in page.input_value(textarea_sel)

        page.click('#reemplazoCancelModal button[data-bs-dismiss="modal"]')
        page.wait_for_selector("#reemplazoCancelModal.show", state="hidden", timeout=12000)

        page.click('button:has-text("Más acciones")')
        page.click('button.dropdown-item.text-danger:has-text("Cancelar reemplazo")')
        page.wait_for_selector("#reemplazoCancelModal.show", timeout=12000)
        page.fill(textarea_sel, motivo)
        assert motivo in page.input_value(textarea_sel)

        page.click('#reemplazoCancelModal .btn.btn-danger[type="submit"]')
        page.wait_for_url("**/admin/clientes/**/solicitudes/**", timeout=12000)
        page.wait_for_load_state("domcontentloaded")
        page.screenshot(path="/private/tmp/reemplazo_cancel_modal_after_confirm.png", full_page=True)

        content = page.content().lower()
        assert "sesión expir" not in content
        assert "sesion expir" not in content

        page.goto(
            f"{base_url}/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/_heavy",
            wait_until="domcontentloaded",
        )
        heavy_html = page.content()
        assert "Reemplazo cancelado · Solicitud no resuelta" in heavy_html

        browser.close()

    with flask_app.app_context():
        solicitud = Solicitud.query.get(solicitud_id)
        reemplazo = Reemplazo.query.get(reemplazo_id)
        assert solicitud is not None
        assert reemplazo is not None
        assert (reemplazo.resultado_final or "").lower() == "cancelado"
        assert (solicitud.estado or "").lower() != "cancelada"
