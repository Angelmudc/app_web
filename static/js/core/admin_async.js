// static/js/core/admin_async.js
// Infraestructura común para interacciones async en admin.

(function () {
  "use strict";

  if (window.AdminAsync) return;

  const DEFAULT_BUTTON_TEXT = "Procesando...";
  const DEFAULT_LINK_TEXT = "Cargando...";
  const BUSY_KEY = "adminAsyncBusy";
  const REEMPLAZO_MODAL_ATTR = 'data-reemplazo-modal';
  const reemplazoModalAncestorState = new WeakMap();
  const reemplazoModalTeleportState = new WeakMap();
  const rowHighlightTimers = new WeakMap();
  let globalRequestSeq = 0;
  const latestRequestByTarget = new Map();
  let lastResponseMeta = null;

  function wantsJsonHeaders(extra) {
    const headers = {
      "Accept": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-Admin-Async": "1",
      ...extra,
    };
    return headers;
  }

  function escapeCssToken(value) {
    const raw = String(value || "");
    try {
      if (window.CSS && typeof window.CSS.escape === "function") {
        return window.CSS.escape(raw);
      }
    } catch (_) {}
    return raw.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function clearGlobalLoaders() {
    try {
      if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
        window.AppLoader.hideAll();
      } else if (window.AppLoader && typeof window.AppLoader.hide === "function") {
        window.AppLoader.hide();
      }
    } catch (_) {}
    // Fallback defensivo: algunas pantallas usan loader global sin AppLoader expuesto.
    try {
      ["globalLoader", "appGlobalLoader", "loader", "pageLoader", "loadingOverlay", "overlayLoader"].forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.style.display = "none";
      });
      document.documentElement.classList.remove("is-loading");
      if (document.body) document.body.classList.remove("is-loading");
    } catch (_) {}
  }

  function showToast(message, type) {
    const text = String(message || "").trim();
    if (!text) return;
    if (window.AppToast && typeof window.AppToast.show === "function") {
      window.AppToast.show(text, type || "primary");
      return;
    }
    try {
      console.warn("[AdminAsync]", text);
    } catch (_) {}
  }

  function normalizeType(category, ok) {
    const c = String(category || "").toLowerCase();
    if (c === "danger" || c === "error") return "danger";
    if (c === "warning" || c === "warn") return "warning";
    if (c === "info") return "info";
    if (c === "success") return "success";
    return ok ? "success" : "danger";
  }

  function getCSRFToken(form) {
    if (form && form.querySelector) {
      const input = form.querySelector('input[name="csrf_token"]');
      if (input && input.value) return input.value;
    }
    const hidden = document.querySelector('input[name="csrf_token"]');
    if (hidden && hidden.value) return hidden.value;
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? (meta.getAttribute("content") || "") : "";
  }

  function parseJsonSafe(text) {
    try {
      return JSON.parse(text);
    } catch (_) {
      return null;
    }
  }

  function statusMessage(status) {
    if (status === 400) return "Solicitud inválida. Revisa los datos e inténtalo de nuevo.";
    if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
    if (status === 403) return "No tienes permisos para esta acción o la sesión expiró.";
    if (status === 404) return "No encontramos el recurso solicitado.";
    if (status === 409) return "La acción no se pudo aplicar por estado actual. Refresca y reintenta.";
    if (status === 429) return "Demasiadas solicitudes seguidas. Espera un momento.";
    if (status >= 500) return "Ocurrió un error interno. Intenta nuevamente.";
    return "No se pudo completar la acción.";
  }

  function setBusyState(container, submitter, isBusy) {
    if (!container) return;

    if (isBusy) {
      container.dataset[BUSY_KEY] = "1";
      container.setAttribute("aria-busy", "true");
    } else {
      delete container.dataset[BUSY_KEY];
      container.removeAttribute("aria-busy");
    }

    const buttons = container.querySelectorAll('button, input[type="submit"], a[data-admin-async-link]');
    buttons.forEach((btn) => {
      if (isBusy) {
        btn.dataset._adminAsyncPrevDisabled = btn.disabled ? "1" : "0";
        btn.disabled = true;
        btn.classList.add("is-loading");
      } else {
        const prev = btn.dataset._adminAsyncPrevDisabled;
        if (prev === "0") btn.disabled = false;
        btn.classList.remove("is-loading");
        delete btn.dataset._adminAsyncPrevDisabled;
      }
    });

    if (submitter && (submitter.tagName === "BUTTON" || submitter.tagName === "A")) {
      if (isBusy) {
        submitter.dataset._adminAsyncPrevHtml = submitter.innerHTML;
        const fallbackText = submitter.tagName === "A" ? DEFAULT_LINK_TEXT : DEFAULT_BUTTON_TEXT;
        const txt = submitter.dataset.loadingText || fallbackText;
        submitter.innerHTML = `<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>${txt}`;
      } else if (submitter.dataset._adminAsyncPrevHtml) {
        submitter.innerHTML = submitter.dataset._adminAsyncPrevHtml;
        delete submitter.dataset._adminAsyncPrevHtml;
      }
    }
  }

  function resolvePreserveScroll(targetSelector, explicit) {
    if (typeof explicit === "boolean") return explicit;
    if (!targetSelector) return false;
    const target = document.querySelector(targetSelector);
    if (!target) return false;
    return target.getAttribute("data-async-preserve-scroll") === "true";
  }

  function disposeModalInstances(root) {
    if (!root || !root.querySelectorAll || !(window.bootstrap && window.bootstrap.Modal)) return;
    const modals = root.querySelectorAll(".modal");
    modals.forEach((modalEl) => {
      try {
        const instance = window.bootstrap.Modal.getInstance(modalEl);
        if (instance && typeof instance.dispose === "function") {
          instance.dispose();
        }
      } catch (_) {}
    });
  }

  function cleanupModalState(force) {
    try {
      const hasVisibleModal = !!document.querySelector(".modal.show");
      if (!force && hasVisibleModal) return;
      document.querySelectorAll(".modal-backdrop").forEach((n) => n.remove());
      if (document.documentElement) {
        document.documentElement.classList.remove("modal-open");
        document.documentElement.style.removeProperty("overflow");
        document.documentElement.style.removeProperty("padding-right");
      }
      if (document.body) {
        document.body.classList.remove("modal-open");
        document.body.style.removeProperty("overflow");
        document.body.style.removeProperty("padding-right");
      }
    } catch (_) {}
  }

  function isReemplazoModal(el) {
    return !!(el && el.matches && el.matches(`.modal[${REEMPLAZO_MODAL_ATTR}="1"]`));
  }

  function normalizeModalBackdrops() {
    try {
      const backdrops = Array.from(document.querySelectorAll(".modal-backdrop"));
      if (backdrops.length <= 1) return;
      backdrops.slice(0, -1).forEach((node) => node.remove());
    } catch (_) {}
  }

  function enforceReemplazoModalLayering(modalEl) {
    if (!modalEl) return;
    try {
      modalEl.style.setProperty("z-index", "1080");
      modalEl.style.setProperty("pointer-events", "auto");
      const dialog = modalEl.querySelector(".modal-dialog");
      const content = modalEl.querySelector(".modal-content");
      if (dialog) dialog.style.setProperty("pointer-events", "auto");
      if (content) content.style.setProperty("pointer-events", "auto");

      const backdrops = Array.from(document.querySelectorAll(".modal-backdrop"));
      backdrops.forEach((node) => {
        node.style.setProperty("z-index", "1070");
        node.style.setProperty("pointer-events", "auto");
      });
    } catch (_) {}
  }

  function teleportReemplazoModalToBody(modalEl) {
    if (!modalEl || !modalEl.parentNode) return;
    if (modalEl.parentNode === document.body && reemplazoModalTeleportState.has(modalEl)) return;

    const placeholder = document.createComment(`reemplazo-modal-anchor:${modalEl.id || "no-id"}`);
    const parent = modalEl.parentNode;
    parent.insertBefore(placeholder, modalEl);

    reemplazoModalTeleportState.set(modalEl, { placeholder });
    document.body.appendChild(modalEl);
  }

  function restoreTeleportedReemplazoModal(modalEl) {
    const state = reemplazoModalTeleportState.get(modalEl);
    if (!state || !state.placeholder) return;

    const anchor = state.placeholder;
    const parent = anchor.parentNode;
    if (parent) {
      parent.insertBefore(modalEl, anchor);
      anchor.remove();
    } else {
      // Si la región async fue reemplazada, evita duplicados huérfanos con mismo id.
      try { modalEl.remove(); } catch (_) {}
    }
    reemplazoModalTeleportState.delete(modalEl);
  }

  function neutralizeModalAncestors(modalEl) {
    if (!modalEl || !modalEl.parentElement) return;
    const changes = [];
    let parent = modalEl.parentElement;
    while (parent && parent !== document.body && parent !== document.documentElement) {
      const style = window.getComputedStyle(parent);
      const hasTransform = style && style.transform && style.transform !== "none";
      const hasPerspective = style && style.perspective && style.perspective !== "none";
      const hasFilter = style && style.filter && style.filter !== "none";
      const clipsOverflow = style && (style.overflow === "hidden" || style.overflowX === "hidden" || style.overflowY === "hidden");
      if (hasTransform || hasPerspective || hasFilter || clipsOverflow) {
        changes.push({
          el: parent,
          transform: parent.style.transform,
          perspective: parent.style.perspective,
          filter: parent.style.filter,
          overflow: parent.style.overflow,
          overflowX: parent.style.overflowX,
          overflowY: parent.style.overflowY,
        });
        if (hasTransform) parent.style.setProperty("transform", "none", "important");
        if (hasPerspective) parent.style.setProperty("perspective", "none", "important");
        if (hasFilter) parent.style.setProperty("filter", "none", "important");
        if (clipsOverflow) {
          parent.style.setProperty("overflow", "visible", "important");
          parent.style.setProperty("overflow-x", "visible", "important");
          parent.style.setProperty("overflow-y", "visible", "important");
        }
      }
      parent = parent.parentElement;
    }
    if (changes.length) {
      reemplazoModalAncestorState.set(modalEl, changes);
    }
  }

  function restoreModalAncestors(modalEl) {
    const changes = reemplazoModalAncestorState.get(modalEl);
    if (!Array.isArray(changes) || !changes.length) return;
    changes.forEach((item) => {
      if (!item || !item.el) return;
      item.el.style.transform = item.transform || "";
      item.el.style.perspective = item.perspective || "";
      item.el.style.filter = item.filter || "";
      item.el.style.overflow = item.overflow || "";
      item.el.style.overflowX = item.overflowX || "";
      item.el.style.overflowY = item.overflowY || "";
    });
    reemplazoModalAncestorState.delete(modalEl);
  }

  function bindReemplazoModalGuards() {
    document.addEventListener("show.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isReemplazoModal(modalEl)) return;
      teleportReemplazoModalToBody(modalEl);
      neutralizeModalAncestors(modalEl);
      normalizeModalBackdrops();
      enforceReemplazoModalLayering(modalEl);
      cleanupModalState(false);
    });

    document.addEventListener("shown.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isReemplazoModal(modalEl)) return;
      normalizeModalBackdrops();
      enforceReemplazoModalLayering(modalEl);
    });

    document.addEventListener("hidden.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isReemplazoModal(modalEl)) return;
      restoreModalAncestors(modalEl);
      restoreTeleportedReemplazoModal(modalEl);
      normalizeModalBackdrops();
      cleanupModalState(false);
    });
  }

  function replaceTargetHtml(targetSelector, html, options) {
    if (!targetSelector || typeof html !== "string") return false;
    const target = document.querySelector(targetSelector);
    if (!target) return false;
    if (target.innerHTML === html) return true;

    const preserveScroll = resolvePreserveScroll(targetSelector, options && options.preserveScroll);
    const rememberCollapse = (
      (options && options.preserveOpenCollapses === true)
      || target.getAttribute("data-async-remember-collapse") === "true"
    );
    const openCollapseIds = rememberCollapse
      ? Array.from(target.querySelectorAll(".collapse.show[id]")).map((el) => String(el.id || "").trim()).filter(Boolean)
      : [];
    const beforeRect = target.getBoundingClientRect();
    const beforeScrollY = window.scrollY || window.pageYOffset || 0;
    const beforeHeight = Math.max(0, target.offsetHeight || 0);
    const targetHasModals = !!target.querySelector(".modal");
    disposeModalInstances(target);
    cleanupModalState(targetHasModals);
    if (beforeHeight > 0) {
      target.style.minHeight = `${beforeHeight}px`;
    }
    target.style.opacity = "0.72";
    target.style.transition = "opacity 120ms ease";
    target.innerHTML = html;
    window.requestAnimationFrame(() => {
      if (openCollapseIds.length) {
        restoreOpenCollapses(target, openCollapseIds);
      }
      if (options && options.focusRowId) {
        highlightSolicitudRow(target, options.focusRowId, options.flashRow !== false);
      }
      syncCollapseToggleLabels(target);
      target.style.opacity = "1";
      target.style.minHeight = "";
      if (preserveScroll) {
        const afterRect = target.getBoundingClientRect();
        const delta = afterRect.top - beforeRect.top;
        if (Math.abs(delta) > 1) {
          window.scrollTo({ top: beforeScrollY + delta, behavior: "auto" });
        }
      }
    });
    document.dispatchEvent(new CustomEvent("admin:content-updated", {
      detail: { targetSelector, container: target },
    }));
    cleanupModalState(false);
    return true;
  }

  function restoreOpenCollapses(target, openCollapseIds) {
    if (!target || !Array.isArray(openCollapseIds) || !openCollapseIds.length) return;
    openCollapseIds.forEach((id) => {
      const selector = `#${escapeCssToken(id)}`;
      const panel = target.querySelector(selector);
      if (!panel || !panel.classList || !panel.classList.contains("collapse")) return;
      try {
        if (window.bootstrap && window.bootstrap.Collapse) {
          window.bootstrap.Collapse.getOrCreateInstance(panel, { toggle: false }).show();
        } else {
          panel.classList.add("show");
        }
      } catch (_) {
        panel.classList.add("show");
      }
    });
  }

  function highlightSolicitudRow(target, rowId, flashRow) {
    const id = Number(rowId || 0);
    if (!target || !Number.isFinite(id) || id <= 0) return;
    const row = target.querySelector(`#sol-${id}`);
    if (!row) return;

    try {
      row.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    } catch (_) {}

    if (!flashRow) return;
    row.classList.add("admin-async-row-updated");
    const prevTimer = rowHighlightTimers.get(row);
    if (prevTimer) {
      window.clearTimeout(prevTimer);
    }
    const timer = window.setTimeout(() => {
      row.classList.remove("admin-async-row-updated");
      rowHighlightTimers.delete(row);
    }, 1500);
    rowHighlightTimers.set(row, timer);
  }

  function collapseTargetFromToggle(toggle) {
    if (!toggle || !toggle.getAttribute) return null;
    const rawTarget = String(toggle.getAttribute("data-bs-target") || "").trim();
    if (rawTarget && rawTarget.startsWith("#")) {
      return document.querySelector(rawTarget);
    }
    const href = String(toggle.getAttribute("href") || "").trim();
    if (href && href.startsWith("#")) {
      return document.querySelector(href);
    }
    return null;
  }

  function updateCollapseToggleLabel(toggle, expanded) {
    if (!toggle) return;
    const openLabel = (toggle.getAttribute("data-collapse-open-label") || "").trim();
    const closedLabel = (toggle.getAttribute("data-collapse-closed-label") || "").trim();
    if (!openLabel || !closedLabel) return;
    toggle.textContent = expanded ? openLabel : closedLabel;
  }

  function syncCollapseToggleLabels(root) {
    const host = root && root.querySelectorAll ? root : document;
    const toggles = host.querySelectorAll("[data-bs-toggle='collapse'][data-collapse-open-label][data-collapse-closed-label]");
    toggles.forEach((toggle) => {
      const target = collapseTargetFromToggle(toggle);
      const isOpen = !!(target && target.classList && target.classList.contains("show"));
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      updateCollapseToggleLabel(toggle, isOpen);
    });
  }

  function normalizeSelector(raw) {
    const selector = String(raw || "").trim();
    return selector.startsWith("#") ? selector : "";
  }

  function registerRequestClaim(targetSelector, requestId) {
    const selector = normalizeSelector(targetSelector);
    if (!selector) return;
    const current = Number(latestRequestByTarget.get(selector) || 0);
    if (requestId > current) {
      latestRequestByTarget.set(selector, requestId);
    }
  }

  function canApplyRequestTarget(targetSelector, requestId) {
    const selector = normalizeSelector(targetSelector);
    if (!selector) return false;
    const current = Number(latestRequestByTarget.get(selector) || 0);
    if (requestId < current) {
      return false;
    }
    latestRequestByTarget.set(selector, requestId);
    return true;
  }

  function removeElement(selectorOrId) {
    if (!selectorOrId) return;
    let el = null;
    if (typeof selectorOrId === "string" && selectorOrId.startsWith("#")) {
      el = document.querySelector(selectorOrId);
    }
    if (!el && typeof selectorOrId === "string") {
      el = document.querySelector(selectorOrId) || document.getElementById(selectorOrId);
    }
    if (el) {
      el.remove();
    }
  }

  async function loadAndReplaceFromUrl(url, targetSelector, options) {
    if (!url || !targetSelector) return false;
    const resp = await fetch(url, {
      credentials: "same-origin",
      headers: wantsJsonHeaders({ "Accept": "text/html,application/xhtml+xml" }),
    });
    if (!resp.ok) return false;
    const text = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(text, "text/html");
    const next = doc.querySelector(targetSelector);
    if (!next) return false;
    return replaceTargetHtml(targetSelector, next.innerHTML, options || {});
  }

  async function loadAndReplaceManyFromUrl(url, targetOps, options) {
    const ops = Array.isArray(targetOps) ? targetOps.filter((op) => op && op.target) : [];
    if (!url || !ops.length) return false;

    const resp = await fetch(url, {
      credentials: "same-origin",
      headers: wantsJsonHeaders({ "Accept": "text/html,application/xhtml+xml" }),
    });
    if (!resp.ok) return false;
    const text = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(text, "text/html");

    let anyApplied = false;
    ops.forEach((op) => {
      const selector = normalizeSelector(op && op.target);
      if (!selector) return;
      const next = doc.querySelector(selector);
      if (!next) return;
      const replaced = replaceTargetHtml(selector, next.innerHTML, {
        preserveScroll: !!op.preserveScroll,
        preserveOpenCollapses: !!(options && options.preserveOpenCollapses),
        focusRowId: options && options.focusRowId,
        flashRow: options ? options.flashRow !== false : true,
      });
      anyApplied = anyApplied || replaced;
    });
    return anyApplied;
  }

  function closeEnclosingModal(sourceEl) {
    if (!sourceEl || !sourceEl.closest) return;
    const modalEl = sourceEl.closest(".modal");
    if (!modalEl) return;
    try {
      if (window.bootstrap && window.bootstrap.Modal) {
        const instance = window.bootstrap.Modal.getInstance(modalEl) || new window.bootstrap.Modal(modalEl);
        instance.hide();
      }
    } catch (_) {}
    cleanupModalState(true);
    window.setTimeout(() => cleanupModalState(false), 80);
  }

  function shouldSkip(el) {
    if (!el) return true;
    const noAsync = el.closest("[data-admin-async='false']");
    return !!noAsync;
  }

  function resolveConfirmMessage(el) {
    return (el.getAttribute("data-async-confirm") || "").trim();
  }

  function updateQuickCloseSelect(container, rows) {
    const select = container.querySelector("[data-reemplazo-search-select]");
    const feedback = container.querySelector("[data-reemplazo-search-feedback]");
    if (!select) return;

    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "— Selecciona una candidata —";
    select.appendChild(empty);

    rows.forEach((item) => {
      const id = Number(item && item.id);
      if (!Number.isFinite(id) || id <= 0) return;
      const option = document.createElement("option");
      option.value = String(id);
      option.textContent = String(item.label || item.nombre || `ID ${id}`);
      select.appendChild(option);
    });

    if (feedback) {
      if (!rows.length) {
        feedback.textContent = "No se encontraron candidatas para esa búsqueda.";
      } else {
        feedback.textContent = `${rows.length} candidata(s) encontrada(s).`;
      }
    }
  }

  async function runQuickCloseCandidateSearch(container) {
    if (!container) return;
    const input = container.querySelector("[data-reemplazo-search-input]");
    if (!input) return;
    const url = (input.getAttribute("data-search-url") || "").trim();
    const q = String(input.value || "").trim();
    if (!url) return;

    if (q.length < 2) {
      updateQuickCloseSelect(container, []);
      const feedback = container.querySelector("[data-reemplazo-search-feedback]");
      if (feedback) feedback.textContent = "Escribe al menos 2 caracteres para buscar.";
      return;
    }

    input.dataset.searchNonce = String((Number(input.dataset.searchNonce || "0") || 0) + 1);
    const currentNonce = input.dataset.searchNonce;
    const targetUrl = `${url}?q=${encodeURIComponent(q)}&limit=25`;
    try {
      const resp = await fetch(targetUrl, {
        credentials: "same-origin",
        headers: wantsJsonHeaders(),
      });
      if (!resp.ok) throw new Error("search-failed");
      const payload = await resp.json();
      if (input.dataset.searchNonce !== currentNonce) return;
      const rows = Array.isArray(payload && payload.items) ? payload.items : [];
      updateQuickCloseSelect(container, rows);
    } catch (_) {
      const feedback = container.querySelector("[data-reemplazo-search-feedback]");
      if (feedback) feedback.textContent = "No se pudo buscar candidatas en este momento.";
      updateQuickCloseSelect(container, []);
    }
  }

  function onReemplazoSearchInput(ev) {
    const input = ev.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.matches("[data-reemplazo-search-input]")) return;
    const container = input.closest("[data-reemplazo-quick-close]");
    if (!container) return;

    const prev = Number(input.dataset.searchTimerId || "0");
    if (prev) window.clearTimeout(prev);
    const timerId = window.setTimeout(() => {
      runQuickCloseCandidateSearch(container);
    }, 260);
    input.dataset.searchTimerId = String(timerId);
  }

  function onReemplazoSearchKeydown(ev) {
    const input = ev.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.matches("[data-reemplazo-search-input]")) return;
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const container = input.closest("[data-reemplazo-quick-close]");
    if (!container) return;
    runQuickCloseCandidateSearch(container);
  }

  function onReemplazoSearchTrigger(ev) {
    const trigger = ev.target && ev.target.closest ? ev.target.closest("[data-reemplazo-search-trigger]") : null;
    if (!trigger) return;
    const container = trigger.closest("[data-reemplazo-quick-close]");
    if (!container) return;
    ev.preventDefault();
    runQuickCloseCandidateSearch(container);
  }

  function normalizePayloadTargets(payload, context) {
    const out = [];
    const seen = new Set();
    const entries = Array.isArray(payload && payload.update_targets) ? payload.update_targets : [];
    const legacyTarget = normalizeSelector(payload && payload.update_target);
    const legacyHtml = (payload && typeof payload.replace_html === "string") ? payload.replace_html : null;
    const fallbackRedirect = (payload && typeof payload.redirect_url === "string") ? payload.redirect_url : "";
    const fallbackPreserve = !!(context && context.preserveScroll);

    function pushTarget(entry) {
      if (!entry || !entry.target) return;
      const key = String(entry.target);
      if (seen.has(key)) return;
      seen.add(key);
      out.push(entry);
    }

    entries.forEach((entry) => {
      if (typeof entry === "string") {
        const target = normalizeSelector(entry);
        if (!target) return;
        pushTarget({
          target,
          replaceHtml: entries.length === 1 ? legacyHtml : null,
          redirectUrl: fallbackRedirect,
          preserveScroll: fallbackPreserve,
        });
        return;
      }
      if (!entry || typeof entry !== "object") return;
      const target = normalizeSelector(entry.target || entry.update_target);
      if (!target) return;
      const replaceHtml = typeof entry.replace_html === "string"
        ? entry.replace_html
        : ((entries.length === 1 && legacyHtml) ? legacyHtml : null);
      const redirectUrl = typeof entry.redirect_url === "string"
        ? entry.redirect_url
        : fallbackRedirect;
      const invalidate = entry.invalidate === true || entry.refresh === true;
      const preserveScroll = typeof entry.preserve_scroll === "boolean"
        ? entry.preserve_scroll
        : fallbackPreserve;
      pushTarget({ target, replaceHtml, redirectUrl, invalidate, preserveScroll });
    });

    if (!out.length && legacyTarget) {
      pushTarget({
        target: legacyTarget,
        replaceHtml: legacyHtml,
        redirectUrl: fallbackRedirect,
        preserveScroll: fallbackPreserve,
      });
    }

    const invalidates = Array.isArray(payload && payload.invalidate_targets) ? payload.invalidate_targets : [];
    invalidates.forEach((entry) => {
      if (typeof entry === "string") {
        const target = normalizeSelector(entry);
        if (!target) return;
        pushTarget({ target, replaceHtml: null, redirectUrl: fallbackRedirect, invalidate: true, preserveScroll: fallbackPreserve });
        return;
      }
      if (!entry || typeof entry !== "object") return;
      const target = normalizeSelector(entry.target || entry.update_target);
      if (!target) return;
      const redirectUrl = typeof entry.redirect_url === "string" ? entry.redirect_url : fallbackRedirect;
      const preserveScroll = typeof entry.preserve_scroll === "boolean" ? entry.preserve_scroll : fallbackPreserve;
      pushTarget({ target, replaceHtml: null, redirectUrl, invalidate: true, preserveScroll });
    });

    return out;
  }

  async function applyPayloadTargets(targets, requestId, options) {
    let anyApplied = false;
    const fetchGroups = new Map(); // redirect_url -> targetOps[]

    for (const targetOp of (targets || [])) {
      if (!targetOp || !targetOp.target) continue;
      if (!canApplyRequestTarget(targetOp.target, requestId)) continue;

      const selector = targetOp.target;
      const targetEl = document.querySelector(selector);
      if (!targetEl) continue;

      if (typeof targetOp.replaceHtml === "string") {
        const replaced = replaceTargetHtml(selector, targetOp.replaceHtml, {
          preserveScroll: !!targetOp.preserveScroll,
          preserveOpenCollapses: !!(options && options.preserveOpenCollapses),
          focusRowId: options && options.focusRowId,
          flashRow: options ? options.flashRow !== false : true,
        });
        anyApplied = anyApplied || replaced;
        continue;
      }

      if ((targetOp.invalidate || targetOp.redirectUrl) && targetOp.redirectUrl) {
        const key = String(targetOp.redirectUrl || "").trim();
        if (!key) continue;
        if (!fetchGroups.has(key)) fetchGroups.set(key, []);
        fetchGroups.get(key).push(targetOp);
      }
    }

    for (const [url, groupedOps] of fetchGroups.entries()) {
      const ops = Array.isArray(groupedOps) ? groupedOps : [];
      if (!ops.length) continue;
      if (ops.length === 1) {
        const op = ops[0];
        const refreshed = await loadAndReplaceFromUrl(url, op.target, {
          preserveScroll: !!op.preserveScroll,
          preserveOpenCollapses: !!(options && options.preserveOpenCollapses),
          focusRowId: options && options.focusRowId,
          flashRow: options ? options.flashRow !== false : true,
        });
        anyApplied = anyApplied || refreshed;
        continue;
      }

      const refreshedMany = await loadAndReplaceManyFromUrl(url, ops, options || {});
      if (refreshedMany) {
        anyApplied = true;
        continue;
      }

      // Fallback seguro: comportamiento previo (un fetch por target).
      for (const op of ops) {
        const refreshed = await loadAndReplaceFromUrl(url, op.target, {
          preserveScroll: !!op.preserveScroll,
          preserveOpenCollapses: !!(options && options.preserveOpenCollapses),
          focusRowId: options && options.focusRowId,
          flashRow: options ? options.flashRow !== false : true,
        });
        anyApplied = anyApplied || refreshed;
      }
    }

    return anyApplied;
  }

  async function handleJsonPayload(payload, context) {
    const ok = Boolean(payload && (payload.success === true || payload.ok === true));
    const message = (payload && (payload.message || payload.detail)) || "";
    const category = (payload && (payload.category || (ok ? "success" : "danger"))) || "info";
    const hasExplicitTarget = !!(payload && Object.prototype.hasOwnProperty.call(payload, "update_target"));

    const targetOps = normalizePayloadTargets(payload || {}, context || {});
    targetOps.forEach((op) => registerRequestClaim(op && op.target, context.requestId));
    const hadTargets = targetOps.length > 0;
    const hadAppliedTarget = await applyPayloadTargets(targetOps, context.requestId, {
      focusRowId: payload && payload.focus_row_id,
      flashRow: payload ? payload.flash_row !== false : true,
      preserveOpenCollapses: payload && payload.preserve_open_collapses === true,
    });

    if (payload && payload.remove_element) {
      removeElement(payload.remove_element);
    }

    if (ok) {
      if (message) showToast(message, normalizeType(category, true));
      if (payload && payload.redirect_url) {
        if (hasExplicitTarget && payload.update_target === null) {
          window.location.assign(payload.redirect_url);
          return true;
        }
      }
      if (payload && payload.redirect_url && !hadAppliedTarget) {
        const candidateTarget = hasExplicitTarget ? payload.update_target : context.updateTarget;
        const targetSelector = normalizeSelector(candidateTarget);
        if (targetSelector) {
          const replaced = await loadAndReplaceFromUrl(payload.redirect_url, targetSelector, { preserveScroll: context.preserveScroll });
          if (!replaced) {
            window.location.assign(payload.redirect_url);
          }
        } else {
          window.location.assign(payload.redirect_url);
        }
      }
      return true;
    }

    if (Array.isArray(payload && payload.errors) && payload.errors.length) {
      showToast(payload.errors.join("\n"), "danger");
    } else {
      showToast(message || "No se pudo completar la acción.", normalizeType(category, false));
    }

    if (payload && payload.redirect_url && (payload.error_code === "csrf" || payload.error_code === "session_expired")) {
      window.location.assign(payload.redirect_url);
    }

    return false;
  }

  async function parseResponse(resp) {
    const contentType = String(resp.headers.get("content-type") || "").toLowerCase();
    const text = await resp.text();
    if (contentType.includes("application/json")) {
      return { type: "json", data: parseJsonSafe(text), raw: text };
    }
    return { type: "text", data: text, raw: text };
  }

  async function handleAsyncRequest({
    url,
    method,
    body,
    sourceEl,
    busyContainer,
    submitter,
    updateTarget,
    noLoader,
    headers,
    preserveScroll,
  }) {
    const container = busyContainer || sourceEl;
    if (!container || container.dataset[BUSY_KEY] === "1") {
      clearGlobalLoaders();
      return false;
    }
    const requestId = ++globalRequestSeq;
    registerRequestClaim(updateTarget, requestId);

    setBusyState(container, submitter, true);
    if (!noLoader && window.AppLoader && typeof window.AppLoader.show === "function") {
      window.AppLoader.show("Procesando...");
    }

    try {
      lastResponseMeta = null;
      const resp = await fetch(url, {
        method,
        body,
        credentials: "same-origin",
        headers: wantsJsonHeaders(headers || {}),
      });

      const parsed = await parseResponse(resp);

      if (parsed.type === "json") {
        const payload = parsed.data || {};
        if (typeof payload.update_target === "undefined" && updateTarget) {
          payload.update_target = updateTarget;
        }
        const ok = await handleJsonPayload(payload, { updateTarget, preserveScroll, requestId });
        lastResponseMeta = {
          ok: !!ok,
          status: Number(resp.status || 0),
          message: String(payload.message || payload.detail || ""),
          category: String(payload.category || (ok ? "success" : "danger")),
          errorCode: String(payload.error_code || ""),
          errors: Array.isArray(payload.errors) ? payload.errors.map((e) => String(e || "")) : [],
        };
        if (ok) {
          closeEnclosingModal(sourceEl);
        }
        if (!resp.ok && !payload.message && !Array.isArray(payload.errors)) {
          showToast(statusMessage(resp.status), "danger");
        }
        return ok;
      }

      if (resp.ok && updateTarget) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(parsed.data, "text/html");
        const node = doc.querySelector(updateTarget);
        if (node) {
          replaceTargetHtml(updateTarget, node.innerHTML, { preserveScroll });
          closeEnclosingModal(sourceEl);
          return true;
        }
      }

      showToast(statusMessage(resp.status), "danger");
      lastResponseMeta = {
        ok: false,
        status: Number(resp.status || 0),
        message: statusMessage(resp.status),
        category: "danger",
        errorCode: "",
        errors: [],
      };
      if (resp.status === 401 || resp.status === 403) {
        const redirectTo = resp.url || "";
        if (redirectTo) {
          window.location.assign(redirectTo);
        }
      }
      return resp.status >= 500 ? null : false;
    } catch (_err) {
      showToast("No se pudo conectar con el servidor. Intenta nuevamente.", "danger");
      lastResponseMeta = {
        ok: false,
        status: 0,
        message: "No se pudo conectar con el servidor. Intenta nuevamente.",
        category: "danger",
        errorCode: "network_error",
        errors: [],
      };
      return null;
    } finally {
      setBusyState(container, submitter, false);
      clearGlobalLoaders();
    }
  }

  function buildFormRequest(form, submitter) {
    const method = String(form.getAttribute("method") || "POST").toUpperCase();
    const action = form.getAttribute("action") || window.location.href;
    const asyncAction = (form.getAttribute("data-async-action") || "").trim();
    const requestUrl = asyncAction || action;
    const noLoader = form.hasAttribute("data-no-loader");
    const updateTarget = (form.getAttribute("data-async-target") || "").trim();

    if (method === "GET") {
      const params = new URLSearchParams(new FormData(form));
      if (updateTarget && !params.has("_async_target")) {
        params.set("_async_target", updateTarget);
      }
      const url = new URL(requestUrl, window.location.origin);
      url.search = params.toString();
      return {
        url: url.toString(),
        method: "GET",
        body: null,
        updateTarget,
        noLoader,
        headers: { "X-CSRFToken": getCSRFToken(form) },
      };
    }

    const data = new FormData(form);
    if (submitter && submitter.name && !data.has(submitter.name)) {
      data.append(submitter.name, submitter.value || "1");
    }
    if (updateTarget && !data.has("_async_target")) {
      data.append("_async_target", updateTarget);
    }

    return {
      url: requestUrl,
      method,
      body: data,
      updateTarget,
      noLoader,
      headers: { "X-CSRFToken": getCSRFToken(form) },
    };
  }

  async function onSubmit(ev) {
    const form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches("form[data-admin-async-form]")) return;
    if (shouldSkip(form)) return;

    const confirmMsg = resolveConfirmMessage(form);
    if (confirmMsg && !window.confirm(confirmMsg)) {
      ev.preventDefault();
      return;
    }

    if (!window.fetch) return;

    ev.preventDefault();
    const submitter = ev.submitter || form.querySelector('button[type="submit"],input[type="submit"]');
    const req = buildFormRequest(form, submitter);
    const containerSel = (form.getAttribute("data-async-busy-container") || "").trim();
    const busyContainer = containerSel ? document.querySelector(containerSel) : form;
    const preserveScroll = form.getAttribute("data-async-preserve-scroll") === "true";

    const result = await handleAsyncRequest({
      ...req,
      sourceEl: form,
      busyContainer,
      submitter,
      preserveScroll,
    });

    if (result === null && form.getAttribute("data-async-fallback") === "native") {
      form.submit();
    }
  }

  async function onClick(ev) {
    const link = ev.target && ev.target.closest ? ev.target.closest("a[data-admin-async-link]") : null;
    if (!link) return;
    if (shouldSkip(link)) return;

    const href = link.getAttribute("href") || "";
    if (!href || href === "#") return;
    if (!window.fetch) return;

    const confirmMsg = resolveConfirmMessage(link);
    if (confirmMsg && !window.confirm(confirmMsg)) {
      ev.preventDefault();
      return;
    }

    ev.preventDefault();

    const containerSel = (link.getAttribute("data-async-busy-container") || "").trim();
    const busyContainer = containerSel ? document.querySelector(containerSel) : (link.closest("[data-admin-async-scope]") || link);
    const updateTarget = (link.getAttribute("data-async-target") || "").trim();
    const preserveScroll = link.getAttribute("data-async-preserve-scroll") === "true";

    await handleAsyncRequest({
      url: href,
      method: "GET",
      body: null,
      sourceEl: link,
      busyContainer,
      submitter: link,
      updateTarget,
      noLoader: link.hasAttribute("data-no-loader"),
      headers: { "X-CSRFToken": getCSRFToken(null) },
      preserveScroll,
    });
  }

  function init() {
    document.addEventListener("submit", onSubmit, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("shown.bs.collapse", (ev) => {
      const target = ev && ev.target;
      if (!target || !target.id) return;
      const toggle = document.querySelector(`[data-bs-toggle='collapse'][data-bs-target='#${escapeCssToken(target.id)}']`);
      if (!toggle) return;
      toggle.setAttribute("aria-expanded", "true");
      updateCollapseToggleLabel(toggle, true);
    });
    document.addEventListener("hidden.bs.collapse", (ev) => {
      const target = ev && ev.target;
      if (!target || !target.id) return;
      const toggle = document.querySelector(`[data-bs-toggle='collapse'][data-bs-target='#${escapeCssToken(target.id)}']`);
      if (!toggle) return;
      toggle.setAttribute("aria-expanded", "false");
      updateCollapseToggleLabel(toggle, false);
    });
    document.addEventListener("input", onReemplazoSearchInput, true);
    document.addEventListener("keydown", onReemplazoSearchKeydown, true);
    document.addEventListener("click", onReemplazoSearchTrigger, true);
    document.addEventListener("admin:content-updated", (ev) => {
      const container = ev && ev.detail ? ev.detail.container : null;
      syncCollapseToggleLabels(container || document);
    });
    syncCollapseToggleLabels(document);
    bindReemplazoModalGuards();
  }

  window.AdminAsync = {
    init,
    request: handleAsyncRequest,
    replaceTargetHtml,
    getLastResponseMeta: () => (lastResponseMeta ? { ...lastResponseMeta } : null),
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
