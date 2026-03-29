import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path("scripts/ops/backup_restore.py").resolve()


def _run(*args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def test_backup_verify_restore_dry_run_and_post_restore_check_sqlite(tmp_path):
    source_db = tmp_path / "source.sqlite"
    conn = sqlite3.connect(str(source_db))
    try:
        conn.execute("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("INSERT INTO staff_users (username) VALUES ('admin')")
        conn.commit()
    finally:
        conn.close()

    backups_dir = tmp_path / "backups"
    rc, out, err = _run(
        "backup-db",
        "--database-url",
        f"sqlite:///{source_db}",
        "--output-dir",
        str(backups_dir),
        "--prefix",
        "test_db",
    )
    assert rc == 0, err

    payload = json.loads(out)
    backup_file = Path(payload["backup_file"])
    assert backup_file.exists()
    assert backup_file.with_suffix(backup_file.suffix + ".sha256").exists()
    assert backup_file.with_suffix(backup_file.suffix + ".metadata.json").exists()

    rc, out, err = _run("verify-backup", "--backup-file", str(backup_file))
    assert rc == 0, err
    verify_payload = json.loads(out)
    assert verify_payload["status"] == "ok"

    target_db = tmp_path / "target.sqlite"
    rc, out, err = _run(
        "restore-db",
        "--backup-file",
        str(backup_file),
        "--target-database-url",
        f"sqlite:///{target_db}",
        "--dry-run",
    )
    assert rc == 0, err

    rc, out, err = _run(
        "post-restore-check",
        "--database-url",
        f"sqlite:///{source_db}",
        "--expect-table",
        "staff_users",
    )
    assert rc == 0, err
    check_payload = json.loads(out)
    assert check_payload["status"] == "ok"
    assert check_payload["table_counts"]["staff_users"] == 1
