#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass

import requests
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import Cliente, ClienteNotificacion, DomainOutbox, Solicitud, StaffUser
from utils.timezone import utc_now_naive


E2E_CLIENT_USERNAME = "e2e_live_cliente"
E2E_CLIENT_PASSWORD = "cliente12345"
E2E_ADMIN_USERNAME = "e2e_live_admin"
E2E_ADMIN_PASSWORD = "admin12345"
E2E_CLIENT_CODE = "E2E-LIVE-CLIENTE"
E2E_EMAIL = "e2e_live_cliente@test.local"
E2E_PHONE = "8090000999"
E2E_CODE_PREFIX = "SOL-E2E-LIVE-"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _new_outbox(event_type: str, payload: dict, *, aggregate_type: str = "Solicitud", aggregate_id: str = "0") -> DomainOutbox:
    now = utc_now_naive()
    return DomainOutbox(
        event_id=f"evt_e2e_live_{uuid.uuid4().hex[:20]}",
        event_type=str(event_type or "").strip().upper(),
        aggregate_type=str(aggregate_type or "Solicitud"),
        aggregate_id=str(aggregate_id or "0"),
        aggregate_version=1,
        occurred_at=now,
        actor_id="staff:e2e_live",
        region="admin",
        payload=dict(payload or {}),
        schema_version=1,
        created_at=now,
    )


@dataclass
class RuntimeCtx:
    base_url: str
    cliente_id: int
    solicitud_id: int

    def admin_session(self) -> requests.Session:
        sess = requests.Session()
        resp = sess.post(
            f"{self.base_url}/admin/login",
            data={"usuario": E2E_ADMIN_USERNAME, "clave": E2E_ADMIN_PASSWORD},
            timeout=12,
            allow_redirects=False,
        )
        assert resp.status_code in (302, 303), f"admin login failed ({resp.status_code})"
        return sess

    def create_admin_solicitud(self, suffix: str) -> tuple[int, str]:
        sess = self.admin_session()
        with flask_app.app_context():
            before_count = int(Solicitud.query.filter_by(cliente_id=self.cliente_id).count() or 0)
        payload = {
            "tipo_servicio": "DOMESTICA_LIMPIEZA",
            "ciudad_sector": f"Santiago Centro {suffix}",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Con dormida",
            "experiencia": f"Experiencia E2E {suffix}",
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
        assert resp.status_code in (302, 303), f"admin create failed ({resp.status_code})"
        with flask_app.app_context():
            after_count = int(Solicitud.query.filter_by(cliente_id=self.cliente_id).count() or 0)
            assert after_count > before_count, f"admin create did not persist row (before={before_count}, after={after_count})"
            row = (
                Solicitud.query.filter_by(cliente_id=self.cliente_id)
                .order_by(Solicitud.id.desc())
                .first()
            )
            assert row is not None
            return int(row.id), str(getattr(row, "codigo_solicitud", "") or "")

    def edit_admin_solicitud(self, solicitud_id: int, experiencia: str) -> None:
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
        assert resp.status_code in (302, 303), f"admin edit failed ({resp.status_code})"

    def push_notificacion(self, title: str, body: str) -> int:
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


def _client_login(page, ctx: RuntimeCtx):
    page.goto(f"{ctx.base_url}/clientes/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', E2E_CLIENT_USERNAME)
    page.fill('input[name="password"]', E2E_CLIENT_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url("**/clientes/dashboard", timeout=12000)


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


def _seed_base_data() -> tuple[int, int]:
    with flask_app.app_context():
        staff = StaffUser.query.filter_by(username=E2E_ADMIN_USERNAME).first()
        if staff is None:
            staff = StaffUser(username=E2E_ADMIN_USERNAME, email="e2e_live_admin@test.local", role="admin", is_active=True, mfa_enabled=False)
            db.session.add(staff)
        staff.role = "admin"
        staff.is_active = True
        staff.mfa_enabled = False
        staff.password_hash = generate_password_hash(E2E_ADMIN_PASSWORD, method="pbkdf2:sha256")

        client = Cliente.query.filter_by(codigo=E2E_CLIENT_CODE).first()
        if client is None:
            client = Cliente(
                codigo=E2E_CLIENT_CODE,
                nombre_completo="Cliente E2E Live",
                email=E2E_EMAIL,
                telefono=E2E_PHONE,
                username=E2E_CLIENT_USERNAME,
                password_hash=generate_password_hash(E2E_CLIENT_PASSWORD, method="pbkdf2:sha256"),
                is_active=True,
                role="cliente",
                total_solicitudes=1,
            )
            db.session.add(client)
            db.session.flush()
        else:
            client.nombre_completo = "Cliente E2E Live"
            client.email = E2E_EMAIL
            client.telefono = E2E_PHONE
            client.username = E2E_CLIENT_USERNAME
            client.password_hash = generate_password_hash(E2E_CLIENT_PASSWORD, method="pbkdf2:sha256")
            client.is_active = True
            client.role = "cliente"
            client.total_solicitudes = 1

        # cleanup previous scenario rows for this client
        ClienteNotificacion.query.filter_by(cliente_id=client.id).delete(synchronize_session=False)
        Solicitud.query.filter_by(cliente_id=client.id).delete(synchronize_session=False)
        db.session.flush()

        sol = Solicitud(
            cliente_id=int(client.id),
            codigo_solicitud=f"{E2E_CODE_PREFIX}{uuid.uuid4().hex[:6].upper()}",
            estado="proceso",
            ciudad_sector="Santiago Centro",
            modalidad_trabajo="Con dormida",
            experiencia="Experiencia inicial E2E live",
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
        db.session.add(sol)
        db.session.commit()
        return int(client.id), int(sol.id)


def _start_server() -> tuple[object, threading.Thread, str]:
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            requests.get(f"{base_url}/health", timeout=0.7)
            break
        except Exception:
            time.sleep(0.1)
    return server, thread, base_url


def _cleanup_all():
    with flask_app.app_context():
        client = Cliente.query.filter_by(codigo=E2E_CLIENT_CODE).first()
        if client:
            ClienteNotificacion.query.filter_by(cliente_id=client.id).delete(synchronize_session=False)
            Solicitud.query.filter_by(cliente_id=client.id).delete(synchronize_session=False)
            client.total_solicitudes = 0
        db.session.commit()


def run_suite() -> dict:
    cliente_id, solicitud_id = _seed_base_data()
    server, thread, base_url = _start_server()
    ctx = RuntimeCtx(base_url=base_url, cliente_id=cliente_id, solicitud_id=solicitud_id)
    report: dict[str, object] = {"base_url": base_url, "scenarios": {}, "transport_evidence": {}}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context_main = browser.new_context()
            context_main.add_init_script(
                """
                window.__CLIENT_LIVE_SSE_RETRY_MS = 1000;
                window.__CLIENT_LIVE_POLL_CONNECTED_MS = 2000;
                window.__CLIENT_LIVE_POLL_FALLBACK_MS = 600;
                """
            )

            # A
            page = context_main.new_page()
            _client_login(page, ctx)
            _, new_code_a = ctx.create_admin_solicitud("A")
            page.wait_for_function("(code) => document.body.innerText.includes(code)", arg=new_code_a, timeout=18000)
            report["scenarios"]["A"] = {"ok": True, "new_code": new_code_a}
            page.close()

            # B + UX local
            page = context_main.new_page()
            _client_login(page, ctx)
            page.goto(f"{base_url}/clientes/solicitudes", wait_until="domcontentloaded")
            page.evaluate(
                """() => {
                  window.__e2eRefreshViews = [];
                  window.addEventListener('client-live:view-refreshed', (ev) => {
                    const view = String(((ev && ev.detail) || {}).view || '');
                    window.__e2eRefreshViews.push(view);
                  });
                }"""
            )
            before_after_id = int(page.evaluate("() => Number(((window.__clientLiveRuntime || {}).afterId) || 0)"))
            _, new_code_b = ctx.create_admin_solicitud("B")
            page.wait_for_function(
                "() => Array.isArray(window.__e2eRefreshViews) && window.__e2eRefreshViews.includes('solicitudes_list')",
                timeout=20000,
            )
            after_after_id = int(page.evaluate("() => Number(((window.__clientLiveRuntime || {}).afterId) || 0)"))
            refreshed_views = page.evaluate("() => window.__e2eRefreshViews || []")
            report["scenarios"]["B"] = {
                "ok": True,
                "new_code": new_code_b,
                "refreshed_views": refreshed_views,
                "after_id_before": before_after_id,
                "after_id_after": after_after_id,
            }
            page.close()

            # UX local: filtro no enviado y foco no se rompen durante invalidación
            page = context_main.new_page()
            _client_login(page, ctx)
            page.goto(f"{base_url}/clientes/solicitudes", wait_until="domcontentloaded")
            page.fill('input[name="q"]', "filtro-temporal-e2e")
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
            ux_event_id = int(ctx.push_solicitud_updated_event(solicitud_id) or 0)
            page.wait_for_function(
                """([eventId, expected]) => {
                  const runtime = window.__clientLiveRuntime || {};
                  const nowId = Number(runtime.afterId || 0);
                  const field = document.querySelector('input[name="q"]');
                  return nowId >= Number(eventId || 0) && !!field && field.value === expected;
                }""",
                arg=[ux_event_id, "filtro-temporal-e2e"],
                timeout=14000,
            )
            q_value = page.eval_on_selector('input[name="q"]', "el => el.value")
            focused_name = page.evaluate("() => (document.activeElement && document.activeElement.getAttribute('name')) || ''")
            blur_count = int(page.evaluate("() => Number(window.__e2eFilterBlurCount || 0)"))
            refresh_count = int(page.evaluate("() => Number(window.__e2eListRefreshCount || 0)"))
            assert q_value == "filtro-temporal-e2e"
            assert refresh_count == 0
            report["scenarios"]["UX"] = {
                "ok": True,
                "q_value": q_value,
                "focused_name": focused_name,
                "blur_count": blur_count,
                "refresh_count": refresh_count,
                "event_outbox_id": ux_event_id,
                "after_id_after": int(page.evaluate("() => Number(((window.__clientLiveRuntime || {}).afterId) || 0)")),
            }
            page.close()

            # C
            page = context_main.new_page()
            _client_login(page, ctx)
            page.goto(f"{base_url}/clientes/solicitudes/{solicitud_id}", wait_until="domcontentloaded")
            exp_text = "Experiencia actualizada staff E2E live"
            ctx.edit_admin_solicitud(solicitud_id, exp_text)
            page.wait_for_function("(txt) => document.body.innerText.includes(txt)", arg=exp_text, timeout=18000)
            report["scenarios"]["C"] = {"ok": True, "updated_experiencia": exp_text}
            page.close()

            # D
            page = context_main.new_page()
            _client_login(page, ctx)
            page.goto(f"{base_url}/clientes/dashboard", wait_until="domcontentloaded")
            notif_title = "Notificación E2E Live"
            ctx.push_notificacion(title=notif_title, body="mensaje en vivo")
            page.wait_for_function(
                "() => { const b = document.getElementById('clientNotifBadge'); return b && !b.classList.contains('d-none') && Number(b.textContent || '0') > 0; }",
                timeout=18000,
            )
            page.click("#clientNotifBell")
            page.wait_for_function(
                "(txt) => { const n = document.getElementById('clientNotifList'); return !!n && n.innerText.includes(txt); }",
                arg=notif_title,
                timeout=18000,
            )
            report["scenarios"]["D"] = {"ok": True, "notif_title": notif_title}
            page.close()
            context_main.close()

            # E transport fallback/recovery
            context = browser.new_context()
            context.add_init_script(
                """
                window.__CLIENT_LIVE_SSE_RETRY_MS = 800;
                window.__CLIENT_LIVE_POLL_CONNECTED_MS = 4000;
                window.__CLIENT_LIVE_POLL_FALLBACK_MS = 600;
                """
            )
            page = context.new_page()
            _client_login(page, ctx)
            page.goto(f"{base_url}/clientes/dashboard", wait_until="domcontentloaded")
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
            report["transport_evidence"] = {
                "sse_errors": int((transport or {}).get("sseErrors") or 0),
                "sse_opens": int((transport or {}).get("sseOpens") or 0),
                "fallback": bool((transport or {}).get("fallback")),
                "poll_interval_ms": int((transport or {}).get("pollIntervalMs") or 0),
                "modes_seen": [t.get("mode") for t in ((transport or {}).get("transitions") or [])[-20:]],
            }
            assert report["transport_evidence"]["sse_opens"] >= 1
            assert report["transport_evidence"]["fallback"] is False
            assert report["transport_evidence"]["poll_interval_ms"] == 4000
            assert "polling_fallback" in report["transport_evidence"]["modes_seen"]
            assert "sse_connected" in report["transport_evidence"]["modes_seen"]
            report["scenarios"]["E"] = {"ok": True}
            page.close()
            context.close()

            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        _cleanup_all()
    return report


if __name__ == "__main__":
    started = time.time()
    result = run_suite()
    result["elapsed_sec"] = round(time.time() - started, 2)
    result["ok"] = all(bool((v or {}).get("ok")) for v in (result.get("scenarios") or {}).values())
    print(json.dumps(result, ensure_ascii=False, indent=2))
