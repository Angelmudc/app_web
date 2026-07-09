from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_BIN = str(ROOT_DIR / "venv" / "bin" / "python")

BOT_SUITE_TESTS: tuple[str, ...] = (
    "tests/test_bot_operational_hardening.py",
    "tests/test_bot_conversation_simulator.py",
    "tests/test_bot_simulator_coverage.py",
    "tests/test_bot_protocol_service.py",
    "tests/test_bot_phase1_services.py",
    "tests/test_bot_phase1_admin_routes.py",
    "tests/test_bot_phase2_whatsapp_integration.py",
    "tests/test_bot_phase3_identity_integration.py",
    "tests/test_bot_phase4_ai_controlled.py",
    "tests/test_bot_candidate_summary_service.py",
    "tests/test_bot_candidate_draft_service.py",
    "tests/test_bot_candidate_conversion_preview_service.py",
    "tests/test_bot_candidate_creation_service.py",
    "tests/test_bot_created_candidates_admin.py",
    "tests/test_bot_ai_eval_runner.py",
    "tests/test_bot_ai_local_script.py",
    "tests/test_bot_ai_provider_check.py",
)


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    env_updates: dict[str, str]


def _safe_env(extra: Mapping[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra)
    return env


def run_step(step: Step, *, cwd: Path = ROOT_DIR) -> int:
    print(f"\n=== {step.name} ===")
    print("$ " + " ".join(step.command))
    proc = subprocess.run(
        list(step.command),
        cwd=str(cwd),
        env=_safe_env(step.env_updates),
        check=False,
    )
    if proc.returncode != 0:
        print(f"{step.name}: FAIL (exit={proc.returncode})")
    else:
        print(f"{step.name}: OK")
    return int(proc.returncode)


def build_steps(*, skip_suite: bool, skip_simulator: bool, fast: bool) -> list[Step]:
    skip_suite_effective = skip_suite or fast

    steps: list[Step] = []

    if not skip_suite_effective:
        steps.append(
            Step(
                name="1) Suite combinada bot segura",
                command=(
                    PYTHON_BIN,
                    "-m",
                    "pytest",
                    "-q",
                    *BOT_SUITE_TESTS,
                ),
                env_updates={
                    "APP_ENV": "development",
                    "WHATSAPP_ENABLED": "false",
                    "BOT_DRY_RUN": "true",
                    "BOT_AUTOREPLY_ENABLED": "false",
                    "BOT_AI_ENABLED": "false",
                    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false",
                },
            )
        )

    if not skip_simulator:
        steps.append(
            Step(
                name="2) Simulador local",
                command=(
                    PYTHON_BIN,
                    "scripts/local/run_bot_conversation_simulator.py",
                    "--verbose",
                ),
                env_updates={
                    "APP_ENV": "test",
                    "DATABASE_URL_TEST": "sqlite:////private/tmp/bot_local_simulator.db",
                    "WHATSAPP_ENABLED": "false",
                    "BOT_DRY_RUN": "true",
                    "BOT_AUTOREPLY_ENABLED": "false",
                    "BOT_AI_ENABLED": "false",
                    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false",
                },
            )
        )

    steps.extend(
        [
            Step(
                name="3) Baseline checker",
                command=(PYTHON_BIN, "scripts/local/check_bot_simulator_baseline.py"),
                env_updates={"APP_ENV": "development"},
            ),
            Step(
                name="4) Regression checker",
                command=(PYTHON_BIN, "scripts/local/check_bot_simulator_regression.py"),
                env_updates={"APP_ENV": "development"},
            ),
            Step(
                name="5) Coverage analyzer",
                command=(PYTHON_BIN, "scripts/local/analyze_bot_simulator_coverage.py"),
                env_updates={"APP_ENV": "development"},
            ),
        ]
    )

    return steps


def run_local_qa(*, skip_suite: bool, skip_simulator: bool, fast: bool) -> int:
    steps = build_steps(skip_suite=skip_suite, skip_simulator=skip_simulator, fast=fast)

    print("BOT_LOCAL_QA: START")
    print(f"cwd={ROOT_DIR}")
    print(f"python={PYTHON_BIN}")

    for step in steps:
        rc = run_step(step)
        if rc != 0:
            print("BOT_LOCAL_QA: FAIL")
            return rc

    print("BOT_LOCAL_QA: OK")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comando único de QA local del bot")
    parser.add_argument("--skip-suite", action="store_true", help="Omite la suite combinada")
    parser.add_argument("--skip-simulator", action="store_true", help="Omite simulador local")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Modo rápido: omite suite combinada y ejecuta simulador + checkers",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_local_qa(
        skip_suite=bool(args.skip_suite),
        skip_simulator=bool(args.skip_simulator),
        fast=bool(args.fast),
    )


if __name__ == "__main__":
    raise SystemExit(main())
