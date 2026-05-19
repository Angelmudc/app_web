from __future__ import annotations

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


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m is not None
    return m.group(1)


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    login_data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        assert login_page.status_code == 200
        login_data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=login_data, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _post_inbound(client, message_id: str, *, phone: str = "+19990000000", message: str = "hola"):
    return client.post(
        "/admin/bot/sandbox/webhook/inbound",
        json={
            "from": phone,
            "name": "Candidata Sandbox",
            "message": message,
            "message_id": message_id,
            "timestamp": "2026-05-12T10:00:00Z",
        },
        follow_redirects=False,
    )


def test_01_inbound_crea_conversacion_y_review_pending(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = _post_inbound(client, "fake-wa-001")
    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("ok") is True

    with flask_app.app_context():
        conv = BotConversation.query.get(int(data["conversation_id"]))
        assert conv is not None
        assert str(conv.phone_e164).startswith("+1999")
        review = BotSandboxReviewQueue.query.get(int(data["review_id"]))
        assert review is not None
        assert review.status == "pending_review"


def test_02_no_outbox_antes_de_aprobar(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    _post_inbound(client, "fake-wa-002")
    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 0


def test_03_aprobar_encola_outbox_fake(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    data = (_post_inbound(client, "fake-wa-003").get_json() or {})
    r = client.post(f"/bot/sandbox/revision/{int(data['review_id'])}/approve", json={}, follow_redirects=False)
    assert r.status_code == 200

    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 1
        row = BotSandboxOutbound.query.first()
        assert row is not None
        assert row.provider == "fake"
        assert row.state == "queued"


def test_04_worker_fake_procesa_simulated_sent(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    data = (_post_inbound(client, "fake-wa-004").get_json() or {})
    client.post(f"/bot/sandbox/revision/{int(data['review_id'])}/approve", json={}, follow_redirects=False)

    with flask_app.app_context():
        stats = run_sandbox_worker_once(batch_size=10)
        assert stats["sent"] >= 1
        review = BotSandboxReviewQueue.query.get(int(data["review_id"]))
        assert review is not None
        assert review.status == "simulated_sent"


def test_05_editar_texto_inseguro_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    data = (_post_inbound(client, "fake-wa-005").get_json() or {})
    client.post(
        f"/bot/sandbox/revision/{int(data['review_id'])}/approve",
        json={"edited_text": "Te conseguimos empleo hoy mismo, ya estás aprobada."},
        follow_redirects=False,
    )

    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(int(data["review_id"]))
        assert review is not None
        assert review.status == "blocked"
        assert BotSandboxOutbound.query.count() == 0


def test_06_rechazar_no_encola(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    data = (_post_inbound(client, "fake-wa-006").get_json() or {})
    client.post(
        f"/bot/sandbox/revision/{int(data['review_id'])}/reject",
        json={"reason": "manual reject"},
        follow_redirects=False,
    )

    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(int(data["review_id"]))
        assert review is not None
        assert review.status == "rejected"
        assert BotSandboxOutbound.query.count() == 0


def test_07_numero_real_bloqueado(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = _post_inbound(client, "fake-wa-007", phone="+18095550000")
    assert resp.status_code == 403


def test_08_duplicate_message_id_idempotente(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    r1 = _post_inbound(client, "fake-wa-008")
    r2 = _post_inbound(client, "fake-wa-008")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get("idempotent") is True

    with flask_app.app_context():
        assert BotMessage.query.count() == 1
        assert BotSandboxReviewQueue.query.count() == 1


def test_09_payload_corrupto_no_rompe(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post("/admin/bot/sandbox/webhook/inbound", data="not-json", content_type="application/json", follow_redirects=False)
    assert resp.status_code == 400


def test_10_requires_human_visible_en_review(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    _post_inbound(client, "fake-wa-010")
    resp = client.get("/bot/sandbox/revision/pending", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert any(bool(item.get("requires_human")) for item in (payload.get("items") or []))


def test_11_no_outbound_real_nunca(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = _post_inbound(client, "fake-wa-011")
    assert resp.status_code == 403
    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 0
