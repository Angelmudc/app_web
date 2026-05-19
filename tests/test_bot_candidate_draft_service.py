# -*- coding: utf-8 -*-
from __future__ import annotations

import re
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
    StaffAuditLog,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from services.bot_candidate_draft_service import (
    DRAFT_STATUS_DRAFT,
    DRAFT_STATUS_REJECTED,
    DRAFT_STATUS_UNDER_REVIEW,
    can_create_candidate_draft,
    create_candidate_draft,
    get_candidate_draft,
    mark_candidate_draft_under_review,
    reject_candidate_draft,
)


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


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    resp = client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _make_conv(*, metadata_json: dict, phone: str = "+18095557777") -> BotConversation:
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Draft Test", status="open", metadata_json=metadata_json)
    db.session.add(conv)
    db.session.commit()
    return conv


def test_create_draft_success_and_no_real_candidate_created():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_version": "domesticas_v1",
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            }
        )
        before_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        draft = create_candidate_draft(conv, actor_id=1)
        after_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        assert draft.id is not None
        assert draft.draft_status == DRAFT_STATUS_DRAFT
        assert after_candidates == before_candidates


def test_blocked_by_pending_corrections_and_incomplete_summary():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv_pending = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
                "pending_corrections": [{"id": 1, "field": "age", "status": "pending_human"}],
            },
            phone="+18095557770",
        )
        check = can_create_candidate_draft(conv_pending)
        assert check["allowed"] is False
        assert check["reason"] in {"pending_corrections", "summary_status_not_allowed"}

        conv_incomplete = _make_conv(metadata_json={"protocol_entities": {"name": "Ana"}}, phone="+18095557771")
        check2 = can_create_candidate_draft(conv_incomplete)
        assert check2["allowed"] is False
        assert check2["reason"] in {"missing_required_fields", "summary_status_not_allowed"}


def test_requires_human_still_allows_draft_and_masks_sensitive_fields():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria 402-1234567-8",
                    "cedula": "40212345678",
                    "documentos_indicados": "cedula",
                    "foto_indicada": "si",
                },
            },
        )
        check = can_create_candidate_draft(conv)
        assert check["allowed"] is True
        draft = create_candidate_draft(conv, actor_id=2)
        snap = draft.source_protocol_entities or {}
        assert draft.sensitive_detected is True
        assert str(snap.get("cedula")) == "<redacted>"
        assert "1234567" not in str(snap)


def test_snapshot_immutable_and_no_conversation_mutation():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
        )
        original_meta = dict(conv.metadata_json or {})
        draft = create_candidate_draft(conv, actor_id=3)

        meta2 = dict(conv.metadata_json or {})
        entities2 = dict(meta2.get("protocol_entities") or {})
        entities2["route"] = "ruta M"
        meta2["protocol_entities"] = entities2
        conv.metadata_json = meta2
        db.session.commit()

        same = BotCandidateDraft.query.get(int(draft.id))
        assert (same.source_protocol_entities or {}).get("route") == "ruta K"
        assert dict((BotConversation.query.get(int(conv.id)).metadata_json or {})) != original_meta


def test_draft_status_transitions_and_audit_logs_created():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557772",
        )
        draft = create_candidate_draft(conv, actor_id=5)
        assert draft.draft_status == DRAFT_STATUS_DRAFT

        reviewed = mark_candidate_draft_under_review(int(conv.id), actor_id=6)
        assert reviewed.draft_status == DRAFT_STATUS_UNDER_REVIEW
        assert reviewed.reviewed_by == 6

        rejected = reject_candidate_draft(int(conv.id), actor_id=7, notes="faltan datos")
        assert rejected.draft_status == DRAFT_STATUS_REJECTED
        assert rejected.reviewed_by == 7


def test_duplicate_draft_prevention_per_conversation():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557773",
        )
        d1 = create_candidate_draft(conv, actor_id=8)
        assert d1.id is not None
        try:
            create_candidate_draft(conv, actor_id=8)
            assert False, "Debió bloquear draft duplicado"
        except ValueError as exc:
            assert "draft_already_exists" in str(exc)


def test_duplicate_draft_race_integrity_error_returns_clean_value_error():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557799",
        )
        with patch("services.bot_candidate_draft_service.db.session.commit", side_effect=IntegrityError("dup", {}, None)):
            try:
                create_candidate_draft(conv, actor_id=8)
                assert False, "Debió mapear IntegrityError a draft_already_exists"
            except ValueError as exc:
                assert "draft_already_exists" in str(exc)


def test_ui_button_enabled_disabled_and_no_outbound_no_whatsapp():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv_enabled = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557774",
        )
        conv_blocked = _make_conv(
            metadata_json={"protocol_entities": {"name": "Ana"}},
            phone="+18095557775",
        )
        conv_enabled_id = int(conv_enabled.id)
        conv_blocked_id = int(conv_blocked.id)

    _login_staff(client)
    with patch("admin.bot_routes.send_text_message") as mocked_send:
        d1 = client.get(f"/admin/bot/conversaciones/{conv_enabled_id}", follow_redirects=False)
        assert d1.status_code == 200
        html1 = d1.get_data(as_text=True)
        assert "Crear borrador de candidata" in html1
        assert "Bloqueado:" not in html1

        d2 = client.get(f"/admin/bot/conversaciones/{conv_blocked_id}", follow_redirects=False)
        assert d2.status_code == 200
        html2 = d2.get_data(as_text=True)
        assert "Crear borrador de candidata" in html2
        assert "Bloqueado:" in html2
        mocked_send.assert_not_called()

    with flask_app.app_context():
        out_count_enabled = BotMessage.query.filter_by(conversation_id=conv_enabled_id, direction="outbound").count()
        out_count_blocked = BotMessage.query.filter_by(conversation_id=conv_blocked_id, direction="outbound").count()
        assert out_count_enabled == 0
        assert out_count_blocked == 0


def test_create_draft_route_and_get_candidate_draft():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557776",
        )
        conv_id = int(conv.id)
    _login_staff(client)
    resp = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    with flask_app.app_context():
        draft = get_candidate_draft(conv_id)
        assert draft is not None
        assert draft.conversation_id == conv_id
        assert draft.draft_status == DRAFT_STATUS_DRAFT


def test_draft_route_transitions_create_audit_logs():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        db.session.query(StaffAuditLog).filter(StaffAuditLog.action_type.like("candidate_draft_%")).delete()
        db.session.commit()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557777",
        )
        conv_id = int(conv.id)
    _login_staff(client)

    r1 = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear", data={}, follow_redirects=False)
    assert r1.status_code in (302, 303)
    r2 = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/revisar", data={}, follow_redirects=False)
    assert r2.status_code in (302, 303)
    r3 = client.post(
        f"/admin/bot/conversaciones/{conv_id}/candidate-draft/rechazar",
        data={"notes": "qa"},
        follow_redirects=False,
    )
    assert r3.status_code in (302, 303)

    with flask_app.app_context():
        actions = [
            x.action_type
            for x in StaffAuditLog.query.filter(StaffAuditLog.action_type.like("candidate_draft_%")).all()
        ]
        assert "candidate_draft_created" in actions
        assert "candidate_draft_reviewed" in actions
        assert "candidate_draft_rejected" in actions


def test_reject_already_rejected_draft_is_blocked():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = _make_conv(
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                },
            },
            phone="+18095557798",
        )
        create_candidate_draft(conv, actor_id=1)
        reject_candidate_draft(int(conv.id), actor_id=2, notes="primero")
        try:
            reject_candidate_draft(int(conv.id), actor_id=3, notes="segundo")
            assert False, "Debió bloquear re-rechazo"
        except ValueError as exc:
            assert "draft_already_rejected" in str(exc)
