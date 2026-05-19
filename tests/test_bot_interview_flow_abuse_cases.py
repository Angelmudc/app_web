from __future__ import annotations

import pytest

from models import BotConversation
from services.bot_interview_flow_service import (
    FLOW_KEY,
    STEP_ASK_AGE,
    STEP_ASK_AVAILABILITY,
    STEP_ASK_CITY_SECTOR,
    STEP_ASK_EXPERIENCE,
    STEP_ASK_NAME,
    STEP_ASK_REFERENCES,
    STEP_ASK_SKILLS,
    process_interview_inbound,
)


def _norm(text: str) -> str:
    return (
        str(text or "")
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("¿", "")
        .replace("?", "")
    )


def _new_conversation() -> BotConversation:
    return BotConversation(
        channel="whatsapp",
        phone_e164="+19990000077",
        contact_name="Candidata QA",
        status="open",
        metadata_json={"sandbox_conversation": True},
    )


def _conversation_at_step(step: str) -> BotConversation:
    conv = _new_conversation()
    warmup = {
        STEP_ASK_NAME: ["hola"],
        STEP_ASK_AGE: ["hola", "Maria Perez"],
        STEP_ASK_CITY_SECTOR: ["hola", "Maria Perez", "30"],
        STEP_ASK_EXPERIENCE: ["hola", "Maria Perez", "30", "Santiago Gurabo"],
        STEP_ASK_SKILLS: ["hola", "Maria Perez", "30", "Santiago Gurabo", "Tengo experiencia en casas de familia"],
        STEP_ASK_AVAILABILITY: ["hola", "Maria Perez", "30", "Santiago Gurabo", "Tengo experiencia en casas de familia", "limpiar cocinar y lavar"],
        STEP_ASK_REFERENCES: [
            "hola",
            "Maria Perez",
            "30",
            "Santiago Gurabo",
            "Tengo experiencia en casas de familia",
            "limpiar cocinar y lavar",
            "salida diaria",
        ],
    }
    for txt in warmup[step]:
        process_interview_inbound(conversation=conv, inbound_text=txt)
    return conv


def _flow(conv: BotConversation) -> dict:
    return dict((conv.metadata_json or {}).get(FLOW_KEY) or {})


def _assert_invalid_case(conv: BotConversation, out: dict, expected_step: str, invalid_answer: str, expected_reply_substring: str | tuple[str, ...]) -> None:
    flow = _flow(conv)
    assert out["advanced"] is False
    assert flow.get("current_step") == expected_step
    assert str(flow.get("last_invalid_answer") or "") == invalid_answer
    assert str(flow.get("validation_error") or "")
    reply_norm = _norm(str(out.get("reply") or ""))
    if isinstance(expected_reply_substring, tuple):
        assert any(_norm(x) in reply_norm for x in expected_reply_substring)
    else:
        assert _norm(expected_reply_substring) in reply_norm


def _assert_valid_case(conv: BotConversation, out: dict, expected_next_step: str, expected_reply_substring: str) -> None:
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == expected_next_step
    assert str(flow.get("validation_error") or "") == ""
    assert str(flow.get("last_invalid_answer") or "") == ""
    if expected_reply_substring:
        assert _norm(expected_reply_substring) in _norm(str(out.get("reply") or ""))


@pytest.mark.parametrize(
    "answer",
    ["que?", "q", "hola", "yo", "si", "ok", "maria solo nombre", "la domestica", "no tengo", "😀"],
)
def test_nombre_invalido_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_NAME)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_NAME, answer, "nombre completo")


@pytest.mark.parametrize(
    "answer",
    ["maria perez", "maría del carmen", "angel manuel del monte", "soy ana rodriguez"],
)
def test_nombre_valido_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_NAME)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_AGE, "edad")


@pytest.mark.parametrize("answer", ["treinta", "no se", "soy joven", "mayor de edad", "17", "80", "?"])
def test_edad_invalida_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_AGE)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_AGE, answer, "edad")


@pytest.mark.parametrize("answer", ["30", "30 años", "tengo 45"])
def test_edad_valida_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_AGE)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_CITY_SECTOR, "ciudad")


@pytest.mark.parametrize("answer", ["no tengo", "que?", "por ahi", "cerca", "no se", "en mi casa"])
def test_ciudad_invalida_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_CITY_SECTOR)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_CITY_SECTOR, answer, "ciudad")


@pytest.mark.parametrize("answer", ["santiago gurabo", "puerto plata centro", "vivo en cienfuegos", "soy de la vega"])
def test_ciudad_valida_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_CITY_SECTOR)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_EXPERIENCE, "experiencia")


@pytest.mark.parametrize("answer", ["que es eso", "ok", "si", "normal", "no se"])
def test_experiencia_invalida_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_EXPERIENCE)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_EXPERIENCE, answer, "experiencia")


@pytest.mark.parametrize(
    "answer",
    [
        "tengo experiencia en casas de familia",
        "he trabajado cuidando niños",
        "no tengo experiencia pero quiero aprender",
        "sé limpiar y cocinar aunque no he trabajado formal",
    ],
)
def test_experiencia_valida_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_EXPERIENCE)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_SKILLS, "sabes hacer")


@pytest.mark.parametrize(
    "answer,reply_hint",
    [
        ("de todo", "sabes hacer"),
        ("lo normal", "sabes hacer"),
        ("cualquier cosa", "sabes hacer"),
        ("si", "funciones"),
        ("ok", "funciones"),
    ],
)
def test_skills_invalidas_no_avanza(answer, reply_hint):
    conv = _conversation_at_step(STEP_ASK_SKILLS)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_SKILLS, answer, reply_hint)


@pytest.mark.parametrize("answer", ["limpiar cocinar y lavar", "sé cuidar niños", "planchar y cocinar", "cuidado de envejecientes"])
def test_skills_validas_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_SKILLS)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_AVAILABILITY, "disponibilidad")


@pytest.mark.parametrize("answer", ["cuando sea", "normal", "si", "mañana", "no se"])
def test_disponibilidad_invalida_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_AVAILABILITY)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_AVAILABILITY, answer, "disponibilidad")


@pytest.mark.parametrize("answer", ["salida diaria", "con dormida", "puedo ambas", "por día", "fines de semana"])
def test_disponibilidad_valida_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_AVAILABILITY)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, STEP_ASK_REFERENCES, "referencias")


@pytest.mark.parametrize("answer", ["necesito esto?", "no", "ninguna", "ok", "después", "si"])
def test_referencias_invalidas_no_avanza(answer):
    conv = _conversation_at_step(STEP_ASK_REFERENCES)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_invalid_case(conv, out, STEP_ASK_REFERENCES, answer, "referencia")


@pytest.mark.parametrize(
    "answer",
    ["te las envío luego", "puedo conseguir referencias", "mi jefa ana 8091234567", "referencia familiar pedro 8295551111"],
)
def test_referencias_validas_completan(answer):
    conv = _conversation_at_step(STEP_ASK_REFERENCES)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == "completed"
    assert flow.get("completed") is True


def test_respuesta_mezclada_nombre_solo_guarda_nombre_y_no_salta_edad():
    conv = _conversation_at_step(STEP_ASK_NAME)
    out = process_interview_inbound(conversation=conv, inbound_text="me llamo maria perez tengo 30")
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == STEP_ASK_AGE
    assert "30" not in str((flow.get("collected_data") or {}).get("full_name") or "")
    assert (flow.get("collected_data") or {}).get("age") is None
    assert int(((flow.get("detected_future_data") or {}).get("age") or {}).get("value") or 0) == 30


def test_respuesta_mezclada_edad_solo_guarda_edad_y_no_salta_ciudad():
    conv = _conversation_at_step(STEP_ASK_AGE)
    out = process_interview_inbound(conversation=conv, inbound_text="tengo 30 y vivo en gurabo")
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == STEP_ASK_CITY_SECTOR
    assert (flow.get("collected_data") or {}).get("age") == 30
    assert (flow.get("collected_data") or {}).get("city_sector") is None
    assert "gurabo" in str(((flow.get("detected_future_data") or {}).get("city_sector") or {}).get("value") or "").lower()


def test_respuesta_mezclada_ciudad_solo_guarda_ciudad_y_pasa_a_experiencia():
    conv = _conversation_at_step(STEP_ASK_CITY_SECTOR)
    out = process_interview_inbound(conversation=conv, inbound_text="vivo en santiago gurabo y sé cocinar")
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == STEP_ASK_EXPERIENCE
    assert "santiago" in str((flow.get("collected_data") or {}).get("city_sector") or "").lower()


def test_respuesta_mezclada_skills_no_salta_disponibilidad():
    conv = _conversation_at_step(STEP_ASK_SKILLS)
    out = process_interview_inbound(conversation=conv, inbound_text="limpio cocino y quiero salida diaria")
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == STEP_ASK_AVAILABILITY
    assert (flow.get("collected_data") or {}).get("availability") is None
    assert str(((flow.get("detected_future_data") or {}).get("availability") or {}).get("value") or "") == "salida diaria"


def test_confirma_dato_futuro_antes_de_usarlo():
    conv = _conversation_at_step(STEP_ASK_NAME)
    process_interview_inbound(conversation=conv, inbound_text="me llamo maria perez tengo 30")
    out = process_interview_inbound(conversation=conv, inbound_text="si")
    flow = _flow(conv)
    assert out["advanced"] is True
    assert flow.get("current_step") == STEP_ASK_CITY_SECTOR
    assert int((flow.get("collected_data") or {}).get("age") or 0) == 30


@pytest.mark.parametrize(
    "step,answer,next_step",
    [
        (STEP_ASK_NAME, "maria peres", STEP_ASK_AGE),
        (STEP_ASK_AGE, "tengo 30 ano", STEP_ASK_CITY_SECTOR),
        (STEP_ASK_CITY_SECTOR, "satiago guravo", STEP_ASK_EXPERIENCE),
        (STEP_ASK_SKILLS, "yo limpio cosino labo", STEP_ASK_AVAILABILITY),
        (STEP_ASK_AVAILABILITY, "salía diaria", STEP_ASK_REFERENCES),
        (STEP_ASK_AVAILABILITY, "con domida", STEP_ASK_REFERENCES),
    ],
)
def test_acepta_respuestas_entendibles_aun_con_faltas(step, answer, next_step):
    conv = _conversation_at_step(step)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    _assert_valid_case(conv, out, next_step, "")


@pytest.mark.parametrize(
    "step,answer,redirect_hint",
    [
        (STEP_ASK_NAME, "que?", "nombre completo"),
        (STEP_ASK_AGE, "hola", "edad"),
        (STEP_ASK_CITY_SECTOR, "hola", "ciudad"),
        (STEP_ASK_SKILLS, "hola", "funciones"),
        (STEP_ASK_AVAILABILITY, "hola", "disponibilidad"),
        (STEP_ASK_REFERENCES, "hola", "referencias"),
    ],
)
def test_redireccion_natural_en_invalidos(step, answer, redirect_hint):
    conv = _conversation_at_step(step)
    out = process_interview_inbound(conversation=conv, inbound_text=answer)
    flow = _flow(conv)
    assert out["advanced"] is False
    assert flow.get("current_step") == step
    assert str(flow.get("validation_error") or "")
    assert redirect_hint in str(out.get("reply") or "").lower()
