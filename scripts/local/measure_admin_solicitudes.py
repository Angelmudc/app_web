#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Medicion local, solo lectura, del costo real de /admin/solicitudes.

Uso recomendado:
  APP_ENV=local venv/bin/python scripts/local/measure_admin_solicitudes.py
  APP_ENV=local venv/bin/python scripts/local/measure_admin_solicitudes.py --runs 5 --warmup 1
  APP_ENV=local venv/bin/python scripts/local/measure_admin_solicitudes.py --json-out /tmp/admin_solicitudes_metrics.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
import admin.routes as admin_routes
from models import Cliente, Solicitud


MEASURE_HEADER = "X-Admin-Solicitudes-Measure"
METRICS_HEADER = "X-Admin-Solicitudes-Metrics"


def _mean(values: list[float]) -> float:
    clean = [float(v) for v in (values or [])]
    if not clean:
        return 0.0
    return round(statistics.fmean(clean), 2)


def _mean_int(values: list[int]) -> int:
    clean = [int(v) for v in (values or [])]
    if not clean:
        return 0
    return int(round(statistics.fmean(clean)))


def _scenario_headers(*, is_async: bool) -> dict[str, str]:
    headers = {MEASURE_HEADER: "1"}
    if is_async:
        headers.update(
            {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-Admin-Async": "1",
            }
        )
    return headers


def _login(client) -> None:
    username = os.getenv("ADMIN_MEASURE_USER", "Karla")
    password = os.getenv("ADMIN_MEASURE_PASSWORD", "9989")
    resp = client.post(
        "/admin/login",
        data={"usuario": username, "clave": password},
        follow_redirects=False,
    )
    if resp.status_code not in (302, 303):
        raise RuntimeError(f"Login admin fallo: status={resp.status_code}")


def _sample_q_value() -> str:
    row = (
        Solicitud.query
        .outerjoin(Cliente, Solicitud.cliente_id == Cliente.id)
        .with_entities(
            Solicitud.codigo_solicitud,
            Solicitud.ciudad_sector,
            Cliente.nombre_completo,
        )
        .order_by(Solicitud.id.desc())
        .first()
    )
    if not row:
        return ""
    for raw in (row[0], row[1], row[2]):
        value = str(raw or "").strip()
        if value:
            return value[:40]
    return ""


def _sample_estado_value() -> str:
    rows = (
        Solicitud.query
        .with_entities(Solicitud.estado, admin_routes.func.count(Solicitud.id).label("total"))
        .filter(Solicitud.estado.isnot(None))
        .group_by(Solicitud.estado)
        .order_by(admin_routes.func.count(Solicitud.id).desc(), Solicitud.estado.asc())
        .all()
    )
    for row in rows or []:
        estado = str(getattr(row, "estado", "") or row[0] or "").strip().lower()
        if estado:
            return estado
    return ""


def _sample_triage_value() -> str:
    try:
        options = admin_routes._solicitudes_triage_options_sql(
            base_query=Solicitud.query,
            selected="",
            now_dt=admin_routes.utc_now_naive(),
            today_rd=admin_routes.rd_today(),
        )
    except Exception:
        options = []
    for option in options or []:
        code = str((option or {}).get("code", "") or "").strip().lower()
        count = int((option or {}).get("count", 0) or 0)
        if code and code != "todas" and count > 0:
            return code
    for code in admin_routes._SOLICITUDES_TRIAGE_CODES:
        if code and code != "todas":
            return str(code)
    return ""


def _pick_scenarios(per_page: int) -> list[dict[str, Any]]:
    q_value = _sample_q_value()
    estado_value = _sample_estado_value()
    triage_value = _sample_triage_value()

    scenarios = [
        {
            "name": "full_no_filters",
            "label": "full sin filtros",
            "params": {"page": 1, "per_page": per_page},
            "is_async": False,
        },
        {
            "name": "async_no_filters",
            "label": "async sin filtros",
            "params": {"page": 1, "per_page": per_page},
            "is_async": True,
        },
    ]
    if q_value:
        scenarios.extend(
            [
                {
                    "name": "full_q",
                    "label": f"full con q={q_value}",
                    "params": {"page": 1, "per_page": per_page, "q": q_value},
                    "is_async": False,
                },
                {
                    "name": "async_q",
                    "label": f"async con q={q_value}",
                    "params": {"page": 1, "per_page": per_page, "q": q_value},
                    "is_async": True,
                },
            ]
        )
    if estado_value:
        scenarios.append(
            {
                "name": "full_estado",
                "label": f"full con estado={estado_value}",
                "params": {"page": 1, "per_page": per_page, "estado": estado_value},
                "is_async": False,
            }
        )
    if triage_value:
        scenarios.append(
            {
                "name": "full_triage",
                "label": f"full con triage={triage_value}",
                "params": {"page": 1, "per_page": per_page, "triage": triage_value},
                "is_async": False,
            }
        )
    return scenarios


def _block_summary(raw_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = sorted(
        {
            str(block_name)
            for run in (raw_runs or [])
            for block_name in ((run.get("blocks") or {}).keys())
        }
    )
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        merged_callsites: dict[str, dict[str, Any]] = {}
        for run in raw_runs:
            for item in ((run.get("blocks", {}).get(name, {}) or {}).get("callsites") or []):
                stack = str((item or {}).get("stack", "") or "").strip()
                if not stack:
                    continue
                row = merged_callsites.setdefault(
                    stack,
                    {
                        "stack": stack,
                        "db_queries_values": [],
                        "db_ms_values": [],
                        "sample_sql": str((item or {}).get("sample_sql", "") or ""),
                    },
                )
                row["db_queries_values"].append(int((item or {}).get("db_queries", 0) or 0))
                row["db_ms_values"].append(float((item or {}).get("db_ms", 0.0) or 0.0))
                if not row["sample_sql"]:
                    row["sample_sql"] = str((item or {}).get("sample_sql", "") or "")
        out[name] = {
            "calls": _mean_int([int((run.get("blocks", {}).get(name, {}) or {}).get("calls", 0) or 0) for run in raw_runs]),
            "wall_ms": _mean([float((run.get("blocks", {}).get(name, {}) or {}).get("wall_ms", 0.0) or 0.0) for run in raw_runs]),
            "db_ms": _mean([float((run.get("blocks", {}).get(name, {}) or {}).get("db_ms", 0.0) or 0.0) for run in raw_runs]),
            "db_queries": _mean_int([int((run.get("blocks", {}).get(name, {}) or {}).get("db_queries", 0) or 0) for run in raw_runs]),
            "callsites": sorted(
                [
                    {
                        "stack": str(item["stack"]),
                        "db_queries": _mean_int(list(item["db_queries_values"])),
                        "db_ms": _mean(list(item["db_ms_values"])),
                        "sample_sql": str(item["sample_sql"]),
                    }
                    for item in merged_callsites.values()
                ],
                key=lambda item: (-int(item["db_queries"]), -float(item["db_ms"]), str(item["stack"])),
            )[:12],
        }
    return out


def _top_block(blocks: dict[str, dict[str, Any]]) -> tuple[str, float]:
    winner_name = ""
    winner_ms = -1.0
    for name, data in (blocks or {}).items():
        wall_ms = float((data or {}).get("wall_ms", 0.0) or 0.0)
        if wall_ms > winner_ms:
            winner_name = str(name)
            winner_ms = wall_ms
    return winner_name, round(max(0.0, winner_ms), 2)


def _run_scenario(client, scenario: dict[str, Any], *, runs: int, warmup: int) -> dict[str, Any]:
    raw_runs: list[dict[str, Any]] = []
    for idx in range(max(0, warmup) + max(1, runs)):
        resp = client.get(
            "/admin/solicitudes",
            query_string=scenario["params"],
            headers=_scenario_headers(is_async=bool(scenario["is_async"])),
            follow_redirects=False,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Escenario {scenario['name']} devolvio status={resp.status_code} params={scenario['params']}"
            )
        detailed_raw = resp.headers.get(METRICS_HEADER) or "{}"
        detailed = json.loads(detailed_raw)
        run_payload = {
            "request_ms": float(resp.headers.get("X-P1C1-Perf-Latency-Ms", "0") or 0.0),
            "db_queries": int(resp.headers.get("X-P1C1-Perf-DB-Queries", "0") or 0),
            "db_ms": float(resp.headers.get("X-P1C1-Perf-DB-Time-Ms", "0") or 0.0),
            "html_bytes": int(resp.headers.get("X-P1C1-Perf-HTML-Bytes", "0") or 0),
            "blocks": dict((detailed or {}).get("blocks") or {}),
        }
        if idx >= max(0, warmup):
            raw_runs.append(run_payload)

    blocks = _block_summary(raw_runs)
    top_block_name, top_block_ms = _top_block(blocks)
    return {
        "name": scenario["name"],
        "label": scenario["label"],
        "params": dict(scenario["params"]),
        "is_async": bool(scenario["is_async"]),
        "runs": int(runs),
        "request_ms_avg": _mean([float(run["request_ms"]) for run in raw_runs]),
        "db_queries_avg": _mean_int([int(run["db_queries"]) for run in raw_runs]),
        "db_ms_avg": _mean([float(run["db_ms"]) for run in raw_runs]),
        "html_bytes_avg": _mean_int([int(run["html_bytes"]) for run in raw_runs]),
        "blocks": blocks,
        "top_block": {"name": top_block_name, "wall_ms": top_block_ms},
        "raw_runs": raw_runs,
    }


def _print_summary(results: list[dict[str, Any]]) -> None:
    print("=== FASE 7 /admin/solicitudes ===")
    for result in results:
        print(f"\n--- {result['label']} ---")
        print(
            json.dumps(
                {
                    "params": result["params"],
                    "async": result["is_async"],
                    "request_ms_avg": result["request_ms_avg"],
                    "db_queries_avg": result["db_queries_avg"],
                    "db_ms_avg": result["db_ms_avg"],
                    "html_bytes_avg": result["html_bytes_avg"],
                    "top_block": result["top_block"],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        for block_name, block in sorted((result.get("blocks") or {}).items()):
            print(
                json.dumps(
                    {
                        "block": block_name,
                        "calls": block["calls"],
                        "wall_ms": block["wall_ms"],
                        "db_ms": block["db_ms"],
                        "db_queries": block["db_queries"],
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            for callsite in (block.get("callsites") or [])[:8]:
                print(
                    json.dumps(
                        {
                            "block": block_name,
                            "callsite": callsite["stack"],
                            "db_queries": callsite["db_queries"],
                            "db_ms": callsite["db_ms"],
                            "sample_sql": callsite["sample_sql"],
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mide /admin/solicitudes con instrumentacion local opt-in.")
    parser.add_argument("--runs", type=int, default=3, help="Corridas por escenario luego del warmup.")
    parser.add_argument("--warmup", type=int, default=1, help="Corridas de calentamiento por escenario.")
    parser.add_argument("--per-page", type=int, default=25, help="per_page usado en los escenarios.")
    parser.add_argument("--json-out", default="", help="Archivo opcional para guardar el reporte JSON.")
    args = parser.parse_args()

    app_env = str(os.getenv("APP_ENV", "") or "").strip().lower()
    if app_env not in {"local", "test"}:
        raise RuntimeError("Esta medicion solo se permite con APP_ENV=local o APP_ENV=test.")

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    scenarios: list[dict[str, Any]] = []
    with flask_app.app_context():
        scenarios = _pick_scenarios(per_page=max(10, min(int(args.per_page), 200)))

    with flask_app.test_client() as client:
        _login(client)
        results = [_run_scenario(client, scenario, runs=int(args.runs), warmup=int(args.warmup)) for scenario in scenarios]

    report = {
        "app_env": app_env,
        "runs": int(args.runs),
        "warmup": int(args.warmup),
        "scenario_count": len(results),
        "results": results,
    }
    _print_summary(results)
    if args.json_out:
        out_path = Path(str(args.json_out)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
        print(f"\nJSON guardado en: {out_path}")


if __name__ == "__main__":
    main()
