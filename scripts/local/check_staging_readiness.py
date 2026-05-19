#!/usr/bin/env python3
"""Local staging-readiness checker (documentation/config only).

This script validates documentation and safe staging example config.
It does not connect to DB and does not perform any deploy action.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

REQUIRED_FLAGS = {
    "APP_ENV": "staging",
    "WHATSAPP_ENABLED": "false",
    "BOT_DRY_RUN": "true",
    "BOT_AUTOREPLY_ENABLED": "false",
    "BOT_AI_ENABLED": "false",
    "BOT_PROTOCOL_AUTO_ADVANCE_ENABLED": "false",
    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false",
    "BOT_AI_DAILY_REQUEST_LIMIT": "5",
    "BOT_AI_SESSION_REQUEST_LIMIT": "5",
    "BOT_AI_EVAL_MAX_CASES": "5",
}

DANGEROUS_TRUE_FLAGS = {
    "WHATSAPP_ENABLED",
    "BOT_AUTOREPLY_ENABLED",
    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL",
}

SECRET_KEYS = {
    "SECRET_KEY",
    "DATABASE_URL",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "OPENAI_API_KEY",
}

PLACEHOLDER_PATTERNS = (
    "<replace-me>",
    "<user>",
    "<password>",
    "<host>",
    "<db_name>",
)


def _load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip()
    return data


def _is_placeholder_secret(value: str) -> bool:
    if any(token in value for token in PLACEHOLDER_PATTERNS):
        return True
    if value.lower() in {"", "changeme", "replace_me", "replace-me", "example", "dummy"}:
        return True
    return False


def run_checks(env_file: Path, plan_file: Path) -> list[str]:
    errors: list[str] = []

    if not plan_file.exists():
        errors.append(f"Missing required document: {plan_file}")
        return errors

    plan_text = plan_file.read_text(encoding="utf-8")
    if "/admin/bot/health" not in plan_text:
        errors.append("Staging plan does not document /admin/bot/health")
    if "tests/test_bot_operational_hardening.py" not in plan_text:
        errors.append("Staging plan does not document checkpoint tests command")

    if not env_file.exists():
        errors.append(f"Missing required env example: {env_file}")
        return errors

    env_data = _load_env(env_file)

    for key, expected in REQUIRED_FLAGS.items():
        got = env_data.get(key)
        if got != expected:
            errors.append(f"Invalid {key}: expected '{expected}', got '{got}'")

    for key in DANGEROUS_TRUE_FLAGS:
        if env_data.get(key, "").lower() == "true":
            errors.append(f"Dangerous flag enabled: {key}=true")

    for key in SECRET_KEYS:
        value = env_data.get(key)
        if value is None:
            continue
        if not _is_placeholder_secret(value):
            if key == "DATABASE_URL":
                if re.search(r"@(localhost|127\.0\.0\.1|<host>)", value):
                    continue
            errors.append(f"Potential real secret in {key}; use placeholder values only")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check staging readiness docs/config (local-only).")
    parser.add_argument(
        "--env-file",
        default=".env.staging.example",
        help="Path to staging env example file.",
    )
    parser.add_argument(
        "--plan-file",
        default="docs/bot_staging_readiness_plan.md",
        help="Path to staging readiness plan markdown.",
    )
    args = parser.parse_args()

    env_file = Path(args.env_file)
    plan_file = Path(args.plan_file)
    errors = run_checks(env_file=env_file, plan_file=plan_file)

    if errors:
        print("STAGING_READINESS_CHECK: FAIL")
        for err in errors:
            print(f"- {err}")
        return 1

    print("STAGING_READINESS_CHECK: OK")
    print(f"- plan file: {plan_file}")
    print(f"- env file: {env_file}")
    print("- safe flags: validated")
    print("- secrets: placeholders only")
    print("- health/checkpoint docs: present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
