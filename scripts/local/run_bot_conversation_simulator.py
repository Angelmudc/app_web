from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_candidate_conversion_preview_service import build_candidate_conversion_preview
from services.bot_candidate_draft_service import can_create_candidate_draft, create_candidate_draft, get_candidate_draft
from services.bot_conversation_service import get_protocol_state, set_current_step
from services.bot_inbound_pipeline_service import process_inbound_ai_pipeline
from services.bot_message_service import create_manual_message
from services.environment_guard_service import is_safe_local_database

DATASET_PATH = Path("data/bot_local_conversation_scenarios.json")
REPORT_PATH = Path("logs/bot_conversation_simulator_report.json")

_ALLOWED_ENVS = {"local", "development", "test", "testing"}
_TRUE_SET = {"1", "true", "yes", "on"}


class GuardrailError(RuntimeError):
    pass


@dataclass
class RunOptions:
    scenario_id: str | None = None
    max_scenarios: int | None = None
    verbose: bool = False
    allow_ai: bool = False
    allow_real_create_local: bool = False


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_SET


def _current_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()


def enforce_guardrails(*, allow_ai: bool, allow_real_create_local: bool) -> None:
    env = _current_env()
    if env not in _ALLOWED_ENVS:
        raise GuardrailError("APP_ENV must be local/development/testing")
    if not is_safe_local_database():
        raise GuardrailError("DB must be local (sqlite/localhost)")
    if _is_true(os.getenv("WHATSAPP_ENABLED")):
        raise GuardrailError("WHATSAPP_ENABLED=true is not allowed")
    if _is_true(os.getenv("BOT_AUTOREPLY_ENABLED")):
        raise GuardrailError("BOT_AUTOREPLY_ENABLED=true is not allowed")
    if _is_true(os.getenv("BOT_AI_ENABLED")) and not allow_ai:
        raise GuardrailError("BOT_AI_ENABLED=true requires --allow-ai")
    if _is_true(os.getenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL")) and not allow_real_create_local:
        raise GuardrailError("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true requires --allow-real-create-local")


def load_scenarios(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Dataset inválido: scenarios vacío")
    seen: set[str] = set()
    for row in scenarios:
        sid = str(row.get("id") or "").strip()
        if not sid:
            raise ValueError("Dataset inválido: escenario sin id")
        if sid in seen:
            raise ValueError(f"Dataset inválido: id duplicado {sid}")
        seen.add(sid)
        if not isinstance(row.get("messages"), list) or not row.get("messages"):
            raise ValueError(f"Dataset inválido: escenario {sid} sin messages")
    return scenarios


def _ensure_bot_tables() -> None:
    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)


def _make_conversation(scenario: dict[str, Any], scenario_index: int) -> BotConversation:
    metadata_seed = deepcopy(dict(scenario.get("metadata_seed") or {}))
    metadata_seed.setdefault("protocol_version", "domesticas_v1")
    metadata_seed.setdefault("protocol_entities", {})
    conv = BotConversation(
        channel="whatsapp",
        phone_e164=f"+1809555{scenario_index:05d}",
        contact_name=f"Sim {scenario.get('id')}",
        status="open",
        metadata_json=metadata_seed,
    )
    db.session.add(conv)
    db.session.commit()
    set_current_step(conv, current_step_code=str(scenario.get("initial_step") or "WELCOME"), autocommit=True)
    return conv


def _normalize_step(step: str | None) -> str:
    return str(step or "").strip().upper()


def _compare_expected_entities(expected: dict[str, Any], got: dict[str, Any], *, failure_prefix: str) -> tuple[int, int, list[str]]:
    total = 0
    matched = 0
    failures: list[str] = []
    for key, exp_value in expected.items():
        total += 1
        got_value = got.get(key)
        if str(got_value) == str(exp_value):
            matched += 1
        else:
            failures.append(f"{failure_prefix}:{key}:expected={exp_value}:got={got_value}")
    return matched, total, failures


def run_single_scenario(scenario: dict[str, Any], *, scenario_index: int, verbose: bool = False) -> dict[str, Any]:
    _ensure_bot_tables()
    conv = _make_conversation(scenario, scenario_index)
    try:
        before_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
    except OperationalError:
        before_candidates = 0
    per_message: list[dict[str, Any]] = []
    auto_advances = 0
    blocked = 0
    out_of_step_hits = 0
    corrections: list[str] = []

    previous_auto = os.getenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED")
    os.environ["BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"] = "true"
    try:
        for raw_message in scenario.get("messages") or []:
            before = get_protocol_state(conv)
            inbound = create_manual_message(
                conversation=conv,
                text_body=str(raw_message),
                direction="inbound",
                source="whatsapp_user",
            )
            result = process_inbound_ai_pipeline(
                conversation=conv,
                inbound_message=inbound,
                identity_status="unknown",
                message_type="text",
                phone_e164=str(conv.phone_e164),
                allow_autoreply_send=False,
                is_ai_enabled_fn=lambda: False,
                is_autoreply_enabled_fn=lambda: False,
            )
            db.session.refresh(conv)
            after = get_protocol_state(conv)
            proto = dict(result.get("protocol_auto_advance") or {})

            changed = _normalize_step(before.get("current_step_code")) != _normalize_step(after.get("current_step_code"))
            if changed:
                auto_advances += 1
            if bool(result.get("blocked")) or (proto.get("matched") is False and not changed):
                blocked += 1
            if bool(proto.get("out_of_step")):
                out_of_step_hits += 1
            if bool(proto.get("pending_correction")):
                item = dict(proto.get("pending_correction_item") or {})
                if item.get("field"):
                    corrections.append(str(item.get("field")))

            per_message.append(
                {
                    "text": str(raw_message),
                    "stage_before": str(before.get("current_step_code")),
                    "stage_after": str(after.get("current_step_code")),
                    "changed_stage": changed,
                    "entities_detected": dict(proto.get("entities_detected") or {}),
                    "missing_fields": list(proto.get("missing_fields") or []),
                    "blocked": bool(result.get("blocked")),
                    "auto": proto,
                }
            )
    finally:
        if previous_auto is None:
            os.environ.pop("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", None)
        else:
            os.environ["BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"] = previous_auto

    final_state = get_protocol_state(conv)
    final_metadata = dict(conv.metadata_json or {})
    final_entities = dict(final_metadata.get("protocol_entities") or {})
    pending_corrections = [
        x for x in (final_metadata.get("pending_corrections") or []) if isinstance(x, dict) and str(x.get("status") or "") == "pending_human"
    ]
    future_entities_map = dict(final_metadata.get("protocol_future_entities") or {})
    future_entities = {
        str(k): (v.get("value") if isinstance(v, dict) else v)
        for k, v in future_entities_map.items()
    }
    requires_human_detected = any(bool((msg.get("auto") or {}).get("requires_human")) for msg in per_message)

    draft_check = can_create_candidate_draft(conv)
    draft_possible = bool(draft_check.get("allowed"))
    draft_id = None
    preview = None
    if draft_possible:
        draft = get_candidate_draft(int(conv.id)) or create_candidate_draft(conv, actor_id=1)
        draft_id = int(draft.id)
        preview = build_candidate_conversion_preview(draft)

    expect = dict(scenario.get("expect") or {})
    failures: list[str] = []

    if "expected_final_step" in expect:
        if _normalize_step(final_state.get("current_step_code")) != _normalize_step(expect.get("expected_final_step")):
            failures.append("final_step_mismatch")
    if "should_advance" in expect:
        should_advance = bool(expect.get("should_advance"))
        did_advance = auto_advances > 0
        if should_advance != did_advance:
            failures.append("advance_expectation_mismatch")
    if expect.get("expect_out_of_step") and out_of_step_hits == 0:
        failures.append("out_of_step_not_detected")
    if expect.get("expect_blocked") and blocked == 0:
        failures.append("block_not_detected")
    pending_field = str(expect.get("expect_pending_correction_field") or "").strip()
    if pending_field and pending_field not in {str(x.get('field') or '') for x in pending_corrections}:
        failures.append("pending_correction_not_detected")
    should_create_draft = bool(expect.get("should_create_draft", expect.get("expect_draft_ready", False)))
    if should_create_draft != draft_possible:
        failures.append("draft_readiness_mismatch")

    should_not_create_candidate = bool(expect.get("should_not_create_candidate", True))
    try:
        after_candidates = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
    except OperationalError:
        after_candidates = 0
    candidate_created = after_candidates > before_candidates
    if should_not_create_candidate and candidate_created:
        failures.append("candidate_creation_detected")

    if "expected_blocks" in expect and int(expect.get("expected_blocks") or 0) != blocked:
        failures.append("block_count_mismatch")

    expected_pending = expect.get("expected_pending_corrections")
    if isinstance(expected_pending, list):
        expected_pending_set = {str(x) for x in expected_pending}
        got_pending_set = {str(x.get("field") or "") for x in pending_corrections}
        if not expected_pending_set.issubset(got_pending_set):
            failures.append("pending_corrections_mismatch")

    if "expect_draft_ready" in expect:
        if bool(expect.get("expect_draft_ready")) != draft_possible:
            failures.append("draft_readiness_mismatch")
    if "expected_draft_possible" in expect:
        if bool(expect.get("expected_draft_possible")) != draft_possible:
            failures.append("draft_possible_mismatch")
    if "expected_requires_human" in expect:
        if bool(expect.get("expected_requires_human")) != requires_human_detected:
            failures.append("requires_human_mismatch")

    exp_entities = dict(expect.get("expected_entities") or {})
    entity_matched, entity_total, entity_failures = _compare_expected_entities(
        exp_entities,
        final_entities,
        failure_prefix="entity_mismatch",
    )
    failures.extend(entity_failures)
    exp_future = dict(expect.get("expected_future_entities") or {})
    fut_matched, fut_total, fut_failures = _compare_expected_entities(
        exp_future,
        future_entities,
        failure_prefix="future_entity_mismatch",
    )
    failures.extend(fut_failures)

    passed = len(failures) == 0 and bool(expect.get("should_pass", True))
    if expect.get("should_pass") is False:
        passed = len(failures) > 0

    summary = {
        "scenario_id": scenario.get("id"),
        "title": scenario.get("title"),
        "passed": passed,
        "failures": failures,
        "initial_step": scenario.get("initial_step"),
        "final_step": final_state.get("current_step_code"),
        "auto_advances": auto_advances,
        "blocked_count": blocked,
        "out_of_step_count": out_of_step_hits,
        "entities": final_entities,
        "pending_corrections": pending_corrections,
        "future_entities": future_entities,
        "corrections_detected": corrections,
        "requires_human_detected": requires_human_detected,
        "messages": per_message,
        "draft_possible": draft_possible,
        "draft_id": draft_id,
        "candidate_created": candidate_created,
        "draft_check": draft_check,
        "preview": preview,
        "extraction": {
            "matched": entity_matched,
            "total": entity_total,
            "future_matched": fut_matched,
            "future_total": fut_total,
        },
    }
    if verbose:
        print(f"[{summary['scenario_id']}] pass={summary['passed']} step={summary['final_step']} failures={summary['failures']}")
    return summary


def run_simulation(options: RunOptions) -> dict[str, Any]:
    scenarios = load_scenarios()
    if options.scenario_id:
        scenarios = [x for x in scenarios if str(x.get("id")) == options.scenario_id]
        if not scenarios:
            raise ValueError(f"Escenario no encontrado: {options.scenario_id}")
    if options.max_scenarios is not None:
        scenarios = scenarios[: max(0, int(options.max_scenarios))]

    with flask_app.app_context():
        enforce_guardrails(allow_ai=options.allow_ai, allow_real_create_local=options.allow_real_create_local)
        _ensure_bot_tables()

        previous_auto = os.getenv("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED")
        os.environ["BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"] = "true"
        try:
            summaries = [
                run_single_scenario(scenario, scenario_index=index + 1, verbose=options.verbose)
                for index, scenario in enumerate(scenarios)
            ]
        finally:
            if previous_auto is None:
                os.environ.pop("BOT_PROTOCOL_AUTO_ADVANCE_ENABLED", None)
            else:
                os.environ["BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"] = previous_auto

    total = len(summaries)
    passed = sum(1 for x in summaries if x["passed"])
    failed = total - passed
    extraction_total = sum(int(x["extraction"]["total"]) for x in summaries)
    extraction_matched = sum(int(x["extraction"]["matched"]) for x in summaries)
    parser_errors = sum(1 for x in summaries if any(f.startswith("entity_mismatch:") for f in x["failures"]))
    advance_errors = sum(1 for x in summaries if "advance_expectation_mismatch" in x["failures"])
    block_errors = sum(
        1
        for x in summaries
        if any(f in {"block_not_detected", "block_count_mismatch"} for f in x["failures"])
    )
    correction_errors = sum(
        1
        for x in summaries
        if any(f in {"pending_correction_not_detected", "pending_corrections_mismatch"} for f in x["failures"])
    )
    future_entity_errors = sum(1 for x in summaries if any(f.startswith("future_entity_mismatch:") for f in x["failures"]))
    draft_errors = sum(
        1
        for x in summaries
        if any(f in {"draft_readiness_mismatch", "candidate_creation_detected"} for f in x["failures"])
    )
    corrections_detected = sum(len(x["pending_corrections"]) for x in summaries)
    drafts_ready = sum(1 for x in summaries if x.get("draft_possible"))

    metrics = {
        "total_scenarios": total,
        "passed": passed,
        "failed": failed,
        "extraction_accuracy": round((extraction_matched / extraction_total), 4) if extraction_total else 1.0,
        "extraction_fields_total": extraction_total,
        "extraction_fields_matched": extraction_matched,
        "parser_errors": parser_errors,
        "advance_errors": advance_errors,
        "block_errors": block_errors,
        "correction_errors": correction_errors,
        "future_entity_errors": future_entity_errors,
        "draft_errors": draft_errors,
        "corrections_detected": corrections_detected,
        "drafts_ready": drafts_ready,
        "failure_reasons": sorted({reason for row in summaries for reason in row.get("failures", [])}),
    }

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenarios": [x.get("scenario_id") for x in summaries],
        "metrics": metrics,
        "failures": [{"scenario_id": x["scenario_id"], "reasons": x["failures"]} for x in summaries if x["failures"]],
        "scenario_summaries": summaries,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"total escenarios: {metrics['total_scenarios']}")
    print(f"passed/failed: {metrics['passed']}/{metrics['failed']}")
    print(f"accuracy extracción por campo: {metrics['extraction_accuracy']} ({metrics['extraction_fields_matched']}/{metrics['extraction_fields_total']})")
    print(f"parser_errors: {metrics['parser_errors']}")
    print(f"errores de avance: {metrics['advance_errors']}")
    print(f"errores de bloqueo: {metrics['block_errors']}")
    print(f"correction_errors: {metrics['correction_errors']}")
    print(f"future_entity_errors: {metrics['future_entity_errors']}")
    print(f"draft_errors: {metrics['draft_errors']}")
    print(f"correcciones detectadas: {metrics['corrections_detected']}")
    print(f"drafts listos: {metrics['drafts_ready']}")
    print(f"reporte: {REPORT_PATH}")

    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Bot Conversation Simulator")
    parser.add_argument("--scenario", dest="scenario_id", help="ID de escenario a ejecutar")
    parser.add_argument("--max-scenarios", type=int, default=None, help="Máximo de escenarios a ejecutar")
    parser.add_argument("--verbose", action="store_true", help="Salida detallada")
    parser.add_argument("--allow-ai", action="store_true", help="Permite BOT_AI_ENABLED=true")
    parser.add_argument(
        "--allow-real-create-local",
        action="store_true",
        help="Permite BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_simulation(
        RunOptions(
            scenario_id=args.scenario_id,
            max_scenarios=args.max_scenarios,
            verbose=bool(args.verbose),
            allow_ai=bool(args.allow_ai),
            allow_real_create_local=bool(args.allow_real_create_local),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
