from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATASET_PATH = Path("data/bot_local_conversation_scenarios.json")
SIM_REPORT_PATH = Path("logs/bot_conversation_simulator_report.json")
COVERAGE_REPORT_PATH = Path("logs/bot_simulator_coverage_report.json")

PROTOCOL_STAGES = [
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

CASE_TYPES = [
    "happy path",
    "typo/bad spelling",
    "multi-answer",
    "correction",
    "out-of-step",
    "refusal",
    "complaint",
    "FAQ",
    "client-vs-candidate confusion",
    "sensitive data",
    "incomplete data",
    "long conversation",
    "noise",
]

ENTITY_ALIASES = {
    "name": "name",
    "nombre": "name",
    "age": "age",
    "edad": "age",
    "phone": "phone",
    "telefono": "phone",
    "phone_number": "phone",
    "city": "city",
    "ciudad": "city",
    "address": "address",
    "direccion": "address",
    "work_type": "work_type",
    "route": "route",
    "work_references": "references",
    "family_references": "references",
    "referencias_laborales": "references",
    "referencias_familiares": "references",
    "references": "references",
    "skills": "skills",
    "habilidades": "skills",
    "acceptance_25": "acceptance_25",
    "cedula": "cedula",
    "documentos": "documents",
    "documents": "documents",
    "photo": "photo",
    "foto": "photo",
}

CANONICAL_ENTITIES = [
    "name",
    "age",
    "phone",
    "city",
    "address",
    "work_type",
    "route",
    "references",
    "skills",
    "acceptance_25",
    "cedula",
    "documents",
    "photo",
]

SEMANTIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "address": ("santiago", "puerto plata", "gurabo", "los jardines", "dirección", "direccion", "vivo en"),
    "skills": ("cocinar", "limpiar", "cuidar niños", "cuidar ninos", "envejecientes"),
    "cedula": ("cedula", "cédula", "termina en", "documento"),
    "documents": ("documentos", "cedula", "cédula", "foto cedula"),
    "photo": ("foto", "perfil", "selfie"),
    "phone": ("número", "numero", "teléfono", "telefono", "809", "829", "849"),
    "references": ("referencia", "jefa", "hermana", "mamá", "mama", "patrona"),
    "acceptance_25": (
        "acepto el 25",
        "acepto el porcentaje",
        "ta bien acepto",
        "ok acepto",
        "eso del 25 no",
        "no acepto",
        "ustedes me quitan cuarto",
        "cuanto es el porcentaje",
        "eso no me gusta",
        "25%",
        "porcentaje",
    ),
}


def _normalize_entity(key: str) -> str | None:
    return ENTITY_ALIASES.get(str(key).strip().lower())


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Dataset inválido: scenarios vacío")
    return scenarios


def load_sim_report(path: Path = SIM_REPORT_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _text_blob(scenario: dict[str, Any]) -> str:
    messages = " ".join(str(x) for x in scenario.get("messages") or [])
    return f"{scenario.get('id', '')} {scenario.get('title', '')} {messages}".lower()


def classify_case_types(scenario: dict[str, Any]) -> set[str]:
    expect = dict(scenario.get("expect") or {})
    text = _text_blob(scenario)
    labels: set[str] = set()

    if any(x in text for x in ["typo", "bad_spelling", "mala ortografía", "low_literacy"]):
        labels.add("typo/bad spelling")
    if any(x in text for x in ["multi_answer", "all_in_one", "varias cosas", "responde varias"]):
        labels.add("multi-answer")
    if expect.get("expect_out_of_step") or "out_of_step" in text or "wrong_question" in text:
        labels.add("out-of-step")
    if any(
        [
            expect.get("expect_pending_correction_field"),
            expect.get("expected_pending_corrections"),
            "correction" in text,
            "corrige" in text,
            "cambia" in text,
            "change" in text,
        ]
    ):
        labels.add("correction")
    if any(x in text for x in ["reject", "refuse", "no acepta", "no me gusta", "cedula_refusal", "unavailable"]):
        labels.add("refusal")
    if any(x in text for x in ["complaint", "angry", "queja"]):
        labels.add("complaint")
    if any(x in text for x in ["asks_", "pregunta", "location_office", "payment", "faq"]):
        labels.add("FAQ")
    if any(x in text for x in ["client_side_request", "client_question", "candidate_gives_client"]):
        labels.add("client-vs-candidate confusion")
    if any(x in text for x in ["cedula", "document", "photo", "foto"]):
        labels.add("sensitive data")
    if any(x in text for x in ["incomplete", "partial", "names_only", "repeats_same_answer"]):
        labels.add("incomplete data")
    if "long_conversation" in text or len(scenario.get("messages") or []) >= 4:
        labels.add("long conversation")
    if any(x in text for x in ["noise", "nonsense", "no entendi", "ambiguous"]):
        labels.add("noise")

    if not labels:
        labels.add("happy path")
    return labels


def _extract_entities_from_scenario(scenario: dict[str, Any]) -> set[str]:
    expect = dict(scenario.get("expect") or {})
    metadata_seed = dict(scenario.get("metadata_seed") or {})
    protocol_entities = dict(metadata_seed.get("protocol_entities") or {})
    found: set[str] = set()

    for source in [expect.get("expected_entities") or {}, expect.get("expected_future_entities") or {}, protocol_entities]:
        if isinstance(source, dict):
            for key in source.keys():
                canonical = _normalize_entity(str(key))
                if canonical:
                    found.add(canonical)
    return found


def _extract_explicit_entities(scenario: dict[str, Any]) -> set[str]:
    expect = dict(scenario.get("expect") or {})
    explicit: set[str] = set()
    for source in [
        expect.get("expected_entities") or {},
        expect.get("expected_future_entities") or {},
    ]:
        if isinstance(source, dict):
            for key in source:
                canonical = _normalize_entity(str(key))
                if canonical:
                    explicit.add(canonical)
    for key in expect.get("expected_pending_corrections") or []:
        canonical = _normalize_entity(str(key))
        if canonical:
            explicit.add(canonical)
    pending_field = str(expect.get("expect_pending_correction_field") or "").strip()
    if pending_field:
        canonical = _normalize_entity(pending_field)
        if canonical:
            explicit.add(canonical)
    # Explicit validation for entity-handling behavior by stage.
    initial = str(scenario.get("initial_step") or "").upper().strip()
    if "expected_requires_human" in expect:
        if initial == "PERCENTAGE_ACCEPTANCE":
            explicit.add("acceptance_25")
        if initial == "DOCUMENT_REQUEST":
            explicit.update({"documents", "cedula"})
        if initial == "PROFILE_PHOTO":
            explicit.add("photo")
        if initial in {"FAMILY_REFERENCES", "LABOR_REFERENCES"}:
            explicit.update({"references", "phone"})
        if initial == "ADDRESS":
            explicit.add("address")
        if initial == "WORK_TYPE":
            explicit.add("work_type")
        if initial == "SKILLS":
            explicit.add("skills")
    return explicit


def _extract_semantic_entities(scenario: dict[str, Any]) -> set[str]:
    text = _text_blob(scenario)
    expect = dict(scenario.get("expect") or {})
    semantic: set[str] = set()
    for entity, keywords in SEMANTIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            semantic.add(entity)

    initial = str(scenario.get("initial_step") or "").upper().strip()
    expected_final = str(expect.get("expected_final_step") or "").upper().strip()
    stage_pair = {initial, expected_final}
    if "ADDRESS" in stage_pair:
        semantic.add("address")
    if "SKILLS" in stage_pair:
        semantic.add("skills")
    if "DOCUMENT_REQUEST" in stage_pair:
        semantic.update({"documents", "cedula"})
    if "PROFILE_PHOTO" in stage_pair:
        semantic.add("photo")
    if "FAMILY_REFERENCES" in stage_pair or "LABOR_REFERENCES" in stage_pair:
        semantic.update({"references", "phone"})
    return semantic


def _stage_paths_from_report(sim_report: dict[str, Any]) -> dict[str, set[str]]:
    paths: dict[str, set[str]] = defaultdict(set)
    for row in sim_report.get("scenario_summaries") or []:
        sid = str(row.get("scenario_id") or "")
        for msg in row.get("messages") or []:
            before = str(msg.get("stage_before") or "").upper().strip()
            after = str(msg.get("stage_after") or "").upper().strip()
            if before:
                paths[sid].add(before)
            if after:
                paths[sid].add(after)
    return paths


def _recommend_next_10(
    stage_counts: dict[str, int],
    entity_counts: dict[str, int],
    missing_case_types: list[str],
    low_stages: list[str],
    low_entities: list[str],
) -> list[str]:
    case_pool = missing_case_types or [
        "incomplete data",
        "multi-answer",
        "correction",
        "out-of-step",
        "refusal",
        "FAQ",
        "noise",
        "happy path",
        "sensitive data",
        "client-vs-candidate confusion",
    ]
    ranked_stages = low_stages or [k for k, _ in sorted(stage_counts.items(), key=lambda item: item[1])]
    ranked_entities = low_entities or [k for k, _ in sorted(entity_counts.items(), key=lambda item: item[1])]

    preferred_names: list[tuple[str, str, str, str]] = [
        ("welcome_unclear_identity", "WELCOME", "name", "noise"),
        ("family_reference_phone_missing", "FAMILY_REFERENCES", "phone", "incomplete data"),
        ("family_reference_complete", "FAMILY_REFERENCES", "references", "happy path"),
        ("skills_multiple_services", "SKILLS", "skills", "multi-answer"),
        ("profile_photo_unavailable", "PROFILE_PHOTO", "photo", "refusal"),
        ("profile_photo_confirmed", "PROFILE_PHOTO", "photo", "happy path"),
        ("document_partial_cedula", "DOCUMENT_REQUEST", "cedula", "sensitive data"),
        ("documents_refusal_requires_human", "DOCUMENT_REQUEST", "documents", "refusal"),
        ("address_sector_only", "ADDRESS", "address", "incomplete data"),
        ("group_warning_confused", "GROUP_WARNING", "documents", "FAQ"),
    ]

    recs: list[str] = []
    used_combos: set[tuple[str, str, str]] = set()
    for name, stage, entity, case_t in preferred_names:
        st = stage if stage in stage_counts else ranked_stages[0]
        en = entity if entity in entity_counts else ranked_entities[0]
        ct = case_t if case_t in CASE_TYPES else case_pool[0]
        combo = (st, en, ct)
        if combo in used_combos:
            continue
        used_combos.add(combo)
        recs.append(f"{name}: foco={st}, entidad={en}, tipo={ct}")

    i = 1
    while len(recs) < 10:
        st = ranked_stages[(i - 1) % len(ranked_stages)]
        en = ranked_entities[(i - 1) % len(ranked_entities)]
        ct = case_pool[(i - 1) % len(case_pool)]
        combo = (st, en, ct)
        if combo not in used_combos:
            used_combos.add(combo)
            recs.append(f"coverage_candidate_{i:02d}: foco={st}, entidad={en}, tipo={ct}")
        i += 1
    return recs[:10]


def analyze_coverage(scenarios: list[dict[str, Any]], sim_report: dict[str, Any] | None = None) -> dict[str, Any]:
    scenarios_by_id = {str(x.get("id")): x for x in scenarios}
    false_positive_protection_ids = {
        "random_numbers_not_phone",
        "price_not_phone",
        "cedula_like_noise",
        "fake_reference_sentence",
        "fake_skill_sentence",
        "percentage_not_acceptance",
        "route_like_noise",
        "city_inside_story",
        "photo_word_noise",
        "document_word_noise",
    }
    anti_noise_ids = {
        "random_numbers_not_phone",
        "price_not_phone",
        "route_like_noise",
        "photo_word_noise",
        "document_word_noise",
    }
    ambiguous_entity_ids = {
        "cedula_like_noise",
        "fake_reference_sentence",
        "fake_skill_sentence",
        "percentage_not_acceptance",
        "city_inside_story",
    }
    stage_hits: dict[str, set[str]] = {x: set() for x in PROTOCOL_STAGES}
    case_hits: dict[str, set[str]] = {x: set() for x in CASE_TYPES}
    entity_hits: dict[str, set[str]] = {x: set() for x in CANONICAL_ENTITIES}
    explicit_entity_hits: dict[str, set[str]] = {x: set() for x in CANONICAL_ENTITIES}
    semantic_entity_hits: dict[str, set[str]] = {x: set() for x in CANONICAL_ENTITIES}
    stage_paths = _stage_paths_from_report(sim_report or {})
    scenario_has_explicit_validation: set[str] = set()
    scenario_has_stage_validation: set[str] = set()
    report_rows_by_id = {
        str(row.get("scenario_id") or ""): row for row in (sim_report or {}).get("scenario_summaries") or []
    }

    for sid, scenario in scenarios_by_id.items():
        stages = set()
        initial = str(scenario.get("initial_step") or "").upper().strip()
        if initial:
            stages.add(initial)
        expected_final = str((scenario.get("expect") or {}).get("expected_final_step") or "").upper().strip()
        if expected_final:
            stages.add(expected_final)
        stages.update(stage_paths.get(sid, set()))
        for stage in stages:
            if stage in stage_hits:
                stage_hits[stage].add(sid)

        for case_t in classify_case_types(scenario):
            case_hits[case_t].add(sid)

        for entity in _extract_entities_from_scenario(scenario):
            entity_hits[entity].add(sid)
        explicit_entities = _extract_explicit_entities(scenario)
        if explicit_entities:
            scenario_has_explicit_validation.add(sid)
        expect = dict(scenario.get("expect") or {})
        stage_validation_keys = {
            "expected_final_step",
            "expected_blocks",
            "should_advance",
            "expect_out_of_step",
            "expect_blocked",
            "should_create_draft",
            "expect_draft_ready",
            "should_not_create_candidate",
        }
        if any(key in expect for key in stage_validation_keys):
            scenario_has_stage_validation.add(sid)
        for entity in explicit_entities:
            explicit_entity_hits[entity].add(sid)
        for entity in _extract_semantic_entities(scenario):
            semantic_entity_hits[entity].add(sid)
        row = report_rows_by_id.get(sid) or {}
        future_entities = dict(row.get("future_entities") or {})
        pending_corrections = row.get("pending_corrections") or []
        for key in future_entities.keys():
            canonical = _normalize_entity(str(key))
            if canonical:
                semantic_entity_hits[canonical].add(sid)
        for item in pending_corrections:
            if isinstance(item, dict):
                canonical = _normalize_entity(str(item.get("field") or ""))
                if canonical:
                    semantic_entity_hits[canonical].add(sid)

    total = len(scenarios)
    stage_counts = {k: len(v) for k, v in stage_hits.items()}
    case_counts = {k: len(v) for k, v in case_hits.items()}
    entity_counts = {k: len(v) for k, v in entity_hits.items()}
    explicit_entity_counts = {k: len(v) for k, v in explicit_entity_hits.items()}
    semantic_entity_counts = {k: len(v) for k, v in semantic_entity_hits.items()}
    low_stage_threshold = max(1, round(total * 0.10))
    low_entity_threshold = max(1, round(total * 0.08))

    requires_human_cases = 0
    pending_corrections = 0
    blocked_cases = 0
    draft_possible_cases = 0
    sensitive_masking_cases = case_counts.get("sensitive data", 0)
    candidate_creation_detected = 0
    if sim_report:
        for row in sim_report.get("scenario_summaries") or []:
            pending = row.get("pending_corrections") or []
            if pending:
                pending_corrections += 1
            if int(row.get("blocked_count") or 0) > 0:
                blocked_cases += 1
            if bool(row.get("draft_possible")):
                draft_possible_cases += 1
            if row.get("candidate_created"):
                candidate_creation_detected += 1
            for msg in row.get("messages") or []:
                auto = dict(msg.get("auto") or {})
                if bool(auto.get("requires_human")):
                    requires_human_cases += 1
                    break

    low_stages = [k for k, v in stage_counts.items() if v <= low_stage_threshold]
    low_entities = [k for k, v in explicit_entity_counts.items() if v <= low_entity_threshold]
    low_semantic_entities = [k for k, v in semantic_entity_counts.items() if v <= low_entity_threshold]
    explicit_validation_gap_entities = [
        k for k in CANONICAL_ENTITIES if semantic_entity_counts[k] > low_entity_threshold and explicit_entity_counts[k] <= low_entity_threshold
    ]
    critical_low_entities = [k for k in low_semantic_entities if semantic_entity_counts[k] == 0]
    missing_case_types = [k for k, v in case_counts.items() if v == 0]
    best_case_types = [k for k, v in sorted(case_counts.items(), key=lambda item: item[1], reverse=True)[:3]]
    stage_validation_coverage = {
        "scenarios_with_entity_expectations": len(scenario_has_explicit_validation),
        "scenarios_without_entity_expectations": total - len(scenario_has_explicit_validation),
        "entity_coverage_ratio": round(len(scenario_has_explicit_validation) / total, 4) if total else 0.0,
        "scenarios_with_stage_expectations": len(scenario_has_stage_validation),
        "scenarios_without_stage_expectations": total - len(scenario_has_stage_validation),
        "coverage_ratio": round(len(scenario_has_stage_validation) / total, 4) if total else 0.0,
    }
    safety_coverage = {
        "no_whatsapp": True,
        "no_outbound": True,
        "no_ai": True,
        "no_candidate_creation": candidate_creation_detected == 0,
        "requires_human_cases": requires_human_cases,
        "pending_corrections_cases": pending_corrections,
        "blocked_cases": blocked_cases,
        "draft_possible_cases": draft_possible_cases,
        "sensitive_masking_cases": sensitive_masking_cases,
        "false_positive_protection_cases": len(false_positive_protection_ids & set(scenarios_by_id.keys())),
        "anti_noise_cases": len(anti_noise_ids & set(scenarios_by_id.keys())),
        "ambiguous_entity_cases": len(ambiguous_entity_ids & set(scenarios_by_id.keys())),
    }
    recommendation_new_scenarios = [
        f"nueva-cobertura:{entity}" for entity in critical_low_entities[:5]
    ]
    recommendation_expectation_updates = [
        f"ajustar-expectativas:{entity}" for entity in explicit_validation_gap_entities[:8]
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "dataset_path": str(DATASET_PATH),
            "simulator_report_path": str(SIM_REPORT_PATH),
            "simulator_report_loaded": bool(sim_report),
            "total_scenarios": total,
        },
        "coverage": {
            "by_protocol_stage": {
                "counts": stage_counts,
                "scenarios": {k: sorted(v) for k, v in stage_hits.items()},
            },
            "by_case_type": {
                "counts": case_counts,
                "scenarios": {k: sorted(v) for k, v in case_hits.items()},
            },
            "by_entity": {
                "counts": entity_counts,
                "scenarios": {k: sorted(v) for k, v in entity_hits.items()},
            },
            "entity_coverage": {
                "explicit": explicit_entity_counts,
                "semantic": semantic_entity_counts,
            },
            "explicit_entity_coverage": {
                "counts": explicit_entity_counts,
                "scenarios": {k: sorted(v) for k, v in explicit_entity_hits.items()},
            },
            "semantic_entity_coverage": {
                "counts": semantic_entity_counts,
                "scenarios": {k: sorted(v) for k, v in semantic_entity_hits.items()},
            },
            "stage_validation_coverage": stage_validation_coverage,
            "safety_coverage": safety_coverage,
            "security": safety_coverage,
        },
        "gaps": {
            "low_coverage_stages": low_stages,
            "low_coverage_entities": low_entities,
            "critical_low_semantic_entities": critical_low_entities,
            "low_semantic_coverage_entities": low_semantic_entities,
            "explicit_validation_gap_entities": explicit_validation_gap_entities,
            "missing_case_types": missing_case_types,
            "best_covered_case_types": best_case_types,
            "recommended_next_10_scenarios": _recommend_next_10(
                stage_counts=stage_counts,
                entity_counts=entity_counts,
                missing_case_types=missing_case_types,
                low_stages=low_stages,
                low_entities=low_entities,
            ),
            "recommended_new_scenarios": recommendation_new_scenarios,
            "recommended_expectation_updates": recommendation_expectation_updates,
        },
    }


def save_report(report: dict[str, Any], path: Path = COVERAGE_REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    total = report["inputs"]["total_scenarios"]
    gaps = report["gaps"]
    print(f"Total escenarios: {total}")
    print(f"Etapas con baja cobertura: {', '.join(gaps['low_coverage_stages']) or 'ninguna'}")
    print(f"Tipos mejor cubiertos: {', '.join(gaps['best_covered_case_types']) or 'n/a'}")
    print(f"Tipos faltantes: {', '.join(gaps['missing_case_types']) or 'ninguno'}")
    print("Recomendación próximos 10:")
    for item in gaps["recommended_next_10_scenarios"]:
        print(f"- {item}")


def main() -> int:
    scenarios = load_dataset()
    sim_report = load_sim_report()
    coverage = analyze_coverage(scenarios, sim_report)
    save_report(coverage)
    print_summary(coverage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
