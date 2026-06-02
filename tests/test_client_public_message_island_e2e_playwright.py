# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import PublicSolicitudShareAlias, StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _launch_browser(p, browser_name: str):
    bt = getattr(p, browser_name)
    return bt.launch(headless=True, args=["--no-sandbox"] if browser_name == "chromium" else None)


def _extract_share_code(link: str) -> str:
    path = urlparse(str(link or "").strip()).path.strip("/")
    if not path:
        return ""
    return path.split("/")[-1].strip().upper()


@pytest.fixture()
def cpmi_e2e_env():
    import admin.routes as admin_routes

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

        owner = StaffUser.query.filter_by(username="owner_cpmi_e2e").first()
        if owner is None:
            owner = StaffUser(
                username="owner_cpmi_e2e",
                email="owner_cpmi_e2e@test.local",
                role="owner",
                is_active=True,
                mfa_enabled=False,
            )
            db.session.add(owner)
        owner.set_password("Owner#12345")
        db.session.commit()

    original_quota = admin_routes._consume_nueva_publica_link_quota
    admin_routes._consume_nueva_publica_link_quota = lambda: {"allowed": True}

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

    yield SimpleNamespace(base_url=base_url)

    admin_routes._consume_nueva_publica_link_quota = original_quota
    server.shutdown()
    thread.join(timeout=3)


def _admin_login(page, base_url: str) -> None:
    page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
    page.fill('input[name="usuario"]', "owner_cpmi_e2e")
    page.fill('input[name="clave"]', "Owner#12345")
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin/**", timeout=15000)


def _install_cpmi_clipboard_stub(context, mode: str) -> None:
    scripts = {
        "clipboard_ok": """
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: { writeText: () => Promise.resolve() }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: true
            });
            document.execCommand = () => true;
        """,
        "clipboard_fail_exec_ok": """
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: { writeText: () => Promise.reject(new DOMException('write denied', 'NotAllowedError')) }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: false
            });
            document.execCommand = () => true;
        """,
        "both_fail": """
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: { writeText: () => Promise.reject(new DOMException('write denied', 'NotAllowedError')) }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: false
            });
            document.execCommand = () => false;
        """,
    }
    context.add_init_script(scripts[mode])


def _wait_cpmi_idle(page) -> None:
    page.wait_for_function(
        """
        () => {
          const btn = document.querySelector('#clientPublicMessageIslandBtn');
          return !!btn && !btn.disabled;
        }
        """,
        timeout=12000,
    )


def _attach_cpmi_link_capture(page):
    endpoint_hits = []
    links = []

    def _capture_response(resp):
        if "/admin/solicitudes/nueva-publica/link.json" not in resp.url:
            return
        endpoint_hits.append(resp.status)
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        link = str((payload or {}).get("link_publico") or "").strip()
        if link:
            links.append(link)

    page.on("response", _capture_response)
    return endpoint_hits, links


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
@pytest.mark.parametrize(
    ("copy_mode", "expected_feedback", "manual_visible", "expected_label"),
    [
        ("clipboard_ok", "Mensaje copiado", False, "Mensaje copiado"),
        ("clipboard_fail_exec_ok", "Mensaje copiado", False, "Mensaje copiado"),
        (
            "both_fail",
            "Enlace generado, pero no se pudo copiar automáticamente",
            True,
            "Copia manual disponible",
        ),
    ],
)
def test_copy_feedback_and_manual_fallback(cpmi_e2e_env, browser_name, copy_mode, expected_feedback, manual_visible, expected_label):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = browser.new_context()
        _install_cpmi_clipboard_stub(context, copy_mode)
        page = context.new_page()
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        page.click("#clientPublicMessageIslandBtn")
        page.wait_for_function(
            """
            ({ expectedFeedback, expectedLabel }) => {
              const feedback = document.querySelector('#clientPublicMessageIslandFeedback');
              const label = document.querySelector('#clientPublicMessageIslandBtn .cpmi-label');
              if (!feedback || !label) return false;
              return feedback.textContent.trim() === expectedFeedback && label.textContent.trim() === expectedLabel;
            }
            """,
            arg={"expectedFeedback": expected_feedback, "expectedLabel": expected_label},
            timeout=12000,
        )

        assert endpoint_hits == [200]
        assert len(links) == 1

        if manual_visible:
            page.wait_for_selector("#clientPublicMessageIslandManual:not(.d-none)", timeout=12000)
            assert page.input_value("#clientPublicMessageIslandManualLink").strip()
        else:
            page.wait_for_function(
                """
                () => {
                  const panel = document.querySelector('#clientPublicMessageIslandManual');
                  return !!panel && panel.classList.contains('d-none');
                }
                """,
                timeout=12000,
            )

        _wait_cpmi_idle(page)

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_main_button_generates_fresh_token_each_time(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = browser.new_context()
        # Fuerza éxito de copiado para observar únicamente generación/token.
        _install_cpmi_clipboard_stub(context, "clipboard_ok")
        page = context.new_page()
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        for _ in range(5):
            page.click("#clientPublicMessageIslandBtn")
            _wait_cpmi_idle(page)

        assert len(endpoint_hits) == 5
        assert all(int(code) == 200 for code in endpoint_hits)
        assert len(links) == 5
        assert len(set(links)) == 5

        with flask_app.app_context():
            codes = [_extract_share_code(link) for link in links]
            codes = [code for code in codes if code]
            rows = (
                PublicSolicitudShareAlias.query
                .filter(PublicSolicitudShareAlias.code.in_(codes))
                .filter_by(link_type="nuevo")
                .all()
            )
            tokens = [str(getattr(r, "token", "") or "") for r in rows]
            token_hashes = [str(getattr(r, "token_hash", "") or "") for r in rows]
            assert len(rows) == 5
            assert len(set(tokens)) == 5
            assert len(set(token_hashes)) == 5

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_copy_again_reuses_current_token_without_new_request(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = browser.new_context()
        _install_cpmi_clipboard_stub(context, "both_fail")
        page = context.new_page()
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        page.click("#clientPublicMessageIslandBtn")
        page.wait_for_selector("#clientPublicMessageIslandManual:not(.d-none)", timeout=12000)

        link_in_manual_before = page.input_value("#clientPublicMessageIslandManualLink").strip()
        assert link_in_manual_before
        assert len(endpoint_hits) == 1

        page.click("#clientPublicMessageIslandRetryCopyBtn")
        _wait_cpmi_idle(page)

        # Copiar de nuevo no debe llamar endpoint ni regenerar token/link.
        assert len(endpoint_hits) == 1
        assert len(links) == 1
        link_in_manual_after = page.input_value("#clientPublicMessageIslandManualLink").strip()
        assert link_in_manual_after == link_in_manual_before

        with flask_app.app_context():
            code = _extract_share_code(link_in_manual_before)
            row = PublicSolicitudShareAlias.query.filter_by(code=code, link_type="nuevo").first()
            assert row is not None

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_rapid_double_click_creates_single_request_then_next_click_creates_new_token(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = browser.new_context()
        _install_cpmi_clipboard_stub(context, "clipboard_ok")
        page = context.new_page()
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        page.dblclick("#clientPublicMessageIslandBtn")
        _wait_cpmi_idle(page)
        assert len(endpoint_hits) == 1
        assert len(links) == 1

        page.click("#clientPublicMessageIslandBtn")
        _wait_cpmi_idle(page)
        assert len(endpoint_hits) == 2
        assert len(links) == 2
        assert links[0] != links[1]

        context.close()
        browser.close()
