#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operational helpers for security incident response.

This script uses the current app context and existing models/tools to avoid
introducing parallel infrastructure.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_

# Ensure repository root is importable when running as a standalone script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_app import create_app, db
from models import StaffAuditLog, StaffUser
from utils.audit_logger import log_action
from utils.enterprise_layer import close_user_sessions, get_alert_items
from utils.secret_rotation import rotate_secret
from utils.timezone import iso_utc_z, utc_now_naive


class IncidentResponseError(RuntimeError):
    pass


def _json_print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _resolve_staff(identity: str) -> StaffUser | None:
    ident = (identity or "").strip().lower()
    if not ident:
        return None
    return (
        StaffUser.query
        .filter(
            or_(
                func.lower(StaffUser.username) == ident,
                func.lower(StaffUser.email) == ident,
            )
        )
        .first()
    )


def _top_action_types(*, since_dt, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.session.query(
            StaffAuditLog.action_type.label("action_type"),
            func.count(StaffAuditLog.id).label("n"),
        )
        .filter(StaffAuditLog.created_at >= since_dt)
        .group_by(StaffAuditLog.action_type)
        .order_by(func.count(StaffAuditLog.id).desc(), StaffAuditLog.action_type.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [{"action_type": str(r.action_type), "count": int(r.n)} for r in rows]


def _top_failed_ips(*, since_dt, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.session.query(
            StaffAuditLog.ip.label("ip"),
            func.count(StaffAuditLog.id).label("n"),
        )
        .filter(StaffAuditLog.created_at >= since_dt)
        .filter(StaffAuditLog.success.is_(False))
        .filter(StaffAuditLog.ip.isnot(None))
        .group_by(StaffAuditLog.ip)
        .order_by(func.count(StaffAuditLog.id).desc(), StaffAuditLog.ip.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    out = []
    for row in rows:
        ip = str(row.ip or "").strip()
        if not ip:
            continue
        out.append({"ip": ip, "count": int(row.n)})
    return out


def build_snapshot_payload(*, minutes: int, scope: str = "all", limit: int = 50) -> dict[str, Any]:
    now = utc_now_naive()
    mins = max(1, int(minutes))
    since = now - timedelta(minutes=mins)

    base = StaffAuditLog.query.filter(StaffAuditLog.created_at >= since)
    fail_base = base.filter(StaffAuditLog.success.is_(False))
    auth_fail_actions = {
        "STAFF_LOGIN_FAIL",
        "CLIENTE_LOGIN_FAIL",
        "AUTH_LOGIN_BLOCKED",
        "AUTH_LOGIN_RATE_LIMITED",
        "MFA_VERIFY_FAIL",
        "MFA_SETUP_FAIL",
        "AUTHZ_DENIED",
        "PERMISSION_DENIED",
    }

    alerts_open = get_alert_items(
        limit=max(20, int(limit)),
        scope=(scope or "all"),
        include_resolved=False,
    )

    payload = {
        "generated_at": iso_utc_z(now),
        "window_minutes": mins,
        "since": iso_utc_z(since),
        "counts": {
            "total_events": int(base.count()),
            "failed_events": int(fail_base.count()),
            "auth_fail_related": int(base.filter(StaffAuditLog.action_type.in_(sorted(auth_fail_actions))).count()),
            "critical_or_warning_alerts": int(
                base.filter(StaffAuditLog.action_type.in_(["ALERT_CRITICAL", "ALERT_WARNING"])).count()
            ),
            "security_alerts": int(base.filter(StaffAuditLog.action_type == "SECURITY_ALERT").count()),
            "error_events": int(base.filter(StaffAuditLog.action_type == "ERROR_EVENT").count()),
            "open_alerts": int(len(alerts_open)),
        },
        "top_action_types": _top_action_types(since_dt=since, limit=min(50, max(5, int(limit)))),
        "top_failed_ips": _top_failed_ips(since_dt=since, limit=min(50, max(5, int(limit)))),
        "open_alerts_sample": alerts_open[: min(25, max(5, int(limit)))],
    }
    return payload


def command_contain_staff_account(args: argparse.Namespace) -> int:
    identity = (args.username or "").strip()
    reason = (args.reason or "").strip()[:300] or "incident_response"

    app = create_app()
    with app.app_context():
        staff = _resolve_staff(identity)
        if not isinstance(staff, StaffUser):
            raise IncidentResponseError("No se encontro staff para --username/--email indicado.")

        before = {
            "id": int(staff.id),
            "username": staff.username,
            "email": staff.email,
            "role": staff.role,
            "is_active": bool(staff.is_active),
            "mfa_enabled": bool(staff.mfa_enabled),
            "has_mfa_secret": bool((staff.mfa_secret or "").strip()),
            "password_hash_prefix": (staff.password_hash or "")[:16],
        }

        actions_done: list[str] = []
        if bool(args.disable_user) and bool(staff.is_active):
            staff.is_active = False
            actions_done.append("user_disabled")

        if bool(args.clear_mfa):
            had_mfa = bool(staff.mfa_enabled) or bool((staff.mfa_secret or "").strip())
            staff.clear_mfa()
            if had_mfa:
                actions_done.append("mfa_cleared")

        if bool(args.rotate_password):
            # Credential invalidation: replaces old hash immediately.
            temporary_password = secrets.token_urlsafe(24)
            staff.set_password(temporary_password)
            actions_done.append("password_rotated")

        db.session.commit()

        if bool(args.revoke_sessions):
            with app.test_request_context("/ops/incident-response", method="POST"):
                close_user_sessions(actor=None, user_id=int(staff.id), reason=reason)
            actions_done.append("sessions_revoked")

        with app.test_request_context("/ops/incident-response", method="POST"):
            log_action(
                action_type="INCIDENT_STAFF_CONTAINMENT",
                entity_type="staff_user",
                entity_id=int(staff.id),
                summary=f"Contencion de cuenta staff: {staff.username}",
                metadata={
                    "reason": reason,
                    "actions_done": list(actions_done),
                    "review_required": True,
                    "source": "scripts/security/incident_response.py",
                },
                success=True,
            )

        after = {
            "id": int(staff.id),
            "username": staff.username,
            "email": staff.email,
            "role": staff.role,
            "is_active": bool(staff.is_active),
            "mfa_enabled": bool(staff.mfa_enabled),
            "has_mfa_secret": bool((staff.mfa_secret or "").strip()),
            "password_hash_prefix": (staff.password_hash or "")[:16],
        }

        return _json_print(
            {
                "status": "ok",
                "operation": "contain_staff_account",
                "reason": reason,
                "actions_done": actions_done,
                "before": before,
                "after": after,
            }
        )


def command_quick_security_check(args: argparse.Namespace) -> int:
    app = create_app()
    with app.app_context():
        payload = build_snapshot_payload(
            minutes=int(args.minutes),
            scope=(args.scope or "all"),
            limit=int(args.limit),
        )
        if args.output:
            output_path = Path(args.output).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            payload["saved_to"] = str(output_path)
        return _json_print(payload)


def command_collect_evidence(args: argparse.Namespace) -> int:
    app = create_app()
    with app.app_context():
        payload = build_snapshot_payload(
            minutes=int(args.minutes),
            scope=(args.scope or "all"),
            limit=int(args.limit),
        )
        stamp = utc_now_naive().strftime("%Y%m%dT%H%M%SZ")
        label = "".join(ch for ch in str(args.label or "incident").strip().lower() if ch.isalnum() or ch in ("-", "_"))
        if not label:
            label = "incident"
        out_dir = Path(args.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"incident_snapshot_{stamp}_{label}.json"
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with app.test_request_context("/ops/incident-response", method="POST"):
            log_action(
                action_type="INCIDENT_EVIDENCE_COLLECTED",
                entity_type="security",
                entity_id=label[:64],
                summary="Snapshot de evidencia de incidente generado",
                metadata={
                    "window_minutes": int(args.minutes),
                    "scope": (args.scope or "all"),
                    "saved_to": str(out_file),
                },
                success=True,
            )
        return _json_print(
            {
                "status": "ok",
                "operation": "collect_evidence",
                "saved_to": str(out_file),
                "window_minutes": int(args.minutes),
                "scope": (args.scope or "all"),
            }
        )


def command_contain_secret_exposure(args: argparse.Namespace) -> int:
    secret_name = (args.secret or "").strip().upper()
    reason = (args.reason or "").strip()[:300] or "secret_exposed"

    rotation = rotate_secret(
        secret_name,
        new_value=(args.new_value or ""),
        breakglass_password=(args.breakglass_password or ""),
        apply=bool(args.apply),
        env_file=(args.env_file or ""),
        verify=bool(args.verify),
        reason=reason,
    )

    app = create_app()
    with app.app_context():
        with app.test_request_context("/ops/incident-response", method="POST"):
            log_action(
                action_type="INCIDENT_SECRET_CONTAINMENT",
                entity_type="security",
                entity_id=secret_name[:64],
                summary=f"Contención por secreto expuesto: {secret_name}",
                metadata={
                    "reason": reason,
                    "apply": bool(args.apply),
                    "env_file": (args.env_file or "")[:255] or None,
                    "rotation_status": rotation.get("status"),
                    "rotation_class": rotation.get("rotation_class"),
                },
                success=bool(rotation.get("status") == "ok"),
            )

    return _json_print(
        {
            "status": "ok" if rotation.get("status") == "ok" else "error",
            "operation": "contain_secret_exposure",
            "secret": secret_name,
            "reason": reason,
            "rotation": rotation,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operational security incident response helpers."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cmd_contain = sub.add_parser(
        "contain-staff-account",
        help="Contain a potentially compromised staff account.",
    )
    cmd_contain.add_argument("--username", required=True, help="Staff username or email.")
    cmd_contain.add_argument("--reason", default="incident_response", help="Reason for the containment action.")
    cmd_contain.add_argument("--disable-user", dest="disable_user", action=argparse.BooleanOptionalAction, default=True)
    cmd_contain.add_argument("--revoke-sessions", dest="revoke_sessions", action=argparse.BooleanOptionalAction, default=True)
    cmd_contain.add_argument("--clear-mfa", dest="clear_mfa", action=argparse.BooleanOptionalAction, default=True)
    cmd_contain.add_argument("--rotate-password", dest="rotate_password", action=argparse.BooleanOptionalAction, default=True)
    cmd_contain.set_defaults(func=command_contain_staff_account)

    cmd_quick = sub.add_parser(
        "quick-security-check",
        help="Generate a quick operational security snapshot from audit logs.",
    )
    cmd_quick.add_argument("--minutes", type=int, default=120, help="Window in minutes.")
    cmd_quick.add_argument("--scope", default="all", choices=["all", "security", "error", "critical"], help="Alert scope.")
    cmd_quick.add_argument("--limit", type=int, default=50, help="Sample limit.")
    cmd_quick.add_argument("--output", default="", help="Optional output JSON path.")
    cmd_quick.set_defaults(func=command_quick_security_check)

    cmd_evidence = sub.add_parser(
        "collect-evidence",
        help="Persist an incident evidence snapshot to disk.",
    )
    cmd_evidence.add_argument("--minutes", type=int, default=180, help="Window in minutes.")
    cmd_evidence.add_argument("--scope", default="all", choices=["all", "security", "error", "critical"], help="Alert scope.")
    cmd_evidence.add_argument("--limit", type=int, default=80, help="Sample limit.")
    cmd_evidence.add_argument("--label", default="incident", help="Short label for output filename.")
    cmd_evidence.add_argument("--output-dir", default="artifacts/incidents", help="Directory for evidence files.")
    cmd_evidence.set_defaults(func=command_collect_evidence)

    cmd_secret = sub.add_parser(
        "contain-secret-exposure",
        help="Contain a secret exposure incident by rotating the secret.",
    )
    cmd_secret.add_argument("--secret", required=True, help="Secret name to rotate.")
    cmd_secret.add_argument("--new-value", default="", help="New explicit value when required.")
    cmd_secret.add_argument("--breakglass-password", default="", help="New breakglass password for hash regeneration.")
    cmd_secret.add_argument("--reason", default="secret_exposed", help="Reason for containment action.")
    cmd_secret.add_argument("--apply", action=argparse.BooleanOptionalAction, default=True, help="Apply changes (default true).")
    cmd_secret.add_argument("--env-file", default="", help="Optional .env file to update.")
    cmd_secret.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True, help="Post-rotation verification.")
    cmd_secret.set_defaults(func=command_contain_secret_exposure)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except IncidentResponseError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
