# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Cliente, Solicitud


playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


def _start_test_server():
    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_port)


@pytest.fixture()
def dom_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        try:
            Cliente.__table__.drop(bind=db.engine, checkfirst=True)
        except Exception:
            pass
        try:
            db.session.execute(text("DROP TABLE IF EXISTS clientes_notificaciones"))
            db.session.execute(text("DROP TABLE IF EXISTS solicitudes"))
            db.session.commit()
        except Exception:
            pass

        Cliente.__table__.create(bind=db.engine, checkfirst=True)
        db.session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS clientes_notificaciones ("
                "id INTEGER PRIMARY KEY, "
                "cliente_id INTEGER NOT NULL, "
                "solicitud_id INTEGER, "
                "tipo TEXT, "
                "titulo TEXT, "
                "cuerpo TEXT, "
                "payload TEXT, "
                "is_read BOOLEAN, "
                "is_deleted BOOLEAN, "
                "created_at DATETIME, "
                "updated_at DATETIME)"
            )
        )
        db.session.commit()
        dialect = str(getattr(getattr(db.engine, "dialect", None), "name", "") or "").lower()
        if dialect == "sqlite":
            cols = []
            for col in Solicitud.__table__.columns:
                name = col.name
                if name == "id":
                    cols.append("id INTEGER PRIMARY KEY")
                elif name.endswith("_id") or name in ("row_version", "veces_activada", "adultos", "ninos", "habitaciones"):
                    cols.append(f"{name} INTEGER")
                elif name in ("banos", "compat_calc_score"):
                    cols.append(f"{name} REAL")
                elif name.startswith("fecha_") or name.endswith("_at"):
                    cols.append(f"{name} DATETIME")
                elif name in ("dos_pisos", "pasaje_aporte"):
                    cols.append(f"{name} BOOLEAN")
                else:
                    cols.append(f"{name} TEXT")
            ddl = "CREATE TABLE IF NOT EXISTS solicitudes (" + ", ".join(cols) + ")"
            db.session.execute(text(ddl))
            db.session.commit()
        else:
            Solicitud.__table__.create(bind=db.engine, checkfirst=True)
        cliente = Cliente(
            codigo="CL-DOM-001",
            nombre_completo="Cliente DOM",
            email="cliente_dom@test.local",
            telefono="8090000100",
            username="cliente_dom",
            password_hash=generate_password_hash("cliente12345", method="pbkdf2:sha256"),
            is_active=True,
            role="cliente",
            acepto_politicas=True,
            total_solicitudes=0,
        )
        db.session.add(cliente)
        db.session.commit()

    server, thread, port = _start_test_server()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "username": "cliente_dom",
            "password": "cliente12345",
        }
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        thread.join(timeout=2)


def _login_and_open_form(page, base_url: str, username: str, password: str):
    page.goto(f"{base_url}/clientes/login?next=%2Fclientes%2Fsolicitudes%2Fnueva", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url("**/clientes/solicitudes/nueva**", timeout=12000)
    page.wait_for_selector("#wrap_ninos_limpieza_smart_alert", state="attached", timeout=12000)


def _set_form_state(page, *, funciones: list[str], edades: str):
    page.evaluate(
        """(payload) => {
          const wanted = new Set(payload.funciones || []);
          const boxes = Array.from(document.querySelectorAll('input[type="checkbox"][name="funciones"]'));
          boxes.forEach((el) => {
            el.checked = wanted.has(el.value);
            el.dispatchEvent(new Event('change', { bubbles: true }));
          });
          const ninos = document.querySelector('input[name="ninos"]');
          if (ninos) {
            ninos.value = '1';
            ninos.dispatchEvent(new Event('input', { bubbles: true }));
            ninos.dispatchEvent(new Event('change', { bubbles: true }));
          }
          const edadesInput = document.querySelector('input[name="edades_ninos"]');
          if (edadesInput) {
            edadesInput.value = payload.edades || '';
            edadesInput.dispatchEvent(new Event('input', { bubbles: true }));
            edadesInput.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }""",
        {"funciones": funciones, "edades": edades},
    )


def _smart_alert_visible(page) -> bool:
    return bool(
        page.evaluate(
            """() => {
              const el = document.querySelector('#wrap_ninos_limpieza_smart_alert');
              if (!el) return false;
              return !el.classList.contains('d-none') && el.getAttribute('aria-hidden') === 'false';
            }"""
        )
    )


def test_smart_alert_dom_interactions(dom_env):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.route("**/clientes/chat/conversations.json**", lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"items":[],"total":0}',
            ))
            _login_and_open_form(page, dom_env["base_url"], dom_env["username"], dom_env["password"])
            assert _smart_alert_visible(page) is False

            _set_form_state(page, funciones=["ninos", "limpieza"], edades="1 año y 5 meses")
            assert _smart_alert_visible(page) is True

            _set_form_state(page, funciones=["ninos"], edades="1 año y 5 meses")
            assert _smart_alert_visible(page) is False

            _set_form_state(page, funciones=["ninos", "limpieza"], edades="6 años")
            assert _smart_alert_visible(page) is False

            _set_form_state(page, funciones=["ninos", "cocinar"], edades="1 año")
            assert _smart_alert_visible(page) is False

            _set_form_state(page, funciones=["ninos", "lavar"], edades="1 año")
            assert _smart_alert_visible(page) is False

            _set_form_state(page, funciones=["ninos", "planchar"], edades="1 año")
            assert _smart_alert_visible(page) is False
        finally:
            browser.close()
