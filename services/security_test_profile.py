"""Guardas explícitas para el perfil local de pruebas de seguridad Round 8."""

from __future__ import annotations

import os

from services.environment_guard_service import (
    EnvironmentSafetyError,
    is_localish_runtime_environment,
    normalize_runtime_environment,
)


PROFILE_NAME = "round8"
PROFILE_ENV_VAR = "SECURITY_TEST_PROFILE"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _runtime_environment() -> str:
    return normalize_runtime_environment(os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "")


def _profile_value() -> str:
    return str(os.getenv(PROFILE_ENV_VAR) or "").strip().lower()


def security_test_profile_requested() -> bool:
    """Whether the operator explicitly requested the Round 8 profile."""
    value = _profile_value()
    return value == PROFILE_NAME or value in _TRUE_VALUES


def security_test_profile_enabled() -> bool:
    """Enable only for the exact profile name in a known local-ish environment."""
    return security_test_profile_requested() and _profile_value() == PROFILE_NAME and is_localish_runtime_environment(_runtime_environment())


def enforce_security_test_profile_safety() -> None:
    """Reject accidental activation in production, staging, or unknown environments."""
    if not security_test_profile_requested():
        return
    env = _runtime_environment()
    if _profile_value() != PROFILE_NAME:
        raise EnvironmentSafetyError(
            f"{PROFILE_ENV_VAR} debe ser exactamente {PROFILE_NAME!r}"
        )
    if not is_localish_runtime_environment(env):
        raise EnvironmentSafetyError(
            f"{PROFILE_ENV_VAR}=round8 bloqueado fuera de local/development/test (APP_ENV={env or 'unknown'})"
        )


def security_test_profile_snapshot() -> dict[str, object]:
    env = _runtime_environment()
    return {
        "requested": security_test_profile_requested(),
        "enabled": security_test_profile_enabled(),
        "profile": _profile_value(),
        "app_env": env,
        "localish": is_localish_runtime_environment(env),
    }
