#!/usr/bin/env python3
"""Create/remove disposable Round 8 staff accounts on the local DB only."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from sqlalchemy import func, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import StaffAuditLog, StaffPresenceState, StaffUser, TrustedDevice
from services.environment_guard_service import assert_local_safe_environment


ADMIN_USERNAME = "round8_admin"
SECRETARIA_USERNAME = "round8_secretaria"
ACCOUNT_USERNAMES = (ADMIN_USERNAME, SECRETARIA_USERNAME)


def _is_local_loopback(value: str) -> bool:
    """Accept only localhost or a single IPv4/IPv6 loopback address."""
    raw = str(value or "").strip().lower()
    if raw == "localhost":
        return True
    try:
        if "/" in raw:
            network = ipaddress.ip_network(raw, strict=False)
            return network.prefixlen == network.max_prefixlen and network.network_address.is_loopback
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def _prove_local_database() -> dict[str, str]:
    assert_local_safe_environment()
    with db.engine.connect() as conn:
        row = conn.execute(
            text(
                "select current_database(), coalesce(inet_server_addr()::text, ''), "
                "coalesce(inet_server_port()::text, '')"
            )
        ).one()
    proof = {"database": str(row[0]), "host": str(row[1]), "port": str(row[2])}
    if proof["database"] != "domestica_cibao_local":
        raise RuntimeError(f"Abortado: DB efectiva inesperada ({proof['database']})")
    if not _is_local_loopback(proof["host"]):
        raise RuntimeError(f"Abortado: host DB no local ({proof['host']})")
    return proof


def _password(value: str | None, env_name: str) -> str:
    result = str(value or os.getenv(env_name) or "").strip()
    if len(result) < 12:
        raise RuntimeError(f"Usa --{env_name.lower().replace('_', '-')} o {env_name} con >=12 caracteres; no hay password hardcodeado.")
    return result


def _upsert_staff(username: str, role: str, password: str) -> StaffUser:
    row = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
    if row is None:
        row = StaffUser(username=username, role=role, is_active=True)
        db.session.add(row)
    row.role = role
    row.is_active = True
    row.email = f"{username}@local.test"
    row.clear_mfa()
    row.set_password(password)
    return row


def prepare(admin_password: str, secretaria_password: str) -> None:
    proof = _prove_local_database()
    admin = _upsert_staff(ADMIN_USERNAME, "admin", admin_password)
    secretaria = _upsert_staff(SECRETARIA_USERNAME, "secretaria", secretaria_password)
    db.session.commit()
    print(f"LOCAL_DB_PROOF app_env={os.getenv('APP_ENV')} database={proof['database']} host={proof['host']} port={proof['port']}")
    print(f"ROUND8_STAFF_READY admin={admin.username} secretaria={secretaria.username}")


def cleanup() -> None:
    proof = _prove_local_database()
    users = StaffUser.query.filter(StaffUser.username.in_(ACCOUNT_USERNAMES)).all()
    user_ids = [int(row.id) for row in users]
    if user_ids:
        TrustedDevice.query.filter(TrustedDevice.user_id.in_(user_ids)).delete(synchronize_session=False)
        StaffPresenceState.query.filter(StaffPresenceState.user_id.in_(user_ids)).delete(synchronize_session=False)
        StaffAuditLog.query.filter(StaffAuditLog.actor_user_id.in_(user_ids)).delete(synchronize_session=False)
        for row in users:
            db.session.delete(row)
        db.session.commit()
    print(f"LOCAL_DB_PROOF app_env={os.getenv('APP_ENV')} database={proof['database']} host={proof['host']} port={proof['port']}")
    print(f"ROUND8_STAFF_CLEANUP deleted={len(users)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixture staff desechable para Security Round 8.")
    parser.add_argument("action", choices=("prepare", "cleanup"))
    parser.add_argument("--admin-password", default=None)
    parser.add_argument("--secretaria-password", default=None)
    args = parser.parse_args()
    with app.app_context():
        if args.action == "prepare":
            prepare(_password(args.admin_password, "ROUND8_ADMIN_PASSWORD"), _password(args.secretaria_password, "ROUND8_SECRETARIA_PASSWORD"))
        else:
            cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
