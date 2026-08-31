# -*- coding: utf-8 -*-
from pathlib import Path


def test_global_loader_public_contract_includes_delay_and_cancellation_hooks():
    txt = Path("static/js/ui/ui.js").read_text(encoding="utf-8")

    assert "const LOADER_DELAY_MS = 180;" in txt
    assert "let loaderTimer = null;" in txt
    assert "function clearLoaderTimer()" in txt
    assert "function scheduleLoader()" in txt
    assert "window.clearTimeout(loaderTimer);" in txt
    assert "loaderTimer = window.setTimeout(() => {" in txt
    assert "scheduleLoader();" in txt
    assert "hideLoader();" in txt
