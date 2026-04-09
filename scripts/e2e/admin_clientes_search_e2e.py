#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import socket
import threading
import time
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
from config_app import db
from models import Cliente, StaffUser


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _seed_data() -> tuple[str, str]:
    admin_username = "e2e_admin_clientes_search"
    admin_password = "admin12345"

    with flask_app.app_context():
        StaffUser.__table__.create(bind=db.engine, checkfirst=True)
        Cliente.__table__.create(bind=db.engine, checkfirst=True)

        staff = StaffUser.query.filter_by(username=admin_username).first()
        if staff is None:
            staff = StaffUser(
                username=admin_username,
                email="e2e_admin_clientes_search@test.local",
                role="admin",
                is_active=True,
                mfa_enabled=False,
            )
            db.session.add(staff)
        staff.role = "admin"
        staff.is_active = True
        staff.password_hash = generate_password_hash(admin_password, method="pbkdf2:sha256")

        c1 = Cliente.query.filter_by(codigo="CL-E2E-SRCH-001").first()
        if c1 is None:
            c1 = Cliente(
                codigo="CL-E2E-SRCH-001",
                nombre_completo="Cliente E2E Uno",
                email="cliente_e2e_search_1@test.local",
                telefono="809-555-0001",
                username="cliente_e2e_search_1",
                password_hash=generate_password_hash("cliente12345", method="pbkdf2:sha256"),
                is_active=True,
                role="cliente",
                total_solicitudes=0,
            )
            db.session.add(c1)
        else:
            c1.nombre_completo = "Cliente E2E Uno"
            c1.telefono = "809-555-0001"

        c2 = Cliente.query.filter_by(codigo="CL-E2E-SRCH-002").first()
        if c2 is None:
            c2 = Cliente(
                codigo="CL-E2E-SRCH-002",
                nombre_completo="Cliente E2E Dos",
                email="cliente_e2e_search_2@test.local",
                telefono="809-555-0002",
                username="cliente_e2e_search_2",
                password_hash=generate_password_hash("cliente12345", method="pbkdf2:sha256"),
                is_active=True,
                role="cliente",
                total_solicitudes=0,
            )
            db.session.add(c2)
        else:
            c2.nombre_completo = "Cliente E2E Dos"
            c2.telefono = "809-555-0002"

        db.session.commit()

    return admin_username, admin_password


def main() -> int:
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    admin_username, admin_password = _seed_data()

    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.8)
            if r.status_code in (200, 404):
                break
        except Exception:
            time.sleep(0.08)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()

            page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
            page.fill('input[name="usuario"]', admin_username)
            page.fill('input[name="clave"]', admin_password)
            page.click('button[type="submit"]')
            page.wait_for_url("**/admin/**", timeout=12000)

            page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
            page.wait_for_selector("#clientesAsyncRegion")

            page.fill('input[name="q"]', "8095550002")
            page.press('input[name="q"]', "Enter")
            page.wait_for_function(
                """() => {
                    const region = document.querySelector('#clientesAsyncRegion');
                    if (!region) return false;
                    const txt = region.innerText || '';
                    return txt.includes('Cliente E2E Dos') && !txt.includes('Cliente E2E Uno');
                }""",
                timeout=12000,
            )

            page.fill('input[name="q"]', "NO-EXISTE-XYZ")
            page.press('input[name="q"]', "Enter")
            page.wait_for_function(
                """() => {
                    const region = document.querySelector('#clientesAsyncRegion');
                    return !!region && (region.innerText || '').includes('No hay clientes para mostrar');
                }""",
                timeout=12000,
            )

            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)

    print("E2E OK: admin clientes search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
