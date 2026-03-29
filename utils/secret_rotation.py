# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import set_key
from werkzeug.security import generate_password_hash

from config_app import create_app, db
from models import StaffUser
from utils.audit_logger import log_action
from utils.secrets_manager import get_secret, reset_secrets_manager_state
from utils.timezone import iso_utc_z, utc_now_naive


class SecretRotationError(RuntimeError):
    """Raised when a requested secret rotation cannot proceed safely."""


@dataclass(frozen=True)
class SecretSpec:
    name: str
    criticality: str
    rotation_class: str
    supports_auto_generation: bool
    requires_external_coordination: bool
    notes: str


SECRET_SPECS: dict[str, SecretSpec] = {
    "FLASK_SECRET_KEY": SecretSpec(
        name="FLASK_SECRET_KEY",
        criticality="critical",
        rotation_class="controlled",
        supports_auto_generation=True,
        requires_external_coordination=False,
        notes="Rota firma de sesiones Flask; requiere despliegue/restart coordinado.",
    ),
    "DATABASE_URL": SecretSpec(
        name="DATABASE_URL",
        criticality="critical",
        rotation_class="external_coordination",
        supports_auto_generation=False,
        requires_external_coordination=True,
        notes="La contraseña/credencial DB se rota fuera de la app y se inyecta nueva URL.",
    ),
    "STAFF_MFA_ENCRYPTION_KEY": SecretSpec(
        name="STAFF_MFA_ENCRYPTION_KEY",
        criticality="critical",
        rotation_class="controlled",
        supports_auto_generation=True,
        requires_external_coordination=False,
        notes="Requiere re-cifrado de secretos MFA almacenados para invalidar la clave previa.",
    ),
    "BREAKGLASS_PASSWORD_HASH": SecretSpec(
        name="BREAKGLASS_PASSWORD_HASH",
        criticality="critical",
        rotation_class="immediate_possible",
        supports_auto_generation=False,
        requires_external_coordination=False,
        notes="Rota hash breakglass; invalida contraseña anterior en cuanto aplica.",
    ),
    "TELEGRAM_BOT_TOKEN": SecretSpec(
        name="TELEGRAM_BOT_TOKEN",
        criticality="secondary",
        rotation_class="external_coordination",
        supports_auto_generation=False,
        requires_external_coordination=True,
        notes="Debe regenerarse en BotFather/proveedor y luego actualizarse en entorno.",
    ),
    "TELEGRAM_CHAT_ID": SecretSpec(
        name="TELEGRAM_CHAT_ID",
        criticality="secondary",
        rotation_class="external_coordination",
        supports_auto_generation=False,
        requires_external_coordination=True,
        notes="No suele rotarse, pero puede cambiar por migración de canal/chat.",
    ),
}

CRITICAL_ROTATION_ORDER = [
    "BREAKGLASS_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "STAFF_MFA_ENCRYPTION_KEY",
    "DATABASE_URL",
]


def _fingerprint(value: str | None) -> str:
    raw = (value or "").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return digest[:16]


def _masked(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _normalize_env_name(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(name or "")).upper()


def _secret_ref_env(name: str) -> str:
    return f"SECRET_REF_{_normalize_env_name(name)}"


def _backend_name() -> str:
    return (os.getenv("SECRET_MANAGER_BACKEND") or "").strip().lower()


def _guess_source(name: str) -> str:
    env_value = os.environ.get(name)
    if env_value is not None and str(env_value).strip():
        return "env"
    if _backend_name() in {"aws", "gcp"}:
        ref_name = _secret_ref_env(name)
        if (os.environ.get(ref_name) or "").strip() or (os.environ.get("SECRET_MANAGER_PREFIX") or "").strip():
            return f"secret_manager:{_backend_name()}"
    return "unset"


def audit_secrets() -> dict[str, Any]:
    specs = [SECRET_SPECS[k] for k in sorted(SECRET_SPECS)]
    rows: list[dict[str, Any]] = []
    for spec in specs:
        current = get_secret(spec.name)
        rows.append(
            {
                "name": spec.name,
                "criticality": spec.criticality,
                "rotation_class": spec.rotation_class,
                "supports_auto_generation": bool(spec.supports_auto_generation),
                "requires_external_coordination": bool(spec.requires_external_coordination),
                "configured": bool((current or "").strip()),
                "current_fingerprint": _fingerprint(current) if current else "",
                "current_masked": _masked(current),
                "source": _guess_source(spec.name),
                "notes": spec.notes,
            }
        )

    by_class: dict[str, list[str]] = {}
    for row in rows:
        by_class.setdefault(str(row["rotation_class"]), []).append(str(row["name"]))

    return {
        "generated_at": iso_utc_z(utc_now_naive()),
        "backend": _backend_name() or "env",
        "critical_rotation_order": list(CRITICAL_ROTATION_ORDER),
        "secrets": rows,
        "classified": {k: sorted(v) for k, v in by_class.items()},
    }


def _set_env_var(name: str, value: str) -> None:
    os.environ[str(name)] = str(value)
    reset_secrets_manager_state()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _upsert_env_file(path: str | Path, name: str, value: str) -> str:
    target = Path(path).resolve()
    _ensure_parent(target)
    if not target.exists():
        target.write_text("", encoding="utf-8")

    set_key(str(target), str(name), str(value), quote_mode="auto")
    return str(target)


def _new_flask_secret_key() -> str:
    return secrets.token_urlsafe(64)


def _new_staff_mfa_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise SecretRotationError(
            "No se pudo generar STAFF_MFA_ENCRYPTION_KEY: falta dependencia cryptography."
        ) from exc
    return Fernet.generate_key().decode("utf-8")


def _fernet_from_key(key_text: str):
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise SecretRotationError("cryptography es requerida para rotar STAFF_MFA_ENCRYPTION_KEY") from exc

    key_raw = (key_text or "").strip().encode("utf-8")
    return Fernet(key_raw)


def _fallback_mfa_key_from_flask_secret() -> str:
    try:
        import base64

        base_secret = (get_secret("FLASK_SECRET_KEY") or "").encode("utf-8")
        if not base_secret:
            return ""
        digest = hashlib.sha256(base_secret).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8")
    except Exception:
        return ""


def _resolve_current_mfa_key() -> str:
    key = (get_secret("STAFF_MFA_ENCRYPTION_KEY") or "").strip()
    if key:
        return key
    return _fallback_mfa_key_from_flask_secret()


def _reencrypt_staff_mfa_data(*, old_key: str, new_key: str, dry_run: bool) -> dict[str, Any]:
    if not old_key:
        raise SecretRotationError(
            "No se pudo resolver la clave MFA actual para re-cifrar datos existentes."
        )

    app = create_app()
    old_cipher = _fernet_from_key(old_key)
    new_cipher = _fernet_from_key(new_key)

    with app.app_context():
        rows = (
            StaffUser.query
            .filter(StaffUser.mfa_secret.isnot(None))
            .all()
        )

        processed = 0
        skipped = 0
        failed: list[int] = []
        updated_ids: list[int] = []

        for user in rows:
            raw = (user.mfa_secret or "").strip()
            if not raw or not raw.startswith("enc:"):
                skipped += 1
                continue

            token = raw.split(":", 1)[1].strip()
            try:
                clear = old_cipher.decrypt(token.encode("utf-8")).decode("utf-8").strip().upper()
                new_token = new_cipher.encrypt(clear.encode("utf-8")).decode("utf-8")
            except Exception:
                failed.append(int(user.id))
                continue

            processed += 1
            updated_ids.append(int(user.id))
            if not dry_run:
                user.mfa_secret = f"enc:{new_token}"

        if failed:
            db.session.rollback()
            raise SecretRotationError(
                f"No se pudo re-cifrar MFA para {len(failed)} usuario(s): {failed[:10]}"
            )

        if not dry_run:
            db.session.commit()

    return {
        "rows_with_mfa": len(rows),
        "processed": processed,
        "skipped": skipped,
        "updated_user_ids": updated_ids,
    }


def _clear_telegram_runtime_cache() -> None:
    try:
        from utils.enterprise_layer import clear_telegram_channel_runtime_cache

        clear_telegram_channel_runtime_cache()
    except Exception:
        return


def _validate_secret_name(secret_name: str) -> SecretSpec:
    name = (secret_name or "").strip().upper()
    spec = SECRET_SPECS.get(name)
    if spec is None:
        allowed = ", ".join(sorted(SECRET_SPECS))
        raise SecretRotationError(f"Secreto no soportado: {secret_name}. Soportados: {allowed}")
    return spec


def _resolve_new_value(*, spec: SecretSpec, new_value: str, breakglass_password: str) -> tuple[str, dict[str, Any]]:
    value = (new_value or "").strip()
    meta: dict[str, Any] = {}

    if spec.name == "FLASK_SECRET_KEY":
        if not value:
            value = _new_flask_secret_key()
        return value, meta

    if spec.name == "STAFF_MFA_ENCRYPTION_KEY":
        if not value:
            value = _new_staff_mfa_key()
        return value, meta

    if spec.name == "BREAKGLASS_PASSWORD_HASH":
        raw = (breakglass_password or "").strip()
        if raw:
            value = generate_password_hash(raw, method="pbkdf2:sha256")
            meta["generated_from_breakglass_password"] = True
        if not value:
            raise SecretRotationError(
                "BREAKGLASS_PASSWORD_HASH requiere --breakglass-password o --new-value (hash ya generado)."
            )
        return value, meta

    if spec.name in {"DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
        if not value:
            raise SecretRotationError(f"{spec.name} requiere --new-value para rotación segura.")
        return value, meta

    raise SecretRotationError(f"Rotación no implementada para {spec.name}")


def rotate_secret(
    secret_name: str,
    *,
    new_value: str = "",
    breakglass_password: str = "",
    apply: bool = False,
    env_file: str = "",
    verify: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    spec = _validate_secret_name(secret_name)
    name = spec.name
    before = get_secret(name) or ""

    resolved_new_value, resolve_meta = _resolve_new_value(
        spec=spec,
        new_value=new_value,
        breakglass_password=breakglass_password,
    )
    if before and _fingerprint(before) == _fingerprint(resolved_new_value):
        raise SecretRotationError("El valor nuevo coincide con el valor actual. Proporciona uno diferente.")

    dry_run = not bool(apply)
    effects: dict[str, Any] = {}

    if name == "STAFF_MFA_ENCRYPTION_KEY":
        old_key = _resolve_current_mfa_key()
        effects["mfa_reencrypt"] = _reencrypt_staff_mfa_data(
            old_key=old_key,
            new_key=resolved_new_value,
            dry_run=dry_run,
        )

    written_to = ""
    if not dry_run:
        _set_env_var(name, resolved_new_value)
        if env_file:
            written_to = _upsert_env_file(env_file, name, resolved_new_value)

        if name == "TELEGRAM_BOT_TOKEN":
            _clear_telegram_runtime_cache()

    after_visible = (get_secret(name) or "") if not dry_run else resolved_new_value
    verify_status = "skipped"
    if verify:
        if name == "BREAKGLASS_PASSWORD_HASH":
            verify_status = "ok" if _fingerprint(after_visible) == _fingerprint(resolved_new_value) else "failed"
        elif name == "STAFF_MFA_ENCRYPTION_KEY":
            verify_status = "ok" if _fingerprint(after_visible) == _fingerprint(resolved_new_value) else "failed"
        else:
            verify_status = "ok" if _fingerprint(after_visible) == _fingerprint(resolved_new_value) else "failed"

    payload = {
        "status": "ok" if verify_status != "failed" else "error",
        "secret": name,
        "criticality": spec.criticality,
        "rotation_class": spec.rotation_class,
        "dry_run": dry_run,
        "reason": (reason or "").strip()[:300] or "manual_rotation",
        "before": {
            "configured": bool(before),
            "fingerprint": _fingerprint(before) if before else "",
            "masked": _masked(before),
            "source": _guess_source(name),
        },
        "after": {
            "fingerprint": _fingerprint(after_visible),
            "masked": _masked(after_visible),
            "written_to_env_file": written_to,
        },
        "effects": effects,
        "verification": verify_status,
        "meta": resolve_meta,
    }

    if not dry_run:
        try:
            app = create_app()
            with app.app_context():
                with app.test_request_context("/ops/secret-rotation", method="POST"):
                    log_action(
                        action_type="SECRET_ROTATED",
                        entity_type="security",
                        entity_id=name,
                        summary=f"Rotación de secreto {name}",
                        metadata={
                            "secret": name,
                            "rotation_class": spec.rotation_class,
                            "criticality": spec.criticality,
                            "written_to_env_file": written_to or None,
                            "reason": payload["reason"],
                            "dry_run": False,
                        },
                        success=bool(payload["status"] == "ok"),
                    )
        except Exception:
            pass

    return payload


def rotate_critical_bundle(
    *,
    apply: bool,
    env_file: str,
    breakglass_password: str,
    database_url: str,
    telegram_bot_token: str,
    reason: str,
) -> dict[str, Any]:
    results = []
    for secret_name in CRITICAL_ROTATION_ORDER:
        if secret_name == "DATABASE_URL" and not (database_url or "").strip():
            results.append(
                {
                    "status": "skipped",
                    "secret": secret_name,
                    "reason": "missing_database_url",
                }
            )
            continue

        params = {
            "secret_name": secret_name,
            "apply": apply,
            "env_file": env_file,
            "breakglass_password": breakglass_password,
            "reason": reason or "critical_bundle_rotation",
        }
        if secret_name == "DATABASE_URL":
            params["new_value"] = database_url

        result = rotate_secret(**params)
        results.append(result)

    if (telegram_bot_token or "").strip():
        results.append(
            rotate_secret(
                "TELEGRAM_BOT_TOKEN",
                new_value=telegram_bot_token,
                apply=apply,
                env_file=env_file,
                reason=reason or "critical_bundle_rotation",
            )
        )

    return {
        "status": "ok",
        "operation": "rotate_critical_bundle",
        "apply": bool(apply),
        "results": results,
    }


def _json_print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Secret audit + rotation helpers.")
    sub = parser.add_subparsers(dest="command", required=True)

    cmd_audit = sub.add_parser("audit-secrets", help="Audita secretos y clasificación de rotación.")
    cmd_audit.set_defaults(func=lambda _args: _json_print(audit_secrets()))

    cmd_rotate = sub.add_parser("rotate-secret", help="Rota un secreto individual.")
    cmd_rotate.add_argument("--secret", required=True, choices=sorted(SECRET_SPECS), help="Secret a rotar.")
    cmd_rotate.add_argument("--new-value", default="", help="Nuevo valor explícito (requerido para secretos externos).")
    cmd_rotate.add_argument("--breakglass-password", default="", help="Password breakglass en texto plano para re-generar hash.")
    cmd_rotate.add_argument("--apply", action=argparse.BooleanOptionalAction, default=False, help="Aplicar cambios reales (por defecto dry-run).")
    cmd_rotate.add_argument("--env-file", default="", help="Archivo .env a actualizar (opcional).")
    cmd_rotate.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True, help="Validar post-rotación.")
    cmd_rotate.add_argument("--reason", default="manual_rotation", help="Motivo operativo (auditoría).")
    cmd_rotate.set_defaults(
        func=lambda args: _json_print(
            rotate_secret(
                args.secret,
                new_value=args.new_value,
                breakglass_password=args.breakglass_password,
                apply=bool(args.apply),
                env_file=args.env_file,
                verify=bool(args.verify),
                reason=args.reason,
            )
        )
    )

    cmd_bundle = sub.add_parser("rotate-critical", help="Rota bundle crítico con ejecución controlada.")
    cmd_bundle.add_argument("--apply", action=argparse.BooleanOptionalAction, default=False, help="Aplicar cambios reales.")
    cmd_bundle.add_argument("--env-file", default="", help="Archivo .env a actualizar (opcional).")
    cmd_bundle.add_argument("--breakglass-password", default="", help="Nueva contraseña breakglass para generar hash.")
    cmd_bundle.add_argument("--database-url", default="", help="Nueva DATABASE_URL (opcional; si no, se marca skipped).")
    cmd_bundle.add_argument("--telegram-bot-token", default="", help="Nuevo TELEGRAM_BOT_TOKEN (opcional).")
    cmd_bundle.add_argument("--reason", default="critical_bundle_rotation", help="Motivo operativo (auditoría).")
    cmd_bundle.set_defaults(
        func=lambda args: _json_print(
            rotate_critical_bundle(
                apply=bool(args.apply),
                env_file=args.env_file,
                breakglass_password=args.breakglass_password,
                database_url=args.database_url,
                telegram_bot_token=args.telegram_bot_token,
                reason=args.reason,
            )
        )
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SecretRotationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
