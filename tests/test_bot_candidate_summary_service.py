# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from sqlalchemy import text
from services.bot_candidate_summary_service import (
    SUMMARY_STATUS_BLOCKED_PENDING_CORRECTIONS,
    SUMMARY_STATUS_INCOMPLETE,
    SUMMARY_STATUS_READY_FOR_REVIEW,
    SUMMARY_STATUS_REQUIRES_HUMAN,
    build_candidate_summary,
    get_candidate_summary_status,
    get_missing_required_candidate_fields,
)


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
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
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


def test_candidate_summary_incomplete():
    conv = SimpleNamespace(
        phone_e164="+18095550000",
        metadata_json={"protocol_entities": {"name": "Ana", "age": 25}},
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_INCOMPLETE
    missing = get_missing_required_candidate_fields(conv)
    assert "phone" not in missing
    assert "city" in missing


def test_candidate_summary_empty_metadata_is_incomplete_and_safe():
    conv = SimpleNamespace(phone_e164=None, metadata_json={})
    summary = build_candidate_summary(conv)
    assert isinstance(summary, dict)
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_INCOMPLETE
    assert "name" in summary["missing_required_fields"]


def test_candidate_summary_phone_not_required_when_no_phone_source_exists():
    conv = SimpleNamespace(
        phone_e164=None,
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "referencias_laborales": "Maria",
            }
        },
    )
    missing = get_missing_required_candidate_fields(conv)
    assert "phone" not in missing
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_READY_FOR_REVIEW


def test_candidate_summary_ready_for_review():
    conv = SimpleNamespace(
        phone_e164="+18095550001",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": "si",
                "referencias_laborales": "Maria",
            }
        },
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_READY_FOR_REVIEW


def test_candidate_summary_complete_with_non_string_values():
    conv = SimpleNamespace(
        phone_e164="+18095550009",
        metadata_json={
            "protocol_entities": {
                "name": {"full": "Ana"},
                "age": 25,
                "city": ["Santiago", "Centro"],
                "work_type": {"label": "salida diaria"},
                "route": ["ruta K"],
                "acceptance_25": 1,
                "referencias_laborales": ["Maria", "Juana"],
            }
        },
    )
    summary = build_candidate_summary(conv)
    assert summary["fields"]["name"] == "full: Ana"
    assert summary["fields"]["city"] == "Santiago, Centro"
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_READY_FOR_REVIEW


def test_candidate_summary_blocked_by_pending_correction():
    conv = SimpleNamespace(
        phone_e164="+18095550002",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
            },
            "pending_corrections": [{"id": 1, "field": "age", "status": "pending_human", "old_value": 24, "new_value": 25}],
        },
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_BLOCKED_PENDING_CORRECTIONS


def test_candidate_summary_rejected_or_superseded_corrections_do_not_block():
    conv = SimpleNamespace(
        phone_e164="+18095550010",
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
            "pending_corrections": [
                {"id": 1, "field": "age", "status": "rejected"},
                {"id": 2, "field": "route", "status": "superseded"},
            ],
        },
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_READY_FOR_REVIEW


def test_candidate_summary_approved_reflected_in_entities():
    conv = SimpleNamespace(
        phone_e164="+18095550011",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": "30",
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "referencias_familiares": "Juana",
            },
            "pending_corrections": [{"id": 7, "field": "age", "status": "approved", "new_value": "30"}],
        },
    )
    summary = build_candidate_summary(conv)
    assert summary["fields"]["age"] == "30"
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_READY_FOR_REVIEW


def test_candidate_summary_requires_human_for_sensitive_data():
    conv = SimpleNamespace(
        phone_e164="+18095550003",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "cedula_masked": "402-2***-***",
            }
        },
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_REQUIRES_HUMAN


def test_candidate_summary_requires_human_for_documents_and_photo():
    conv = SimpleNamespace(
        phone_e164="+18095550012",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "referencias_laborales": "Maria",
                "documentos_indicados": "cedula",
                "foto_indicada": "si",
            }
        },
    )
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_REQUIRES_HUMAN


def test_candidate_summary_masks_cedula_in_text_fields():
    conv = SimpleNamespace(
        phone_e164="+18095550013",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
                "referencias_laborales": "Mi cedula 402-1234567-8",
                "referencias_familiares": "40212345678",
                "observaciones": "doc 402 1234567 8",
            }
        },
    )
    summary = build_candidate_summary(conv)
    assert "402-2***-***" in str(summary["fields"]["work_references"])
    assert "1234567" not in str(summary["fields"]["work_references"])
    assert "1234567" not in str(summary["fields"]["family_references"])
    assert "1234567" not in str(summary["fields"]["observations"])


def test_candidate_summary_requires_at_least_one_reference():
    conv = SimpleNamespace(
        phone_e164="+18095550014",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "work_type": "salida diaria",
                "route": "ruta K",
                "acceptance_25": True,
            }
        },
    )
    missing = get_missing_required_candidate_fields(conv)
    assert "references_any" in missing
    assert get_candidate_summary_status(conv) == SUMMARY_STATUS_INCOMPLETE


def test_build_candidate_summary_includes_requested_fields():
    conv = SimpleNamespace(
        phone_e164="+18095550004",
        metadata_json={
            "protocol_entities": {
                "name": "Ana",
                "age": 25,
                "city": "Santiago",
                "sector": "Los Jardines",
                "work_type": "dormida",
                "route": "ruta P",
                "experiencia": "3 anos limpieza",
                "referencias_laborales": "Maria 809...",
                "referencias_familiares": "Juana 809...",
                "acceptance_25": True,
                "documentos_indicados": "cedula y papel buena conducta",
                "foto_indicada": "si",
                "observaciones": "prefiere entrada temprana",
            },
            "pending_corrections": [{"id": 7, "field": "route", "status": "pending_human", "old_value": "ruta M", "new_value": "ruta P"}],
        },
    )
    summary = build_candidate_summary(conv)
    assert summary["fields"]["name"] == "Ana"
    assert summary["fields"]["sector_address"] == "Los Jardines"
    assert summary["fields"]["documents_indicated"] == "cedula y papel buena conducta"
    assert len(summary["pending_corrections_active"]) == 1


def test_ui_candidate_summary_shows_missing_and_pending_and_is_read_only():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(
            channel="whatsapp",
            phone_e164="+18095550005",
            contact_name="Resumen UI",
            status="open",
            metadata_json={
                "protocol_entities": {"name": "Ana", "age": 24},
                "pending_corrections": [{"id": 11, "field": "age", "status": "pending_human", "old_value": 23, "new_value": 24}],
            },
        )
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Resumen de candidata" in html
    assert "Campos faltantes" in html
    assert "city" in html
    assert "Correcciones pendientes" in html
    assert "Este resumen no crea ni actualiza candidatas. Requiere revisión humana." in html
    assert "Crear/actualizar candidata" in html
    assert "Próxima fase: requiere aprobación humana." in html
    assert "blocked_pending_corrections" in html
    assert "Bloqueado:" in html


def test_ui_candidate_summary_shows_requires_human_status():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(
            channel="whatsapp",
            phone_e164="+18095550015",
            contact_name="Resumen UI Sensitive",
            status="open",
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 24,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                    "referencias_laborales": "Maria",
                    "cedula_masked": "402-2***-***",
                },
            },
        )
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "requires_human" in html
    assert "Revisión humana obligatoria" in html


def test_ui_summary_render_does_not_create_or_modify_candidate_nor_outbound_nor_whatsapp():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(
            channel="whatsapp",
            phone_e164="+18095550006",
            contact_name="No Side Effects",
            status="open",
            metadata_json={
                "protocol_entities": {
                    "name": "Ana",
                    "age": 25,
                    "city": "Santiago",
                    "work_type": "salida diaria",
                    "route": "ruta K",
                    "acceptance_25": True,
                }
            },
        )
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)
        before_candidata_count = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        before_outbound_count = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()

    _login_staff(client)
    with patch("admin.bot_routes.send_text_message") as mocked_send:
        detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
        assert detail.status_code == 200
        mocked_send.assert_not_called()

    with flask_app.app_context():
        after_candidata_count = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        after_outbound_count = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert after_candidata_count == before_candidata_count
        assert after_outbound_count == before_outbound_count
