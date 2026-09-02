# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_SOURCE = ROOT / "static" / "js" / "core" / "admin_nav.js"


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.env.ADMIN_NAV_SOURCE, "utf8");

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.target = init.target || null;
    this.currentTarget = null;
    this.relatedTarget = init.relatedTarget || null;
    this.button = init.button || 0;
    this.metaKey = !!init.metaKey;
    this.ctrlKey = !!init.ctrlKey;
    this.shiftKey = !!init.shiftKey;
    this.altKey = !!init.altKey;
    this.defaultPrevented = false;
    this.bubbles = init.bubbles !== undefined ? !!init.bubbles : true;
    this.state = init.state;
  }
  preventDefault() {
    this.defaultPrevented = true;
  }
}

class FakeCustomEvent extends FakeEvent {}

function dataKey(name) {
  return String(name || "").replace(/^data-/, "").replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

function matchesSelector(node, selector) {
  if (!node || !selector) return false;
  const sel = String(selector || "").trim();
  if (!sel) return false;
  if (sel.includes(",")) return sel.split(",").some((part) => matchesSelector(node, part));
  if (sel === "[data-admin-nav-viewport='true']") return !!node.getAttribute && node.getAttribute("data-admin-nav-viewport") === "true";
  if (sel === "a[data-admin-nav='true']") return node.tagName === "A" && !!node.getAttribute && node.getAttribute("data-admin-nav") === "true";
  if (sel === "[data-admin-focus-anchor]") return !!node.getAttribute && node.getAttribute("data-admin-focus-anchor") !== null;
  if (sel === "input[name], select[name], textarea[name]") return ["INPUT", "SELECT", "TEXTAREA"].includes(node.tagName) && !!node.getAttribute && !!node.getAttribute("name");
  if (sel === "input[name]") return node.tagName === "INPUT" && !!node.getAttribute && !!node.getAttribute("name");
  if (sel === "select[name]") return node.tagName === "SELECT" && !!node.getAttribute && !!node.getAttribute("name");
  if (sel === "textarea[name]") return node.tagName === "TEXTAREA" && !!node.getAttribute && !!node.getAttribute("name");
  if (sel === ".modal") return (node.className || "").split(/\s+/).includes("modal");
  if (sel === ".modal.show") return (node.className || "").split(/\s+/).includes("modal") && (node.className || "").split(/\s+/).includes("show");
  if (sel === ".modal-backdrop") return (node.className || "").split(/\s+/).includes("modal-backdrop");
  if (sel === "h1, h2") return ["H1", "H2"].includes(node.tagName);
  if (sel === "button, input, select, textarea, a[data-admin-async-link]") {
    return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(node.tagName);
  }
  if (sel === "button") return node.tagName === "BUTTON";
  if (sel === "a") return node.tagName === "A";
  return false;
}

class FakeClassList {
  constructor(node) {
    this.node = node;
    this.items = new Set();
  }
  add(...names) {
    names.forEach((name) => { if (name) this.items.add(String(name)); });
    this.sync();
  }
  remove(...names) {
    names.forEach((name) => this.items.delete(String(name)));
    this.sync();
  }
  toggle(name, force) {
    const key = String(name);
    const shouldAdd = typeof force === "boolean" ? force : !this.items.has(key);
    if (shouldAdd) this.items.add(key);
    else this.items.delete(key);
    this.sync();
    return shouldAdd;
  }
  contains(name) {
    return this.items.has(String(name));
  }
  sync() {
    this.node.className = Array.from(this.items).join(" ");
  }
}

class FakeNode {
  constructor(tagName = "div", attrs = {}) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.parentNode = null;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.className = "";
    this.classList = new FakeClassList(this);
    this.id = "";
    this.hidden = false;
    this.value = "";
    this._innerHTML = "";
    this._textContent = "";
    this.isConnected = true;
    this.style = {
      setProperty() {},
      removeProperty() {},
    };
    for (const [key, value] of Object.entries(attrs || {})) {
      this.setAttribute(key, value);
    }
  }
  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
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
  setAttribute(name, value) {
    const key = String(name);
    const val = String(value);
    this.attributes[key] = val;
    if (key === "id") this.id = val;
    if (key === "class") {
      this.className = val;
      this.classList.items = new Set(val.split(/\s+/).filter(Boolean));
    }
    if (key === "value") this.value = val;
    if (key.startsWith("data-")) this.dataset[dataKey(key)] = val;
  }
  getAttribute(name) {
    const key = String(name);
    return Object.prototype.hasOwnProperty.call(this.attributes, key) ? this.attributes[key] : null;
  }
  hasAttribute(name) {
    return this.getAttribute(name) !== null;
  }
  removeAttribute(name) {
    const key = String(name);
    delete this.attributes[key];
    if (key === "id") this.id = "";
    if (key === "class") {
      this.className = "";
      this.classList.items = new Set();
    }
    if (key.startsWith("data-")) delete this.dataset[dataKey(key)];
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
  contains(node) {
    if (!node) return false;
    if (node === this) return true;
    return this.children.some((child) => child.contains(node));
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
    if (ev.bubbles !== false && this.parentNode) {
      return this.parentNode.dispatchEvent(ev);
    }
    return !ev.defaultPrevented;
  }
  querySelectorAll(selector) {
    const out = [];
    const visit = (node) => {
      if (!node) return;
      if (matchesSelector(node, selector)) out.push(node);
      (node.children || []).forEach(visit);
    };
    visit(this);
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  focus() {}
  getBoundingClientRect() {
    return { top: 0, bottom: 120, left: 0, right: 0, width: 0, height: 120 };
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set textContent(value) {
    this._textContent = String(value);
  }
  get textContent() {
    return this._textContent;
  }
}

class FakeDocument extends FakeNode {
  constructor() {
    super("#document", {});
    this.documentElement = new FakeNode("html", {});
    this.body = new FakeNode("body", {});
    this.head = new FakeNode("head", {});
    this.documentElement.parentNode = this;
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.children = [this.documentElement];
    this.readyState = "complete";
    this.title = "Domesticas";
  }
  createElement(tag) {
    return new FakeNode(tag, {});
  }
  querySelectorAll(selector) {
    const out = [];
    [this.head, this.body, this.documentElement].forEach((root) => {
      root.querySelectorAll(selector).forEach((node) => {
        if (!out.includes(node)) out.push(node);
      });
    });
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  getElementById(id) {
    return this.querySelector("#" + id);
  }
}

function createAbortError() {
  if (typeof DOMException === "function") return new DOMException("The operation was aborted.", "AbortError");
  const err = new Error("The operation was aborted.");
  err.name = "AbortError";
  return err;
}

function createFetchMock() {
  const calls = [];
  let routeMap = new Map();
  const fetch = (url, options = {}) => {
    const call = { url, options, aborted: false };
    calls.push(call);
    const entry = routeMap.get(String(url));
    if (!entry) {
      return Promise.resolve({
        ok: true,
        url: String(url),
        text: async () => "<html><head><title>Fallback</title></head><body><div data-admin-nav-viewport='true'>fallback</div></body></html>",
      });
    }
    if (entry.type === "pending") {
      return new Promise((resolve, reject) => {
        call.resolve = resolve;
        call.reject = reject;
        if (options.signal) {
          options.signal.addEventListener("abort", () => {
            call.aborted = true;
            reject(createAbortError());
          }, { once: true });
        }
      });
    }
    if (entry.type === "abort-error") {
      return Promise.reject(createAbortError());
    }
    if (options.signal) {
      options.signal.addEventListener("abort", () => {
        call.aborted = true;
      }, { once: true });
    }
    return Promise.resolve({
      ok: true,
      url: entry.finalUrl || String(url),
      text: async () => entry.html,
    });
  };
  return {
    fetch,
    calls,
    setRoute(url, entry) {
      routeMap.set(String(url), entry);
    },
  };
}

function flushTimers(timers) {
  let index = 0;
  while (index < timers.length) {
    const timer = timers[index++];
    if (timer.cleared) continue;
    timer.cleared = true;
    timer.fn();
  }
}

async function waitTick() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function bootstrap() {
  const doc = new FakeDocument();
  const timers = [];
  const fetchMock = createFetchMock();
  const location = {
    href: "https://example.test/admin/candidatas",
    origin: "https://example.test",
    pathname: "/admin/candidatas",
    assign(url) {
      const next = new URL(url, this.href);
      this.href = next.toString();
      this.pathname = next.pathname;
    },
  };
  const historyStack = [{ url: location.href, state: null }];
  let historyIndex = 0;
  const listeners = {};
  const session = new Map();

  const win = {
    document: doc,
    window: null,
    self: null,
    globalThis: null,
    console,
    URL,
    fetch: fetchMock.fetch,
    AbortController,
    DOMException,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    location,
    scrollY: 0,
    pageYOffset: 0,
    requestAnimationFrame(fn) { return (typeof fn === "function") ? (fn(), 1) : 1; },
    cancelAnimationFrame() {},
    scrollTo(opts) {
      const top = Number((opts && opts.top) || 0);
      this.scrollY = top;
      this.pageYOffset = top;
    },
    getComputedStyle() { return { position: "static", height: "0px" }; },
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay, cleared: false });
      return id;
    },
    clearTimeout(id) {
      const item = timers.find((timer) => timer.id === id);
      if (item) item.cleared = true;
    },
    sessionStorage: {
      getItem(key) { return session.get(String(key)) || null; },
      setItem(key, value) { session.set(String(key), String(value)); },
      removeItem(key) { session.delete(String(key)); },
      key(index) { return Array.from(session.keys())[Number(index)] || null; },
      get length() { return session.size; },
    },
  };
  win.addEventListener = function (type, fn) {
    if (!listeners[type]) listeners[type] = [];
    listeners[type].push(fn);
  };
  win.dispatchEvent = function (ev) {
    if (!ev) return true;
    if (!ev.target) ev.target = this;
    ev.currentTarget = this;
    const handlers = listeners[ev.type] || [];
    handlers.slice().forEach((fn) => fn.call(this, ev));
    return !ev.defaultPrevented;
  };
  win.history = {
    state: null,
    pushState(state, _title, url) {
      historyStack.splice(historyIndex + 1);
      historyStack.push({ state, url });
      historyIndex = historyStack.length - 1;
      this.state = state;
      location.href = String(url);
      location.pathname = new URL(String(url), location.origin).pathname;
    },
    replaceState(state, _title, url) {
      historyStack[historyIndex] = { state, url };
      this.state = state;
      location.href = String(url);
      location.pathname = new URL(String(url), location.origin).pathname;
    },
    back() {
      if (historyIndex <= 0) return;
      historyIndex -= 1;
      const entry = historyStack[historyIndex];
      this.state = entry.state;
      location.href = String(entry.url);
      location.pathname = new URL(String(entry.url), location.origin).pathname;
      win.dispatchEvent(new FakeCustomEvent("popstate", { state: entry.state }));
    },
  };
  win.window = win;
  win.self = win;
  win.globalThis = win;
  doc.defaultView = win;
  doc.addEventListener = FakeNode.prototype.addEventListener;
  doc.dispatchEvent = FakeNode.prototype.dispatchEvent;

  class FakeDOMParser {
    parseFromString(html) {
      const nextDoc = new FakeDocument();
      const titleMatch = /<title>([\s\S]*?)<\/title>/i.exec(String(html || ""));
      nextDoc.title = titleMatch ? titleMatch[1] : "";
      const viewportMatch = /<div[^>]*data-admin-nav-viewport=["']true["'][^>]*>([\s\S]*?)<\/div>/i.exec(String(html || ""));
      const viewport = new FakeNode("div", { "data-admin-nav-viewport": "true" });
      viewport.innerHTML = viewportMatch ? viewportMatch[1] : "";
      nextDoc.body.appendChild(viewport);
      return nextDoc;
    }
  }

  const ctx = {
    window: win,
    document: doc,
    console,
    URL,
    AbortController,
    DOMException,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    fetch: fetchMock.fetch,
    DOMParser: FakeDOMParser,
    setTimeout: win.setTimeout.bind(win),
    clearTimeout: win.clearTimeout.bind(win),
    sessionStorage: win.sessionStorage,
    history: win.history,
  };
  ctx.globalThis = win;
  ctx.self = win;

  const viewport = new FakeNode("div", { "data-admin-nav-viewport": "true" });
  const qInput = new FakeNode("input", { name: "q", value: "Maria" });
  const detailLink = new FakeNode("a", {
    href: "/admin/candidatas/990580",
    "data-admin-nav": "true",
  });
  detailLink.textContent = "Abrir";
  viewport.appendChild(qInput);
  viewport.appendChild(detailLink);
  doc.body.appendChild(viewport);

  const backLink = new FakeNode("a", {
    href: "/admin/candidatas",
    "data-admin-nav": "true",
    "data-admin-nav-back": "true",
  });
  doc.body.appendChild(backLink);

  const title = new FakeNode("title", {});
  doc.head.appendChild(title);

  vm.runInNewContext(source, ctx, { filename: "admin_nav.js" });

  function dispatch(target, type, init = {}) {
    const ev = new FakeEvent(type, Object.assign({ target }, init));
    target.dispatchEvent(ev);
    return ev;
  }

  function setRoute(url, html) {
    fetchMock.setRoute(url, { html, finalUrl: url });
  }

  return {
    win,
    doc,
    viewport,
    qInput,
    detailLink,
    backLink,
    timers,
    fetchMock,
    dispatch,
    flushTimers: () => flushTimers(timers),
    setRoute,
  };
}

async function runScenario(name) {
  if (name === "hover_prefetch") {
    const env = bootstrap();
    env.setRoute("/admin/candidatas/990580", "<html><head><title>Ana Prefetch</title></head><body><div data-admin-nav-viewport='true'><main>Detalle 990580</main></div></body></html>");
    env.dispatch(env.detailLink, "pointerover", { relatedTarget: null });
    env.flushTimers();
    await Promise.resolve();
    return {
      fetches: env.fetchMock.calls.length,
      urls: env.fetchMock.calls.map((call) => call.url),
      cached: env.win.AdminNav ? true : false,
    };
  }

  if (name === "hover_cancel_before_delay") {
    const env = bootstrap();
    env.setRoute("/admin/candidatas/990580", "<html><head><title>Ana Prefetch</title></head><body><div data-admin-nav-viewport='true'><main>Detalle 990580</main></div></body></html>");
    env.dispatch(env.detailLink, "pointerover", { relatedTarget: null });
    env.dispatch(env.detailLink, "pointerout", { relatedTarget: env.doc.body });
    env.flushTimers();
    return {
      fetches: env.fetchMock.calls.length,
      timers: env.timers.filter((t) => !t.cleared).length,
    };
  }

  if (name === "click_uses_prefetch") {
    const env = bootstrap();
    env.setRoute("/admin/candidatas/990580", "<html><head><title>Ana Prefetch</title></head><body><div data-admin-nav-viewport='true'><main>Detalle 990580</main></div></body></html>");
    env.dispatch(env.detailLink, "pointerover", { relatedTarget: null });
    env.flushTimers();
    await waitTick();
    await env.win.AdminNav.navigateTo(env.detailLink.getAttribute("href"));
    await waitTick();
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      urls: env.fetchMock.calls.map((call) => call.url),
      title: env.doc.title,
      pathname: env.win.location.pathname,
    };
  }

  if (name === "prefetch_old_not_used") {
    const env = bootstrap();
    env.setRoute("/admin/candidatas/990580", "<html><head><title>Ana Prefetch</title></head><body><div data-admin-nav-viewport='true'><main>Detalle A</main></div></body></html>");
    env.setRoute("/admin/candidatas/990581", "<html><head><title>Ana B</title></head><body><div data-admin-nav-viewport='true'><main>Detalle B</main></div></body></html>");
    const linkB = new FakeNode("a", { href: "/admin/candidatas/990581", "data-admin-nav": "true" });
    env.doc.body.appendChild(linkB);
    env.dispatch(env.detailLink, "pointerover", { relatedTarget: null });
    env.flushTimers();
    await waitTick();
    env.dispatch(linkB, "pointerover", { relatedTarget: null });
    env.flushTimers();
    await waitTick();
    await env.win.AdminNav.navigateTo(linkB.getAttribute("href"));
    await waitTick();
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      urls: env.fetchMock.calls.map((call) => call.url),
      title: env.doc.title,
      pathname: env.win.location.pathname,
    };
  }

  if (name === "back_restores_snapshot") {
    const env = bootstrap();
    const listUrl = "https://example.test/admin/candidatas?q=Maria&page=2";
    env.win.scrollTo({ top: 420 });
    env.win.history.replaceState({
      __admin_pjax_pilot: true,
      url: listUrl,
      scrollY: 420,
      uiState: {
        values: { q: "Maria" },
        openCollapseIds: [],
        activeTabs: [],
      },
    }, "", listUrl);
    env.win.sessionStorage.setItem(
      "__admin_pjax_snapshot__::" + listUrl,
      JSON.stringify({
        html: env.viewport.innerHTML,
        title: "Domesticas",
        uiState: {
          values: { q: "Maria" },
          openCollapseIds: [],
          activeTabs: [],
        },
        ts: Date.now(),
      }),
    );
    env.setRoute("/admin/candidatas/990580", "<html><head><title>Ana Prefetch</title></head><body><div data-admin-nav-viewport='true'><main>Detalle 990580</main></div></body></html>");
    env.dispatch(env.detailLink, "pointerover", { relatedTarget: null });
    env.flushTimers();
    await waitTick();
    await env.win.AdminNav.navigateTo(env.detailLink.getAttribute("href"));
    await waitTick();
    await waitTick();
    env.win.history.back();
    await waitTick();
    await waitTick();
    return {
      fetches: env.fetchMock.calls.length,
      urls: env.fetchMock.calls.map((call) => call.url),
      html: env.viewport.innerHTML,
      title: env.doc.title,
      pathname: env.win.location.pathname,
      scrollY: env.win.scrollY,
      q: env.win.history.state && env.win.history.state.uiState && env.win.history.state.uiState.values
        ? env.win.history.state.uiState.values.q
        : null,
    };
  }

  if (name === "invalidate_snapshot_prefix") {
    const env = bootstrap();
    env.win.sessionStorage.setItem("__admin_pjax_snapshot__::https://example.test/admin/candidatas?q=Maria", JSON.stringify({ html: "list", ts: Date.now() }));
    env.win.AdminNav.invalidateSnapshots(["/admin/candidatas"]);
    return {
      remaining: env.win.sessionStorage.getItem("__admin_pjax_snapshot__::https://example.test/admin/candidatas?q=Maria"),
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
      script_path = Path(tmpdir) / "admin_nav_runtime.js"
      script_path.write_text(NODE_HARNESS, encoding="utf-8")
      env = dict(os.environ)
      env["ADMIN_NAV_SOURCE"] = str(JS_SOURCE)
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


def test_admin_nav_prefetch_by_intent():
    data = _run_node_case("hover_prefetch")
    assert data["fetches"] == 1
    assert "/admin/candidatas/990580" in data["urls"]


def test_admin_nav_prefetch_cancels_before_delay():
    data = _run_node_case("hover_cancel_before_delay")
    assert data["fetches"] == 0


def test_admin_nav_click_reuses_prefetch():
    data = _run_node_case("click_uses_prefetch")
    assert data["fetches"] == 1
    assert data["pathname"] == "/admin/candidatas/990580"
    assert data["title"] == "Ana Prefetch"


def test_admin_nav_prefetch_antiguo_no_se_usa_para_otro_link():
    data = _run_node_case("prefetch_old_not_used")
    assert data["fetches"] == 2
    assert data["urls"] == ["/admin/candidatas/990580", "/admin/candidatas/990581"]
    assert data["pathname"] == "/admin/candidatas/990581"
    assert data["title"] == "Ana B"


def test_admin_nav_back_restores_snapshot_and_state():
    data = _run_node_case("back_restores_snapshot")
    assert data["fetches"] == 1
    assert data["pathname"] == "/admin/candidatas"
    assert "Maria" == data["q"]
    assert data["scrollY"] == 420


def test_admin_nav_invalidates_snapshot_prefix():
    data = _run_node_case("invalidate_snapshot_prefix")
    assert data["remaining"] is None
