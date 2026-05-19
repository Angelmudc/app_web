# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import (
    BotCandidateDraft,
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSetting,
)
from sqlalchemy import text
from services.bot_candidate_conversion_preview_service import (
    PREVIEW_STATUS_BLOCKED_CONFLICTS,
    PREVIEW_STATUS_BLOCKED_DRAFT_STATUS,
    PREVIEW_STATUS_BLOCKED_MISSING_FIELDS,
    PREVIEW_STATUS_REQUIRES_HUMAN_REVIEW,
    build_candidate_conversion_preview,
)
from services.bot_candidate_draft_service import create_candidate_draft, mark_candidate_draft_under_review, reject_candidate_draft


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
    db.session.commit()


def _make_conv(*, metadata_json: dict, phone: str = "+18095558888") -> BotConversation:
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Preview Test", status="open", metadata_json=metadata_json)
    db.session.add(conv)
    db.session.commit()
    return conv


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    resp = client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_preview_with_valid_draft_and_no_candidates_write():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana Preview",
                    "age": 26,
                    "city": "Santiago",
                    "sector_address": "Los Jardines",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                    "observations": "sin novedad",
                },
            },
        )
        draft = create_candidate_draft(conv, actor_id=1)
        before_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        preview = build_candidate_conversion_preview(draft)
        after_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        assert preview["status"] in {"ready_to_convert", PREVIEW_STATUS_REQUIRES_HUMAN_REVIEW}
        assert preview["mapped_fields"]["nombre_completo"] == "Ana Preview"
        assert after_candidates == before_candidates


def test_preview_blocked_when_draft_rejected():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana Preview 2",
                    "age": 27,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095558881",
        )
        create_candidate_draft(conv, actor_id=1)
        draft = reject_candidate_draft(int(conv.id), actor_id=2, notes="rechazo QA")
        preview = build_candidate_conversion_preview(draft)
        assert preview["status"] == PREVIEW_STATUS_BLOCKED_DRAFT_STATUS


def test_preview_blocked_by_missing_required_fields():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(metadata_json={"protocol_entities": {"name": "Ana"}}, phone="+18095558882")
        draft = BotCandidateDraft(
            conversation_id=int(conv.id),
            draft_status="draft",
            summary_status="incomplete",
            source_protocol_entities={"name": "Ana"},
            source_pending_corrections_snapshot=[],
            metadata_json={"summary": {"fields": {"name": "Ana"}}},
            created_by=1,
            requires_human=False,
            sensitive_detected=False,
        )
        db.session.add(draft)
        db.session.commit()
        preview = build_candidate_conversion_preview(draft)
        assert preview["status"] == PREVIEW_STATUS_BLOCKED_MISSING_FIELDS
        assert "edad" in preview["missing_required_fields"]


def test_preview_detects_phone_duplicate_conflict():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana New",
                    "age": 24,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                    "phone_e164": "+18095558883",
                },
            },
            phone="+18095558883",
        )
        draft = create_candidate_draft(conv, actor_id=1)
        with patch("services.bot_candidate_conversion_preview_service.db.session.execute") as mocked_exec:
            phone_result = type("R", (), {"first": lambda self: (77,)})()
            name_result = type("R", (), {"all": lambda self: []})()
            mocked_exec.side_effect = [
                phone_result,
                name_result,
            ]
            preview = build_candidate_conversion_preview(draft)
        assert preview["status"] == PREVIEW_STATUS_BLOCKED_CONFLICTS
        assert any(x.get("type") == "phone_duplicate" for x in preview["conflicts"])


def test_preview_masks_cedula_and_ui_shows_preview_disabled_real_button_no_outbound():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana Mask",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                    "cedula": "40212345678",
                },
            },
            phone="+18095558884",
        )
        conv_id = int(conv.id)
        create_candidate_draft(conv, actor_id=1)

    _login_staff(client)
    with patch("admin.bot_routes.send_text_message") as mocked_send:
        detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
        assert detail.status_code == 200
        html = detail.get_data(as_text=True)
        assert "Preview de candidata" in html
        assert "Esto es solo una vista previa. No se ha creado ninguna candidata." in html
        assert ("Preparar creación real" in html) or ("Crear candidata real" in html)
        assert ("&lt;redacted&gt;" in html) or ("<redacted>" in html)
        mocked_send.assert_not_called()

    with flask_app.app_context():
        outbound_count = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbound_count == 0


def test_preview_under_review_allowed_and_metadata_weird_or_empty_phone_safe():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana Weird",
                    "age": 29,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                    "telefono": "",
                    "observations": {"raw": "doc 402-1234567-8"},
                },
                "pending_corrections": "unexpected-string",
            },
            phone="+18095558885",
        )
        create_candidate_draft(conv, actor_id=1)
        draft = mark_candidate_draft_under_review(int(conv.id), actor_id=2)
        preview = build_candidate_conversion_preview(draft)
        assert preview["status"] in {
            "ready_to_convert",
            PREVIEW_STATUS_REQUIRES_HUMAN_REVIEW,
            "blocked_conflicts",
            "blocked_missing_fields",
        }
        assert preview["mapped_fields"]["numero_telefono"] in {"+18095558885", None, ""}


def test_preview_name_similar_conflict_detected():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana Maria",
                    "age": 24,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095558886",
        )
        draft = create_candidate_draft(conv, actor_id=1)
        with patch("services.bot_candidate_conversion_preview_service.db.session.execute") as mocked_exec:
            phone_result = type("R", (), {"first": lambda self: None})()
            name_result = type("R", (), {"all": lambda self: [(15, "Ana Maria")]})()
            cedula_result = type("R", (), {"first": lambda self: None})()
            mocked_exec.side_effect = [phone_result, name_result, cedula_result]
            preview = build_candidate_conversion_preview(draft)
        assert preview["status"] == PREVIEW_STATUS_BLOCKED_CONFLICTS
        assert any(x.get("type") == "name_similar" for x in preview["conflicts"])


def test_create_real_endpoint_exists_but_requires_strong_confirmation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana No Endpoint",
                    "age": 24,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095558887",
        )
        conv_id = int(conv.id)
        create_candidate_draft(conv, actor_id=1)

    _login_staff(client)
    route = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear-real", data={}, follow_redirects=False)
    assert route.status_code in (302, 303)
