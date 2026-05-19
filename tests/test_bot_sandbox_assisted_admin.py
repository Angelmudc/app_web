from __future__ import annotations

import re

from app import app as flask_app
from config_app import db
from models import (
    BotCandidateDraft,
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSandboxOutbound,
    BotSandboxReviewQueue,
    BotSetting,
)


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
        BotCandidateDraft.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotSandboxReviewQueue).delete()
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.query(BotCandidateDraft).delete()
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
    monkeypatch.setenv("ADMIN_AUTO_PRESENCE_TOUCH_ENABLED", "false")


def _real_sandbox_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("ADMIN_AUTO_PRESENCE_TOUCH_ENABLED", "false")


def _extract_csrf(html: str) -> str:
    m_meta = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', html)
    if m_meta:
        return m_meta.group(1)
    m_input = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m_input is not None
    return m_input.group(1)


def _login_staff(client) -> None:
    login_data = {"usuario": "Owner", "clave": "admin123"}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
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


def _seed_reviews(client):
    for i in range(1, 6):
        _post_inbound(client, f"assist-wa-{i:03d}")


def test_01_vista_carga_con_staff(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    _seed_reviews(client)
    _login_staff(client)
    _login_staff(client)

    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 200


def test_02_sin_login_redirige(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_03_lista_pendientes_json(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    _seed_reviews(client)
    _login_staff(client)

    resp = client.get("/admin/bot/sandbox/asistente/pending.json", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert len(payload.get("items") or []) >= 1


def test_03b_ui_muestra_modo_manual(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    html = client.get("/admin/bot/sandbox/asistente", follow_redirects=False).get_data(as_text=True)
    assert "MANUAL REVIEW MODE" in html
    assert "btn-reset-conversation" in html
    assert "Reiniciar conversación" in html


def test_03c_ui_js_reset_conversation_payload(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    html = client.get("/admin/bot/sandbox/asistente", follow_redirects=False).get_data(as_text=True)
    assert "data-initial-conversation-id=" in html
    js = open("static/js/admin_bot_sandbox_asistente.js", "r", encoding="utf-8").read()
    assert "/admin/bot/sandbox/asistente/conversation/" in js
    assert "confirm: true" in js
    assert "archive_pending: true" in js
    assert "Datos capturados" in js
    assert "Datos detectados (futuros)" in js
    assert "Crear/Actualizar borrador" in js


def test_04_detalle_review_json(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-004").get_json() or {})
    rid = int(data["review_id"])

    resp = client.get(f"/admin/bot/sandbox/asistente/review/{rid}.json", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert int((payload.get("review") or {}).get("id") or 0) == rid


def test_04b_detalle_review_json_expone_validation_error(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    phone = "+19990000444"
    _post_inbound(client, "assist-wa-04b-1", phone=phone, message="hola")
    data = (_post_inbound(client, "assist-wa-04b-2", phone=phone, message="hola").get_json() or {})
    rid = int(data["review_id"])

    resp = client.get(f"/admin/bot/sandbox/asistente/review/{rid}.json", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    review = dict(payload.get("review") or {})
    assert "nombre completo" in str(review.get("validation_error") or "").lower()
    assert str(review.get("last_invalid_answer") or "") == "hola"
    assert isinstance(review.get("interview_collected_data"), dict)
    assert isinstance(review.get("interview_detected_future_data"), dict)


def test_04c_detalle_review_json_expone_draft_candidate(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    phone = "+19990000445"
    steps = ["hola", "Ana Perez", "30", "Santiago Gurabo", "Tengo experiencia", "limpio cocino", "salida diaria", "Ana 8095550001"]
    last = {}
    for i, msg in enumerate(steps, start=1):
        last = _post_inbound(client, f"assist-wa-04c-{i}", phone=phone, message=msg).get_json() or {}
    rid = int(last["review_id"])
    resp = client.get(f"/admin/bot/sandbox/asistente/review/{rid}.json", follow_redirects=False)
    review = dict((resp.get_json() or {}).get("review") or {})
    assert review.get("draft_candidate_created") is True
    assert int(review.get("draft_candidate_id") or 0) > 0


def test_05_aprobar_encola_outbox_sandbox(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-005").get_json() or {})
    rid = int(data["review_id"])

    resp = client.post(f"/admin/bot/sandbox/asistente/review/{rid}/approve", json={}, follow_redirects=False)
    assert resp.status_code == 200

    with flask_app.app_context():
        assert BotSandboxOutbound.query.count() == 1
        row = BotSandboxOutbound.query.first()
        assert row is not None
        assert row.provider == "fake"


def test_06_editar_inseguro_bloquea(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-006").get_json() or {})
    rid = int(data["review_id"])

    resp = client.post(
        f"/admin/bot/sandbox/asistente/review/{rid}/edit-approve",
        json={"edited_text": "Ya estas aprobada y empleo seguro hoy"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 409)
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(rid)
        assert review is not None
        assert review.status == "blocked"


def test_07_rechazar_requiere_razon(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-007").get_json() or {})
    rid = int(data["review_id"])

    bad = client.post(f"/admin/bot/sandbox/asistente/review/{rid}/reject", json={}, follow_redirects=False)
    assert bad.status_code == 400


def test_08_bloquear_requiere_razon(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-008").get_json() or {})
    rid = int(data["review_id"])

    bad = client.post(f"/admin/bot/sandbox/asistente/review/{rid}/block", json={}, follow_redirects=False)
    assert bad.status_code == 400


def test_09_worker_fake_actualiza_simulated_sent(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-009").get_json() or {})
    rid = int(data["review_id"])

    client.post(f"/admin/bot/sandbox/asistente/review/{rid}/approve", json={}, follow_redirects=False)
    run = client.post("/admin/bot/sandbox/asistente/worker/run", json={"batch_size": 20, "confirm_global": True}, follow_redirects=False)
    assert run.status_code == 200
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(rid)
        assert review is not None
        assert review.status == "simulated_sent"


def test_09c_reset_conversation_limpia_interview_flow(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    phone = "+19990000990"
    _post_inbound(client, "assist-reset-1", phone=phone, message="hola")
    _post_inbound(client, "assist-reset-2", phone=phone, message="hola")
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164=phone).order_by(BotConversation.id.desc()).first()
        assert conv is not None
        assert "interview_flow" in dict(conv.metadata_json or {})
        conv_id = int(conv.id)
    resp = client.post(f"/admin/bot/sandbox/asistente/conversation/{conv_id}/reset", json={"confirm": True}, follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("message") == "Conversación reiniciada correctamente. Lista para nueva prueba."
    with flask_app.app_context():
        conv = BotConversation.query.get(conv_id)
        assert conv is not None
        assert "interview_flow" not in dict(conv.metadata_json or {})
        assert bool(dict(conv.metadata_json or {}).get("sandbox_conversation", False)) is True
        assert BotMessage.query.filter_by(conversation_id=conv_id).count() >= 2


def test_09e_reset_no_borra_borrador(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    phone = "+19990000991"
    steps = ["hola", "Ana Perez", "30", "Santiago Gurabo", "Tengo experiencia", "limpio cocino", "salida diaria", "Ana 8095550001"]
    for i, msg in enumerate(steps, start=1):
        _post_inbound(client, f"assist-reset-draft-{i}", phone=phone, message=msg)
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164=phone).order_by(BotConversation.id.desc()).first()
        draft = BotCandidateDraft.query.filter_by(conversation_id=int(conv.id)).first()
        assert draft is not None
        conv_id = int(conv.id)
        draft_id = int(draft.id)
    client.post(f"/admin/bot/sandbox/asistente/conversation/{conv_id}/reset", json={"confirm": True}, follow_redirects=False)
    with flask_app.app_context():
        draft = BotCandidateDraft.query.get(draft_id)
        assert draft is not None


def test_09d_reset_archive_pendientes(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-reset-arch-1").get_json() or {})
    rid = int(data["review_id"])
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(rid)
        conv_id = int(review.conversation_id)
    resp = client.post(
        f"/admin/bot/sandbox/asistente/conversation/{conv_id}/reset",
        json={"confirm": True, "archive_pending": True},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with flask_app.app_context():
        review = BotSandboxReviewQueue.query.get(rid)
        assert review is not None
        assert review.status == "blocked"


def test_09b_worker_scope_solo_review_actual(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    rid1 = int((_post_inbound(client, "assist-wa-09b-1").get_json() or {})["review_id"])
    rid2 = int((_post_inbound(client, "assist-wa-09b-2").get_json() or {})["review_id"])
    client.post(f"/admin/bot/sandbox/asistente/review/{rid1}/approve", json={}, follow_redirects=False)
    client.post(f"/admin/bot/sandbox/asistente/review/{rid2}/approve", json={}, follow_redirects=False)

    run = client.post("/admin/bot/sandbox/asistente/worker/run", json={"batch_size": 20, "review_id": rid1}, follow_redirects=False)
    assert run.status_code == 200
    with flask_app.app_context():
        one = BotSandboxReviewQueue.query.get(rid1)
        two = BotSandboxReviewQueue.query.get(rid2)
        assert one.status == "simulated_sent"
        assert two.status == "approved"


def test_09c_worker_global_requiere_confirm(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.post("/admin/bot/sandbox/asistente/worker/run", json={"batch_size": 20}, follow_redirects=False)
    assert resp.status_code == 409
    assert (resp.get_json() or {}).get("error") == "global_worker_confirmation_required"


def test_09d_housekeeping_archive_endpoint(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.post(
        "/admin/bot/sandbox/asistente/outbox/housekeeping",
        json={"action": "archive_old_pending", "older_than_hours": 1, "limit": 10},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert (resp.get_json() or {}).get("ok") is True


def test_10_labels_sandbox_presentes(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    _seed_reviews(client)

    html = client.get("/admin/bot/sandbox/asistente", follow_redirects=False).get_data(as_text=True)
    assert "Sandbox" in html
    assert "No enviado real" in html
    assert "Revisión manual obligatoria" in html
    assert "Simulado" in html


def test_11_no_outbound_real(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    data = (_post_inbound(client, "assist-wa-011").get_json() or {})
    rid = int(data["review_id"])

    client.post(f"/admin/bot/sandbox/asistente/review/{rid}/approve", json={}, follow_redirects=False)
    client.post("/admin/bot/sandbox/asistente/worker/run", json={"batch_size": 20, "confirm_global": True}, follow_redirects=False)
    metrics = client.get("/admin/bot/sandbox/asistente/metrics.json", follow_redirects=False)
    payload = metrics.get_json() or {}
    assert payload.get("outbound_real_count") == 0
    assert payload.get("whatsapp_real_count") == 0


def test_12_csrf_protegido(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)

    data = (_post_inbound(client, "assist-wa-012").get_json() or {})
    rid = int(data["review_id"])

    no_token = client.post(f"/admin/bot/sandbox/asistente/review/{rid}/approve", json={}, follow_redirects=False)
    assert no_token.status_code in (400, 302, 303)

    _login_staff(client)
    page = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    token = _extract_csrf(page.get_data(as_text=True))
    yes_token = client.post(
        f"/admin/bot/sandbox/asistente/review/{rid}/approve",
        json={},
        headers={"X-CSRFToken": token},
        follow_redirects=False,
    )
    assert yes_token.status_code in (200, 409)


def test_13_real_sandbox_owner_only_permite_abrir(monkeypatch):
    _real_sandbox_env(monkeypatch)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "REAL SANDBOX OWNER ONLY" in html
    assert "No producción" in html
    assert "Revisión manual obligatoria" in html
    assert "Enviar por sandbox real" in html


def test_14_real_sandbox_owner_only_false_bloquea(monkeypatch):
    _real_sandbox_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "false")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 403
    assert (resp.get_json() or {}).get("error") == "sandbox_mode_required"


def test_15_real_sandbox_manual_review_false_bloquea(monkeypatch):
    _real_sandbox_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "false")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 403
    assert (resp.get_json() or {}).get("error") == "sandbox_mode_required"


def test_16_real_sandbox_provider_no_sandbox_bloquea(monkeypatch):
    _real_sandbox_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_production")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 403
    assert (resp.get_json() or {}).get("error") == "sandbox_mode_required"


def test_17_staging_con_whatsapp_bloquea(monkeypatch):
    _real_sandbox_env(monkeypatch)
    monkeypatch.setenv("BOT_STAGING_MODE", "true")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    _login_staff(client)
    resp = client.get("/admin/bot/sandbox/asistente", follow_redirects=False)
    assert resp.status_code == 403
    assert (resp.get_json() or {}).get("error") == "sandbox_mode_required"
