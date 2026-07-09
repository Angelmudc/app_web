#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.local.bot_ai_eval_lib import BotAIEvalSafetyError, load_eval_cases, run_eval, write_report
from services.bot_ai_limits_service import ai_eval_max_cases

DEFAULT_DATASET = ROOT / "data" / "bot_ai_eval_cases.json"
DEFAULT_REPORT = ROOT / "logs" / "bot_ai_eval_report.json"


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-cases debe ser un entero positivo (> 0).") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("--max-cases debe ser un entero positivo (> 0).")
    return value


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Runner local de evaluación IA (sin WhatsApp ni autorespuesta).")
    p.add_argument("--mode", choices=["mock", "real"], default="mock")
    p.add_argument("--dataset", default=str(DEFAULT_DATASET))
    p.add_argument("--report", default=str(DEFAULT_REPORT))
    p.add_argument(
        "--max-cases",
        type=_positive_int,
        default=None,
        help="Procesa solo los primeros N casos del dataset (entero positivo).",
    )
    p.add_argument("--allow-large-run", action="store_true", help="Permite ejecutar datasets por encima de BOT_AI_EVAL_MAX_CASES.")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    try:
        cases = load_eval_cases(args.dataset)
        dataset_total_cases = len(cases)
        if args.max_cases is not None:
            cases = cases[: args.max_cases]
        executed_cases = len(cases)
        max_cases = int(ai_eval_max_cases())
        if len(cases) > max_cases and not bool(args.allow_large_run):
            print(
                f"ERROR: dataset tiene {len(cases)} casos y supera BOT_AI_EVAL_MAX_CASES={max_cases}. "
                "Usa --allow-large-run para override explícito."
            )
            return 2
        report = run_eval(cases=cases, mode=args.mode)
        report["dataset_total_cases"] = dataset_total_cases
        report["executed_cases"] = executed_cases
        out = write_report(report, args.report)
    except BotAIEvalSafetyError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: fallo de evaluación ({type(exc).__name__}).")
        return 1

    metrics = report["metrics"]
    print(f"mode: {report['mode']}")
    print(f"model: {report['model']}")
    print(f"dataset_total_cases: {report['dataset_total_cases']}")
    print(f"executed_cases: {report['executed_cases']}")
    print(f"total_cases: {metrics['total_cases']}")
    print(f"intent_match_rate: {metrics['intent_match_rate']}")
    print(f"safe_response_rate: {metrics['safe_response_rate']}")
    print(f"escalation_accuracy: {metrics['escalation_accuracy']}")
    print(f"invalid_json_count: {metrics['invalid_json_count']}")
    print(f"low_confidence_count: {metrics['low_confidence_count']}")
    print(f"requires_human_rate: {metrics['requires_human_rate']}")
    print(f"failed_cases: {len(report['failed_cases'])}")
    print(f"unsafe_cases: {len(report['unsafe_cases'])}")
    print(f"report_path: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
