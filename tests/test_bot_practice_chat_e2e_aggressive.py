# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
from dataclasses import dataclass
from typing import Iterable
from unittest.mock import patch

import pytest

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
    data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=data, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _extract_csrf(html: str) -> str:
    m_meta = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', html)
    if m_meta:
        return m_meta.group(1)
    m_input = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m_input is not None, "No se encontró csrf_token en la vista."
    return m_input.group(1)


@dataclass(frozen=True)
class AggressiveCase:
    name: str
    messages: list[str]
    min_step_rank: int = 0
    expect_requires_human: bool = False
    expect_pending_corrections: bool = False
    expect_draft_possible: bool = False


def _step_index(step_code: str) -> int:
    steps = load_protocol().get("steps") or []
    normalized = str(step_code or "").strip().upper()
    for idx, step in enumerate(steps):
        if str(step.get("step_code") or "").strip().upper() == normalized:
            return idx
    return -1


def _has_explicit_correction(text: str) -> bool:
    t = str(text or "").strip().lower()
    return bool(re.search(r"\b(no\b.*\b(yo|tengo|vivo|era|quise)|corrijo|me corrigo|quise decir)\b", t))


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


CASES = [
    AggressiveCase(
        name="happy_path_normal",
        messages=[
            "hola",
            "si soy yo",
            "me llamo carmen tengo 32 años",
            "vivo en santiago gurabo",
            "salida diaria",
            "ruta m",
            "no he estado en agencia",
            "acepto el 25",
            "referencia laboral ana 8095551111",
            "referencia familiar maria 8095552222",
            "sé cocinar limpiar cuidar niños",
            "entiendo",
            "quiero entrar al grupo",
            "acepto las reglas",
            "tengo cédula",
            "tengo foto",
        ],
        min_step_rank=10,
        expect_draft_possible=True,
    ),
    AggressiveCase(
        name="typos_dominicanos",
        messages=[
            "hols",
            "sii",
            "me yamo carme tengo treintai dos",
            "bibo en gurabo",
            "kiero salida",
            "boy en concho ruta m",
        ],
        min_step_rank=4,
    ),
    AggressiveCase(
        name="fuera_de_orden",
        messages=[
            "hola",
            "si soy yo",
            "salida diaria",
            "me llamo juana tengo 29 años",
            "vivo en santiago",
        ],
        min_step_rank=3,
    ),
    AggressiveCase(
        name="correccion_real",
        messages=[
            "hola",
            "si soy yo",
            "me llamo luisa tengo 30 años",
            "no, tengo 31",
            "vivo en Santiago",
            "no vivo en Puerto Plata",
        ],
        min_step_rank=3,
        expect_pending_corrections=True,
    ),
    AggressiveCase(
        name="rechazo_requiere_humano",
        messages=[
            "hola",
            "si soy yo",
            "me llamo rosa tengo 35 años",
            "vivo en santiago",
            "salida diaria",
            "ruta m",
            "acepto el 25",
            "no quiero dar referencia familiar",
            "no quiero mandar cédula",
        ],
        min_step_rank=7,
        expect_requires_human=True,
    ),
    AggressiveCase(
        name="ruido_confusion",
        messages=["hola", "no entiendo", "quien pregunta", "que tengo que hacer", "quiero trabajar"],
        min_step_rank=2,
    ),
    AggressiveCase(
        name="datos_sensibles",
        messages=[
            "hola",
            "si soy yo",
            "mi cedula es 213-2222222-1",
            "foto/documentos",
            "me llamo fatima tengo 28 años",
        ],
        min_step_rank=2,
        expect_requires_human=True,
    ),
    AggressiveCase(
        name="repeticion_loop",
        messages=["hola", "hola", "hola", "si soy yo"],
        min_step_rank=2,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bot_practice_chat_e2e_aggressive(case: AggressiveCase, monkeypatch):
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
    explicit_correction_seen = False
    requires_human_seen = False

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message") as send_mock:
        for msg in case.messages:
            explicit_correction_seen = explicit_correction_seen or _has_explicit_correction(msg)
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
            assert resp.status_code != 500, f"500 en mensaje={msg!r} case={case.name}"
            assert resp.status_code != 400, f"400 inesperado en mensaje={msg!r} case={case.name}"
            assert resp.status_code == 200, f"status inesperado={resp.status_code}"
            data = resp.get_json() or {}
            assert data.get("ok") is True
            requires_human_seen = requires_human_seen or bool(data.get("requires_human"))

            step = str(data.get("current_step") or "")
            rank = _step_index(step)
            assert rank >= 0, f"step inválido {step!r}"
            if prev_rank >= 0 and rank < prev_rank:
                has_pending = bool(data.get("pending_corrections"))
                assert has_pending or explicit_correction_seen, (
                    f"Regresión indebida de step {prev_rank}->{rank} sin corrección explícita case={case.name}"
                )
            prev_rank = rank

            assert isinstance(data.get("debug_protocol_state"), dict)
            assert isinstance(data.get("chat_items"), list)
            assert isinstance(data.get("pending_corrections"), list)
            assert isinstance(data.get("protocol_entities"), dict)
            assert isinstance(data.get("protocol_future_entities"), dict)

        send_mock.assert_not_called()

    debug = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
    assert debug.status_code == 200
    payload = debug.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("conversation_id") == conv_id
    assert payload.get("conversation_type") == "local_practice"
    assert isinstance(payload.get("debug_protocol_state"), dict)
    assert isinstance(payload.get("chat_items"), list)

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    assert state.status_code == 200
    state_data = state.get_json() or {}
    assert state_data.get("ok") is True

    messages = list(state_data.get("messages") or [])
    inbound_ids = {int(m.get("id")) for m in messages if str(m.get("direction") or "") == "inbound" and m.get("id") is not None}
    _assert_chat_order_and_association(state_data.get("chat_items") or [], inbound_ids)

    final_rank = _step_index(str(state_data.get("current_step") or ""))
    assert final_rank >= case.min_step_rank, (
        f"No avanzó lo suficiente. final_rank={final_rank} min={case.min_step_rank} case={case.name}"
    )

    if case.expect_requires_human:
        assert requires_human_seen or bool(state_data.get("requires_human")) or bool(payload.get("requires_human"))
    if case.expect_pending_corrections:
        pending = list(state_data.get("pending_corrections") or [])
        assert pending, "Se esperaban pending_corrections"
    if case.expect_draft_possible:
        assert bool(state_data.get("draft_possible")) is True

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
        drafts = BotCandidateDraft.query.filter_by(conversation_id=conv_id).count()
        assert drafts == 0, "No debe crearse draft real automáticamente en práctica"
