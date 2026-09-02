# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_SOURCE = ROOT / "static" / "js" / "core" / "admin_lazy_scripts.js"


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.env.ADMIN_LAZY_SOURCE, "utf8");

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.bubbles = !!init.bubbles;
    this.cancelable = !!init.cancelable;
    this.defaultPrevented = false;
    this.target = init.target || null;
    this.currentTarget = null;
  }
  preventDefault() {
    this.defaultPrevented = true;
  }
}

class FakeCustomEvent extends FakeEvent {}

function dataKey(name) {
  return String(name || "")
    .replace(/^data-/, "")
    .replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

function matchesSelector(node, selector) {
  if (!node || !selector) return false;
  const sel = String(selector).trim();
  if (!sel) return false;
  if (sel.includes(",")) {
    return sel.split(",").some((part) => matchesSelector(node, part));
  }
  if (sel === "#resumenCliente") return node.id === "resumenCliente";
  if (sel === ".copy-btn-interno") return (node.className || "").split(/\s+/).includes("copy-btn-interno");
  if (sel === ".js-copy-contract-link") return (node.className || "").split(/\s+/).includes("js-copy-contract-link");
  if (sel === "#deleteClienteFromListModalShared") return node.id === "deleteClienteFromListModalShared";
  if (sel === "#clientesSearchForm") return node.id === "clientesSearchForm";
  if (sel === "[data-candidata-center]") return !!node.getAttribute && node.getAttribute("data-candidata-center") !== null;
  if (sel === "[data-admin-lazy-fragment-url]") return !!node.getAttribute && !!node.getAttribute("data-admin-lazy-fragment-url");
  if (sel === "[data-admin-lazy-status]") return !!node.getAttribute && node.getAttribute("data-admin-lazy-status") !== null;
  if (sel === "[data-admin-lazy-retry]") return !!node.getAttribute && node.getAttribute("data-admin-lazy-retry") !== null;
  if (sel === "[data-lazy-script-candidata-detail-ui]") return !!node.getAttribute && node.getAttribute("data-lazy-script-candidata-detail-ui") !== null;
  if (sel === "[data-live-refresh='1']") return !!node.getAttribute && node.getAttribute("data-live-refresh") === "1";
  if (sel === "script[src]") return node.tagName === "SCRIPT" && !!node.getAttribute("src");
  return false;
}

class FakeNode {
  constructor(tagName = "div", attrs = {}) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.parentNode = null;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.isConnected = true;
    this.id = "";
    this.className = "";
    this._textContent = "";
    this._innerHTML = "";
    for (const [key, value] of Object.entries(attrs || {})) {
      this.setAttribute(key, value);
    }
  }

  set src(value) {
    this.setAttribute("src", value);
  }

  get src() {
    return this.getAttribute("src");
  }

  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
    child.isConnected = true;
    this.children.push(child);
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    }
    this.parentNode = null;
    this.isConnected = false;
  }

  contains(node) {
    if (!node) return false;
    if (node === this) return true;
    for (const child of this.children) {
      if (child.contains(node)) return true;
    }
    return false;
  }

  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }

  dispatchEvent(ev) {
    if (!ev) return true;
    if (!ev.target) ev.target = this;
    ev.currentTarget = this;
    const handlers = this.listeners[ev.type] || [];
    for (const fn of handlers.slice()) {
      fn.call(this, ev);
    }
    return !ev.defaultPrevented;
  }

  setAttribute(name, value) {
    const key = String(name);
    const val = String(value);
    this.attributes[key] = val;
    if (key === "id") this.id = val;
    if (key === "class") this.className = val;
    if (key.startsWith("data-")) {
      this.dataset[dataKey(key)] = val;
    }
  }

  getAttribute(name) {
    const key = String(name);
    if (Object.prototype.hasOwnProperty.call(this.attributes, key)) return this.attributes[key];
    return null;
  }

  removeAttribute(name) {
    const key = String(name);
    delete this.attributes[key];
    if (key === "id") this.id = "";
    if (key === "class") this.className = "";
    if (key.startsWith("data-")) {
      delete this.dataset[dataKey(key)];
    }
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches && node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }

  querySelectorAll(selector) {
    const seen = [];
    const push = (node) => {
      if (!seen.includes(node)) seen.push(node);
    };
    const selectors = String(selector || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    const visit = (node) => {
      if (!node) return;
      for (const part of selectors) {
        if (matchesSelector(node, part)) {
          push(node);
          break;
        }
      }
      for (const child of node.children || []) visit(child);
    };
    visit(this);
    return seen;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value);
  }
}

class FakeDocument extends FakeNode {
  constructor() {
    super("#document", {});
    this.documentElement = new FakeNode("html", {});
    this.body = new FakeNode("body", {});
    this.head = new FakeNode("head", {});
    this.readyState = "complete";
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.children = [this.documentElement];
  }

  createElement(tag) {
    return new FakeNode(tag, {});
  }

  querySelectorAll(selector) {
    const matches = [];
    for (const root of [this.head, this.body, this.documentElement]) {
      for (const node of root.querySelectorAll(selector)) {
        if (!matches.includes(node)) matches.push(node);
      }
    }
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  getElementById(id) {
    return this.querySelector("#" + id);
  }
}

function createAbortError() {
  if (typeof DOMException === "function") {
    return new DOMException("The operation was aborted.", "AbortError");
  }
  const err = new Error("The operation was aborted.");
  err.name = "AbortError";
  return err;
}

function createFetchMock(mode) {
  const calls = [];
  let pendingResolve = null;
  let pendingReject = null;
  let callIndex = 0;
  const fetch = (url, options = {}) => {
    const call = { url, options, index: callIndex++ };
    calls.push(call);
    const next = Array.isArray(mode) ? mode[Math.min(call.index, mode.length - 1)] : mode;
    if (next.type === "pending") {
      return new Promise((resolve, reject) => {
        pendingResolve = resolve;
        pendingReject = reject;
        if (options.signal) {
          if (options.signal.aborted) {
            call.aborted = true;
            reject(createAbortError());
            return;
          }
          options.signal.addEventListener("abort", () => {
            call.aborted = true;
            reject(createAbortError());
          }, { once: true });
        }
      });
    }
    if (options.signal) {
      if (options.signal.aborted) {
        call.aborted = true;
        return Promise.reject(createAbortError());
      }
      options.signal.addEventListener("abort", () => {
        call.aborted = true;
      }, { once: true });
    }
    if (next.type === "abort-error") {
      return Promise.reject(createAbortError());
    }
    if (next.type === "error") {
      return Promise.reject(new Error(next.message || "boom"));
    }
    return Promise.resolve({
      ok: true,
      text: async () => next.html || "<div class='frag-ok'>ok</div>",
    });
  };
  return {
    fetch,
    calls,
    resolvePending(html) {
      if (pendingResolve) {
        pendingResolve({
          ok: true,
          text: async () => html || "<div class='frag-ok'>ok</div>",
        });
      }
    },
    rejectPending(err) {
      if (pendingReject) pendingReject(err);
    },
  };
}

function bootstrap(options) {
  const doc = new FakeDocument();
  const timers = [];
  const idleCalls = [];
  const fetchMock = createFetchMock(options.fetchMode || { type: "success" });
  const win = {
    document: doc,
    window: null,
    self: null,
    globalThis: null,
    console,
    AbortController,
    DOMException,
    fetch: fetchMock.fetch,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay, cleared: false });
      return id;
    },
    clearTimeout(id) {
      const item = timers.find((timer) => timer.id === id);
      if (item) item.cleared = true;
    },
    requestIdleCallback: options.useRequestIdleCallback
      ? (fn, cfg) => {
          idleCalls.push({ timeout: cfg && cfg.timeout });
          return idleCalls.length;
        }
      : undefined,
    listeners: {},
  };
  win.addEventListener = function (type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  };
  win.dispatchEvent = function (ev) {
    if (!ev) return true;
    if (!ev.target) ev.target = this;
    ev.currentTarget = this;
    const handlers = this.listeners[ev.type] || [];
    for (const fn of handlers.slice()) {
      fn.call(this, ev);
    }
    return !ev.defaultPrevented;
  };
  win.window = win;
  win.self = win;
  win.globalThis = win;
  doc.defaultView = win;

  class MockIntersectionObserver {
    constructor(cb, opts) {
      this.cb = cb;
      this.opts = opts;
      this.observed = [];
      this.unobserved = [];
      this.disconnected = false;
      MockIntersectionObserver.instances.push(this);
    }
    observe(node) {
      if (!this.observed.includes(node)) this.observed.push(node);
    }
    unobserve(node) {
      this.unobserved.push(node);
    }
    disconnect() {
      this.disconnected = true;
    }
    trigger(entries) {
      this.cb(entries);
    }
  }
  MockIntersectionObserver.instances = [];
  if (options.useIntersectionObserver) {
    win.IntersectionObserver = MockIntersectionObserver;
  }

  const ctx = {
    window: win,
    document: doc,
    console,
    AbortController,
    DOMException,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    fetch: fetchMock.fetch,
    setTimeout: win.setTimeout.bind(win),
    clearTimeout: win.clearTimeout.bind(win),
    requestIdleCallback: win.requestIdleCallback,
    IntersectionObserver: win.IntersectionObserver,
  };
  ctx.globalThis = win;
  ctx.self = win;

  const region = new FakeNode("div", {
    id: "lazyRegion",
    "data-admin-lazy-fragment-url": "/admin/candidatas/990539/_entrevistas",
    "data-admin-lazy-loading-label": "Cargando...",
  });
  const status = new FakeNode("div", { "data-admin-lazy-status": "" });
  region.appendChild(status);
  doc.body.appendChild(region);
  if (options.includeCandidateDetailMarker) {
    const candidateRoot = new FakeNode("div", {
      "data-candidata-center": "",
      "data-lazy-script-candidata-detail-ui": "/static/js/admin/candidatas_operativo_detail_ui.js",
    });
    doc.body.appendChild(candidateRoot);
  }

  vm.runInNewContext(source, ctx, { filename: "admin_lazy_scripts.js" });

  function flushTimers() {
    let index = 0;
    while (index < timers.length) {
      const timer = timers[index++];
      if (timer.cleared) continue;
      timer.fn();
    }
  }

  function getObserver() {
    return MockIntersectionObserver.instances[0] || null;
  }

  function clickRetry() {
    const retryButton = new FakeNode("button", { "data-admin-lazy-retry": "" });
    retryButton.closest = (selector) => {
      if (selector === "[data-admin-lazy-retry]") return retryButton;
      if (selector === "[data-admin-lazy-fragment-url]") return region;
      return null;
    };
    doc.dispatchEvent(new FakeEvent("click", { target: retryButton }));
  }

  return { win, doc, region, status, timers, idleCalls, fetchMock, flushTimers, getObserver, clickRetry };
}

async function waitTick() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function runScenario(name) {
  if (name === "io_no_autoload") {
    const env = bootstrap({ useIntersectionObserver: true, useRequestIdleCallback: true, fetchMode: { type: "success" } });
    const observer = env.getObserver();
    return {
      rootMargin: observer ? observer.opts.rootMargin : null,
      observed: observer ? observer.observed.length : 0,
      fetches: env.fetchMock.calls.length,
      timers: env.timers.map((t) => t.delay),
      idleCalls: env.idleCalls.length,
    };
  }

  if (name === "io_intersect_fetches") {
    const env = bootstrap({ useIntersectionObserver: true, useRequestIdleCallback: true, fetchMode: { type: "success", html: "<div>fragment</div>" } });
    const observer = env.getObserver();
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      url: env.fetchMock.calls[0] ? env.fetchMock.calls[0].url : null,
      loaded: env.region.dataset.lazyLoaded || null,
      state: env.region.dataset.lazyState || null,
      html: env.region.innerHTML,
    };
  }

  if (name === "io_no_double_fetch") {
    const env = bootstrap({ useIntersectionObserver: true, useRequestIdleCallback: true, fetchMode: { type: "pending" } });
    const observer = env.getObserver();
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      inflight: env.region.dataset.lazyInflight,
      observed: observer.observed.length,
    };
  }

  if (name === "fallback_without_io") {
    const env = bootstrap({ useIntersectionObserver: false, useRequestIdleCallback: false, fetchMode: { type: "success", html: "<div>fallback</div>" } });
    env.flushTimers();
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      timers: env.timers.map((t) => t.delay),
      html: env.region.innerHTML,
      state: env.region.dataset.lazyState || null,
    };
  }

  if (name === "abort_on_navigation") {
    const env = bootstrap({ useIntersectionObserver: true, useRequestIdleCallback: true, fetchMode: { type: "pending" } });
    const observer = env.getObserver();
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    const replacement = new FakeNode("div", { id: "replacementViewport" });
    env.doc.dispatchEvent(new FakeCustomEvent("admin:navigation-complete", { detail: { viewport: replacement } }));
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      aborted: !!(env.fetchMock.calls[0] && env.fetchMock.calls[0].aborted),
      state: env.region.dataset.lazyState || null,
      retry: env.region.innerHTML.indexOf("data-admin-lazy-retry") >= 0,
    };
  }

  if (name === "abort_error_silent") {
    const env = bootstrap({ useIntersectionObserver: true, useRequestIdleCallback: true, fetchMode: { type: "abort-error" } });
    const observer = env.getObserver();
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      html: env.region.innerHTML,
      state: env.region.dataset.lazyState || null,
      retry: env.region.innerHTML.indexOf("data-admin-lazy-retry") >= 0,
    };
  }

  if (name === "real_error_retry") {
    const env = bootstrap({
      useIntersectionObserver: true,
      useRequestIdleCallback: true,
      fetchMode: [
        { type: "error", message: "boom" },
        { type: "success", html: "<div>retry-ok</div>" },
      ],
    });
    const observer = env.getObserver();
    observer.trigger([{ target: env.region, isIntersecting: true }]);
    await waitTick();
    const firstHtml = env.region.innerHTML;
    env.clickRetry();
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      firstHtml,
      html: env.region.innerHTML,
      state: env.region.dataset.lazyState || null,
      retry: env.region.innerHTML.indexOf("data-admin-lazy-retry") >= 0,
    };
  }

  if (name === "candidate_detail_marker") {
    const env = bootstrap({
      useIntersectionObserver: true,
      useRequestIdleCallback: true,
      fetchMode: { type: "success" },
      includeCandidateDetailMarker: true,
    });
    return {
      scripts: env.doc.head.querySelectorAll("script[src]").map((node) => node.getAttribute("src")),
    };
  }

  throw new Error("unknown scenario: " + name);
}

runScenario(process.argv[2]).then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((err) => {
  process.stderr.write(String(err && err.stack || err));
  process.exit(1);
});
"""


def _run_node_case(case: str) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "admin_lazy_runtime.js"
        script_path.write_text(NODE_HARNESS, encoding="utf-8")
        env = dict(os.environ)
        env["ADMIN_LAZY_SOURCE"] = str(JS_SOURCE)
        proc = subprocess.run(
            ["node", str(script_path), case],
            cwd=str(ROOT),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(f"node failed for {case}: {proc.stderr.strip()}")
        return json.loads(proc.stdout or "{}")


def test_admin_lazy_scripts_no_autoload_with_intersection_observer():
    data = _run_node_case("io_no_autoload")
    assert data["rootMargin"] == "600px 0px"
    assert data["observed"] == 1
    assert data["fetches"] == 0
    assert 60 not in data["timers"]


def test_admin_lazy_scripts_fetches_when_fragment_intersects():
    data = _run_node_case("io_intersect_fetches")
    assert data["fetches"] == 1
    assert data["url"] == "/admin/candidatas/990539/_entrevistas"
    assert data["loaded"] == "1"
    assert data["state"] == "loaded"
    assert "fragment" in data["html"]


def test_admin_lazy_scripts_fallback_without_intersection_observer():
    data = _run_node_case("fallback_without_io")
    assert data["fetches"] == 1
    assert data["state"] == "loaded"
    assert "fallback" in data["html"]


def test_admin_lazy_scripts_does_not_double_fetch_while_inflight():
    data = _run_node_case("io_no_double_fetch")
    assert data["fetches"] == 1
    assert data["inflight"] == "1" or data["inflight"] == 1


def test_admin_lazy_scripts_aborts_pending_fetch_on_navigation():
    data = _run_node_case("abort_on_navigation")
    assert data["fetches"] == 1
    assert data["aborted"] is True
    assert data["state"] == "idle"
    assert data["retry"] is False


def test_admin_lazy_scripts_ignores_aborterror_without_error_ui():
    data = _run_node_case("abort_error_silent")
    assert data["fetches"] == 1
    assert data["state"] == "idle"
    assert data["retry"] is False
    assert "Reintentar" not in data["html"]


def test_admin_lazy_scripts_keeps_retry_on_real_error():
    data = _run_node_case("real_error_retry")
    assert data["fetches"] == 2
    assert "Reintentar" in data["firstHtml"]
    assert "retry-ok" in data["html"]
    assert data["state"] == "loaded"


def test_admin_lazy_scripts_loads_candidate_detail_runtime_marker():
    data = _run_node_case("candidate_detail_marker")
    assert "/static/js/admin/candidatas_operativo_detail_ui.js" in data["scripts"]
