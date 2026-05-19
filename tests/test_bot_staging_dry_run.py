from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local" / "staging_dry_run_startup.py"


SAFE_ENV_LINES = [
    "APP_ENV=staging",
    "WHATSAPP_ENABLED=false",
    "BOT_DRY_RUN=true",
    "BOT_AUTOREPLY_ENABLED=false",
    "BOT_AI_ENABLED=false",
    "BOT_PROTOCOL_AUTO_ADVANCE_ENABLED=false",
    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false",
    "DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db_name>",
]


def _run(env_file: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_script_startup_ok_with_safe_env():
    proc = _run(ROOT / ".env.staging.example")
    assert proc.returncode == 0
    assert "STAGING_DRY_RUN_STARTUP: OK" in proc.stdout


def test_script_detects_dangerous_flag_on(tmp_path: Path):
    env_file = tmp_path / ".env.staging.example"
    env_file.write_text("\n".join([*SAFE_ENV_LINES, "WHATSAPP_ENABLED=true"]), encoding="utf-8")

    proc = _run(env_file)
    assert proc.returncode == 1
    assert "Dangerous flag enabled in env file: WHATSAPP_ENABLED=true" in proc.stdout


def test_script_detects_invalid_app_env(tmp_path: Path):
    env_file = tmp_path / ".env.staging.example"
    lines = [line for line in SAFE_ENV_LINES if not line.startswith("APP_ENV=")]
    env_file.write_text("\n".join(["APP_ENV=production", *lines]), encoding="utf-8")

    proc = _run(env_file)
    assert proc.returncode == 1
    assert "Invalid APP_ENV in env file" in proc.stdout


def test_script_detects_non_local_db_in_env_file(tmp_path: Path):
    env_file = tmp_path / ".env.staging.example"
    lines = [line for line in SAFE_ENV_LINES if not line.startswith("DATABASE_URL=")]
    env_file.write_text(
        "\n".join([*lines, "DATABASE_URL=postgresql://user:pass@prod-db.example.com:5432/prod"]),
        encoding="utf-8",
    )

    proc = _run(env_file)
    assert proc.returncode == 1
    assert "DATABASE_URL in env file points to non-local/non-placeholder host" in proc.stdout


def test_script_health_route_and_startup_context_reported():
    proc = _run(ROOT / ".env.staging.example")
    assert proc.returncode == 0
    assert "Startup context OK" in proc.stdout
    assert "Critical routes reachable" in proc.stdout
