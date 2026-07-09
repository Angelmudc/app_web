from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_REPORT_PATH = Path("logs/bot_conversation_simulator_report.json")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Reporte no encontrado: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reporte JSON inválido: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Reporte inválido: {path} (raíz no es objeto)")
    return data


def validate_simulator_baseline(report: dict[str, Any]) -> list[str]:
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return ["Reporte inválido: falta objeto metrics"]

    def metric_int(name: str) -> int:
        try:
            return int(metrics.get(name, 0))
        except Exception:
            return -1

    errors: list[str] = []
    total = metric_int("total_scenarios")
    failed = metric_int("failed")
    parser_errors = metric_int("parser_errors")
    advance_errors = metric_int("advance_errors")
    block_errors = metric_int("block_errors")
    correction_errors = metric_int("correction_errors")
    future_entity_errors = metric_int("future_entity_errors")
    draft_errors = metric_int("draft_errors")

    if total < 100:
        errors.append(f"total_scenarios esperado >=100, recibido={total}")
    if failed > 0:
        errors.append(f"failed esperado=0, recibido={failed}")
    if parser_errors > 0:
        errors.append(f"parser_errors esperado=0, recibido={parser_errors}")
    if advance_errors > 0:
        errors.append(f"advance_errors esperado=0, recibido={advance_errors}")
    if block_errors > 0:
        errors.append(f"block_errors esperado=0, recibido={block_errors}")
    if correction_errors > 0:
        errors.append(f"correction_errors esperado=0, recibido={correction_errors}")
    if future_entity_errors > 0:
        errors.append(f"future_entity_errors esperado=0, recibido={future_entity_errors}")
    if draft_errors > 0:
        errors.append(f"draft_errors esperado=0, recibido={draft_errors}")
    return errors


def validate_suite_log(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if "passed" not in text:
        return ["suite_log sin resumen con 'passed'"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida baseline del simulador local bot.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Ruta al reporte del simulador JSON")
    parser.add_argument("--suite-log", default="", help="Ruta opcional a log de suite combinada")
    args = parser.parse_args()

    report = load_json(Path(args.report))
    errors = validate_simulator_baseline(report)

    suite_log = str(args.suite_log or "").strip()
    if suite_log:
        suite_path = Path(suite_log)
        if not suite_path.exists():
            errors.append(f"suite_log no encontrado: {suite_path}")
        else:
            errors.extend(validate_suite_log(suite_path))

    if errors:
        print("BOT_SIMULATOR_BASELINE: FAIL")
        for err in errors:
            print(f"- {err}")
        return 1

    metrics = report.get("metrics") or {}
    print("BOT_SIMULATOR_BASELINE: OK")
    print(f"total_scenarios={int(metrics.get('total_scenarios') or 0)}")
    print(f"passed={int(metrics.get('passed') or 0)}")
    print(f"failed={int(metrics.get('failed') or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
