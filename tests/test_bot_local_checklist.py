from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "bot_required_local_checklist.md"
HELPER = ROOT / "scripts" / "local" / "print_bot_local_checklist.py"


def test_checklist_document_exists() -> None:
    assert CHECKLIST.exists()


def test_checklist_contains_key_commands() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    assert "venv/bin/python scripts/local/run_bot_local_qa.py --fast" in text
    assert "venv/bin/python scripts/local/run_bot_local_qa.py" in text
    assert "BOT_SIMULATOR_BASELINE: OK" in text
    assert "BOT_SIMULATOR_REGRESSION: OK" in text
    assert "100/100" in text


def test_helper_prints_expected_references() -> None:
    proc = subprocess.run(
        [sys.executable, str(HELPER)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "BOT_LOCAL_CHECKLIST_HELPER" in out
    assert "docs/bot_required_local_checklist.md" in out
    assert "run_bot_local_qa.py --fast" in out
    assert "WHATSAPP_ENABLED=false" in out
    assert "No usar producción" in out
