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
def clientes_modal_env():
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
            username="owner_e2e_modal",
            email="owner_e2e_modal@test.local",
            role="owner",
            password="Owner#12345",
        )

        cliente = Cliente(
            codigo="CL-E2E-MODAL-001",
            nombre_completo="Cliente E2E Modal",
            email="cliente_e2e_modal@test.local",
            telefono="8095551201",
            username="cliente_e2e_modal",
            password_hash=generate_password_hash("Cliente#12345", method="pbkdf2:sha256"),
            is_active=True,
            role="cliente",
            total_solicitudes=0,
        )
        db.session.add(cliente)
        db.session.commit()

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
        "owner_user": "owner_e2e_modal",
        "owner_pass": "Owner#12345",
        "cliente_codigo": "CL-E2E-MODAL-001",
    }

    server.shutdown()
    thread.join(timeout=3)


def test_clientes_delete_modal_is_interactive_and_not_blocked(clientes_modal_env):
    base_url = clientes_modal_env["base_url"]
    owner_user = clientes_modal_env["owner_user"]
    owner_pass = clientes_modal_env["owner_pass"]
    cliente_codigo = clientes_modal_env["cliente_codigo"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', owner_user)
        page.fill('input[name="clave"]', owner_pass)
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
        page.fill('input[name="q"]', cliente_codigo)
        page.press('input[name="q"]', "Enter")
        page.wait_for_selector('[data-bs-target="#deleteClienteFromListModalShared"]', timeout=12000)
        page.click('[data-bs-target="#deleteClienteFromListModalShared"]')
        page.wait_for_selector("#deleteClienteFromListModalShared.show", timeout=12000)

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
                function details(sel) {
                  const arr = Array.from(document.querySelectorAll(sel));
                  return arr.map((el) => {
                    const cs = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return {
                      node: shortNode(el),
                      hiddenAttr: !!el.hidden,
                      display: cs.display,
                      visibility: cs.visibility,
                      opacity: cs.opacity,
                      zIndex: cs.zIndex,
                      pointerEvents: cs.pointerEvents,
                      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
                    };
                  });
                }

                const modal = document.querySelector('#deleteClienteFromListModalShared');
                const input = document.querySelector('#delete_cliente_list_confirm_input_shared');
                const modalRect = modal ? modal.getBoundingClientRect() : null;
                const inputRect = input ? input.getBoundingClientRect() : null;

                let modalTop = null;
                let inputTop = null;
                if (modalRect) {
                  modalTop = document.elementFromPoint(
                    Math.max(0, Math.floor(modalRect.left + (modalRect.width / 2))),
                    Math.max(0, Math.floor(modalRect.top + 40))
                  );
                }
                if (inputRect) {
                  inputTop = document.elementFromPoint(
                    Math.max(0, Math.floor(inputRect.left + (inputRect.width / 2))),
                    Math.max(0, Math.floor(inputRect.top + (inputRect.height / 2)))
                  );
                }

                return {
                  modalNode: shortNode(modal),
                  modalParent: modal && modal.parentElement ? shortNode(modal.parentElement) : null,
                  inputNode: shortNode(input),
                  modalTopNode: shortNode(modalTop),
                  inputTopNode: shortNode(inputTop),
                  bodyClasses: document.body ? document.body.className : '',
                  modalCount: document.querySelectorAll('.modal.show').length,
                  backdropCount: document.querySelectorAll('.modal-backdrop').length,
                  overlays: {
                    bootstrapBackdrop: details('.modal-backdrop'),
                    segBackdrop: details('.seg-candidatas-backdrop'),
                    segDrawer: details('.seg-candidatas-drawer'),
                    clientIsland: details('.client-public-message-island'),
                    segIsland: details('.seg-candidatas-island'),
                    finalIsland: details('.candidatas-finalizar-island'),
                  },
                };
            }"""
        )

        # Debe haber un solo backdrop bootstrap activo.
        assert int(diag["backdropCount"]) == 1, f"Backdrop inconsistente: {diag}"

        # El elemento en el punto del input debe pertenecer al modal.
        input_top = str(diag.get("inputTopNode") or "")
        assert (
            "deleteClienteFromListModalShared" in input_top
            or "delete_cliente_list_confirm_input_shared" in input_top
            or ".modal-dialog" in input_top
            or ".modal-content" in input_top
            or ".modal-body" in input_top
        ), f"Elemento bloqueando input detectado por elementFromPoint: {diag}"

        # Interacción real: focus + escritura + botones.
        page.click("#delete_cliente_list_confirm_input_shared")
        page.fill("#delete_cliente_list_confirm_input_shared", "NO-ELIMINAR")
        value = page.input_value("#delete_cliente_list_confirm_input_shared")
        assert value == "NO-ELIMINAR", f"No se pudo escribir en input modal: {diag}"

        # Cerrar con X.
        page.click("#deleteClienteFromListModalShared .btn-close")
        page.wait_for_selector("#deleteClienteFromListModalShared.show", state="hidden", timeout=12000)

        # Reabrir y cerrar con Cancelar.
        page.click('[data-bs-target="#deleteClienteFromListModalShared"]')
        page.wait_for_selector("#deleteClienteFromListModalShared.show", timeout=12000)
        page.click('#deleteClienteFromListModalShared .modal-footer button[data-bs-dismiss="modal"]')
        page.wait_for_function(
            "() => { const m = document.querySelector('#deleteClienteFromListModalShared'); return !!m && !m.classList.contains('show'); }",
            timeout=12000,
        )

        browser.close()
