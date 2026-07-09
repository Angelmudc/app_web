from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local" / "run_staging_offline_replay.py"


def test_replay_runs_isolated_without_postgres_dependency():
    env = dict(os.environ)
    env.pop("DATABASE_URL_LOCAL", None)
    env.pop("DATABASE_URL", None)
    env["APP_ENV"] = "test"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STAGING_REPLAY_SANDBOX" in proc.stdout
    assert "db=" in proc.stdout
    assert "sent=" in proc.stdout
