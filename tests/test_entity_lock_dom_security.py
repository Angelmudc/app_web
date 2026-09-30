# -*- coding: utf-8 -*-

from pathlib import Path


def test_entity_lock_renders_server_message_as_text_not_html():
    source = Path("static/js/core/entity_lock.js").read_text(encoding="utf-8")
    render = source.split("function renderBanner", 1)[1].split("async function pingLock", 1)[0]
    assert "banner.textContent = ''" in render
    assert "document.createTextNode(' ' + String(message || ''))" in render
    assert "banner.innerHTML" not in render
