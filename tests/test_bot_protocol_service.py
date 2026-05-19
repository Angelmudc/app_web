# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re

import pytest

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services import bot_protocol_service as protocol_service
from services.bot_conversation_service import get_or_create_manual_conversation, get_protocol_state, set_current_step
from services.bot_conversation_service import (
    advance_protocol_step,
    regress_protocol_step,
    reset_protocol_state,
    select_protocol_step,
)
from services.bot_protocol_service import (
    approve_pending_correction,
    build_step_prompt,
    normalize_correction_text,
    detect_pending_correction,
    detect_expected_answer,
    detect_out_of_step_answer,
    extract_step_entities,
    get_next_step,
    get_step,
    load_protocol,
    mask_sensitive_cedula,
    reject_pending_correction,
    upsert_pending_correction,
    parse_spanish_age_words,
    has_personal_data_signal,
    is_greeting_only,
)


def _ensure_bot_tables() -> None:
    db.session.remove()
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
    db.session.remove()


def _reset_bot_tables() -> None:
    db.session.remove()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()
    db.session.remove()


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    os.environ["ADMIN_AUTO_PRESENCE_TOUCH_ENABLED"] = "0"
    data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=data, follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = (resp.headers.get("Location") or "").lower()
    assert "/admin/login" not in location
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None


def _extract_csrf(html: str) -> str:
    m_meta = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', html)
    if m_meta:
        return m_meta.group(1)
    m_input = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m_input is not None, "No se encontró csrf_token en la vista."
    return m_input.group(1)


def test_protocol_load_and_steps_navigation():
    protocol = load_protocol()
    assert protocol["version"] == "v1"
    assert protocol["business_name"] == "Agencia Doméstica del Cibao A&D"

    step = get_step("WELCOME")
    assert step is not None
    assert step["title"] == "Bienvenida"

    nxt = get_next_step("WELCOME")
    assert nxt is not None
    assert nxt["step_code"] == "PERSONAL_CONFIRMATION"

    prompt = build_step_prompt("WELCOME")
    assert "Agencia Doméstica del Cibao A&D" in prompt


def test_protocol_answer_detection_basics():
    yn_ok = detect_expected_answer("PERSONAL_CONFIRMATION", "SI")
    assert yn_ok["matched"] is True

    yn_bad = detect_expected_answer("PERSONAL_CONFIRMATION", "tal vez")
    assert yn_bad["matched"] is False

    city_ok = detect_expected_answer("ADDRESS", "Vivo en Puerto Plata, centro")
    assert city_ok["matched"] is True

    mode_ok = detect_expected_answer("WORK_TYPE", "prefiero salida diaria")
    assert mode_ok["matched"] is True
    mode_typo_ok = detect_expected_answer("WORK_TYPE", "con domida")
    assert mode_typo_ok["matched"] is True

    pct_ok = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "si, acepto el 25%")
    assert pct_ok["matched"] is True
    pct_no = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "no acepto el 25%")
    assert pct_no["matched"] is False
    city_stg = detect_expected_answer("ADDRESS", "Estoy en Santiago, Gurabo")
    assert city_stg["matched"] is True
    city_plain = detect_expected_answer("ADDRESS", "puerto plata centro")
    assert city_plain["matched"] is True
    tr_bad = detect_expected_answer("TRANSPORT_ROUTE", "no, con dormida mejor")
    assert tr_bad["matched"] is False
    tr_ok = detect_expected_answer("TRANSPORT_ROUTE", "voy en concho por la ruta M")
    assert tr_ok["matched"] is True
    for txt in [
        "voy en concho",
        "cojo la ruta M",
        "me voy en guagua",
        "me llevan",
        "mi esposo me lleva",
        "voy en motor",
        "motoconcho",
        "camino y luego cojo carro",
        "ruta K",
        "ruta M",
        "parada",
    ]:
        assert detect_expected_answer("TRANSPORT_ROUTE", txt)["matched"] is True
    for txt in [
        "no",
        "si",
        "no se",
        "con dormida",
        "salida diaria",
        "mejor dormida",
        "quiero dormida",
        "vivo en Santiago",
        "tengo 30 años",
    ]:
        assert detect_expected_answer("TRANSPORT_ROUTE", txt)["matched"] is False


def test_personal_confirmation_no_confirma_con_saludos():
    for txt in ["hola", "hols", "ola", "buenas", "buen día", "hey", "saludos", "???", ""]:
        assert detect_expected_answer("PERSONAL_CONFIRMATION", txt)["matched"] is False
    for txt in ["si", "sí", "sii", "si soy yo", "soy yo", "correcto", "claro", "dale", "quiero trabajar", "quiero registrarme", "hasme las preguntas"]:
        assert detect_expected_answer("PERSONAL_CONFIRMATION", txt)["matched"] is True


def test_has_personal_data_signal_detecta_datos_y_descarta_saludos():
    positives = [
        "angel manuel",
        "me llamo carmen",
        "tengo 34 años",
        "angel y tengo 34 años",
        "mi cedula es 402-1234567-8",
        "mi teléfono es 8095551234",
    ]
    negatives = ["hola", "hols", "buenas", "saludos", "???", ""]
    for txt in positives:
        assert has_personal_data_signal(txt) is True
    for txt in negatives:
        assert has_personal_data_signal(txt) is False
    assert has_personal_data_signal("no entiendo") is False
    assert has_personal_data_signal("quien pregunta") is False
    assert has_personal_data_signal("que tengo que hacer") is False
    assert has_personal_data_signal("angel y tengo 34 años") is True
    assert has_personal_data_signal("me llamo carmen") is True
    assert has_personal_data_signal("tengo 34 años") is True


def test_is_greeting_only_detecta_repeticion_ruido_y_emojis():
    for txt in ["hola hola", "hola otra vez", "buenas hey", "???", "🙂🙂", "   "]:
        assert is_greeting_only(txt) is True
    for txt in ["me llamo carmen", "tengo 30 años", "vivo en santiago"]:
        assert is_greeting_only(txt) is False


def test_protocol_invalid_step_behaviors():
    assert get_step("NO_EXISTE") is None
    assert get_next_step("NO_EXISTE") is None
    not_found = detect_expected_answer("NO_EXISTE", "hola")
    assert not_found["matched"] is False
    assert not_found["reason"] == "step_not_found"


def test_protocol_last_step_has_no_next():
    protocol = load_protocol()
    last = protocol["steps"][-1]["step_code"]
    assert get_next_step(last) is None


def test_protocol_missing_or_invalid_file_raises(tmp_path, monkeypatch):
    protocol_service.load_protocol.cache_clear()
    missing_path = tmp_path / "missing.json"
    monkeypatch.setattr(protocol_service, "DATA_PATH", missing_path)
    with pytest.raises(ValueError):
        protocol_service.load_protocol()

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{not-valid-json", encoding="utf-8")
    protocol_service.load_protocol.cache_clear()
    monkeypatch.setattr(protocol_service, "DATA_PATH", invalid_path)
    with pytest.raises(ValueError):
        protocol_service.load_protocol()

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps({"protocol_code": "x", "steps": []}), encoding="utf-8")
    protocol_service.load_protocol.cache_clear()
    monkeypatch.setattr(protocol_service, "DATA_PATH", valid_path)
    with pytest.raises(ValueError):
        protocol_service.load_protocol()

    bad_step_path = tmp_path / "bad_step.json"
    bad_step_path.write_text(
        json.dumps(
            {
                "protocol_code": "x",
                "steps": [
                    {
                        "step_code": "A",
                        "messages": {"primary": [], "secondary": "invalid", "warnings": []},
                        "validations": [],
                        "expected_answers": [],
                        "fallback": "x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    protocol_service.load_protocol.cache_clear()
    monkeypatch.setattr(protocol_service, "DATA_PATH", bad_step_path)
    with pytest.raises(ValueError):
        protocol_service.load_protocol()

    protocol_service.load_protocol.cache_clear()


def test_percentage_acceptance_variants():
    ok_1 = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "Sí, acepto 25 por ciento.")
    assert ok_1["matched"] is True
    ok_2 = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "si acepto 25%")
    assert ok_2["matched"] is True
    ok_3 = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "si, acepto veinticinco")
    assert ok_3["matched"] is True
    no_1 = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "si acepto, pero no el 25")
    assert no_1["matched"] is False
    ok_short = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "ta bien")
    assert ok_short["matched"] is True


def test_basic_info_contextual_entity_extraction_variants():
    r1 = extract_step_entities("BASIC_INFO", "me llamo juana")
    assert r1["entities"].get("name") == "juana"
    assert "age" in r1["missing_fields"]

    r2 = extract_step_entities("BASIC_INFO", "tengo 22 años")
    assert r2["entities"].get("age") == 22
    assert "name" in r2["missing_fields"]

    r3 = extract_step_entities("BASIC_INFO", "me llamo angel manuel y tengo 26 años")
    assert r3["matched"] is True
    assert r3["entities"].get("name") == "angel manuel"
    assert r3["entities"].get("age") == 26
    assert r3["missing_fields"] == []

    r4 = extract_step_entities("BASIC_INFO", "mi cedula es 402-1234567-8")
    assert r4["requires_human"] is True
    assert r4["entities"].get("cedula_masked") == "402-2***-***"
    r5 = extract_step_entities("BASIC_INFO", "me llamo juana tengo 32 vivo en santiago quiero dormida")
    future = dict(r5.get("future_entities") or {})
    assert future.get("city") == "Santiago"
    assert future.get("work_type") == "dormida"
    r6 = extract_step_entities("BASIC_INFO", "yulisa 28")
    assert r6["entities"].get("name") == "yulisa"
    assert r6["entities"].get("age") == 28
    r7 = extract_step_entities("BASIC_INFO", "juana perez 32")
    assert r7["entities"].get("name") == "juana perez"
    assert r7["entities"].get("age") == 32
    r8 = extract_step_entities("BASIC_INFO", "yulisa 28 santiago salida diaria")
    assert r8["entities"].get("name") == "yulisa"
    assert r8["entities"].get("age") == 28
    future8 = dict(r8.get("future_entities") or {})
    assert future8.get("city") == "Santiago"
    assert future8.get("work_type") == "salida diaria"


def test_spanish_age_words_parser():
    assert parse_spanish_age_words("tengo treinta y dos años") == 32
    assert parse_spanish_age_words("tengo veintiocho") == 28
    assert parse_spanish_age_words("cuarenta y uno") == 41
    assert parse_spanish_age_words("dieciocho") == 18


def test_basic_info_accumulates_existing_entities():
    r = extract_step_entities("BASIC_INFO", "tengo 26 años", existing_entities={"name": "angel manuel"})
    assert r["matched"] is True
    assert r["merged_entities"].get("name") == "angel manuel"
    assert r["merged_entities"].get("age") == 26
    assert r["missing_fields"] == []


def test_references_and_skills_safety_rules():
    labor_ok = extract_step_entities("LABOR_REFERENCES", "trabaje con dona ana 8095551111")
    assert labor_ok["matched"] is True
    assert labor_ok["requires_human"] is False

    labor_missing_phone = extract_step_entities("LABOR_REFERENCES", "trabaje con una senora pero no tengo numero")
    assert labor_missing_phone["matched"] is False
    assert labor_missing_phone["requires_human"] is True

    family_refusal = extract_step_entities("FAMILY_REFERENCES", "no quiero dar referencia familiar")
    assert family_refusal["matched"] is False
    assert family_refusal["requires_human"] is True

    skills_ok = extract_step_entities("SKILLS", "se cuidar ninos envejecientes cocinar y limpiar")
    assert skills_ok["matched"] is True
    assert skills_ok["requires_human"] is False

    skills_no_experience = extract_step_entities("SKILLS", "no tengo experiencia pero aprendo")
    assert skills_no_experience["matched"] is False
    assert skills_no_experience["requires_human"] is True


def test_anti_false_positive_noise_rules():
    n1 = extract_step_entities("LABOR_REFERENCES", "la vecina me conoce")
    assert n1["matched"] is False
    assert n1["requires_human"] is False

    n2 = extract_step_entities("SKILLS", "me gusta cocinar")
    assert n2["matched"] is False
    assert n2["requires_human"] is False

    n3 = extract_step_entities("BASIC_INFO", "mi numero favorito es 40212345678")
    assert n3["entities"].get("cedula_detected") is None
    assert n3["requires_human"] is False

    n4 = detect_expected_answer("PERCENTAGE_ACCEPTANCE", "25 minutos me queda lejos")
    assert n4["matched"] is False

    n5 = detect_expected_answer("ADDRESS", "mi hermana vive en santiago")
    assert n5["matched"] is False

    n6 = detect_expected_answer("TRANSPORT_ROUTE", "vivo por la m")
    assert n6["matched"] is False


def test_mask_sensitive_cedula():
    assert mask_sensitive_cedula("40212345678") == "402-2***-***"


def test_detect_out_of_step_answer_cases():
    a = detect_out_of_step_answer("TRANSPORT_ROUTE", "no, con dormida mejor")
    assert a["out_of_step"] is True
    assert a["suggested_step_code"] == "WORK_TYPE"

    b = detect_out_of_step_answer("ADDRESS", "salida diaria")
    assert b["out_of_step"] is True
    assert b["suggested_step_code"] == "WORK_TYPE"

    c = detect_out_of_step_answer("BASIC_INFO", "quiero dormida")
    assert c["out_of_step"] is True
    assert c["suggested_step_code"] == "WORK_TYPE"

    d = detect_out_of_step_answer("PERCENTAGE_ACCEPTANCE", "vivo en Santiago")
    assert d["out_of_step"] is True
    assert d["suggested_step_code"] == "ADDRESS"
    e = detect_out_of_step_answer("TRANSPORT_ROUTE", "tengo 30 años")
    assert e["out_of_step"] is True
    assert e["suggested_step_code"] == "BASIC_INFO"


def test_detect_pending_correction_cases():
    a = detect_pending_correction("TRANSPORT_ROUTE", "no, mi edad es 30", {"age": 26})
    assert a["has_correction"] is True
    assert a["field"] == "age"
    assert a["new_value"] == "30"
    assert a["old_value"] == 26
    assert a["suggested_step_code"] == "BASIC_INFO"

    b = detect_pending_correction("TRANSPORT_ROUTE", "me equivoqué, me llamo Juana", {"name": "Angel"})
    assert b["has_correction"] is True
    assert b["field"] == "name"
    assert b["new_value"] == "juana"

    c = detect_pending_correction("TRANSPORT_ROUTE", "mejor dormida", {"work_type": "salida diaria"})
    assert c["has_correction"] is True
    assert c["field"] == "work_type"
    assert c["old_value"] == "salida diaria"
    assert c["new_value"] == "dormida"
    assert c["suggested_step_code"] == "WORK_TYPE"

    d = detect_pending_correction("TRANSPORT_ROUTE", "mi ruta es la M", {"route": "ruta K"})
    assert d["has_correction"] is True
    assert d["field"] == "route"
    assert d["old_value"] == "ruta K"
    assert d["new_value"] == "ruta M"
    assert d["suggested_step_code"] == "TRANSPORT_ROUTE"

    e = detect_pending_correction("TRANSPORT_ROUTE", "ese no es mi número", {"phone": "8095551234"})
    assert e["has_correction"] is True
    assert e["field"] == "phone"
    assert e["requires_human"] is True


def test_detect_pending_correction_prefixes_and_global_fields():
    cases = [
        ("no, mi edad es 30", "age", "30", "BASIC_INFO"),
        ("nop, tengo 30", "age", "30", "BASIC_INFO"),
        ("perdón tengo 31", "age", "31", "BASIC_INFO"),
        ("corrijo, tengo 30", "age", "30", "BASIC_INFO"),
        ("quise decir 30", "age", "30", "BASIC_INFO"),
        ("me equivoqué, me llamo Juana", "name", "juana", "BASIC_INFO"),
        ("no, mi nombre es María", "name", "maria", "BASIC_INFO"),
        ("mejor dormida", "work_type", "dormida", "WORK_TYPE"),
        ("no, con dormida mejor", "work_type", "dormida", "WORK_TYPE"),
        ("mejor salida diaria", "work_type", "salida diaria", "WORK_TYPE"),
        ("mi ruta es la M", "route", "ruta M", "TRANSPORT_ROUTE"),
        ("no, voy en concho por la ruta K", "route", "ruta K", "TRANSPORT_ROUTE"),
        ("ese no es mi número", "phone", "", "BASIC_INFO"),
        ("cambié de número", "phone", "", "BASIC_INFO"),
    ]
    for txt, field, new_value, suggested in cases:
        out = detect_pending_correction("TRANSPORT_ROUTE", txt, {})
        assert out["has_correction"] is True
        assert out["field"] == field
        assert out["new_value"] == new_value
        assert out["suggested_step_code"] == suggested
        assert out["requires_human"] is True

    no_old = detect_pending_correction("TRANSPORT_ROUTE", "no, mi edad es 30", {})
    assert no_old["has_correction"] is True
    assert no_old["field"] == "age"
    assert no_old["old_value"] is None


def test_normalize_correction_text_prefixes():
    p = normalize_correction_text("no, mi edad es 30")
    assert p["normalized_text"] == "no, mi edad es 30"
    assert p["analysis_text"] == "mi edad es 30"
    assert p["has_correction_cue"] is True


def test_pending_correction_upsert_duplicate_and_supersede():
    meta = {"pending_corrections": []}
    meta, c1, act1 = upsert_pending_correction(
        meta,
        {
            "field": "age",
            "old_value": 26,
            "new_value": "30",
            "suggested_step_code": "BASIC_INFO",
            "source_message_id": 11,
            "normalized_text": "no, mi edad es 30",
            "original_text": "no, mi edad es 30",
        },
    )
    assert act1 == "created"
    assert c1["status"] == "pending_human"
    assert c1["duplicate_count"] == 1

    meta, c1b, act2 = upsert_pending_correction(
        meta,
        {
            "field": "age",
            "old_value": 26,
            "new_value": "30",
            "suggested_step_code": "BASIC_INFO",
            "source_message_id": 12,
            "normalized_text": "nop, tengo 30",
            "original_text": "nop, tengo 30",
        },
    )
    assert act2 == "duplicate_updated"
    assert c1b["id"] == c1["id"]
    assert c1b["duplicate_count"] == 2
    assert len(meta["pending_corrections"]) == 1

    meta, c2, act3 = upsert_pending_correction(
        meta,
        {
            "field": "age",
            "old_value": 30,
            "new_value": "31",
            "suggested_step_code": "BASIC_INFO",
            "source_message_id": 13,
            "normalized_text": "perdon tengo 31",
            "original_text": "perdón tengo 31",
        },
    )
    assert act3 == "superseded_created"
    assert len(meta["pending_corrections"]) == 2
    prev = [x for x in meta["pending_corrections"] if int(x.get("id")) == int(c1["id"])][0]
    assert prev["status"] == "superseded"
    assert prev["superseded_by_id"] == c2["id"]
    assert c2["status"] == "pending_human"


def test_pending_correction_approve_reject_rules():
    meta = {"protocol_entities": {"age": 26}, "pending_corrections": []}
    meta, c1, _ = upsert_pending_correction(
        meta,
        {
            "field": "age",
            "old_value": 26,
            "new_value": "30",
            "suggested_step_code": "BASIC_INFO",
            "source_message_id": 21,
            "normalized_text": "no, mi edad es 30",
            "original_text": "no, mi edad es 30",
        },
    )
    meta2, approved = approve_pending_correction(meta, int(c1["id"]), 99)
    assert approved["status"] == "approved"
    assert approved["approved_by"] == 99
    assert (meta2.get("protocol_entities") or {}).get("age") == "30"

    with pytest.raises(ValueError):
        reject_pending_correction(meta2, int(c1["id"]), 99, "no aplica")

    meta3 = {"protocol_entities": {"work_type": "salida diaria"}, "pending_corrections": []}
    meta3, c2, _ = upsert_pending_correction(
        meta3,
        {
            "field": "work_type",
            "old_value": "salida diaria",
            "new_value": "dormida",
            "suggested_step_code": "WORK_TYPE",
            "source_message_id": 22,
            "normalized_text": "mejor dormida",
            "original_text": "mejor dormida",
        },
    )
    before_entities = dict(meta3.get("protocol_entities") or {})
    meta4, rejected = reject_pending_correction(meta3, int(c2["id"]), 100, "invalida")
    assert rejected["status"] == "rejected"
    assert rejected["rejected_by"] == 100
    assert rejected["rejection_reason"] == "invalida"
    assert (meta4.get("protocol_entities") or {}) == before_entities

    with pytest.raises(ValueError):
        approve_pending_correction(meta4, int(c2["id"]), 100)


def test_conversation_protocol_state_progress_and_admin_render():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = get_or_create_manual_conversation(phone_e164="+18095551111", contact_name="Proto Test")
        state = get_protocol_state(conv)
        assert state["current_step_code"] == "WELCOME"
        assert state["protocol_version"] == "domesticas_v1"

        set_current_step(conv, current_step_code="ADDRESS", last_completed_step="BASIC_INFO")
        state2 = get_protocol_state(conv)
        assert state2["current_step_code"] == "ADDRESS"
        assert state2["last_completed_step"] == "BASIC_INFO"
        assert state2["next_step_code"] == "WORK_TYPE"
        conv_id = int(conv.id)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Protocolo de captación" in html
    assert "Etapa actual:" in html
    assert "ADDRESS" in html
    assert "Siguiente etapa:" in html
    assert "WORK_TYPE" in html
    assert "domesticas_v1" in html


def test_conversation_legacy_metadata_empty_does_not_break_admin():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095552222", contact_name="Legacy", status="open", metadata_json={})
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)
        state = get_protocol_state(conv)
        assert state["current_step_code"] == "WELCOME"
        assert state["protocol_version"] == "domesticas_v1"

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Protocolo de captación" in html
    assert "WELCOME" in html


def test_admin_detail_protocol_load_failure_fallback(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = get_or_create_manual_conversation(phone_e164="+18095553333", contact_name="Proto Fail")
        conv_id = int(conv.id)

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("admin.bot_routes.get_step", _raise)
    monkeypatch.setattr("admin.bot_routes.get_next_step", _raise)
    monkeypatch.setattr("admin.bot_routes.build_step_prompt", _raise)

    _login_staff(client)
    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Protocolo de captación" in html
    assert "Etapa actual:" in html


def test_admin_detail_requires_login_redirects_to_admin_login():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = get_or_create_manual_conversation(phone_e164="+18095559999", contact_name="No Auth")
        conv_id = int(conv.id)

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code in (302, 303)
    assert "/admin/login" in (detail.headers.get("Location") or "")


def test_protocol_manual_flow_helpers_and_audit():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = get_or_create_manual_conversation(phone_e164="+18095554444", contact_name="Flow Manual")
        assert get_protocol_state(conv)["current_step_code"] == "WELCOME"
        msg_before = BotMessage.query.filter_by(conversation_id=conv.id).count()

        advance_protocol_step(conv, actor_id=99)
        assert get_protocol_state(conv)["current_step_code"] == "PERSONAL_CONFIRMATION"

        regress_protocol_step(conv, actor_id=99)
        assert get_protocol_state(conv)["current_step_code"] == "WELCOME"

        select_protocol_step(conv, step_code="ADDRESS", actor_id=99)
        state = get_protocol_state(conv)
        assert state["current_step_code"] == "ADDRESS"
        assert state["last_completed_step"] == "BASIC_INFO"

        with pytest.raises(ValueError):
            select_protocol_step(conv, step_code="BAD_STEP", actor_id=99)

        reset_protocol_state(conv, actor_id=99)
        assert get_protocol_state(conv)["current_step_code"] == "WELCOME"

        msg_after = BotMessage.query.filter_by(conversation_id=conv.id).count()
        assert msg_before == msg_after

        logs = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="protocol_step_change")
            .order_by(BotDecisionLog.id.asc())
            .all()
        )
        assert len(logs) >= 4
        last = logs[-1]
        assert last.decision_result == "manual_only"
        assert str(last.rule_code).startswith("PROTOCOL_")
        assert (last.facts_json or {}).get("action") == "reset"
