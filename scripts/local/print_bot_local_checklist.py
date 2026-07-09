from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKLIST_PATH = ROOT_DIR / "docs" / "bot_required_local_checklist.md"


def build_output() -> str:
    return "\n".join(
        [
            "BOT_LOCAL_CHECKLIST_HELPER",
            f"checklist={CHECKLIST_PATH}",
            "",
            "Comandos recomendados:",
            "- venv/bin/python scripts/local/run_bot_local_qa.py --fast",
            "- venv/bin/python scripts/local/run_bot_local_qa.py",
            "",
            "Flags seguros obligatorios:",
            "- APP_ENV=development/test",
            "- WHATSAPP_ENABLED=false",
            "- BOT_AUTOREPLY_ENABLED=false",
            "- BOT_AI_ENABLED=false",
            "- BOT_DRY_RUN=true",
            "- BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false",
            "",
            "Advertencia:",
            "- No usar producción, no WhatsApp real, no IA automática, no creación real automática.",
        ]
    )


def main() -> int:
    print(build_output())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
