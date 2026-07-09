#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app


OUT = ROOT / "artifacts" / "home_visual_refine_20260628"
OUT.mkdir(parents=True, exist_ok=True)

SCENARIOS = [
    {"name": "desktop_1440", "engine": "chromium", "width": 1440, "height": 2200, "is_mobile": False},
    {"name": "desktop_1920", "engine": "chromium", "width": 1920, "height": 2400, "is_mobile": False},
    {"name": "tablet_820", "engine": "chromium", "width": 820, "height": 1180, "is_mobile": False},
    {"name": "mobile_390", "engine": "chromium", "width": 390, "height": 844, "is_mobile": True},
    {"name": "mobile_390_webkit", "engine": "webkit", "width": 390, "height": 844, "is_mobile": True},
]

SECTION_SELECTORS = {
    "hero": ".hero",
    "servicios": "#servicios",
    "proceso": "#como-funciona",
    "historias": "#historias",
    "faq": "#preguntas",
    "cta": ".cta-section",
}


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _start_server() -> tuple[object, threading.Thread, str]:
    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.8)
            if response.status_code in (200, 404):
                return server, thread, base_url
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("No se pudo levantar servidor local para validar la home.")


def _capture(playwright, scenario: dict[str, object], base_url: str) -> dict[str, object]:
    browser_type = getattr(playwright, str(scenario["engine"]))
    browser = browser_type.launch(headless=True)
    context = browser.new_context(
        viewport={"width": int(scenario["width"]), "height": int(scenario["height"])},
        is_mobile=bool(scenario["is_mobile"]),
        device_scale_factor=2 if bool(scenario["is_mobile"]) else 1,
        has_touch=bool(scenario["is_mobile"]),
    )
    page = context.new_page()
    page.goto(f"{base_url}/", wait_until="networkidle")
    page.wait_for_timeout(1400)
    page.evaluate(
        """async () => {
          const step = Math.max(320, Math.floor(window.innerHeight * 0.75));
          const max = document.documentElement.scrollHeight - window.innerHeight;
          for (let y = 0; y <= max; y += step) {
            window.scrollTo(0, y);
            await new Promise((resolve) => setTimeout(resolve, 120));
          }
          window.scrollTo(0, max);
          await new Promise((resolve) => setTimeout(resolve, 260));
          window.scrollTo(0, 0);
        }"""
    )
    page.wait_for_timeout(500)

    full_path = OUT / f"{scenario['name']}_full.png"
    cta_path = OUT / f"{scenario['name']}_cta.png"
    footer_path = OUT / f"{scenario['name']}_footer.png"

    page.screenshot(path=str(full_path), full_page=True)
    page.locator(".cta-section").scroll_into_view_if_needed()
    page.wait_for_timeout(900)
    page.locator(".cta-section").screenshot(path=str(cta_path))
    page.locator("footer.footer").scroll_into_view_if_needed()
    page.wait_for_timeout(700)
    page.locator("footer.footer").screenshot(path=str(footer_path))

    section_paths: dict[str, str] = {}
    for key, selector in SECTION_SELECTORS.items():
        section_file = OUT / f"{scenario['name']}_{key}.png"
        locator = page.locator(selector)
        locator.scroll_into_view_if_needed()
        page.wait_for_timeout(700 if key == "cta" else 420)
        locator.screenshot(path=str(section_file))
        section_paths[key] = str(section_file)

    metrics = page.evaluate(
        """() => {
          const cta = document.querySelector('.cta-section');
          const panel = document.querySelector('.cta-panel');
          const footer = document.querySelector('footer.footer');
          const body = document.body;
          const doc = document.documentElement;
          const panelStyle = panel ? getComputedStyle(panel) : null;
          const ctaStyle = cta ? getComputedStyle(cta) : null;
          return {
            scrollWidth: doc.scrollWidth,
            innerWidth: window.innerWidth,
            bodyScrollHeight: body.scrollHeight,
            ctaRect: cta ? {
              top: cta.getBoundingClientRect().top + window.scrollY,
              height: cta.getBoundingClientRect().height,
              bottom: cta.getBoundingClientRect().bottom + window.scrollY,
            } : null,
            panelRect: panel ? {
              top: panel.getBoundingClientRect().top + window.scrollY,
              height: panel.getBoundingClientRect().height,
              bottom: panel.getBoundingClientRect().bottom + window.scrollY,
              width: panel.getBoundingClientRect().width,
            } : null,
            footerRect: footer ? {
              top: footer.getBoundingClientRect().top + window.scrollY,
              height: footer.getBoundingClientRect().height,
            } : null,
            panelGridColumns: panelStyle ? panelStyle.gridTemplateColumns : null,
            panelGap: panelStyle ? panelStyle.gap : null,
            panelPaddingTop: panelStyle ? panelStyle.paddingTop : null,
            panelPaddingBottom: panelStyle ? panelStyle.paddingBottom : null,
            ctaPaddingTop: ctaStyle ? ctaStyle.paddingTop : null,
            ctaPaddingBottom: ctaStyle ? ctaStyle.paddingBottom : null,
            whatsappVisible: !!document.querySelector('.whatsapp-float'),
          };
        }"""
    )

    browser.close()
    return {
        "scenario": scenario,
        "full": str(full_path),
        "cta": str(cta_path),
        "footer": str(footer_path),
        "sections": section_paths,
        "metrics": metrics,
    }


def main() -> int:
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    server, thread, base_url = _start_server()
    try:
        with sync_playwright() as playwright:
            results = [_capture(playwright, scenario, base_url) for scenario in SCENARIOS]
        payload = {"base_url": base_url, "generated_at": int(time.time()), "results": results}
        (OUT / "validation.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
