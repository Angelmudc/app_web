# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
from services.whatsapp_cloud_service import send_text_message
from services.whatsapp_webhook_security import validate_whatsapp_signature, verify_webhook_token


@pytest.fixture(autouse=True)
def _force_safe_bot_flags(monkeypatch):
    # Aisla tests legacy de Fase 2 ante estado/env residual de Fase 4.
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotSandboxReviewQueue.__table__.create(bind=db.engine, checkfirst=True)
    BotSandboxOutbound.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotSandboxReviewQueue).delete()
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def test_webhook_real_auto_reply_on_sends_without_manual_approval(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550041")
    monkeypatch.setenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [{"changes": [{"value": {"contacts": [{"wa_id": "18095550041", "profile": {"name": "Auto"}}], "messages": [{"id": "wamid-real-auto-1", "from": "18095550041", "timestamp": "1715000021", "type": "text", "text": {"body": "hola autoreply test"}}]}}]}]
    }
    with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "wa_message_id": "wamid.auto.real.1", "http_status": 200, "raw_response": {"messages": [{"id": "wamid.auto.real.1"}]}}):
        resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.join(BotMessage, BotMessage.id == BotSandboxReviewQueue.inbound_message_id).filter(BotMessage.wa_message_id == "wamid-real-auto-1").first()
        assert review is not None
        assert review.status == "simulated_sent"
        assert review.outbound_message_id is not None
        outbox = BotSandboxOutbound.query.filter_by(bot_message_id=int(review.outbound_message_id)).first()
        assert outbox is not None
        assert outbox.state == "simulated_sent"
        outbound = BotMessage.query.get(int(review.outbound_message_id))
        assert outbound is not None
        assert str(outbound.wa_message_id or "") == "wamid.auto.real.1"


def test_webhook_real_auto_reply_duplicate_does_not_duplicate_send(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550041")
    monkeypatch.setenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [{"changes": [{"value": {"contacts": [{"wa_id": "18095550041", "profile": {"name": "Auto"}}], "messages": [{"id": "wamid-real-auto-dup", "from": "18095550041", "timestamp": "1715000021", "type": "text", "text": {"body": "hola autoreply test"}}]}}]}]
    }
    with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "wa_message_id": "wamid.auto.real.dup", "http_status": 200, "raw_response": {"messages": [{"id": "wamid.auto.real.dup"}]}}) as send_mock:
        r1 = client.post("/bot/whatsapp/webhook", json=payload)
        r2 = client.post("/bot/whatsapp/webhook", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert send_mock.call_count == 1
    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1
        assert BotSandboxOutbound.query.count() == 1


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _login_staff(client):
    flask_app.config["WTF_CSRF_ENABLED"] = False
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_webhook_get_verify_token_ok_and_forbidden(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "token-ok")

    ok = client.get("/bot/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=token-ok&hub.challenge=123")
    assert ok.status_code == 200
    assert ok.get_data(as_text=True) == "123"

    bad = client.get("/bot/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=777")
    assert bad.status_code == 403


def test_signature_validation_unit():
    body = b'{"ok":1}'
    secret = "app-secret"
    good_header = _sign(body, secret)
    bad_header = "sha256=deadbeef"
    assert validate_whatsapp_signature(body, good_header, secret) is True
    assert validate_whatsapp_signature(body, bad_header, secret) is False


def test_webhook_post_text_inbound_create_and_deduplicate(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "true")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "sec-1")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "18095550001", "profile": {"name": "Ana"}}],
                            "messages": [
                                {
                                    "id": "wamid-100",
                                    "from": "18095550001",
                                    "timestamp": "1715000000",
                                    "type": "text",
                                    "text": {"body": "hola bot"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-Hub-Signature-256": _sign(body, "sec-1"), "Content-Type": "application/json"}

    first = client.post("/bot/whatsapp/webhook", data=body, headers=headers)
    assert first.status_code == 200
    second = client.post("/bot/whatsapp/webhook", data=body, headers=headers)
    assert second.status_code == 200

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095550001").first()
        assert conv is not None
        assert conv.unread_count_admin == 1
        rows = BotMessage.query.filter_by(wa_message_id="wamid-100").all()
        assert len(rows) == 1
        assert rows[0].text_body == "hola bot"


def test_webhook_post_text_inbound_crea_review_visible_en_pending_json(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550041")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "18095550041", "profile": {"name": "Ana Real"}}],
                            "messages": [
                                {
                                    "id": "wamid-real-queue-1",
                                    "from": "18095550041",
                                    "timestamp": "1715000021",
                                    "type": "text",
                                    "text": {"body": "hola, quiero informacion"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True

    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.join(BotMessage, BotMessage.id == BotSandboxReviewQueue.inbound_message_id).filter(BotMessage.wa_message_id == "wamid-real-queue-1").first()
        assert review is not None
        assert review.status == "pending_review"

    pending = client.get("/admin/bot/sandbox/asistente/pending.json", follow_redirects=False)
    assert pending.status_code == 200
    pending_body = pending.get_json() or {}
    assert pending_body.get("ok") is True
    ids = [int((it or {}).get("id") or 0) for it in (pending_body.get("items") or [])]
    assert int(review.id) in ids


def test_webhook_post_status_updates_existing_message(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550002", status="open")
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(
            conversation_id=conv.id,
            direction="outbound",
            source="admin_manual",
            message_type="text",
            wa_message_id="wamid-200",
            text_body="x",
            status="queued",
        )
        db.session.add(msg)
        db.session.commit()

    status_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {"id": "wamid-200", "status": "delivered", "timestamp": "1715000001"},
                                {"id": "wamid-200", "status": "read", "timestamp": "1715000002"},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/bot/whatsapp/webhook", json=status_payload)
    assert resp.status_code == 200

    with flask_app.app_context():
        msg = BotMessage.query.filter_by(wa_message_id="wamid-200").first()
        assert msg is not None
        assert msg.status == "read"
        assert msg.delivered_at is not None
        assert msg.read_at is not None


def test_webhook_payload_weird_does_not_break(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    resp = client.post("/bot/whatsapp/webhook", data=b'{"entry":[{"changes":[{"value":{"messages":[{}]}}]}]}', headers={"Content-Type": "application/json"})
    assert resp.status_code == 200


def test_webhook_signature_toggle_allows_mock_payload(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
    resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200


def test_webhook_post_rejects_missing_signature_when_validation_enabled(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "true")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "sec-1")
    resp = client.post("/bot/whatsapp/webhook", json={"entry": []})
    assert resp.status_code == 403


def test_webhook_post_rejects_signature_bypass_in_production(monkeypatch):
    flask_app.config["TESTING"] = False
    client = flask_app.test_client()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    resp = client.post("/bot/whatsapp/webhook", json={"entry": []})
    assert resp.status_code == 503
    assert resp.get_json().get("error") == "signature_validation_required"
    flask_app.config["TESTING"] = True


def test_webhook_partial_failure_does_not_rollback_previous_valid_message(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid-900",
                                    "from": "18095550009",
                                    "timestamp": "1715000000",
                                    "type": "text",
                                    "text": {"body": "primero"},
                                },
                                {
                                    "id": "wamid-901",
                                    "from": "18095550010",
                                    "timestamp": "1715000001",
                                    "type": "text",
                                    "text": {"body": "segundo"},
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }

    from services.bot_conversation_service import get_or_create_manual_conversation as real_get_or_create_manual_conversation

    call_counter = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            raise RuntimeError("forced")
        return real_get_or_create_manual_conversation(*args, **kwargs)

    with patch("bot.whatsapp_routes.get_or_create_manual_conversation", side_effect=_side_effect):
        resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200

    with flask_app.app_context():
        rows = BotMessage.query.filter_by(wa_message_id="wamid-900").all()
        assert len(rows) == 1
        second = BotMessage.query.filter_by(wa_message_id="wamid-901").first()
        assert second is None


def test_cloud_service_disabled_and_dry_run_do_not_call_api(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    with patch("services.whatsapp_cloud_service.requests.post") as post_mock:
        result = send_text_message("+18095550003", "hola")
    assert result.get("skipped") is True
    post_mock.assert_not_called()


def test_cloud_service_success_and_error_with_mocks(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "12345")
    monkeypatch.setenv("WHATSAPP_API_VERSION", "v23.0")
    monkeypatch.setenv("WHATSAPP_GRAPH_BASE_URL", "https://graph.facebook.com")

    class _RespOk:
        status_code = 200
        content = b"1"

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid-ok-1"}]}

    class _RespFail:
        status_code = 400
        content = b"1"

        @staticmethod
        def json():
            return {"error": {"code": 100, "message": "Bad request"}}

    with patch("services.whatsapp_cloud_service.requests.post", return_value=_RespOk()):
        ok = send_text_message("+18095550004", "hola")
    assert ok.get("ok") is True
    assert ok.get("wa_message_id") == "wamid-ok-1"

    with patch("services.whatsapp_cloud_service.requests.post", return_value=_RespFail()):
        bad = send_text_message("+18095550004", "hola")
    assert bad.get("ok") is False
    assert bad.get("status") == "failed"


def test_admin_manual_message_dry_run_and_enabled_send(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550005", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    dry_resp = client.post(f"/admin/bot/conversaciones/{conv_id}/mensaje", data={"body": "msg dry"}, follow_redirects=False)
    assert dry_resp.status_code in (302, 303)

    with flask_app.app_context():
        m1 = BotMessage.query.filter_by(conversation_id=conv_id).order_by(BotMessage.id.desc()).first()
        assert m1 is not None
        assert m1.status == "queued"

    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    with patch("admin.bot_routes.send_text_message", return_value={"ok": True, "status": "sent", "wa_message_id": "wamid-manual-1"}):
        send_resp = client.post(f"/admin/bot/conversaciones/{conv_id}/mensaje", data={"body": "msg send"}, follow_redirects=False)
    assert send_resp.status_code in (302, 303)

    with flask_app.app_context():
        m2 = BotMessage.query.filter_by(conversation_id=conv_id).order_by(BotMessage.id.desc()).first()
        assert m2 is not None
        assert m2.status == "sent"
        assert m2.wa_message_id == "wamid-manual-1"


def test_verify_webhook_token_unit():
    ok, challenge = verify_webhook_token("subscribe", "abc", "999", "abc")
    assert ok is True
    assert challenge == "999"
    bad, _ = verify_webhook_token("subscribe", "bad", "999", "abc")
    assert bad is False
