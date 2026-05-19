from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_candidate_draft_service import get_or_create_interview_flow_draft
from services.bot_rate_limit_service import reset_rate_limits
from sqlalchemy import text
from sqlalchemy import inspect as sa_inspect


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotCandidateDraft.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotCandidateDraft).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.execute(text("DELETE FROM candidatas"))
    db.session.commit()


def _login_staff(client) -> None:
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_intake(*, phone: str, completed: bool = True, refs: str = "Maria 8095550011", invalid_count: int = 0):
    flow = {
        "completed": completed,
        "summary": "Perfil con experiencia en limpieza y cocina.",
        "collected_data": {
            "full_name": "Ana Perez",
            "age": 31,
            "city_sector": "Santiago, Gurabo",
            "experience": "3 años en casas de familia",
            "skills": ["limpieza", "cocinar"],
            "availability": "salida diaria",
            "references": refs,
        },
        "help_repeat_count_by_step": {"ask_name": invalid_count},
        "detected_future_data": {"availability": "salida diaria"},
    }
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Ana", status="open", metadata_json={"interview_flow": flow})
    db.session.add(conv)
    db.session.commit()
    draft, _ = get_or_create_interview_flow_draft(conv)
    return conv, draft


def test_candidate_intake_queue_flow(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "true")
    monkeypatch.setenv("ADMIN_AUTO_PRESENCE_TOUCH_ENABLED", "false")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        reset_rate_limits()
        conv, draft = _seed_intake(phone="+18095551001")
    _login_staff(client)
    _login_staff(client)

    page = client.get("/admin/bot/candidate-intake", follow_redirects=False)
    assert page.status_code == 200
    assert "Candidate Intake Queue" in page.get_data(as_text=True)

    pending = client.get("/admin/bot/candidate-intake/pending.json", follow_redirects=False).get_json() or {}
    assert pending.get("ok") is True
    assert len(pending.get("items") or []) >= 1

    intake_id = int((pending.get("items") or [])[0]["intake_id"])
    detail = client.get(f"/admin/bot/candidate-intake/{intake_id}.json", follow_redirects=False).get_json() or {}
    assert detail.get("ok") is True
    assert int((detail.get("intake") or {}).get("quality_score") or 0) >= 60

    approved = client.post(
        f"/admin/bot/candidate-intake/{intake_id}/action",
        json={"action": "approve"},
        follow_redirects=False,
    )
    assert approved.status_code == 200
    body = approved.get_json() or {}
    assert body.get("ok") is True
    assert int(body.get("candidate_id") or 0) > 0

    again = client.post(f"/admin/bot/candidate-intake/{intake_id}/action", json={"action": "approve"}, follow_redirects=False)
    assert again.status_code in (200, 400)

    metrics = client.get("/admin/bot/candidate-intake/metrics.json", follow_redirects=False).get_json() or {}
    assert metrics.get("ok") is True
    assert int(metrics.get("approved") or 0) >= 1


def test_candidate_intake_reject_duplicate_followup_edit_and_incomplete(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "true")
    monkeypatch.setenv("ADMIN_AUTO_PRESENCE_TOUCH_ENABLED", "false")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        reset_rate_limits()
        cols = {str(c.get("name")) for c in sa_inspect(db.session.get_bind()).get_columns("candidatas")}
        payload = {"nombre_completo": "Ana Perez", "numero_telefono": "8095551002", "telefono_e164": "+18095551002"}
        if "direccion_completa" in cols:
            payload["direccion_completa"] = "Santiago"
        if "cedula" in cols:
            payload["cedula"] = "40212345679"
        names = list(payload.keys())
        db.session.execute(text(f"INSERT INTO candidatas ({', '.join(names)}) VALUES ({', '.join([f':{n}' for n in names])})"), payload)
        db.session.commit()
        _, draft_dup = _seed_intake(phone="+18095551002")
        _, draft_low = _seed_intake(phone="+18095551003", refs="", invalid_count=6)
        conv_inc = BotConversation(
            channel="whatsapp",
            phone_e164="+18095551004",
            contact_name="Inc",
            status="open",
            metadata_json={"interview_flow": {"completed": False, "collected_data": {"full_name": "Inc"}}},
        )
        db.session.add(conv_inc)
        db.session.commit()
        draft_inc = BotCandidateDraft(
            conversation_id=int(conv_inc.id),
            protocol_version="interview_flow_v1",
            draft_status="draft",
            summary_status="incomplete",
            metadata_json={"summary": {}, "intake": {"status": "pending_review"}},
            source_protocol_entities={"name": "Inc"},
            source_pending_corrections_snapshot=[],
            requires_human=True,
            sensitive_detected=False,
        )
        db.session.add(draft_inc)
        db.session.commit()
        id_dup = int(draft_dup.id)
        id_low = int(draft_low.id)
        id_inc = int(draft_inc.id)
    _login_staff(client)
    _login_staff(client)

    ddup = client.get(f"/admin/bot/candidate-intake/{id_dup}.json", follow_redirects=False).get_json() or {}
    assert len((ddup.get("intake") or {}).get("duplicates") or []) >= 1

    low = client.get(f"/admin/bot/candidate-intake/{id_low}.json", follow_redirects=False).get_json() or {}
    assert int((low.get("intake") or {}).get("quality_score") or 0) <= 55

    edit = client.post(
        f"/admin/bot/candidate-intake/{id_low}/action",
        json={"action": "edit_before_approve", "fields": {"name": "Ana Editada", "city": "La Vega"}},
        follow_redirects=False,
    )
    assert edit.status_code == 200

    rej = client.post(f"/admin/bot/candidate-intake/{id_low}/action", json={"action": "reject", "note": "dato insuficiente"}, follow_redirects=False)
    assert rej.status_code == 200
    assert (rej.get_json() or {}).get("status") == "rejected"

    with flask_app.app_context():
        reset_rate_limits()
    fol = client.post(f"/admin/bot/candidate-intake/{id_dup}/action", json={"action": "followup", "note": "confirmar cedula"}, follow_redirects=False)
    assert fol.status_code == 200
    assert (fol.get_json() or {}).get("status") == "needs_followup"

    dup = client.post(f"/admin/bot/candidate-intake/{id_dup}/action", json={"action": "mark_duplicate"}, follow_redirects=False)
    assert dup.status_code == 200
    assert (dup.get_json() or {}).get("status") == "duplicate"

    inc_pending = client.get("/admin/bot/candidate-intake/pending.json", follow_redirects=False).get_json() or {}
    found_inc = [x for x in (inc_pending.get("items") or []) if int(x.get("intake_id") or 0) == id_inc]
    assert found_inc and found_inc[0].get("status") == "incomplete"
