# -*- coding: utf-8 -*-

from datetime import datetime, timezone
import importlib.util
from pathlib import Path


def _load_runner_module():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "local_registro_publico_stress.py"
    spec = importlib.util.spec_from_file_location("local_registro_publico_stress", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_stress_prefix_manual_tiene_prioridad():
    mod = _load_runner_module()
    prefix = mod._resolve_prefix("QA_TEAM", "MAY05")
    assert prefix == "QA_TEAM"
    assert mod._marker(prefix, 2) == "QA_TEAM-0002"


def test_stress_prefix_fallback_automatico():
    mod = _load_runner_module()
    now = datetime(2026, 5, 6, 12, 30, 45, tzinfo=timezone.utc)
    prefix = mod._resolve_prefix(None, None, now=now)
    assert prefix == "RUN20260506123045"


def test_ids_generados_no_colisionan_entre_runs_distintas():
    mod = _load_runner_module()
    ns_a = "LOADTEST1:T120001"
    ns_b = "LOADTEST1:T120002"

    ced_a = mod._cedula_from_index(ns_a, 1)
    ced_b = mod._cedula_from_index(ns_b, 1)
    tel_a = mod._telefono_from_index(ns_a, 1)
    tel_b = mod._telefono_from_index(ns_b, 1)

    assert ced_a != ced_b
    assert tel_a != tel_b
