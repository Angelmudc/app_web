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
from models import Candidata, DomainOutbox, Solicitud, SolicitudCandidata, StaffAuditLog, StaffUser
from tests.test_admin_candidatas_operativo import _ensure_tables, _seed_center_candidate
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
        user = StaffUser(username=username, email=email, role=role, is_active=True, mfa_enabled=False)
        db.session.add(user)
    else:
        user.email = email
        user.role = role
        user.is_active = True
        user.mfa_enabled = False
    user.set_password(password)


@pytest.fixture()
def candidata_disqualify_modal_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    fila = 990690

    with flask_app.app_context():
        mapped_models = []
        for mapper in db.Model.registry.mappers:
            cls = getattr(mapper, "class_", None)
            if cls is not None and hasattr(cls, "__table__"):
                mapped_models.append(cls)
        ensure_sqlite_compat_tables(mapped_models, reset=True)
        _ensure_tables()
        _ensure_staff_user(
            username="owner_e2e_cand_modal",
            email="owner_e2e_cand_modal@test.local",
            role="owner",
            password="Owner#12345",
        )
        _seed_center_candidate(fila=fila)
        SolicitudCandidata.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
        Solicitud.query.filter_by(id=fila).delete(synchronize_session=False)
        cand = Candidata.query.get(fila)
        assert cand is not None
        cand.estado = "lista_para_trabajar"
        cand.nota_descalificacion = None
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
        "owner_user": "owner_e2e_cand_modal",
        "owner_pass": "Owner#12345",
        "fila": fila,
    }

    server.shutdown()
    thread.join(timeout=3)


def _modal_state(page):
    return page.evaluate(
        """() => {
          const modal = document.querySelector('#candDisqualifyModal');
          const style = modal ? window.getComputedStyle(modal) : null;
          const active = document.activeElement;
          return {
            modalCount: document.querySelectorAll('#candDisqualifyModal').length,
            modalVisible: !!(modal && modal.classList.contains('show') && style && style.display !== 'none'),
            modalClasses: modal ? modal.className : '',
            ariaHidden: modal ? modal.getAttribute('aria-hidden') : null,
            backdropCount: document.querySelectorAll('.modal-backdrop').length,
            bodyClasses: document.body ? document.body.className : '',
            bodyPointerEvents: document.body ? window.getComputedStyle(document.body).pointerEvents : '',
            bodyOverflow: document.body ? window.getComputedStyle(document.body).overflow : '',
            activeTag: active ? active.tagName : '',
            activeId: active ? active.id : '',
            activeText: active ? (active.textContent || active.getAttribute('aria-label') || '').trim() : '',
            loaderVisible: Array.from(document.querySelectorAll('#globalLoader,#appGlobalLoader,#loader,#pageLoader,#loadingOverlay,#overlayLoader')).some((el) => window.getComputedStyle(el).display !== 'none'),
          };
        }"""
    )


def _assert_page_unblocked(page):
    page.wait_for_function(
        """() => {
          const modal = document.querySelector('#candDisqualifyModal');
          const visible = !!(modal && modal.classList.contains('show'));
          return !visible
            && document.querySelectorAll('.modal-backdrop').length === 0
            && !document.body.classList.contains('modal-open')
            && window.getComputedStyle(document.body).pointerEvents !== 'none';
        }""",
        timeout=5000,
    )
    state = _modal_state(page)
    assert state["modalVisible"] is False
    assert state["backdropCount"] == 0
    assert "modal-open" not in state["bodyClasses"]
    assert state["bodyPointerEvents"] != "none"
    assert state["loaderVisible"] is False


def _open_disqualify_modal(page):
    page.locator('button[data-bs-target="#candDisqualifyModal"]').click()
    page.wait_for_selector("#candDisqualifyModal.show", timeout=5000)
    state = _modal_state(page)
    assert state["modalCount"] == 1
    assert state["modalVisible"] is True
    assert state["backdropCount"] == 1
    assert "modal-open" in state["bodyClasses"]
    assert state["ariaHidden"] in (None, "false")
    assert state["loaderVisible"] is False


def _login(page, base_url: str, username: str, password: str) -> None:
    page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
    page.fill('input[name="usuario"]', username)
    page.fill('input[name="clave"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin/**", timeout=12000)


def _interesting_console_errors(messages):
    needles = ("typeerror", "referenceerror", "modal", "focus", "fetch", "networkerror")
    return [
        msg
        for msg in messages
        if msg["type"] == "error" or any(needle in msg["text"].lower() for needle in needles)
    ]


def _unexpected_failed_requests(requests_):
    unexpected = []
    for req in requests_:
        url = req.get("url") or ""
        failure = str(req.get("failure") or "").lower()
        expected_shutdown_abort = (
            "err_aborted" in failure
            and (
                "/admin/live/invalidation/stream" in url
                or "/admin/live/observability" in url
                or "/admin/monitoreo/presence/ping" in url
            )
        )
        if expected_shutdown_abort:
            continue
        unexpected.append(req)
    return unexpected


@pytest.mark.e2e
def test_candidata_disqualify_modal_cancel_escape_save_and_backdrop_cleanup(candidata_disqualify_modal_env):
    base_url = candidata_disqualify_modal_env["base_url"]
    owner_user = candidata_disqualify_modal_env["owner_user"]
    owner_pass = candidata_disqualify_modal_env["owner_pass"]
    fila = int(candidata_disqualify_modal_env["fila"])
    console_messages = []
    failed_requests = []
    disqualify_posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
        page.on("requestfailed", lambda req: failed_requests.append({"method": req.method, "url": req.url, "failure": req.failure}))
        page.on(
            "request",
            lambda req: disqualify_posts.append(req.url)
            if req.method == "POST" and f"/admin/candidatas/{fila}/estado/descalificar" in req.url
            else None,
        )

        _login(page, base_url, owner_user, owner_pass)
        page.goto(f"{base_url}/admin/candidatas/{fila}", wait_until="domcontentloaded")
        page.wait_for_selector('[data-candidata-center]', timeout=12000)
        assert page.locator('button[data-bs-target="#candDisqualifyModal"]').is_enabled()

        _open_disqualify_modal(page)
        page.locator('#candDisqualifyModal button[data-bs-dismiss="modal"]', has_text="Cancelar").click()
        _assert_page_unblocked(page)
        assert disqualify_posts == []
        page.locator('[data-edit-section="personal"] [data-edit-toggle]').click()
        assert "cand-editing" in page.locator('[data-edit-section="personal"]').get_attribute("class")

        _open_disqualify_modal(page)
        page.locator('#candDisqualifyModal .btn-close').click()
        _assert_page_unblocked(page)
        assert disqualify_posts == []

        _open_disqualify_modal(page)
        page.fill("#cand_disqualify_motivo", "Motivo temporal para probar Escape")
        page.keyboard.press("Escape")
        _assert_page_unblocked(page)
        assert disqualify_posts == []

        _open_disqualify_modal(page)
        page.mouse.click(10, 10)
        _assert_page_unblocked(page)
        assert disqualify_posts == []

        _open_disqualify_modal(page)
        page.locator('#candDisqualifyModal button[type="submit"]').click()
        page.wait_for_selector("#candDisqualifyModal.show", timeout=5000)
        assert page.locator('#candDisqualifyModal [data-error-for="motivo"]').inner_text().strip()
        invalid_state = _modal_state(page)
        assert invalid_state["loaderVisible"] is False
        assert invalid_state["backdropCount"] == 1
        assert disqualify_posts == []

        page.fill("#cand_disqualify_motivo", "No cumple perfil E2E modal")
        with page.expect_response(
            lambda resp: resp.request.method == "POST"
            and f"/admin/candidatas/{fila}/estado/descalificar" in resp.url,
            timeout=12000,
        ) as response_info:
            page.locator('#candDisqualifyModal button[type="submit"]').click()
        response = response_info.value
        assert response.status == 200
        _assert_page_unblocked(page)
        assert len(disqualify_posts) == 1
        assert "Descalificada" in page.locator("[data-status-badges]").inner_text()
        page.locator('[data-edit-section="references"] [data-edit-toggle]').click()
        assert "cand-editing" in page.locator('[data-edit-section="references"]').get_attribute("class")

        browser.close()

    assert _unexpected_failed_requests(failed_requests) == []
    assert _interesting_console_errors(console_messages) == []

    with flask_app.app_context():
        cand = Candidata.query.get(fila)
        assert cand is not None
        assert cand.estado == "descalificada"
        assert cand.nota_descalificacion == "No cumple perfil E2E modal"
        audit_count = StaffAuditLog.query.filter_by(entity_type="Candidata", entity_id=str(fila)).count()
        outbox_count = DomainOutbox.query.filter_by(aggregate_type="Candidata", aggregate_id=fila).count()
        assert audit_count >= 1
        assert outbox_count >= 1
