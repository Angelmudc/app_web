# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass

import pytest
import requests
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Cliente, ClienteNotificacion, DomainOutbox, Solicitud, StaffUser
from utils.timezone import utc_now_naive

if "sqlite" in str(os.getenv("DATABASE_URL", "")).lower():
    pytest.skip("E2E Playwright requiere una BD real (PostgreSQL) para este proyecto.", allow_module_level=True)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _new_outbox(event_type: str, payload: dict, *, aggregate_type: str = "Solicitud", aggregate_id: str = "0") -> DomainOutbox:
    now = utc_now_naive()
    return DomainOutbox(
        event_id=f"evt_e2e_{uuid.uuid4().hex[:18]}",
        event_type=str(event_type or "").strip().upper(),
        aggregate_type=str(aggregate_type or "Solicitud"),
        aggregate_id=str(aggregate_id or "0"),
        aggregate_version=1,
        occurred_at=now,
        actor_id="staff:e2e",
        region="admin",
        payload=dict(payload or {}),
        schema_version=1,
        created_at=now,
    )


@dataclass
class LiveFixture:
    base_url: str
    cliente_id: int
    solicitud_id: int
    client_username: str
    client_password: str
    admin_username: str
    admin_password: str

    def admin_session(self) -> requests.Session:
        sess = requests.Session()
        resp = sess.post(
            f"{self.base_url}/admin/login",
            data={"usuario": self.admin_username, "clave": self.admin_password},
            timeout=12,
            allow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        return sess

    def create_admin_solicitud(self, *, suffix: str) -> tuple[int, str]:
        sess = self.admin_session()
        payload = {
            "tipo_servicio": "DOMESTICA_LIMPIEZA",
            "ciudad_sector": f"Santiago Centro {suffix}",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Con dormida",
            "experiencia": f"Experiencia operativa {suffix}",
            "horario": "L-V 8:00-5:00",
            "funciones": ["limpieza"],
            "edad_requerida": ["26-35"],
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "1",
            "adultos": "2",
            "ninos": "0",
            "sueldo": "22000",
            "pasaje_aporte": "0",
            "areas_comunes": ["sala"],
            "nota_cliente": f"nota {suffix}",
        }
        resp = sess.post(
            f"{self.base_url}/admin/clientes/{self.cliente_id}/solicitudes/nueva",
            data=payload,
            timeout=15,
            allow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        with flask_app.app_context():
            row = (
                Solicitud.query
                .filter_by(cliente_id=self.cliente_id)
                .order_by(Solicitud.id.desc())
                .first()
            )
            assert row is not None
            return int(row.id), str(getattr(row, "codigo_solicitud", "") or "")

    def edit_admin_solicitud(self, solicitud_id: int, *, experiencia: str) -> None:
        sess = self.admin_session()
        with flask_app.app_context():
            row = Solicitud.query.filter_by(id=int(solicitud_id), cliente_id=self.cliente_id).first()
            assert row is not None
            payload = {
                "tipo_servicio": str(getattr(row, "tipo_servicio", "") or "DOMESTICA_LIMPIEZA"),
                "ciudad_sector": str(getattr(row, "ciudad_sector", "") or "Santiago"),
                "rutas_cercanas": str(getattr(row, "rutas_cercanas", "") or "Ruta K"),
                "modalidad_trabajo": str(getattr(row, "modalidad_trabajo", "") or "Con dormida"),
                "experiencia": str(experiencia),
                "horario": str(getattr(row, "horario", "") or "L-V 8:00-5:00"),
                "funciones": list(getattr(row, "funciones", None) or ["limpieza"]),
                "edad_requerida": list(getattr(row, "edad_requerida", None) or ["26-35"]),
                "tipo_lugar": str(getattr(row, "tipo_lugar", "") or "casa"),
                "habitaciones": str(getattr(row, "habitaciones", 2) or 2),
                "banos": str(getattr(row, "banos", 1) or 1),
                "adultos": str(getattr(row, "adultos", 2) or 2),
                "ninos": str(getattr(row, "ninos", 0) or 0),
                "sueldo": str(getattr(row, "sueldo", "22000") or "22000"),
                "pasaje_aporte": "1" if bool(getattr(row, "pasaje_aporte", False)) else "0",
                "areas_comunes": list(getattr(row, "areas_comunes", None) or ["sala"]),
                "nota_cliente": str(getattr(row, "nota_cliente", "") or ""),
            }
        resp = sess.post(
            f"{self.base_url}/admin/clientes/{self.cliente_id}/solicitudes/{int(solicitud_id)}/editar",
            data=payload,
            timeout=15,
            allow_redirects=False,
        )
        assert resp.status_code in (302, 303)

    def push_notificacion(self, *, title: str, body: str) -> int:
        with flask_app.app_context():
            notif = ClienteNotificacion(
                cliente_id=int(self.cliente_id),
                solicitud_id=int(self.solicitud_id),
                tipo="candidatas_enviadas",
                titulo=str(title),
                cuerpo=str(body),
                payload={"count": 1},
                is_read=False,
                is_deleted=False,
            )
            db.session.add(notif)
            db.session.flush()
            db.session.add(
                _new_outbox(
                    "CLIENTE_NOTIFICACION_CREATED",
                    {
                        "cliente_id": int(self.cliente_id),
                        "solicitud_id": int(self.solicitud_id),
                        "notificacion_id": int(notif.id),
                        "tipo": "candidatas_enviadas",
                    },
                    aggregate_type="ClienteNotificacion",
                    aggregate_id=str(notif.id),
                )
            )
            db.session.commit()
            return int(notif.id)

    def push_solicitud_updated_event(self, solicitud_id: int) -> int:
        with flask_app.app_context():
            evt = _new_outbox(
                "CLIENTE_SOLICITUD_UPDATED",
                {
                    "cliente_id": int(self.cliente_id),
                    "solicitud_id": int(solicitud_id),
                    "estado": "proceso",
                },
                aggregate_type="Solicitud",
                aggregate_id=str(solicitud_id),
            )
            db.session.add(evt)
            db.session.flush()
            db.session.commit()
            return int(getattr(evt, "id", 0) or 0)

    def push_solicitud_updated_event(self, solicitud_id: int) -> int:
        with flask_app.app_context():
            evt = _new_outbox(
                "CLIENTE_SOLICITUD_UPDATED",
                {
                    "cliente_id": int(self.cliente_id),
                    "solicitud_id": int(solicitud_id),
                    "estado": "proceso",
                },
                aggregate_type="Solicitud",
                aggregate_id=str(solicitud_id),
            )
            db.session.add(evt)
            db.session.flush()
            db.session.commit()
            return int(getattr(evt, "id", 0) or 0)


@pytest.fixture()
def live_env() -> LiveFixture:
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )

    with flask_app.app_context():
        db.drop_all()
        db.create_all()

        admin = StaffUser(username="e2e_admin", email="e2e_admin@test.local", role="admin", is_active=True, mfa_enabled=False)
        admin.password_hash = generate_password_hash("admin12345", method="pbkdf2:sha256")
        db.session.add(admin)

        cliente = Cliente(
            codigo="CL-E2E-001",
            nombre_completo="Cliente E2E",
            email="cliente_e2e@test.local",
            telefono="8090000001",
            username="cliente_e2e",
            password_hash=generate_password_hash("cliente12345", method="pbkdf2:sha256"),
            is_active=True,
            role="cliente",
            total_solicitudes=1,
        )
        db.session.add(cliente)
        db.session.flush()

        solicitud = Solicitud(
            cliente_id=int(cliente.id),
            codigo_solicitud="SOL-E2E-001",
            estado="proceso",
            ciudad_sector="Santiago Centro",
            modalidad_trabajo="Con dormida",
            experiencia="Experiencia inicial E2E",
            horario="L-V 8:00-5:00",
            tipo_lugar="casa",
            habitaciones=2,
            banos=1,
            adultos=2,
            ninos=0,
            funciones=["limpieza"],
            edad_requerida=["26-35"],
            sueldo="20000",
            pasaje_aporte=False,
            areas_comunes=["sala"],
        )
        db.session.add(solicitud)
        db.session.commit()

        cliente_id = int(cliente.id)
        solicitud_id = int(solicitud.id)

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

    yield LiveFixture(
        base_url=base_url,
        cliente_id=cliente_id,
        solicitud_id=solicitud_id,
        client_username="cliente_e2e",
        client_password="cliente12345",
        admin_username="e2e_admin",
        admin_password="admin12345",
    )

    server.shutdown()
    thread.join(timeout=3)


def _client_login(page, fx: LiveFixture):
    page.goto(f"{fx.base_url}/clientes/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', fx.client_username)
    page.fill('input[name="password"]', fx.client_password)
    page.click('button[type="submit"]')
    page.wait_for_url("**/clientes/dashboard", timeout=12000)


def _value_for_filter_q(page) -> str:
    return page.eval_on_selector('input[name="q"]', "el => el.value")


def _listar_total(page) -> int:
    return int(page.evaluate(
        """() => {
          const el = Array.from(document.querySelectorAll('.card-footer .small.text-muted'))
            .find((x) => (x.innerText || '').includes('de '));
          if (!el) return 0;
          const m = (el.innerText || '').match(/de\\s+(\\d+)/);
          return m ? Number(m[1]) : 0;
        }"""
    ) or 0)


@pytest.mark.e2e
def test_cliente_live_e2e_a_dashboard_reacciona_sin_f5(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        _client_login(page, live_env)

        before_codes = page.locator('[data-client-live-view="dashboard"] a[href*="/clientes/solicitudes/"]').all_inner_texts()
        _, new_code = live_env.create_admin_solicitud(suffix="A")

        page.wait_for_function(
            "(code) => document.body.innerText.includes(code)",
            arg=new_code,
            timeout=15000,
        )
        after_codes = page.locator('[data-client-live-view="dashboard"] a[href*="/clientes/solicitudes/"]').all_inner_texts()
        assert new_code in "".join(after_codes)
        assert len(after_codes) >= len(before_codes)
        browser.close()


@pytest.mark.e2e
def test_cliente_live_e2e_b_lista_reacciona_creacion_admin_sin_f5(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        _client_login(page, live_env)
        page.goto(f"{live_env.base_url}/clientes/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector('[data-client-live-view="solicitudes_list"]')

        total_before = _listar_total(page)
        _, new_code = live_env.create_admin_solicitud(suffix="B")
        page.wait_for_function(
            """(beforeTotal) => {
              const el = Array.from(document.querySelectorAll('.card-footer .small.text-muted'))
                .find((x) => (x.innerText || '').includes('de '));
              if (!el) return false;
              const m = (el.innerText || '').match(/de\\s+(\\d+)/);
              const now = m ? Number(m[1]) : 0;
              return now > Number(beforeTotal || 0);
            }""",
            arg=int(total_before),
            timeout=15000,
        )
        assert _listar_total(page) > total_before
        browser.close()


@pytest.mark.e2e
def test_cliente_live_e2e_ux_no_rompe_input_ni_filtros_sin_enviar(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        _client_login(page, live_env)
        page.goto(f"{live_env.base_url}/clientes/solicitudes", wait_until="domcontentloaded")
        page.wait_for_selector('[data-client-live-view="solicitudes_list"]')
        page.fill('input[name="q"]', "texto-no-enviado")
        page.focus('input[name="q"]')
        page.wait_for_function(
            "() => (document.activeElement && document.activeElement.getAttribute('name')) === 'q'",
            timeout=5000,
        )
        page.evaluate(
            """() => {
              window.__e2eFilterBlurCount = 0;
              window.__e2eListRefreshCount = 0;
              const q = document.querySelector('input[name="q"]');
              if (q && !q.__e2eBlurHooked) {
                q.__e2eBlurHooked = true;
                q.addEventListener('blur', () => { window.__e2eFilterBlurCount += 1; });
              }
              if (!window.__e2eRefreshHooked) {
                window.__e2eRefreshHooked = true;
                window.addEventListener('client-live:view-refreshed', (ev) => {
                  const view = String(((ev && ev.detail) || {}).view || '');
                  if (view === 'solicitudes_list') window.__e2eListRefreshCount += 1;
                });
              }
            }"""
        )

        event_outbox_id = int(live_env.push_solicitud_updated_event(live_env.solicitud_id) or 0)
        page.wait_for_function(
            """([eventId, expected]) => {
              const runtime = window.__clientLiveRuntime || {};
              const nowId = Number(runtime.afterId || 0);
              const field = document.querySelector('input[name="q"]');
              return nowId >= Number(eventId || 0) && !!field && field.value === expected;
            }""",
            arg=[event_outbox_id, "texto-no-enviado"],
            timeout=14000,
        )

        assert _value_for_filter_q(page) == "texto-no-enviado"
        refresh_count = int(page.evaluate("() => Number(window.__e2eListRefreshCount || 0)"))
        assert refresh_count == 0
        browser.close()


@pytest.mark.e2e
def test_cliente_live_e2e_c_detalle_reacciona_edicion_admin_sin_f5(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        _client_login(page, live_env)
        page.goto(f"{live_env.base_url}/clientes/solicitudes/{live_env.solicitud_id}", wait_until="domcontentloaded")
        page.wait_for_selector('[data-client-live-view="solicitud_detail"]')

        new_text = "Experiencia actualizada por staff E2E"
        live_env.edit_admin_solicitud(live_env.solicitud_id, experiencia=new_text)
        page.wait_for_function(
            "(txt) => document.body.innerText.includes(txt)",
            arg=new_text,
            timeout=15000,
        )
        assert page.locator("body").inner_text().find(new_text) >= 0
        browser.close()


@pytest.mark.e2e
def test_cliente_live_e2e_d_notificaciones_badge_y_modal_en_vivo(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        _client_login(page, live_env)
        page.goto(f"{live_env.base_url}/clientes/dashboard", wait_until="domcontentloaded")

        title = "Nueva notificacion E2E"
        live_env.push_notificacion(title=title, body="Se registró un evento en vivo")

        page.wait_for_function(
            "() => { const b = document.getElementById('clientNotifBadge'); return b && !b.classList.contains('d-none') && Number(b.textContent || '0') > 0; }",
            timeout=15000,
        )
        page.click("#clientNotifBell")
        page.wait_for_function(
            "(txt) => { const n = document.getElementById('clientNotifList'); return !!n && n.innerText.includes(txt); }",
            arg=title,
            timeout=15000,
        )
        browser.close()


@pytest.mark.e2e
def test_cliente_live_e2e_e_sse_fallback_y_recovery(live_env: LiveFixture):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        context.add_init_script(
            """
            window.__CLIENT_LIVE_SSE_RETRY_MS = 800;
            window.__CLIENT_LIVE_POLL_CONNECTED_MS = 4000;
            window.__CLIENT_LIVE_POLL_FALLBACK_MS = 600;
            """
        )
        page = context.new_page()
        _client_login(page, live_env)
        page.goto(f"{live_env.base_url}/clientes/dashboard", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => !!window.__clientLiveRuntime && typeof window.__clientLiveRuntime.forceFallbackForTest === 'function'",
            timeout=10000,
        )
        page.evaluate("() => window.__clientLiveRuntime.forceFallbackForTest()")

        page.wait_for_function(
            "() => !!window.__clientLiveRuntime && window.__clientLiveRuntime.transitions.some(t => t.mode === 'polling_fallback')",
            timeout=12000,
        )
        page.wait_for_function(
            "() => !!window.__clientLiveRuntime && window.__clientLiveRuntime.transitions.some(t => t.mode === 'sse_connected')",
            timeout=12000,
        )
        transport = page.evaluate("() => window.__clientLiveRuntime")
        assert bool(transport)
        assert int(transport.get("sseOpens", 0)) >= 1
        assert bool(transport.get("fallback", True)) is False
        assert int(transport.get("pollIntervalMs", 0)) == 4000
        modes = [str((t or {}).get("mode") or "") for t in (transport.get("transitions", []) or [])]
        assert "polling_fallback" in modes
        assert "sse_connected" in modes
        browser.close()
