# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import requests
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app as flask_app


if os.getenv("RUN_SEGUIMIENTO_PERF_E2E", "0").strip() != "1":
    pytest.skip(
        "Seguimiento perf E2E deshabilitado. Ejecuta con RUN_SEGUIMIENTO_PERF_E2E=1.",
        allow_module_level=True,
    )


@dataclass
class PerfReq:
    url: str
    method: str
    started_ms: float
    ended_ms: float
    status: int

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.ended_ms - self.started_ms)


@dataclass
class ActionPerf:
    name: str
    total_ms: float
    network_window_ms: float
    frontend_overhead_ms: float
    requests: list[PerfReq]


@pytest.fixture(scope="module")
def live_server():
    server = make_server("127.0.0.1", 5077, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:5077"

    deadline = time.time() + 15
    last_exc = None
    while time.time() < deadline:
        try:
            r = requests.get(base_url + "/admin/login", timeout=2)
            if r.status_code in (200, 302):
                break
        except Exception as exc:  # pragma: no cover
            last_exc = exc
        time.sleep(0.2)
    else:
        server.shutdown()
        raise RuntimeError(f"No fue posible iniciar servidor E2E: {last_exc}")

    try:
        yield base_url
    finally:
        server.shutdown()


def _login_admin(page, base_url: str):
    user = os.getenv("E2E_ADMIN_USER", "Karla")
    pwd = os.getenv("E2E_ADMIN_PASS", "9989")
    page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
    page.fill('input[name="usuario"]', user)
    page.fill('input[name="clave"]', pwd)
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin/**", timeout=15000)


def _canonical_endpoint(url: str) -> str:
    if "/admin/seguimiento-candidatas/cola.json" in url:
        return "cola.json"
    if "/admin/seguimiento-candidatas/badge.json" in url:
        return "badge.json"
    if "/admin/seguimiento-candidatas/casos" in url and "/admin/seguimiento-candidatas/casos/" not in url:
        return "casos_post"
    return "other"


def _measure_action(page, name: str, trigger, wait_done, request_filter) -> ActionPerf:
    records: list[PerfReq] = []
    starts: dict[Any, float] = {}

    def on_request(req):
        starts[req] = time.perf_counter() * 1000.0

    def on_finished(req):
        st = starts.get(req)
        if st is None:
            return
        if not request_filter(req.url):
            return
        resp = req.response()
        status = int(resp.status) if resp else 0
        records.append(
            PerfReq(
                url=req.url,
                method=req.method,
                started_ms=st,
                ended_ms=time.perf_counter() * 1000.0,
                status=status,
            )
        )

    page.on("request", on_request)
    page.on("requestfinished", on_finished)

    t0 = time.perf_counter() * 1000.0
    trigger()
    wait_done()
    t1 = time.perf_counter() * 1000.0

    page.remove_listener("request", on_request)
    page.remove_listener("requestfinished", on_finished)

    if records:
        net_start = min(r.started_ms for r in records)
        net_end = max(r.ended_ms for r in records)
        net_window = max(0.0, net_end - net_start)
    else:
        net_window = 0.0

    total = max(0.0, t1 - t0)
    front = max(0.0, total - net_window)
    return ActionPerf(name=name, total_ms=total, network_window_ms=net_window, frontend_overhead_ms=front, requests=records)


def _summarize(action: ActionPerf) -> dict[str, Any]:
    by_ep: dict[str, list[PerfReq]] = {}
    for r in action.requests:
        key = _canonical_endpoint(r.url)
        by_ep.setdefault(key, []).append(r)

    endpoints = []
    duplicates = {}
    for ep, reqs in by_ep.items():
        durs = [x.duration_ms for x in reqs]
        endpoints.append(
            {
                "endpoint": ep,
                "count": len(reqs),
                "avg_ms": round(sum(durs) / len(durs), 2),
                "max_ms": round(max(durs), 2),
            }
        )
        if len(reqs) > 1:
            duplicates[ep] = len(reqs)

    endpoints.sort(key=lambda x: x["max_ms"], reverse=True)
    return {
        "action": action.name,
        "total_ms": round(action.total_ms, 2),
        "network_window_ms": round(action.network_window_ms, 2),
        "frontend_overhead_ms": round(action.frontend_overhead_ms, 2),
        "request_count": len(action.requests),
        "endpoints": endpoints,
        "duplicates": duplicates,
    }


@pytest.mark.e2e
def test_seguimiento_candidatas_perf_e2e(live_server):
    base_url = live_server
    run_tag = uuid.uuid4().hex[:8]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        _login_admin(page, base_url)
        page.goto(f"{base_url}/admin/seguimiento-candidatas/cola", wait_until="domcontentloaded")
        page.wait_for_selector("#segCandidatasIslandBtn", timeout=15000)

        open_action = _measure_action(
            page,
            name="open_drawer",
            trigger=lambda: page.click("#segCandidatasIslandBtn"),
            wait_done=lambda: page.wait_for_selector("#segCandidatasContent:not(.d-none)", timeout=20000),
            request_filter=lambda u: "/admin/seguimiento-candidatas/cola.json" in u or "/admin/seguimiento-candidatas/badge.json" in u,
        )

        page.click('[data-seg-tab-btn="create"]')
        phone = f"80977{run_tag[:5]}"
        page.fill('#segCreateNombre', f"Perf {run_tag}")
        page.fill('#segCreateTelefono', phone)
        page.fill('#segCreateQuePidio', 'Evaluacion perf')
        page.fill('#segCreateNota', 'Medicion E2E sin cambios de logica')
        page.fill('#segCreateNextAction', 'devolver_llamada')

        create_action = _measure_action(
            page,
            name="quick_create_case",
            trigger=lambda: page.click('#segQuickCreateSubmitBtn'),
            wait_done=lambda: page.wait_for_selector('#segQuickCreateFeedback.alert-success, #segQuickCreateFeedback.alert-warning', timeout=20000),
            request_filter=lambda u: "/admin/seguimiento-candidatas/casos" in u or "/admin/seguimiento-candidatas/cola.json" in u or "/admin/seguimiento-candidatas/badge.json" in u,
        )

        refresh_action = _measure_action(
            page,
            name="drawer_refresh",
            trigger=lambda: page.evaluate("() => document.dispatchEvent(new CustomEvent('admin:live-invalidation-event', {detail:{event:{event_type:'staff.case_tracking.case_created'}}}))"),
            wait_done=lambda: page.wait_for_timeout(1800),
            request_filter=lambda u: "/admin/seguimiento-candidatas/cola.json" in u or "/admin/seguimiento-candidatas/badge.json" in u,
        )

        browser.close()

    summary = {
        "meta": {
            "base_url": base_url,
            "ts_epoch": int(time.time()),
        },
        "actions": [
            _summarize(open_action),
            _summarize(create_action),
            _summarize(refresh_action),
        ],
    }

    print("\nSEGUIMIENTO_PERF_E2E_SUMMARY=" + json.dumps(summary, ensure_ascii=False))

    assert any(a["request_count"] > 0 for a in summary["actions"])
