from __future__ import annotations

from scripts.local.check_bot_simulator_baseline import validate_simulator_baseline


def _report(
    *,
    total: int = 100,
    failed: int = 0,
    parser_errors: int = 0,
    advance_errors: int = 0,
    block_errors: int = 0,
    correction_errors: int = 0,
    future_entity_errors: int = 0,
    draft_errors: int = 0,
):
    return {
        "metrics": {
            "total_scenarios": total,
            "failed": failed,
            "parser_errors": parser_errors,
            "advance_errors": advance_errors,
            "block_errors": block_errors,
            "correction_errors": correction_errors,
            "future_entity_errors": future_entity_errors,
            "draft_errors": draft_errors,
        }
    }


def test_baseline_ok_with_valid_report():
    errors = validate_simulator_baseline(_report())
    assert errors == []


def test_baseline_fails_if_failed_gt_zero():
    errors = validate_simulator_baseline(_report(failed=1))
    assert any("failed esperado=0" in x for x in errors)


def test_baseline_fails_if_total_below_100():
    errors = validate_simulator_baseline(_report(total=99))
    assert any("total_scenarios esperado >=100" in x for x in errors)


def test_baseline_fails_if_parser_errors_gt_zero():
    errors = validate_simulator_baseline(_report(parser_errors=1))
    assert any("parser_errors esperado=0" in x for x in errors)


def test_baseline_fails_if_advance_errors_gt_zero():
    errors = validate_simulator_baseline(_report(advance_errors=1))
    assert any("advance_errors esperado=0" in x for x in errors)
