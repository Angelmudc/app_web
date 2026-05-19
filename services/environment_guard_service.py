from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from flask import current_app


_LOCAL_ENVS = {"local", "development", "test", "testing"}
_TRUE_SET = {"1", "true", "yes", "on"}


class EnvironmentSafetyError(RuntimeError):
    """Error de seguridad operacional por entorno/flags peligrosas."""


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_SET


def _current_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()


def _current_db_url() -> str:
    try:
        return str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    except Exception:
        return str(os.getenv("DATABASE_URL_LOCAL") or os.getenv("DATABASE_URL_TEST") or os.getenv("DATABASE_URL") or "")


def _is_local_db_url(db_url: str | None) -> bool:
    raw = str(db_url or "").strip().lower()
    if not raw:
        return False
    if raw.startswith("sqlite:"):
        return True
    parsed = urlparse(raw)
    host = str(parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1"}


def mask_database_url(db_url: str | None) -> dict[str, str]:
    raw = str(db_url or "").strip()
    if not raw:
        return {
            "db_driver": "unknown",
            "db_host_type": "unknown",
            "db_name_masked": "***",
            "db_url_masked": "unknown://***",
        }
    low = raw.lower()
    if low.startswith("sqlite:"):
        return {
            "db_driver": "sqlite",
            "db_host_type": "sqlite",
            "db_name_masked": "sqlite",
            "db_url_masked": "sqlite:///***",
        }

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "postgresql").split("+", 1)[0]
    host = str(parsed.hostname or "").strip().lower()
    port = parsed.port
    path = str(parsed.path or "").strip("/")
    db_name = path.split("/")[-1] if path else ""
    db_name_masked = db_name if not db_name else (db_name[:24] if len(db_name) > 24 else db_name)
    if not db_name_masked:
        db_name_masked = "***"

    if host in {"localhost", "127.0.0.1"}:
        host_type = "local"
        host_label = host
        if port:
            host_label = f"{host_label}:{port}"
        url_masked = f"{scheme}://***@{host_label}/{db_name_masked}"
    elif host:
        host_type = "non_local"
        url_masked = f"{scheme}://***@non-local-host/***"
    else:
        host_type = "unknown"
        url_masked = f"{scheme}://***@unknown-host/***"

    return {
        "db_driver": scheme,
        "db_host_type": host_type,
        "db_name_masked": db_name_masked,
        "db_url_masked": url_masked,
    }


def is_local_environment() -> bool:
    return _current_env() in _LOCAL_ENVS


def is_safe_local_database() -> bool:
    return _is_local_db_url(_current_db_url())


def assert_local_safe_environment() -> None:
    if not is_local_environment():
        raise EnvironmentSafetyError("unsafe_environment_not_local")
    if not is_safe_local_database():
        raise EnvironmentSafetyError("unsafe_database_not_local")


def assert_real_creation_allowed() -> None:
    assert_local_safe_environment()
    if not _is_true(os.getenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")):
        raise EnvironmentSafetyError("real_creation_flag_disabled")


def get_sensitive_flags_snapshot() -> dict[str, Any]:
    env = _current_env()
    db_url = _current_db_url()
    whatsapp_enabled = _is_true(os.getenv("WHATSAPP_ENABLED", "false"))
    bot_dry_run = _is_true(os.getenv("BOT_DRY_RUN", "true"))
    bot_ai_enabled = _is_true(os.getenv("BOT_AI_ENABLED", "false"))
    bot_autoreply_enabled = _is_true(os.getenv("BOT_AUTOREPLY_ENABLED", "false"))
    real_creation_allowed = False
    try:
        assert_real_creation_allowed()
        real_creation_allowed = True
    except Exception:
        real_creation_allowed = False

    db_mask = mask_database_url(db_url)
    warnings: list[str] = []
    if env not in _LOCAL_ENVS:
        warnings.append("APP_ENV fuera de local/development/testing.")
    if not _is_local_db_url(db_url):
        warnings.append("Base de datos no local (no localhost/sqlite).")
    if whatsapp_enabled and not bot_dry_run:
        warnings.append("WhatsApp real activo (WHATSAPP_ENABLED=true y BOT_DRY_RUN=false).")
    if bot_ai_enabled and bot_autoreply_enabled:
        warnings.append("IA automática activa (BOT_AI_ENABLED=true y BOT_AUTOREPLY_ENABLED=true).")
    if real_creation_allowed:
        warnings.append("Creación real habilitada (BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true).")

    return {
        "app_env": env,
        "is_local_environment": env in _LOCAL_ENVS,
        "db_driver": db_mask["db_driver"],
        "db_host_type": db_mask["db_host_type"],
        "db_name_masked": db_mask["db_name_masked"],
        "db_url_masked": db_mask["db_url_masked"],
        "is_safe_local_database": _is_local_db_url(db_url),
        "whatsapp_enabled": whatsapp_enabled,
        "bot_dry_run": bot_dry_run,
        "bot_ai_enabled": bot_ai_enabled,
        "bot_autoreply_enabled": bot_autoreply_enabled,
        "real_creation_allowed": real_creation_allowed,
        "warnings": warnings,
    }


def get_dangerous_flags_for_production() -> list[str]:
    dangerous: list[str] = []
    if _is_true(os.getenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")):
        dangerous.append("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true")
    if _is_true(os.getenv("WHATSAPP_ENABLED", "false")) and not _is_true(os.getenv("BOT_DRY_RUN", "true")):
        dangerous.append("WHATSAPP_ENABLED=true + BOT_DRY_RUN=false")
    if _is_true(os.getenv("BOT_AI_ENABLED", "false")) and _is_true(os.getenv("BOT_AUTOREPLY_ENABLED", "false")):
        dangerous.append("BOT_AI_ENABLED=true + BOT_AUTOREPLY_ENABLED=true")
    return dangerous


def enforce_staging_offline_safety() -> None:
    staging_mode = _is_true(os.getenv("BOT_STAGING_MODE", "false"))
    sandbox_mode = _is_true(os.getenv("BOT_SANDBOX_MODE", "false"))
    if not (staging_mode and sandbox_mode):
        return
    if _is_true(os.getenv("WHATSAPP_ENABLED", "false")):
        raise EnvironmentSafetyError("staging_offline_blocked:WHATSAPP_ENABLED=true")
    if _is_true(os.getenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")):
        raise EnvironmentSafetyError("staging_offline_blocked:real_candidate_creation_flag=true")


def enforce_production_safety_startup() -> None:
    enforce_staging_offline_safety()
    if _is_true(os.getenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "false")):
        if not _is_true(os.getenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "false")):
            raise EnvironmentSafetyError("sandbox_auto_reply_blocked:owner_only_false")
        provider = str(os.getenv("BOT_REAL_WHATSAPP_PROVIDER", "") or "").strip().lower()
        if provider not in {"meta_sandbox", "meta-sandbox"}:
            raise EnvironmentSafetyError("sandbox_auto_reply_blocked:provider_not_meta_sandbox")
    env = _current_env()
    if env not in {"production", "prod"}:
        return
    dangerous = get_dangerous_flags_for_production()
    if dangerous:
        raise EnvironmentSafetyError("production_dangerous_flags_enabled:" + ", ".join(dangerous))
