// static/js/core/admin_async.js
// Infraestructura común para interacciones async en admin.

(function () {
  "use strict";

  if (window.AdminAsync) return;

  const DEFAULT_BUTTON_TEXT = "Procesando...";
  const DEFAULT_LINK_TEXT = "Cargando...";
  const BUSY_KEY = "adminAsyncBusy";
  const REEMPLAZO_MODAL_ATTR = 'data-reemplazo-modal';
  const BODY_MODAL_ATTR = 'data-admin-body-modal';
  const reemplazoModalAncestorState = new WeakMap();
  const reemplazoModalTeleportState = new WeakMap();
  const rowHighlightTimers = new WeakMap();
  const targetResponseCache = new Map();
  const TARGET_CACHE_TTL_MS = 90000;
  let globalRequestSeq = 0;
  const latestRequestByTarget = new Map();
  const activeRequestControllerByTarget = new Map();
  const lastSubmitterByForm = new WeakMap();
  let lastResponseMeta = null;
  let secondaryBound = false;
  let modalGuardsBound = false;
  let gestionarPlanBound = false;
  const scheduleIdle = (cb, timeout = 700) => {
    if (typeof window.requestIdleCallback === "function") {
      return window.requestIdleCallback(cb, { timeout });
    }
    return window.setTimeout(cb, 60);
  };
  const candidatasIndexState = new WeakMap();
  let candidatasIndexBound = false;

  function getCandidatasIndexForm(node) {
    if (!node || !node.closest) return null;
    return node.closest("[data-cand-index-search]");
  }

  function getCandidatasIndexState(form) {
    if (!form) return null;
    let state = candidatasIndexState.get(form);
    if (!state) {
      state = {
        timer: null,
        controller: null,
        requestSeq: 0,
        active: 0,
        items: [],
      };
      candidatasIndexState.set(form, state);
    }
    return state;
  }

  function clearCandidatasIndexTimer(state) {
    if (!state || !state.timer) return;
    window.clearTimeout(state.timer);
    state.timer = null;
  }

  function stopCandidatasIndexRequest(state) {
    if (!state || !state.controller) return;
    try {
      state.controller.abort();
    } catch (_) {}
    state.controller = null;
  }

  function hideCandidatasIndexSuggestions(form, state) {
    if (!form) return;
    const box = form.querySelector("#candSuggest");
    const input = form.querySelector('input[name="q"]');
    if (box) box.hidden = true;
    if (input) input.setAttribute("aria-expanded", "false");
    if (state) state.active = 0;
  }

  function renderCandidatasIndexSuggestions(form, state) {
    if (!form || !state) return;
    const input = form.querySelector('input[name="q"]');
    const box = form.querySelector("#candSuggest");
    if (!input || !box) return;

    box.innerHTML = "";
    if (!Array.isArray(state.items) || !state.items.length) {
      hideCandidatasIndexSuggestions(form, state);
      return;
    }

    state.items.forEach((item, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = index === state.active ? "is-active" : "";
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", index === state.active ? "true" : "false");
      const meta = [
        item.codigo || "sin código",
        item.edad ? `${item.edad} años` : "",
        item.telefono || "",
        item.estado_label || "",
      ].filter(Boolean).join(" · ");
      btn.innerHTML = '<span><strong></strong><div class="small cand-muted"></div></span><span class="small cand-muted">Abrir</span>';
      btn.querySelector("strong").textContent = item.nombre || "Sin nombre";
      btn.querySelector(".small").textContent = meta;
      btn.addEventListener("click", () => {
        if (item.detail_url) {
          if (window.AdminNav && typeof window.AdminNav.navigateTo === "function") {
            window.AdminNav.navigateTo(item.detail_url);
          } else {
            window.location.href = item.detail_url;
          }
        }
      });
      box.appendChild(btn);
    });

    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function scheduleCandidatasIndexFetch(form, state) {
    if (!form || !state) return;
    const input = form.querySelector('input[name="q"]');
    if (!input) return;
    clearCandidatasIndexTimer(state);
    state.timer = window.setTimeout(() => {
      state.timer = null;

      const q = String(input.value || "").trim();
      const searchUrl = String(form.dataset.searchUrl || "").trim();
      if (q.length < 2 || !searchUrl) {
        stopCandidatasIndexRequest(state);
        state.items = [];
        state.active = 0;
        renderCandidatasIndexSuggestions(form, state);
        return;
      }

      stopCandidatasIndexRequest(state);
      state.controller = new AbortController();
      const requestSeq = ++state.requestSeq;
      const url = new URL(searchUrl, window.location.origin);
      url.searchParams.set("q", q);
      url.searchParams.set("limit", "7");

      fetch(url.toString(), {
        headers: { Accept: "application/json" },
        signal: state.controller.signal,
      })
        .then((resp) => (resp && resp.ok ? resp.json() : Promise.reject(new Error("search_failed"))))
        .then((payload) => {
          if (requestSeq !== state.requestSeq || !form.isConnected) return;
          state.items = Array.isArray(payload && payload.items) ? payload.items : [];
          state.active = 0;
          renderCandidatasIndexSuggestions(form, state);
        })
        .catch((err) => {
          if (err && err.name === "AbortError") return;
          if (requestSeq !== state.requestSeq || !form.isConnected) return;
          state.items = [];
          state.active = 0;
          renderCandidatasIndexSuggestions(form, state);
        })
        .finally(() => {
          if (requestSeq === state.requestSeq) {
            state.controller = null;
          }
        });
    }, 220);
  }

  function syncCandidatasOperativoIndex(root) {
    const scope = root && root.querySelector ? root : document;
    const form = scope.querySelector("[data-cand-index-search]");
    if (!form) return;

    form.dataset.candIndexBound = "1";
    const state = getCandidatasIndexState(form);
    if (!state) return;

    if (form.dataset.candIndexPrevBound !== "1") {
      form.dataset.candIndexPrevBound = "1";
    }

    const box = form.querySelector("#candSuggest");
    const input = form.querySelector('input[name="q"]');
    if (box) box.hidden = true;
    if (input) input.setAttribute("aria-expanded", "false");

    scope.querySelectorAll("[data-row-url]").forEach((row) => {
      if (row.dataset.candRowBound === "1") return;
      row.dataset.candRowBound = "1";
      row.style.cursor = "pointer";
    });
  }

  function handleCandidatasOperativoInput(ev) {
    const input = ev && ev.target ? ev.target : null;
    if (!input || String(input.name || "") !== "q") return;
    const form = getCandidatasIndexForm(input);
    if (!form) return;
    const state = getCandidatasIndexState(form);
    if (!state) return;
    scheduleCandidatasIndexFetch(form, state);
  }

  function handleCandidatasOperativoKeydown(ev) {
    const input = ev && ev.target ? ev.target : null;
    if (!input || String(input.name || "") !== "q") return;
    const form = getCandidatasIndexForm(input);
    if (!form) return;
    const state = getCandidatasIndexState(form);
    if (!state) return;
    const box = form.querySelector("#candSuggest");

    if (ev.key === "Escape") {
      hideCandidatasIndexSuggestions(form, state);
      return;
    }
    if (!Array.isArray(state.items) || !state.items.length || !box || box.hidden) return;
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      state.active = Math.min(state.items.length - 1, state.active + 1);
      renderCandidatasIndexSuggestions(form, state);
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state.active = Math.max(0, state.active - 1);
      renderCandidatasIndexSuggestions(form, state);
      return;
    }
    if (ev.key === "Enter") {
      ev.preventDefault();
      const selected = state.items[state.active] || state.items[0];
      if (selected && selected.detail_url) {
        if (window.AdminNav && typeof window.AdminNav.navigateTo === "function") {
          window.AdminNav.navigateTo(selected.detail_url);
        } else {
          window.location.href = selected.detail_url;
        }
      }
    }
  }

  function handleCandidatasOperativoClick(ev) {
    const target = ev && ev.target ? ev.target : null;
    if (!target) return;

    const row = target.closest ? target.closest("[data-row-url]") : null;
    if (row) {
      if (target.closest && target.closest("a, button, input, select, textarea")) return;
      const url = String(row.getAttribute("data-row-url") || "").trim();
      if (url) {
        if (window.AdminNav && typeof window.AdminNav.navigateTo === "function") {
          window.AdminNav.navigateTo(url);
        } else {
          window.location.href = url;
        }
      }
      return;
    }

    if (target.closest && target.closest("[data-cand-index-search]")) return;
    document.querySelectorAll("[data-cand-index-search]").forEach((form) => {
      const state = candidatasIndexState.get(form);
      hideCandidatasIndexSuggestions(form, state || null);
    });
  }

  function bindCandidatasOperativoIndexRuntime() {
    if (candidatasIndexBound) return;
    candidatasIndexBound = true;

    document.addEventListener("input", handleCandidatasOperativoInput, true);
    document.addEventListener("keydown", handleCandidatasOperativoKeydown, true);
    document.addEventListener("click", handleCandidatasOperativoClick, true);
    document.addEventListener("admin:content-updated", (ev) => {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      syncCandidatasOperativoIndex(detail.container || document);
    });
    document.addEventListener("admin:navigation-complete", (ev) => {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      syncCandidatasOperativoIndex(detail.viewport || document);
    });

    syncCandidatasOperativoIndex(document);
  }

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
    if (status === 419) return "Tu sesión de seguridad expiró. Recarga la página e intenta de nuevo.";
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

    const controls = container.querySelectorAll('button, input, select, textarea, a[data-admin-async-link]');
    controls.forEach((btn) => {
      const isAnchor = btn.tagName === "A";
      const isHiddenInput = btn.tagName === "INPUT" && String(btn.type || "").toLowerCase() === "hidden";
      if (isHiddenInput) return;
      if (isBusy) {
        btn.dataset._adminAsyncPrevDisabled = btn.disabled ? "1" : "0";
        if (!isAnchor) {
          btn.disabled = true;
        } else {
          btn.setAttribute("aria-disabled", "true");
          btn.classList.add("disabled");
        }
        btn.classList.add("is-loading");
      } else {
        const prev = btn.dataset._adminAsyncPrevDisabled;
        if (!isAnchor) {
          if (prev === "0") btn.disabled = false;
        } else {
          btn.removeAttribute("aria-disabled");
          btn.classList.remove("disabled");
        }
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

  function forceHideManagedModal(modalEl) {
    if (!modalEl || !isBodyManagedModal(modalEl) || !modalEl.classList.contains("show")) return;
    try {
      modalEl.classList.remove("show");
      modalEl.style.display = "none";
      modalEl.setAttribute("aria-hidden", "true");
      modalEl.removeAttribute("aria-modal");
      modalEl.removeAttribute("role");
      resetManagedModalOnClose(modalEl);
      if (window.bootstrap && window.bootstrap.Modal) {
        const instance = window.bootstrap.Modal.getInstance(modalEl);
        if (instance && typeof instance.dispose === "function") instance.dispose();
      }
      restoreModalAncestors(modalEl);
      restoreTeleportedReemplazoModal(modalEl);
      cleanupModalState(true);
      const selector = modalEl.id ? `[data-bs-toggle="modal"][data-bs-target="#${escapeCssToken(modalEl.id)}"]` : "";
      const trigger = selector ? document.querySelector(selector) : null;
      if (trigger && typeof trigger.focus === "function") trigger.focus();
    } catch (_) {}
  }

  function requestManagedModalHide(modalEl) {
    if (!modalEl || !isBodyManagedModal(modalEl)) return;
    try {
      if (window.bootstrap && window.bootstrap.Modal) {
        const instance = window.bootstrap.Modal.getInstance(modalEl) || new window.bootstrap.Modal(modalEl);
        instance.hide();
        window.setTimeout(() => {
          try {
            if (modalEl.classList.contains("show")) {
              (window.bootstrap.Modal.getInstance(modalEl) || instance).hide();
            }
          } catch (_) {}
        }, 180);
        window.setTimeout(() => forceHideManagedModal(modalEl), 420);
      } else {
        forceHideManagedModal(modalEl);
      }
    } catch (_) {
      forceHideManagedModal(modalEl);
    }
  }

  function resetManagedModalOnClose(modalEl) {
    if (!modalEl || String(modalEl.getAttribute("data-admin-reset-on-close") || "").toLowerCase() !== "true") return;
    try {
      modalEl.querySelectorAll("form").forEach((form) => {
        if (typeof form.reset === "function") form.reset();
        form.querySelectorAll("[data-error-for]").forEach((el) => { el.textContent = ""; });
        form.removeAttribute("aria-busy");
        delete form.dataset.quickBusy;
      });
    } catch (_) {}
  }

  function isReemplazoModal(el) {
    return !!(el && el.matches && el.matches(`.modal[${REEMPLAZO_MODAL_ATTR}="1"]`));
  }

  function isBodyManagedModal(el) {
    if (!el || !el.matches) return false;
    return isReemplazoModal(el) || el.matches(`.modal[${BODY_MODAL_ATTR}="true"], .modal[${BODY_MODAL_ATTR}="1"]`);
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
      const modalZ = isReemplazoModal(modalEl)
        ? 1080
        : (Number(modalEl.getAttribute("data-admin-modal-z") || "2000") || 2000);
      const backdropZ = Math.max(0, modalZ - 10);
      modalEl.style.setProperty("z-index", String(modalZ));
      modalEl.style.setProperty("pointer-events", "auto");
      const dialog = modalEl.querySelector(".modal-dialog");
      const content = modalEl.querySelector(".modal-content");
      if (dialog) dialog.style.setProperty("pointer-events", "auto");
      if (content) content.style.setProperty("pointer-events", "auto");

      const backdrops = Array.from(document.querySelectorAll(".modal-backdrop"));
      backdrops.forEach((node) => {
        node.style.setProperty("z-index", String(backdropZ));
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

  function bindManagedModalGuards() {
    if (modalGuardsBound) return;
    modalGuardsBound = true;

    document.addEventListener("keydown", (ev) => {
      if (!ev || ev.key !== "Escape" || ev.defaultPrevented) return;
      const visibleManagedModals = Array.from(document.querySelectorAll(".modal.show")).filter(isBodyManagedModal);
      const modalEl = visibleManagedModals[visibleManagedModals.length - 1];
      if (!modalEl) return;
      requestManagedModalHide(modalEl);
      ev.preventDefault();
    }, true);

    document.addEventListener("click", (ev) => {
      const target = ev && ev.target;
      if (!target) return;
      const visibleManagedModals = Array.from(document.querySelectorAll(".modal.show")).filter(isBodyManagedModal);
      const modalEl = visibleManagedModals[visibleManagedModals.length - 1];
      if (!modalEl) return;
      const backdropMode = String(modalEl.getAttribute("data-bs-backdrop") || "").trim().toLowerCase();
      if (backdropMode === "static") return;
      const clickedBackdrop = target === modalEl || (target.classList && target.classList.contains("modal-backdrop"));
      if (!clickedBackdrop) return;
      requestManagedModalHide(modalEl);
    }, true);

    document.addEventListener("show.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isBodyManagedModal(modalEl)) return;
      teleportReemplazoModalToBody(modalEl);
      neutralizeModalAncestors(modalEl);
      normalizeModalBackdrops();
      enforceReemplazoModalLayering(modalEl);
      cleanupModalState(false);
    });

    document.addEventListener("shown.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isBodyManagedModal(modalEl)) return;
      normalizeModalBackdrops();
      enforceReemplazoModalLayering(modalEl);
    });

    document.addEventListener("hidden.bs.modal", (ev) => {
      const modalEl = ev && ev.target;
      if (!isBodyManagedModal(modalEl)) return;
      restoreModalAncestors(modalEl);
      restoreTeleportedReemplazoModal(modalEl);
      resetManagedModalOnClose(modalEl);
      normalizeModalBackdrops();
      cleanupModalState(false);
    });
  }

  function suppressTargetRowScroll(targetSelector) {
    return normalizeSelector(targetSelector) === "#clienteSolicitudesAsyncRegion";
  }

  function replaceTargetHtml(targetSelector, html, options) {
    if (!targetSelector || typeof html !== "string") return false;
    const target = document.querySelector(targetSelector);
    if (!target) return false;
    if (target.innerHTML === html) return true;

    const suppressRowScroll = suppressTargetRowScroll(targetSelector);
    const preserveScroll = resolvePreserveScroll(targetSelector, options && options.preserveScroll);
    const rememberCollapse = (
      (options && options.preserveOpenCollapses === true)
      || target.getAttribute("data-async-remember-collapse") === "true"
    );
    const openCollapseIds = rememberCollapse
      ? Array.from(target.querySelectorAll(".collapse.show[id]")).map((el) => String(el.id || "").trim()).filter(Boolean)
      : [];
    const snapshot = captureVisualSnapshot(target);
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
      restoreVisualSnapshot(target, snapshot, { allowScroll: !suppressRowScroll });
      if (options && options.focusRowId) {
        highlightSolicitudRow(target, options.focusRowId, options.flashRow !== false, !suppressRowScroll);
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

  function cacheKeyFor(url, targetSelector) {
    const u = String(url || "").trim();
    const t = normalizeSelector(targetSelector);
    if (!u || !t) return "";
    return `${u}::${t}`;
  }

  function setTargetCache(url, targetSelector, html) {
    const key = cacheKeyFor(url, targetSelector);
    if (!key || typeof html !== "string") return;
    targetResponseCache.set(key, { html, ts: Date.now() });
    if (targetResponseCache.size > 40) {
      let oldestKey = "";
      let oldestTs = Infinity;
      targetResponseCache.forEach((entry, k) => {
        const ts = Number(entry && entry.ts) || 0;
        if (ts < oldestTs) {
          oldestTs = ts;
          oldestKey = k;
        }
      });
      if (oldestKey) targetResponseCache.delete(oldestKey);
    }
  }

  function getTargetCache(url, targetSelector) {
    const key = cacheKeyFor(url, targetSelector);
    if (!key) return null;
    const hit = targetResponseCache.get(key);
    if (!hit) return null;
    const age = Date.now() - (Number(hit.ts) || 0);
    if (age > TARGET_CACHE_TTL_MS) {
      targetResponseCache.delete(key);
      return null;
    }
    return hit;
  }

  function captureVisualSnapshot(target) {
    if (!target || !target.querySelectorAll) return null;
    const openQuickViews = [];
    target.querySelectorAll(".collapse.show[id]").forEach((panel) => {
      const id = String(panel.id || "").trim();
      if (!id.startsWith("sol-quick-view-")) return;
      const slot = panel.querySelector("[data-quick-view-slot='1']");
      if (!slot) return;
      openQuickViews.push({
        id,
        loaded: slot.dataset.loaded === "1",
        html: String(slot.innerHTML || ""),
      });
    });
    return { openQuickViews };
  }

  function restoreVisualSnapshot(target, snapshot, options) {
    const allowScroll = !(options && options.allowScroll === false);
    const rows = Array.from(target.querySelectorAll("[id^='sol-']"));
    if (!rows.length) return;
    const firstVisible = rows.find((row) => {
      const rect = row.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    });
    if (firstVisible && allowScroll) {
      try {
        firstVisible.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" });
      } catch (_) {}
    }
    const quick = Array.isArray(snapshot && snapshot.openQuickViews) ? snapshot.openQuickViews : [];
    quick.forEach((entry) => {
      const panel = target.querySelector(`#${escapeCssToken(entry.id)}`);
      if (!panel || !panel.classList.contains("show")) return;
      const slot = panel.querySelector("[data-quick-view-slot='1']");
      if (!slot) return;
      if (entry.loaded && entry.html) {
        slot.innerHTML = entry.html;
        slot.dataset.loaded = "1";
      }
    });
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

  function highlightSolicitudRow(target, rowId, flashRow, allowScroll) {
    const id = Number(rowId || 0);
    if (!target || !Number.isFinite(id) || id <= 0) return;
    const row = target.querySelector(`#sol-${id}`);
    if (!row) return;

    if (allowScroll !== false) {
      try {
        row.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
      } catch (_) {}
    }

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

  function tryPushHistoryState(state, url) {
    if (!window.history || typeof window.history.pushState !== "function") return;
    try {
      window.history.pushState(state, "", url);
    } catch (_) {}
  }

  function scheduleDebouncedFormSubmit(form, triggerKey) {
    if (!form) return;
    const msRaw = Number(form.getAttribute("data-async-debounce-ms") || "0");
    const ms = Number.isFinite(msRaw) && msRaw > 0 ? msRaw : 0;
    if (!ms) return;
    const lastKey = String(form.dataset.asyncDebounceLastKey || "");
    if (triggerKey && lastKey === triggerKey) return;
    form.dataset.asyncDebounceLastKey = triggerKey || "";
    const prev = Number(form.dataset.asyncDebounceTimerId || "0");
    if (prev) window.clearTimeout(prev);
    const timerId = window.setTimeout(() => {
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        const btn = form.querySelector('button[type="submit"],input[type="submit"]');
        if (btn) btn.click();
        else form.submit();
      }
    }, ms);
    form.dataset.asyncDebounceTimerId = String(timerId);
  }

  function onAsyncDebouncedInput(ev) {
    const input = ev.target;
    if (!(input instanceof HTMLElement)) return;
    const form = input.closest("form[data-admin-async-form][data-async-debounce-ms]");
    if (!form) return;
    const fieldsRaw = (form.getAttribute("data-async-debounce-fields") || "").trim();
    if (!fieldsRaw) return;
    const fields = fieldsRaw.split(",").map((x) => x.trim()).filter(Boolean);
    const name = String(input.getAttribute("name") || "").trim();
    if (!name || !fields.includes(name)) return;
    const value = String(input.value || "");
    scheduleDebouncedFormSubmit(form, `${name}:${value}`);
  }

  function syncFormFieldsFromUrl(form, urlString) {
    if (!form || !urlString) return;
    try {
      const url = new URL(urlString, window.location.origin);
      const params = url.searchParams;
      const elements = form.querySelectorAll("input[name], select[name], textarea[name]");
      elements.forEach((el) => {
        const name = String(el.getAttribute("name") || "").trim();
        if (!name || String(el.type || "").toLowerCase() === "hidden") return;
        const nextValue = params.get(name);
        if (el.tagName === "SELECT") {
          el.value = nextValue !== null ? nextValue : "";
          return;
        }
        if (String(el.type || "").toLowerCase() === "checkbox") {
          el.checked = nextValue !== null;
          return;
        }
        if (String(el.type || "").toLowerCase() === "radio") {
          el.checked = nextValue !== null && String(el.value || "") === nextValue;
          return;
        }
        el.value = nextValue !== null ? nextValue : "";
      });
    } catch (_) {}
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

  function isAuthRedirectUrl(url) {
    if (!url) return false;
    try {
      const parsed = new URL(String(url), window.location.origin);
      const path = String(parsed.pathname || "").toLowerCase();
      if (path === "/login" || path === "/admin/login" || path === "/clientes/login") return true;
      if (path.endsWith("/login")) return true;
      return false;
    } catch (_) {
      return false;
    }
  }

  function isSameOriginUrl(url) {
    if (!url) return false;
    try {
      const parsed = new URL(String(url), window.location.origin);
      return parsed.origin === window.location.origin;
    } catch (_) {
      return false;
    }
  }

  async function loadAndReplaceFromUrl(url, targetSelector, options) {
    if (!url || !targetSelector) return false;
    const resp = await fetch(url, {
      credentials: "same-origin",
      headers: wantsJsonHeaders({ "Accept": "text/html,application/xhtml+xml" }),
    });
    if (!resp.ok) return false;
    const contentType = String(resp.headers.get("content-type") || "").toLowerCase();
    const text = await resp.text();
    if (contentType.includes("application/json")) {
      const payload = parseJsonSafe(text) || {};
      const jsonHtml = resolveTargetHtmlFromAsyncPayload(payload, targetSelector);
      if (typeof jsonHtml !== "string") return false;
      return replaceTargetHtml(targetSelector, jsonHtml, options || {});
    }
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
    const contentType = String(resp.headers.get("content-type") || "").toLowerCase();
    const text = await resp.text();
    if (contentType.includes("application/json")) {
      const payload = parseJsonSafe(text) || {};
      let anyAppliedFromJson = false;
      ops.forEach((op) => {
        const selector = normalizeSelector(op && op.target);
        if (!selector) return;
        const jsonHtml = resolveTargetHtmlFromAsyncPayload(payload, selector);
        if (typeof jsonHtml !== "string") return;
        const replaced = replaceTargetHtml(selector, jsonHtml, {
          preserveScroll: !!op.preserveScroll,
          preserveOpenCollapses: !!(options && options.preserveOpenCollapses),
          focusRowId: options && options.focusRowId,
          flashRow: options ? options.flashRow !== false : true,
        });
        anyAppliedFromJson = anyAppliedFromJson || replaced;
      });
      return anyAppliedFromJson;
    }
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

  function resolveTargetHtmlFromAsyncPayload(payload, targetSelector) {
    if (!payload || !targetSelector) return null;
    const target = normalizeSelector(targetSelector);
    if (!target) return null;

    const legacyTarget = normalizeSelector(payload.update_target);
    if (legacyTarget && legacyTarget === target && typeof payload.replace_html === "string") {
      return payload.replace_html;
    }

    const entries = Array.isArray(payload.update_targets) ? payload.update_targets : [];
    for (const entry of entries) {
      if (!entry || typeof entry !== "object") continue;
      const entryTarget = normalizeSelector(entry.target || entry.update_target);
      if (!entryTarget || entryTarget !== target) continue;
      if (typeof entry.replace_html === "string") return entry.replace_html;
    }

    return null;
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

  function invalidateSnapshotsFromPayload(payload) {
    const entries = Array.isArray(payload && payload.invalidate_snapshots) ? payload.invalidate_snapshots : [];
    if (!entries.length) return 0;
    if (window.AdminNav && typeof window.AdminNav.invalidateSnapshots === "function") {
      return window.AdminNav.invalidateSnapshots(entries);
    }
    return 0;
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
    invalidateSnapshotsFromPayload(payload || {});

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
    pushHistory,
    historyFormSelector,
    historyMode,
    allowCached,
  }) {
    const container = busyContainer || sourceEl;
    if (!container || container.dataset[BUSY_KEY] === "1") {
      clearGlobalLoaders();
      return false;
    }
    const requestId = ++globalRequestSeq;
    registerRequestClaim(updateTarget, requestId);

    const normalizedTarget = normalizeSelector(updateTarget);
    const isGetForTarget = method === "GET" && normalizedTarget;
    if (isGetForTarget && allowCached) {
      const cached = getTargetCache(url, normalizedTarget);
      if (cached && typeof cached.html === "string") {
        replaceTargetHtml(normalizedTarget, cached.html, { preserveScroll });
        return true;
      }
    }
    let requestController = null;
    if (method === "GET" && normalizedTarget && window.AbortController) {
      const prevController = activeRequestControllerByTarget.get(normalizedTarget);
      if (prevController && typeof prevController.abort === "function") {
        prevController.abort();
      }
      requestController = new AbortController();
      activeRequestControllerByTarget.set(normalizedTarget, requestController);
    }

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
        signal: requestController ? requestController.signal : undefined,
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
        if (ok && method === "GET" && pushHistory && historyMode !== "pop") {
          tryPushHistoryState({
            adminAsync: true,
            url: String(url || ""),
            updateTarget: normalizedTarget,
            busyContainerSelector: container && container.id ? `#${container.id}` : "",
            formSelector: historyFormSelector || "",
          }, String(url || ""));
        }
        if (ok && isGetForTarget) {
          const liveTarget = document.querySelector(normalizedTarget);
          if (liveTarget) setTargetCache(url, normalizedTarget, String(liveTarget.innerHTML || ""));
        }
        return ok;
      }

      if (resp.redirected && resp.ok) {
        if (isAuthRedirectUrl(resp.url)) {
          showToast("Tu sesión de seguridad expiró. Recarga la página e intenta de nuevo.", "warning");
          if (resp.url) window.location.assign(resp.url);
          return false;
        }
        if (isSameOriginUrl(resp.url)) {
          if (updateTarget) {
            const parser = new DOMParser();
            const doc = parser.parseFromString(parsed.data, "text/html");
            const node = doc.querySelector(updateTarget);
            if (node) {
              replaceTargetHtml(updateTarget, node.innerHTML, { preserveScroll });
              closeEnclosingModal(sourceEl);
              return true;
            }
          }
          window.location.assign(resp.url);
          return true;
        }
      }

      if (resp.ok && updateTarget) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(parsed.data, "text/html");
        const node = doc.querySelector(updateTarget);
        if (node) {
          replaceTargetHtml(updateTarget, node.innerHTML, { preserveScroll });
          if (isGetForTarget) {
            setTargetCache(url, normalizedTarget, String(node.innerHTML || ""));
          }
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
      if (_err && _err.name === "AbortError") {
        return false;
      }
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
      if (requestController && normalizedTarget) {
        const current = activeRequestControllerByTarget.get(normalizedTarget);
        if (current === requestController) {
          activeRequestControllerByTarget.delete(normalizedTarget);
        }
      }
      setBusyState(container, submitter, false);
      clearGlobalLoaders();
    }
  }

  function buildFormRequest(form, submitter) {
    const method = String(form.getAttribute("method") || "POST").toUpperCase();
    const submitterAction = submitter && submitter.getAttribute ? (submitter.getAttribute("formaction") || "") : "";
    const action = submitterAction || form.getAttribute("action") || window.location.href;
    const asyncAction = (form.getAttribute("data-async-action") || "").trim();
    const requestUrl = asyncAction || action;
    const submitterMethod = submitter && submitter.getAttribute ? (submitter.getAttribute("formmethod") || "") : "";
    const requestMethod = String(submitterMethod || method || "POST").toUpperCase();
    const noLoader = form.hasAttribute("data-no-loader");
    const updateTarget = (form.getAttribute("data-async-target") || "").trim();

    if (requestMethod === "GET") {
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
      method: requestMethod,
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
    const submitter = ev.submitter || lastSubmitterByForm.get(form) || form.querySelector('button[type="submit"],input[type="submit"]');
    if (lastSubmitterByForm.has(form)) {
      lastSubmitterByForm.delete(form);
    }
    const req = buildFormRequest(form, submitter);
    const containerSel = (form.getAttribute("data-async-busy-container") || "").trim();
    const busyContainer = containerSel ? document.querySelector(containerSel) : form;
    const preserveScroll = form.getAttribute("data-async-preserve-scroll") === "true";
    const pushHistory = form.getAttribute("data-async-history") === "true";
    const formSelector = form.id ? `#${form.id}` : "";

    const result = await handleAsyncRequest({
      ...req,
      sourceEl: form,
      busyContainer,
      submitter,
      preserveScroll,
      pushHistory,
      historyFormSelector: formSelector,
      historyMode: "push",
      allowCached: false,
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
    const pushHistory = link.getAttribute("data-async-history") === "true";
    const historyFormSelector = (link.getAttribute("data-async-history-form") || "").trim();

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
      pushHistory,
      historyFormSelector,
      historyMode: "push",
      allowCached: true,
    });
  }

  function trackFormSubmitterClick(ev) {
    const target = ev && ev.target ? ev.target : null;
    if (!target || !target.closest) return;
    const submitter = target.closest("button[type='submit'],input[type='submit']");
    if (!submitter || !submitter.closest) return;
    const form = submitter.closest("form[data-admin-async-form]");
    if (!form) return;
    lastSubmitterByForm.set(form, submitter);
  }

  async function onHistoryPopState(ev) {
    const state = ev && ev.state ? ev.state : null;
    if (!state || state.adminAsync !== true) return;
    if (!window.fetch) return;
    const target = normalizeSelector(state.updateTarget);
    if (!target) return;
    const busyContainerSelector = String(state.busyContainerSelector || "").trim();
    const busyContainer = busyContainerSelector ? document.querySelector(busyContainerSelector) : document.querySelector(target);
    const formSelector = String(state.formSelector || "").trim();
    const form = formSelector ? document.querySelector(formSelector) : null;
    syncFormFieldsFromUrl(form, state.url);
    await handleAsyncRequest({
      url: String(state.url || window.location.href),
      method: "GET",
      body: null,
      sourceEl: form || (busyContainer || document.body),
      busyContainer: busyContainer || (form || document.body),
      submitter: null,
      updateTarget: target,
      noLoader: true,
      headers: { "X-CSRFToken": getCSRFToken(form) },
      preserveScroll: true,
      pushHistory: false,
      historyFormSelector: formSelector,
      historyMode: "pop",
      allowCached: true,
    });
  }

  function bindSecondaryListeners() {
    if (secondaryBound) return;
    secondaryBound = true;
    document.addEventListener("click", trackFormSubmitterClick, true);
    document.addEventListener("submit", onSubmit, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("input", onAsyncDebouncedInput, true);
    window.addEventListener("popstate", onHistoryPopState);
    document.addEventListener("input", onReemplazoSearchInput, true);
    document.addEventListener("keydown", onReemplazoSearchKeydown, true);
    document.addEventListener("click", onReemplazoSearchTrigger, true);
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
    document.addEventListener("admin:content-updated", (ev) => {
      const container = ev && ev.detail ? ev.detail.container : null;
      syncCollapseToggleLabels(container || document);
      syncRegistrarPagoManualFields(container || document);
    });
    document.addEventListener("change", onRegistrarPagoModeChange, true);
  }

  function resolveRegistrarPagoRoot(input) {
    if (!input || !input.closest) return null;
    return input.closest("#registrarPagoAsyncRegion") || input.closest("#registrarPagoAsyncScope") || document;
  }

  function syncRegistrarPagoManualFields(root) {
    const host = root && root.querySelectorAll ? root : document;
    const groups = host.querySelectorAll ? host.querySelectorAll("#manual-payment-fields") : [];
    groups.forEach((panel) => {
      const scope = panel.closest("form") || panel.parentElement || document;
      const selected = scope.querySelector("input[name='payment_mode']:checked");
      const isManual = Boolean(selected && String(selected.value || "").toLowerCase() === "manual");
      panel.classList.toggle("show", isManual);
    });
  }

  function onRegistrarPagoModeChange(ev) {
    const input = ev && ev.target ? ev.target : null;
    if (!input || String(input.name || "") !== "payment_mode") return;
    const root = resolveRegistrarPagoRoot(input);
    syncRegistrarPagoManualFields(root || document);
  }

  function formatPlanMoney(value) {
    return "RD$ " + Number(value || 0).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function renderGestionarPlanLoading() {
    return [
      '<div class="p-4">',
      '  <div class="d-flex align-items-center gap-2 text-muted">',
      '    <span class="spinner-border spinner-border-sm" aria-hidden="true"></span>',
      '    <span>Cargando formulario de plan...</span>',
      "  </div>",
      "</div>",
    ].join("");
  }

  function renderGestionarPlanError(message, retryLabel) {
    const safeMessage = String(message || "No se pudo cargar el formulario de plan.").replace(/[&<>\"']/g, (ch) => {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[ch] || ch;
    });
    const safeRetry = String(retryLabel || "Reintentar").replace(/[&<>\"']/g, (ch) => {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[ch] || ch;
    });
    return [
      '<div class="p-4">',
      '  <div class="alert alert-danger mb-0" role="alert" aria-live="polite">',
      '    <div class="fw-semibold">No se pudo cargar el plan.</div>',
      `    <div class="small mt-1">${safeMessage}</div>`,
      `    <button type="button" class="btn btn-outline-danger btn-sm mt-3" data-gestionar-plan-modal-retry="1">${safeRetry}</button>`,
      "  </div>",
      "</div>",
    ].join("");
  }

  function getGestionarPlanModal(trigger) {
    if (!trigger || !trigger.closest) return null;
    const selector = (trigger.getAttribute("data-gestionar-plan-modal") || "").trim() || "#gestionarPlanModal";
    return document.querySelector(selector);
  }

  function getGestionarPlanUrl(trigger) {
    if (!trigger || !trigger.getAttribute) return "";
    return String(trigger.getAttribute("href") || trigger.getAttribute("data-gestionar-plan-url") || "").trim();
  }

  function syncGestionarPlanSummary(root) {
    const host = root && root.querySelectorAll ? root : document;
    const forms = host.querySelectorAll("#planForm");
    forms.forEach((form) => {
      if (!form || form.dataset.planSummaryReady === "1") return;
      const planSelect = form.querySelector("#tipo_plan");
      const totalEl = form.querySelector("#plan-summary-total");
      const depositEl = form.querySelector("#plan-summary-deposit");
      const balanceEl = form.querySelector("#plan-summary-balance");
      const abonoInput = form.querySelector("#abono_auto");
      if (!planSelect || !totalEl || !depositEl || !balanceEl || !abonoInput) return;

      function syncSummaryFromPlan() {
        const selected = planSelect.options[planSelect.selectedIndex];
        const total = Number((selected && selected.dataset && selected.dataset.price) || 0);
        const deposit = total * 0.5;
        const balance = total - deposit;
        totalEl.textContent = formatPlanMoney(total);
        depositEl.textContent = formatPlanMoney(deposit);
        balanceEl.textContent = formatPlanMoney(balance);
        abonoInput.value = deposit.toFixed(2);
      }

      planSelect.addEventListener("change", syncSummaryFromPlan);
      syncSummaryFromPlan();
      form.dataset.planSummaryReady = "1";
    });
  }

  function ensureGestionarPlanModalShown(modal) {
    if (!modal) return;
    try {
      if (window.bootstrap && window.bootstrap.Modal) {
        window.bootstrap.Modal.getOrCreateInstance(modal).show();
        return;
      }
    } catch (_) {}
    modal.classList.add("show");
    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");
  }

  async function openGestionarPlanModal(trigger) {
    const modal = getGestionarPlanModal(trigger);
    const url = getGestionarPlanUrl(trigger);
    if (!modal || !url) {
      if (url) window.location.assign(url);
      return false;
    }

    const region = modal.querySelector("#gestionarPlanAsyncRegion");
    if (!region) {
      window.location.assign(url);
      return false;
    }

    modal.dataset.gestionarPlanUrl = url;
    region.innerHTML = renderGestionarPlanLoading();
    ensureGestionarPlanModalShown(modal);

    if (!window.AdminAsync || typeof window.AdminAsync.request !== "function") {
      window.location.assign(url);
      return false;
    }

    const result = await window.AdminAsync.request({
      url,
      method: "GET",
      body: null,
      sourceEl: trigger,
      busyContainer: modal.querySelector(".modal-body") || modal,
      submitter: trigger,
      updateTarget: "#gestionarPlanAsyncRegion",
      noLoader: true,
      headers: {},
      preserveScroll: false,
      pushHistory: false,
      historyFormSelector: "",
      historyMode: "push",
      allowCached: true,
    });

    if (result === false || result === null) {
      const meta = window.AdminAsync.getLastResponseMeta ? window.AdminAsync.getLastResponseMeta() : null;
      const fallbackMessage = meta && meta.message ? meta.message : "Intenta nuevamente.";
      region.innerHTML = renderGestionarPlanError(fallbackMessage, "Reintentar");
      modal.dataset.gestionarPlanLoadState = "error";
      return false;
    }

    modal.dataset.gestionarPlanLoadState = "ready";
    return true;
  }

  function handleGestionarPlanTriggerClick(ev) {
    const trigger = ev && ev.target ? ev.target.closest("[data-gestionar-plan-modal-trigger]") : null;
    if (!trigger) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || (typeof ev.button === "number" && ev.button !== 0)) {
      return;
    }
    ev.preventDefault();
    openGestionarPlanModal(trigger);
  }

  function handleGestionarPlanRetryClick(ev) {
    const btn = ev && ev.target ? ev.target.closest("[data-gestionar-plan-modal-retry='1']") : null;
    if (!btn) return;
    ev.preventDefault();
    const modal = btn.closest("#gestionarPlanModal");
    if (!modal) return;
    const url = String(modal.dataset.gestionarPlanUrl || "").trim();
    if (!url) return;
    const pseudoTrigger = {
      getAttribute(name) {
        const key = String(name || "");
        if (key === "href" || key === "data-gestionar-plan-url") return url;
        if (key === "data-gestionar-plan-modal") return "#gestionarPlanModal";
        return null;
      },
      closest(selector) {
        if (selector === "#gestionarPlanModal") return modal;
        return null;
      },
    };
    openGestionarPlanModal(pseudoTrigger);
  }

  function bindGestionarPlanRuntime() {
    if (gestionarPlanBound) return;
    gestionarPlanBound = true;

    document.addEventListener("click", handleGestionarPlanTriggerClick, true);
    document.addEventListener("click", handleGestionarPlanRetryClick, true);
    document.addEventListener("admin:content-updated", (ev) => {
      const container = ev && ev.detail ? ev.detail.container : null;
      syncGestionarPlanSummary(container || document);
    });
    document.addEventListener("admin:navigation-complete", (ev) => {
      const detail = ev && ev.detail ? ev.detail : {};
      syncGestionarPlanSummary(detail.viewport || document);
    });

    syncGestionarPlanSummary(document);
  }

  function init() {
    bindSecondaryListeners();
    bindCandidatasOperativoIndexRuntime();
    bindGestionarPlanRuntime();
    syncCollapseToggleLabels(document);
    syncRegistrarPagoManualFields(document);
    bindManagedModalGuards();
  }

  window.AdminAsync = {
    init,
    request: handleAsyncRequest,
    replaceTargetHtml,
    getLastResponseMeta: () => (lastResponseMeta ? { ...lastResponseMeta } : null),
  };

  if (document.readyState === "loading") {
    bindManagedModalGuards();
    document.addEventListener("DOMContentLoaded", () => scheduleIdle(init, 900), { once: true });
  } else {
    bindManagedModalGuards();
    scheduleIdle(init, 900);
  }
})();
