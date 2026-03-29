# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


_DOTENV_LOADED = False
_SECRET_CACHE: dict[str, Optional[str]] = {}


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is missing."""


def _app_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()


def _is_dev_like_env(env_name: Optional[str] = None) -> bool:
    env = (env_name or _app_env()).strip().lower()
    return env in {"dev", "development", "test", "testing", "local"}


def _dotenv_path() -> Path:
    custom = (os.getenv("APP_DOTENV_PATH") or "").strip()
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parents[1] / ".env"


def _should_load_dotenv() -> bool:
    raw = (os.getenv("SECRETS_LOAD_DOTENV") or "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _ensure_dev_dotenv_loaded() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    if not _should_load_dotenv() or not _is_dev_like_env():
        _DOTENV_LOADED = True
        return
    load_dotenv(_dotenv_path(), override=False)
    _DOTENV_LOADED = True


def _clean_secret(value: Optional[str], *, allow_blank: bool) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned:
        return cleaned
    return cleaned if allow_blank else None


def _secret_ref_env(secret_name: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in secret_name).upper()
    return f"SECRET_REF_{normalized}"


def _backend_name() -> str:
    return (os.getenv("SECRET_MANAGER_BACKEND") or "").strip().lower()


def _aws_fetch_secret(secret_id: str) -> Optional[str]:
    try:
        import boto3  # type: ignore
    except Exception as exc:
        raise RuntimeError("SECRET_MANAGER_BACKEND=aws requiere boto3 instalado") from exc

    cache_key = f"aws:{secret_id}"
    if cache_key in _SECRET_CACHE:
        return _SECRET_CACHE[cache_key]

    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_id)
    value = resp.get("SecretString")
    if value is None and resp.get("SecretBinary") is not None:
        value = resp.get("SecretBinary")
    value = str(value) if value is not None else None
    _SECRET_CACHE[cache_key] = value
    return value


def _gcp_fetch_secret(secret_id: str) -> Optional[str]:
    try:
        from google.cloud import secretmanager  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "SECRET_MANAGER_BACKEND=gcp requiere google-cloud-secret-manager instalado"
        ) from exc

    project_id = (os.getenv("GCP_PROJECT_ID") or "").strip()
    if not project_id:
        raise RuntimeError("Falta GCP_PROJECT_ID para usar Secret Manager de GCP")

    cache_key = f"gcp:{project_id}:{secret_id}"
    if cache_key in _SECRET_CACHE:
        return _SECRET_CACHE[cache_key]

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    resp = client.access_secret_version(request={"name": name})
    payload = resp.payload.data.decode("utf-8") if resp and resp.payload else None
    _SECRET_CACHE[cache_key] = payload
    return payload


def _extract_json_field(raw_secret: Optional[str], field_name: str) -> Optional[str]:
    if not raw_secret:
        return None
    parsed = None
    try:
        parsed = json.loads(raw_secret)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(field_name)
    return None if value is None else str(value)


def _resolve_from_secret_manager(secret_name: str) -> Optional[str]:
    backend = _backend_name()
    if not backend or backend == "env":
        return None

    secret_ref = (os.getenv(_secret_ref_env(secret_name)) or "").strip()
    if not secret_ref:
        prefix = (os.getenv("SECRET_MANAGER_PREFIX") or "").strip().strip("/")
        if prefix:
            secret_ref = f"{prefix}/{secret_name}"

    if not secret_ref:
        return None

    if backend == "aws":
        raw = _aws_fetch_secret(secret_ref)
    elif backend == "gcp":
        raw = _gcp_fetch_secret(secret_ref)
    else:
        raise RuntimeError(f"SECRET_MANAGER_BACKEND no soportado: {backend}")

    json_key = (os.getenv("SECRET_MANAGER_JSON_KEY") or "").strip()
    if json_key:
        candidate = _extract_json_field(raw, json_key)
        return candidate if candidate is not None else raw

    by_name = _extract_json_field(raw, secret_name)
    if by_name is not None:
        return by_name
    return raw


def get_secret(
    secret_name: str,
    *,
    required: bool = False,
    default: Optional[str] = None,
    allow_blank: bool = False,
) -> Optional[str]:
    if not secret_name or not str(secret_name).strip():
        raise ValueError("secret_name es requerido")

    _ensure_dev_dotenv_loaded()

    value = _clean_secret(os.getenv(secret_name), allow_blank=allow_blank)
    if value is None:
        from_manager = _clean_secret(
            _resolve_from_secret_manager(secret_name),
            allow_blank=allow_blank,
        )
        value = from_manager if from_manager is not None else _clean_secret(default, allow_blank=allow_blank)

    if required and value is None:
        raise SecretNotFoundError(f"Missing required secret: {secret_name}")
    return value


def get_required_secret(secret_name: str, *, allow_blank: bool = False) -> str:
    value = get_secret(secret_name, required=True, allow_blank=allow_blank)
    return str(value)


def reset_secrets_manager_state() -> None:
    """Utility for tests to reset lazy-loading/caches."""
    global _DOTENV_LOADED
    _DOTENV_LOADED = False
    _SECRET_CACHE.clear()
