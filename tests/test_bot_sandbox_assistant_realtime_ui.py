from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def test_sandbox_asistente_js_habilita_auto_refresh_realtime():
    txt = _read("static/js/admin_bot_sandbox_asistente.js")
    assert "AUTO_REFRESH_MS = 2500" in txt
    assert "window.setInterval" in txt
    assert "refreshAll();" in txt
    assert "__chatGlobalBadgeRuntime" in txt


def test_chat_global_badge_expone_refresh_now():
    txt = _read("static/js/chat/chat_global_badge.js")
    assert "window.__chatGlobalBadgeRuntime" in txt
    assert "refreshNow" in txt


def test_sandbox_asistente_template_define_scroll_interno():
    txt = _read("templates/admin/bot/sandbox_asistente.html")
    assert 'id="chat-thread"' in txt
    assert "max-height:62vh" in txt
    assert "overflow-y:auto" in txt
    assert 'id="review-panel"' in txt
    assert 'id="pending-list"' in txt


def test_sandbox_asistente_js_autoscroll_chat_al_ultimo_mensaje():
    txt = _read("static/js/admin_bot_sandbox_asistente.js")
    assert "lastRenderedMessageId" in txt
    assert "chatThread.scrollTop = chatThread.scrollHeight" in txt
