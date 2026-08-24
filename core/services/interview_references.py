from __future__ import annotations

from typing import Any


INTERVIEW_REFERENCE_QUESTION_KEYS = frozenset(
    {
        "domestica.referencia_laboral",
        "domestica.referencia_familiar",
        "enfermera.referencia_laboral",
        "enfermera.referencia_familiar",
        "empleo_general.referencia_laboral",
        "empleo_general.referencia_familiar",
    }
)


def is_interview_reference_question(pregunta: Any) -> bool:
    clave = str(getattr(pregunta, "clave", "") or "").strip().lower()
    return clave in INTERVIEW_REFERENCE_QUESTION_KEYS
