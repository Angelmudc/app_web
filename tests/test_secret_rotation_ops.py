import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest


SCRIPT = Path("scripts/security/secret_rotation.py").resolve()


def _run(*args: str, extra_env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["APP_ENV"] = "testing"
    env["FLASK_ENV"] = "testing"
    env["DATABASE_URL"] = "sqlite:///:memory:"
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def test_audit_secrets_lists_expected_catalog():
    rc, out, err = _run("audit-secrets")
    assert rc == 0, err

    payload = json.loads(out)
    names = {row["name"] for row in payload["secrets"]}

    assert "FLASK_SECRET_KEY" in names
    assert "DATABASE_URL" in names
    assert "STAFF_MFA_ENCRYPTION_KEY" in names
    assert "BREAKGLASS_PASSWORD_HASH" in names
    assert "TELEGRAM_BOT_TOKEN" in names


def test_rotate_flask_secret_writes_env_file(tmp_path):
    env_file = tmp_path / ".env.rotate"
    env_file.write_text("FLASK_SECRET_KEY=old-value\n", encoding="utf-8")

    rc, out, err = _run(
        "rotate-secret",
        "--secret",
        "FLASK_SECRET_KEY",
        "--apply",
        "--env-file",
        str(env_file),
        "--reason",
        "pytest_rotate_flask",
        extra_env={"FLASK_SECRET_KEY": "old-value"},
    )
    assert rc == 0, err

    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["secret"] == "FLASK_SECRET_KEY"
    assert payload["dry_run"] is False
    assert payload["verification"] == "ok"
    assert payload["before"]["fingerprint"] != payload["after"]["fingerprint"]

    env_text = env_file.read_text(encoding="utf-8")
    assert "FLASK_SECRET_KEY=" in env_text
    assert "old-value" not in env_text


def test_rotate_staff_mfa_key_reencrypts_and_invalidates_old_key(tmp_path):
    from cryptography.fernet import Fernet, InvalidToken

    db_path = tmp_path / "mfa_rotation.sqlite"
    env_file = tmp_path / ".env.mfa_rotation"
    env_file.write_text("", encoding="utf-8")
    db_url = f"sqlite:///{db_path}"
    old_key = Fernet.generate_key().decode("utf-8")

    # Inicializa tablas seed en DB de testing.
    init_proc = subprocess.run(
        [sys.executable, "scripts/security/incident_response.py", "quick-security-check", "--minutes", "5"],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "APP_ENV": "testing",
            "FLASK_ENV": "testing",
            "DATABASE_URL": db_url,
            "STAFF_MFA_ENCRYPTION_KEY": old_key,
            "FLASK_SECRET_KEY": "test-flask-secret",
        },
    )
    assert init_proc.returncode == 0, init_proc.stderr

    old_cipher = Fernet(old_key.encode("utf-8"))
    clear_secret = "JBSWY3DPEHPK3PXP"
    old_token = old_cipher.encrypt(clear_secret.encode("utf-8")).decode("utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE staff_users SET mfa_enabled=1, mfa_secret=? WHERE lower(username)='owner'",
            (f"enc:{old_token}",),
        )
        conn.commit()
    finally:
        conn.close()

    rc, out, err = _run(
        "rotate-secret",
        "--secret",
        "STAFF_MFA_ENCRYPTION_KEY",
        "--apply",
        "--env-file",
        str(env_file),
        "--reason",
        "pytest_mfa_rotation",
        extra_env={
            "DATABASE_URL": db_url,
            "STAFF_MFA_ENCRYPTION_KEY": old_key,
            "FLASK_SECRET_KEY": "test-flask-secret",
        },
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["verification"] == "ok"
    assert payload["effects"]["mfa_reencrypt"]["processed"] >= 1

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT mfa_secret FROM staff_users WHERE lower(username)='owner' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    new_raw = str(row[0] or "")
    assert new_raw.startswith("enc:")
    env_text = env_file.read_text(encoding="utf-8")
    match = re.search(r"^STAFF_MFA_ENCRYPTION_KEY=(.+)$", env_text, flags=re.MULTILINE)
    assert match is not None
    new_key = str(match.group(1)).strip().strip("\"'")  # dotenv set_key may quote value.
    new_cipher = Fernet(new_key.encode("utf-8"))

    with pytest.raises(InvalidToken):
        old_cipher.decrypt(new_raw.split(":", 1)[1].encode("utf-8"))
    clear = new_cipher.decrypt(new_raw.split(":", 1)[1].encode("utf-8")).decode("utf-8").strip()
    assert clear == clear_secret
