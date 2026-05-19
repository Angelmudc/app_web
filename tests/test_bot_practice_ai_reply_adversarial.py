# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from services.bot_practice_ai_reply_service import get_practice_reply_with_ai_fallback


def _conv_local_practice() -> SimpleNamespace:
    return SimpleNamespace(metadata_json={"conversation_type": "local_practice"})


def _enable_local_ai(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_PRACTICE_REAL_OUTBOUND_ENABLED", "false")
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")


def _run(monkeypatch, ai_text: str, *, base: str, step: str = "BASIC_INFO", candidate: str = "hola", requires_human: bool = False):
    _enable_local_ai(monkeypatch)
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value=ai_text):
        return get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply=base,
            current_step=step,
            candidate_message=candidate,
            requires_human=requires_human,
        )


def test_adversarial_1_promete_empleo_fallback(monkeypatch):
    out = _run(monkeypatch, "Te conseguimos empleo hoy mismo.", base="Gracias. Continuemos.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "dangerous_promise"


def test_adversarial_2_aprueba_candidata_fallback(monkeypatch):
    out = _run(monkeypatch, "Ya estás aprobada.", base="Seguimos con el proceso.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "dangerous_promise"


def test_adversarial_3_dice_envio_whatsapp_fallback(monkeypatch):
    out = _run(monkeypatch, "Te lo envié por WhatsApp.", base="Te comparto la información por aquí.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "claims_whatsapp_sent"


def test_adversarial_4_cambia_intencion_si_no_fallback(monkeypatch):
    out = _run(monkeypatch, "Envíame tu cédula y foto.", base="Responde SI o NO.", step="PERSONAL_CONFIRMATION")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] in {"changed_binary_intent", "out_of_step_request"}


def test_adversarial_5_pide_fuera_etapa_fallback(monkeypatch):
    out = _run(monkeypatch, "Comparte tu dirección y sector.", base="Responde SI o NO.", step="PERSONAL_CONFIRMATION")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] in {"out_of_step_request", "changed_binary_intent"}


def test_adversarial_6_inventa_dato_fallback(monkeypatch):
    out = _run(monkeypatch, "Gracias Carmen, ya anoté que tienes 30 años.", base="Gracias. Continuemos.", candidate="hola")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "invented_candidate_data"


def test_adversarial_7_demasiado_larga_fallback(monkeypatch):
    out = _run(monkeypatch, "x" * 251, base="Gracias. Continuemos.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "reply_too_long"


def test_adversarial_8_vacia_fallback(monkeypatch):
    out = _run(monkeypatch, "", base="Gracias. Continuemos.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "empty_ai_reply"


def test_adversarial_9_tono_no_profesional_fallback(monkeypatch):
    out = _run(monkeypatch, "Mi amor manda eso rápido.", base="Comparte tu ciudad por favor.")
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "unprofessional_tone"


def test_adversarial_10_requires_human_sin_aviso_fallback(monkeypatch):
    out = _run(
        monkeypatch,
        "Gracias. Continuamos.",
        base="Este caso requiere revisión humana.",
        requires_human=True,
    )
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "requires_human_notice_missing"
