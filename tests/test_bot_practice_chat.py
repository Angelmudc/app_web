# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import os
import re
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_conversation_service import set_current_step


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.drop(bind=db.engine, checkfirst=True)

    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotCandidateDraft).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    os.environ["ADMIN_AUTO_PRESENCE_TOUCH_ENABLED"] = "0"
    data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=data, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _extract_csrf(html: str) -> str:
    m_meta = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', html)
    if m_meta:
        return m_meta.group(1)
    m_input = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m_input is not None, "No se encontró csrf_token en la vista."
    return m_input.group(1)


def test_practica_routes_require_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    r1 = client.get("/admin/bot/practica", follow_redirects=False)
    assert r1.status_code in (302, 303)
    assert "/admin/login" in (r1.headers.get("Location") or "")

    r2 = client.post("/admin/bot/practica/1/mensaje", json={"text": "hola"}, follow_redirects=False)
    assert r2.status_code in (302, 303)


def test_practica_page_renders_and_has_chat_ui_structure():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    _login_staff(client)
    list_page = client.get("/admin/bot/practica", follow_redirects=False)
    list_csrf = _extract_csrf(list_page.get_data(as_text=True))
    create = client.post("/admin/bot/practica", data={"csrf_token": list_csrf}, follow_redirects=False)
    assert create.status_code in (302, 303)
    location = create.headers.get("Location") or ""
    resp = client.get(location, follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Práctica local del bot" in html
    assert "Nueva práctica" in html
    assert "admin_bot_practice_chat.css" in html
    assert "practice-chat-shell" in html
    assert "data-chat-shell=\"1\"" in html
    assert "practice-chat-messages" in html
    assert "id=\"practiceMessages\"" in html
    assert "practice-composer-fixed" in html
    assert "practice-chat-composer" in html
    assert "data-practice-composer=\"1\"" in html
    assert "practice-side-col" in html
    assert "practice-textarea" in html
    assert "practice-send-btn" in html
    assert html.index("practice-chat-messages") < html.index("practice-chat-composer")
    js_text = Path("static/js/admin_bot_practice_chat.js").read_text(encoding="utf-8")
    assert "botSuggestedBubble" in js_text
    assert "practice-suggested-badge" in js_text
    assert "chat_items" in js_text
    assert "scrollLogToBottom" in js_text
    assert "practiceMessages" in js_text
    assert "scrollTop = log.scrollHeight" in js_text
    css_text = Path("static/css/admin_bot_practice_chat.css").read_text(encoding="utf-8")
    assert ".practice-chat-messages" in css_text
    assert "overflow-y: auto" in css_text
    assert "padding-bottom: 96px" in css_text
    assert ".practice-chat-composer" in css_text or ".practice-composer" in css_text


def test_practica_demo_mode_renders_simple_ui_and_hides_debug(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["BOT_PRACTICE_DEMO_MODE"] = True
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    location = create.headers.get("Location") or ""
    resp = client.get(location, follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert 'data-practice-demo-mode="1"' in html
    assert "No enviado" in html
    assert "Debug estado" not in html
    assert "Future entities" not in html
    assert "Ver metadata JSON" not in html
    assert "Conversación práctica #" not in html
    assert "practice-entity-name" in html
    assert "practice-entity-age" in html
    assert "practice-entity-city" in html
    assert "practice-entity-work-type" in html
    assert "Reiniciar práctica" not in html
    assert "data-control-action=\"reset\"" in html
    assert "data-control-action=\"advance\"" not in html
    assert "requires_human:" not in html


def test_practica_demo_mode_keeps_debug_json_endpoint(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["BOT_PRACTICE_DEMO_MODE"] = True
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    resp = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("conversation_id") == conv_id


def test_practica_js_typing_indicator_lifecycle_present():
    js_text = Path("static/js/admin_bot_practice_chat.js").read_text(encoding="utf-8")
    assert "Bot escribiendo..." in js_text
    assert "showTypingIndicator" in js_text
    assert "clearTypingIndicator" in js_text
    assert "typingIndicatorId" in js_text
    assert "if (document.getElementById(typingIndicatorId)) return;" in js_text
    assert "activeSendToken" in js_text
    assert "sendToken === activeSendToken" in js_text


def test_practica_scroll_container_structure_is_stable():
    css_text = Path("static/css/admin_bot_practice_chat.css").read_text(encoding="utf-8")
    assert ".practice-chat-shell" in css_text
    assert ".practice-chat-messages" in css_text
    assert "overflow-y: auto" in css_text
    assert "scroll-behavior: smooth" in css_text
    assert "overscroll-behavior: contain" in css_text
    assert "scrollbar-gutter: stable both-edges" in css_text
    assert ".practice-composer" in css_text


def test_nueva_practica_crea_conversacion_local_practice(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    assert create.status_code in (302, 303)
    location = create.headers.get("Location") or ""
    assert "/admin/bot/practica/" in location

    with flask_app.app_context():
        conv = BotConversation.query.order_by(BotConversation.id.desc()).first()
        assert conv is not None
        metadata = dict(conv.metadata_json or {})
        assert metadata.get("conversation_type") == "local_practice"
        assert conv.contact_name == "Práctica Bot"


def test_practica_mensaje_ejecuta_pipeline_y_no_outbound_real(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    list_page = client.get("/admin/bot/practica", follow_redirects=False)
    list_csrf = _extract_csrf(list_page.get_data(as_text=True))
    create = client.post("/admin/bot/practica", data={"csrf_token": list_csrf}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=True), patch("admin.bot_routes.is_autoreply_enabled", return_value=False), patch(
        "admin.bot_routes.classify_intent",
        return_value={
            "ok": True,
            "intent": "FAQ_REQUISITOS",
            "answer_text": "Sugerencia local de prueba.",
            "confidence": 0.95,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    ), patch("admin.bot_routes.send_text_message") as send_mock:
        sent = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)

    assert sent.status_code == 200
    data = sent.get_json()
    assert data.get("ok") is True
    assert isinstance(data.get("timings"), dict)
    assert "practice_message_ms" in data.get("timings", {})
    assert "pipeline_ms" in data.get("timings", {})
    assert data.get("current_step")
    assert "progress" in data
    assert data.get("suggested_reply")
    assert data.get("suggested_reply_source")
    virtual = data.get("virtual_bot_message") or {}
    assert virtual.get("role") == "bot_suggested"
    assert str(virtual.get("text") or "").strip()
    assert isinstance(data.get("chat_items"), list)
    assert isinstance(data.get("protocol_entities"), dict)
    assert isinstance(data.get("pending_corrections"), list)
    assert "draft_possible" in data
    send_mock.assert_not_called()

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        assert BotCandidateDraft.query.count() == 0


def test_practica_estado_endpoint_devuelve_estado(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    assert state.status_code == 200
    data = state.get_json()
    assert data.get("ok") is True
    assert data.get("conversation_id") == conv_id
    assert "messages" in data
    assert data.get("suggested_reply")
    assert isinstance(data.get("virtual_bot_message"), dict)
    assert data.get("virtual_bot_message", {}).get("role") == "bot_suggested"
    assert isinstance(data.get("virtual_messages"), list)
    assert isinstance(data.get("chat_items"), list)


def test_practica_debug_json_retorna_estado_legible(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)

    resp = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("conversation_id") == conv_id
    assert payload.get("conversation_type") == "local_practice"
    assert isinstance(payload.get("chat_items"), list)
    assert isinstance(payload.get("protocol_entities"), dict)
    assert isinstance(payload.get("debug_protocol_state"), dict)


def test_practica_guardrails_bloquean_flags_peligrosos(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    _login_staff(client)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "true")
    page = client.get("/admin/bot/practica", follow_redirects=False)
    assert page.status_code == 200
    assert "Práctica bloqueada por seguridad" in page.get_data(as_text=True)


def test_practica_control_actions_retorna_estado(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    action = client.post(f"/admin/bot/practica/{conv_id}/control", json={"action": "advance"}, follow_redirects=False)
    assert action.status_code == 200
    data = action.get_json()
    assert data.get("ok") is True
    assert data.get("current_step")


def test_practica_mensaje_json_vacio_devuelve_400_controlado(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    sent = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "   "}, follow_redirects=False)
    assert sent.status_code == 400
    data = sent.get_json()
    assert data.get("ok") is False
    assert data.get("error") == "empty_body"


def test_practica_mensaje_requiere_csrf_si_esta_activo(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    list_page = client.get("/admin/bot/practica", follow_redirects=False)
    list_csrf = _extract_csrf(list_page.get_data(as_text=True))
    create = client.post("/admin/bot/practica", data={"csrf_token": list_csrf}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    page = client.get(f"/admin/bot/practica/{conv_id}", follow_redirects=False)
    csrf_token = _extract_csrf(page.get_data(as_text=True))

    missing = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
    assert missing.status_code == 400
    missing_data = missing.get_json() or {}
    assert missing_data.get("error_code") == "csrf"

    ok = client.post(
        f"/admin/bot/practica/{conv_id}/mensaje",
        json={"text": "hola"},
        headers={"X-CSRFToken": csrf_token},
        follow_redirects=False,
    )
    assert ok.status_code == 200
    ok_data = ok.get_json()
    assert ok_data.get("ok") is True
    assert isinstance(ok_data.get("messages"), list)
    assert ok_data.get("current_step")
    assert "suggested_reply" in ok_data


def test_practica_welcome_hola_retorna_virtual_bot_message_visible(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        sent = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
    assert sent.status_code == 200
    payload = sent.get_json() or {}
    assert str(payload.get("suggested_reply") or "").strip()
    assert isinstance(payload.get("virtual_bot_message"), dict)
    assert str((payload.get("virtual_bot_message") or {}).get("text") or "").strip()
    assert isinstance(payload.get("virtual_messages"), list)
    assert isinstance(payload.get("chat_items"), list)

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    assert state.status_code == 200
    state_payload = state.get_json() or {}
    assert str(state_payload.get("suggested_reply") or "").strip()
    assert isinstance(state_payload.get("virtual_bot_message"), dict)
    assert str((state_payload.get("virtual_bot_message") or {}).get("text") or "").strip()

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0


def test_practica_chat_items_ordenados_por_turno_sin_outbound(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola uno"}, follow_redirects=False)
        assert r1.status_code == 200
        r2 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola dos"}, follow_redirects=False)
        assert r2.status_code == 200

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    assert state.status_code == 200
    data = state.get_json() or {}
    chat_items = data.get("chat_items") or []
    assert len(chat_items) >= 4
    assert chat_items[0].get("role") == "candidate"
    assert chat_items[1].get("role") == "bot_suggested"
    assert chat_items[2].get("role") == "candidate"
    assert chat_items[3].get("role") == "bot_suggested"
    assert chat_items[1].get("inbound_message_id")
    assert chat_items[3].get("inbound_message_id")
    assert chat_items[1].get("inbound_message_id") != chat_items[3].get("inbound_message_id")

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0


def test_practica_saludo_repetido_en_basic_info_no_avanza_a_address(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "Si", "hola", "hola"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "BASIC_INFO"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert "nombre" in suggested and "edad" in suggested
        assert "ADDRESS" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_saludo_en_address_no_avanza_a_work_type(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "Si", "me llamo carmen tengo 30 años", "hola"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "ADDRESS"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert ("vivo" in suggested) or ("direccion" in suggested) or ("santiago" in suggested) or ("puerto plata" in suggested)
        assert "WORK_TYPE" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_saludo_en_work_type_no_avanza_a_transport(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "Si", "me llamo carmen tengo 30 años", "vivo en santiago", "hola"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "WORK_TYPE"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert ("dormida" in suggested) or ("salida" in suggested) or ("modalidad" in suggested) or ("trabajo" in suggested)
        assert "TRANSPORT_ROUTE" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_hola_repetido_no_dispara_protocol_anti_loop(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "Si", "hola", "hola", "hola"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200

        debug = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
        payload = debug.get_json() or {}
        virtual = payload.get("practice_virtual_messages") or []
        sources = [str((x or {}).get("source") or "") for x in virtual]
        assert "protocol_anti_loop" not in sources


def test_practica_chat_items_growth_keeps_payload_consistent(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for idx in range(40):
            txt = "hola" if idx % 2 == 0 else "si"
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        chat_items = payload.get("chat_items") or []
        assert len(chat_items) >= 20
        assert isinstance(payload.get("protocol_entities"), dict)
        assert isinstance(payload.get("debug_protocol_state"), dict)


def test_practica_20_mensajes_hola_crece_chat_items_sin_error(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for _ in range(20):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}

    assert payload.get("ok") is True
    assert len(payload.get("chat_items") or []) >= 20


def test_practica_address_a_work_type_a_transport_route_salida_diaria(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    sequence = ["hola", "si", "me llamo carmen tengo 30 años", "puerto plata centro", "salida diaria"]
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in sequence:
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}

    assert payload.get("current_step") == "TRANSPORT_ROUTE"
    assert str((payload.get("debug_protocol_state") or {}).get("last_completed_step") or "") == "WORK_TYPE"
    suggested = str(payload.get("suggested_reply") or "").lower()
    assert any(x in suggested for x in ("ruta", "transporte", "concho", "carro", "guagua"))
    assert ("ciudad" not in suggested) and ("sector" not in suggested)
    entities = payload.get("protocol_entities") or {}
    assert str(entities.get("work_type") or "").strip().lower() == "salida diaria"
    assert str(entities.get("city") or "").strip().lower() == "puerto plata"
    chat_items = payload.get("chat_items") or []
    assert len(chat_items) >= 10
    assert chat_items[0].get("role") == "candidate"
    assert chat_items[1].get("role") == "bot_suggested"
    assert chat_items[-2].get("role") == "candidate"
    assert chat_items[-1].get("role") == "bot_suggested"


def test_practica_address_a_work_type_a_transport_route_dormida(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    sequence = ["hola", "si", "me llamo carmen tengo 30 años", "santiago gurabo", "dormida"]
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in sequence:
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}

    assert payload.get("current_step") == "TRANSPORT_ROUTE"
    assert str((payload.get("debug_protocol_state") or {}).get("last_completed_step") or "") == "WORK_TYPE"
    suggested = str(payload.get("suggested_reply") or "").lower()
    assert any(x in suggested for x in ("ruta", "transporte", "concho", "carro", "guagua"))
    assert ("ciudad" not in suggested) and ("sector" not in suggested)
    entities = payload.get("protocol_entities") or {}
    assert str(entities.get("work_type") or "").strip().lower() == "dormida"
    assert str(entities.get("city") or "").strip().lower() == "santiago"


def test_practica_welcome_hola_luego_si_hasme_avanza_y_no_repite_bienvenida(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        assert r1.status_code == 200
        d1 = r1.get_json() or {}
        assert d1.get("current_step") == "PERSONAL_CONFIRMATION"

        r2 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si hasme las preguntas"}, follow_redirects=False)
        assert r2.status_code == 200
        d2 = r2.get_json() or {}
        assert d2.get("current_step") == "BASIC_INFO"

    virtuals = d2.get("virtual_messages") or []
    assert len(virtuals) >= 2
    assert str(virtuals[-1].get("text") or "").strip() != str(virtuals[-2].get("text") or "").strip()


def test_practica_personal_confirmation_acepta_frases_naturales(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    phrases = ["sii", "hasme las preguntas", "quiero trabajar", "kiero registrarme", "dale", "correcto"]
    for idx, phrase in enumerate(phrases, start=1):
        with flask_app.app_context():
            _ensure_bot_tables()
            _reset_bot_tables()
        _login_staff(client)
        create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
        conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
        with patch("admin.bot_routes.is_ai_enabled", return_value=False):
            first = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
            assert first.status_code == 200, f"first fail idx={idx}"
            second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": phrase}, follow_redirects=False)
            assert second.status_code == 200, f"second fail idx={idx}"
            payload = second.get_json() or {}
            assert payload.get("current_step") == "BASIC_INFO", f"phrase={phrase} step={payload.get('current_step')}"


def test_practica_personal_confirmation_saludos_repetidos_no_avanzan(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hols"}, follow_redirects=False)
        assert r1.status_code == 200
        d1 = r1.get_json() or {}
        assert d1.get("current_step") == "PERSONAL_CONFIRMATION"

        r2 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        assert r2.status_code == 200
        d2 = r2.get_json() or {}
        assert d2.get("current_step") == "PERSONAL_CONFIRMATION"

        r3 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        assert r3.status_code == 200
        d3 = r3.get_json() or {}
        assert d3.get("current_step") == "PERSONAL_CONFIRMATION"
        suggested = str(d3.get("suggested_reply") or "").lower()
        assert ("si o no" in suggested) or ("responde si o no" in suggested)


def test_practica_personal_confirmation_hola_luego_si_soy_yo_avanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si soy yo"}, follow_redirects=False)
        assert (second.get_json() or {}).get("current_step") == "BASIC_INFO"


def test_practica_regresion_hola_hola_hola_no_salta_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "hola", "hola"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert "si o no" in suggested
        assert "nombre" not in suggested and "edad" not in suggested
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_regresion_hola_buenas_hey_no_salta_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "buenas", "hey"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert "si o no" in suggested
        assert "nombre" not in suggested and "edad" not in suggested
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_regresion_hola_3signos_no_salta_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "???"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert "si o no" in suggested
        assert "nombre" not in suggested and "edad" not in suggested
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_regresion_hola_hola_si_soy_yo_avanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "hola", "si soy yo"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") == "BASIC_INFO"
        assert "edad" in str(payload.get("suggested_reply") or "").lower()


def test_practica_regresion_hola_hola_nombre_edad_avanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ("hola", "hola", "me llamo carmen tengo 30 años"):
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
        assert payload.get("current_step") in {"BASIC_INFO", "ADDRESS"}
        assert "si o no" not in str(payload.get("suggested_reply") or "").lower()


def test_practica_personal_confirmation_hola_luego_quiero_trabajar_avanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "quiero trabajar"}, follow_redirects=False)
        assert (second.get_json() or {}).get("current_step") == "BASIC_INFO"


def test_practica_personal_confirmation_hola_luego_buenas_no_avanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "buenas"}, follow_redirects=False)
        assert (second.get_json() or {}).get("current_step") == "PERSONAL_CONFIRMATION"


def test_practica_personal_confirmation_no_soy_yo_bloquea(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "no soy yo"}, follow_redirects=False)
        payload = second.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        assert bool(payload.get("requires_human")) is True


def test_practica_personal_confirmation_datos_implicitos_salen_de_si_no(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        s1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        p1 = s1.get_json() or {}
        assert p1.get("current_step") == "PERSONAL_CONFIRMATION"
        assert "si o no" in str(p1.get("suggested_reply") or "").lower()

        s2 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "angel manuel"}, follow_redirects=False)
        p2 = s2.get_json() or {}
        assert p2.get("current_step") == "BASIC_INFO"
        suggested2 = str(p2.get("suggested_reply") or "").lower()
        assert "edad" in suggested2
        assert "si o no" not in suggested2

        s3 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "angel y tengo 34 años"}, follow_redirects=False)
        p3 = s3.get_json() or {}
        assert p3.get("current_step") == "ADDRESS"
        suggested3 = str(p3.get("suggested_reply") or "").lower()
        assert ("vivo" in suggested3) or ("direccion" in suggested3) or ("puerto plata" in suggested3) or ("santiago" in suggested3)
        assert "si o no" not in suggested3


def test_practica_saludo_confusion_permanece_personal_confirmation(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        for txt in ["hola", "hola", "no entiendo", "quien pregunta"]:
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
        payload = (client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False).get_json() or {})
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        assert "si o no" in str(payload.get("suggested_reply") or "").lower()


def test_practica_welcome_spam_no_avanza_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        response = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "promo vendo tenis baratos"}, follow_redirects=False)
        payload = response.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_welcome_unclear_identity_no_avanza_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        response = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "quien e"}, follow_redirects=False)
        payload = response.get_json() or {}
        assert payload.get("current_step") == "PERSONAL_CONFIRMATION"
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_datos_implicitos_hola_luego_nombre_edad_avanza_address(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "angel y tengo 34 años"},
            follow_redirects=False,
        )
        payload = second.get_json() or {}
        assert payload.get("current_step") == "ADDRESS"
        suggested = str(payload.get("suggested_reply") or "").lower()
        assert ("vivo" in suggested) or ("direccion" in suggested) or ("santiago" in suggested) or ("puerto plata" in suggested)
        assert "si o no" not in suggested


def test_practica_completo_hasta_modalidad_no_regresa_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "angel y tengo 34 años"}, follow_redirects=False)
        third = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "vivo en puerto plata en cerro alto"},
            follow_redirects=False,
        )
        payload = third.get_json() or {}
        assert payload.get("current_step") == "WORK_TYPE"
        assert "si o no" not in str(payload.get("suggested_reply") or "").lower()
        assert "BASIC_INFO" not in str((payload.get("debug_protocol_state") or {}).get("current_step_after") or "")


def test_practica_nombre_solo_no_repite_si_no(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        second = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "angel manuel"}, follow_redirects=False)
        payload = second.get_json() or {}
        assert payload.get("current_step") == "BASIC_INFO"
        assert "si o no" not in str(payload.get("suggested_reply") or "").lower()


def test_practica_personal_confirmation_directo_datos_completos_a_address(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with flask_app.app_context():
        conv = BotConversation.query.get(conv_id)
        assert conv is not None
        set_current_step(conv, current_step_code="PERSONAL_CONFIRMATION", last_completed_step="WELCOME")
        db.session.commit()

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        s1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "angel y tengo 34 años"}, follow_redirects=False)
        p1 = s1.get_json() or {}
        assert p1.get("current_step") == "ADDRESS"
        assert "si o no" not in str(p1.get("suggested_reply") or "").lower()


def test_practica_no_reabre_basic_info_tras_direccion_con_cedula(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        assert r1.status_code == 200
        r2 = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "Si soy yo"}, follow_redirects=False)
        assert r2.status_code == 200
        r3 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "mi nombre es angel manuel de la Cruz y mi cedula es 213-2222222-1 y tengo 29 años"},
            follow_redirects=False,
        )
        assert r3.status_code == 200
        r4 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "vivo en puerto plata en cerro alto"},
            follow_redirects=False,
        )
        assert r4.status_code == 200

    payload = r4.get_json() or {}
    suggested = str(payload.get("suggested_reply") or "").lower()
    assert payload.get("current_step") == "WORK_TYPE"
    assert any(x in suggested for x in ("dormida", "salida", "modalidad", "trabajo"))
    assert "nombre completo" not in suggested
    assert "edad" not in suggested
    assert "cedula a mano" not in suggested

    chat_items = payload.get("chat_items") or []
    assert len(chat_items) >= 8
    assert chat_items[0].get("role") == "candidate"
    assert chat_items[1].get("role") == "bot_suggested"
    assert chat_items[2].get("role") == "candidate"
    assert chat_items[3].get("role") == "bot_suggested"
    assert chat_items[4].get("role") == "candidate"
    assert chat_items[5].get("role") == "bot_suggested"

    debug_state = payload.get("debug_protocol_state") or {}
    assert debug_state.get("current_step_before") == "ADDRESS"
    assert debug_state.get("current_step_after") == "WORK_TYPE"


def test_nueva_practica_no_hereda_metadata_ni_mensajes(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create_a = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_a = int((create_a.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_a}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_a}/mensaje", json={"text": "si soy yo"}, follow_redirects=False)
        client.post(
            f"/admin/bot/practica/{conv_a}/mensaje",
            json={"text": "me llamo ana y tengo 28 años en puerto plata"},
            follow_redirects=False,
        )

    create_b = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_b = int((create_b.headers.get("Location") or "").rstrip("/").split("/")[-1])
    assert conv_b != conv_a
    state_b = client.get(f"/admin/bot/practica/{conv_b}/estado", follow_redirects=False)
    payload = state_b.get_json() or {}
    assert payload.get("current_step") == "WELCOME"
    assert payload.get("messages") == []
    assert payload.get("chat_items") == []
    assert payload.get("protocol_entities") == {}
    assert payload.get("protocol_future_entities") == {}
    assert payload.get("pending_corrections") == []
    assert payload.get("virtual_messages") == []


def test_reiniciar_practica_crea_nueva_conversacion_limpia(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si soy yo"}, follow_redirects=False)

    reset = client.post(f"/admin/bot/practica/{conv_id}/control", json={"action": "reset"}, follow_redirects=False)
    assert reset.status_code == 200
    data = reset.get_json() or {}
    assert data.get("ok") is True
    assert data.get("redirect_url")
    new_conv_id = int(data.get("conversation_id"))
    assert new_conv_id != conv_id
    assert data.get("current_step") == "WELCOME"
    assert data.get("messages") == []
    assert data.get("chat_items") == []
    assert data.get("protocol_entities") == {}
    assert data.get("protocol_future_entities") == {}
    assert data.get("virtual_messages") == []


def test_future_entity_city_se_consume_al_responder_address(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si soy yo"}, follow_redirects=False)
        m3 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "me llamo juana y tengo 30 años y vivo en puerto plata"},
            follow_redirects=False,
        )
        p3 = m3.get_json() or {}
        assert "city" in ((p3.get("metadata_json") or {}).get("protocol_future_entities") or {})
        m4 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "vivo en puerto plata en cerro alto"},
            follow_redirects=False,
        )
    p4 = m4.get_json() or {}
    assert p4.get("current_step") == "WORK_TYPE"
    assert "city" not in (p4.get("protocol_future_entities") or {})
    assert (p4.get("protocol_entities") or {}).get("city") == "Puerto Plata"


def test_practica_address_work_type_sync_exact_case_salida_diaria(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si"}, follow_redirects=False)
        client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "me llamo carmen tengo 30 años"},
            follow_redirects=False,
        )
        client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "puerto plata centro"},
            follow_redirects=False,
        )
        r5 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "salida diaria"},
            follow_redirects=False,
        )

    payload = r5.get_json() or {}
    suggested = str(payload.get("suggested_reply") or "").lower()
    assert payload.get("current_step") == "TRANSPORT_ROUTE"
    assert str((payload.get("debug_protocol_state") or {}).get("last_completed_step") or "") == "WORK_TYPE"
    assert any(x in suggested for x in ("ruta", "transporte", "concho", "cercana"))
    assert "ciudad" not in suggested
    assert "sector" not in suggested
    assert "salida diaria" in str((payload.get("protocol_entities") or {}).get("work_type") or "").lower()

    chat_items = payload.get("chat_items") or []
    roles = [str(x.get("role") or "") for x in chat_items]
    assert roles[:10] == [
        "candidate",
        "bot_suggested",
        "candidate",
        "bot_suggested",
        "candidate",
        "bot_suggested",
        "candidate",
        "bot_suggested",
        "candidate",
        "bot_suggested",
    ]


def test_practica_address_work_type_sync_exact_case_dormida(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
        client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "si"}, follow_redirects=False)
        client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "me llamo carmen tengo 30 años"},
            follow_redirects=False,
        )
        client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "santiago gurabo"},
            follow_redirects=False,
        )
        r5 = client.post(
            f"/admin/bot/practica/{conv_id}/mensaje",
            json={"text": "dormida"},
            follow_redirects=False,
        )

    payload = r5.get_json() or {}
    assert payload.get("current_step") == "TRANSPORT_ROUTE"
    assert str((payload.get("debug_protocol_state") or {}).get("last_completed_step") or "") == "WORK_TYPE"


def test_practica_json_includes_ai_fields_flag_off(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert "base_suggested_reply" in payload
    assert "ai_suggested_reply" in payload
    assert "ai_reply_used" in payload
    assert "ai_reply_safety_status" in payload
    assert "ai_reply_fallback_reason" in payload
    assert payload.get("ai_reply_used") is False


def test_practica_flag_on_uses_ai_or_fallback(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch(
        "services.bot_practice_ai_reply_service._call_provider",
        return_value="Para continuar, responde SI o NO.",
    ):
        resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ai_reply_safety_status") in {"ok", "fallback", "disabled"}
    assert payload.get("base_suggested_reply")


def test_practica_ai_dangerous_promise_falls_back(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    with patch(
        "services.bot_practice_ai_reply_service._call_provider",
        return_value="Perfecto, te conseguimos empleo y ya estás aprobada",
    ):
        resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": "hola"}, follow_redirects=False)
    payload = resp.get_json() or {}
    assert payload.get("ai_reply_used") is False
    assert payload.get("ai_reply_fallback_reason") == "dangerous_promise"


def test_practica_ai_on_off_does_not_change_step_entities_or_order(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)

    create_off = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_off = int((create_off.headers.get("Location") or "").rstrip("/").split("/")[-1])
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "false")
    off_resp = client.post(f"/admin/bot/practica/{conv_off}/mensaje", json={"text": "hola"}, follow_redirects=False)
    off_payload = off_resp.get_json() or {}

    create_on = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_on = int((create_on.headers.get("Location") or "").rstrip("/").split("/")[-1])
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch(
        "services.bot_practice_ai_reply_service._call_provider",
        return_value="Por favor confirma si eres tú: responde SI o NO.",
    ):
        on_resp = client.post(f"/admin/bot/practica/{conv_on}/mensaje", json={"text": "hola"}, follow_redirects=False)
    on_payload = on_resp.get_json() or {}

    assert off_payload.get("current_step") == on_payload.get("current_step")
    assert off_payload.get("protocol_entities") == on_payload.get("protocol_entities")
    assert isinstance(on_payload.get("chat_items"), list)
    roles = [str(x.get("role") or "") for x in (on_payload.get("chat_items") or [])]
    assert roles[:2] == ["candidate", "bot_suggested"]
    assert on_payload.get("ai_reply_used") is True

    with flask_app.app_context():
        assert BotMessage.query.filter_by(conversation_id=conv_on, direction="outbound").count() == 0
        assert BotMessage.query.filter_by(conversation_id=conv_off, direction="outbound").count() == 0
