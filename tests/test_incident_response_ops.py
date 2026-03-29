import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional


SCRIPT = Path("scripts/security/incident_response.py").resolve()


def _run(db_path: Path, *args: str, extra_env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["APP_ENV"] = "testing"
    env["FLASK_ENV"] = "testing"
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
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


def _db_fetch_one(db_path: Path, query: str):
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(query)
        return cur.fetchone()
    finally:
        conn.close()


def test_contain_staff_account_disables_rotates_and_logs(tmp_path):
    db_path = tmp_path / "incident.sqlite"

    rc, out, err = _run(
        db_path,
        "quick-security-check",
        "--minutes",
        "30",
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert int(payload["window_minutes"]) == 30

    before = _db_fetch_one(
        db_path,
        "SELECT password_hash, is_active FROM staff_users WHERE lower(username)='owner' LIMIT 1",
    )
    assert before is not None
    before_hash, before_active = before
    assert int(before_active) == 1

    rc, out, err = _run(
        db_path,
        "contain-staff-account",
        "--username",
        "Owner",
        "--reason",
        "pytest_compromise_check",
    )
    assert rc == 0, err
    result = json.loads(out)
    assert result["status"] == "ok"
    assert "user_disabled" in result["actions_done"]
    assert "password_rotated" in result["actions_done"]
    assert "sessions_revoked" in result["actions_done"]

    after = _db_fetch_one(
        db_path,
        "SELECT password_hash, is_active, mfa_enabled, mfa_secret FROM staff_users WHERE lower(username)='owner' LIMIT 1",
    )
    assert after is not None
    after_hash, after_active, after_mfa_enabled, after_mfa_secret = after
    assert int(after_active) == 0
    assert str(after_hash) != str(before_hash)
    assert int(after_mfa_enabled or 0) == 0
    assert (after_mfa_secret or "") == ""

    containment_log = _db_fetch_one(
        db_path,
        "SELECT count(*) FROM staff_audit_logs WHERE action_type='INCIDENT_STAFF_CONTAINMENT'",
    )
    assert containment_log is not None
    assert int(containment_log[0] or 0) >= 1

    revoked_log = _db_fetch_one(
        db_path,
        "SELECT count(*) FROM staff_audit_logs WHERE action_type='SESSION_FORCED_LOGOUT'",
    )
    assert revoked_log is not None
    assert int(revoked_log[0] or 0) >= 1


def test_collect_evidence_generates_json_file(tmp_path):
    db_path = tmp_path / "incident.sqlite"
    out_dir = tmp_path / "artifacts"

    rc, out, err = _run(
        db_path,
        "contain-staff-account",
        "--username",
        "Owner",
        "--reason",
        "pytest_evidence",
    )
    assert rc == 0, err
    result = json.loads(out)
    assert result["status"] == "ok"

    rc, out, err = _run(
        db_path,
        "collect-evidence",
        "--minutes",
        "180",
        "--label",
        "pytest_incident",
        "--output-dir",
        str(out_dir),
    )
    assert rc == 0, err
    evidence = json.loads(out)
    assert evidence["status"] == "ok"
    evidence_path = Path(evidence["saved_to"])
    assert evidence_path.exists()

    body = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert int(body["window_minutes"]) == 180
    assert "counts" in body
    assert "top_action_types" in body


def test_contain_secret_exposure_rotates_breakglass_hash_and_updates_env_file(tmp_path):
    db_path = tmp_path / "incident.sqlite"
    env_file = tmp_path / ".env.rotation"
    env_file.write_text("", encoding="utf-8")

    rc, out, err = _run(
        db_path,
        "contain-secret-exposure",
        "--secret",
        "BREAKGLASS_PASSWORD_HASH",
        "--breakglass-password",
        "NuevaClaveTemporal_2026!",
        "--apply",
        "--env-file",
        str(env_file),
        "--reason",
        "pytest_secret_exposed",
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["operation"] == "contain_secret_exposure"
    assert payload["rotation"]["secret"] == "BREAKGLASS_PASSWORD_HASH"
    assert payload["rotation"]["dry_run"] is False
    assert payload["rotation"]["verification"] == "ok"

    env_text = env_file.read_text(encoding="utf-8")
    assert "BREAKGLASS_PASSWORD_HASH=" in env_text

    containment_log = _db_fetch_one(
        db_path,
        "SELECT count(*) FROM staff_audit_logs WHERE action_type='INCIDENT_SECRET_CONTAINMENT'",
    )
    assert containment_log is not None
    assert int(containment_log[0] or 0) >= 1
