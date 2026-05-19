from pathlib import Path


def test_replay_bot_demo_humano_script_has_expected_guards_and_outputs():
    txt = Path("scripts/local/replay_bot_demo_humano.py").read_text(encoding="utf-8")
    assert "BOT_PRACTICE_DEMO_MODE" in txt
    assert "WHATSAPP_ENABLED\", \"false\"" in txt
    assert "BOT_DRY_RUN\", \"true\"" in txt
    assert "BOT_AUTOREPLY_ENABLED\", \"false\"" in txt
    assert "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL\", \"false\"" in txt
    assert "loops" in txt
    assert "illegal_regressions" in txt
    assert "bot_demo_humano_replay_report.json" in txt
