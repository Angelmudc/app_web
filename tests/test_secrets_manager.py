# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import pytest

from utils.secrets_manager import (
    SecretNotFoundError,
    get_required_secret,
    reset_secrets_manager_state,
)


def test_get_secret_loads_dotenv_in_dev(tmp_path, monkeypatch):
    dotenv_file = tmp_path / ".env.dev"
    dotenv_file.write_text("DEV_ONLY_SECRET=from-dotenv\n", encoding="utf-8")

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_DOTENV_PATH", str(dotenv_file))
    monkeypatch.delenv("DEV_ONLY_SECRET", raising=False)
    reset_secrets_manager_state()

    assert get_required_secret("DEV_ONLY_SECRET") == "from-dotenv"


def test_get_secret_reads_env_without_dotenv_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("APP_DOTENV_PATH", raising=False)
    reset_secrets_manager_state()

    assert get_required_secret("DATABASE_URL") == "sqlite:///:memory:"


def test_get_secret_fails_fast_when_required_is_missing(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("NON_EXISTENT_CRITICAL_SECRET", raising=False)
    reset_secrets_manager_state()

    with pytest.raises(SecretNotFoundError):
        get_required_secret("NON_EXISTENT_CRITICAL_SECRET")


def test_no_direct_getenv_for_sensitive_secrets():
    root = Path(__file__).resolve().parents[1]
    sensitive_names = {
        "FLASK_SECRET_KEY",
        "DATABASE_URL",
        "BREAKGLASS_PASSWORD_HASH",
        "STAFF_MFA_ENCRYPTION_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    }
    allowed = {
        root / "utils" / "secrets_manager.py",
        root / "tests" / "test_secrets_manager.py",
    }

    offenders: list[str] = []
    for py_file in root.rglob("*.py"):
        if py_file in allowed:
            continue
        rel = py_file.relative_to(root)
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for secret_name in sensitive_names:
            token = f'os.getenv("{secret_name}"'
            if token in content:
                offenders.append(f"{rel}:{secret_name}")

    assert offenders == [], f"Direct os.getenv for sensitive secrets found: {offenders}"
