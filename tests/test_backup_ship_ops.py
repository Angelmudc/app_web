import json
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKUP_SCRIPT = Path("scripts/ops/backup_restore.py").resolve()
SHIP_SCRIPT = Path("scripts/ops/backup_ship.py").resolve()


def _run(script: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def test_ship_latest_local_fs_uploads_db_and_files_artifacts(tmp_path):
    db_path = tmp_path / "source.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("INSERT INTO staff_users (username) VALUES ('shiptest')")
        conn.commit()
    finally:
        conn.close()

    db_dir = tmp_path / "backups" / "db"
    files_dir = tmp_path / "backups" / "files"
    remote_dir = tmp_path / "remote"

    rc, out, err = _run(
        BACKUP_SCRIPT,
        "backup-db",
        "--database-url",
        f"sqlite:///{db_path}",
        "--output-dir",
        str(db_dir),
        "--prefix",
        "ship_db",
    )
    assert rc == 0, err

    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("ok", encoding="utf-8")

    rc, out, err = _run(
        BACKUP_SCRIPT,
        "backup-files",
        "--output-dir",
        str(files_dir),
        "--prefix",
        "ship_files",
        "--include",
        str(sample_file),
    )
    assert rc == 0, err

    rc, out, err = _run(
        SHIP_SCRIPT,
        "ship-latest",
        "--provider",
        "local_fs",
        "--db-dir",
        str(db_dir),
        "--files-dir",
        str(files_dir),
        "--local-remote-dir",
        str(remote_dir),
        "--remote-prefix",
        "app_web/backups",
        "--environment",
        "test",
        "--host-label",
        "ci-host",
    )
    assert rc == 0, err

    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["provider"] == "local_fs"
    assert payload["uploaded_count"] == 6

    uploaded_targets = [item["remote"]["target"] for item in payload["uploaded_files"]]
    for target in uploaded_targets:
        target_path = Path(target)
        assert target_path.exists()
        assert target_path.stat().st_size > 0
