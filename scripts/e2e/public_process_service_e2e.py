#!/usr/bin/env python3
"""Focused browser validation for the public service-process guide."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import app as flask_app


VIEWPORTS = [
    (320, 568), (360, 640), (375, 667), (390, 844), (414, 896), (430, 932),
    (768, 1024), (1024, 768), (1280, 720), (1366, 768), (1440, 900), (1920, 1080),
]


def main() -> int:
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/proceso-servicio"
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                for width, height in VIEWPORTS:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    if width == 320:
                        page.emulate_media(reduced_motion="reduce")
                    console_errors = []
                    page_errors = []
                    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    try:
                        response = page.goto(url, wait_until="networkidle", timeout=15000)
                        if not response or response.status != 200:
                            raise AssertionError(f"HTTP esperado=200 observado={None if not response else response.status}")
                        page.wait_for_function("() => document.querySelector('#readingHint') !== null")
                        assert page.locator("a").count() == 1
                        whatsapp_href = page.locator(".service-process-whatsapp").get_attribute("href")
                        assert whatsapp_href == "https://wa.me/18094296892"
                        assert "?text=" not in whatsapp_href
                        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
                        initial_hint_visible = page.locator("#readingHint").is_visible()
                        assert initial_hint_visible
                        page.locator("#readingHint").press("Enter")
                        page.wait_for_function("() => window.scrollY > 0")
                        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
                        page.wait_for_function("() => document.querySelector('#readingHint').hidden === true")
                        page.keyboard.press("Shift+Tab")
                        page.keyboard.press("Tab")
                        # A reduced viewport approximates the layout pressure of 200% browser zoom
                        # without applying CSS zoom, which is not equivalent to browser zoom.
                        page.set_viewport_size({"width": 320, "height": 568})
                        page.wait_for_timeout(50)
                        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 2")
                        results.append({"viewport": [width, height], "status": "PASS", "console_errors": console_errors, "page_errors": page_errors})
                    finally:
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
    report = {"total": len(results), "pass": sum(item["status"] == "PASS" for item in results), "fail": 0, "viewports": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
