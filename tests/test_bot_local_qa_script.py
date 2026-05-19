from __future__ import annotations

from scripts.local.run_bot_local_qa import build_steps, run_local_qa


def test_order_of_steps_default() -> None:
    steps = build_steps(skip_suite=False, skip_simulator=False, fast=False)
    names = [s.name for s in steps]
    assert names == [
        "1) Suite combinada bot segura",
        "2) Simulador local",
        "3) Baseline checker",
        "4) Regression checker",
        "5) Coverage analyzer",
    ]


def test_stop_on_first_failure(monkeypatch) -> None:
    executed: list[str] = []

    def fake_run_step(step, *, cwd=None):
        executed.append(step.name)
        if step.name.startswith("2)"):
            return 2
        return 0

    monkeypatch.setattr("scripts.local.run_bot_local_qa.run_step", fake_run_step)
    rc = run_local_qa(skip_suite=False, skip_simulator=False, fast=False)

    assert rc == 2
    assert executed == [
        "1) Suite combinada bot segura",
        "2) Simulador local",
    ]


def test_skip_flags_apply() -> None:
    steps = build_steps(skip_suite=True, skip_simulator=True, fast=False)
    names = [s.name for s in steps]
    assert names == [
        "3) Baseline checker",
        "4) Regression checker",
        "5) Coverage analyzer",
    ]


def test_fast_skips_suite_only() -> None:
    steps = build_steps(skip_suite=False, skip_simulator=False, fast=True)
    names = [s.name for s in steps]
    assert names == [
        "2) Simulador local",
        "3) Baseline checker",
        "4) Regression checker",
        "5) Coverage analyzer",
    ]


def test_safe_env_applied_and_not_production() -> None:
    steps = build_steps(skip_suite=False, skip_simulator=False, fast=False)

    suite = steps[0]
    sim = steps[1]

    assert suite.env_updates["APP_ENV"] == "development"
    assert suite.env_updates["WHATSAPP_ENABLED"] == "false"
    assert suite.env_updates["BOT_DRY_RUN"] == "true"
    assert suite.env_updates["BOT_AUTOREPLY_ENABLED"] == "false"
    assert suite.env_updates["BOT_AI_ENABLED"] == "false"
    assert suite.env_updates["BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL"] == "false"

    assert sim.env_updates["APP_ENV"] == "test"
    assert sim.env_updates["DATABASE_URL_TEST"] == "sqlite:////private/tmp/bot_local_simulator.db"
    assert sim.env_updates["WHATSAPP_ENABLED"] == "false"
    assert sim.env_updates["BOT_DRY_RUN"] == "true"
    assert sim.env_updates["BOT_AUTOREPLY_ENABLED"] == "false"
    assert sim.env_updates["BOT_AI_ENABLED"] == "false"
    assert sim.env_updates["BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL"] == "false"

    assert suite.env_updates["APP_ENV"] != "production"
    assert sim.env_updates["APP_ENV"] != "production"
