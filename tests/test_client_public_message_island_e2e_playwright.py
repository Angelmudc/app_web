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
            window.__CPMI_COPIES = [];
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: {
                writeText: (text) => {
                  window.__CPMI_COPIES.push({ method: 'clipboard', text: String(text || '') });
                  return Promise.resolve();
                }
              }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: true
            });
            document.execCommand = (command) => {
              if (String(command || '').toLowerCase() === 'copy') {
                const el = document.activeElement;
                const value = el && typeof el.value === 'string' ? el.value : '';
                const start = el && typeof el.selectionStart === 'number' ? el.selectionStart : 0;
                const end = el && typeof el.selectionEnd === 'number' ? el.selectionEnd : value.length;
                window.__CPMI_COPIES.push({ method: 'execCommand', text: String(value).slice(start, end) || String(value) });
              }
              return true;
            };
        """,
        "clipboard_fail_exec_ok": """
            window.__CPMI_COPIES = [];
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: {
                writeText: (text) => {
                  window.__CPMI_COPIES.push({ method: 'clipboard-attempt', text: String(text || '') });
                  return Promise.reject(new DOMException('write denied', 'NotAllowedError'));
                }
              }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: false
            });
            document.execCommand = (command) => {
              if (String(command || '').toLowerCase() === 'copy') {
                const el = document.activeElement;
                const value = el && typeof el.value === 'string' ? el.value : '';
                const start = el && typeof el.selectionStart === 'number' ? el.selectionStart : 0;
                const end = el && typeof el.selectionEnd === 'number' ? el.selectionEnd : value.length;
                window.__CPMI_COPIES.push({ method: 'execCommand', text: String(value).slice(start, end) || String(value) });
              }
              return true;
            };
        """,
        "both_fail": """
            window.__CPMI_COPIES = [];
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: {
                writeText: (text) => {
                  window.__CPMI_COPIES.push({ method: 'clipboard-attempt', text: String(text || '') });
                  return Promise.reject(new DOMException('write denied', 'NotAllowedError'));
                }
              }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: false
            });
            document.execCommand = (command) => {
              if (String(command || '').toLowerCase() === 'copy') {
                const el = document.activeElement;
                const value = el && typeof el.value === 'string' ? el.value : '';
                const start = el && typeof el.selectionStart === 'number' ? el.selectionStart : 0;
                const end = el && typeof el.selectionEnd === 'number' ? el.selectionEnd : value.length;
                window.__CPMI_COPIES.push({ method: 'execCommand-attempt', text: String(value).slice(start, end) || String(value) });
              }
              return false;
            };
        """,
        "clipboard_write_promise_ok": """
            window.__CPMI_COPIES = [];
            window.ClipboardItem = function ClipboardItem(items) {
              this.items = items || {};
            };
            Object.defineProperty(window.navigator, 'clipboard', {
              configurable: true,
              value: {
                write: async (items) => {
                  const first = Array.isArray(items) ? items[0] : null;
                  const plain = first && first.items ? first.items['text/plain'] : null;
                  let text = '';
                  if (plain && typeof plain.then === 'function') {
                    const blob = await plain;
                    text = await blob.text();
                  }
                  window.__CPMI_COPIES.push({ method: 'clipboard-write', text: String(text || '') });
                  return Promise.resolve();
                },
                writeText: (text) => {
                  window.__CPMI_COPIES.push({ method: 'clipboard', text: String(text || '') });
                  return Promise.resolve();
                }
              }
            });
            Object.defineProperty(window, 'isSecureContext', {
              configurable: true,
              value: true
            });
            document.execCommand = (command) => {
              if (String(command || '').toLowerCase() === 'copy') {
                const el = document.activeElement;
                const value = el && typeof el.value === 'string' ? el.value : '';
                const start = el && typeof el.selectionStart === 'number' ? el.selectionStart : 0;
                const end = el && typeof el.selectionEnd === 'number' ? el.selectionEnd : value.length;
                window.__CPMI_COPIES.push({ method: 'execCommand', text: String(value).slice(start, end) || String(value) });
              }
              return true;
            };
        """,
    }
    context.add_init_script(scripts[mode])


def _new_context(browser, real_clipboard: bool = False, browser_name: str = ""):
    permissions = ["clipboard-read", "clipboard-write"] if real_clipboard and browser_name != "webkit" else None
    return browser.new_context(permissions=permissions)


def _read_real_clipboard(page) -> str:
    return page.evaluate(
        """
        async () => {
          if (!navigator.clipboard || typeof navigator.clipboard.readText !== 'function') {
            throw new Error('clipboard-read-unavailable');
          }
          return await navigator.clipboard.readText();
        }
        """
    )


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


def _mask_link(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 18:
        return text
    return f"{text[:12]}...{text[-6:]}"


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_global_island_copies_real_clipboard_same_click(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, real_clipboard=True, browser_name=browser_name)
        try:
            page = context.new_page()
        except Exception as exc:
            if browser_name == "webkit":
                pytest.skip(f"webkit real clipboard limitado en este runner: {exc}")
            raise
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        debug_errors = []
        page.on("console", lambda msg: debug_errors.append(msg.text) if "clipboard diagnosis" in msg.text else None)

        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        try:
            page.evaluate(
                """
                async () => {
                  if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') return;
                  await navigator.clipboard.writeText('seed-before-click');
                }
                """
            )
            page.click("#clientPublicMessageIslandBtn")
            page.wait_for_function(
                """
                () => {
                  const feedback = document.querySelector('#clientPublicMessageIslandFeedback');
                  const label = document.querySelector('#clientPublicMessageIslandBtn .cpmi-label');
                  const panel = document.querySelector('#clientPublicMessageIslandManual');
                  return !!feedback
                    && !!label
                    && feedback.textContent.trim() === 'Mensaje copiado'
                    && label.textContent.trim() === 'Mensaje copiado'
                    && !!panel
                    && panel.classList.contains('d-none');
                }
                """,
                timeout=12000,
            )
            assert endpoint_hits == [200]
            assert len(links) == 1
            clipboard_text = _read_real_clipboard(page).strip()
            copy_method = page.evaluate("() => window.__CPMI_LAST_COPY_METHOD || ''").strip()
            copy_error = page.evaluate("() => window.__CPMI_LAST_COPY_ERROR || null")
        except Exception as exc:
            if browser_name == "webkit":
                pytest.skip(f"webkit clipboard real no verificable en este entorno: {exc}")
            raise

        generated_link = links[0].strip()
        assert generated_link
        assert clipboard_text
        assert generated_link in clipboard_text
        assert clipboard_text.endswith("Cuando lo completes, envíame tu nombre y dime que ya terminaste.")

        print(
            "[cpmi-e2e]",
            {
                "browser": browser_name,
                "route": "/admin/solicitudes",
                "endpoint_status": endpoint_hits[0],
                "generated_link": _mask_link(generated_link),
                "clipboard": _mask_link(clipboard_text),
                "copy_method": copy_method,
                "copy_error": copy_error,
                "diagnostics": debug_errors[-1] if debug_errors else "",
            },
        )

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
@pytest.mark.parametrize(
    ("copy_mode", "expected_feedback", "manual_visible", "expected_label"),
    [
        ("clipboard_ok", "Mensaje copiado", False, "Mensaje copiado"),
        ("clipboard_fail_exec_ok", "Mensaje copiado", False, "Mensaje copiado"),
        (
            "both_fail",
            "Enlace listo para copiar manualmente",
            True,
            "Enlace listo para copiar manualmente",
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

        context = _new_context(browser, browser_name=browser_name)
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
            assert page.locator("#clientPublicMessageIslandManualBackdrop").count() == 0
            assert page.input_value("#clientPublicMessageIslandManualLink").strip()
            assert page.locator("#clientPublicMessageIslandManualStatus").inner_text().strip() == "Safari no permitió copiar automáticamente. Copia el enlace manualmente."
            panel_rect = page.locator("#clientPublicMessageIslandManual").evaluate(
                """
                (el) => {
                  const rect = el.getBoundingClientRect();
                  return {
                    top: rect.top,
                    left: rect.left,
                    right: rect.right,
                    bottom: rect.bottom,
                    width: rect.width,
                    height: rect.height,
                    viewportWidth: window.innerWidth,
                    viewportHeight: window.innerHeight
                  };
                }
                """
            )
            assert panel_rect["width"] < panel_rect["viewportWidth"]
            assert panel_rect["height"] < panel_rect["viewportHeight"]
            assert panel_rect["bottom"] > 0
            assert panel_rect["right"] > 0
            page.wait_for_function(
                """
                () => {
                  const el = document.querySelector('#clientPublicMessageIslandManualLink');
                  return !!el && el.selectionStart === 0 && el.selectionEnd === el.value.length;
                }
                """,
                timeout=12000,
            )
            selected = page.locator("#clientPublicMessageIslandManualLink").evaluate(
                """
                (el) => ({
                  start: el.selectionStart,
                  end: el.selectionEnd,
                  length: el.value.length
                })
                """
            )
            assert selected["start"] == 0
            assert selected["end"] == selected["length"]
            page.click("#clientPublicMessageIslandSelectLinkBtn")
            selected_after_btn = page.locator("#clientPublicMessageIslandManualLink").evaluate(
                """
                (el) => ({
                  start: el.selectionStart,
                  end: el.selectionEnd,
                  length: el.value.length
                })
                """
            )
            assert selected_after_btn["start"] == 0
            assert selected_after_btn["end"] == selected_after_btn["length"]
            page.click("#clientPublicMessageIslandCloseManualBtn")
            page.wait_for_function(
                """
                () => {
                  const panel = document.querySelector('#clientPublicMessageIslandManual');
                  return !!panel && panel.classList.contains('d-none');
                }
                """,
                timeout=12000,
            )
            page.wait_for_selector("#clientPublicMessageIslandBtn", state="visible", timeout=12000)
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
def test_generation_error_shows_backend_message_with_retry_after(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, browser_name=browser_name)
        _install_cpmi_clipboard_stub(context, "clipboard_ok")
        page = context.new_page()
        _admin_login(page, base_url)

        endpoint_hits, links = _attach_cpmi_link_capture(page)
        page.route(
            "**/admin/solicitudes/nueva-publica/link.json",
            lambda route: route.fulfill(
                status=429,
                content_type="application/json",
                headers={"Retry-After": "60"},
                body='{"ok": false, "error": "rate_limited", "message": "Has generado varios enlaces recientemente.", "retry_after_sec": 60}',
            ),
        )
        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        page.click("#clientPublicMessageIslandBtn")
        page.wait_for_function(
            """
            ({ expectedFeedback }) => {
              const feedback = document.querySelector('#clientPublicMessageIslandFeedback');
              const label = document.querySelector('#clientPublicMessageIslandBtn .cpmi-label');
              if (!feedback || !label) return false;
              return feedback.textContent.trim() === expectedFeedback && label.textContent.trim() === expectedFeedback;
            }
            """,
            arg={"expectedFeedback": "Has generado varios enlaces recientemente. (60s)"},
            timeout=12000,
        )

        assert endpoint_hits == [429]
        assert links == []
        assert page.locator("#clientPublicMessageIslandFeedback").inner_text().strip() == "Has generado varios enlaces recientemente. (60s)"
        assert page.locator("#clientPublicMessageIslandBtn .cpmi-label").inner_text().strip() == "Has generado varios enlaces recientemente. (60s)"

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

        context = _new_context(browser, browser_name=browser_name)
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

        context = _new_context(browser, browser_name=browser_name)
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
        page.wait_for_selector("#clientPublicMessageIslandManual:not(.d-none)", timeout=12000)
        assert page.locator("#clientPublicMessageIslandFeedback").inner_text().strip() == "Enlace listo para copiar manualmente"

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

        context = _new_context(browser, browser_name=browser_name)
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


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_global_island_attempts_auto_copy_with_live_user_activation(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, browser_name=browser_name)
        _install_cpmi_clipboard_stub(context, "clipboard_write_promise_ok")
        page = context.new_page()
        _admin_login(page, base_url)

        page.goto(f"{base_url}/admin/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector("#clientPublicMessageIslandBtn", timeout=12000)

        page.click("#clientPublicMessageIslandBtn")
        page.wait_for_function(
            """
            () => {
              const diagnosis = window.__CPMI_LAST_COPY_DIAGNOSIS || null;
              return !!(diagnosis && diagnosis.writePromiseAttempted === true && diagnosis.writePromiseSuccess === true);
            }
            """,
            timeout=12000,
        )

        diagnosis = page.evaluate("() => window.__CPMI_LAST_COPY_DIAGNOSIS || null")
        assert diagnosis is not None
        assert diagnosis["writePromiseAttempted"] is True
        assert diagnosis["writePromiseSuccess"] is True
        assert diagnosis["userActivationAtWritePromise"]["isActive"] is True
        assert diagnosis["userActivationAtWritePromise"]["hasBeenActive"] is True
        assert diagnosis["userActivationAtAttempt"]["hasBeenActive"] is True

        copied_entries = page.evaluate("() => window.__CPMI_COPIES || []")
        assert copied_entries
        assert copied_entries[0]["method"] == "clipboard-write"
        assert "https://" in str(copied_entries[0]["text"] or "")

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
@pytest.mark.parametrize(
    ("copy_mode", "expected_feedback", "manual_visible"),
    [
        ("clipboard_ok", "Mensaje copiado", False),
        ("clipboard_fail_exec_ok", "Mensaje copiado", False),
        ("both_fail", "Enlace listo para copiar manualmente", True),
    ],
)
def test_clientes_link_page_auto_copy_uses_fallbacks_before_manual_panel(cpmi_e2e_env, browser_name, copy_mode, expected_feedback, manual_visible):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, browser_name=browser_name)
        _install_cpmi_clipboard_stub(context, copy_mode)
        page = context.new_page()
        _admin_login(page, base_url)

        page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
        page.wait_for_selector('a[href$="/admin/solicitudes/nueva-publica/link"]', timeout=12000)

        with context.expect_page() as popup_info:
          page.click('a[href$="/admin/solicitudes/nueva-publica/link"]')
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        popup.wait_for_selector("#linkPublicoNuevo", timeout=12000)

        popup.wait_for_function(
            """
            ({ expectedFeedback, manualVisible }) => {
              const feedback = document.querySelector('#copyIslandNuevoFeedback');
              const panel = document.querySelector('#linkPublicoNuevoManualPanel');
              const msg = document.querySelector('#copyMsgNuevo');
              if (!feedback || !panel || !msg) return false;
              if (feedback.textContent.trim() !== expectedFeedback) return false;
              const isVisible = panel.classList.contains('is-visible');
              if (isVisible !== manualVisible) return false;
              if (!manualVisible) return msg.textContent.trim() === 'Mensaje copiado.';
              return msg.textContent.includes('No se pudo copiar automáticamente');
            }
            """,
            arg={"expectedFeedback": expected_feedback, "manualVisible": manual_visible},
            timeout=12000,
        )

        original_link = popup.input_value("#linkPublicoNuevo").strip()
        copied_entries = popup.evaluate("() => window.__CPMI_COPIES || []")
        assert copied_entries
        assert any(original_link in str(entry.get("text") or "") for entry in copied_entries)

        if manual_visible:
            popup.wait_for_selector("#linkPublicoNuevoManualPanel.is-visible", timeout=12000)
            assert popup.input_value("#linkPublicoNuevoManualInput").strip() == original_link
        else:
            popup.wait_for_function(
                """
                () => {
                  const panel = document.querySelector('#linkPublicoNuevoManualPanel');
                  return !!panel && !panel.classList.contains('is-visible');
                }
                """,
                timeout=12000,
            )

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_clientes_link_page_manual_copy_panel_is_non_blocking(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, browser_name=browser_name)
        _install_cpmi_clipboard_stub(context, "both_fail")
        page = context.new_page()
        _admin_login(page, base_url)

        page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
        page.wait_for_selector('a[href$="/admin/solicitudes/nueva-publica/link"]', timeout=12000)

        with context.expect_page() as popup_info:
            page.click('a[href$="/admin/solicitudes/nueva-publica/link"]')
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        popup.wait_for_selector("#linkPublicoNuevo", timeout=12000)

        initial_url = popup.url
        original_link = popup.input_value("#linkPublicoNuevo").strip()
        assert original_link

        extra_requests = []

        def _capture_request(req):
            if req.url.endswith("/admin/solicitudes/nueva-publica/link"):
                extra_requests.append(req.url)

        popup.on("request", _capture_request)

        popup.wait_for_selector("#linkPublicoNuevoManualPanel.is-visible", timeout=12000)

        assert popup.locator(".modal-backdrop").count() == 0
        assert popup.locator("#linkPublicoNuevoManualPanel").get_attribute("aria-modal") is None
        assert popup.input_value("#linkPublicoNuevoManualInput").strip() == original_link
        assert popup.locator("#linkPublicoNuevoManualSelectBtn").count() == 1
        popup.click("#linkPublicoNuevoManualSelectBtn")
        popup.wait_for_function(
            """
            () => {
              const input = document.querySelector('#linkPublicoNuevoManualInput');
              return !!input && input.selectionStart === 0 && input.selectionEnd === input.value.length;
            }
            """,
            timeout=12000,
        )

        dom_state = popup.evaluate(
            """
            () => {
              const panel = document.querySelector('#linkPublicoNuevoManualPanel');
              const input = document.querySelector('#linkPublicoNuevoManualInput');
              const selectBtn = document.querySelector('#linkPublicoNuevoManualSelectBtn');
              const style = panel ? window.getComputedStyle(panel) : null;
              return {
                htmlClasses: document.documentElement.className,
                bodyClasses: document.body.className,
                hasOldBackdrop: !!document.querySelector('#clientPublicMessageIslandManualBackdrop'),
                hasModalBackdrop: !!document.querySelector('.modal-backdrop'),
                panelVisible: !!(panel && panel.classList.contains('is-visible')),
                panelClasses: panel ? panel.className : '',
                panelZIndex: style ? style.zIndex : '',
                panelPointerEvents: style ? style.pointerEvents : '',
                panelPosition: style ? style.position : '',
                panelDisplay: style ? style.display : '',
                inputValue: input ? input.value : '',
                selectExists: !!selectBtn,
                selectionStart: input ? input.selectionStart : -1,
                selectionEnd: input ? input.selectionEnd : -1,
                inputLength: input ? input.value.length : 0
              };
            }
            """
        )
        assert "modal-open" not in dom_state["htmlClasses"]
        assert "modal-open" not in dom_state["bodyClasses"]
        assert dom_state["hasOldBackdrop"] is False
        assert dom_state["hasModalBackdrop"] is False
        assert dom_state["panelVisible"] is True
        assert dom_state["panelPosition"] == "fixed"
        assert dom_state["panelDisplay"] == "block"
        assert dom_state["panelPointerEvents"] != "none"
        assert dom_state["inputValue"].strip() == original_link
        assert dom_state["selectExists"] is True
        assert dom_state["selectionStart"] == 0
        assert dom_state["selectionEnd"] == dom_state["inputLength"]

        popup.click("#linkPublicoNuevoManualRetryBtn")
        popup.wait_for_selector("#linkPublicoNuevoManualPanel.is-visible", timeout=12000)
        assert popup.input_value("#linkPublicoNuevoManualInput").strip() == original_link
        assert popup.url == initial_url
        assert extra_requests == []

        popup.click("#linkPublicoNuevoManualCloseBtn")
        popup.wait_for_function(
            """
            () => {
              const panel = document.querySelector('#linkPublicoNuevoManualPanel');
              const input = document.querySelector('#linkPublicoNuevoManualInput');
              return !!panel && !panel.classList.contains('is-visible') && !!input && input.value === '';
            }
            """,
            timeout=12000,
        )

        context.close()
        browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_clientes_link_page_separate_opens_generate_distinct_links(cpmi_e2e_env, browser_name):
    base_url = cpmi_e2e_env.base_url

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, browser_name)
        except Exception as exc:
            pytest.skip(f"{browser_name} no disponible: {exc}")

        context = _new_context(browser, browser_name=browser_name)
        _install_cpmi_clipboard_stub(context, "clipboard_ok")
        page = context.new_page()
        _admin_login(page, base_url)

        page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
        page.wait_for_selector('a[href$="/admin/solicitudes/nueva-publica/link"]', timeout=12000)

        with context.expect_page() as first_popup_info:
            page.click('a[href$="/admin/solicitudes/nueva-publica/link"]')
        first_popup = first_popup_info.value
        first_popup.wait_for_load_state("domcontentloaded")
        first_popup.wait_for_selector("#linkPublicoNuevo", timeout=12000)
        first_link = first_popup.input_value("#linkPublicoNuevo").strip()
        assert first_link
        first_popup.close()

        with context.expect_page() as second_popup_info:
            page.click('a[href$="/admin/solicitudes/nueva-publica/link"]')
        second_popup = second_popup_info.value
        second_popup.wait_for_load_state("domcontentloaded")
        second_popup.wait_for_selector("#linkPublicoNuevo", timeout=12000)
        second_link = second_popup.input_value("#linkPublicoNuevo").strip()
        assert second_link
        assert second_link != first_link

        with flask_app.app_context():
            first_code = _extract_share_code(first_link)
            second_code = _extract_share_code(second_link)
            rows = (
                PublicSolicitudShareAlias.query
                .filter(PublicSolicitudShareAlias.code.in_([first_code, second_code]))
                .filter_by(link_type="nuevo", is_active=True)
                .all()
            )
            assert len(rows) == 2
            assert len({str(getattr(row, "token_hash", "") or "") for row in rows}) == 2

        context.close()
        browser.close()
