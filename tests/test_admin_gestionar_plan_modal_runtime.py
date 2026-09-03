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

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.target = init.target || null;
    this.currentTarget = null;
    this.bubbles = init.bubbles !== undefined ? !!init.bubbles : true;
    this.defaultPrevented = false;
    this.button = init.button || 0;
    this.metaKey = !!init.metaKey;
    this.ctrlKey = !!init.ctrlKey;
    this.shiftKey = !!init.shiftKey;
    this.altKey = !!init.altKey;
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
  const sel = String(selector).trim();
  if (!sel) return false;
  if (sel.includes(",")) return sel.split(",").some((part) => matchesSelector(node, part));
  if (sel === "form[data-admin-async-form]") return node.tagName === "FORM" && node.getAttribute && node.getAttribute("data-admin-async-form") !== null;
  if (sel === "[data-gestionar-plan-modal-trigger]") return !!node.getAttribute && node.getAttribute("data-gestionar-plan-modal-trigger") !== null;
  if (sel === "[data-gestionar-plan-modal-retry='1']") return !!node.getAttribute && node.getAttribute("data-gestionar-plan-modal-retry") === "1";
  if (sel === "#gestionarPlanModal") return node.id === "gestionarPlanModal";
  if (sel === "#gestionarPlanAsyncRegion") return node.id === "gestionarPlanAsyncRegion";
  if (sel === "#planForm") return node.id === "planForm";
  if (sel === "#tipo_plan") return node.id === "tipo_plan";
  if (sel === "#plan-summary-total") return node.id === "plan-summary-total";
  if (sel === "#plan-summary-deposit") return node.id === "plan-summary-deposit";
  if (sel === "#plan-summary-balance") return node.id === "plan-summary-balance";
  if (sel === "#abono_auto") return node.id === "abono_auto";
  if (sel === ".modal") return (node.className || "").split(/\s+/).includes("modal");
  if (sel === ".modal.show") return (node.className || "").split(/\s+/).includes("modal") && (node.className || "").split(/\s+/).includes("show");
  if (sel === ".modal-body") return (node.className || "").split(/\s+/).includes("modal-body");
  if (sel === ".modal-backdrop") return (node.className || "").split(/\s+/).includes("modal-backdrop");
  if (sel === "form") return node.tagName === "FORM";
  if (sel === "select") return node.tagName === "SELECT";
  if (sel === "option") return node.tagName === "OPTION";
  if (sel === "a") return node.tagName === "A";
  if (sel === "button") return node.tagName === "BUTTON";
  if (sel === "button[type='submit']" || sel === "input[type='submit']") return node.tagName === "BUTTON" || node.tagName === "INPUT";
  if (sel === "button[type='submit'],input[type='submit']") return node.tagName === "BUTTON" || node.tagName === "INPUT";
  if (sel === "button, input, select, textarea, a[data-admin-async-link]") {
    return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(node.tagName);
  }
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
    this.hidden = false;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.action = "";
    this.method = "";
    this._formDataFields = {};
    this.style = { setProperty() {}, removeProperty() {} };
    this.options = [];
    this.selectedIndex = 0;
    this.isConnected = true;
    for (const [key, value] of Object.entries(attrs || {})) {
      this.setAttribute(key, value);
    }
  }
  submit() {}
  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
    child.isConnected = true;
    this.children.push(child);
    if (this.tagName === "SELECT" && child.tagName === "OPTION") {
      this.options.push(child);
    }
    return child;
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
      if (this.parentNode.tagName === "SELECT" && this.tagName === "OPTION") {
        this.parentNode.options = this.parentNode.options.filter((child) => child !== this);
      }
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
    if (key === "name") this.name = val;
    if (key === "action") this.action = val;
    if (key === "method") this.method = val.toUpperCase();
    if (key === "selected") this.selected = true;
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
    for (const fn of handlers.slice()) {
      fn.call(this, ev);
    }
    if (ev.bubbles !== false && this.parentNode) {
      return this.parentNode.dispatchEvent(ev);
    }
    return !ev.defaultPrevented;
  }
  focus() {}
  getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; }
}

class FakeDocument extends FakeNode {
  constructor() {
    super("#document", {});
    this.readyState = "loading";
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
  location: { assign(url) { window.assigned = url; }, origin: "https://example.test", href: "https://example.test/admin/clientes/2671" },
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
    for (const fn of handlers.slice()) {
      fn.call(window, ev);
    }
    return !ev.defaultPrevented;
  },
  setTimeout(fn) { if (typeof fn === "function") fn(); return 1; },
  clearTimeout() {},
  requestIdleCallback(fn) { if (typeof fn === "function") fn(); return 1; },
  scrollCalls: 0,
  scrollTo() { window.scrollCalls += 1; },
  confirm() { return true; },
  bootstrap: {
    Modal: (function () {
      const instances = new WeakMap();
      class ModalStub {
        constructor(modal) {
          this.modal = modal;
          instances.set(modal, this);
        }
        show() {
          this.modal.classList.add("show");
          this.modal.setAttribute("aria-hidden", "false");
        }
        hide() {
          this.modal.classList.remove("show");
          this.modal.setAttribute("aria-hidden", "true");
        }
        dispose() {
          instances.delete(this.modal);
        }
        static getOrCreateInstance(modal) {
          return instances.get(modal) || new ModalStub(modal);
        }
        static getInstance(modal) {
          return instances.get(modal) || null;
        }
      }
      return ModalStub;
    })(),
  },
};

global.window = window;
global.document = document;
global.console = console;
global.CustomEvent = FakeCustomEvent;
global.Event = FakeEvent;
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
    this.form = form || null;
    this.items = new Map();
    if (form && form._formDataFields) {
      Object.entries(form._formDataFields).forEach(([key, value]) => {
        this.items.set(String(key), String(value));
      });
    }
  }
  has(name) {
    return this.items.has(String(name));
  }
  append(name, value) {
    this.items.set(String(name), String(value));
  }
  set(name, value) {
    this.items.set(String(name), String(value));
  }
  get(name) {
    return this.items.get(String(name));
  }
};
global.AbortController = class {
  constructor() { this.signal = {}; }
  abort() {}
};
global.bootstrap = window.bootstrap;
global.requestIdleCallback = window.requestIdleCallback;
global.setTimeout = window.setTimeout;
global.clearTimeout = window.clearTimeout;
global.URL = URL;

const anchor = new FakeNode("a", {
  href: "https://example.test/admin/clientes/7/solicitudes/101/plan?next=/admin/clientes/7",
  "data-no-loader": "true",
  "data-gestionar-plan-modal-trigger": "1",
  "data-gestionar-plan-modal": "#gestionarPlanModal",
});
document.body.appendChild(anchor);

const modal = new FakeNode("div", {
  id: "gestionarPlanModal",
  class: "modal fade",
  "data-admin-body-modal": "true",
});
const modalBody = new FakeNode("div", { class: "modal-body" });
const region = new FakeNode("div", {
  id: "gestionarPlanAsyncRegion",
  "data-async-preserve-scroll": "true",
});
modalBody.appendChild(region);
modal.appendChild(modalBody);
document.body.appendChild(modal);

const form = new FakeNode("form", {
  id: "planForm",
  "data-admin-async-form": "1",
  "data-async-target": "#gestionarPlanAsyncRegion",
  "data-async-busy-container": "#gestionarPlanAsyncScope",
  "data-async-preserve-scroll": "true",
  "data-async-fallback": "native",
});
form.setAttribute("method", "POST");
form.setAttribute("action", "https://example.test/admin/clientes/7/solicitudes/101/plan?next=%2Fadmin%2Fclientes%2F7");
const select = new FakeNode("select", { id: "tipo_plan" });
const optBasic = new FakeNode("option", { value: "basico" });
optBasic.dataset.price = "3500";
const optPremium = new FakeNode("option", { value: "premium" });
optPremium.dataset.price = "5000";
select.appendChild(optBasic);
select.appendChild(optPremium);
select.selectedIndex = 0;
const total = new FakeNode("strong", { id: "plan-summary-total" });
const deposit = new FakeNode("strong", { id: "plan-summary-deposit" });
const balance = new FakeNode("strong", { id: "plan-summary-balance" });
const abono = new FakeNode("input", { id: "abono_auto" });
const createBtn = new FakeNode("button", { type: "submit", name: "plan_action", value: "create_new_cycle", id: "btn-create-cycle" });
const submitBtn = new FakeNode("button", { type: "submit", name: "plan_action", value: "update", id: "btn-submit" });
form.appendChild(select);
form.appendChild(total);
form.appendChild(deposit);
form.appendChild(balance);
form.appendChild(abono);
form.appendChild(createBtn);
form.appendChild(submitBtn);
region.appendChild(form);

vm.runInThisContext(source, { filename: "admin_async.js" });

const captured = [];
window.AdminAsync.request = async function (opts) {
  captured.push({
    url: opts.url,
    updateTarget: opts.updateTarget,
    sourceIsTrigger: opts.sourceEl === anchor,
    busyIsModalBody: opts.busyContainer === modalBody,
    noLoader: opts.noLoader === true,
    loadingMarkup: String(region.innerHTML || ""),
  });
  return true;
};

const submitRequests = [];
window.fetch = async function (url, opts) {
  submitRequests.push({
    url,
    method: opts && opts.method,
    bodyIsFormData: !!(opts && opts.body && typeof opts.body.get === "function" && typeof opts.body.append === "function"),
    planAction: opts && opts.body && typeof opts.body.get === "function" ? opts.body.get("plan_action") : null,
  });
  return {
    ok: true,
    status: 200,
    redirected: false,
    url: String(url),
    headers: { get() { return "application/json"; } },
    text: async () => JSON.stringify({
      success: true,
      message: "ok",
      category: "success",
      update_target: "#gestionarPlanAsyncRegion",
      replace_html: "<div id='gestionarPlanAsyncRegion'></div>",
    }),
  };
};
global.fetch = window.fetch;

window.AdminAsync.init();

const clickEvent = new FakeEvent("click", { target: anchor, bubbles: true });
anchor.dispatchEvent(clickEvent);

const initSummary = {
  total: total.textContent,
  deposit: deposit.textContent,
  balance: balance.textContent,
  ready: form.dataset.planSummaryReady || "",
};

select.selectedIndex = 1;
select.dispatchEvent(new FakeEvent("change", { target: select, bubbles: true }));

const updatedSummary = {
  total: total.textContent,
  deposit: deposit.textContent,
  balance: balance.textContent,
  abono: abono.value,
};

const retryBtn = new FakeNode("button", { "data-gestionar-plan-modal-retry": "1" });
modalBody.appendChild(retryBtn);
modal.dataset.gestionarPlanUrl = anchor.getAttribute("href");

window.AdminAsync.request = async function (opts) {
  captured.push({
    retryUrl: opts.url,
    retryUpdateTarget: opts.updateTarget,
  });
  return true;
};

retryBtn.dispatchEvent(new FakeEvent("click", { target: retryBtn, bubbles: true }));

const submitterMode = String(process.env.PLAN_SUBMITTER_MODE || "explicit");
if (submitterMode === "click-update") {
  submitBtn.dispatchEvent(new FakeEvent("click", { target: submitBtn, bubbles: true }));
}
const submitEvent = new FakeEvent("submit", { target: form, bubbles: true });
if (submitterMode === "explicit") {
  submitEvent.submitter = submitBtn;
}
form.dispatchEvent(submitEvent);

process.stdout.write(JSON.stringify({
  modalShown: modal.classList.contains("show"),
  request: captured[0] || null,
  initSummary,
  updatedSummary,
  retryRequest: captured[1] || null,
  scrollCalls: window.scrollCalls,
  formAction: form.action,
  formMethod: form.method,
  submitRequest: submitRequests[0] || null,
  submitDefaultPrevented: submitEvent.defaultPrevented,
  submitButtonInsideForm: submitBtn.closest("form") === form,
}));
"""


def _run_node_case() -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(NODE_HARNESS)
        harness_path = fh.name
    try:
        env = os.environ.copy()
        env["ADMIN_ASYNC_SOURCE"] = str(JS_SOURCE)
        raw = subprocess.check_output(["node", harness_path], env=env, text=True)
        return json.loads(raw)
    finally:
        try:
            Path(harness_path).unlink()
        except Exception:
            pass


def test_plan_modal_runtime_opens_and_binds_summary():
    data = _run_node_case()
    assert data["modalShown"] is True
    assert data["request"]["updateTarget"] == "#gestionarPlanAsyncRegion"
    assert data["request"]["sourceIsTrigger"] is True
    assert data["request"]["busyIsModalBody"] is True
    assert data["request"]["noLoader"] is True
    assert "Cargando formulario de plan" in data["request"]["loadingMarkup"]
    assert data["scrollCalls"] == 0
    assert data["formAction"] == "https://example.test/admin/clientes/7/solicitudes/101/plan?next=%2Fadmin%2Fclientes%2F7"
    assert data["formMethod"] == "POST"
    assert data["submitRequest"]["url"] == "https://example.test/admin/clientes/7/solicitudes/101/plan?next=%2Fadmin%2Fclientes%2F7"
    assert data["submitRequest"]["method"] == "POST"
    assert data["submitRequest"]["bodyIsFormData"] is True
    assert data["submitRequest"]["planAction"] == "update"
    assert data["submitDefaultPrevented"] is True
    assert data["submitButtonInsideForm"] is True
    assert data["initSummary"]["ready"] == "1"
    assert data["initSummary"]["total"] == "RD$ 3,500.00"
    assert data["updatedSummary"]["total"] == "RD$ 5,000.00"
    assert data["updatedSummary"]["deposit"] == "RD$ 2,500.00"
    assert data["updatedSummary"]["abono"] == "2500.00"
    assert data["retryRequest"]["retryUpdateTarget"] == "#gestionarPlanAsyncRegion"


def test_plan_modal_runtime_preserves_clicked_submitter_without_submitter_property():
    env = os.environ.copy()
    env["PLAN_SUBMITTER_MODE"] = "click-update"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(NODE_HARNESS)
        harness_path = fh.name
    try:
        env["ADMIN_ASYNC_SOURCE"] = str(JS_SOURCE)
        raw = subprocess.check_output(["node", harness_path], env=env, text=True)
        data = json.loads(raw)
    finally:
        try:
            Path(harness_path).unlink()
        except Exception:
            pass

    assert data["submitRequest"]["planAction"] == "update"
