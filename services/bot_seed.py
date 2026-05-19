"""Seed manual de configuración inicial para módulo bot (no auto-ejecutable)."""

from __future__ import annotations

from config_app import db
from models import BotSetting


DEFAULT_BOT_SETTINGS = {
    "bot_global_mode": {"value": "dry_run", "description": "Modo global del bot: disabled|dry_run|enabled"},
    "bot_autoreply_enabled": {"value": False, "description": "Habilita respuestas automáticas"},
    "bot_ai_enabled": {"value": False, "description": "Habilita integración IA"},
    "bot_whatsapp_enabled": {"value": False, "description": "Habilita integración WhatsApp"},
    "bot_require_human_for_unknown": {"value": True, "description": "Escalar contactos unknown"},
    "bot_faq_only_mode": {"value": True, "description": "Respuestas automáticas solo FAQ seguras"},
}


def seed_bot_settings(*, commit: bool = True) -> list[BotSetting]:
    created_or_updated: list[BotSetting] = []
    for key, payload in DEFAULT_BOT_SETTINGS.items():
        setting = BotSetting.query.filter_by(key=key).first()
        if not setting:
            setting = BotSetting(key=key)
            db.session.add(setting)
        setting.value_json = {"value": payload["value"]}
        setting.description = payload["description"]
        created_or_updated.append(setting)

    if commit:
        db.session.commit()
    return created_or_updated
