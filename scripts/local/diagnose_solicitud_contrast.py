#!/usr/bin/env python3
from __future__ import annotations
import json, socket, threading, time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import requests
from werkzeug.serving import make_server

from app import app as flask_app
from config_app import db
from models import StaffUser
from playwright.sync_api import sync_playwright

OUT = Path('logs/visual_debug')
OUT.mkdir(parents=True, exist_ok=True)


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return int(p)


def _ensure_user(username: str, password: str):
    with flask_app.app_context():
        u = StaffUser.query.filter_by(username=username).first()
        if u is None:
            u = StaffUser(username=username, email=f"{username}@test.local", role="owner", is_active=True, mfa_enabled=False)
            db.session.add(u)
        u.role = "owner"
        u.is_active = True
        u.set_password(password)
        db.session.commit()


def main() -> int:
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    username = "e2e_visual_admin"
    password = "Owner#12345"
    _ensure_user(username, password)

    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                r = requests.get(f"{base}/health", timeout=0.8)
                if r.status_code in (200,404):
                    break
            except Exception:
                time.sleep(0.1)

        target = f"{base}/admin/clientes/1117/solicitudes/1381"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 2200})

            page.goto(f"{base}/admin/login", wait_until="domcontentloaded")
            page.fill('input[name="usuario"]', username)
            page.fill('input[name="clave"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url("**/admin/**", timeout=15000)

            page.goto(target, wait_until="domcontentloaded")
            page.wait_for_timeout(2200)
            page.screenshot(path=str(OUT / "before.png"), full_page=True)

            cdp = page.context.new_cdp_session(page)
            cdp.send("DOM.enable")
            cdp.send("CSS.enable")

            selectors = {
                "title": ".glass-header",
                "label": "label, .form-label, dt",
                "card": ".glass-card",
                "alert": ".alert",
                "row": ".detail-focus-row, .row",
                "value": "dd, .fw-semibold, strong",
                "text_muted": ".text-muted",
                "glass_card": ".glass-card",
                "glass_header": ".glass-header",
            }

            js = """
            (sels) => {
              const out = {};
              for (const [k, sel] of Object.entries(sels)) {
                const el = document.querySelector(sel);
                if (!el) { out[k] = {found:false, selector:sel}; continue; }
                const cs = getComputedStyle(el);
                out[k] = {
                  found: true,
                  selector: sel,
                  path: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).trim().replace(/\\s+/g,'.') : ''),
                  color: cs.color,
                  backgroundColor: cs.backgroundColor,
                  opacity: cs.opacity,
                  filter: cs.filter,
                  inheritedColor: getComputedStyle(el.parentElement || el).color,
                };
              }
              return out;
            }
            """
            comp = page.evaluate(js, selectors)

            details = {}
            for key, meta in comp.items():
                if not meta.get("found"):
                    details[key] = meta
                    continue
                sel = meta["selector"]
                root = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
                q = cdp.send("DOM.querySelector", {"nodeId": root["root"]["nodeId"], "selector": sel})
                node_id = q.get("nodeId")
                if not node_id:
                    details[key] = {**meta, "matched": []}
                    continue
                matched = cdp.send("CSS.getMatchedStylesForNode", {"nodeId": node_id})
                rules = []
                for item in matched.get("matchedCSSRules", []):
                    rule = item.get("rule", {})
                    style = rule.get("style", {})
                    css_text = style.get("cssText", "") or ""
                    if any(x in css_text for x in ["color", "background", "opacity", "filter"]):
                        rules.append({
                            "selector": rule.get("selectorList", {}).get("text"),
                            "origin": rule.get("origin"),
                            "styleSheetId": style.get("styleSheetId"),
                            "range": style.get("range"),
                            "snippet": css_text[:700],
                        })
                details[key] = {**meta, "matched": rules[:20]}

            payload = {
                "url": target,
                "title": page.title(),
                "computed": details,
                "html_class": page.evaluate("() => document.documentElement.className"),
                "body_class": page.evaluate("() => document.body.className"),
                "has_summary_region": page.evaluate("() => !!document.querySelector('#solicitudSummaryAsyncRegion')"),
                "has_operativa_region": page.evaluate("() => !!document.querySelector('#solicitudOperativaCoreAsyncRegion')"),
                "has_heavy_region": page.evaluate("() => !!document.querySelector('#solicitudDetailHeavyAsyncRegion')"),
                "summary_html_len": page.evaluate("() => (document.querySelector('#solicitudSummaryAsyncRegion')?.innerHTML||'').length"),
                "operativa_html_len": page.evaluate("() => (document.querySelector('#solicitudOperativaCoreAsyncRegion')?.innerHTML||'').length"),
                "heavy_html_len": page.evaluate("() => (document.querySelector('#solicitudDetailHeavyAsyncRegion')?.innerHTML||'').length"),
            }
            (OUT / "before_diag.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            browser.close()

        print(f"OK {target}")
        return 0
    finally:
        server.shutdown(); thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
