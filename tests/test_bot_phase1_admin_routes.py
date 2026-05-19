# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting, Cliente
from services.bot_conversation_service import set_current_step


def _ensure_bot_tables() -> None:
    BotCandidateDraft.__table__.drop(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)


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
    login_data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        assert login_page.status_code == 200
        login_data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=login_data, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m is not None, "No se encontró csrf_token en la vista."
    return m.group(1)


def test_bot_routes_require_staff_for_anonymous_and_non_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    anon_get = client.get("/admin/bot/conversaciones", follow_redirects=False)
    assert anon_get.status_code in (302, 303)
    assert "/admin/login" in (anon_get.headers.get("Location") or "")

    anon_post = client.post("/admin/bot/conversaciones/1/simular-inbound", data={"body": "hola"}, follow_redirects=False)
    assert anon_post.status_code in (302, 303)
    assert "/admin/login" in (anon_post.headers.get("Location") or "")

    with flask_app.app_context():
        cliente = Cliente(
            codigo="CL-BOT-TEST-ROUTES-1",
            nombre_completo="Cliente Bot Test",
            email="cliente.bot.routes.1@example.com",
            telefono="8095550001",
            password_hash="DISABLED_RESET_REQUIRED",
        )
        db.session.add(cliente)
        db.session.commit()
        cliente_id = int(cliente.id)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(cliente_id)
        sess["_fresh"] = True
        sess["is_admin_session"] = False

    non_staff_get = client.get("/admin/bot/conversaciones", follow_redirects=False)
    assert non_staff_get.status_code in (302, 303)
    assert "/admin/login" in (non_staff_get.headers.get("Location") or "")

    non_staff_post = client.post("/admin/bot/conversaciones/1/simular-inbound", data={"body": "hola"}, follow_redirects=False)
    assert non_staff_post.status_code in (302, 303)
    assert "/admin/login" in (non_staff_post.headers.get("Location") or "")


def test_bot_get_views_render_without_data_for_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)

    list_resp = client.get("/admin/bot/conversaciones", follow_redirects=False)
    assert list_resp.status_code == 200
    html = list_resp.get_data(as_text=True)
    assert "Bot WhatsApp - Conversaciones" in html
    assert "No hay conversaciones." in html

    cfg_resp = client.get("/admin/bot/configuracion", follow_redirects=False)
    assert cfg_resp.status_code == 200
    cfg_html = cfg_resp.get_data(as_text=True)
    assert "Configuración Bot WhatsApp" in cfg_html
    assert ("Sin settings iniciales cargados." in cfg_html) or ("<th>Key</th>" in cfg_html)


def test_bot_routes_with_data_and_status_actions():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550999", contact_name="Contacto QA", status="open")
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(
            conversation_id=conv.id,
            direction="outbound",
            source="admin_manual",
            message_type="text",
            text_body="hola inicial",
            status="queued",
        )
        db.session.add(msg)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert "Conversación #" in detail_html
    assert "hola inicial" in detail_html
    assert "Admin" in detail_html

    post_msg = client.post(
        f"/admin/bot/conversaciones/{conv_id}/mensaje",
        data={"body": "nuevo mensaje staff"},
        follow_redirects=False,
    )
    assert post_msg.status_code in (302, 303)
    assert f"/admin/bot/conversaciones/{conv_id}" in (post_msg.headers.get("Location") or "")

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert conv.last_message_at is not None
        stored = BotMessage.query.filter_by(conversation_id=conv_id).order_by(BotMessage.id.desc()).first()
        assert stored is not None
        assert stored.text_body == "nuevo mensaje staff"

    pause = client.post(f"/admin/bot/conversaciones/{conv_id}/pausar", data={"reason": "qa"}, follow_redirects=False)
    assert pause.status_code in (302, 303)
    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert conv.status == "bot_paused"
        assert conv.bot_paused is True

    activate = client.post(f"/admin/bot/conversaciones/{conv_id}/activar", data={}, follow_redirects=False)
    assert activate.status_code in (302, 303)
    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert conv.status == "open"
        assert conv.bot_paused is False

    resolve = client.post(f"/admin/bot/conversaciones/{conv_id}/resolver", data={}, follow_redirects=False)
    assert resolve.status_code in (302, 303)
    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert conv.status == "resolved"
        assert conv.resolved_at is not None


def test_bot_create_manual_conversation_post_and_detail_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)

    create = client.post(
        "/admin/bot/conversaciones/nueva",
        data={"phone_e164": "+18095550123", "contact_name": "Alta Manual"},
        follow_redirects=False,
    )
    assert create.status_code in (302, 303)
    location = create.headers.get("Location") or ""
    assert "/admin/bot/conversaciones/" in location

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095550123").first()
        assert conv is not None
        assert conv.contact_name == "Alta Manual"


def test_bot_post_routes_enforce_csrf_when_enabled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550777", contact_name="CSRF Test", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    client_missing = flask_app.test_client()
    _login_staff(client_missing)

    missing = client_missing.post(
        f"/admin/bot/conversaciones/{conv_id}/mensaje",
        data={"body": "sin token"},
        follow_redirects=False,
    )
    assert missing.status_code in (302, 303)
    assert f"/admin/bot/conversaciones/{conv_id}/mensaje" in (missing.headers.get("Location") or "")

    client_ok = flask_app.test_client()
    _login_staff(client_ok)
    form_page = client_ok.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert form_page.status_code == 200
    csrf_token = _extract_csrf(form_page.get_data(as_text=True))

    ok = client_ok.post(
        f"/admin/bot/conversaciones/{conv_id}/mensaje",
        data={"csrf_token": csrf_token, "body": "con token"},
        follow_redirects=False,
    )
    assert ok.status_code in (302, 303)
    assert f"/admin/bot/conversaciones/{conv_id}" in (ok.headers.get("Location") or "")


def test_simular_inbound_crea_mensaje_inbound_y_renderiza_decisiones(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550888", contact_name="Inbound QA", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="ADDRESS", last_completed_step="BASIC_INFO")
        conv_id = int(conv.id)

    _login_staff(client)

    from unittest.mock import patch

    with patch("admin.bot_routes.is_ai_enabled", return_value=True), patch("admin.bot_routes.is_autoreply_enabled", return_value=False), patch(
        "admin.bot_routes.classify_intent",
        return_value={
            "ok": True,
            "intent": "FAQ_REQUISITOS",
            "answer_text": "Te orientamos con requisitos generales.",
            "confidence": 0.93,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    ), patch("admin.bot_routes.send_text_message") as send_mock:
        resp = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "hola inbound"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        inbound = BotMessage.query.filter_by(conversation_id=conv_id).order_by(BotMessage.id.desc()).first()
        assert inbound is not None
        assert inbound.direction == "inbound"
        assert inbound.source == "whatsapp_user"
        assert inbound.status == "received"
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        ai_dec = BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="ai_classification").first()
        assert ai_dec is not None
        assert (ai_dec.facts_json or {}).get("current_step_code") == "ADDRESS"
        assert (ai_dec.facts_json or {}).get("protocol_version") == "domesticas_v1"
        auto_dec = BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="auto_reply").first()
        assert auto_dec is not None
        assert auto_dec.decision_result == "manual_only"

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "FAQ_REQUISITOS" in html
    assert "Sugerencia IA para este mensaje" in html
    assert "Protocolo:" in html
    assert "Etapa:</strong> ADDRESS" in html
    assert "Decisiones IA de este inbound" in html
    assert "Copiar sugerencia" in html
    assert "Usar como respuesta" in html
    assert "La IA sugirió, pero no envió nada." in html
    assert "Contacto" in html
    assert "Responder manualmente" in html
    assert "Dry-run activo" in html
    assert "WhatsApp real apagado" in html
    assert "Estado IA y Seguridad" in html
    assert "límite diario IA:" in html


def test_protocol_auto_advance_flag_off_no_advanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550011", contact_name="Auto Off", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="PERSONAL_CONFIRMATION", last_completed_step="WELCOME")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        resp = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "si"}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "PERSONAL_CONFIRMATION"


def test_protocol_auto_advance_personal_confirmation_si_to_basic_info(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550012", contact_name="Auto On", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="PERSONAL_CONFIRMATION", last_completed_step="WELCOME")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        resp = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "si"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("last_completed_step") == "PERSONAL_CONFIRMATION"
        assert meta.get("current_step_code") == "BASIC_INFO"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_LOCAL"
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Próximo mensaje sugerido" in html
    assert "Etapa completada automáticamente en modo local/dry-run." in html
    assert "Auto-avance protocolo: activo" in html


def test_protocol_auto_advance_invalid_answer_no_advanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550013", contact_name="Auto Bad", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="PERSONAL_CONFIRMATION", last_completed_step="WELCOME")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        resp = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "tal vez"}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "PERSONAL_CONFIRMATION"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_INVALID_ANSWER"


def test_protocol_auto_advance_requires_human_step_no_advanza(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550014", contact_name="Auto Human", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="DOCUMENT_REQUEST", last_completed_step="GROUP_WARNING")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        resp = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "enviaré cédula ahora"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "DOCUMENT_REQUEST"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_BLOCKED_HUMAN"


def test_basic_info_partial_then_complete_advances_with_required_fields(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550015", contact_name="Basic Partial", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="BASIC_INFO", last_completed_step="PERSONAL_CONFIRMATION")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        r1 = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "me llamo angel manuel"}, follow_redirects=False)
        r2 = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "tengo 26 años"}, follow_redirects=False)
    assert r1.status_code in (302, 303)
    assert r2.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        entities = dict(meta.get("protocol_entities") or {})
        assert entities.get("name") == "angel manuel"
        assert entities.get("age") == 26
        assert meta.get("last_completed_step") == "BASIC_INFO"
        assert meta.get("current_step_code") == "ADDRESS"
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        latest = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert latest is not None
        assert latest.rule_code == "PROTOCOL_AUTO_ADVANCE_LOCAL"

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Entidades detectadas:" in html
    assert "información suficiente para avanzar".lower() in html.lower()


def test_basic_info_multi_answer_stores_future_entities_and_limits_single_advance(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500151", contact_name="Basic Multi", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="BASIC_INFO", last_completed_step="PERSONAL_CONFIRMATION")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "me llamo juana tengo 32 vivo en santiago quiero dormida"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("last_completed_step") == "BASIC_INFO"
        assert meta.get("current_step_code") == "ADDRESS"
        future = dict(meta.get("protocol_future_entities") or {})
        assert (future.get("city") or {}).get("value") == "Santiago"
        assert (future.get("work_type") or {}).get("value") == "dormida"
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_LOCAL"
        facts = dec.facts_json or {}
        assert facts.get("multi_entity_message") is True
        assert facts.get("auto_advance_limited_to_one_step") is True
        future_detected = facts.get("future_entities_detected") or {}
        assert future_detected.get("city") == "Santiago"
        assert future_detected.get("work_type") == "dormida"

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Datos adicionales detectados" in html
    assert "No se avanzaron etapas futuras automáticamente." in html


def test_basic_info_with_cedula_detected_blocks_for_human_and_masks(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550016", contact_name="Basic Cedula", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="BASIC_INFO", last_completed_step="PERSONAL_CONFIRMATION")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "mi nombre es ana y tengo 33 años mi cedula es 40212345678"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "BASIC_INFO"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_BLOCKED_HUMAN"
        masked = ((dec.facts_json or {}).get("entities_detected") or {}).get("cedula_masked")
        assert masked == "402-2***-***"


def test_transport_route_out_of_step_work_type_does_not_advance(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550017", contact_name="TR OutStep", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "no, con dormida mejor"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert (conv.metadata_json or {}).get("current_step_code") == "TRANSPORT_ROUTE"
        dec = (
            BotDecisionLog.query.filter(BotDecisionLog.conversation_id == conv_id)
            .filter(BotDecisionLog.decision_type.in_(["protocol_auto_advance", "protocol_pending_correction"]))
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_PENDING_CORRECTION_DETECTED"
        facts = dec.facts_json or {}
        corr = facts.get("pending_correction") or {}
        assert corr.get("field") == "work_type"
        assert corr.get("suggested_step_code") == "WORK_TYPE"
        assert corr.get("requires_human") is True
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Corrección pendiente detectada" in html
    assert "Requiere confirmación humana" in html


def test_transport_route_valid_transport_advances(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550018", contact_name="TR OK", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "voy en concho por la ruta M"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)
    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert (conv.metadata_json or {}).get("current_step_code") == "PREVIOUS_AGENCY"


def test_transport_route_valid_transport_auto_off_valid_no_advance(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500181", contact_name="TR OFF", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "voy en concho por la ruta M"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        assert (conv.metadata_json or {}).get("current_step_code") == "TRANSPORT_ROUTE"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_AUTO_ADVANCE_DISABLED"
        assert ((dec.facts_json or {}).get("validation_result") or {}).get("matched") is True

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Respuesta válida, auto-avance desactivado." in html


def test_pending_correction_detected_saved_audited_and_not_advance(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500182", contact_name="Corr", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        meta = dict(conv.metadata_json or {})
        meta["protocol_entities"] = {"work_type": "salida diaria", "route": "ruta K", "age": 26, "name": "Angel"}
        conv.metadata_json = meta
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "mejor dormida"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "TRANSPORT_ROUTE"
        assert (meta.get("protocol_entities") or {}).get("work_type") == "salida diaria"
        pending = list(meta.get("pending_corrections") or [])
        assert len(pending) == 1
        assert pending[0].get("field") == "work_type"
        assert pending[0].get("new_value") == "dormida"
        assert pending[0].get("status") == "pending_human"

        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_pending_correction")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.decision_result == "manual_only"
        assert dec.rule_code == "PROTOCOL_PENDING_CORRECTION_DETECTED"
        facts = dec.facts_json or {}
        corr = facts.get("pending_correction") or {}
        assert corr.get("field") == "work_type"
        assert corr.get("old_value") == "salida diaria"
        assert corr.get("new_value") == "dormida"
        assert corr.get("suggested_step_code") == "WORK_TYPE"
        assert corr.get("requires_human") is True
        assert corr.get("normalized_text") == "mejor dormida"
        assert corr.get("original_text") == "mejor dormida"

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Corrección pendiente detectada" in html
    assert "Requiere confirmación humana" in html
    assert "Pendiente de confirmar" in html


def test_pending_correction_age_with_prefix_does_not_advance_or_overwrite(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500183", contact_name="Corr Age", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        meta = dict(conv.metadata_json or {})
        meta["protocol_entities"] = {"work_type": "salida diaria", "route": "ruta K"}
        conv.metadata_json = meta
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        r1 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "no, mi edad es 30"},
            follow_redirects=False,
        )
    assert r1.status_code in (302, 303)
    send_mock.assert_not_called()

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        meta = dict(conv.metadata_json or {})
        assert meta.get("current_step_code") == "TRANSPORT_ROUTE"
        assert (meta.get("protocol_entities") or {}).get("age") is None
        pending = list(meta.get("pending_corrections") or [])
        assert len(pending) == 1
        assert pending[0].get("field") == "age"
        assert pending[0].get("new_value") == "30"
        assert pending[0].get("old_value") is None
        assert pending[0].get("suggested_step_code") == "BASIC_INFO"

        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_pending_correction")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "PROTOCOL_PENDING_CORRECTION_DETECTED"
        corr = (dec.facts_json or {}).get("pending_correction") or {}
        assert corr.get("field") == "age"
        assert corr.get("new_value") == "30"
        assert corr.get("suggested_step_code") == "BASIC_INFO"
        assert corr.get("requires_human") is True
        assert corr.get("normalized_text") == "no, mi edad es 30"
        assert corr.get("original_text") == "no, mi edad es 30"
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Corrección pendiente detectada" in html
    assert "No se aplicó automáticamente." in html


def test_pending_correction_approve_and_apply_entities(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500184", contact_name="Approve Corr", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "no, mi edad es 30"}, follow_redirects=False)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        corr_id = int(((conv.metadata_json or {}).get("pending_corrections") or [])[0].get("id"))

    resp = client.post(f"/admin/bot/conversaciones/{conv_id}/correcciones/{corr_id}/aprobar", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        meta = dict(conv.metadata_json or {})
        corr = [x for x in list(meta.get("pending_corrections") or []) if int(x.get("id") or 0) == corr_id][0]
        assert corr.get("status") == "approved"
        assert corr.get("approved_by") is not None
        assert (meta.get("protocol_entities") or {}).get("age") == "30"
        assert (meta.get("current_step_code") or "") == "TRANSPORT_ROUTE"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_correction_approved")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert (dec.facts_json or {}).get("correction_id") == corr_id


def test_pending_correction_reject_no_apply_and_rules(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500185", contact_name="Reject Corr", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        meta = dict(conv.metadata_json or {})
        meta["protocol_entities"] = {"work_type": "salida diaria"}
        conv.metadata_json = meta
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "mejor dormida"}, follow_redirects=False)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        corr_id = int(((conv.metadata_json or {}).get("pending_corrections") or [])[0].get("id"))
        before_entities = dict((conv.metadata_json or {}).get("protocol_entities") or {})

    rej = client.post(
        f"/admin/bot/conversaciones/{conv_id}/correcciones/{corr_id}/rechazar",
        data={"rejection_reason": "no confirmada"},
        follow_redirects=False,
    )
    assert rej.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        meta = dict(conv.metadata_json or {})
        corr = [x for x in list(meta.get("pending_corrections") or []) if int(x.get("id") or 0) == corr_id][0]
        assert corr.get("status") == "rejected"
        assert corr.get("rejected_by") is not None
        assert corr.get("rejection_reason") == "no confirmada"
        assert dict(meta.get("protocol_entities") or {}) == before_entities
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_correction_rejected")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None

    bad_approve = client.post(f"/admin/bot/conversaciones/{conv_id}/correcciones/{corr_id}/aprobar", data={}, follow_redirects=False)
    assert bad_approve.status_code in (302, 303)


def test_pending_correction_duplicate_and_superseded(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500186", contact_name="Sup Corr", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "no, mi edad es 30"}, follow_redirects=False)
        client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "nop, tengo 30"}, follow_redirects=False)
        client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "perdón tengo 31"}, follow_redirects=False)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        items = list((conv.metadata_json or {}).get("pending_corrections") or [])
        assert len(items) == 2
        sup = [x for x in items if str(x.get("status")) == "superseded"][0]
        pend = [x for x in items if str(x.get("status")) == "pending_human"][0]
        assert int(sup.get("duplicate_count") or 0) >= 2
        assert int(sup.get("superseded_by_id") or 0) == int(pend.get("id") or 0)


def test_pending_correction_invalid_id_and_ui_grouped(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955500187", contact_name="UI Corr", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="TRANSPORT_ROUTE", last_completed_step="WORK_TYPE")
        conv_id = int(conv.id)

    _login_staff(client)
    bad = client.post(f"/admin/bot/conversaciones/{conv_id}/correcciones/9999/aprobar", data={}, follow_redirects=False)
    assert bad.status_code in (302, 303)

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Pendientes" in html
    assert "Aprobadas" in html
    assert "Rechazadas" in html
    assert "Reemplazadas" in html


def test_out_of_step_other_stage_cases(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv1 = BotConversation(channel="whatsapp", phone_e164="+18095550019", contact_name="ADDR outstep", status="open")
        conv2 = BotConversation(channel="whatsapp", phone_e164="+18095550020", contact_name="BASIC outstep", status="open")
        conv3 = BotConversation(channel="whatsapp", phone_e164="+18095550021", contact_name="PCT outstep", status="open")
        db.session.add_all([conv1, conv2, conv3])
        db.session.commit()
        set_current_step(conv1, current_step_code="ADDRESS", last_completed_step="BASIC_INFO")
        set_current_step(conv2, current_step_code="BASIC_INFO", last_completed_step="PERSONAL_CONFIRMATION")
        set_current_step(conv3, current_step_code="PERCENTAGE_ACCEPTANCE", last_completed_step="PREVIOUS_AGENCY")
        ids = [int(conv1.id), int(conv2.id), int(conv3.id)]

    _login_staff(client)
    with patch("admin.bot_routes.is_ai_enabled", return_value=False):
        client.post(f"/admin/bot/conversaciones/{ids[0]}/simular-inbound", data={"body": "salida diaria"}, follow_redirects=False)
        client.post(f"/admin/bot/conversaciones/{ids[1]}/simular-inbound", data={"body": "quiero dormida"}, follow_redirects=False)
        client.post(f"/admin/bot/conversaciones/{ids[2]}/simular-inbound", data={"body": "vivo en Santiago"}, follow_redirects=False)

    with flask_app.app_context():
        for conv_id, expected in zip(ids, ["WORK_TYPE", "WORK_TYPE", "ADDRESS"]):
            dec = (
                BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_auto_advance")
                .order_by(BotDecisionLog.id.desc())
                .first()
            )
            assert dec is not None
            assert dec.rule_code == "PROTOCOL_OUT_OF_STEP_ANSWER"
            details = (dec.facts_json or {}).get("out_of_step_details") or {}
            assert details.get("suggested_step_code") == expected


def test_detalle_asocia_sugerencias_a_inbound_correcto_en_multiples_mensajes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550890", contact_name="Inbound Multi", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    from unittest.mock import patch

    intents = iter(
        [
            {
                "ok": True,
                "intent": "FAQ_REQUISITOS",
                "answer_text": "Respuesta requisitos.",
                "confidence": 0.91,
                "requires_human": False,
                "prompt_version": "phase4_v1",
                "ai_model": "fake",
            },
            {
                "ok": True,
                "intent": "FAQ_UBICACION",
                "answer_text": "Respuesta ubicacion.",
                "confidence": 0.92,
                "requires_human": False,
                "prompt_version": "phase4_v1",
                "ai_model": "fake",
            },
            {
                "ok": True,
                "intent": "UNKNOWN",
                "answer_text": "Respuesta hola.",
                "confidence": 0.9,
                "requires_human": True,
                "prompt_version": "phase4_v1",
                "ai_model": "fake",
            },
        ]
    )

    with patch("admin.bot_routes.is_ai_enabled", return_value=True), patch("admin.bot_routes.is_autoreply_enabled", return_value=False), patch(
        "admin.bot_routes.classify_intent", side_effect=lambda *_a, **_k: next(intents)
    ):
        r1 = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "cuales son requisitos"}, follow_redirects=False)
        r2 = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "donde estan ubicados"}, follow_redirects=False)
        r3 = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "hola"}, follow_redirects=False)
    assert r1.status_code in (302, 303)
    assert r2.status_code in (302, 303)
    assert r3.status_code in (302, 303)

    with flask_app.app_context():
        inbound_ids = [
            int(x.id)
            for x in BotMessage.query.filter_by(conversation_id=conv_id, direction="inbound")
            .order_by(BotMessage.id.asc())
            .all()
        ]
    assert len(inbound_ids) == 3

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert html.count("Sugerencia IA para este mensaje") >= 3
    assert "Respuesta requisitos." in html
    assert "Respuesta ubicacion." in html
    assert "Respuesta hola." in html
    assert "data-inbound-message-id=" in html

    last_block = re.search(
        rf'data-inbound-message-id="{inbound_ids[2]}".*?(?=data-inbound-message-id="|\Z)',
        html,
        flags=re.S,
    )
    assert last_block is not None
    assert "Respuesta hola." in last_block.group(0)
    assert "Respuesta requisitos." not in last_block.group(0)


def test_detalle_muestra_sin_sugerencia_para_inbound_sin_decisiones():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550901", contact_name="Inbound Sin IA", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    from unittest.mock import patch

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.classify_intent") as ai_mock:
        off = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "hola sin ia"},
            follow_redirects=False,
        )
    assert off.status_code in (302, 303)
    ai_mock.assert_not_called()

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Sin sugerencia IA" in html
    assert ("Sin decisiones IA para este inbound." in html) or ("protocol_auto_advance" in html)


def test_simular_inbound_texto_vacio_rechaza_y_ai_apagada_no_llama_clasificador():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550889", contact_name="Inbound QA2", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    empty = client.post(f"/admin/bot/conversaciones/{conv_id}/simular-inbound", data={"body": "   "}, follow_redirects=False)
    assert empty.status_code in (302, 303)

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.classify_intent") as ai_mock:
        off = client.post(
            f"/admin/bot/conversaciones/{conv_id}/simular-inbound",
            data={"body": "mensaje sin ia"},
            follow_redirects=False,
        )
    assert off.status_code in (302, 303)
    ai_mock.assert_not_called()


def test_detalle_conversacion_no_falla_con_pending_corrections_dict_sin_id():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(
            channel="whatsapp",
            phone_e164="+180955501001",
            contact_name="Dict Corr",
            status="open",
            metadata_json={
                "protocol_version": "domesticas_v1",
                "pending_corrections": [
                    {"field": "age", "status": "pending_human", "old_value": "24", "new_value": "25"}
                ],
                "protocol_entities": {"name": "Ana"},
            },
        )
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Pendientes" in html
    assert "Sin ID para aprobar/rechazar" in html
    assert "Resumen de candidata" in html
    assert "Protocolo de captación" in html


def test_detalle_conversacion_renderiza_burbujas_y_sugerencia_inbound():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+180955501002", contact_name="UI Chat", status="open")
        db.session.add(conv)
        db.session.flush()
        inbound = BotMessage(
            conversation_id=conv.id,
            direction="inbound",
            source="whatsapp_user",
            message_type="text",
            text_body="hola",
            status="inbound_received",
        )
        outbound = BotMessage(
            conversation_id=conv.id,
            direction="outbound",
            source="admin_manual",
            message_type="text",
            text_body="respuesta",
            status="queued",
        )
        db.session.add_all([inbound, outbound])
        db.session.flush()
        db.session.add(
            BotDecisionLog(
                conversation_id=conv.id,
                message_id=inbound.id,
                decision_type="ai_classification",
                decision_result="manual_only",
                rule_code="AI_CLASSIFICATION_LOCAL",
                reason_human="sugerencia",
                facts_json={
                    "intent": "FAQ_REQUISITOS",
                    "confidence": 0.9,
                    "requires_human": True,
                    "suggested_reply": "Te explico los requisitos.",
                },
            )
        )
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert 'class="msg-row inbound"' in html
    assert 'class="msg-row outbound"' in html
    assert "Sugerencia IA para este mensaje" in html
    assert "Te explico los requisitos." in html
    assert "admin_bot_chat.css" in html


def test_protocol_manual_routes_require_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    anon = client.post("/admin/bot/conversaciones/1/protocolo/avanzar", data={}, follow_redirects=False)
    assert anon.status_code in (302, 303)
    assert "/admin/login" in (anon.headers.get("Location") or "")


def test_protocol_manual_routes_change_only_state_and_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550988", contact_name="Proto Route", status="open", metadata_json={})
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    from unittest.mock import patch

    with patch("admin.bot_routes.classify_intent") as ai_mock, patch("admin.bot_routes.send_text_message") as wa_mock:
        r1 = client.post(f"/admin/bot/conversaciones/{conv_id}/protocolo/avanzar", data={}, follow_redirects=False)
        r2 = client.post(f"/admin/bot/conversaciones/{conv_id}/protocolo/retroceder", data={}, follow_redirects=False)
        r3 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/protocolo/seleccionar",
            data={"step_code": "ADDRESS"},
            follow_redirects=False,
        )
        r4 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/protocolo/seleccionar",
            data={"step_code": "INVALID_STEP"},
            follow_redirects=False,
        )
        r5 = client.post(
            f"/admin/bot/conversaciones/{conv_id}/protocolo/reiniciar",
            data={"confirm_reset": "REINICIAR"},
            follow_redirects=False,
        )
        ai_mock.assert_not_called()
        wa_mock.assert_not_called()

    assert r1.status_code in (302, 303)
    assert r2.status_code in (302, 303)
    assert r3.status_code in (302, 303)
    assert r4.status_code in (302, 303)
    assert r5.status_code in (302, 303)

    with flask_app.app_context():
        conv = db.session.get(BotConversation, conv_id)
        assert conv is not None
        metadata = dict(conv.metadata_json or {})
        assert metadata.get("current_step_code") == "WELCOME"
        assert metadata.get("protocol_version") == "domesticas_v1"

        outbound_count = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbound_count == 0

        logs = (
            BotDecisionLog.query.filter_by(conversation_id=conv_id, decision_type="protocol_step_change")
            .order_by(BotDecisionLog.id.asc())
            .all()
        )
        assert len(logs) >= 4
        assert all(x.decision_result == "manual_only" for x in logs)
        actions = {(x.facts_json or {}).get("action") for x in logs}
        assert {"advance", "regress", "select", "reset"}.issubset(actions)
