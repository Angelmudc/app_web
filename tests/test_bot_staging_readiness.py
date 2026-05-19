from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.staging.example"
SCRIPT = ROOT / "scripts" / "local" / "check_staging_readiness.py"
PLAN = ROOT / "docs" / "bot_staging_readiness_plan.md"


def test_env_staging_example_exists():
    assert ENV_EXAMPLE.exists()


def test_env_staging_has_safe_flags_and_no_dangerous_true():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "APP_ENV=staging" in text
    assert "WHATSAPP_ENABLED=false" in text
    assert "BOT_DRY_RUN=true" in text
    assert "BOT_STAGING_MODE=true" in text
    assert "BOT_SANDBOX_MODE=true" in text
    assert "BOT_AUTOREPLY_ENABLED=false" in text
    assert "BOT_AI_ENABLED=false" in text
    assert "BOT_PROTOCOL_AUTO_ADVANCE_ENABLED=false" in text
    assert "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false" in text
    assert "BOT_AI_DAILY_REQUEST_LIMIT=5" in text
    assert "BOT_AI_SESSION_REQUEST_LIMIT=5" in text
    assert "BOT_AI_EVAL_MAX_CASES=5" in text
    assert "WHATSAPP_ENABLED=true" not in text
    assert "BOT_AUTOREPLY_ENABLED=true" not in text
    assert "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true" not in text


def test_env_staging_example_uses_placeholder_secrets():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=<replace-me>" in text
    assert "WHATSAPP_ACCESS_TOKEN=replace_me" in text
    assert "WHATSAPP_VERIFY_TOKEN=<replace-me>" in text
    assert "SECRET_KEY=<replace-me>" in text


def test_script_detects_dangerous_flag(tmp_path: Path):
    env_file = tmp_path / ".env.staging.example"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=staging",
                "WHATSAPP_ENABLED=true",
                "BOT_DRY_RUN=true",
                "BOT_AUTOREPLY_ENABLED=false",
                "BOT_AI_ENABLED=false",
                "BOT_PROTOCOL_AUTO_ADVANCE_ENABLED=false",
                "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false",
                "BOT_AI_DAILY_REQUEST_LIMIT=5",
                "BOT_AI_SESSION_REQUEST_LIMIT=5",
                "BOT_AI_EVAL_MAX_CASES=5",
                "DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db_name>",
                "SECRET_KEY=<replace-me>",
                "WHATSAPP_VERIFY_TOKEN=<replace-me>",
                "WHATSAPP_ACCESS_TOKEN=replace_me",
                "OPENAI_API_KEY=<replace-me>",
            ]
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--env-file",
            str(env_file),
            "--plan-file",
            str(PLAN),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "Dangerous flag enabled: WHATSAPP_ENABLED=true" in proc.stdout


def test_script_passes_with_safe_config():
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--env-file",
            str(ENV_EXAMPLE),
            "--plan-file",
            str(PLAN),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert "STAGING_READINESS_CHECK: OK" in proc.stdout
