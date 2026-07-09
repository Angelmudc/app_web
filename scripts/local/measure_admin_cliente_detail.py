#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Medicion local, solo lectura, del costo real del detalle de cliente.

Uso recomendado:
  APP_ENV=local venv/bin/python scripts/local/measure_admin_cliente_detail.py
  APP_ENV=local venv/bin/python scripts/local/measure_admin_cliente_detail.py --runs 5 --warmup 1
  APP_ENV=local venv/bin/python scripts/local/measure_admin_cliente_detail.py --json-out /tmp/admin_cliente_detail_metrics.json
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

from sqlalchemy import func

from app import app as flask_app
from config_app import db
from models import Cliente, Solicitud


MEASURE_HEADER = "X-Admin-Cliente-Detail-Measure"
METRICS_HEADER = "X-Admin-Cliente-Detail-Metrics"


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


def _headers() -> dict[str, str]:
    return {MEASURE_HEADER: "1"}


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


def _pick_client_rows() -> list[dict[str, Any]]:
    rows = (
        db.session.query(
            Cliente.id.label("cliente_id"),
            Cliente.codigo.label("codigo"),
            Cliente.nombre_completo.label("nombre_completo"),
            func.count(Solicitud.id).label("solicitudes_count"),
        )
        .outerjoin(Solicitud, Solicitud.cliente_id == Cliente.id)
        .group_by(Cliente.id, Cliente.codigo, Cliente.nombre_completo)
        .having(func.count(Solicitud.id) > 0)
        .order_by(func.count(Solicitud.id).asc(), Cliente.id.asc())
        .all()
    )
    return [
        {
            "cliente_id": int(getattr(row, "cliente_id", 0) or 0),
            "codigo": str(getattr(row, "codigo", "") or ""),
            "nombre_completo": str(getattr(row, "nombre_completo", "") or ""),
            "solicitudes_count": int(getattr(row, "solicitudes_count", 0) or 0),
        }
        for row in (rows or [])
        if int(getattr(row, "cliente_id", 0) or 0) > 0
    ]


def _pick_clients(*, small_id: int = 0, medium_id: int = 0, heavy_id: int = 0) -> list[dict[str, Any]]:
    rows = _pick_client_rows()
    if not rows:
        raise RuntimeError("No hay clientes con solicitudes para medir.")

    rows_by_id = {int(row["cliente_id"]): row for row in rows}

    chosen: list[dict[str, Any]] = []
    used_ids: set[int] = set()

    def _take(label: str, preferred_id: int | None, fallback_index: int) -> None:
        row = None
        if preferred_id:
            row = rows_by_id.get(int(preferred_id))
            if row is None:
                raise RuntimeError(f"Cliente {preferred_id} no existe o no tiene solicitudes.")
        else:
            idx = max(0, min(int(fallback_index), len(rows) - 1))
            row = rows[idx]
            if int(row["cliente_id"]) in used_ids:
                for candidate in rows:
                    if int(candidate["cliente_id"]) not in used_ids:
                        row = candidate
                        break
        if row is None:
            raise RuntimeError(f"No se pudo elegir cliente {label}.")
        out = dict(row)
        out["tier"] = label
        chosen.append(out)
        used_ids.add(int(out["cliente_id"]))

    _take("small", small_id or None, 0)
    _take("medium", medium_id or None, len(rows) // 2)
    _take("heavy", heavy_id or None, len(rows) - 1)
    return chosen


def _scenario_defs(cliente_id: int) -> list[dict[str, Any]]:
    return [
        {
            "name": "full_detail",
            "label": "full detail",
            "path": f"/admin/clientes/{cliente_id}",
        },
        {
            "name": "summary_fragment",
            "label": "summary fragment",
            "path": f"/admin/clientes/{cliente_id}/_summary",
        },
        {
            "name": "solicitudes_fragment",
            "label": "solicitudes fragment",
            "path": f"/admin/clientes/{cliente_id}/_solicitudes",
        },
    ]


def _merge_block_summary(raw_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = sorted({name for run in raw_runs for name in (run.get("blocks") or {}).keys()})
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        out[name] = {
            "calls": _mean_int([int(((run.get("blocks") or {}).get(name) or {}).get("calls", 0) or 0) for run in raw_runs]),
            "wall_ms": _mean([float(((run.get("blocks") or {}).get(name) or {}).get("wall_ms", 0.0) or 0.0) for run in raw_runs]),
            "db_ms": _mean([float(((run.get("blocks") or {}).get(name) or {}).get("db_ms", 0.0) or 0.0) for run in raw_runs]),
            "db_queries": _mean_int([int(((run.get("blocks") or {}).get(name) or {}).get("db_queries", 0) or 0) for run in raw_runs]),
        }
    return out


def _merge_table_summary(raw_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = sorted({name for run in raw_runs for name in (run.get("tables") or {}).keys()})
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        out[name] = {
            "queries": _mean_int([int(((run.get("tables") or {}).get(name) or {}).get("queries", 0) or 0) for run in raw_runs]),
            "db_ms": _mean([float(((run.get("tables") or {}).get(name) or {}).get("db_ms", 0.0) or 0.0) for run in raw_runs]),
            "sample_sql": str(next((((run.get("tables") or {}).get(name) or {}).get("sample_sql", "") for run in raw_runs if ((run.get("tables") or {}).get(name) or {}).get("sample_sql")), "")),
        }
    return out


def _merge_fingerprint_summary(raw_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for run in raw_runs:
        for item in (run.get("top_fingerprints") or []):
            fingerprint = str((item or {}).get("fingerprint", "") or "")
            if not fingerprint:
                continue
            row = merged.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "queries_values": [],
                    "db_ms_values": [],
                    "template_render_queries_values": [],
                    "tables": list((item or {}).get("tables") or []),
                    "sample_sql": str((item or {}).get("sample_sql", "") or ""),
                },
            )
            row["queries_values"].append(int((item or {}).get("queries", 0) or 0))
            row["db_ms_values"].append(float((item or {}).get("db_ms", 0.0) or 0.0))
            row["template_render_queries_values"].append(int((item or {}).get("template_render_queries", 0) or 0))
            if not row["sample_sql"]:
                row["sample_sql"] = str((item or {}).get("sample_sql", "") or "")
    return sorted(
        [
            {
                "fingerprint": row["fingerprint"],
                "queries": _mean_int(row["queries_values"]),
                "db_ms": _mean(row["db_ms_values"]),
                "template_render_queries": _mean_int(row["template_render_queries_values"]),
                "tables": list(row["tables"]),
                "sample_sql": str(row["sample_sql"]),
            }
            for row in merged.values()
        ],
        key=lambda item: (-int(item["queries"]), -float(item["db_ms"]), str(item["fingerprint"])),
    )[:12]


def _merge_lazy_summary(raw_runs: list[dict[str, Any]]) -> dict[str, Any]:
    merged_callsites: dict[str, dict[str, Any]] = {}
    merged_accesses: dict[str, dict[str, Any]] = {}
    for run in raw_runs:
        lazy = dict(run.get("lazy_loads") or {})
        for item in (lazy.get("top_callsites") or []):
            stack = str((item or {}).get("stack", "") or "")
            if not stack:
                continue
            row = merged_callsites.setdefault(
                stack,
                {
                    "stack": stack,
                    "queries_values": [],
                    "db_ms_values": [],
                    "tables": list((item or {}).get("tables") or []),
                    "sample_sql": str((item or {}).get("sample_sql", "") or ""),
                },
            )
            row["queries_values"].append(int((item or {}).get("queries", 0) or 0))
            row["db_ms_values"].append(float((item or {}).get("db_ms", 0.0) or 0.0))
            if not row["sample_sql"]:
                row["sample_sql"] = str((item or {}).get("sample_sql", "") or "")
        for item in (lazy.get("top_accesses") or []):
            template = str((item or {}).get("template", "") or "")
            line = int((item or {}).get("line", 0) or 0)
            attr = str((item or {}).get("attr", "") or "")
            fingerprint = str((item or {}).get("fingerprint", "") or "")
            if not (template or attr or fingerprint):
                continue
            key = "|".join([template, str(line), attr, fingerprint])
            row = merged_accesses.setdefault(
                key,
                {
                    "template": template,
                    "line": line,
                    "attr": attr,
                    "kind": str((item or {}).get("kind", "") or ""),
                    "entity": str((item or {}).get("entity", "") or ""),
                    "fingerprint": fingerprint,
                    "queries_values": [],
                    "db_ms_values": [],
                    "sample_sql": str((item or {}).get("sample_sql", "") or ""),
                    "path": str((item or {}).get("path", "") or ""),
                },
            )
            row["queries_values"].append(int((item or {}).get("queries", 0) or 0))
            row["db_ms_values"].append(float((item or {}).get("db_ms", 0.0) or 0.0))
            if not row["sample_sql"]:
                row["sample_sql"] = str((item or {}).get("sample_sql", "") or "")
    return {
        "query_count": _mean_int([int((run.get("lazy_loads") or {}).get("query_count", 0) or 0) for run in raw_runs]),
        "db_ms": _mean([float((run.get("lazy_loads") or {}).get("db_ms", 0.0) or 0.0) for run in raw_runs]),
        "top_callsites": sorted(
            [
                {
                    "stack": row["stack"],
                    "queries": _mean_int(row["queries_values"]),
                    "db_ms": _mean(row["db_ms_values"]),
                    "tables": list(row["tables"]),
                    "sample_sql": str(row["sample_sql"]),
                }
                for row in merged_callsites.values()
            ],
            key=lambda item: (-int(item["queries"]), -float(item["db_ms"]), str(item["stack"])),
        )[:8],
        "top_accesses": sorted(
            [
                {
                    "template": row["template"],
                    "line": int(row["line"]),
                    "attr": row["attr"],
                    "kind": row["kind"],
                    "entity": row["entity"],
                    "fingerprint": row["fingerprint"],
                    "queries": _mean_int(row["queries_values"]),
                    "db_ms": _mean(row["db_ms_values"]),
                    "sample_sql": str(row["sample_sql"]),
                    "path": str(row["path"]),
                }
                for row in merged_accesses.values()
            ],
            key=lambda item: (-int(item["queries"]), -float(item["db_ms"]), str(item["template"]), int(item["line"]), str(item["attr"])),
        )[:12],
    }


def _payment_cycle_update_summary(raw_runs: list[dict[str, Any]]) -> dict[str, Any]:
    updates_per_run: list[int] = []
    fingerprints: dict[str, dict[str, Any]] = {}
    for run in (raw_runs or []):
        run_count = 0
        for item in (run.get("top_fingerprints") or []):
            fingerprint = str((item or {}).get("fingerprint", "") or "")
            if not fingerprint:
                continue
            normalized = " ".join(fingerprint.split()).lower()
            if not normalized.startswith("update solicitudes set"):
                continue
            if "payment_cycle_" not in normalized:
                continue
            queries = int((item or {}).get("queries", 0) or 0)
            run_count += queries
            row = fingerprints.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "queries_values": [],
                    "db_ms_values": [],
                    "sample_sql": str((item or {}).get("sample_sql", "") or ""),
                },
            )
            row["queries_values"].append(queries)
            row["db_ms_values"].append(float((item or {}).get("db_ms", 0.0) or 0.0))
            if not row["sample_sql"]:
                row["sample_sql"] = str((item or {}).get("sample_sql", "") or "")
        updates_per_run.append(run_count)

    return {
        "count_avg": _mean_int(updates_per_run),
        "fingerprints": sorted(
            [
                {
                    "fingerprint": row["fingerprint"],
                    "queries": _mean_int(row["queries_values"]),
                    "db_ms": _mean(row["db_ms_values"]),
                    "sample_sql": str(row["sample_sql"]),
                }
                for row in fingerprints.values()
            ],
            key=lambda item: (-int(item["queries"]), -float(item["db_ms"]), str(item["fingerprint"])),
        )[:10],
    }


def _run_scenario(client, scenario: dict[str, Any], *, runs: int, warmup: int) -> dict[str, Any]:
    raw_runs: list[dict[str, Any]] = []
    for idx in range(max(0, warmup) + max(1, runs)):
        resp = client.get(
            scenario["path"],
            headers=_headers(),
            follow_redirects=False,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Escenario {scenario['name']} devolvio status={resp.status_code} path={scenario['path']}")
        detailed = json.loads(resp.headers.get(METRICS_HEADER) or "{}")
        run_payload = {
            "request_ms": float(resp.headers.get("X-P1C1-Perf-Latency-Ms", "0") or 0.0),
            "db_queries": int(resp.headers.get("X-P1C1-Perf-DB-Queries", "0") or 0),
            "db_ms": float(resp.headers.get("X-P1C1-Perf-DB-Time-Ms", "0") or 0.0),
            "html_bytes": int(resp.headers.get("X-P1C1-Perf-HTML-Bytes", "0") or 0),
            "blocks": dict((detailed or {}).get("blocks") or {}),
            "tables": dict((detailed or {}).get("tables") or {}),
            "top_fingerprints": list((detailed or {}).get("top_fingerprints") or []),
            "lazy_loads": dict((detailed or {}).get("lazy_loads") or {}),
        }
        if idx >= max(0, warmup):
            raw_runs.append(run_payload)

    return {
        "name": scenario["name"],
        "label": scenario["label"],
        "path": scenario["path"],
        "runs": int(runs),
        "request_ms_avg": _mean([float(run["request_ms"]) for run in raw_runs]),
        "db_queries_avg": _mean_int([int(run["db_queries"]) for run in raw_runs]),
        "db_ms_avg": _mean([float(run["db_ms"]) for run in raw_runs]),
        "html_bytes_avg": _mean_int([int(run["html_bytes"]) for run in raw_runs]),
        "blocks": _merge_block_summary(raw_runs),
        "tables": _merge_table_summary(raw_runs),
        "top_fingerprints": _merge_fingerprint_summary(raw_runs),
        "payment_cycle_updates": _payment_cycle_update_summary(raw_runs),
        "lazy_loads": _merge_lazy_summary(raw_runs),
        "raw_runs": raw_runs,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("=== FASE 15 detalle de cliente ===")
    for cliente in (report.get("clients") or []):
        meta = dict(cliente.get("client") or {})
        print(
            json.dumps(
                {
                    "tier": cliente.get("tier"),
                    "cliente_id": meta.get("cliente_id"),
                    "codigo": meta.get("codigo"),
                    "nombre_completo": meta.get("nombre_completo"),
                    "solicitudes_count": meta.get("solicitudes_count"),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        for result in (cliente.get("results") or []):
            print(
                json.dumps(
                    {
                        "tier": cliente.get("tier"),
                        "endpoint": result.get("label"),
                        "path": result.get("path"),
                        "request_ms_avg": result.get("request_ms_avg"),
                        "db_queries_avg": result.get("db_queries_avg"),
                        "db_ms_avg": result.get("db_ms_avg"),
                        "payment_cycle_updates_avg": ((result.get("payment_cycle_updates") or {}).get("count_avg") or 0),
                        "lazy_query_count_avg": (result.get("lazy_loads") or {}).get("query_count"),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            for item in ((result.get("payment_cycle_updates") or {}).get("fingerprints") or []):
                print(
                    json.dumps(
                        {
                            "tier": cliente.get("tier"),
                            "endpoint": result.get("label"),
                            "payment_cycle_update_fingerprint": item.get("fingerprint"),
                            "queries": item.get("queries"),
                            "db_ms": item.get("db_ms"),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            lazy = dict(result.get("lazy_loads") or {})
            for item in (lazy.get("top_accesses") or []):
                print(
                    json.dumps(
                        {
                            "tier": cliente.get("tier"),
                            "endpoint": result.get("label"),
                            "template": item.get("template"),
                            "line": item.get("line"),
                            "attr": item.get("attr"),
                            "kind": item.get("kind"),
                            "entity": item.get("entity"),
                            "fingerprint": item.get("fingerprint"),
                            "queries": item.get("queries"),
                            "db_ms": item.get("db_ms"),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mide el detalle de cliente con instrumentacion local opt-in.")
    parser.add_argument("--runs", type=int, default=3, help="Corridas por escenario luego del warmup.")
    parser.add_argument("--warmup", type=int, default=1, help="Corridas de calentamiento por escenario.")
    parser.add_argument("--cliente-small-id", type=int, default=0, help="Override opcional para cliente pequeno.")
    parser.add_argument("--cliente-medium-id", type=int, default=0, help="Override opcional para cliente mediano.")
    parser.add_argument("--cliente-heavy-id", type=int, default=0, help="Override opcional para cliente pesado.")
    parser.add_argument("--json-out", default="", help="Archivo opcional para guardar el reporte JSON.")
    args = parser.parse_args()

    app_env = str(os.getenv("APP_ENV", "") or "").strip().lower()
    if app_env not in {"local", "test"}:
        raise RuntimeError("Esta medicion solo se permite con APP_ENV=local o APP_ENV=test.")

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        clients = _pick_clients(
            small_id=int(args.cliente_small_id),
            medium_id=int(args.cliente_medium_id),
            heavy_id=int(args.cliente_heavy_id),
        )

    report_clients: list[dict[str, Any]] = []
    with flask_app.test_client() as client:
        _login(client)
        for client_meta in clients:
            scenarios = _scenario_defs(int(client_meta["cliente_id"]))
            results = [_run_scenario(client, scenario, runs=int(args.runs), warmup=int(args.warmup)) for scenario in scenarios]
            report_clients.append(
                {
                    "tier": client_meta["tier"],
                    "client": client_meta,
                    "results": results,
                }
            )

    report = {
        "app_env": app_env,
        "runs": int(args.runs),
        "warmup": int(args.warmup),
        "clients": report_clients,
    }
    _print_summary(report)
    if args.json_out:
        out_path = Path(str(args.json_out)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
        print(f"\nJSON guardado en: {out_path}")


if __name__ == "__main__":
    main()
