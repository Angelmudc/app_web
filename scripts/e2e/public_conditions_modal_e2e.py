#!/usr/bin/env python3
"""Dedicated Playwright matrix for the public employment-conditions modal."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

os.environ.pop("DATABASE_URL", None)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.e2e import public_intake_extreme_e2e as base

RUN_ID = base.RUN_ID
ARTIFACT_DIR = ROOT / "artifacts" / "e2e" / "public_conditions_modal" / RUN_ID
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
VIEWPORTS = [
    (320, 568), (360, 640), (375, 667), (390, 844), (414, 896), (430, 932),
    (768, 1024), (1024, 768), (1280, 720), (1366, 768), (1440, 900), (1920, 1080),
]


def build_cases() -> list[base.CaseSpec]:
    cases = []
    for index in range(60):
        viewport = VIEWPORTS[index % len(VIEWPORTS)]
        client_type = "nuevo" if index < 30 else "existente"
        block_cdn = index in {28, 29, 58, 59}
        block_local = index in {29, 59}
        interaction = "indicator" if index % 4 == 0 else "keyboard" if index % 4 == 1 else "end" if index % 4 == 2 else "cancel_reopen"
        cases.append(base.CaseSpec(
            case_id=f"MODAL-{index + 1:03d}",
            purpose="condiciones publicas: overflow, lectura, resize y accesibilidad",
            client_type=client_type,
            viewport=viewport,
            valid=True,
            block_cdn=block_cdn,
            block_local_bootstrap=block_local,
            interaction=interaction,
            profile=index,
            category="nuevo" if client_type == "nuevo" else "existente",
        ))
    return cases


def run_case(browser, flask_app, base_url, catalog, case, client, token) -> dict[str, Any]:
    context = browser.new_context(viewport={"width": case.viewport[0], "height": case.viewport[1]})
    page = context.new_page()
    posts: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[dict[str, Any]] = []
    modal_id = "#publicEmploymentConditionsNuevaModal" if case.client_type == "nuevo" else "#publicEmploymentConditionsModal"
    try:
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("response", lambda response: http_errors.append({"status": response.status, "url": response.url}) if response.status >= 400 else None)
        if case.block_cdn:
            page.route("**://cdn.jsdelivr.net/**", lambda route: route.abort() if route.request.resource_type == "script" else route.continue_())
        if case.block_local_bootstrap:
            page.route("**/static/vendor/bootstrap/bootstrap.bundle.min.js", lambda route: route.abort())
        page.goto(base.url_for_case(base_url, token, case), wait_until="domcontentloaded", timeout=30000)
        base.fill_valid_form(page, catalog, case, client)
        submit = page.locator("#publicSubmitNuevaBtn" if case.client_type == "nuevo" else "#publicSubmitBtn")
        submit.click()
        modal = page.locator(modal_id)
        page.wait_for_function("selector => { const el = document.querySelector(selector); return el && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden'; }", arg=modal_id, timeout=5000)
        page.wait_for_function(
            "selectors => {"
            " const body = document.querySelector(selectors.body);"
            " const confirm = document.querySelector(selectors.confirm);"
            " const indicator = document.querySelector(selectors.indicator);"
            " if (!body || !confirm || !indicator) return false;"
            " const overflow = body.scrollHeight > body.clientHeight + 10;"
            " return overflow === confirm.disabled && indicator.hidden === !overflow;"
            "}",
            arg={
                "body": f"{modal_id} .employment-conditions-body",
                "confirm": f"{modal_id} .employment-conditions-confirm",
                "indicator": f"{modal_id} .employment-conditions-read-more",
            },
            timeout=5000,
        )
        body = modal.locator(".employment-conditions-body")
        confirm = modal.locator(".employment-conditions-confirm")
        indicator = modal.locator(".employment-conditions-read-more")
        state = body.evaluate("el => ({scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight})")
        overflow = state["scrollHeight"] > state["clientHeight"] + 10
        if overflow != (not confirm.is_enabled()):
            raise RuntimeError(f"estado inicial inconsistente overflow={overflow} disabled={not confirm.is_enabled()}")
        if indicator.is_hidden() == overflow:
            raise RuntimeError("indicador no coincide con el overflow inicial")

        if overflow:
            body.evaluate("el => { el.scrollTop = Math.min(40, el.scrollHeight); el.dispatchEvent(new Event('scroll')); }")
            if confirm.is_enabled() and state["scrollHeight"] - state["clientHeight"] > 40:
                raise RuntimeError("confirmar se habilitó antes del final")
            body.evaluate("el => { el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight - 12); el.dispatchEvent(new Event('scroll')); }")
            if confirm.is_enabled():
                raise RuntimeError("confirmar se habilitó fuera de la tolerancia")
            indicator.click()
            page.wait_for_function("selector => { const el = document.querySelector(selector); return el && el.scrollTop + el.clientHeight >= el.scrollHeight - 10; }", arg=f"{modal_id} .employment-conditions-body", timeout=5000)
            if not confirm.is_enabled() or not indicator.is_hidden():
                raise RuntimeError("el final real no desbloqueó correctamente")
            body.evaluate("el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }")
            if not confirm.is_enabled():
                raise RuntimeError("confirmar se volvió a bloquear después de leer")

        # Resize is exercised before confirmation for a subset of cases.
        if case.profile % 6 == 0:
            page.set_viewport_size({"width": 1920 if case.viewport[0] < 1000 else 320, "height": 1080 if case.viewport[0] < 1000 else 568})
            page.wait_for_timeout(100)
            page.evaluate("selector => { const el = document.querySelector(selector); el.dispatchEvent(new Event('scroll')); }", f"{modal_id} .employment-conditions-body")

        if case.interaction == "cancel_reopen":
            modal.get_by_role("button", name="Volver al formulario", exact=True).click()
            page.wait_for_function("selector => { const el = document.querySelector(selector); return el && (getComputedStyle(el).display === 'none' || el.getAttribute('aria-hidden') === 'true'); }", arg=modal_id, timeout=5000)
            if posts:
                raise RuntimeError("cancelar produjo POST")
            submit.click()
            page.wait_for_function("selector => { const el = document.querySelector(selector); return el && getComputedStyle(el).display !== 'none'; }", arg=modal_id, timeout=5000)
            page.wait_for_function(
                "selectors => {"
                " const body = document.querySelector(selectors.body);"
                " const confirm = document.querySelector(selectors.confirm);"
                " const indicator = document.querySelector(selectors.indicator);"
                " if (!body || !confirm || !indicator) return false;"
                " const overflow = body.scrollHeight > body.clientHeight + 10;"
                " return body.scrollTop <= 1 && overflow === confirm.disabled && indicator.hidden === !overflow;"
                "}",
                arg={
                    "body": f"{modal_id} .employment-conditions-body",
                    "confirm": f"{modal_id} .employment-conditions-confirm",
                    "indicator": f"{modal_id} .employment-conditions-read-more",
                },
                timeout=5000,
            )
            reopened = modal.locator(".employment-conditions-body").evaluate("el => ({top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight})")
            reopened_overflow = reopened["height"] > reopened["client"] + 10
            if reopened["top"] != 0 or (reopened_overflow and modal.locator(".employment-conditions-confirm").is_enabled()):
                raise RuntimeError("reapertura no reseteó lectura")
            # Read the reopened modal through the actual indicator/End path.
            reopened_body = modal.locator(".employment-conditions-body")
            modal.locator(".employment-conditions-read-more").click()
            page.wait_for_function(
                "selectors => {"
                " const confirm = document.querySelector(selectors.confirm);"
                " const indicator = document.querySelector(selectors.indicator);"
                " return confirm && indicator && !confirm.disabled && indicator.hidden;"
                "}",
                arg={
                    "confirm": f"{modal_id} .employment-conditions-confirm",
                    "indicator": f"{modal_id} .employment-conditions-read-more",
                },
                timeout=5000,
            )
            if reopened_overflow and not modal.locator(".employment-conditions-confirm").is_enabled():
                raise RuntimeError("reapertura no permitió completar lectura")

        modal.locator(".employment-conditions-confirm").click()
        page.wait_for_timeout(500)
        non_plan_posts = [url for url in posts if "/plan" not in url]
        if len(non_plan_posts) != 1:
            raise RuntimeError(f"POST esperado=1 observado={non_plan_posts!r}")
        return {"ok": True, "case": asdict(case), "overflow": overflow, "post_count": len(non_plan_posts), "console_errors": console_errors, "page_errors": page_errors, "http_errors": http_errors}
    except Exception as exc:
        return {"ok": False, "case": asdict(case), "error": str(exc), "traceback": traceback.format_exc(), "post_count": len([url for url in posts if "/plan" not in url]), "console_errors": console_errors, "page_errors": page_errors, "http_errors": http_errors}
    finally:
        context.close()


def main() -> int:
    if (os.getenv("APP_ENV") or "").strip().lower() != "local":
        raise RuntimeError("Modal E2E requiere APP_ENV=local")
    db_url, flask_app = base.safe_database_check()
    flask_app.config.update(TESTING=False, WTF_CSRF_ENABLED=False, ADMIN_LIVE_SSE_ENABLED=False, CLIENTES_LIVE_SSE_ENABLED=False)
    cases = build_cases()
    existing_clients = []
    server = None
    thread = None
    results = []
    try:
        server, thread, base_url = base.start_internal_server(flask_app)
        with flask_app.app_context():
            existing_clients = base.seed_existing_clients(flask_app, RUN_ID, 30)
        jobs = base.prepare_case_jobs(flask_app, cases, existing_clients)
        base.validate_prepared_jobs(jobs)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                with flask_app.app_context():
                    from clientes.routes import generar_token_publico_cliente_nuevo
                    probe_token = generar_token_publico_cliente_nuevo(created_by=f"{base.MARKER}-PROBE")
                probe = browser.new_page(viewport={"width": 1440, "height": 900})
                probe.goto(f"{base_url}/clientes/solicitudes/nueva-publica/{probe_token}", wait_until="domcontentloaded", timeout=30000)
                catalog = base.discover_catalog(probe)
                probe.close()
                for job in jobs:
                    results.append(run_case(browser, flask_app, base_url, catalog, job["case"], job["client"], job["token"]))
            finally:
                browser.close()
    finally:
        cleanup = base.cleanup(flask_app)
        if server is not None:
            server.shutdown()
        report = {"run_id": RUN_ID, "total": len(results), "pass": sum(1 for result in results if result.get("ok")), "fail": sum(1 for result in results if not result.get("ok")), "cases": results, "cleanup": cleanup, "database": base.redact_db_url(db_url)}
        (ARTIFACT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"MODAL COMPLETA: {report['pass']}/{report['total']} PASS", flush=True)
        print(f"Artifacts: {ARTIFACT_DIR}", flush=True)
        if report["fail"] or not cleanup.get("ok"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
