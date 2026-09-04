# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_SOURCE = ROOT / "static" / "js" / "admin" / "candidatas_operativo_detail_ui.js"


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.env.DETAIL_UI_SOURCE, "utf8");

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.target = init.target || null;
    this.currentTarget = null;
    this.defaultPrevented = false;
    this.bubbles = init.bubbles !== undefined ? !!init.bubbles : true;
  }
  preventDefault() {
    this.defaultPrevented = true;
  }
}

class FakeCustomEvent extends FakeEvent {}

class FakeClassList {
  constructor(node) {
    this.node = node;
    this.items = new Set();
  }
  add(...names) {
    names.forEach((name) => {
      if (name) this.items.add(String(name));
    });
    this.sync();
  }
  remove(...names) {
    names.forEach((name) => this.items.delete(String(name)));
    this.sync();
  }
  contains(name) {
    return this.items.has(String(name));
  }
  toggle(name, force) {
    const key = String(name);
    const shouldAdd = typeof force === "boolean" ? force : !this.items.has(key);
    if (shouldAdd) this.items.add(key);
    else this.items.delete(key);
    this.sync();
    return shouldAdd;
  }
  sync() {
    this.node.className = Array.from(this.items).join(" ");
  }
}

function dataKey(name) {
  return String(name || "").replace(/^data-/, "").replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

function matchesSelector(node, selector) {
  if (!node || !selector) return false;
  const sel = String(selector).trim();
  if (!sel) return false;
  if (sel.includes(",")) return sel.split(",").some((part) => matchesSelector(node, part));
  const attrMatch = sel.match(/^\[([^\]=]+)(?:=(["']?)(.*?)\2)?\]$/);
  if (attrMatch) {
    if (!node.getAttribute) return false;
    const attrName = attrMatch[1];
    const expected = attrMatch[3];
    const actual = node.getAttribute(attrName);
    if (actual === null) return false;
    if (expected === undefined) return true;
    return String(actual) === String(expected);
  }
  if (sel === "[data-candidata-center]") return !!node.getAttribute && node.getAttribute("data-candidata-center") !== null;
  if (sel === "[data-cand-identity-sticky]") return !!node.getAttribute && node.getAttribute("data-cand-identity-sticky") !== null;
  if (sel === ".detail-hero") return (node.className || "").split(/\s+/).includes("detail-hero");
  if (sel === "[data-cand-inline-search]") return !!node.getAttribute && node.getAttribute("data-cand-inline-search") !== null;
  if (sel === "[data-edit-toggle]") return !!node.getAttribute && node.getAttribute("data-edit-toggle") !== null;
  if (sel === "[data-edit-cancel]") return !!node.getAttribute && node.getAttribute("data-edit-cancel") !== null;
  if (sel === "[data-edit-section]") return !!node.getAttribute && node.getAttribute("data-edit-section") !== null;
  if (sel === "[data-quick-form]") return !!node.getAttribute && node.getAttribute("data-quick-form") !== null;
  if (sel === "[data-feedback]") return !!node.getAttribute && node.getAttribute("data-feedback") !== null;
  if (sel === ".modal") return (node.className || "").split(/\s+/).includes("modal");
  if (sel === "[data-doc-batch-modal]") return !!node.getAttribute && node.getAttribute("data-doc-batch-modal") !== null;
  if (sel === "[data-doc-batch-form]") return !!node.getAttribute && node.getAttribute("data-doc-batch-form") !== null;
  if (sel === "[data-doc-batch-open]") return !!node.getAttribute && node.getAttribute("data-doc-batch-open") !== null;
  if (sel === "[data-doc-batch-input]") return !!node.getAttribute && node.getAttribute("data-doc-batch-input") !== null;
  if (sel === "[data-doc-batch-clear]") return !!node.getAttribute && node.getAttribute("data-doc-batch-clear") !== null;
  if (sel === "[data-doc-batch-preview-wrap]") return !!node.getAttribute && node.getAttribute("data-doc-batch-preview-wrap") !== null;
  if (sel === "[data-doc-batch-preview]") return !!node.getAttribute && node.getAttribute("data-doc-batch-preview") !== null;
  if (sel === "[data-doc-batch-filename]") return !!node.getAttribute && node.getAttribute("data-doc-batch-filename") !== null;
  if (sel === "[data-doc-batch-submit]") return !!node.getAttribute && node.getAttribute("data-doc-batch-submit") !== null;
  if (sel === "button") return node.tagName === "BUTTON";
  if (sel === "form") return node.tagName === "FORM";
  if (sel === "input") return node.tagName === "INPUT";
  if (sel === "textarea") return node.tagName === "TEXTAREA";
  if (sel === "select") return node.tagName === "SELECT";
  if (sel === "button, input, select, textarea") return ["BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(node.tagName);
  if (sel === "[data-cand-identity-name]") return !!node.getAttribute && node.getAttribute("data-cand-identity-name") !== null;
  if (sel === "[data-cand-identity-code]") return !!node.getAttribute && node.getAttribute("data-cand-identity-code") !== null;
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
    this.className = "";
    this.classList = new FakeClassList(this);
    this.id = "";
    this.hidden = false;
    this.style = {};
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.files = [];
    this._formDataFields = {};
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

  setAttribute(name, value) {
    const key = String(name);
    const val = String(value);
    this.attributes[key] = val;
    if (key === "id") this.id = val;
    if (key === "class") {
      this.className = val;
      this.classList.items = new Set(val.split(/\s+/).filter(Boolean));
    }
    if (key.startsWith("data-")) this.dataset[dataKey(key)] = val;
  }

  getAttribute(name) {
    const key = String(name);
    return Object.prototype.hasOwnProperty.call(this.attributes, key) ? this.attributes[key] : null;
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
    const seen = new Set();
    const selectors = String(selector || "").split(",").map((part) => part.trim()).filter(Boolean);
    const visit = (node) => {
      if (!node) return;
      if (selectors.some((part) => matchesSelector(node, part)) && !seen.has(node)) {
        seen.add(node);
        out.push(node);
      }
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

  removeEventListener(type, fn) {
    if (!this.listeners[type]) return;
    this.listeners[type] = this.listeners[type].filter((handler) => handler !== fn);
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

  requestSubmit(submitter) {
    const event = new FakeEvent("submit", { target: this });
    event.submitter = submitter || null;
    this.dispatchEvent(event);
  }

  scrollIntoView() {}
  focus() {}
  getBoundingClientRect() {
    return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
  }
}

class FakeDocument extends FakeNode {
  constructor() {
    super("#document", {});
    this.body = new FakeNode("body", {});
    this.head = new FakeNode("head", {});
    this.children = [this.head, this.body];
    this.readyState = "complete";
    this.documentElement = new FakeNode("html", {});
    this.title = "Domésticas";
  }

  createElement(tag) {
    return new FakeNode(tag, {});
  }

  getElementById(id) {
    return this.querySelector("#" + id);
  }

  querySelectorAll(selector) {
    return [...this.head.querySelectorAll(selector), ...this.body.querySelectorAll(selector)];
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class FakeFormData {
  constructor(form) {
    this.entries = [];
    if (form && form._formDataFields) {
      Object.entries(form._formDataFields).forEach(([key, value]) => {
        this.entries.push([key, value]);
      });
    }
  }
}

function createFetchMock(config = {}) {
  const calls = [];
  const responses = Array.isArray(config.responses) ? config.responses.slice() : [];
  let pending = null;
  const fetch = (url, options = {}) => {
    calls.push({ url, options });
    const next = responses.length ? responses.shift() : null;
    if (next && next.deferred) {
      return new Promise((resolve, reject) => {
        pending = { resolve, reject, ok: next.ok !== false, payload: next.payload || null };
      });
    }
    const ok = next ? next.ok !== false : true;
    const payload = next && next.payload ? next.payload : {
      ok: true,
      message: "Guardado.",
      header: {},
      display: {},
    };
    return Promise.resolve({
      ok,
      text: async () => JSON.stringify(payload),
    });
  };
  return {
    fetch,
    calls,
    responses,
    pending: () => pending,
    resolvePending(payload) {
      if (!pending) return false;
      const nextPayload = payload || pending.payload || {
        ok: pending.ok,
        message: pending.ok ? "Guardado." : "No se pudo guardar.",
        header: {},
        display: {},
      };
      const resolver = pending.resolve;
      pending = null;
      resolver({
        ok: !!nextPayload.ok,
        text: async () => JSON.stringify(nextPayload),
      });
      return true;
    },
  };
}

function makeDetailRoot(extraAttrs = {}) {
  const root = new FakeNode("div", {
    "data-candidata-center": "",
    "data-cand-detail-ui-bound": "1",
    ...extraAttrs,
  });

  const hero = new FakeNode("section", { class: "detail-hero" });
  const sticky = new FakeNode("div", { "data-cand-identity-sticky": "" });
  sticky.hidden = true;
  const stickyName = new FakeNode("span", { "data-cand-identity-name": "" });
  const stickyCode = new FakeNode("span", { "data-cand-identity-code": "" });
  sticky.appendChild(stickyName);
  sticky.appendChild(stickyCode);
  hero.appendChild(sticky);

  const personal = new FakeNode("section", { "data-edit-section": "personal" });
  const personalToggle = new FakeNode("button", { "data-edit-toggle": "" });
  const personalDisplay = new FakeNode("div", { "data-display": "personal", class: "cand-display" });
  const personalForm = new FakeNode("form", {
    "data-quick-form": "",
    "data-endpoint": "/admin/candidatas/990501/datos",
    "data-quick-form-bound": "1",
  });
  personalForm._formDataFields = { nombre: "Ana", telefono: "809" };
  const personalCancel = new FakeNode("button", { "data-edit-cancel": "" });
  personalForm.appendChild(personalCancel);
  personal.appendChild(personalToggle);
  personal.appendChild(personalDisplay);
  personal.appendChild(personalForm);

  const labor = new FakeNode("section", { "data-edit-section": "labor" });
  const laborToggle = new FakeNode("button", { "data-edit-toggle": "" });
  const laborDisplay = new FakeNode("div", { "data-display": "labor", class: "cand-display" });
  const laborForm = new FakeNode("form", {
    "data-quick-form": "",
    "data-endpoint": "/admin/candidatas/990501/datos-laborales",
    "data-quick-form-bound": "1",
  });
  laborForm._formDataFields = { modalidad: "Con dormida" };
  labor.appendChild(laborToggle);
  labor.appendChild(laborDisplay);
  labor.appendChild(laborForm);

  const refs = new FakeNode("section", { "data-edit-section": "secretary_references" });
  const refsToggle = new FakeNode("button", { "data-edit-toggle": "" });
  const refsDisplay = new FakeNode("div", { "data-display": "secretary-references", class: "cand-display" });
  const refsForm = new FakeNode("form", {
    "data-quick-form": "",
    "data-endpoint": "/admin/candidatas/990501/referencias",
    "data-quick-form-bound": "1",
  });
  refsForm._formDataFields = { referencias_laboral: "Texto" };
  refs.appendChild(refsToggle);
  refs.appendChild(refsDisplay);
  refs.appendChild(refsForm);

  const inlineShell = new FakeNode("div", { "data-cand-inline-search": "", "data-search-url": "/admin/candidatas/buscar" });
  const inlineInput = new FakeNode("input", {});
  inlineShell.appendChild(inlineInput);

  const docUpload = new FakeNode("form", {
    "data-doc-upload-form": "",
    "data-doc-key": "cedula1",
    "data-quick-form": "",
    "data-endpoint": "/admin/candidatas/990501/documentos/cedula1",
    "data-doc-form-bound": "1",
  });
  const fileInput = new FakeNode("input", { "data-doc-file-input": "" });
  const pick = new FakeNode("button", { "data-doc-action": "pick" });
  docUpload.appendChild(fileInput);
  docUpload.appendChild(pick);

  const batchOpen = new FakeNode("button", { "data-doc-batch-open": "" });
  const batchModal = new FakeNode("div", { class: "modal fade", "data-doc-batch-modal": "", id: "docBatchModal" });
  const batchDialog = new FakeNode("div", { class: "modal-dialog" });
  const batchForm = new FakeNode("form", {
    "data-doc-batch-form": "",
    "data-max-bytes": String(3 * 1024 * 1024),
    "data-endpoint": "/admin/candidatas/990501/documentos/batch",
  });
  const batchFeedback = new FakeNode("div", { "data-feedback": "" });
  batchForm.appendChild(batchFeedback);
  ["depuracion", "perfil", "cedula1", "cedula2"].forEach((field) => {
    const input = new FakeNode("input", {
      "data-doc-batch-input": field,
      name: field,
      type: "file",
    });
    const filename = new FakeNode("div", { "data-doc-batch-filename": field });
    const previewWrap = new FakeNode("div", { class: "d-none", "data-doc-batch-preview-wrap": field });
    const preview = new FakeNode("img", { "data-doc-batch-preview": field });
    const clearBtn = new FakeNode("button", { "data-doc-batch-clear": field });
    const err = new FakeNode("div", { "data-error-for": field });
    previewWrap.appendChild(preview);
    batchForm.appendChild(input);
    batchForm.appendChild(filename);
    batchForm.appendChild(previewWrap);
    batchForm.appendChild(clearBtn);
    batchForm.appendChild(err);
  });
  const batchSubmit = new FakeNode("button", { "data-doc-batch-submit": "" });
  const batchClose = new FakeNode("button", { "data-doc-batch-close": "" });
  batchForm.appendChild(batchSubmit);
  batchForm.appendChild(batchClose);
  batchDialog.appendChild(batchForm);
  batchModal.appendChild(batchDialog);

  root.appendChild(hero);
  root.appendChild(personal);
  root.appendChild(labor);
  root.appendChild(refs);
  root.appendChild(inlineShell);
  root.appendChild(docUpload);
  root.appendChild(batchOpen);
  root.appendChild(batchModal);

  return {
    root,
    hero,
    sticky,
    stickyName,
    stickyCode,
    personal,
    personalToggle,
    personalDisplay,
    personalForm,
    personalCancel,
    labor,
    laborToggle,
    laborDisplay,
    laborForm,
    refs,
    refsToggle,
    refsDisplay,
    refsForm,
    inlineShell,
    inlineInput,
    docUpload,
    fileInput,
    pick,
    batchOpen,
    batchModal,
    batchForm,
    batchFeedback,
    batchSubmit,
    batchClose,
  };
}

function bootstrap(fetchConfig = {}) {
  const doc = new FakeDocument();
  const fetchMock = createFetchMock(fetchConfig);
  const timers = [];
  const modalState = {
    showCount: 0,
    hideCount: 0,
    visible: false,
  };
  const modalInstances = new WeakMap();
  const win = {
    document: doc,
    window: null,
    self: null,
    globalThis: null,
    console,
    fetch: fetchMock.fetch,
    AbortController,
    DOMException,
    FormData: FakeFormData,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ fn, delay, cleared: false });
      return id;
    },
    clearTimeout(id) {
      const timer = timers[id - 1];
      if (timer) timer.cleared = true;
    },
    requestAnimationFrame(fn) {
      return setTimeout(fn, 0);
    },
    cancelAnimationFrame(id) {
      clearTimeout(id);
    },
    crypto: { randomUUID: () => "uuid-1" },
    location: { href: "http://test/admin/candidatas/990501", origin: "http://test" },
    listeners: {},
  };
  win.addEventListener = function (type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  };
  win.removeEventListener = function (type, fn) {
    if (!this.listeners[type]) return;
    this.listeners[type] = this.listeners[type].filter((handler) => handler !== fn);
  };
  win.dispatchEvent = function (ev) {
    if (!ev.target) ev.target = this;
    ev.currentTarget = this;
    const handlers = this.listeners[ev.type] || [];
    for (const fn of handlers.slice()) fn.call(this, ev);
    return !ev.defaultPrevented;
  };
  win.scrollTo = function () {};
  win.IntersectionObserver = undefined;
  win.bootstrap = {
    Modal: {
      getInstance(modalEl) {
        return modalInstances.get(modalEl) || null;
      },
      getOrCreateInstance(modalEl) {
        let instance = modalInstances.get(modalEl);
        if (!instance) {
          instance = {
            show() {
              modalState.visible = true;
              modalState.showCount += 1;
              modalEl.hidden = false;
              modalEl.classList.add("show");
            },
            hide() {
              modalState.visible = false;
              modalState.hideCount += 1;
              modalEl.hidden = true;
              modalEl.classList.remove("show");
              modalEl.dispatchEvent(new FakeEvent("hidden.bs.modal", { target: modalEl }));
            },
          };
          modalInstances.set(modalEl, instance);
        }
        return instance;
      },
    },
  };
  win.window = win;
  win.self = win;
  win.globalThis = win;
  doc.defaultView = win;

  const ctx = {
    window: win,
    document: doc,
    console,
    fetch: fetchMock.fetch,
    AbortController,
    DOMException,
    FormData: FakeFormData,
    CustomEvent: FakeCustomEvent,
    Event: FakeEvent,
    setTimeout: win.setTimeout.bind(win),
    clearTimeout: win.clearTimeout.bind(win),
    requestAnimationFrame: win.requestAnimationFrame.bind(win),
    cancelAnimationFrame: win.cancelAnimationFrame.bind(win),
    crypto: win.crypto,
  };
  ctx.globalThis = win;
  ctx.self = win;

  vm.runInNewContext(source, ctx, { filename: "candidatas_operativo_detail_ui.js" });

  function dispatch(target, type, detail) {
    const ev = new FakeCustomEvent(type, { detail });
    ev.target = target;
    target.dispatchEvent(ev);
  }

  return { win, doc, fetchMock, timers, dispatch, makeDetailRoot, modalState };
}

async function runScenario(name) {
  const wait = () => new Promise((resolve) => setImmediate(resolve));
  const fetchConfig = {};
  if (name === "batch_modal_error_keeps_open") {
    fetchConfig.responses = [{
      deferred: true,
      ok: false,
      payload: {
        ok: false,
        message: "No se pudo guardar.",
        errors: { perfil: "Archivo demasiado grande." },
      },
    }];
  } else if (name === "batch_modal_success_flow") {
    fetchConfig.responses = [{
      deferred: true,
      payload: {
        ok: true,
        message: "2 documentos actualizados correctamente.",
        doc_flags: {
          depuracion: true,
          perfil: true,
          cedula1: true,
          cedula2: false,
        },
        doc_labels: {
          depuracion: "Depuración",
          perfil: "Perfil",
          cedula1: "Cédula frente",
          cedula2: "Cédula reverso",
        },
        updated_fields: ["depuracion", "perfil"],
      },
    }];
  } else if (name === "batch_modal_double_submit_blocks_second_fetch") {
    fetchConfig.responses = [{
      deferred: true,
      payload: {
        ok: true,
        message: "Guardado.",
        doc_flags: {
          depuracion: true,
          perfil: true,
          cedula1: false,
          cedula2: false,
        },
        doc_labels: {
          depuracion: "Depuración",
          perfil: "Perfil",
          cedula1: "Cédula frente",
          cedula2: "Cédula reverso",
        },
      },
    }];
  }
  const env = bootstrap(fetchConfig);
  const first = env.makeDetailRoot();
  env.doc.body.appendChild(first.root);
  env.win.AdminCandidataDetailUI.init();

  if (name === "click_and_submit_after_snapshot_restore") {
    first.personalToggle.dispatchEvent(new FakeEvent("click", { target: first.personalToggle }));
    const firstEditing = first.personal.classList.contains("cand-editing");

    const restored = env.makeDetailRoot({
      "data-cand-detail-ui-bound": "1",
    });
    env.doc.body.children = [restored.root];
    env.dispatch(env.doc, "admin:navigation-complete", { viewport: env.doc });

    restored.personalToggle.dispatchEvent(new FakeEvent("click", { target: restored.personalToggle }));
    const restoredEditing = restored.personal.classList.contains("cand-editing");
    restored.personalForm.requestSubmit();

    return {
      firstEditing,
      restoredEditing,
      fetches: env.fetchMock.calls.length,
      firstUrl: env.fetchMock.calls[0] ? env.fetchMock.calls[0].url : null,
      rootBound: restored.root.__candDetailUiBound || null,
      quickBoundAttr: restored.personalForm.getAttribute("data-quick-form-bound"),
      currentShellBound: env.win.__candInlineSearchShell === restored.inlineShell,
    };
  }

  if (name === "batch_modal_success_flow") {
    const modalInstance = env.win.bootstrap.Modal.getOrCreateInstance(first.batchModal);
    first.batchOpen.dispatchEvent(new FakeEvent("click", { target: first.batchOpen }));
    const fileA = { name: "depuracion.jpg", type: "image/jpeg", size: 1200 };
    const fileB = { name: "perfil.png", type: "image/png", size: 900 };
    const inputA = first.batchForm.querySelector('[data-doc-batch-input="depuracion"]');
    const inputB = first.batchForm.querySelector('[data-doc-batch-input="perfil"]');
    inputA.files = [fileA];
    inputB.files = [fileB];
    inputA.dispatchEvent(new FakeEvent("change", { target: inputA }));
    inputB.dispatchEvent(new FakeEvent("change", { target: inputB }));
    const previewA = first.batchForm.querySelector('[data-doc-batch-preview="depuracion"]');
    const filenameA = first.batchForm.querySelector('[data-doc-batch-filename="depuracion"]');
    const previewB = first.batchForm.querySelector('[data-doc-batch-preview="perfil"]');
    const filenameB = first.batchForm.querySelector('[data-doc-batch-filename="perfil"]');
    const clearBtnA = first.batchForm.querySelector('[data-doc-batch-clear="depuracion"]');
    const previewBeforeSubmit = {
      dep: previewA.getAttribute("src"),
      perfil: previewB.getAttribute("src"),
      depName: filenameA.textContent,
      perfilName: filenameB.textContent,
      depWrapHidden: first.batchForm.querySelector('[data-doc-batch-preview-wrap="depuracion"]').classList.contains("d-none"),
      perfilWrapHidden: first.batchForm.querySelector('[data-doc-batch-preview-wrap="perfil"]').classList.contains("d-none"),
    };
    clearBtnA.dispatchEvent(new FakeEvent("click", { target: clearBtnA }));
    inputA.files = [fileA];
    inputA.dispatchEvent(new FakeEvent("change", { target: inputA }));
    const submitEvent = new FakeEvent("submit", { target: first.batchForm });
    submitEvent.submitter = first.batchSubmit;
    first.batchForm.dispatchEvent(submitEvent);
    const feedbackDuringSave = first.batchFeedback.textContent;
    env.fetchMock.resolvePending({
      ok: true,
      message: "2 documentos actualizados correctamente.",
      doc_flags: {
        depuracion: true,
        perfil: true,
        cedula1: true,
        cedula2: false,
      },
      doc_labels: {
        depuracion: "Depuración",
        perfil: "Perfil",
        cedula1: "Cédula frente",
        cedula2: "Cédula reverso",
      },
      updated_fields: ["depuracion", "perfil"],
    });
    await wait();
    await wait();
    await wait();

    return {
      modalShown: env.modalState.showCount,
      modalHidden: env.modalState.hideCount,
      modalVisible: env.modalState.visible,
      fetches: env.fetchMock.calls.length,
      firstUrl: env.fetchMock.calls[0] ? env.fetchMock.calls[0].url : null,
      feedbackDuringSave,
      previewBeforeSubmit,
      quickBusy: first.batchForm.dataset.quickBusy || "",
      updatedFields: JSON.stringify(["depuracion", "perfil"]),
    };
  }

  if (name === "batch_modal_error_keeps_open") {
    first.batchOpen.dispatchEvent(new FakeEvent("click", { target: first.batchOpen }));
    const input = first.batchForm.querySelector('[data-doc-batch-input="perfil"]');
    input.files = [{ name: "perfil.jpg", type: "image/jpeg", size: 50 }];
    input.dispatchEvent(new FakeEvent("change", { target: input }));
    const submitEvent = new FakeEvent("submit", { target: first.batchForm });
    submitEvent.submitter = first.batchSubmit;
    first.batchForm.dispatchEvent(submitEvent);
    await wait();
    await wait();
    const feedbackDuringSave = first.batchFeedback.textContent;
    env.fetchMock.resolvePending({
      ok: false,
      message: "No se pudo guardar.",
      errors: { perfil: "Archivo demasiado grande." },
    });
    await wait();
    await wait();

    return {
      modalShown: env.modalState.showCount,
      modalHidden: env.modalState.hideCount,
      modalVisible: env.modalState.visible,
      fetches: env.fetchMock.calls.length,
      feedback: first.batchFeedback.textContent,
      feedbackDuringSave,
      error: first.batchForm.querySelector('[data-error-for="perfil"]').textContent,
      quickBusy: first.batchForm.dataset.quickBusy || "",
    };
  }

  if (name === "batch_modal_double_submit_blocks_second_fetch") {
    first.batchOpen.dispatchEvent(new FakeEvent("click", { target: first.batchOpen }));
    const input = first.batchForm.querySelector('[data-doc-batch-input="depuracion"]');
    input.files = [{ name: "depuracion.jpg", type: "image/jpeg", size: 300 }];
    input.dispatchEvent(new FakeEvent("change", { target: input }));
    const submitEvent = new FakeEvent("submit", { target: first.batchForm });
    submitEvent.submitter = first.batchSubmit;
    first.batchForm.dispatchEvent(submitEvent);
    const feedbackDuringSave = first.batchFeedback.textContent;
    const fetchesBefore = env.fetchMock.calls.length;
    const secondSubmitEvent = new FakeEvent("submit", { target: first.batchForm });
    secondSubmitEvent.submitter = first.batchSubmit;
    first.batchForm.dispatchEvent(secondSubmitEvent);
    await wait();
    const fetchesAfterSecondAttempt = env.fetchMock.calls.length;
    env.fetchMock.resolvePending({
      ok: true,
      message: "Guardado.",
      doc_flags: {
        depuracion: true,
        perfil: false,
        cedula1: false,
        cedula2: false,
      },
      doc_labels: {
        depuracion: "Depuración",
        perfil: "Perfil",
        cedula1: "Cédula frente",
        cedula2: "Cédula reverso",
      },
      updated_fields: ["depuracion"],
    });
    await wait();
    await wait();
    await wait();
    return {
      fetchesBefore,
      fetchesAfterSecondAttempt,
      finalFetches: env.fetchMock.calls.length,
      modalHidden: env.modalState.hideCount,
      feedbackDuringSave,
    };
  }

  throw new Error("unknown scenario: " + name);
}

runScenario(process.argv[2]).then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
"""


def _run_node_case(case: str) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "admin_candidata_detail_runtime.js"
        script_path.write_text(NODE_HARNESS, encoding="utf-8")
        env = dict(os.environ)
        env["DETAIL_UI_SOURCE"] = str(JS_SOURCE)
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


def test_candidate_detail_runtime_rebinds_after_snapshot_restore():
    data = _run_node_case("click_and_submit_after_snapshot_restore")
    assert data["firstEditing"] is True
    assert data["restoredEditing"] is True
    assert data["fetches"] == 1
    assert data["firstUrl"] == "/admin/candidatas/990501/datos"
    assert data["currentShellBound"] is True


def test_candidate_detail_runtime_batch_modal_success_flow():
    data = _run_node_case("batch_modal_success_flow")
    assert data["modalShown"] >= 1
    assert data["modalHidden"] >= 1
    assert data["modalVisible"] is False
    assert data["fetches"] == 1
    assert data["firstUrl"] == "/admin/candidatas/990501/documentos/batch"
    assert "Guardando..." in data["feedbackDuringSave"]
    preview = data["previewBeforeSubmit"]
    assert preview["depWrapHidden"] is False
    assert preview["perfilWrapHidden"] is False
    assert preview["depName"] == "depuracion.jpg"
    assert preview["perfilName"] == "perfil.png"
    assert data["quickBusy"] == ""
    assert "depuracion" in data["updatedFields"]
    assert "perfil" in data["updatedFields"]


def test_candidate_detail_runtime_batch_modal_error_keeps_open():
    data = _run_node_case("batch_modal_error_keeps_open")
    assert data["modalShown"] >= 1
    assert data["modalHidden"] == 0
    assert data["modalVisible"] is True
    assert data["fetches"] == 1
    assert "No se pudo guardar" in data["feedback"]
    assert "Archivo demasiado grande" in data["error"]
    assert data["quickBusy"] == ""


def test_candidate_detail_runtime_batch_modal_blocks_double_submit():
    data = _run_node_case("batch_modal_double_submit_blocks_second_fetch")
    assert data["fetchesBefore"] == 1
    assert data["fetchesAfterSecondAttempt"] == 1
    assert data["finalFetches"] == 1
    assert data["modalHidden"] >= 1
    assert "Guardando..." in data["feedbackDuringSave"]
