from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
from services.bot_sandbox_review_service import ReviewTransitionError, approve_review, reject_review
from services.bot_sandbox_service import enqueue_sandbox_outbound, run_sandbox_worker_once


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotSandboxReviewQueue.__table__.drop(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.drop(bind=conn, checkfirst=True)
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


def _new_review(client, message_id: str) -> int:
    payload = (_post_inbound(client, message_id).get_json() or {})
    return int(payload["review_id"])


def test_01_duplicate_webhook_no_duplica_review(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _post_inbound(client, "harden-wa-001")
    _post_inbound(client, "harden-wa-001")
    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1


def test_02_double_approve_no_duplica_outbox(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-002")
    r1 = client.post(f"/bot/sandbox/revision/{review_id}/approve", json={}, follow_redirects=False)
    r2 = client.post(f"/bot/sandbox/revision/{review_id}/approve", json={}, follow_redirects=False)
    assert r1.status_code == 200
    assert r2.status_code in (200, 409)
    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 1


def test_03_approve_rejected_review_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-003")
    client.post(f"/bot/sandbox/revision/{review_id}/reject", json={"reason": "x"}, follow_redirects=False)
    resp = client.post(f"/bot/sandbox/revision/{review_id}/approve", json={}, follow_redirects=False)
    assert resp.status_code == 409


def test_04_reject_approved_review_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-004")
    client.post(f"/bot/sandbox/revision/{review_id}/approve", json={}, follow_redirects=False)
    resp = client.post(f"/bot/sandbox/revision/{review_id}/reject", json={"reason": "x"}, follow_redirects=False)
    assert resp.status_code == 409


def test_05_edit_approved_review_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-005")
    client.post(f"/bot/sandbox/revision/{review_id}/approve", json={}, follow_redirects=False)
    resp = client.post(
        f"/bot/sandbox/revision/{review_id}/approve",
        json={"edited_text": "Texto nuevo"},
        follow_redirects=False,
    )
    assert resp.status_code == 409


def test_06_unsafe_edited_text_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-006")
    client.post(
        f"/bot/sandbox/revision/{review_id}/approve",
        json={"edited_text": "Ya estas aprobada y empleo seguro hoy"},
        follow_redirects=False,
    )
    with flask_app.app_context():
        row = BotSandboxReviewQueue.query.get(review_id)
        assert row.status == "blocked"
        assert str(row.fallback_reason).startswith("edited_text_")


def test_07_safe_edited_text_aprueba(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-007")
    with flask_app.app_context():
        safe_text = str(BotSandboxReviewQueue.query.get(review_id).final_suggested_reply or "Gracias por escribir.").strip()
    resp = client.post(
        f"/bot/sandbox/revision/{review_id}/approve",
        json={"edited_text": safe_text},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with flask_app.app_context():
        row = BotSandboxReviewQueue.query.get(review_id)
        assert row.status == "approved"


def test_08_dos_admins_approve_simultaneo_un_outbox(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-008")

    def _approve_once():
        with flask_app.app_context():
            review = BotSandboxReviewQueue.query.get(review_id)
            try:
                approve_review(review=review, reviewer_id=1, edited_text=None)
                db.session.commit()
                return "ok"
            except ReviewTransitionError:
                db.session.commit()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: _approve_once(), [1, 2]))

    assert "ok" in results
    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 1


def test_09_approve_reject_simultaneo_estado_valido(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-009")

    def _approve_once():
        with flask_app.app_context():
            review = BotSandboxReviewQueue.query.get(review_id)
            try:
                approve_review(review=review, reviewer_id=1, edited_text=None)
                db.session.commit()
                return "approved"
            except ReviewTransitionError:
                db.session.commit()
                return "blocked"

    def _reject_once():
        with flask_app.app_context():
            review = BotSandboxReviewQueue.query.get(review_id)
            try:
                reject_review(review=review, reviewer_id=2, reason="race")
                db.session.commit()
                return "rejected"
            except ReviewTransitionError:
                db.session.commit()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as ex:
        a = ex.submit(_approve_once)
        b = ex.submit(_reject_once)
        results = {a.result(), b.result()}

    with flask_app.app_context():
        row = BotSandboxReviewQueue.query.get(review_id)
        assert row.status in {"approved", "rejected"}
        assert results & {"approved", "rejected"}


def test_10_worker_no_procesa_review_no_aprobada(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-010")
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(review_id)
        reject_review(review=review, reviewer_id=1, reason="manual")
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=20)
        assert stats["sent"] == 0


def test_11_outbox_duplicate_key_no_rompe(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-011")
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(review_id)
        approve_review(review=review, reviewer_id=1, edited_text=None)
        db.session.commit()
        msg = BotMessage.query.get(review.outbound_message_id)
        conv = BotConversation.query.get(review.conversation_id)
        first = enqueue_sandbox_outbound(conversation=conv, message=msg)
        second = enqueue_sandbox_outbound(conversation=conv, message=msg)
        assert first.id == second.id


def test_12_audit_event_para_transicion_invalida(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-012")
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(review_id)
        reject_review(review=review, reviewer_id=1, reason="manual")
        db.session.commit()
        review = BotSandboxReviewQueue.query.get(review_id)
        try:
            approve_review(review=review, reviewer_id=1, edited_text=None)
        except ReviewTransitionError:
            db.session.commit()
        row = BotSandboxReviewQueue.query.get(review_id)
        events = list((row.metadata_json or {}).get("review_events") or [])
        assert any(str(e.get("event_type")) == "invalid_transition" for e in events)


def test_13_worker_no_reenvia_outbound_message_ya_enviado(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    review_id = _new_review(client, "harden-wa-013")
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(review_id)
        approve_review(review=review, reviewer_id=1, edited_text=None)
        db.session.commit()
        outbox = BotSandboxOutbound.query.first()
        msg = BotMessage.query.get(int(outbox.bot_message_id))
        msg.status = "outbound_sent"
        msg.wa_message_id = "wamid-existing-013"
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=20)
        db.session.refresh(outbox)
        assert stats["skipped"] >= 1
        assert outbox.state == "simulated_sent"
