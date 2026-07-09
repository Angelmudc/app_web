from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.bot_protocol_service import load_protocol

REPORT_PATH = Path("logs/bot_suggested_replies_audit_report.json")

AUDIT_STAGES = [
    "WELCOME",
    "PERSONAL_CONFIRMATION",
    "BASIC_INFO",
    "ADDRESS",
    "WORK_TYPE",
    "TRANSPORT_ROUTE",
    "PREVIOUS_AGENCY",
    "PERCENTAGE_ACCEPTANCE",
    "LABOR_REFERENCES",
    "FAMILY_REFERENCES",
    "SKILLS",
    "OFFICE_INFO",
    "GROUP_SELECTION",
    "GROUP_WARNING",
    "DOCUMENT_REQUEST",
    "PROFILE_PHOTO",
]

TECHNICAL_WORDS = {
    "validación",
    "protocolo",
    "expediente",
    "captación",
    "canal oficial",
    "incompatible",
    "schema",
}

ROBOTIC_PHRASES = {
    "proceso por etapas",
    "confirmacion_info",
    "confirmacion_documentos",
    "fallback",
}

PROHIBITED_PROMISES = {
    "te conseguiremos empleo",
    "estás aprobada",
    "ya estás inscrita",
}

UNPROFESSIONAL_PHRASES = {
    "apurate",
    "apúrate",
    "callate",
    "cállate",
    "de una vez",
}

FORMAL_WORDS = {
    "procederemos",
    "validaremos",
    "gestión",
    "expediente",
    "captación",
}

MAX_MESSAGE_CHARS = 220
MAX_QUESTIONS_PER_STAGE = 1

LEGACY_STAGE_REPLIES = {
    "WELCOME": [
        "Hola, gracias por comunicarte con Agencia Doméstica del Cibao A&D.",
        "Este proceso es para tu captación como doméstica y se realiza por etapas.",
        "Trabajamos Santiago y Puerto Plata.",
        "Horario de oficina: Lunes a Viernes de 8:00 AM a 5:00 PM.",
        "Si estás de acuerdo, responde: LISTA para continuar.",
    ],
    "BASIC_INFO": [
        "Indica tu nombre completo y edad.",
        "Si tienes la cédula disponible, puedes compartirla para agilizar revisión manual.",
        "Si prefieres no enviar cédula en este momento, un agente humano puede continuar la validación de forma segura.",
        "Comparte nombre completo y edad. Si no deseas enviar cédula aún, solicita revisión humana.",
    ],
    "PERCENTAGE_ACCEPTANCE": [
        "¿Aceptas el porcentaje de agencia del 25% del primer sueldo?",
        "Responde SI o NO para continuar.",
        "Sin aceptación del 25% no se puede completar el registro de captación.",
        "Necesito una respuesta directa: SI acepto 25% o NO acepto.",
    ],
    "FAMILY_REFERENCES": [
        "Comparte 2 referencias familiares con nombre, parentesco y teléfono.",
        "Formato sugerido: Nombre - Parentesco - Teléfono.",
        "No enviar referencias vacías o repetidas.",
        "Necesito referencias familiares válidas con nombre y teléfono.",
    ],
    "SKILLS": [
        "Indica qué sabes hacer: limpieza, cocina, niños, envejecientes, lavado, planchado u otras habilidades.",
        "Incluye años de experiencia si puedes.",
        "Cuéntanos al menos 2 habilidades principales.",
    ],
    "GROUP_WARNING": [
        "Importante: aplicar a grupos fuera de tu experiencia puede limitar colocación rápida.",
        "Te recomendamos seleccionar solo grupos que realmente dominas.",
        "Si eliges grupos incompatibles se marca revisión manual.",
        "Confirma con OK GRUPOS para continuar.",
    ],
    "DOCUMENT_REQUEST": [
        "Debes enviar foto de cédula por ambos lados y demás documentos solicitados.",
        "La cédula debe verse legible.",
        "Sin cédula no se finaliza expediente.",
        "No compartas documentos fuera de los canales oficiales del equipo.",
        "Confirma: ENVIARÉ CÉDULA para pasar al siguiente paso.",
    ],
    "PROFILE_PHOTO": [
        "Finalmente, envía una foto de perfil reciente y clara.",
        "Debe verse el rostro completo, sin filtros fuertes.",
        "Sin foto de perfil no pasa a lista para trabajar.",
        "Si tienes dudas de privacidad, solicita revisión humana antes de enviar la foto.",
        "Confirma con ENVIARÉ FOTO DE PERFIL.",
    ],
}


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _collect_messages(step: dict[str, Any]) -> list[str]:
    messages = step.get("messages") or {}
    out: list[str] = []
    for key in ("primary", "secondary", "warnings"):
        for item in messages.get(key) or []:
            msg = str(item or "").strip()
            if msg:
                out.append(msg)
    fallback = str(step.get("fallback") or "").strip()
    if fallback:
        out.append(fallback)
    return out


def _too_long_issues(messages: list[str]) -> list[str]:
    issues: list[str] = []
    if any(len(msg) > MAX_MESSAGE_CHARS for msg in messages):
        issues.append("Respuesta demasiado larga")
    if sum(len(msg) for msg in messages) > 500:
        issues.append("Falta de claridad")
    joined = " ".join(messages)
    for sentence in [s.strip() for s in joined.replace("\n", " ").split(".") if s.strip()]:
        if len(sentence) > 150:
            issues.append("Falta de claridad")
            break
    return issues


def _check_stage(step: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    suggestions: list[str] = []

    step_code = str(step.get("step_code") or "").upper()
    messages = step.get("messages") or {}
    primary = [str(x).strip() for x in (messages.get("primary") or []) if str(x).strip()]
    secondary = [str(x).strip() for x in (messages.get("secondary") or []) if str(x).strip()]
    warnings = [str(x).strip() for x in (messages.get("warnings") or []) if str(x).strip()]
    collected = _collect_messages(step)
    all_text = "\n".join(collected)
    norm = _normalize(all_text)

    issues.extend(_too_long_issues(collected))

    if any(word in norm for word in TECHNICAL_WORDS):
        issues.append("Lenguaje muy técnico")
        suggestions.append("Simplificar términos técnicos a lenguaje cotidiano")

    if any(phrase in norm for phrase in ROBOTIC_PHRASES):
        issues.append("Frases raras o robóticas")
        suggestions.append("Usar frases más naturales del habla dominicana profesional")

    question_count = sum(msg.count("?") for msg in primary + secondary)
    if question_count > MAX_QUESTIONS_PER_STAGE:
        issues.append("Pregunta con demasiadas cosas a la vez")
        suggestions.append("Limitar a una sola pregunta directa por etapa")
    elif any(q.count(" y ") >= 2 or q.count(",") >= 2 for q in primary):
        issues.append("Pregunta con demasiadas cosas a la vez")
        suggestions.append("Dividir la pregunta en pasos más simples")

    if any(p in norm for p in PROHIBITED_PROMISES):
        issues.append("Promesas peligrosas")
        suggestions.append("Eliminar promesas de empleo/aprobación/inscripción")

    sensitive_step = step_code in {"BASIC_INFO", "DOCUMENT_REQUEST", "PROFILE_PHOTO"}
    mentions_sensitive = any(x in norm for x in ("cedula", "cédula", "document", "foto"))
    if sensitive_step and mentions_sensitive and not warnings:
        issues.append("Petición sensible sin advertencia")
        suggestions.append("Agregar advertencia de privacidad y revisión humana")

    if any(p in norm for p in UNPROFESSIONAL_PHRASES):
        issues.append("Tono poco profesional")
        suggestions.append("Mantener tono respetuoso y profesional")

    formal_hits = sum(1 for w in FORMAL_WORDS if w in norm)
    if formal_hits >= 2:
        issues.append("Lenguaje demasiado formal")
        suggestions.append("Usar palabras simples y cercanas")

    if step_code in {"PERSONAL_CONFIRMATION", "PREVIOUS_AGENCY", "PERCENTAGE_ACCEPTANCE"}:
        if not any("responde" in _normalize(x) for x in secondary + [str(step.get("fallback") or "")]):
            issues.append("Falta de instrucciones simples")
            suggestions.append("Añadir instrucción directa tipo 'Responde SI o NO'")

    if step_code == "PERCENTAGE_ACCEPTANCE":
        if "25" not in norm or not any(w in norm for w in ("primer sueldo", "agencia", "aceptas")):
            issues.append("No explicar el 25% de forma clara")
            suggestions.append("Explicar de forma explícita el 25% del primer sueldo")

    if not primary:
        issues.append("Falta de claridad")
        suggestions.append("Agregar mensaje principal claro y corto")

    unique_issues: list[str] = []
    for issue in issues:
        if issue not in unique_issues:
            unique_issues.append(issue)

    unique_suggestions: list[str] = []
    for suggestion in suggestions:
        if suggestion not in unique_suggestions:
            unique_suggestions.append(suggestion)

    return unique_issues, unique_suggestions


def _score_from_warning_count(warnings_count: int, total: int) -> float:
    return round(((total - warnings_count) / total) * 100, 2) if total else 0.0


def _build_legacy_stage(stage: str, current_step: dict[str, Any]) -> dict[str, Any]:
    old = LEGACY_STAGE_REPLIES.get(stage)
    if not old:
        return current_step
    return {
        **current_step,
        "messages": {
            "primary": old[:2],
            "secondary": old[2:3],
            "warnings": old[3:-1],
        },
        "fallback": old[-1],
    }


def build_audit_report(protocol: dict[str, Any]) -> dict[str, Any]:
    steps_by_code = {
        str(step.get("step_code") or "").upper(): step
        for step in (protocol.get("steps") or [])
        if isinstance(step, dict)
    }

    problems_by_stage: dict[str, list[str]] = {}
    suggestions_by_stage: dict[str, list[str]] = {}
    before_after_by_stage: dict[str, dict[str, Any]] = {}
    ok_stages: list[str] = []
    warning_stages: list[str] = []
    before_warning_count = 0

    for stage in AUDIT_STAGES:
        step = steps_by_code.get(stage)
        if not step:
            problems_by_stage[stage] = ["Etapa no encontrada en protocolo"]
            suggestions_by_stage[stage] = ["Agregar etapa al protocolo o ajustar auditoría"]
            warning_stages.append(stage)
            continue

        issues, suggestions = _check_stage(step)
        legacy_step = _build_legacy_stage(stage, step)
        old_issues, _ = _check_stage(legacy_step)
        if old_issues:
            before_warning_count += 1

        before_after_by_stage[stage] = {
            "old_reply": "\n".join(_collect_messages(legacy_step)),
            "new_reply": "\n".join(_collect_messages(step)),
            "warnings_before": old_issues,
            "warnings_after": issues,
            "warnings_resueltos": [x for x in old_issues if x not in issues],
        }

        if issues:
            warning_stages.append(stage)
            problems_by_stage[stage] = issues
            suggestions_by_stage[stage] = suggestions or ["Revisar redacción para claridad y tono"]
        else:
            ok_stages.append(stage)

    total = len(AUDIT_STAGES)
    warnings_count = len(warning_stages)
    score = _score_from_warning_count(warnings_count, total)
    score_before = _score_from_warning_count(before_warning_count, total)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_stages_audited": total,
        "stages_ok": ok_stages,
        "stages_with_warnings": warning_stages,
        "problems_by_stage": problems_by_stage,
        "improvement_suggestions_by_stage": suggestions_by_stage,
        "overall_score": score,
        "score_before": score_before,
        "score_after": score,
        "before_after_by_stage": before_after_by_stage,
    }


def run_audit(report_path: Path = REPORT_PATH) -> dict[str, Any]:
    protocol = load_protocol()
    report = build_audit_report(protocol)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    report = run_audit()
    print("BOT_SUGGESTED_REPLIES_AUDIT: OK")
    print(f"report={REPORT_PATH}")
    print(f"total_stages_audited={report['total_stages_audited']}")
    print(f"stages_ok={len(report['stages_ok'])}")
    print(f"stages_with_warnings={len(report['stages_with_warnings'])}")
    print(f"overall_score={report['overall_score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
