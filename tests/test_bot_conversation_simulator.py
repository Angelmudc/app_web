from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import app as flask_app
from config_app import db
from scripts.local.run_bot_conversation_simulator import (
    DATASET_PATH,
    GuardrailError,
    RunOptions,
    enforce_guardrails,
    load_scenarios,
    run_single_scenario,
)


def _base_env() -> dict[str, str]:
    return {
        "APP_ENV": "development",
        "WHATSAPP_ENABLED": "false",
        "BOT_AUTOREPLY_ENABLED": "false",
        "BOT_AI_ENABLED": "false",
        "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false",
        "BOT_DRY_RUN": "true",
    }


def _get_scenario(sid: str) -> dict:
    return next(x for x in load_scenarios() if str(x.get("id")) == sid)


def test_dataset_is_valid_and_has_minimum_scenarios():
    payload = json.loads(Path(DATASET_PATH).read_text(encoding="utf-8"))
    assert payload.get("version")
    scenarios = payload.get("scenarios")
    assert isinstance(scenarios, list)
    assert len(scenarios) >= 10
    ids = {str(x.get("id")) for x in scenarios}
    required_ids = {
        "clear_ordered_candidate",
        "typo_basic_info",
        "multi_answer_single_message",
        "work_modality_change_detected",
        "age_correction_detected",
        "does_not_understand_question",
        "out_of_step_answer",
        "rejects_25_percent",
        "incomplete_references",
        "ready_for_draft",
    }
    assert required_ids.issubset(ids)


def test_runner_rejects_dangerous_flags():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        with patch.dict("os.environ", {**_base_env(), "WHATSAPP_ENABLED": "true"}, clear=False):
            with pytest.raises(GuardrailError):
                enforce_guardrails(allow_ai=False, allow_real_create_local=False)
        with patch.dict("os.environ", {**_base_env(), "BOT_AUTOREPLY_ENABLED": "true"}, clear=False):
            with pytest.raises(GuardrailError):
                enforce_guardrails(allow_ai=False, allow_real_create_local=False)
        with patch.dict("os.environ", {**_base_env(), "BOT_AI_ENABLED": "true"}, clear=False):
            with pytest.raises(GuardrailError):
                enforce_guardrails(allow_ai=False, allow_real_create_local=False)
        with patch.dict("os.environ", {**_base_env(), "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "true"}, clear=False):
            with pytest.raises(GuardrailError):
                enforce_guardrails(allow_ai=False, allow_real_create_local=False)


def test_simple_scenario_passes():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("clear_ordered_candidate")
        result = run_single_scenario(scenario, scenario_index=9001, verbose=False)
        assert result["passed"] is True
        assert result["final_step"] == "BASIC_INFO"


def test_typo_scenario_extracts_basic_fields():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("typo_basic_info")
        result = run_single_scenario(scenario, scenario_index=9002, verbose=False)
        assert result["passed"] is True
        assert int(result["entities"].get("age") or 0) == 32


def test_age_correction_detected():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("age_correction_detected")
        result = run_single_scenario(scenario, scenario_index=9003, verbose=False)
        pending_fields = {str(x.get("field") or "") for x in result.get("pending_corrections") or []}
        assert "age" in pending_fields


def test_modality_change_detected():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("work_modality_change_detected")
        result = run_single_scenario(scenario, scenario_index=9004, verbose=False)
        pending_fields = {str(x.get("field") or "") for x in result.get("pending_corrections") or []}
        assert "work_type" in pending_fields


def test_multi_answer_extracts_future_entities_and_advances_one_step_only():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("multi_answer_single_message")
        result = run_single_scenario(scenario, scenario_index=90041, verbose=False)
        assert result["passed"] is True
        assert result["auto_advances"] == 1
        assert result["final_step"] == "ADDRESS"
        assert str((result.get("future_entities") or {}).get("city") or "") == "Santiago"


def test_reject_25_blocks_progress():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        scenario = _get_scenario("rejects_25_percent")
        result = run_single_scenario(scenario, scenario_index=9005, verbose=False)
        assert result["auto_advances"] == 0


def test_no_real_candidate_created_by_default():
    flask_app.config["TESTING"] = True
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        before = None
        try:
            before = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
        except SQLAlchemyError:
            db.session.rollback()
        scenario = _get_scenario("ready_for_draft")
        result = run_single_scenario(scenario, scenario_index=9006, verbose=False)
        assert bool(result.get("candidate_created")) is False
        if before is not None:
            after = int(db.session.execute(text("SELECT COUNT(*) FROM candidatas")).scalar() or 0)
            assert after == before


def test_cli_options_shape():
    opts = RunOptions(scenario_id="clear_ordered_candidate", max_scenarios=1, verbose=True)
    assert opts.scenario_id == "clear_ordered_candidate"
    assert opts.max_scenarios == 1
    assert opts.verbose is True


def test_welcome_to_personal_confirmation_accepts_si_hasme_las_preguntas():
    flask_app.config["TESTING"] = True
    scenario = {
        "id": "welcome_si_hasme",
        "initial_step": "WELCOME",
        "messages": ["hola", "si hasme las preguntas"],
        "expect": {"expected_final_step": "BASIC_INFO", "should_advance": True},
    }
    with flask_app.app_context(), patch.dict("os.environ", _base_env(), clear=False):
        result = run_single_scenario(scenario, scenario_index=9910, verbose=False)
        assert result["passed"] is True
        assert result["final_step"] == "BASIC_INFO"
