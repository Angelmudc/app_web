#!/usr/bin/env python3
"""Staging dry-run startup validation (local-only, safe mode).

- Loads .env.staging.example
- Enforces safe runtime flags
- Boots Flask app in controlled mode
- Validates critical routes and guard-rails snapshot

This script does not deploy, does not run workers, and does not perform DB writes.
"""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys
from typing import Dict
from urllib.parse import urlparse


REQUIRED_SAFE_FLAGS = {
    "WHATSAPP_ENABLED": "false",
    "BOT_DRY_RUN": "true",
    "BOT_AUTOREPLY_ENABLED": "false",
    "BOT_AI_ENABLED": "false",
    "BOT_PROTOCOL_AUTO_ADVANCE_ENABLED": "false",
    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL": "false",
}

DANGEROUS_TRUE_FLAGS = {
    "WHATSAPP_ENABLED",
    "BOT_AUTOREPLY_ENABLED",
    "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL",
}

REQUIRED_IMPORTS = [
    "admin.bot_routes",
    "services.bot_inbound_pipeline_service",
    "services.bot_candidate_creation_service",
    "services.whatsapp_cloud_service",
    "services.environment_guard_service",
]

REQUIRED_ROUTES = [
    "/admin/bot/health",
    "/admin/bot/conversaciones",
    "/admin/bot/configuracion",
]


def _load_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def _is_local_or_placeholder_db(url: str) -> bool:
    value = (url or "").strip()
    if not value:
        return False
    low = value.lower()
    if low.startswith("sqlite:"):
        return True
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "<host>"}


def _validate_env_config(env_data: Dict[str, str]) -> list[str]:
    errors: list[str] = []

    if env_data.get("APP_ENV") != "staging":
        errors.append(f"Invalid APP_ENV in env file: expected 'staging', got '{env_data.get('APP_ENV')}'")

    for key, expected in REQUIRED_SAFE_FLAGS.items():
        got = env_data.get(key)
        if got != expected:
            errors.append(f"Invalid {key}: expected '{expected}', got '{got}'")

    for key in DANGEROUS_TRUE_FLAGS:
        if (env_data.get(key) or "").strip().lower() == "true":
            errors.append(f"Dangerous flag enabled in env file: {key}=true")

    db_url = env_data.get("DATABASE_URL", "")
    if db_url and not _is_local_or_placeholder_db(db_url):
        errors.append("DATABASE_URL in env file points to non-local/non-placeholder host")

    return errors


def run_dry_run(env_file: Path) -> tuple[bool, list[str]]:
    notes: list[str] = []
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    if not env_file.exists():
        return False, [f"Missing env file: {env_file}"]

    env_data = _load_env_file(env_file)
    config_errors = _validate_env_config(env_data)
    if config_errors:
        return False, config_errors

    # Runtime compatibility: app currently expects local/development/test/production.
    # We simulate staging with development runtime while preserving staging-safe flags.
    os.environ["APP_ENV"] = "development"
    for key, value in REQUIRED_SAFE_FLAGS.items():
        os.environ[key] = value

    # Enforce local-only DB for dry-run startup to avoid any remote database access.
    os.environ["DATABASE_URL_LOCAL"] = "sqlite:////tmp/app_web_staging_dry_run.sqlite"

    notes.append("Runtime APP_ENV forced to development for startup compatibility")
    notes.append("Runtime DATABASE_URL_LOCAL forced to sqlite local file")

    for mod in REQUIRED_IMPORTS:
        importlib.import_module(mod)

    from config_app import create_app

    app = create_app()
    with app.app_context():
        from services.environment_guard_service import get_sensitive_flags_snapshot

        snapshot = get_sensitive_flags_snapshot()
        if snapshot.get("whatsapp_enabled") is not False:
            return False, ["Guard-rail snapshot shows whatsapp_enabled != false"]
        if snapshot.get("bot_autoreply_enabled") is not False:
            return False, ["Guard-rail snapshot shows bot_autoreply_enabled != false"]
        if snapshot.get("bot_ai_enabled") is not False:
            return False, ["Guard-rail snapshot shows bot_ai_enabled != false"]
        if snapshot.get("real_creation_allowed") is not False:
            return False, ["Guard-rail snapshot shows real_creation_allowed != false"]

    client = app.test_client()
    for route in REQUIRED_ROUTES:
        resp = client.get(route, follow_redirects=False)
        if resp.status_code not in (200, 302, 401, 403):
            return False, [f"Unexpected status for {route}: {resp.status_code}"]

    notes.append("Startup context OK")
    notes.append("Critical routes reachable (200/302/401/403)")
    return True, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Staging dry-run startup checker (safe/local-only).")
    parser.add_argument("--env-file", default=".env.staging.example", help="Path to staging env example.")
    args = parser.parse_args()

    ok, details = run_dry_run(Path(args.env_file))
    if not ok:
        print("STAGING_DRY_RUN_STARTUP: FAIL")
        for item in details:
            print(f"- {item}")
        return 1

    print("STAGING_DRY_RUN_STARTUP: OK")
    for item in details:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
