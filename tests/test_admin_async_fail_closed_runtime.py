# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_SOURCE = ROOT / "static" / "js" / "core" / "admin_async.js"


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.env.ADMIN_ASYNC_SOURCE, "utf8");

function dataKey(name) {
  return String(name || "").replace(/^data-/, "").replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

function matchesSelector(node, selector) {
  if (!node || !selector) return false;
  const sel = String(selector || "").trim();
  if (!sel) return false;
  if (sel.includes(",")) return sel.split(",").some((part) => matchesSelector(node, part));
  if (sel === "form[data-admin-async-form]") return node.tagName === "FORM" && node.getAttribute("data-admin-async-form") !== null;
  if (sel === "form") return node.tagName === "FORM";
  if (sel === "button") return node.tagName === "BUTTON";
  if (sel === "input") return node.tagName === "INPUT";
  if (sel === "textarea") return node.tagName === "TEXTAREA";
  if (sel === "select") return node.tagName === "SELECT";
  if (sel === "button[type='submit']" || sel === "input[type='submit']") return node.tagName === "BUTTON" || node.tagName === "INPUT";
  if (sel === "button[type='submit'],input[type='submit']") return node.tagName === "BUTTON" || node.tagName === "INPUT";
  if (sel === "button, input, select, textarea, a[data-admin-async-link]") return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(node.tagName);
  if (sel === "#explicit-target") return node.id === "explicit-target";
  if (sel === "#async-target") return node.id === "async-target";
  if (sel === ".alert") return (node.className || "").split(/\s+/).includes("alert");
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
    this.disabled = false;
    this.hidden = false;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.method = "";
    this.action = "";
    this.isConnected = true;
    this.style = { setProperty() {}, removeProperty() {} };
    this._formDataFields = {};
    Object.entries(attrs || {}).forEach(([key, value]) => this.setAttribute(key, value));
  }
  get parentElement() { return this.parentNode; }
  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
    child.isConnected = true;
    this.children.push(child);
    return child;
  }
  insertBefore(child, before) {
    if (!child) return child;
    child.parentNode = this;
    child.isConnected = true;
    const idx = this.children.indexOf(before);
    if (idx < 0) {
      this.children.push(child);
    } else {
      this.children.splice(idx, 0, child);
    }
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
    if (key === "disabled") this.disabled = true;
    if (key === "value") this.value = val;
    if (key === "action") this.action = val;
    if (key === "method") this.method = val.toUpperCase();
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
    if (key === "disabled") this.disabled = false;
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
  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }
  dispatchEvent(ev) {
    if (!ev) return true;
    if (!ev.target) ev.target = this;
    ev.currentTarget = this;
    const handlers = this.listeners[ev.type] || [];
    handlers.slice().forEach((fn) => fn.call(this, ev));
    if (ev.bubbles !== false && this.parentNode) return this.parentNode.dispatchEvent(ev);
    return !ev.defaultPrevented;
  }
  focus() {}
  submit() {}
}

class FakeDocument extends FakeNode {
  constructor() {
    super("#document", {});
    this.readyState = "complete";
    this.documentElement = new FakeNode("html", {});
    this.body = new FakeNode("body", {});
    this.head = new FakeNode("head", {});
    this.documentElement.parentNode = this;
    this.body.parentNode = this.documentElement;
    this.head.parentNode = this.documentElement;
    this.documentElement.children = [this.head, this.body];
    this.children = [this.documentElement];
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

const document = new FakeDocument();
const window = {
  document,
  location: { assign(url) { window.assigned = url; }, origin: "https://example.test", href: "https://example.test/admin/usuarios/1/editar" },
  console,
  navigator: {},
  listeners: {},
  addEventListener(type, fn) {
    if (!window.listeners[type]) window.listeners[type] = [];
    window.listeners[type].push(fn);
  },
  removeEventListener(type, fn) {
    const list = window.listeners[type] || [];
    window.listeners[type] = list.filter((item) => item !== fn);
  },
  dispatchEvent(ev) {
    if (!ev) return true;
    const handlers = window.listeners[ev.type] || [];
    handlers.slice().forEach((fn) => fn.call(window, ev));
    return !ev.defaultPrevented;
  },
  setTimeout(fn) { if (typeof fn === "function") fn(); return 1; },
  clearTimeout() {},
  requestIdleCallback(fn) { if (typeof fn === "function") fn(); return 1; },
  requestAnimationFrame(fn) { if (typeof fn === "function") fn(); return 1; },
  cancelAnimationFrame() {},
  scrollTo() {},
  confirm() { return true; },
  AppLoader: { hideAll() {}, hide() {}, show() {} },
  bootstrap: { Modal: { getInstance() { return null; }, getOrCreateInstance() { return { show() {}, hide() {}, dispose() {} }; } } },
};

global.window = window;
global.document = document;
global.console = console;
global.CustomEvent = class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; this.target = init.target || null; this.bubbles = init.bubbles !== undefined ? !!init.bubbles : true; this.defaultPrevented = false; } preventDefault() { this.defaultPrevented = true; } };
global.Event = global.CustomEvent;
global.HTMLElement = FakeNode;
global.HTMLFormElement = FakeNode;
global.HTMLButtonElement = FakeNode;
global.HTMLInputElement = FakeNode;
global.HTMLSelectElement = FakeNode;
global.HTMLTextAreaElement = FakeNode;
global.HTMLAnchorElement = FakeNode;
global.Node = FakeNode;
global.FormData = class FakeFormData {
  constructor(form) {
    this.items = new Map();
    if (form && form._formDataFields) {
      Object.entries(form._formDataFields).forEach(([key, value]) => this.items.set(String(key), String(value)));
    }
  }
  has(name) { return this.items.has(String(name)); }
  append(name, value) { this.items.set(String(name), String(value)); }
  set(name, value) { this.items.set(String(name), String(value)); }
  get(name) { return this.items.get(String(name)); }
};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.bootstrap = window.bootstrap;
global.requestIdleCallback = window.requestIdleCallback;
global.setTimeout = window.setTimeout;
global.clearTimeout = window.clearTimeout;
global.URL = URL;

const host = new FakeNode("div", { id: "host" });
document.body.appendChild(host);

const missingForm = new FakeNode("form", { id: "missing-form", "data-admin-async-form": "1" });
missingForm.setAttribute("method", "POST");
missingForm._formDataFields = { nombre: "Sin endpoint" };
missingForm.appendChild(new FakeNode("input", { name: "nombre", value: "Sin endpoint" }));
missingForm.appendChild(new FakeNode("button", { type: "submit", id: "missing-submit" }));

const explicitTarget = new FakeNode("div", { id: "explicit-target" });
const explicitForm = new FakeNode("form", {
  id: "explicit-form",
  "data-admin-async-form": "1",
  "data-async-target": "#explicit-target",
});
explicitForm.setAttribute("method", "POST");
explicitForm.setAttribute("action", "https://example.test/admin/usuarios/1/editar");
explicitForm._formDataFields = { email: "owner@example.com" };
explicitForm.appendChild(new FakeNode("input", { name: "email", value: "owner@example.com" }));
explicitForm.appendChild(new FakeNode("button", { type: "submit", id: "explicit-submit" }));

const asyncTarget = new FakeNode("div", { id: "async-target" });
const asyncActionForm = new FakeNode("form", {
  id: "async-action-form",
  "data-admin-async-form": "1",
  "data-async-target": "#async-target",
  "data-async-action": "https://example.test/admin/clientes/7/editar",
});
asyncActionForm.setAttribute("method", "POST");
asyncActionForm._formDataFields = { nombre_completo: "Cliente" };
asyncActionForm.appendChild(new FakeNode("input", { name: "nombre_completo", value: "Cliente" }));
asyncActionForm.appendChild(new FakeNode("button", { type: "submit", id: "async-submit" }));

host.appendChild(missingForm);
host.appendChild(explicitTarget);
host.appendChild(explicitForm);
host.appendChild(asyncTarget);
host.appendChild(asyncActionForm);

vm.runInThisContext(source, { filename: "admin_async.js" });
if (window.AdminAsync && typeof window.AdminAsync.init === "function") {
  window.AdminAsync.init();
}

const consoleErrors = [];
console.error = function () {
  consoleErrors.push(Array.from(arguments).map((item) => String(item)).join(" "));
};
window.console.error = console.error;

const fetchCalls = [];
window.fetch = async function (url, opts) {
  fetchCalls.push({
    url: String(url || ""),
    method: String(opts && opts.method || "").toUpperCase(),
    bodyHasGet: !!(opts && opts.body && typeof opts.body.get === "function"),
  });
  return {
    ok: true,
    status: 200,
    redirected: false,
    url: String(url || ""),
    headers: { get(name) { return String(name || "").toLowerCase() === "content-type" ? "application/json; charset=utf-8" : ""; } },
    text: async () => JSON.stringify({
      success: true,
      message: "Guardado.",
      category: "success",
      update_target: String((opts && opts.url) || url || ""),
      replace_html: "<div>OK</div>",
      errors: [],
    }),
  };
};
global.fetch = window.fetch;

window.AdminAsync.init();

function submitForm(form, submitter) {
  const ev = new Event("submit", { target: form, bubbles: true });
  if (submitter) ev.submitter = submitter;
  form.dispatchEvent(ev);
  return ev;
}

const missingSubmit = missingForm.querySelector("button");
const explicitSubmit = explicitForm.querySelector("button");
const asyncSubmit = asyncActionForm.querySelector("button");

const missingEvent = submitForm(missingForm, missingSubmit);
const missingAlert = host.querySelector(".alert");
const missingSnapshot = {
  prevented: missingEvent.defaultPrevented,
  alertText: missingAlert ? String(missingAlert.textContent || "") : "",
  submitDisabled: !!missingSubmit.disabled,
  inputValue: String(missingForm.querySelector("input").value || ""),
};

const explicitEvent = submitForm(explicitForm, explicitSubmit);
const asyncEvent = submitForm(asyncActionForm, asyncSubmit);

process.stdout.write(JSON.stringify({
  missing: missingSnapshot,
  explicitFetch: fetchCalls[0] || null,
  asyncActionFetch: fetchCalls[1] || null,
  explicitPrevented: explicitEvent.defaultPrevented,
  asyncPrevented: asyncEvent.defaultPrevented,
  consoleErrors,
  explicitAction: explicitForm.getAttribute("action"),
  asyncAction: asyncActionForm.getAttribute("data-async-action"),
  explicitTargetText: String(explicitTarget.textContent || ""),
  asyncTargetText: String(asyncTarget.textContent || ""),
}));
"""


def _run_node() -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(NODE_HARNESS)
        harness_path = fh.name
    try:
        env = os.environ.copy()
        env["ADMIN_ASYNC_SOURCE"] = str(JS_SOURCE)
        result = subprocess.run(
            ["node", harness_path],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    finally:
        try:
            os.unlink(harness_path)
        except OSError:
            pass


def test_admin_async_fail_closed_without_endpoint_and_supports_explicit_async_action():
    payload = _run_node()

    assert payload["missing"]["prevented"] is True
    assert payload["missing"]["submitDisabled"] is False
    assert payload["missing"]["inputValue"] == "Sin endpoint"
    assert "falta la ruta de guardado" in payload["missing"]["alertText"].lower()
    assert "missing async endpoint" in " ".join(payload["consoleErrors"]).lower()

    assert payload["explicitAction"] == "https://example.test/admin/usuarios/1/editar"
    assert payload["asyncAction"] == "https://example.test/admin/clientes/7/editar"
    assert payload["explicitFetch"] and payload["explicitFetch"]["url"] == "https://example.test/admin/usuarios/1/editar"
    assert payload["asyncActionFetch"] and payload["asyncActionFetch"]["url"] == "https://example.test/admin/clientes/7/editar"
    assert payload["explicitPrevented"] is True
    assert payload["asyncPrevented"] is True
