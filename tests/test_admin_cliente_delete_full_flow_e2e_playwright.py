# -*- coding: utf-8 -*-
from __future__ import annotations

import socket
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Candidata, Cliente, PagoSolicitud, Solicitud, StaffUser
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


def _wait_for_db_row(fetch_fn, *, timeout_sec: float = 5.0, sleep_sec: float = 0.1):
    deadline = time.time() + timeout_sec
    last_value = None
    while time.time() < deadline:
        with flask_app.app_context():
            db.session.remove()
            last_value = fetch_fn()
            if last_value is not None:
                return last_value
        time.sleep(sleep_sec)
    return last_value


@pytest.fixture()
def delete_full_flow_env():
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
            username="owner_e2e_delete_flow",
            email="owner_e2e_delete_flow@test.local",
            role="owner",
            password="Owner#12345",
        )

        candidata = Candidata(
            nombre_completo="Candidata E2E Delete Flow",
            cedula="00112345678",
            numero_telefono="8095554444",
            estado="lista_para_trabajar",
        )
        db.session.add(candidata)
        db.session.commit()

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
        "owner_user": "owner_e2e_delete_flow",
        "owner_pass": "Owner#12345",
        "candidata_id": candidata_id,
        "cliente_codigo": "CL-E2E-DEL-001",
        "cliente_nombre": "Cliente E2E Delete Flow",
        "cliente_email": "cliente.e2e.delete.flow@example.com",
        "cliente_telefono": "8095553333",
    }

    server.shutdown()
    thread.join(timeout=3)


def test_owner_can_create_pay_and_delete_cliente_full_flow(delete_full_flow_env):
    base_url = delete_full_flow_env["base_url"]
    owner_user = delete_full_flow_env["owner_user"]
    owner_pass = delete_full_flow_env["owner_pass"]
    candidata_id = delete_full_flow_env["candidata_id"]
    cliente_codigo = delete_full_flow_env["cliente_codigo"]
    cliente_nombre = delete_full_flow_env["cliente_nombre"]
    cliente_email = delete_full_flow_env["cliente_email"]
    cliente_telefono = delete_full_flow_env["cliente_telefono"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', owner_user)
        page.fill('input[name="clave"]', owner_pass)
        page.click('button[type="submit"]')
        page.wait_for_url("**/admin/**", timeout=12000)

        page.goto(f"{base_url}/admin/clientes/nuevo", wait_until="domcontentloaded")
        page.fill('input[name="codigo"]', cliente_codigo)
        page.fill('input[name="nombre_completo"]', cliente_nombre)
        page.fill('input[name="email"]', cliente_email)
        page.fill('input[name="telefono"]', cliente_telefono)
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and resp.url.endswith("/admin/clientes/nuevo"),
            timeout=12000,
        ):
            page.click("#btn-submit")
        page.wait_for_url("**/admin/clientes", timeout=12000)
        cliente = _wait_for_db_row(
            lambda: Cliente.query.filter_by(codigo=cliente_codigo).first()
        )
        assert cliente is not None
        cliente_id = int(cliente.id)

        page.goto(f"{base_url}/admin/clientes/{cliente_id}/solicitudes/nueva", wait_until="domcontentloaded")
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and "/solicitudes/nueva" in resp.url,
            timeout=12000,
        ):
            page.evaluate(
                """() => {
                const set = (sel, val) => {
                  const el = document.querySelector(sel);
                  if (!el) return;
                  el.value = val;
                  el.dispatchEvent(new Event("input", { bubbles: true }));
                  el.dispatchEvent(new Event("change", { bubbles: true }));
                };
                const check = (sel) => {
                  const el = document.querySelector(sel);
                  if (!el) return;
                  el.checked = true;
                  el.dispatchEvent(new Event("change", { bubbles: true }));
                };
                const setSelectFirstRealValue = (sel) => {
                  const el = document.querySelector(sel);
                  if (!el || !el.options || el.options.length < 2) return;
                  el.value = el.options[1].value;
                  el.dispatchEvent(new Event("change", { bubbles: true }));
                };

                set('select[name="tipo_servicio"]', 'DOMESTICA_LIMPIEZA');
                set('input[name="ciudad_sector"]', 'Santiago / Los Jardines');
                set('input[name="ciudad_input_ui"]', 'Santiago');
                set('input[name="sector_input_ui"]', 'Los Jardines');
                set('#modalidad_trabajo_hidden', 'Salida diaria');
                check('input[name="modalidad_grupo"][value="con_salida_diaria"]');
                setSelectFirstRealValue('select[name="modalidad_especifica"]');
                set('#horario_hidden', 'Lunes a viernes, de 08:00 a 17:00');
                set('input[name="horario_dias_trabajo"]', 'Lunes a viernes');
                set('input[name="horario_hora_entrada"]', '08:00');
                set('input[name="horario_hora_salida"]', '17:00');
                check('input[name="funciones"][value="limpieza"]');
                set('textarea[name="experiencia"]', 'Experiencia en limpieza general de casas.');
                set('select[name="tipo_lugar"]', 'casa');
                set('#habitaciones_hidden', '3');
                    set('#banos_hidden', '2');
                    set('input[name="adultos"]', '2');
                    set('input[name="sueldo"]', '18000');
                    set('#pasaje_aporte_hidden', '0');
                    check('input[name="pasaje_mode"][value="incluido"]');
                    document.querySelector('#solicitud-form').requestSubmit();
                }"""
                )
        try:
            page.wait_for_url(f"**/admin/clientes/{cliente_id}*", timeout=12000)
        except Exception as exc:
            invalid_feedback = [
                txt.strip()
                for txt in page.locator(".invalid-feedback").all_inner_texts()
                if txt.strip()
            ]
            alerts = [
                txt.strip()
                for txt in page.locator(".alert").all_inner_texts()
                if txt.strip()
            ]
            with flask_app.app_context():
                db.session.remove()
                persisted_ids = [
                    int(row.id)
                    for row in Solicitud.query.filter_by(cliente_id=cliente_id).order_by(Solicitud.id.asc()).all()
                ]
            raise AssertionError(
                "Solicitud no creada; "
                f"errores renderizados: {invalid_feedback}; "
                f"alerts: {alerts}; "
                f"solicitudes_en_bd: {persisted_ids}"
            ) from exc
        solicitud_row = page.locator("tr[id^='sol-']").first
        solicitud_id = int((solicitud_row.get_attribute("id") or "sol-0").split("-")[-1])

        page.goto(
            f"{base_url}/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan",
            wait_until="domcontentloaded",
        )
        page.select_option('select[name="tipo_plan"]', "basico")
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and resp.url.endswith(f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan"),
            timeout=12000,
        ) as plan_response_info:
            page.click('button[name="plan_action"][value="update"]')
        plan_response = plan_response_info.value
        assert plan_response.status == 200
        plan_payload = plan_response.json()
        assert plan_payload["success"] is True
        assert "Plan guardado" in plan_payload["message"]

        plan_payments = _wait_for_db_row(
            lambda: (
                PagoSolicitud.query
                .filter_by(cliente_id=cliente_id, solicitud_id=solicitud_id)
                .order_by(PagoSolicitud.id.asc())
                .all()
            )
            or None
        )
        assert plan_payments is not None
        assert len(plan_payments) == 1
        assert str(plan_payments[0].tipo_pago) == "abono"

        page.goto(
            (
                f"{base_url}/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago"
                f"?q=Candidata%20E2E%20Delete%20Flow&next=/admin/clientes/{cliente_id}"
            ),
            wait_until="domcontentloaded",
        )
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and f"/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/pago" in resp.url,
            timeout=12000,
        ) as pago_response_info:
            page.evaluate(
                f"""(candidataId) => {{
                const select = document.querySelector('select[name="candidata_id"]');
                if (!select) throw new Error('selector candidata_id no encontrado');
                select.value = String(candidataId);
                select.dispatchEvent(new Event("change", {{ bubbles: true }}));

                const saldo = document.querySelector('#mode_saldo');
                const full = document.querySelector('#mode_full');
                const target = saldo || full;
                if (!target) throw new Error('modo de pago no disponible');
                target.checked = true;
                target.dispatchEvent(new Event("change", {{ bubbles: true }}));

                document.querySelector('#pago-form').requestSubmit();
            }}""",
                candidata_id,
            )
        pago_response = pago_response_info.value
        assert pago_response.status == 200
        pago_payload = pago_response.json()
        assert pago_payload["success"] is True
        assert "Pago registrado correctamente" in pago_payload["message"]

        pagos = _wait_for_db_row(
            lambda: (
                PagoSolicitud.query.filter_by(cliente_id=cliente_id)
                .order_by(PagoSolicitud.id.asc())
                .all()
            )
            or None
        )
        assert pagos is not None
        assert len(pagos) >= 2

        page.goto(f"{base_url}/admin/clientes/{cliente_id}", wait_until="domcontentloaded")
        page.click('[data-bs-target="#deleteClienteModal"]')
        page.wait_for_selector("#deleteClienteModal.show", timeout=12000)
        page.evaluate(
            f"""(codigo) => {{
                document.querySelector('#deleteClienteModal input[name="next"]').value = '/admin/clientes?q=' + encodeURIComponent(codigo);
            }}""",
            cliente_codigo,
        )
        page.fill("#deleteClienteModal #delete_cliente_confirm_input", cliente_codigo)
        page.evaluate(
            """(codigo) => {
                const cleanup = document.querySelector('#deleteClienteModal input[name="confirm_full_cleanup"]');
                if (!cleanup) throw new Error('checkbox cleanup no encontrado');
                cleanup.checked = true;
                cleanup.dispatchEvent(new Event('change', { bubbles: true }));

                const code = document.querySelector('#deleteClienteModal input[name="cleanup_confirmation_code"]');
                if (!code) throw new Error('input cleanup_confirmation_code no encontrado');
                code.value = codigo;
                code.dispatchEvent(new Event('input', { bubbles: true }));
                code.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            cliente_codigo,
        )

        captured = {}

        def on_request(req):
            if req.method == "POST" and req.url.endswith(f"/admin/clientes/{cliente_id}/eliminar"):
                captured["url"] = req.url
                captured["post_data"] = req.post_data or ""

        page.on("request", on_request)
        with page.expect_response(
            lambda resp: resp.request.method == "POST" and resp.url.endswith(f"/admin/clientes/{cliente_id}/eliminar"),
            timeout=12000,
        ):
            page.evaluate(
                """() => {
                    const form = document.querySelector('#deleteClienteModal form');
                    if (!form) throw new Error('formulario deleteClienteModal no encontrado');
                    form.requestSubmit();
                }"""
            )
        page.wait_for_url(f"**/admin/clientes?q={cliente_codigo}", timeout=12000)

        assert captured["url"].endswith(f"/admin/clientes/{cliente_id}/eliminar")
        assert "confirm_full_cleanup=1" in captured["post_data"]
        assert f"cleanup_confirmation_code={cliente_codigo}" in captured["post_data"]

        with flask_app.app_context():
            assert db.session.get(Cliente, cliente_id) is None
            assert db.session.get(Solicitud, solicitud_id) is None
            assert PagoSolicitud.query.filter_by(cliente_id=cliente_id).count() == 0

        assert page.locator(f"a[href='/admin/clientes/{cliente_id}']").count() == 0
        assert page.locator(f"text={cliente_nombre}").count() == 0

        browser.close()
