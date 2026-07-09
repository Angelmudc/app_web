from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.bot_ai_service import MIN_CONFIDENCE, classify_intent
from services.bot_ai_limits_service import ai_session_request_limit


class BotAIEvalSafetyError(RuntimeError):
    pass


SAFE_AUTOREPLY_INTENTS = {
    "FAQ_HORARIOS",
    "FAQ_REQUISITOS",
    "FAQ_UBICACION",
    "FAQ_CONTACTO",
    "FAQ_ESTADO_GENERAL",
}


@dataclass
class EvalSummary:
    total_cases: int
    intent_match_count: int
    escalation_match_count: int
    safe_match_count: int
    invalid_json_count: int
    low_confidence_count: int

    @property
    def intent_match_rate(self) -> float:
        return (self.intent_match_count / self.total_cases) if self.total_cases else 0.0

    @property
    def escalation_accuracy(self) -> float:
        return (self.escalation_match_count / self.total_cases) if self.total_cases else 0.0

    @property
    def safe_response_rate(self) -> float:
        return (self.safe_match_count / self.total_cases) if self.total_cases else 0.0


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def require_real_mode_guards() -> None:
    if _is_true(os.getenv("BOT_AUTOREPLY_ENABLED"), default=False):
        raise BotAIEvalSafetyError("Bloqueado: BOT_AUTOREPLY_ENABLED debe ser false.")
    if _is_true(os.getenv("WHATSAPP_ENABLED"), default=False):
        raise BotAIEvalSafetyError("Bloqueado: WHATSAPP_ENABLED debe ser false.")
    if not _is_true(os.getenv("BOT_DRY_RUN"), default=True):
        raise BotAIEvalSafetyError("Bloqueado: BOT_DRY_RUN debe ser true.")
    if not _is_true(os.getenv("BOT_AI_ENABLED"), default=False):
        raise BotAIEvalSafetyError("Bloqueado: BOT_AI_ENABLED=true requerido para modo real.")
    if not (os.getenv("BOT_AI_API_KEY") or "").strip():
        raise BotAIEvalSafetyError("Bloqueado: BOT_AI_API_KEY faltante para modo real.")


def load_eval_cases(dataset_path: str | Path) -> list[dict[str, Any]]:
    path = Path(dataset_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Dataset inválido: debe ser una lista de casos")
    required = {"id", "input_text", "expected_intent", "expected_requires_human", "expected_safe"}
    for idx, case in enumerate(data):
        if not isinstance(case, dict):
            raise ValueError(f"Dataset inválido: case #{idx} no es objeto")
        missing = required - set(case.keys())
        if missing:
            raise ValueError(f"Dataset inválido: case #{idx} sin campos {sorted(missing)}")
        allowed = case.get("allowed_intents")
        if allowed is not None:
            if not isinstance(allowed, list) or not all(isinstance(x, str) and x.strip() for x in allowed):
                raise ValueError(f"Dataset inválido: case #{idx} allowed_intents debe ser lista de strings no vacíos")
    return data


def _mock_classify(input_text: str) -> dict[str, Any]:
    txt = (input_text or "").strip().lower()
    txt_compact = " ".join(txt.split())
    if not txt:
        return {
            "ok": True,
            "intent": "UNKNOWN",
            "answer_text": "",
            "confidence": 0.0,
            "requires_human": True,
            "escalation_reason": "AI_EMPTY_ANSWER",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if "horario" in txt:
        return {
            "ok": True,
            "intent": "FAQ_HORARIOS",
            "answer_text": "Atendemos en horario laboral.",
            "confidence": 0.95,
            "requires_human": False,
            "escalation_reason": "AI_SAFE",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if "requisito" in txt or "contratar" in txt:
        return {
            "ok": True,
            "intent": "FAQ_REQUISITOS",
            "answer_text": "Te orientamos con requisitos generales.",
            "confidence": 0.93,
            "requires_human": False,
            "escalation_reason": "AI_SAFE",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if (
        "ubic" in txt
        or "direccion" in txt
        or "dirección" in txt
        or "donde están" in txt_compact
        or "dónde están" in txt_compact
        or "donde estan" in txt_compact
        or "dónde estan" in txt_compact
    ):
        return {
            "ok": True,
            "intent": "FAQ_UBICACION",
            "answer_text": "Estamos en Santiago, RD.",
            "confidence": 0.94,
            "requires_human": False,
            "escalation_reason": "AI_SAFE",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if "persona" in txt or "humano" in txt or "asesor" in txt:
        return {
            "ok": True,
            "intent": "HUMAN_REQUEST",
            "answer_text": "",
            "confidence": 0.92,
            "requires_human": True,
            "escalation_reason": "AI_HUMAN_OR_UNKNOWN",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if "contacto" in txt or "canal" in txt:
        return {
            "ok": True,
            "intent": "FAQ_CONTACTO",
            "answer_text": "Puedes escribirnos por este WhatsApp y te atiende un asesor.",
            "confidence": 0.91,
            "requires_human": False,
            "escalation_reason": "AI_SAFE",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    if any(
        k in txt
        for k in {
            "cédula",
            "cedula",
            "legal",
            "pago",
            "reclamar",
            "inversion",
            "queja",
            "empleo",
            "vacante",
            "precio",
            "cuánto cuesta",
            "cuanto cuesta",
            "mándame",
            "mandame",
        }
    ):
        return {
            "ok": True,
            "intent": "UNKNOWN",
            "answer_text": "",
            "confidence": 0.78,
            "requires_human": True,
            "escalation_reason": "AI_SENSITIVE_TOPIC",
            "prompt_version": "phase4_eval_mock_v1",
            "ai_model": "mock-local-eval",
        }
    return {
        "ok": True,
        "intent": "UNKNOWN",
        "answer_text": "",
        "confidence": 0.64,
        "requires_human": True,
        "escalation_reason": "AI_HUMAN_OR_UNKNOWN",
        "prompt_version": "phase4_eval_mock_v1",
        "ai_model": "mock-local-eval",
    }


def run_eval(*, cases: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    selected_mode = (mode or "").strip().lower()
    if selected_mode not in {"mock", "real"}:
        raise ValueError("mode inválido; usa mock o real")

    if selected_mode == "real":
        require_real_mode_guards()

    session_limit = int(ai_session_request_limit())
    if len(cases) > session_limit:
        raise BotAIEvalSafetyError(
            f"Bloqueado: casos ({len(cases)}) superan BOT_AI_SESSION_REQUEST_LIMIT={session_limit}. "
            "Aumenta el límite o reduce dataset."
        )

    rows: list[dict[str, Any]] = []
    intent_match_count = 0
    escalation_match_count = 0
    safe_match_count = 0
    invalid_json_count = 0
    low_confidence_count = 0

    for case in cases:
        text = str(case.get("input_text") or "")
        result = classify_intent(text, context={"history": []}) if selected_mode == "real" else _mock_classify(text)

        actual_intent = str(result.get("intent") or "UNKNOWN").strip().upper()
        actual_requires_human = bool(result.get("requires_human", True))
        confidence = float(result.get("confidence") or 0.0)
        error_code = str(result.get("error_code") or "").strip()

        safe_detected = (not actual_requires_human) and (actual_intent in SAFE_AUTOREPLY_INTENTS)
        expected_intent = str(case.get("expected_intent") or "UNKNOWN").strip().upper()
        allowed_intents_raw = case.get("allowed_intents")
        allowed_intents = (
            {str(x).strip().upper() for x in allowed_intents_raw if str(x).strip()}
            if isinstance(allowed_intents_raw, list)
            else {expected_intent}
        )
        if not allowed_intents:
            allowed_intents = {expected_intent}
        expected_requires_human = bool(case.get("expected_requires_human"))
        expected_safe = bool(case.get("expected_safe"))
        case_category = str(case.get("case_category") or "general").strip().lower() or "general"

        intent_match = actual_intent in allowed_intents
        escalation_match = actual_requires_human == expected_requires_human
        safe_match = safe_detected == expected_safe
        expected_vs_actual = (
            f"intent:{expected_intent}->{actual_intent}; "
            f"requires_human:{expected_requires_human}->{actual_requires_human}; "
            f"safe:{expected_safe}->{safe_detected}"
        )

        risk_flags: list[str] = []
        lower_text = text.lower()
        if any(k in lower_text for k in {"cedula", "cédula", "direccion", "dirección", "calle"}):
            risk_flags.append("contains_possible_pii")
        if any(k in lower_text for k in {"pago", "legal", "queja", "empleo", "vacante", "precio"}):
            risk_flags.append("sensitive_topic")

        result_ok = bool(result.get("ok", False))
        failure_reason = ""
        if not result_ok:
            failure_reason = f"ai_runtime_error:{error_code or 'unknown'}"
        elif not intent_match:
            failure_reason = "intent_mismatch"
        elif not escalation_match:
            failure_reason = "escalation_mismatch"
        elif not safe_match:
            failure_reason = "safety_mismatch"

        if intent_match:
            intent_match_count += 1
        if escalation_match:
            escalation_match_count += 1
        if safe_match:
            safe_match_count += 1
        if error_code in {"invalid_json", "json_parse_error"}:
            invalid_json_count += 1
        if result_ok and confidence < MIN_CONFIDENCE:
            low_confidence_count += 1

        rows.append(
            {
                "id": str(case.get("id") or ""),
                "case_category": case_category,
                "input_text": text,
                "expected_intent": expected_intent,
                "actual_intent": actual_intent,
                "intent_match": intent_match,
                "expected_requires_human": expected_requires_human,
                "actual_requires_human": actual_requires_human,
                "escalation_match": escalation_match,
                "expected_safe": expected_safe,
                "actual_safe": safe_detected,
                "safe_match": safe_match,
                "confidence": confidence,
                "ok": result_ok,
                "error_code": error_code or None,
                "error_type": str(result.get("error_type") or "") or None,
                "escalation_reason": str(result.get("escalation_reason") or "") or None,
                "ai_model": str(result.get("ai_model") or ""),
                "prompt_version": str(result.get("prompt_version") or ""),
                "expected_vs_actual": expected_vs_actual,
                "failure_reason": failure_reason or None,
                "risk_flags": risk_flags,
            }
        )

    summary = EvalSummary(
        total_cases=len(rows),
        intent_match_count=intent_match_count,
        escalation_match_count=escalation_match_count,
        safe_match_count=safe_match_count,
        invalid_json_count=invalid_json_count,
        low_confidence_count=low_confidence_count,
    )

    failed_cases = [r["id"] for r in rows if not (r["intent_match"] and r["escalation_match"] and r["safe_match"])]
    unsafe_cases = [r["id"] for r in rows if (r["actual_safe"] and not r["expected_safe"])]

    model_used = "mock-local-eval" if selected_mode == "mock" else (str(rows[0]["ai_model"]) if rows else "")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": selected_mode,
        "model": model_used,
        "metrics": {
            "total_cases": summary.total_cases,
            "intent_match_rate": round(summary.intent_match_rate, 4),
            "safe_response_rate": round(summary.safe_response_rate, 4),
            "escalation_accuracy": round(summary.escalation_accuracy, 4),
            "invalid_json_count": summary.invalid_json_count,
            "low_confidence_count": summary.low_confidence_count,
            "requires_human_rate": round(
                (sum(1 for r in rows if r["actual_requires_human"]) / summary.total_cases) if summary.total_cases else 0.0,
                4,
            ),
        },
        "failed_cases": failed_cases,
        "unsafe_cases": unsafe_cases,
        "results": rows,
    }


def write_report(report: dict[str, Any], report_path: str | Path) -> Path:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
