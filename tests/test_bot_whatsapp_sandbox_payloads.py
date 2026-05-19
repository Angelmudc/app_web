from __future__ import annotations

import hashlib
import hmac
import json
import re

from app import app as flask_app
from config_app import db
from models import (
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSandboxOutbound,
    BotSandboxReviewQueue,
    BotSetting,
)
from services.bot_sandbox_review_service import approve_review
from services.bot_sandbox_service import run_sandbox_worker_once


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.create(bind=conn, checkfirst=True)
        BotSandboxReviewQueue.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotSandboxReviewQueue).delete()
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _base_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "true")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")


def _cloud_payload(*, message_id: str, from_num: str = "19990000001", msg_type: str = "text", text: str = "hola") -> dict:
    base_msg = {
        "from": from_num,
        "id": message_id,
        "timestamp": "1710000000",
        "type": msg_type,
    }
    if msg_type == "text":
        base_msg["text"] = {"body": text}
    elif msg_type == "audio":
        base_msg["audio"] = {"id": "fake-audio-001", "mime_type": "audio/ogg"}
    elif msg_type == "image":
        base_msg["image"] = {"id": "fake-image-001", "mime_type": "image/jpeg"}
    elif msg_type == "document":
        base_msg["document"] = {"id": "fake-doc-001", "mime_type": "application/pdf"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [base_msg],
                            "contacts": [{"profile": {"name": "Candidata Test"}, "wa_id": from_num}],
                        }
                    }
                ]
            }
        ],
    }


def _simple_payload(message_id: str) -> dict:
    return {
        "from": "+19990000001",
        "name": "Candidata Sandbox",
        "message": "hola legacy",
        "message_id": message_id,
        "timestamp": "2026-05-12T10:00:00Z",
    }


def _sandbox_signature(secret: str, payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def test_01_cloud_api_text_payload_crea_review(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.fake.001"))
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True

    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1


def test_02_simple_payload_viejo_funciona(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_simple_payload("legacy-001"))
    assert resp.status_code == 200


def test_03_duplicate_wamid_no_duplica_review(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    p = _cloud_payload(message_id="wamid.dup.001")
    r1 = client.post("/admin/bot/sandbox/webhook/inbound", json=p)
    r2 = client.post("/admin/bot/sandbox/webhook/inbound", json=p)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get("duplicate_webhook") is True

    with flask_app.app_context():
        assert BotMessage.query.count() == 1
        assert BotSandboxReviewQueue.query.count() == 1


def test_04_audio_fake_placeholder_y_requires_human(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.audio.001", msg_type="audio"))
    assert resp.status_code == 200

    with flask_app.app_context():
        msg = BotMessage.query.filter_by(wa_message_id="wamid.audio.001").first()
        assert msg is not None
        assert msg.message_type == "audio"
        assert "transcripcion manual" in str(msg.text_body or "")
        raw = dict(msg.raw_payload_json or {})
        assert bool((raw.get("normalized") or {}).get("requires_human")) is True


def test_05_imagen_fake_placeholder_y_requires_human(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.image.001", msg_type="image"))
    assert resp.status_code == 200
    with flask_app.app_context():
        msg = BotMessage.query.filter_by(wa_message_id="wamid.image.001").first()
        assert "revision manual" in str(msg.text_body or "")


def test_06_documento_fake_placeholder_y_requires_human(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.doc.001", msg_type="document"))
    assert resp.status_code == 200
    with flask_app.app_context():
        msg = BotMessage.query.filter_by(wa_message_id="wamid.doc.001").first()
        assert "documento recibido" in str(msg.text_body or "")


def test_07_payload_corrupto_devuelve_400(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", data="no-json", content_type="application/json")
    assert resp.status_code == 400


def test_08_falta_message_id_devuelve_400(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    bad = _simple_payload("x")
    bad.pop("message_id", None)
    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=bad)
    assert resp.status_code == 400


def test_09_numero_real_bloqueado(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.real.001", from_num="18095550000"))
    assert resp.status_code == 403


def test_10_firma_requerida_valida_pasa(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED", "true")
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SECRET", "sandbox-secret")
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    payload = _cloud_payload(message_id="wamid.sig.ok")
    sig = _sandbox_signature("sandbox-secret", payload)
    resp = client.post(
        "/admin/bot/sandbox/webhook/inbound",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-Sandbox-Signature": sig},
    )
    assert resp.status_code == 200


def test_11_firma_requerida_ausente_falla(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED", "true")
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SECRET", "sandbox-secret")
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.sig.none"))
    assert resp.status_code == 403


def test_12_firma_invalida_falla(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED", "true")
    monkeypatch.setenv("BOT_SANDBOX_WEBHOOK_SECRET", "sandbox-secret")
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post(
        "/admin/bot/sandbox/webhook/inbound",
        data=json.dumps(_cloud_payload(message_id="wamid.sig.bad")),
        content_type="application/json",
        headers={"X-Sandbox-Signature": "bad"},
    )
    assert resp.status_code == 403


def test_13_no_outbound_antes_de_aprobar(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.no.outbound"))
    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 0


def test_14_aprobar_media_review_encola_sandbox_fake(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.media.approve", msg_type="audio"))
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.order_by(BotSandboxReviewQueue.id.desc()).first()
        assert review is not None
        assert "Un miembro del equipo" in str(review.final_suggested_reply or "")
        approve_review(review=review, reviewer_id=1, edited_text=None)
        db.session.commit()
        assert BotSandboxOutbound.query.count() == 1
        run_sandbox_worker_once(batch_size=10)
        # Nunca se usa outbound real.
        assert BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count() == 0


def test_15_interview_hola_y_nombre_generan_preguntas_correctas(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    base_from = "19990000071"
    client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.intv.001", from_num=base_from, text="hola"))
    client.post("/admin/bot/sandbox/webhook/inbound", json=_cloud_payload(message_id="wamid.intv.002", from_num=base_from, text="María Pérez"))

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+19990000071").order_by(BotConversation.id.desc()).first()
        assert conv is not None
        reviews = (
            BotSandboxReviewQueue.query.filter_by(conversation_id=int(conv.id))
            .order_by(BotSandboxReviewQueue.id.asc())
            .all()
        )
        assert len(reviews) == 2
        first = str(reviews[0].final_suggested_reply or "")
        second = str(reviews[1].final_suggested_reply or "")
        assert "¿Cuál es tu nombre completo?" in first
        assert "Agencia Doméstica del Cibao A&D" in first
        assert "Trabajamos en Santiago y Puerto Plata" not in first
        assert "¿Qué edad tienes?" in second
