# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
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
  if (sel === "[data-registrar-pago-modal-trigger]") return node.getAttribute("data-registrar-pago-modal-trigger") !== null;
  if (sel === "[data-registrar-pago-modal-retry='1']") return node.getAttribute("data-registrar-pago-modal-retry") === "1";
  if (sel === "#registrarPagoModal") return node.id === "registrarPagoModal";
  if (sel === "#registrarPagoAsyncRegion") return node.id === "registrarPagoAsyncRegion";
  if (sel === "#registrarPagoAsyncScope") return node.id === "registrarPagoAsyncScope";
  if (sel === "#clienteSummaryAsyncRegion") return node.id === "clienteSummaryAsyncRegion";
  if (sel === "#clienteSolicitudesAsyncRegion") return node.id === "clienteSolicitudesAsyncRegion";
  if (sel === "#solicitudSummaryAsyncRegion") return node.id === "solicitudSummaryAsyncRegion";
  if (sel === "#solicitudOperativaCoreAsyncRegion") return node.id === "solicitudOperativaCoreAsyncRegion";
  if (sel === ".modal") return (node.className || "").split(/\s+/).includes("modal");
  if (sel === ".modal.show") return (node.className || "").split(/\s+/).includes("modal") && (node.className || "").split(/\s+/).includes("show");
  if (sel === ".modal-body") return (node.className || "").split(/\s+/).includes("modal-body");
  if (sel === ".modal-backdrop") return (node.className || "").split(/\s+/).includes("modal-backdrop");
  if (sel === "form") return node.tagName === "FORM";
  if (sel === "button") return node.tagName === "BUTTON";
  if (sel === "button[type='submit'],input[type='submit']") return node.tagName === "BUTTON" || node.tagName === "INPUT";
  if (sel === "button, input, select, textarea, a[data-admin-async-link]") return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(node.tagName);
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
    this.options = [];
    this.selectedIndex = 0;
    this.style = { setProperty() {}, removeProperty() {} };
    this._formDataFields = {};
    Object.entries(attrs || {}).forEach(([key, value]) => this.setAttribute(key, value));
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
    if (key === "class") {
      this.className = "";
      this.classList.items = new Set();
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
    handlers.slice().forEach((fn) => {
      const result = fn.call(this, ev);
      if (result && typeof result.then === "function") {
        if (!globalThis.__pendingAsyncOps) globalThis.__pendingAsyncOps = [];
        globalThis.__pendingAsyncOps.push(result);
      }
    });
    if (ev.bubbles !== false && this.parentNode) return this.parentNode.dispatchEvent(ev);
    return !ev.defaultPrevented;
  }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
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
const scenario = String(process.env.PAGO_SCENARIO || "success");
const window = {
  document,
  location: { assign(url) { window.assigned = url; }, origin: "https://example.test", href: "https://example.test/admin/clientes/7" },
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
  scrollCalls: 0,
  scrollTo() { window.scrollCalls += 1; },
  confirm() { return true; },
  requestAnimationFrame(fn) { if (typeof fn === "function") fn(); return 1; },
  cancelAnimationFrame() {},
  AppLoader: { show() { window.loaderShown += 1; }, hideAll() {}, hide() {} },
  loaderShown: 0,
  bootstrap: {
    Modal: (function () {
      const instances = new WeakMap();
      class ModalStub {
        constructor(modal) { this.modal = modal; instances.set(modal, this); }
        show() { this.modal.classList.add("show"); this.modal.setAttribute("aria-hidden", "false"); }
        hide() { this.modal.classList.remove("show"); this.modal.setAttribute("aria-hidden", "true"); }
        dispose() { instances.delete(this.modal); }
        static getOrCreateInstance(modal) { return instances.get(modal) || new ModalStub(modal); }
        static getInstance(modal) { return instances.get(modal) || null; }
      }
      return ModalStub;
    })(),
  },
};

global.window = window;
global.document = document;
global.console = console;
global.CustomEvent = class FakeCustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; this.target = init.target || null; this.bubbles = init.bubbles !== undefined ? !!init.bubbles : true; this.defaultPrevented = false; } preventDefault() { this.defaultPrevented = true; } };
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
    this.form = form || null;
    this.items = new Map();
    if (form && form._formDataFields) {
      Object.entries(form._formDataFields).forEach(([key, value]) => this.items.set(String(key), String(value)));
    }
  }
  has(name) { return this.items.has(String(name)); }
  append(name, value) { this.items.set(String(name), String(value)); }
  get(name) { return this.items.get(String(name)); }
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

const trigger = new FakeNode("a", {
  href: "https://example.test/admin/clientes/7/solicitudes/10/pago?next=/admin/clientes/7&modal=1",
  "data-no-loader": "true",
  "data-registrar-pago-modal-trigger": "1",
  "data-registrar-pago-modal": "#registrarPagoModal",
});
document.body.appendChild(trigger);

const modal = new FakeNode("div", {
  id: "registrarPagoModal",
  class: "modal fade",
  "data-admin-body-modal": "true",
});
const modalBody = new FakeNode("div", { class: "modal-body" });
const region = new FakeNode("div", { id: "registrarPagoAsyncRegion", "data-async-preserve-scroll": "true" });
const form = new FakeNode("form", {
  id: "pago-form",
  "data-admin-async-form": "1",
  "data-no-loader": "true",
  "data-async-target": "#registrarPagoAsyncRegion",
  "data-async-busy-container": "#registrarPagoAsyncScope",
  "data-async-preserve-scroll": "true",
  "data-async-fallback": "native",
});
form.setAttribute("method", "POST");
form.setAttribute("action", "https://example.test/admin/clientes/7/solicitudes/10/pago?next=/admin/clientes/7&modal=1");
form._formDataFields = { candidata_id: "1", payment_mode: "auto_saldo", row_version: "0", idempotency_key: "pay-1" };
const submitBtn = new FakeNode("button", { type: "submit", id: "pago-submit", "data-loading-text": "Registrando..." });
form.appendChild(submitBtn);
region.appendChild(form);
modalBody.appendChild(region);
modal.appendChild(modalBody);
document.body.appendChild(modal);
document.body.appendChild(new FakeNode("div", { id: "clienteSummaryAsyncRegion" }));
document.body.appendChild(new FakeNode("div", { id: "clienteSolicitudesAsyncRegion" }));
document.body.appendChild(new FakeNode("div", { id: "solicitudSummaryAsyncRegion" }));
document.body.appendChild(new FakeNode("div", { id: "solicitudOperativaCoreAsyncRegion" }));

vm.runInThisContext(source, { filename: "admin_async.js" });

const openCalls = [];
const modalRefreshCalls = [];
const modalCache = new Map();
let modalFreshVersion = 0;

function renderPaymentPartial(version, urlValue) {
  const nextUrl = String(urlValue || form.action || "");
  region.children = [];
  const shell = new FakeNode("div", { class: "card shadow-sm" });
  const body = new FakeNode("div", { class: "card-body p-3" });
  const formNode = new FakeNode("form", {
    id: "pago-form",
    "data-admin-async-form": "1",
    "data-no-loader": "true",
    "data-async-target": "#registrarPagoAsyncRegion",
    "data-async-busy-container": "#registrarPagoAsyncScope",
    "data-async-preserve-scroll": "true",
    "data-async-fallback": "native",
  });
  formNode.setAttribute("method", "POST");
  formNode.setAttribute("action", `${nextUrl}&version=${version}`);
  formNode._formDataFields = {
    candidata_id: String(version),
    payment_mode: "auto_saldo",
    row_version: String(version),
    idempotency_key: `pay-${version}`,
  };
  const select = new FakeNode("select", { id: "candidata_id" });
  const option = new FakeNode("option", { value: String(version) });
  option.textContent = `Candidata ${version}`;
  select.appendChild(option);
  const submit = new FakeNode("button", { type: "submit", id: "pago-submit", "data-loading-text": "Registrando..." });
  const cancel = new FakeNode("button", { type: "button", id: "pago-cancel" });
  formNode.appendChild(select);
  formNode.appendChild(submit);
  formNode.appendChild(cancel);
  body.appendChild(formNode);
  shell.appendChild(body);
  region.appendChild(shell);
  region.innerHTML = `version:${version}`;
  region.textContent = `version:${version}`;
  return formNode;
}

window.AdminAsync.request = async function (opts) {
  openCalls.push({
    url: opts.url,
    updateTarget: opts.updateTarget,
    busyContainer: opts.busyContainer === modalBody,
    noLoader: opts.noLoader === true,
    allowCached: opts.allowCached === true,
    modalShown: modal.classList.contains("show"),
    loadingMarkup: String(region.innerHTML || ""),
  });
  if (scenario === "cache") {
    const cacheKey = `${opts.method || "GET"}|${opts.url || ""}|${opts.updateTarget || ""}`;
    if (opts.allowCached && modalCache.has(cacheKey)) {
      const cachedVersion = modalCache.get(cacheKey);
      renderPaymentPartial(cachedVersion, opts.url);
      return true;
    }
    modalFreshVersion += 1;
    modalRefreshCalls.push({ url: String(opts.url || ""), allowCached: opts.allowCached === true, version: modalFreshVersion });
    await window.fetch(opts.url, {
      method: opts.method || "GET",
      body: opts.body || null,
      credentials: "same-origin",
      headers: opts.headers || {},
    });
    modalCache.set(cacheKey, modalFreshVersion);
    renderPaymentPartial(modalFreshVersion, opts.url);
    return true;
  }
  return true;
};

const fetchCalls = [];
window.fetch = async function (url, opts) {
  const method = String(opts && opts.method || "GET").toUpperCase();
  const body = opts && opts.body;
  if (method === "GET") {
    const path = String(url || "").replace(/^https?:\/\/[^/]+/, "");
    if (path === "/admin/clientes/7") {
      fetchCalls.push({
        url: String(url || ""),
        method,
        cache: String(opts && opts.cache || ""),
        bodyIsFormData: !!(body && typeof body.get === "function"),
        bodyModal: body && typeof body.get === "function" ? body.get("modal") : "",
        submitterDisabled: !!submitBtn.disabled,
      });
      return {
        ok: true,
        status: 200,
        redirected: false,
        url: String(url),
        headers: { get(name) { return String(name || "").toLowerCase() === "content-type" ? "application/json; charset=utf-8" : ""; } },
        text: async () => JSON.stringify({
          success: true,
          update_targets: [
            { target: "#clienteSummaryAsyncRegion", replace_html: "<div id='clienteSummaryAsyncRegion'>Resumen OK</div>" },
            { target: "#clienteSolicitudesAsyncRegion", replace_html: "<div id='clienteSolicitudesAsyncRegion'>Solicitudes OK</div>" },
          ],
        }),
      };
    }
    if (path === "/admin/clientes/7/solicitudes/10") {
      fetchCalls.push({
        url: String(url || ""),
        method,
        cache: String(opts && opts.cache || ""),
        bodyIsFormData: !!(body && typeof body.get === "function"),
        bodyModal: body && typeof body.get === "function" ? body.get("modal") : "",
        submitterDisabled: !!submitBtn.disabled,
      });
      return {
        ok: true,
        status: 200,
        redirected: false,
        url: String(url),
        headers: { get(name) { return String(name || "").toLowerCase() === "content-type" ? "application/json; charset=utf-8" : ""; } },
        text: async () => JSON.stringify({
          success: true,
          update_targets: [
            { target: "#solicitudSummaryAsyncRegion", replace_html: "<div id='solicitudSummaryAsyncRegion'>Resumen solicitud</div>" },
            { target: "#solicitudOperativaCoreAsyncRegion", replace_html: "<div id='solicitudOperativaCoreAsyncRegion'>Operativa OK</div>" },
          ],
        }),
      };
    }
  }
  fetchCalls.push({
    url: String(url || ""),
    method,
    cache: String(opts && opts.cache || ""),
    bodyIsFormData: !!(body && typeof body.get === "function"),
    bodyModal: body && typeof body.get === "function" ? body.get("modal") : "",
    submitterDisabled: !!submitBtn.disabled,
  });
  const isError = scenario === "error";
  return {
    ok: !isError,
    status: isError ? 409 : 200,
    redirected: false,
    url: String(url),
    headers: { get() { return "application/json"; } },
    text: async () => JSON.stringify(isError ? {
      success: false,
      message: "Pago con conflicto.",
      category: "warning",
      error_code: "conflict",
      update_target: "#registrarPagoAsyncRegion",
      replace_html: "<div id='registrarPagoAsyncRegion'></div>",
    } : {
      success: true,
      message: "Pago registrado correctamente.",
      category: "success",
      update_target: "#registrarPagoAsyncRegion",
      replace_html: "<div id='registrarPagoAsyncRegion'><form id='pago-form'></form></div>",
      update_targets: [
        { target: "#clienteSummaryAsyncRegion", redirect_url: "/admin/clientes/7", invalidate: true },
        { target: "#clienteSolicitudesAsyncRegion", redirect_url: "/admin/clientes/7", invalidate: true },
      ],
      invalidate_snapshots: ["/admin/clientes/7"],
    }),
  };
};
global.fetch = window.fetch;

window.AdminAsync.init();

const clickEv = { type: "click", target: trigger, bubbles: true, button: 0, preventDefault() { this.defaultPrevented = true; }, defaultPrevented: false };
trigger.dispatchEvent(clickEv);
if (scenario === "cache") {
  window.bootstrap.Modal.getOrCreateInstance(modal).hide();
  trigger.dispatchEvent(clickEv);
}

const submitEv = { type: "submit", target: form, bubbles: true, preventDefault() { this.defaultPrevented = true; }, defaultPrevented: false };
submitEv.submitter = submitBtn;
form.dispatchEvent(submitEv);

Promise.all(globalThis.__pendingAsyncOps || []).then(() => {
  const modalForm = region.querySelector("form");
  process.stdout.write(JSON.stringify({
    modalShown: modal.classList.contains("show"),
    openCall: openCalls[0] || null,
    openCalls,
    firstFetch: fetchCalls[0] || null,
    secondaryFetch: fetchCalls[1] || null,
    modalRefreshCalls,
    modalVersion: region.textContent,
    modalAction: modalForm ? modalForm.getAttribute("action") : null,
    modalActionProp: modalForm ? modalForm.action : null,
    modalRowVersion: modalForm && modalForm._formDataFields ? modalForm._formDataFields.row_version : null,
    loaderShown: window.loaderShown,
    scrollCalls: window.scrollCalls,
    submitPrevented: submitEv.defaultPrevented,
    submitButtonDisabledAfterRequest: submitBtn.disabled,
  }));
});
"""


def _run_node_harness(*, scenario="success"):
    env = os.environ.copy()
    env["ADMIN_ASYNC_SOURCE"] = str(JS_SOURCE)
    env["PAGO_SCENARIO"] = scenario
    proc = subprocess.run(
        ["node", "-e", NODE_HARNESS],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_registrar_pago_modal_runtime_abre_sin_loader_global_y_sin_scroll():
    result = _run_node_harness(scenario="success")
    assert result["modalShown"] is False
    assert result["openCall"]["updateTarget"] == "#registrarPagoAsyncRegion"
    assert result["openCall"]["busyContainer"] is True
    assert result["openCall"]["noLoader"] is True
    assert result["openCall"]["modalShown"] is True
    assert "Cargando formulario de pago" in result["openCall"]["loadingMarkup"]
    assert result["loaderShown"] == 0
    assert result["scrollCalls"] == 0
    assert result["firstFetch"]["method"] == "POST"
    assert "modal=1" in result["firstFetch"]["url"]
    assert result["secondaryFetch"]["method"] == "GET"
    assert result["secondaryFetch"]["url"].endswith("/admin/clientes/7")
    assert result["secondaryFetch"]["cache"] == "no-store"
    assert result["submitPrevented"] is True
    assert result["submitButtonDisabledAfterRequest"] is False


def test_registrar_pago_modal_runtime_error_no_cierra_modal_y_rehabilita_submit():
    result = _run_node_harness(scenario="error")
    assert result["modalShown"] is True
    assert result["firstFetch"]["method"] == "POST"
    assert "modal=1" in result["firstFetch"]["url"]
    assert result["secondaryFetch"] is None
    assert result["submitPrevented"] is True
    assert result["submitButtonDisabledAfterRequest"] is False


def test_registrar_pago_modal_runtime_reabre_sin_cache_y_recarga_formulario_fresco():
    result = _run_node_harness(scenario="cache")
    assert len(result["openCalls"]) == 2
    assert result["openCalls"][0]["allowCached"] is False
    assert result["openCalls"][1]["allowCached"] is False
    assert len(result["modalRefreshCalls"]) == 2
    assert result["modalRefreshCalls"][0]["version"] == 1
    assert result["modalRefreshCalls"][1]["version"] == 2
    assert result["modalVersion"] == "version:2"
    assert "version=2" in result["modalAction"]
    assert "version=2" in result["modalActionProp"]
    assert result["modalRowVersion"] == "2"
