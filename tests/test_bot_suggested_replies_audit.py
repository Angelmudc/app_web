from __future__ import annotations

import json
from pathlib import Path

from scripts.local.audit_bot_suggested_replies import (
    AUDIT_STAGES,
    MAX_MESSAGE_CHARS,
    MAX_QUESTIONS_PER_STAGE,
    _check_stage,
    build_audit_report,
    run_audit,
)
from services.bot_protocol_service import load_protocol


def _step(step_code: str, primary: list[str], secondary: list[str] | None = None, warnings: list[str] | None = None) -> dict:
    return {
        "step_code": step_code,
        "messages": {
            "primary": primary,
            "secondary": secondary or [],
            "warnings": warnings or [],
        },
        "fallback": "Responde SI o NO.",
        "expected_answers": [],
        "validations": [],
    }


def test_script_loads_protocol() -> None:
    protocol = load_protocol()
    assert isinstance(protocol, dict)
    assert isinstance(protocol.get("steps"), list)


def test_detects_long_reply() -> None:
    long_text = "A" * 260
    issues, _ = _check_stage(_step("WELCOME", [long_text]))
    assert "Respuesta demasiado larga" in issues


def test_detects_prohibited_promises() -> None:
    issues, _ = _check_stage(_step("WELCOME", ["Te conseguiremos empleo muy pronto."]))
    assert "Promesas peligrosas" in issues


def test_detects_sensitive_without_warning() -> None:
    issues, _ = _check_stage(_step("DOCUMENT_REQUEST", ["Envía tu cédula y foto ahora."], warnings=[]))
    assert "Petición sensible sin advertencia" in issues


def test_generates_report_json(tmp_path: Path) -> None:
    output = tmp_path / "audit_report.json"
    report = run_audit(output)

    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_stages_audited"] == len(AUDIT_STAGES)
    assert "overall_score" in payload
    assert payload["overall_score"] == report["overall_score"]


def test_build_audit_report_structure() -> None:
    protocol = {
        "steps": [
            _step("WELCOME", ["Hola"]),
            _step("PERSONAL_CONFIRMATION", ["¿Eres tú?"], ["Responde SI o NO."]),
        ]
    }
    report = build_audit_report(protocol)
    assert "total_stages_audited" in report
    assert "stages_with_warnings" in report
    assert "problems_by_stage" in report
    assert "score_before" in report
    assert "score_after" in report
    assert "before_after_by_stage" in report


def test_protocol_messages_do_not_exceed_recommended_length() -> None:
    protocol = load_protocol()
    for step in protocol.get("steps") or []:
        messages = (step.get("messages") or {})
        for key in ("primary", "secondary", "warnings"):
            for msg in messages.get(key) or []:
                assert len(str(msg)) <= MAX_MESSAGE_CHARS


def test_protocol_has_no_prohibited_promises() -> None:
    protocol = load_protocol()
    joined = []
    for step in protocol.get("steps") or []:
        messages = step.get("messages") or {}
        for key in ("primary", "secondary", "warnings"):
            joined.extend(str(x).lower() for x in (messages.get(key) or []))
        joined.append(str(step.get("fallback") or "").lower())
    text = "\n".join(joined)
    assert "te conseguiremos empleo" not in text
    assert "estás aprobada" not in text
    assert "ya estás inscrita" not in text


def test_max_question_count_for_rewritten_stages() -> None:
    protocol = load_protocol()
    rewritten = {
        "WELCOME",
        "BASIC_INFO",
        "PERCENTAGE_ACCEPTANCE",
        "FAMILY_REFERENCES",
        "SKILLS",
        "GROUP_WARNING",
        "DOCUMENT_REQUEST",
        "PROFILE_PHOTO",
    }
    for step in protocol.get("steps") or []:
        code = str(step.get("step_code") or "").upper()
        if code not in rewritten:
            continue
        messages = step.get("messages") or {}
        text_parts = [str(x) for k in ("primary", "secondary", "warnings") for x in (messages.get(k) or [])]
        qcount = sum(t.count("?") for t in text_parts)
        assert qcount <= MAX_QUESTIONS_PER_STAGE


def test_sensitive_stages_keep_warnings() -> None:
    protocol = load_protocol()
    by_code = {str(s.get("step_code") or "").upper(): s for s in (protocol.get("steps") or [])}
    for code in ("BASIC_INFO", "DOCUMENT_REQUEST", "PROFILE_PHOTO"):
        warnings = ((by_code.get(code) or {}).get("messages") or {}).get("warnings") or []
        assert len(warnings) > 0
