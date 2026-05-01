# -*- coding: utf-8 -*-
from __future__ import annotations

import socket
import threading
import time

import pytest
import requests
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Cliente, StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables

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
def cliente_detail_modal_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with flask_app.app_context():
        mapped_models = []
        for mapper in db.Model.registry.mappers:
            cls = getattr(mapper, "class_", None)
            if cls is None or not hasattr(cls, "__table__"):
                continue
            mapped_models.append(cls)
        ensure_sqlite_compat_tables(mapped_models, reset=True)

        _ensure_staff_user(
            username="owner_e2e_detail_modal",
            email="owner_e2e_detail_modal@test.local",
            role="owner",
            password="Owner#12345",
        )

        cliente = Cliente(
            codigo="CL-E2E-DETAIL-MODAL-001",
            nombre_completo="Cliente E2E Detail Modal",
            email="cliente_e2e_detail_modal@test.local",
            telefono="8095551202",
            username="cliente_e2e_detail_modal",
            password_hash=generate_password_hash("Cliente#12345", method="pbkdf2:sha256"),
            is_active=True,
            role="cliente",
            total_solicitudes=0,
        )
        db.session.add(cliente)
        db.session.commit()
        cliente_id = int(cliente.id)

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
        "owner_user": "owner_e2e_detail_modal",
        "owner_pass": "Owner#12345",
        "cliente_id": cliente_id,
    }

    server.shutdown()
    thread.join(timeout=3)


def test_cliente_detail_delete_modal_is_interactive_and_reopenable(cliente_detail_modal_env):
    base_url = cliente_detail_modal_env["base_url"]
    owner_user = cliente_detail_modal_env["owner_user"]
    owner_pass = cliente_detail_modal_env["owner_pass"]
    cliente_id = cliente_detail_modal_env["cliente_id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', owner_user)
        page.fill('input[name="clave"]', owner_pass)
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        page.goto(f"{base_url}/admin/clientes/{cliente_id}", wait_until="domcontentloaded")
        page.wait_for_selector('[data-bs-target="#deleteClienteModal"]', timeout=12000)
        page.click('[data-bs-target="#deleteClienteModal"]')
        page.wait_for_selector("#deleteClienteModal.show", timeout=12000)

        diag = page.evaluate(
            """() => {
                function shortNode(el) {
                  if (!el) return null;
                  const id = el.id ? `#${el.id}` : '';
                  const cls = (el.className && typeof el.className === 'string')
                    ? '.' + el.className.trim().replace(/\\s+/g, '.')
                    : '';
                  return `${el.tagName.toLowerCase()}${id}${cls}`;
                }

                const modal = document.querySelector('#deleteClienteModal');
                const input = document.querySelector('#delete_cliente_confirm_input');
                const inputRect = input ? input.getBoundingClientRect() : null;
                let inputTop = null;
                if (inputRect) {
                  inputTop = document.elementFromPoint(
                    Math.max(0, Math.floor(inputRect.left + (inputRect.width / 2))),
                    Math.max(0, Math.floor(inputRect.top + (inputRect.height / 2)))
                  );
                }

                return {
                  modalParent: modal && modal.parentElement ? shortNode(modal.parentElement) : null,
                  inputTopNode: shortNode(inputTop),
                  backdropCount: document.querySelectorAll('.modal-backdrop').length,
                };
            }"""
        )

        assert int(diag["backdropCount"]) == 1, f"Backdrop inconsistente: {diag}"
        input_top = str(diag.get("inputTopNode") or "")
        assert "modal-backdrop" not in input_top, f"Backdrop bloqueando input detectado: {diag}"

        page.click("#delete_cliente_confirm_input")
        page.fill("#delete_cliente_confirm_input", "NO-ELIMINAR")
        assert page.input_value("#delete_cliente_confirm_input") == "NO-ELIMINAR"

        page.click("#deleteClienteModal .btn-close")
        page.wait_for_selector("#deleteClienteModal.show", state="hidden", timeout=12000)

        page.click('[data-bs-target="#deleteClienteModal"]')
        page.wait_for_selector("#deleteClienteModal.show", timeout=12000)
        page.click('#deleteClienteModal .modal-footer button[data-bs-dismiss="modal"]')
        page.wait_for_function(
            "() => { const m = document.querySelector('#deleteClienteModal'); return !!m && !m.classList.contains('show'); }",
            timeout=12000,
        )

        browser.close()
