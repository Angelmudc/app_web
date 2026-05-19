# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable
from unittest.mock import patch

import pytest

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_protocol_service import load_protocol


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.drop(bind=db.engine, checkfirst=True)

    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotCandidateDraft).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    os.environ["ADMIN_AUTO_PRESENCE_TOUCH_ENABLED"] = "0"
    resp = client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _step_index(step_code: str) -> int:
    steps = load_protocol().get("steps") or []
    normalized = str(step_code or "").strip().upper()
    for idx, step in enumerate(steps):
        if str(step.get("step_code") or "").strip().upper() == normalized:
            return idx
    return -1


def _assert_chat_order_and_association(chat_items: Iterable[dict], inbound_ids: set[int]) -> None:
    ordered = list(chat_items)
    assert ordered, "chat_items vacío"

    associated_inbound_ids: set[int] = set()
    for idx, item in enumerate(ordered):
        role = str(item.get("role") or "")
        if role == "candidate":
            found = False
            for nxt in ordered[idx + 1 :]:
                nxt_role = str(nxt.get("role") or "")
                if nxt_role == "candidate":
                    break
                if nxt_role == "bot_suggested":
                    inbound_id = nxt.get("inbound_message_id")
                    if inbound_id:
                        associated_inbound_ids.add(int(inbound_id))
                    found = True
                    break
            assert found, "Cada inbound debe tener bot_suggested asociado en chat_items"

    assert inbound_ids.issubset(associated_inbound_ids), "Hay inbound sin inbound_message_id asociado en bot_suggested"


def _is_explicit_correction(text: str) -> bool:
    t = str(text or "").lower()
    return bool(re.search(r"\b(no\b.*\b(mejor|vivo|tengo|era|quise)|corrijo|quise decir)\b", t))


@dataclass(frozen=True)
class ChaosCase:
    name: str
    messages: list[str]
    expected_step: str | None = None
    expect_requires_human: bool = False
    expect_city_future_entity: bool = False
    expect_work_type: str | None = None


CASES = [
    ChaosCase(
        name="saludos_caoticos",
        messages=["hola klk hey hola otra vez ???"],
        expected_step="PERSONAL_CONFIRMATION",
    ),
    ChaosCase(
        name="confirmacion_humana_rara",
        messages=["hola si soy yo mmg dale"],
        expected_step="BASIC_INFO",
    ),
    ChaosCase(
        name="datos_mezclados",
        messages=["hola me llamo carmen tengo 30 años soy de puerto plata"],
        expect_city_future_entity=True,
    ),
    ChaosCase(
        name="datos_fuera_de_orden",
        messages=["hola vivo en santiago tengo 33 años salida diaria me llamo ana"],
        expect_work_type="salida",
    ),
    ChaosCase(
        name="typos_dominicanos",
        messages=["hola me yamo carmen tengo treintai dos vivo en gurabo salia diaria"],
    ),
    ChaosCase(
        name="contradiccion_work_type",
        messages=[
            "hola",
            "si soy yo",
            "me llamo elena tengo 31",
            "vivo en santiago",
            "salida diaria",
            "salida diaria no mejor dormida",
        ],
        expect_work_type="dormida",
    ),
    ChaosCase(
        name="ruido_entre_etapas",
        messages=["hola si hola hola me llamo jose tengo 40 hola como tu ta santiago los ciruelitos jeje salida diaria"],
        expect_work_type="salida",
    ),
    ChaosCase(
        name="datos_sensibles_cedula",
        messages=["hola", "si soy yo", "mi cedula es 213-2222222-1", "me llamo juan tengo 35"],
        expect_requires_human=True,
    ),
    ChaosCase(
        name="audio_largo",
        messages=[
            "hola klk mira te hablo rapido, soy maria fernandez, tengo 28 años, vivo por santiago gurabo, "
            "yo trabaje con una familia en villa olga cuidando niños y cocinando, no fui por agencia, "
            "prefiero salida diaria pero si hay que dormir a veces se negocia, jeje tu sabe, "
            "quiero trabajar y entrar al proceso full"
        ],
    ),
    ChaosCase(
        name="emojis_y_caracteres_raros",
        messages=["hola 😂😂 si 👍 me llamo juana 😭 tengo 29"],
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bot_practice_chat_human_chaos(case: ChaosCase, monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    assert create.status_code in (302, 303)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    prev_rank = -1
    max_rank = -1
    work_type_rank = _step_index("WORK_TYPE")
    address_rank = _step_index("ADDRESS")
    same_reply_streak = 0
    prev_reply_norm = ""
    loop_detected = False
    requires_human_seen = False
    last_turn_payload: dict = {}

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        for msg in case.messages:
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
            assert resp.status_code == 200
            data = resp.get_json() or {}
            last_turn_payload = dict(data)
            assert data.get("ok") is True
            assert data.get("suggested_reply")
            assert isinstance(data.get("chat_items"), list)
            assert isinstance(data.get("protocol_entities"), dict)
            assert isinstance(data.get("protocol_future_entities"), dict)
            assert isinstance(data.get("pending_corrections"), list)
            requires_human_seen = requires_human_seen or bool(data.get("requires_human"))

            step = str(data.get("current_step") or "")
            rank = _step_index(step)
            assert rank >= 0, f"step inválido: {step!r}"

            if prev_rank >= 0 and rank < prev_rank:
                has_pending = bool(data.get("pending_corrections"))
                assert has_pending or _is_explicit_correction(msg), f"retroceso ilegal {prev_rank}->{rank} en case={case.name}"
            prev_rank = rank
            max_rank = max(max_rank, rank)

            if max_rank >= work_type_rank and rank == address_rank:
                raise AssertionError("Volvió a ADDRESS después de WORK_TYPE")

            debug_state = dict(data.get("debug_protocol_state") or {})
            last_completed = str(debug_state.get("last_completed_step") or "")
            if last_completed:
                lc_rank = _step_index(last_completed)
                assert lc_rank >= 0, f"last_completed_step inválido: {last_completed!r}"
                assert rank >= lc_rank, f"current_step inconsistente con last_completed_step: {step}/{last_completed}"

            reply_norm = re.sub(r"\s+", " ", str(data.get("suggested_reply") or "").strip().lower())
            if reply_norm == prev_reply_norm and reply_norm:
                same_reply_streak += 1
            else:
                same_reply_streak = 1 if reply_norm else 0
            prev_reply_norm = reply_norm
            if same_reply_streak >= 4:
                loop_detected = True

        send_mock.assert_not_called()

    assert loop_detected is False, f"loop_detected=True case={case.name}"

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    assert state.status_code == 200
    payload = state.get_json() or {}
    assert payload.get("ok") is True

    messages = list(payload.get("messages") or [])
    inbound_ids = {int(m.get("id")) for m in messages if str(m.get("direction") or "") == "inbound" and m.get("id") is not None}
    _assert_chat_order_and_association(payload.get("chat_items") or [], inbound_ids)

    final_step = str(payload.get("current_step") or "")
    assert _step_index(final_step) >= 0

    if case.expected_step:
        assert final_step == case.expected_step

    entities = dict(payload.get("protocol_entities") or {})
    futures = dict(payload.get("protocol_future_entities") or {})
    raw_future = dict((last_turn_payload.get("metadata_json") or {}).get("protocol_future_entities") or {})

    if case.name == "datos_mezclados":
        assert str(entities.get("name") or "").strip(), "name no detectado"
        assert str(entities.get("age") or "").strip(), "age no detectada"
        assert ("city" in raw_future) or str(entities.get("city") or "").strip(), "city no persistida"
        assert final_step in {"BASIC_INFO", "ADDRESS", "WORK_TYPE"}

    if case.expect_city_future_entity:
        assert ("city" in raw_future) or str(entities.get("city") or "").strip()

    if case.expect_work_type:
        future_work_type = ""
        raw_work = raw_future.get("work_type")
        if isinstance(raw_work, dict):
            future_work_type = str(raw_work.get("value") or "").lower()
        elif raw_work is not None:
            future_work_type = str(raw_work).lower()
        wt = str(entities.get("work_type") or "").lower() or future_work_type
        assert case.expect_work_type in wt, f"work_type no actualizado: {wt!r}"

    if case.expect_requires_human:
        assert requires_human_seen or bool(payload.get("requires_human")), "No marcó requires_human"

    if _step_index(final_step) > address_rank:
        assert "city" not in futures, "future_entities eternas: city sigue pendiente después de ADDRESS"

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        drafts = BotCandidateDraft.query.filter_by(conversation_id=conv_id).count()
        assert drafts == 0
