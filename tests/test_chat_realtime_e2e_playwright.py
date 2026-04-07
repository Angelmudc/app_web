# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import time
import uuid
import urllib.parse

import pytest
import requests
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import ChatConversation, ChatMessage, Cliente, DomainOutbox, Solicitud, StaffUser
from utils.chat_e2e_guard import chat_e2e_scope_key, chat_e2e_subject


if os.getenv("RUN_CHAT_E2E", "0").strip() != "1":
    pytest.skip("Chat E2E deshabilitado. Ejecuta con RUN_CHAT_E2E=1 para correrlo.", allow_module_level=True)

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


def _ensure_e2e_tables():
    with flask_app.app_context():
        bind = db.engine
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
        # Base tables needed for chat E2E.
        for model in (Cliente, StaffUser, ChatConversation, ChatMessage, DomainOutbox):
            try:
                model.__table__.create(bind=bind, checkfirst=True)
            except Exception:
                pass
        # Solicitud has PG-only defaults/types; create a lightweight sqlite table if needed.
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
            try:
                Solicitud.__table__.create(bind=bind, checkfirst=True)
            except Exception:
                pass


def _candidate_ports() -> list[int]:
    env_port = (os.getenv("E2E_PORT", "") or "").strip()
    candidates: list[int] = []
    if env_port:
        try:
            candidates.append(int(env_port))
        except ValueError:
            pass
    for p in (5005, 8000, 8081):
        if p not in candidates:
            candidates.append(p)
    return candidates


def _start_test_server():
    last_error: Exception | None = None
    for port in _candidate_ports():
        try:
            server = make_server("127.0.0.1", int(port), flask_app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return server, thread, int(port)
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"No fue posible iniciar servidor E2E en puertos candidatos: {_candidate_ports()}") from last_error


def _seed_fixture(tag: str):
    with flask_app.app_context():
        _ensure_e2e_tables()
        now = int(time.time())
        staff = StaffUser(
            username=f"e2e_chat_admin_{tag}",
            email=f"e2e_chat_admin_{tag}@test.local",
            password_hash=generate_password_hash("Admin#12345", method="pbkdf2:sha256"),
            role="admin",
            is_active=True,
            mfa_enabled=False,
        )
        c1 = Cliente(
            codigo=f"CL-E2ECHAT-{tag}-A",
            nombre_completo=f"Cliente E2E Chat A {tag}",
            email=f"e2e_chat_cliente_a_{tag}@test.local",
            telefono=f"80977{str(now % 100000).zfill(5)}",
            username=f"e2e_chat_cliente_a_{tag}",
            password_hash=generate_password_hash("Cliente#12345", method="pbkdf2:sha256"),
            role="cliente",
            is_active=True,
            acepto_politicas=True,
        )
        c2 = Cliente(
            codigo=f"CL-E2ECHAT-{tag}-B",
            nombre_completo=f"Cliente E2E Chat B {tag}",
            email=f"e2e_chat_cliente_b_{tag}@test.local",
            telefono=f"80988{str((now + 7) % 100000).zfill(5)}",
            username=f"e2e_chat_cliente_b_{tag}",
            password_hash=generate_password_hash("Cliente#12345", method="pbkdf2:sha256"),
            role="cliente",
            is_active=True,
            acepto_politicas=True,
        )
        db.session.add_all([staff, c1, c2])
        db.session.flush()

        dialect = str(getattr(getattr(db.engine, "dialect", None), "name", "") or "").lower()
        if dialect == "sqlite":
            codigo = f"SOL-E2ECHAT-{tag}"
            db.session.execute(
                text(
                    "INSERT INTO solicitudes (cliente_id, codigo_solicitud, estado, experiencia, fecha_solicitud, row_version) "
                    "VALUES (:cid, :codigo, :estado, :exp, :fecha, :rv)"
                ),
                {
                    "cid": int(c1.id),
                    "codigo": codigo,
                    "estado": "proceso",
                    "exp": f"Solicitud E2E {tag}",
                    "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "rv": 1,
                },
            )
            solicitud_id = db.session.execute(text("SELECT last_insert_rowid()")).scalar() or 0
            solicitud = Solicitud(id=int(solicitud_id), cliente_id=int(c1.id), codigo_solicitud=codigo, estado="proceso")
        else:
            solicitud = Solicitud(
                cliente_id=int(c1.id),
                codigo_solicitud=f"SOL-E2ECHAT-{tag}",
                estado="proceso",
                experiencia=f"Solicitud E2E {tag}",
            )
            db.session.add(solicitud)
            db.session.flush()

        conv_general = ChatConversation(
            scope_key=chat_e2e_scope_key(cliente_id=int(c1.id), solicitud_id=None),
            conversation_type="general",
            status="open",
            cliente_id=int(c1.id),
            subject=chat_e2e_subject("Soporte E2E"),
        )
        conv_solicitud = ChatConversation(
            scope_key=chat_e2e_scope_key(cliente_id=int(c1.id), solicitud_id=int(solicitud.id)),
            conversation_type="solicitud",
            status="open",
            cliente_id=int(c1.id),
            solicitud_id=int(solicitud.id),
            subject=chat_e2e_subject("Soporte solicitud E2E"),
        )
        db.session.add_all([conv_general, conv_solicitud])
        db.session.commit()

        return {
            "staff_id": int(staff.id),
            "staff_username": staff.username,
            "staff_password": "Admin#12345",
            "cliente_a_id": int(c1.id),
            "cliente_a_username": c1.username,
            "cliente_a_password": "Cliente#12345",
            "cliente_b_id": int(c2.id),
            "cliente_b_username": c2.username,
            "cliente_b_password": "Cliente#12345",
            "solicitud_id": int(solicitud.id),
            "conv_general_id": int(conv_general.id),
            "conv_solicitud_id": int(conv_solicitud.id),
        }


def _cleanup_fixture(fx: dict):
    with flask_app.app_context():
        conv_ids = [int(fx.get("conv_general_id") or 0), int(fx.get("conv_solicitud_id") or 0)]
        conv_ids = [x for x in conv_ids if x > 0]
        staff_id = int(fx.get("staff_id") or 0)
        c1 = int(fx.get("cliente_a_id") or 0)
        c2 = int(fx.get("cliente_b_id") or 0)
        solicitud_id = int(fx.get("solicitud_id") or 0)

        if conv_ids:
            db.session.query(ChatMessage).filter(ChatMessage.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            db.session.query(ChatConversation).filter(ChatConversation.id.in_(conv_ids)).delete(synchronize_session=False)
            db.session.query(DomainOutbox).filter(
                DomainOutbox.aggregate_type == "ChatConversation",
                DomainOutbox.aggregate_id.in_([str(x) for x in conv_ids]),
            ).delete(synchronize_session=False)
        if solicitud_id > 0:
            db.session.query(Solicitud).filter(Solicitud.id == solicitud_id).delete(synchronize_session=False)
        if c1 > 0:
            db.session.query(Cliente).filter(Cliente.id == c1).delete(synchronize_session=False)
        if c2 > 0:
            db.session.query(Cliente).filter(Cliente.id == c2).delete(synchronize_session=False)
        if staff_id > 0:
            db.session.query(StaffUser).filter(StaffUser.id == staff_id).delete(synchronize_session=False)
        db.session.commit()


def _client_login(page, base_url: str, username: str, password: str):
    next_path = urllib.parse.quote("/clientes/chat", safe="")
    page.goto(f"{base_url}/clientes/login?next={next_path}", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url("**/clientes/chat**", timeout=12000)


def _admin_login(page, base_url: str, username: str, password: str):
    page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
    page.fill('input[name="usuario"]', username)
    page.fill('input[name="clave"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin/**", timeout=12000)


def _open_client_conversation(page, conv_id: int):
    parsed = urllib.parse.urlsplit(page.url or "")
    base = f"{parsed.scheme}://{parsed.netloc}"
    page.goto(f"{base}/clientes/chat?conversation_id={int(conv_id)}", wait_until="domcontentloaded")
    page.wait_for_function(
        "(cid) => Number((document.querySelector('#clientChatMessages')||{}).getAttribute('data-conversation-id')||0)===Number(cid)",
        arg=int(conv_id),
        timeout=12000,
    )


def _open_staff_conversation(page, conv_id: int):
    parsed = urllib.parse.urlsplit(page.url or "")
    base = f"{parsed.scheme}://{parsed.netloc}"
    page.goto(f"{base}/admin/chat?conversation_id={int(conv_id)}", wait_until="domcontentloaded")
    page.wait_for_function(
        "(cid) => Number((document.querySelector('#adminChatMessages')||{}).getAttribute('data-conversation-id')||0)===Number(cid)",
        arg=int(conv_id),
        timeout=12000,
    )


def _wait_badge_count(page, selector: str, expected_min: int):
    page.wait_for_function(
        """([sel,minN]) => {
            const n = document.querySelector(sel);
            if (!n) return Number(minN) <= 0;
            const txt = (n.innerText || n.textContent || '').trim();
            const val = Number(txt || 0);
            return Number.isFinite(val) && val >= Number(minN);
        }""",
        arg=[selector, int(expected_min)],
        timeout=12000,
    )


def _admin_send(page, text: str):
    page.fill("#adminChatBody", text)
    submitted = page.evaluate(
        """() => {
            const form = document.getElementById('adminChatComposeForm');
            if (form && typeof form.requestSubmit === 'function') {
                form.requestSubmit();
                return true;
            }
            const btn = document.getElementById('adminChatSendBtn');
            if (btn && typeof btn.click === 'function') {
                btn.click();
                return true;
            }
            return false;
        }"""
    )
    assert bool(submitted) is True


@pytest.mark.e2e
def test_chat_realtime_cliente_staff_roundtrip_safe():
    tag = uuid.uuid4().hex[:8]
    os.environ["CHAT_E2E_RUN_ID"] = tag
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    fixture = _seed_fixture(tag)
    os.environ["CHAT_E2E_ALLOWLIST_CLIENTE_IDS"] = str(fixture["cliente_a_id"])
    os.environ["CHAT_E2E_ALLOWLIST_SOLICITUD_IDS"] = str(fixture["solicitud_id"])
    os.environ["CHAT_E2E_ALLOWLIST_CONVERSATION_IDS"] = f"{fixture['conv_general_id']},{fixture['conv_solicitud_id']}"

    server, thread, port = _start_test_server()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.8)
            if r.status_code in (200, 404):
                break
        except Exception:
            time.sleep(0.1)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page_client_a = browser.new_page()
            page_client_a.add_init_script(
                "window.__CLIENT_LIVE_SSE_RETRY_MS=1000;window.__CLIENT_LIVE_POLL_CONNECTED_MS=4000;window.__CLIENT_LIVE_POLL_FALLBACK_MS=1000;"
            )
            page_client_b = browser.new_page()
            page_staff = browser.new_page()

            _client_login(page_client_a, base_url, fixture["cliente_a_username"], fixture["cliente_a_password"])
            _client_login(page_client_b, base_url, fixture["cliente_b_username"], fixture["cliente_b_password"])
            _admin_login(page_staff, base_url, fixture["staff_username"], fixture["staff_password"])

            conv_general = int(fixture["conv_general_id"])
            conv_solicitud = int(fixture["conv_solicitud_id"])

            page_client_a.goto(f"{base_url}/clientes/chat?conversation_id={conv_general}", wait_until="domcontentloaded")
            page_staff.goto(f"{base_url}/admin/chat?conversation_id={conv_general}", wait_until="domcontentloaded")
            res_b = page_client_b.goto(f"{base_url}/clientes/chat", wait_until="domcontentloaded")
            assert res_b is not None
            assert int(res_b.status or 0) in (401, 403)

            # Escenario A: inbox cliente abre conversación general y puede abrirla.
            page_client_a.wait_for_selector(f'#clientChatConversationList [data-conversation-id="{conv_general}"]', timeout=10000)
            _open_client_conversation(page_client_a, conv_general)

            # Escenario B: cliente -> staff sin F5.
            msg_b = f"e2e-B-client-to-staff-{tag}"
            page_client_a.fill("#clientChatBody", msg_b)
            page_client_a.click("#clientChatSendBtn")
            page_staff.wait_for_function(
                "(txt) => (document.querySelector('#adminChatMessages')||{}).innerText.includes(txt)",
                arg=msg_b,
                timeout=15000,
            )

            # Escenario C: staff -> cliente sin F5.
            msg_c = f"e2e-C-staff-to-client-{tag}"
            _admin_send(page_staff, msg_c)
            page_client_a.wait_for_function(
                "(txt) => (document.querySelector('#clientChatMessages')||{}).innerText.includes(txt)",
                arg=msg_c,
                timeout=15000,
            )

            # Escenario F (parte 1): scope solicitud y general separados.
            _open_client_conversation(page_client_a, conv_solicitud)
            msg_f_solicitud = f"e2e-F-solicitud-msg-{tag}"
            page_client_a.fill("#clientChatBody", msg_f_solicitud)
            page_client_a.click("#clientChatSendBtn")
            page_staff.wait_for_function(
                "(txt) => (document.querySelector('#adminChatConversationList')||{}).innerText.includes(txt)",
                arg=msg_f_solicitud,
                timeout=15000,
            )
            _open_client_conversation(page_client_a, conv_general)
            page_client_a.wait_for_function(
                "(txt) => !(document.querySelector('#clientChatMessages')||{}).innerText.includes(txt)",
                arg=msg_f_solicitud,
                timeout=8000,
            )
            _open_client_conversation(page_client_a, conv_solicitud)
            page_client_a.wait_for_function(
                "(txt) => (document.querySelector('#clientChatMessages')||{}).innerText.includes(txt)",
                arg=msg_f_solicitud,
                timeout=8000,
            )

            # Escenario D: unread counters en ambos lados.
            _open_staff_conversation(page_staff, conv_general)
            msg_d_client = f"e2e-D-client-unread-{tag}"
            page_client_a.fill("#clientChatBody", msg_d_client)
            page_client_a.click("#clientChatSendBtn")
            _wait_badge_count(
                page_staff,
                f'#adminChatConversationList [data-conversation-id="{conv_solicitud}"] .badge',
                1,
            )

            _open_client_conversation(page_client_a, conv_general)
            _open_staff_conversation(page_staff, conv_solicitud)
            msg_d_staff = f"e2e-D-staff-unread-{tag}"
            _admin_send(page_staff, msg_d_staff)
            _wait_badge_count(
                page_client_a,
                f'#clientChatConversationList [data-conversation-id="{conv_solicitud}"] .badge',
                1,
            )

            # Escenario E: marcar leído cambia estado visible.
            _open_client_conversation(page_client_a, conv_solicitud)
            page_client_a.click("#clientChatMarkReadBtn")
            page_client_a.wait_for_function(
                """(cid) => {
                    const row = document.querySelector('#clientChatConversationList [data-conversation-id="' + String(cid) + '"]');
                    if (!row) return false;
                    return !row.querySelector('.badge');
                }""",
                arg=conv_solicitud,
                timeout=12000,
            )

            _open_staff_conversation(page_staff, conv_solicitud)
            page_staff.click("#adminChatMarkReadBtn")
            page_staff.wait_for_function(
                """(cid) => {
                    const row = document.querySelector('#adminChatConversationList [data-conversation-id="' + String(cid) + '"]');
                    if (!row) return false;
                    return !row.querySelector('.badge');
                }""",
                arg=conv_solicitud,
                timeout=12000,
            )

            # Escenario G: caída SSE cliente => polling fallback => reconvergencia SSE.
            page_client_a.wait_for_function(
                "() => !!window.__clientLiveRuntime && typeof window.__clientLiveRuntime.forceFallbackForTest === 'function'",
                timeout=10000,
            )
            page_client_a.evaluate(
                """() => {
                    if (window.__clientLiveRuntime && typeof window.__clientLiveRuntime.forceFallbackForTest === 'function') {
                        window.__clientLiveRuntime.forceFallbackForTest();
                    }
                }"""
            )
            page_client_a.wait_for_function(
                """() => {
                    const rt = window.__clientLiveRuntime || {};
                    const tr = Array.isArray(rt.transitions) ? rt.transitions : [];
                    return tr.some((x) => String((x||{}).mode || '') === 'polling_fallback');
                }""",
                timeout=12000,
            )
            _open_staff_conversation(page_staff, conv_general)
            _open_client_conversation(page_client_a, conv_general)
            msg_g = f"e2e-G-fallback-msg-{tag}"
            _admin_send(page_staff, msg_g)
            page_client_a.wait_for_function(
                "(txt) => (document.querySelector('#clientChatMessages')||{}).innerText.includes(txt)",
                arg=msg_g,
                timeout=16000,
            )
            page_client_a.wait_for_function(
                """() => {
                    const rt = window.__clientLiveRuntime || {};
                    const tr = Array.isArray(rt.transitions) ? rt.transitions : [];
                    return tr.some((x) => String((x||{}).mode || '') === 'sse_connected');
                }""",
                timeout=20000,
            )

            # Escenario H: aislamiento cross-cliente explícito.
            chat_box_b = page_client_b.locator("#clientChatMessages")
            if chat_box_b.count() > 0:
                assert msg_g not in (chat_box_b.first.inner_text(timeout=3000) or "")
            forbidden = page_client_b.goto(f"{base_url}/clientes/chat?conversation_id={conv_general}", wait_until="domcontentloaded")
            assert forbidden is not None
            assert int(forbidden.status or 0) in (403, 404)

            # Escenario I: orden correcto y sin duplicados visuales/persistidos.
            seq_msgs = [
                f"e2e-I-1-{tag}",
                f"e2e-I-2-{tag}",
                f"e2e-I-3-{tag}",
            ]
            _open_staff_conversation(page_staff, conv_general)
            _open_client_conversation(page_client_a, conv_general)
            for txt in seq_msgs:
                _admin_send(page_staff, txt)
                page_client_a.wait_for_function(
                    "(v) => (document.querySelector('#clientChatMessages')||{}).innerText.includes(v)",
                    arg=txt,
                    timeout=12000,
                )
            page_client_a.wait_for_function(
                """(vals) => {
                    const host = document.querySelector('#clientChatMessages');
                    if (!host) return false;
                    const txt = host.innerText || '';
                    const idx = vals.map((v) => txt.indexOf(v));
                    if (idx.some((i) => i < 0)) return false;
                    return idx[0] < idx[1] && idx[1] < idx[2];
                }""",
                arg=seq_msgs,
                timeout=10000,
            )

            with flask_app.app_context():
                rows = (
                    ChatMessage.query
                    .filter(ChatMessage.conversation_id == conv_general)
                    .filter(ChatMessage.body.in_(seq_msgs))
                    .all()
                )
                by_body = {}
                for r in (rows or []):
                    by_body[str(r.body)] = by_body.get(str(r.body), 0) + 1
                for txt in seq_msgs:
                    assert int(by_body.get(txt, 0)) == 1

            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        _cleanup_fixture(fixture)
