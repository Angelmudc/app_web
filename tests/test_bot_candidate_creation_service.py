# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting, StaffAuditLog
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from services.bot_candidate_creation_service import (
    create_candidate_from_draft,
    evaluate_real_creation_guardrails,
    normalize_candidate_phone,
    validate_candidate_creation,
)
from services.bot_candidate_draft_service import create_candidate_draft


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
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
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
    r = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def _make_conv(phone: str = "+18095550090", name: str = "Ana Real") -> BotConversation:
    conv = BotConversation(
        channel="whatsapp",
        phone_e164=phone,
        contact_name=name,
        status="open",
        metadata_json={
            "protocol_entities": {
                "name": name,
                "age": 30,
                "city": "Santiago",
                "sector_address": "Los Jardines",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "referencias_laborales": "Maria Perez",
                "referencias_familiares": "Juana Perez",
                "cedula": "40212345678",
            }
        },
    )
    db.session.add(conv)
    db.session.commit()
    return conv


def _insert_candidate_raw(*, nombre: str, cedula: str, phone: str | None = None, telefono_e164: str | None = None, direccion: str | None = None) -> None:
    cols = {str(c.get("name")) for c in sa_inspect(db.session.get_bind()).get_columns("candidatas")}
    payload = {
        "nombre_completo": nombre,
        "cedula": cedula,
        "numero_telefono": phone,
        "telefono_e164": telefono_e164,
        "direccion_completa": direccion,
    }
    data = {k: v for k, v in payload.items() if k in cols}
    names = list(data.keys())
    db.session.execute(text(f"INSERT INTO candidatas ({', '.join(names)}) VALUES ({', '.join([f':{n}' for n in names])})"), data)
    db.session.commit()


def _candidate_columns() -> set[str]:
    return {str(c.get("name")) for c in sa_inspect(db.session.get_bind()).get_columns("candidatas")}


def test_phone_normalization_rd_variants():
    flask_app.config["TESTING"] = True
    assert normalize_candidate_phone("809-555-0199")["normalized"] == "+18095550199"
    assert normalize_candidate_phone("(809) 555 0199")["normalized"] == "+18095550199"
    assert normalize_candidate_phone("+1 809 555 0199")["normalized"] == "+18095550199"


def test_creation_success_and_draft_reference_and_state():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv()
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
            cand = create_candidate_from_draft(draft, actor_id=1)
        db.session.commit()
        assert cand.fila is not None
        cols = _candidate_columns()
        if "origen_registro" in cols:
            stored = db.session.execute(text("SELECT origen_registro FROM candidatas WHERE fila = :id"), {"id": int(cand.fila)}).scalar()
            assert stored == "bot_draft"
        same_draft = db.session.get(BotCandidateDraft, int(draft.id))
        assert same_draft.draft_status == "converted"
        assert int((same_draft.metadata_json or {}).get("created_candidate_id") or 0) == int(cand.fila)


def test_rollback_safe_on_failure():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(phone="+18095550091")
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
        before = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
            with patch("services.bot_candidate_creation_service.db.session.execute", side_effect=RuntimeError("boom")):
                try:
                    create_candidate_from_draft(draft, actor_id=1)
                except RuntimeError:
                    db.session.rollback()
        after = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        assert after == before


def test_blocking_conflicts_phone_and_cedula_and_rejected_and_pending():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        _insert_candidate_raw(
            nombre="Existente",
            cedula="402-1234567-8",
            phone="8095550092",
            telefono_e164="+18095550092",
            direccion="Santiago",
        )
        conv = _make_conv(phone="+18095550092")
        draft = create_candidate_draft(conv, actor_id=1)
        meta = dict(conv.metadata_json or {})
        meta["pending_corrections"] = [{"id": 1, "status": "pending_human"}]
        conv.metadata_json = meta
        db.session.commit()
        draft.draft_status = "rejected"
        db.session.commit()
        v = validate_candidate_creation(draft)
        t = {x.get("type") for x in (v.get("blocking_conflicts") or [])}
        assert "phone_duplicate" in t
        if "cedula" in _candidate_columns():
            assert "cedula_duplicate" in t
        assert "draft_rejected" in t
        assert "pending_corrections_active" in t


def test_warning_name_similar_allows_continue_when_approved():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        _insert_candidate_raw(
            nombre="Ana Real",
            cedula="402-1234567-9",
            phone="8095550198",
            telefono_e164="+18095550198",
            direccion="Santiago",
        )
        conv = _make_conv(phone="+18095550093")
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
        v = validate_candidate_creation(draft, require_approved=True)
        types = {x.get("type") for x in (v.get("warning_conflicts") or [])}
        assert "name_similar" in types
        assert v["valid"] is True


def test_double_click_does_not_duplicate():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(phone="+18095550094")
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
        before = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
            c1 = create_candidate_from_draft(draft, actor_id=1)
        db.session.commit()
        try:
            with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
                create_candidate_from_draft(draft, actor_id=1)
            assert False, "Debió bloquear doble creación"
        except ValueError:
            db.session.rollback()
        total = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        assert int(c1.fila) > 0
        assert total == before + 1


def test_ui_and_audit_and_no_outbound_whatsapp_or_ai():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        db.session.query(StaffAuditLog).filter(StaffAuditLog.action_type.like("candidate_real_%")).delete()
        db.session.commit()
        conv = _make_conv(phone="+18095550095")
        conv_id = int(conv.id)
        create_candidate_draft(conv, actor_id=1)
    _login_staff(client)

    with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
        with patch("admin.bot_routes.send_text_message") as send_mock, patch("admin.bot_routes.classify_intent") as ai_mock:
            prep = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/preparar-creacion-real", data={}, follow_redirects=False)
            assert prep.status_code in (302, 303)
            confirm = client.post(
                f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear-real",
                data={"confirm_reviewed": "on"},
                follow_redirects=False,
            )
            assert confirm.status_code in (302, 303)
            detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
            html = detail.get_data(as_text=True)
            assert "Candidata real ya creada" in html
            send_mock.assert_not_called()
            ai_mock.assert_not_called()

    with flask_app.app_context():
        out_count = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert out_count == 0
        actions = [x.action_type for x in StaffAuditLog.query.filter(StaffAuditLog.action_type.like("candidate_real_%")).all()]
        assert "candidate_real_creation_started" in actions
        assert "candidate_real_created" in actions


def test_guardrails_block_by_default_flag_false_no_insert_no_convert():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(phone="+18095550096")
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
        before = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false"}, clear=False):
            try:
                create_candidate_from_draft(draft, actor_id=1)
                assert False, "Debió bloquear por guard rails"
            except ValueError as exc:
                assert "guardrails" in str(exc)
                db.session.rollback()
        after = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        fresh = db.session.get(BotCandidateDraft, int(draft.id))
        assert after == before
        assert fresh and fresh.draft_status == "approved_for_creation"


def test_guardrails_matrix_env_db_flag():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        original_db = str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true", "APP_ENV": "development"}, clear=False):
            flask_app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://x@localhost:5432/localdb"
            g = evaluate_real_creation_guardrails()
            assert g["allowed"] is True
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false", "APP_ENV": "development"}, clear=False):
            flask_app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://x@localhost:5432/localdb"
            g = evaluate_real_creation_guardrails()
            assert g["allowed"] is False
            assert g["flag_ok"] is False
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true", "APP_ENV": "production"}, clear=False):
            flask_app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://x@localhost:5432/localdb"
            g = evaluate_real_creation_guardrails()
            assert g["allowed"] is False
            assert g["env_ok"] is False
        with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true", "APP_ENV": "development"}, clear=False):
            flask_app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://x@db.example.com:5432/prodlike"
            g = evaluate_real_creation_guardrails()
            assert g["allowed"] is False
            assert g["db_ok"] is False
        flask_app.config["SQLALCHEMY_DATABASE_URI"] = original_db


def test_ui_guardrails_shows_no_and_disables_button_and_audits_block():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        db.session.query(StaffAuditLog).filter(StaffAuditLog.action_type.like("candidate_real_%")).delete()
        db.session.commit()
        conv = _make_conv(phone="+18095550097")
        conv_id = int(conv.id)
        draft = create_candidate_draft(conv, actor_id=1)
        draft.draft_status = "approved_for_creation"
        db.session.commit()
    _login_staff(client)
    with patch.dict(os.environ, {"BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false"}, clear=False):
        detail = client.get(f"/admin/bot/conversaciones/{conv_id}?confirm_real_creation=1", follow_redirects=False)
        html = detail.get_data(as_text=True)
        assert "Flag creación local: " in html
        assert "disabled" in html and "Confirmar creación REAL" in html
        confirm = client.post(
            f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear-real",
            data={"confirm_reviewed": "on"},
            follow_redirects=False,
        )
        assert confirm.status_code in (302, 303)
        with flask_app.app_context():
            blocked = StaffAuditLog.query.filter_by(action_type="candidate_real_creation_blocked").order_by(StaffAuditLog.id.desc()).first()
            assert blocked is not None
