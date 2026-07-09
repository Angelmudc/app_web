from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_BASELINE_PATH = Path("logs/bot_simulator_baseline_snapshot.json")
DEFAULT_REPORT_PATH = Path("logs/bot_conversation_simulator_report.json")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"JSON no encontrado: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON inválido: {path} (raíz no es objeto)")
    return data


def _to_int(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"Valor inválido para {field}: {value!r}") from exc


def _to_float(value: Any, *, field: str) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"Valor inválido para {field}: {value!r}") from exc


def _extract_report_metrics(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Reporte inválido: falta objeto metrics")
    return {
        "total_scenarios": _to_int(metrics.get("total_scenarios", 0), field="total_scenarios"),
        "passed": _to_int(metrics.get("passed", 0), field="passed"),
        "failed": _to_int(metrics.get("failed", 0), field="failed"),
        "parser_errors": _to_int(metrics.get("parser_errors", 0), field="parser_errors"),
        "advance_errors": _to_int(metrics.get("advance_errors", 0), field="advance_errors"),
        "block_errors": _to_int(metrics.get("block_errors", 0), field="block_errors"),
        "correction_errors": _to_int(metrics.get("correction_errors", 0), field="correction_errors"),
        "future_entity_errors": _to_int(metrics.get("future_entity_errors", 0), field="future_entity_errors"),
        "draft_errors": _to_int(metrics.get("draft_errors", 0), field="draft_errors"),
        "accuracy": _to_float(metrics.get("extraction_accuracy", 0.0), field="accuracy"),
    }


def _extract_baseline_metrics(baseline: dict[str, Any]) -> dict[str, Any]:
    required = [
        "total_scenarios",
        "passed",
        "failed",
        "parser_errors",
        "advance_errors",
        "block_errors",
        "correction_errors",
        "future_entity_errors",
        "draft_errors",
        "accuracy",
    ]
    missing = [k for k in required if k not in baseline]
    if missing:
        raise ValueError(f"Baseline inválido: faltan campos {', '.join(missing)}")

    return {
        "total_scenarios": _to_int(baseline["total_scenarios"], field="total_scenarios"),
        "passed": _to_int(baseline["passed"], field="passed"),
        "failed": _to_int(baseline["failed"], field="failed"),
        "parser_errors": _to_int(baseline["parser_errors"], field="parser_errors"),
        "advance_errors": _to_int(baseline["advance_errors"], field="advance_errors"),
        "block_errors": _to_int(baseline["block_errors"], field="block_errors"),
        "correction_errors": _to_int(baseline["correction_errors"], field="correction_errors"),
        "future_entity_errors": _to_int(baseline["future_entity_errors"], field="future_entity_errors"),
        "draft_errors": _to_int(baseline["draft_errors"], field="draft_errors"),
        "accuracy": _to_float(baseline["accuracy"], field="accuracy"),
    }


def compare_metrics(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[bool, list[str], list[str], list[str]]:
    failures: list[str] = []
    improvements: list[str] = []
    neutral: list[str] = []

    def cmp_int(field: str, *, fail_if_higher: bool = False, fail_if_lower: bool = False) -> None:
        b = int(baseline[field])
        c = int(current[field])
        delta = c - b
        if fail_if_higher and c > b:
            failures.append(f"{field}: baseline={b}, current={c}, delta={delta:+d} (regresión)")
            return
        if fail_if_lower and c < b:
            failures.append(f"{field}: baseline={b}, current={c}, delta={delta:+d} (regresión)")
            return
        if c == b:
            neutral.append(f"{field}: baseline={b}, current={c}, delta={delta:+d} (sin cambio)")
        elif c < b:
            improvements.append(f"{field}: baseline={b}, current={c}, delta={delta:+d} (mejora)")
        else:
            improvements.append(f"{field}: baseline={b}, current={c}, delta={delta:+d} (cambio)")

    cmp_int("total_scenarios", fail_if_lower=True)
    cmp_int("failed", fail_if_higher=True)
    cmp_int("parser_errors", fail_if_higher=True)
    cmp_int("advance_errors", fail_if_higher=True)
    cmp_int("block_errors", fail_if_higher=True)
    cmp_int("correction_errors", fail_if_higher=True)
    cmp_int("future_entity_errors", fail_if_higher=True)
    cmp_int("draft_errors", fail_if_higher=True)

    b_acc = float(baseline["accuracy"])
    c_acc = float(current["accuracy"])
    d_acc = c_acc - b_acc
    if c_acc < b_acc:
        failures.append(
            f"accuracy: baseline={b_acc:.6f}, current={c_acc:.6f}, delta={d_acc:+.6f} (regresión)"
        )
    elif c_acc == b_acc:
        neutral.append(
            f"accuracy: baseline={b_acc:.6f}, current={c_acc:.6f}, delta={d_acc:+.6f} (sin cambio)"
        )
    else:
        improvements.append(
            f"accuracy: baseline={b_acc:.6f}, current={c_acc:.6f}, delta={d_acc:+.6f} (mejora)"
        )

    return (len(failures) == 0, failures, improvements, neutral)


def run_regression_check(baseline_path: Path, report_path: Path) -> int:
    baseline = _extract_baseline_metrics(load_json(baseline_path))
    current = _extract_report_metrics(load_json(report_path))

    ok, failures, improvements, neutral = compare_metrics(baseline, current)

    if ok:
        print("BOT_SIMULATOR_REGRESSION: OK")
    else:
        print("BOT_SIMULATOR_REGRESSION: FAIL")

    print(f"baseline_snapshot={baseline_path}")
    print(f"current_report={report_path}")

    if failures:
        print("[FAILURES]")
        for line in failures:
            print(f"- {line}")

    if improvements:
        print("[INFO_IMPROVEMENTS]")
        for line in improvements:
            print(f"- {line}")

    if neutral:
        print("[NEUTRAL]")
        for line in neutral:
            print(f"- {line}")

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Chequeo anti-regresión local del simulador bot")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH), help="Ruta snapshot baseline JSON")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Ruta reporte actual simulador JSON")
    args = parser.parse_args()
    return run_regression_check(Path(args.baseline), Path(args.report))


if __name__ == "__main__":
    raise SystemExit(main())
