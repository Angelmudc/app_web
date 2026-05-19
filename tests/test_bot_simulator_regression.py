from __future__ import annotations

import json
from pathlib import Path

from scripts.local.check_bot_simulator_regression import run_regression_check


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _baseline() -> dict:
    return {
        "total_scenarios": 100,
        "passed": 100,
        "failed": 0,
        "parser_errors": 0,
        "advance_errors": 0,
        "block_errors": 0,
        "correction_errors": 0,
        "future_entity_errors": 0,
        "draft_errors": 0,
        "accuracy": 1.0,
        "generated_at": "2026-05-09T23:28:04.645061+00:00",
        "baseline_version": "2026-05-09-official-v1",
    }


def _report(metrics_overrides: dict | None = None) -> dict:
    metrics = {
        "total_scenarios": 100,
        "passed": 100,
        "failed": 0,
        "parser_errors": 0,
        "advance_errors": 0,
        "block_errors": 0,
        "correction_errors": 0,
        "future_entity_errors": 0,
        "draft_errors": 0,
        "extraction_accuracy": 1.0,
    }
    if metrics_overrides:
        metrics.update(metrics_overrides)
    return {"metrics": metrics}


def test_regression_ok_when_identical(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    _write_json(baseline_path, _baseline())
    _write_json(report_path, _report())

    assert run_regression_check(baseline_path, report_path) == 0


def test_regression_fail_when_failed_increases(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    _write_json(baseline_path, _baseline())
    _write_json(report_path, _report({"failed": 1, "passed": 99}))

    assert run_regression_check(baseline_path, report_path) == 1


def test_regression_fail_when_accuracy_decreases(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    _write_json(baseline_path, _baseline())
    _write_json(report_path, _report({"extraction_accuracy": 0.99}))

    assert run_regression_check(baseline_path, report_path) == 1


def test_regression_fail_when_total_scenarios_decreases(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    _write_json(baseline_path, _baseline())
    _write_json(report_path, _report({"total_scenarios": 99, "passed": 99}))

    assert run_regression_check(baseline_path, report_path) == 1


def test_regression_ok_when_improves(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"

    improved_baseline = _baseline()
    improved_baseline["failed"] = 2
    improved_baseline["parser_errors"] = 1
    improved_baseline["accuracy"] = 0.95

    _write_json(baseline_path, improved_baseline)
    _write_json(report_path, _report())

    assert run_regression_check(baseline_path, report_path) == 0
