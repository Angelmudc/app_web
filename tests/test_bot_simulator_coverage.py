from __future__ import annotations

import json
from pathlib import Path

from scripts.local.analyze_bot_simulator_coverage import (
    COVERAGE_REPORT_PATH,
    DATASET_PATH,
    analyze_coverage,
    load_dataset,
    load_sim_report,
    save_report,
)


def test_load_dataset_works():
    scenarios = load_dataset()
    assert isinstance(scenarios, list)
    assert len(scenarios) >= 50
    assert all("id" in row for row in scenarios)


def test_generate_report_and_detect_stage_coverage():
    scenarios = load_dataset()
    sim_report = load_sim_report()
    report = analyze_coverage(scenarios, sim_report)

    assert report["inputs"]["total_scenarios"] >= 50
    stages = report["coverage"]["by_protocol_stage"]["counts"]
    assert "BASIC_INFO" in stages
    assert stages["BASIC_INFO"] > 0
    assert "DOCUMENT_REQUEST" in stages
    assert stages["DOCUMENT_REQUEST"] > 0
    semantic = report["coverage"]["semantic_entity_coverage"]["counts"]
    assert semantic["photo"] > 0
    assert semantic["cedula"] > 0
    assert semantic["skills"] > 0
    assert semantic["acceptance_25"] > 0


def test_detect_gaps_and_case_types():
    scenarios = load_dataset()
    report = analyze_coverage(scenarios, None)
    gaps = report["gaps"]

    assert "low_coverage_stages" in gaps
    assert "missing_case_types" in gaps
    assert "recommended_next_10_scenarios" in gaps
    assert len(gaps["recommended_next_10_scenarios"]) == 10
    recs = gaps["recommended_next_10_scenarios"]
    assert len(set(recs)) == len(recs)
    assert any("family_reference_phone_missing" in item for item in recs)
    assert "recommended_expectation_updates" in gaps
    assert "recommended_new_scenarios" in gaps


def test_save_report_json(tmp_path: Path):
    scenarios = load_dataset()
    report = analyze_coverage(scenarios, None)
    out = tmp_path / "coverage.json"
    save_report(report, out)

    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert persisted["inputs"]["dataset_path"] == str(DATASET_PATH)
    assert "explicit_entity_coverage" in persisted["coverage"]
    assert "semantic_entity_coverage" in persisted["coverage"]
    assert "stage_validation_coverage" in persisted["coverage"]
    assert "safety_coverage" in persisted["coverage"]


def test_semantic_examples_detected_from_messages():
    scenarios = load_dataset()
    report = analyze_coverage(scenarios, None)
    semantic = report["coverage"]["semantic_entity_coverage"]["scenarios"]

    assert "profile_photo_unavailable" in semantic["photo"]
    assert "document_partial_cedula" in semantic["cedula"]
    assert "skills_multiple_services" in semantic["skills"]


def test_real_gap_vs_explicit_expectation_gap():
    scenarios = load_dataset()
    report = analyze_coverage(scenarios, None)
    gaps = report["gaps"]

    explicit_gaps = set(gaps["explicit_validation_gap_entities"])
    critical_semantic = set(gaps["critical_low_semantic_entities"])
    assert "address" in explicit_gaps
    assert "address" not in critical_semantic


def test_stage_validation_ratio_improved():
    scenarios = load_dataset()
    report = analyze_coverage(scenarios, None)
    ratio = float(report["coverage"]["stage_validation_coverage"]["coverage_ratio"])
    assert ratio >= 0.25


def test_no_db_or_external_required():
    code = Path("scripts/local/analyze_bot_simulator_coverage.py").read_text(encoding="utf-8").lower()
    forbidden = ["sqlalchemy", "db.session", "openai", "requests.", "http://", "https://"]
    for token in forbidden:
        assert token not in code

    assert COVERAGE_REPORT_PATH.name == "bot_simulator_coverage_report.json"
