# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import socket
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Candidata, Entrevista, EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta, StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _wait_for_db_value(fetch_fn, *, timeout_sec: float = 5.0, sleep_sec: float = 0.1):
    deadline = time.time() + timeout_sec
    last_value = None
    while time.time() < deadline:
        with flask_app.app_context():
            db.session.remove()
            last_value = fetch_fn()
            if last_value:
                return last_value
        time.sleep(sleep_sec)
    return last_value


def _ensure_staff_user(*, username: str, password: str) -> None:
    user = StaffUser.query.filter_by(username=username).first()
    if user is None:
        user = StaffUser(
            username=username,
            email=f"{username}@test.local",
            role="secretaria",
            is_active=True,
            mfa_enabled=False,
        )
        db.session.add(user)
    user.role = "secretaria"
    user.is_active = True
    user.mfa_enabled = False
    user.set_password(password)


@pytest.fixture()
def candidata_sin_entrevista_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    fila = 990650
    username = "secretaria_e2e_entrevista"
    password = "Secretaria#12345"

    with flask_app.app_context():
        mapped_models = [
            cls
            for mapper in db.Model.registry.mappers
            if (cls := getattr(mapper, "class_", None)) is not None and hasattr(cls, "__table__")
        ]
        ensure_sqlite_compat_tables(mapped_models, reset=True)
        _ensure_staff_user(username=username, password=password)

        cand = Candidata(
            fila=fila,
            nombre_completo="STRESSLOC Maria Fernanda de los Angeles Rodriguez Hernandez",
            codigo="CAN-000001",
            cedula="00199065000",
            numero_telefono="8095550650",
            estado="en_proceso",
            entrevista="",
            contactos_referencias_laborales="FORM-LAB-111",
            referencias_familiares_detalle="FORM-FAM-222",
            referencias_laboral="FORM-LAB-111",
            referencias_familiares="FORM-FAM-222",
        )
        db.session.add(cand)
        db.session.add_all(
            [
                EntrevistaPregunta(
                    clave="domestica.nombre",
                    texto="Nombre completo",
                    tipo="texto",
                    orden=1,
                    activa=True,
                ),
                EntrevistaPregunta(
                    clave="domestica.descripcion_personal",
                    texto="¿Cómo te describes como persona?",
                    tipo="texto_largo",
                    orden=2,
                    activa=True,
                ),
                EntrevistaPregunta(
                    clave="domestica.revision_salida",
                    texto="¿Puedes ser revisada a la salida?",
                    tipo="radio",
                    opciones=["Sí", "No"],
                    orden=3,
                    activa=True,
                ),
                EntrevistaPregunta(
                    clave="domestica.referencia_laboral",
                    texto="Referencia laboral mencionada",
                    tipo="texto",
                    orden=4,
                    activa=True,
                ),
                EntrevistaPregunta(
                    clave="domestica.referencia_familiar",
                    texto="Referencia familiar mencionada",
                    tipo="texto",
                    orden=5,
                    activa=True,
                ),
            ]
        )
        db.session.commit()

    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.7)
            if response.status_code in (200, 404):
                break
        except Exception:
            time.sleep(0.08)

    yield {"base_url": base_url, "fila": fila, "username": username, "password": password}

    server.shutdown()
    thread.join(timeout=3)


@pytest.fixture()
def candidata_descalificada_sin_entrevista_env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    fila = 990651
    username = "secretaria_e2e_entrevista_desc"
    password = "Secretaria#12345"

    with flask_app.app_context():
        mapped_models = [
            cls
            for mapper in db.Model.registry.mappers
            if (cls := getattr(mapper, "class_", None)) is not None and hasattr(cls, "__table__")
        ]
        ensure_sqlite_compat_tables(mapped_models, reset=True)
        _ensure_staff_user(username=username, password=password)

        cand = Candidata(
            fila=fila,
            nombre_completo="STRESSLOC Descalificada Entrevista Bloqueada",
            codigo="CAN-000002",
            cedula="00199065100",
            numero_telefono="8095550651",
            estado="descalificada",
            entrevista="",
        )
        db.session.add(cand)
        db.session.add_all(
            [
                EntrevistaPregunta(
                    clave="domestica.nombre",
                    texto="Nombre completo",
                    tipo="texto",
                    orden=1,
                    activa=True,
                ),
                EntrevistaPregunta(
                    clave="domestica.descripcion_personal",
                    texto="¿Cómo te describes como persona?",
                    tipo="texto_largo",
                    orden=2,
                    activa=True,
                ),
            ]
        )
        db.session.commit()
        active_questions_count = EntrevistaPregunta.query.filter(
            EntrevistaPregunta.activa.is_(True),
            EntrevistaPregunta.clave.like("domestica.%"),
        ).count()

    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.7)
            if response.status_code in (200, 404):
                break
        except Exception:
            time.sleep(0.08)

    yield {
        "base_url": base_url,
        "fila": fila,
        "username": username,
        "password": password,
        "active_questions_count": active_questions_count,
    }

    server.shutdown()
    thread.join(timeout=3)


@pytest.mark.e2e
def test_candidata_sin_entrevista_crea_domestica_desde_ficha_y_regresa(candidata_sin_entrevista_env):
    base_url = candidata_sin_entrevista_env["base_url"]
    fila = int(candidata_sin_entrevista_env["fila"])
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}"),
        )

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', candidata_sin_entrevista_env["username"])
        page.fill('input[name="clave"]', candidata_sin_entrevista_env["password"])
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r".*/(home|admin).*"), timeout=12000)

        page.goto(f"{base_url}/admin/candidatas/{fila}", wait_until="domcontentloaded")
        entrevistas_panel = page.locator("#entrevistas")
        assert "Total" in entrevistas_panel.inner_text()
        assert re.search(r"Total\s+0", entrevistas_panel.inner_text())
        assert "FORM-LAB-111" in page.locator("#referencias").inner_text()
        assert "FORM-FAM-222" in page.locator("#referencias").inner_text()

        page.get_by_role("link", name=re.compile(r"Nueva entrevista doméstica", re.I)).click()
        page.wait_for_url(f"**/entrevistas/nueva/{fila}/domestica?next=/admin/candidatas/{fila}", timeout=12000)
        assert page.get_by_text("Nueva entrevista").is_visible()
        assert page.get_by_text("STRESSLOC Maria Fernanda de los Angeles Rodriguez Hernandez").is_visible()
        assert page.get_by_text("CAN-000001").is_visible()
        assert page.get_by_text(re.compile(r"Tipo:\s*Domestica", re.I)).is_visible()
        assert page.locator('input[type="text"][name^="q_"], textarea[name^="q_"]').count() == 4
        assert page.locator('input[type="radio"][name^="q_"]').count() == 2

        page.evaluate(
            """() => {
              for (const el of document.querySelectorAll('input[type="text"][name^="q_"], textarea[name^="q_"]')) {
                el.value = `Respuesta valida ${el.name}`;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
              }
              const radio = document.querySelector('input[type="radio"][name^="q_"][value="Sí"]')
                || document.querySelector('input[type="radio"][name^="q_"]');
              if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }"""
        )
        page.evaluate(
            """() => {
              for (const field of document.querySelectorAll('.field')) {
                const label = field.querySelector('label')?.textContent?.trim() || '';
                const input = field.querySelector('input[type="text"][name^="q_"], textarea[name^="q_"]');
                if (!input) continue;
                if (label.includes('Referencia laboral mencionada')) input.value = 'INT-LAB-333';
                if (label.includes('Referencia familiar mencionada')) input.value = 'INT-FAM-444';
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }"""
        )
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and f"/entrevistas/nueva/{fila}/domestica" in resp.url,
            timeout=12000,
        ):
            page.get_by_role("button", name=re.compile(r"Guardar entrevista", re.I)).click()

        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=12000)
        persisted = _wait_for_db_value(
            lambda: Entrevista.query.filter_by(candidata_id=fila, tipo="domestica").first()
        )
        assert persisted is not None
        respuestas_count = _wait_for_db_value(
            lambda: EntrevistaRespuesta.query.filter_by(entrevista_id=int(persisted.id)).count()
        )
        assert respuestas_count == 5
        referencias_count = _wait_for_db_value(
            lambda: EntrevistaReferencia.query.filter_by(entrevista_id=int(persisted.id)).count()
        )
        assert referencias_count == 2

        entrevistas_panel = page.locator("#entrevistas")
        text = entrevistas_panel.inner_text()
        assert re.search(r"Total\s+1", text)
        assert "domestica" in text
        assert "completa" in text
        assert "Cargando entrevistas recientes..." in text
        fragment = page.locator("#candidataInterviewsRecentAsyncRegion")
        fragment.scroll_into_view_if_needed()
        fragment_url = fragment.get_attribute("data-admin-lazy-fragment-url")
        assert fragment_url
        fragment_html = page.evaluate(
            """async (url) => {
              const resp = await fetch(url, {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/html,*/*'}
              });
              return await resp.text();
            }""",
            fragment_url,
        )
        fragment_text = fragment_html
        assert "Leer respuestas" in fragment_text
        referencias_text = page.locator("#referencias").inner_text()
        assert "Referencias y entrevista" in referencias_text
        assert "Formulario, entrevista y respuestas registradas." in referencias_text
        assert "Referencias del formulario" in referencias_text
        assert "Referencias de entrevista" in referencias_text
        assert "FORM-LAB-111" in referencias_text
        assert "FORM-FAM-222" in referencias_text
        assert "Referencias registradas durante entrevista" in fragment_text
        assert "INT-LAB-333" in fragment_text
        assert "INT-FAM-444" in fragment_text

        with flask_app.app_context():
            db.session.remove()
            cand = Candidata.query.get(fila)
            assert cand.contactos_referencias_laborales == "FORM-LAB-111"
            assert cand.referencias_familiares_detalle == "FORM-FAM-222"
            refs = [
                (r.tipo, r.texto)
                for r in EntrevistaReferencia.query.filter_by(entrevista_id=int(persisted.id)).order_by(EntrevistaReferencia.tipo.asc()).all()
            ]
            assert refs == [("familiar", "INT-FAM-444"), ("laboral", "INT-LAB-333")]
            ref_answers = [
                r.respuesta
                for r in EntrevistaRespuesta.query.join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
                .filter(EntrevistaRespuesta.entrevista_id == int(persisted.id))
                .filter(EntrevistaPregunta.clave.in_(["domestica.referencia_laboral", "domestica.referencia_familiar"]))
                .order_by(EntrevistaPregunta.orden.asc())
                .all()
            ]
            assert ref_answers == ["INT-LAB-333", "INT-FAM-444"]

        page.get_by_role("button", name=re.compile(r"Editar entrevista", re.I)).click()
        page.fill("#cand_ref_int_lab", "FORM-LAB-555")
        page.fill("#cand_ref_int_fam", "FORM-FAM-666")
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and f"/admin/candidatas/{fila}/referencias" in resp.url,
            timeout=12000,
        ):
            page.get_by_role("button", name=re.compile(r"Guardar entrevista", re.I)).click()
        page.wait_for_timeout(250)

        with flask_app.app_context():
            db.session.remove()
            cand = Candidata.query.get(fila)
            assert cand.contactos_referencias_laborales == "FORM-LAB-111"
            assert cand.referencias_familiares_detalle == "FORM-FAM-222"
            assert cand.referencias_laboral == "FORM-LAB-555"
            assert cand.referencias_familiares == "FORM-FAM-666"
            refs_after_form_edit = [
                (r.tipo, r.texto)
                for r in EntrevistaReferencia.query.filter_by(entrevista_id=int(persisted.id)).order_by(EntrevistaReferencia.tipo.asc()).all()
            ]
            assert refs_after_form_edit == [("familiar", "INT-FAM-444"), ("laboral", "INT-LAB-333")]
            ref_answers_after_form_edit = [
                r.respuesta
                for r in EntrevistaRespuesta.query.join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
                .filter(EntrevistaRespuesta.entrevista_id == int(persisted.id))
                .filter(EntrevistaPregunta.clave.in_(["domestica.referencia_laboral", "domestica.referencia_familiar"]))
                .order_by(EntrevistaPregunta.orden.asc())
                .all()
            ]
            assert ref_answers_after_form_edit == ["INT-LAB-333", "INT-FAM-444"]

        page.goto(
            f"{base_url}/entrevistas/editar/{int(persisted.id)}?next=/admin/candidatas/{fila}",
            wait_until="domcontentloaded",
        )
        page.evaluate(
            """() => {
              for (const field of document.querySelectorAll('.field')) {
                const label = field.querySelector('label')?.textContent?.trim() || '';
                const input = field.querySelector('input[type="text"][name^="q_"], textarea[name^="q_"]');
                if (!input) continue;
                if (label.includes('Referencia laboral mencionada')) input.value = 'INT-LAB-777';
                if (label.includes('Referencia familiar mencionada')) input.value = 'INT-FAM-888';
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }"""
        )
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and f"/entrevistas/editar/{int(persisted.id)}" in resp.url,
            timeout=12000,
        ):
            page.get_by_role("button", name=re.compile(r"Guardar cambios", re.I)).click()
        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=12000)

        with flask_app.app_context():
            db.session.remove()
            cand = Candidata.query.get(fila)
            assert cand.contactos_referencias_laborales == "FORM-LAB-111"
            assert cand.referencias_familiares_detalle == "FORM-FAM-222"
            assert cand.referencias_laboral == "FORM-LAB-555"
            assert cand.referencias_familiares == "FORM-FAM-666"
            refs_after_edit = [
                (r.tipo, r.texto)
                for r in EntrevistaReferencia.query.filter_by(entrevista_id=int(persisted.id)).order_by(EntrevistaReferencia.tipo.asc()).all()
            ]
            assert refs_after_edit == [("familiar", "INT-FAM-888"), ("laboral", "INT-LAB-777")]
            ref_answers_after_interview_edit = [
                r.respuesta
                for r in EntrevistaRespuesta.query.join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
                .filter(EntrevistaRespuesta.entrevista_id == int(persisted.id))
                .filter(EntrevistaPregunta.clave.in_(["domestica.referencia_laboral", "domestica.referencia_familiar"]))
                .order_by(EntrevistaPregunta.orden.asc())
                .all()
            ]
            assert ref_answers_after_interview_edit == ["INT-LAB-777", "INT-FAM-888"]

        page.goto(f"{base_url}/entrevistas/candidata/{fila}?next=/admin/candidatas/{fila}", wait_until="domcontentloaded")
        page.get_by_role("link", name=re.compile(r"\+\s*Doméstica", re.I)).click()
        page.wait_for_url(f"**/entrevistas/nueva/{fila}/domestica?next=/admin/candidatas/{fila}", timeout=12000)
        assert page.get_by_text("Nueva entrevista").is_visible()
        assert page.get_by_text("¿Cómo te describes como persona?").is_visible()
        page.evaluate(
            """() => {
              for (const el of document.querySelectorAll('input[type="text"][name^="q_"], textarea[name^="q_"]')) {
                el.value = `Segunda respuesta ${el.name}`;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
              }
              for (const field of document.querySelectorAll('.field')) {
                const label = field.querySelector('label')?.textContent?.trim() || '';
                const input = field.querySelector('input[type="text"][name^="q_"], textarea[name^="q_"]');
                if (!input) continue;
                if (label.includes('Referencia laboral mencionada')) input.value = 'INT2-LAB-999';
                if (label.includes('Referencia familiar mencionada')) input.value = 'INT2-FAM-000';
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
              }
              const radio = document.querySelector('input[type="radio"][name^="q_"][value="Sí"]')
                || document.querySelector('input[type="radio"][name^="q_"]');
              if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }"""
        )
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and f"/entrevistas/nueva/{fila}/domestica" in resp.url,
            timeout=12000,
        ):
            page.get_by_role("button", name=re.compile(r"Guardar entrevista", re.I)).click()
        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=12000)

        final_refs_text = page.locator("#referencias").inner_text()
        final_interviews_text = page.locator("#entrevistas").inner_text()
        assert "FORM-LAB-555" in final_refs_text
        assert "FORM-FAM-666" in final_refs_text
        assert re.search(r"Total\s+2", final_interviews_text)
        assert "domestica" in final_interviews_text
        assert "completa" in final_interviews_text

        with flask_app.app_context():
            db.session.remove()
            cand = Candidata.query.get(fila)
            assert cand.contactos_referencias_laborales == "FORM-LAB-111"
            assert cand.referencias_familiares_detalle == "FORM-FAM-222"
            interviews = (
                Entrevista.query
                .filter_by(candidata_id=fila, tipo="domestica")
                .order_by(Entrevista.id.asc())
                .all()
            )
            assert len(interviews) == 2
            answers_by_interview = {}
            for interview in interviews:
                answers_by_interview[int(interview.id)] = [
                    r.respuesta
                    for r in EntrevistaRespuesta.query.join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
                    .filter(EntrevistaRespuesta.entrevista_id == int(interview.id))
                    .filter(EntrevistaPregunta.clave.in_(["domestica.referencia_laboral", "domestica.referencia_familiar"]))
                    .order_by(EntrevistaPregunta.orden.asc())
                    .all()
                ]
            assert answers_by_interview[int(interviews[0].id)] == ["INT-LAB-777", "INT-FAM-888"]
            assert answers_by_interview[int(interviews[1].id)] == ["INT2-LAB-999", "INT2-FAM-000"]

        browser.close()

    same_origin_failures = [
        item
        for item in failed_requests
        if item.startswith(("GET " + base_url, "POST " + base_url))
        and "/admin/live/invalidation/stream" not in item
        and "/admin/live/invalidation/poll" not in item
        and "/admin/chat/badge.json" not in item
        and "/admin/seguimiento-candidatas/badge.json" not in item
        and "/admin/monitoreo/presence/ping" not in item
        and "/admin/live/observability" not in item
    ]
    critical_console_errors = [
        item for item in console_errors if "429 (TOO MANY REQUESTS)" not in item
    ]
    assert page_errors == []
    assert same_origin_failures == []
    assert critical_console_errors == []


@pytest.mark.e2e
def test_candidata_descalificada_bloquea_domestica_con_razon_y_sin_crear(
    candidata_descalificada_sin_entrevista_env,
):
    base_url = candidata_descalificada_sin_entrevista_env["base_url"]
    fila = int(candidata_descalificada_sin_entrevista_env["fila"])
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    assert int(candidata_descalificada_sin_entrevista_env["active_questions_count"]) > 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}"),
        )

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', candidata_descalificada_sin_entrevista_env["username"])
        page.fill('input[name="clave"]', candidata_descalificada_sin_entrevista_env["password"])
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r".*/(home|admin).*"), timeout=12000)

        page.goto(f"{base_url}/admin/candidatas/{fila}", wait_until="domcontentloaded")
        entrevistas_panel = page.locator("#entrevistas")
        assert re.search(r"Total\s+0", entrevistas_panel.inner_text())
        assert page.get_by_role("link", name=re.compile(r"Nueva entrevista doméstica", re.I)).count() == 0
        disabled = page.get_by_role("button", name=re.compile(r"Nueva entrevista doméstica", re.I))
        assert disabled.is_visible()
        assert disabled.is_disabled()
        assert page.get_by_text("No se puede crear una entrevista mientras la candidata esté descalificada.").is_visible()

        page.goto(f"{base_url}/entrevistas/candidata/{fila}?next=/admin/candidatas/{fila}", wait_until="domcontentloaded")
        assert page.get_by_role("link", name=re.compile(r"\+\s*Doméstica", re.I)).count() == 0
        list_disabled = page.get_by_role("button", name=re.compile(r"\+\s*Doméstica", re.I))
        assert list_disabled.is_visible()
        assert list_disabled.is_disabled()
        assert page.get_by_text("No se puede crear una entrevista mientras la candidata esté descalificada.").is_visible()

        response = page.goto(
            f"{base_url}/entrevistas/nueva/{fila}/domestica?next=/admin/candidatas/{fila}",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 200
        assert f"/entrevistas/candidata/{fila}" in page.url
        assert page.locator(".alert.alert-warning").get_by_text(
            "No se puede crear una entrevista mientras la candidata esté descalificada."
        ).is_visible()

        browser.close()

    with flask_app.app_context():
        assert Entrevista.query.filter_by(candidata_id=fila).count() == 0

    same_origin_failures = [
        item
        for item in failed_requests
        if item.startswith(("GET " + base_url, "POST " + base_url))
        and "/admin/live/invalidation/stream" not in item
        and "/admin/live/invalidation/poll" not in item
        and "/admin/chat/badge.json" not in item
        and "/admin/seguimiento-candidatas/badge.json" not in item
        and "/admin/monitoreo/presence/ping" not in item
        and "/admin/live/observability" not in item
    ]
    critical_console_errors = [
        item for item in console_errors if "429 (TOO MANY REQUESTS)" not in item
    ]
    assert page_errors == []
    assert same_origin_failures == []
    assert critical_console_errors == []
