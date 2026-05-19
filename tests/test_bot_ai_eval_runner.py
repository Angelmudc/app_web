# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.local import run_bot_ai_eval
from scripts.local.bot_ai_eval_lib import BotAIEvalSafetyError, load_eval_cases, run_eval


def test_dataset_loads_with_required_fields():
    cases = load_eval_cases("data/bot_ai_eval_cases.json")
    assert isinstance(cases, list)
    assert len(cases) >= 10
    sample = cases[0]
    for key in ["id", "input_text", "expected_intent", "expected_requires_human", "expected_safe"]:
        assert key in sample



def test_runner_mock_mode_generates_report_without_whatsapp_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    report_path = tmp_path / "bot_ai_eval_report.json"

    with patch("services.whatsapp_cloud_service.requests.post") as wa_post_mock:
        monkeypatch.setattr(
            "sys.argv",
            [
                "run_bot_ai_eval.py",
                "--mode",
                "mock",
                "--dataset",
                "data/bot_ai_eval_cases.json",
                "--report",
                str(report_path),
            ],
        )
        exit_code = run_bot_ai_eval.main()

    assert exit_code == 0
    wa_post_mock.assert_not_called()
    assert report_path.exists()



def test_report_json_generated_and_has_metrics(tmp_path):
    cases = load_eval_cases("data/bot_ai_eval_cases.json")
    report = run_eval(cases=cases, mode="mock")
    out = tmp_path / "report.json"
    out.write_text(json.dumps(report), encoding="utf-8")
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert "timestamp" in payload
    assert "metrics" in payload
    assert "results" in payload
    assert payload["metrics"]["intent_match_rate"] >= 0



def test_metrics_shape_and_low_confidence_count():
    cases = [
        {
            "id": "x1",
            "input_text": "",
            "expected_intent": "UNKNOWN",
            "expected_requires_human": True,
            "expected_safe": False,
        }
    ]
    report = run_eval(cases=cases, mode="mock")
    metrics = report["metrics"]
    assert set(["intent_match_rate", "safe_response_rate", "escalation_accuracy", "invalid_json_count", "low_confidence_count"]).issubset(metrics.keys())
    assert metrics["low_confidence_count"] == 1



def test_allowed_intents_accepts_explicit_alternative():
    cases = [
        {
            "id": "amb-1",
            "input_text": "Necesito ayuda con mi caso y no sé por dónde empezar",
            "expected_intent": "UNKNOWN",
            "allowed_intents": ["UNKNOWN", "HUMAN_REQUEST"],
            "expected_requires_human": True,
            "expected_safe": False,
        }
    ]
    report = run_eval(cases=cases, mode="mock")
    row = report["results"][0]
    assert row["intent_match"] is True
    assert row["escalation_match"] is True
    assert row["safe_match"] is True


def test_real_mode_guard_rejects_invalid_flags(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    with patch("scripts.local.bot_ai_eval_lib.classify_intent") as ai_mock:
        try:
            run_eval(cases=load_eval_cases("data/bot_ai_eval_cases.json"), mode="real")
            raised = None
        except Exception as exc:
            raised = exc

    assert isinstance(raised, BotAIEvalSafetyError)
    ai_mock.assert_not_called()



def test_mock_mode_works_without_api_key(monkeypatch):
    monkeypatch.delenv("BOT_AI_API_KEY", raising=False)
    report = run_eval(cases=load_eval_cases("data/bot_ai_eval_cases.json"), mode="mock")
    assert report["mode"] == "mock"
    assert report["metrics"]["total_cases"] > 0



def test_unsafe_case_escalates_in_mock():
    cases = [
        {
            "id": "unsafe-case",
            "input_text": "Necesito resolver un tema legal y pago urgente",
            "expected_intent": "UNKNOWN",
            "expected_requires_human": True,
            "expected_safe": False,
        }
    ]
    report = run_eval(cases=cases, mode="mock")
    row = report["results"][0]
    assert row["actual_requires_human"] is True
    assert row["actual_safe"] is False



def test_runner_output_does_not_print_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BOT_AI_API_KEY", "sk-test-secret-value")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ai_eval.py",
            "--mode",
            "mock",
            "--dataset",
            "data/bot_ai_eval_cases.json",
            "--report",
            str(tmp_path / "eval-report.json"),
        ],
    )

    code = run_bot_ai_eval.main()
    out = capsys.readouterr().out

    assert code == 0
    assert "sk-test-secret-value" not in out
    assert "BOT_AI_API_KEY" not in out


def test_runner_blocks_large_dataset_without_allow_flag(monkeypatch, tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            [
                {"id": "1", "input_text": "a", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "2", "input_text": "b", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "3", "input_text": "c", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_AI_EVAL_MAX_CASES", "2")
    monkeypatch.setattr(
        "sys.argv",
        ["run_bot_ai_eval.py", "--mode", "mock", "--dataset", str(dataset), "--report", str(tmp_path / "r.json")],
    )
    code = run_bot_ai_eval.main()
    assert code == 2


def test_runner_allows_large_dataset_with_override_flag(monkeypatch, tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            [
                {"id": "1", "input_text": "a", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "2", "input_text": "b", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "3", "input_text": "c", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_AI_EVAL_MAX_CASES", "2")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ai_eval.py",
            "--mode",
            "mock",
            "--allow-large-run",
            "--dataset",
            str(dataset),
            "--report",
            str(tmp_path / "r.json"),
        ],
    )
    code = run_bot_ai_eval.main()
    assert code == 0


def test_run_eval_respects_session_request_limit(monkeypatch):
    monkeypatch.setenv("BOT_AI_SESSION_REQUEST_LIMIT", "1")
    cases = [
        {"id": "1", "input_text": "a", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
        {"id": "2", "input_text": "b", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
    ]
    try:
        run_eval(cases=cases, mode="mock")
        raised = None
    except Exception as exc:
        raised = exc
    assert isinstance(raised, BotAIEvalSafetyError)


def test_runner_max_cases_truncates_dataset(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ai_eval.py",
            "--mode",
            "mock",
            "--dataset",
            "data/bot_ai_eval_cases.json",
            "--max-cases",
            "3",
            "--report",
            str(report_path),
        ],
    )
    code = run_bot_ai_eval.main()
    assert code == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["dataset_total_cases"] >= 10
    assert payload["executed_cases"] == 3
    assert payload["metrics"]["total_cases"] == 3
    assert len(payload["results"]) == 3


def test_runner_max_cases_invalid_zero(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_bot_ai_eval.py", "--mode", "mock", "--max-cases", "0"],
    )
    with patch("sys.stderr") as stderr_mock:
        try:
            run_bot_ai_eval.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Expected SystemExit for invalid --max-cases=0")
    written = "".join(str(args[0]) for args, _ in stderr_mock.write.call_args_list)
    assert "--max-cases debe ser un entero positivo (> 0)." in written


def test_runner_max_cases_invalid_negative(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_bot_ai_eval.py", "--mode", "mock", "--max-cases", "-1"],
    )
    with patch("sys.stderr") as stderr_mock:
        try:
            run_bot_ai_eval.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Expected SystemExit for invalid --max-cases=-1")
    written = "".join(str(args[0]) for args, _ in stderr_mock.write.call_args_list)
    assert "--max-cases debe ser un entero positivo (> 0)." in written


def test_runner_max_cases_invalid_string(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_bot_ai_eval.py", "--mode", "mock", "--max-cases", "abc"],
    )
    with patch("sys.stderr") as stderr_mock:
        try:
            run_bot_ai_eval.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Expected SystemExit for invalid --max-cases=abc")
    written = "".join(str(args[0]) for args, _ in stderr_mock.write.call_args_list)
    assert "--max-cases debe ser un entero positivo (> 0)." in written


def test_runner_max_cases_applies_before_session_limit(monkeypatch, tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            [
                {"id": "1", "input_text": "a", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "2", "input_text": "b", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "3", "input_text": "c", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
                {"id": "4", "input_text": "d", "expected_intent": "UNKNOWN", "expected_requires_human": True, "expected_safe": False},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_AI_SESSION_REQUEST_LIMIT", "3")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ai_eval.py",
            "--mode",
            "mock",
            "--dataset",
            str(dataset),
            "--max-cases",
            "3",
            "--report",
            str(tmp_path / "r.json"),
        ],
    )
    code = run_bot_ai_eval.main()
    assert code == 0


def test_runner_max_cases_works_in_real_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ai_eval.py",
            "--mode",
            "real",
            "--max-cases",
            "3",
            "--report",
            str(tmp_path / "real-report.json"),
        ],
    )

    with patch("scripts.local.bot_ai_eval_lib.classify_intent") as ai_mock:
        ai_mock.return_value = {
            "ok": True,
            "intent": "UNKNOWN",
            "answer_text": "",
            "confidence": 0.9,
            "requires_human": True,
            "escalation_reason": "AI_HUMAN_OR_UNKNOWN",
            "prompt_version": "test",
            "ai_model": "test-model",
        }
        code = run_bot_ai_eval.main()

    assert code == 0
    assert ai_mock.call_count == 3
