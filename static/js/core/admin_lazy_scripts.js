// static/js/core/admin_lazy_scripts.js
// Carga diferida de scripts admin cuando el DOM realmente los necesita.
(function () {
  "use strict";

  if (window.AdminLazyScripts) return;

  const loaded = new Set();
  const pending = new Map();
  const lazyFetches = new Map();
  const scheduleIdle = (cb, timeout = 900) => {
    if (typeof window.requestIdleCallback === "function") {
      return window.requestIdleCallback(cb, { timeout });
    }
    return window.setTimeout(cb, 80);
  };
  let observer = null;
  const observerOptions = { rootMargin: "600px 0px" };

  function scriptAlreadyInDom(src) {
    const scripts = document.querySelectorAll("script[src]");
    for (const node of scripts) {
      const current = String(node.getAttribute("src") || "");
      if (!current) continue;
      if (current === src) return true;
      if (current.endsWith(src)) return true;
    }
    return false;
  }

  function loadScriptOnce(src) {
    const url = String(src || "").trim();
    if (!url) return Promise.resolve(false);
    if (loaded.has(url) || scriptAlreadyInDom(url)) {
      loaded.add(url);
      return Promise.resolve(false);
    }
    if (pending.has(url)) return pending.get(url);

    const promise = new Promise((resolve, reject) => {
      const node = document.createElement("script");
      node.src = url;
      node.defer = true;
      node.onload = function () {
        loaded.add(url);
        pending.delete(url);
        resolve(true);
      };
      node.onerror = function () {
        pending.delete(url);
        reject(new Error("LAZY_LOAD_FAILED:" + url));
      };
      document.head.appendChild(node);
    });
    pending.set(url, promise);
    return promise;
  }

  function hasSolicitudDetailMarkers(scope) {
    const root = scope && scope.querySelector ? scope : document;
    if (root.querySelector("#resumenCliente")) return true;
    if (root.querySelector(".copy-btn-interno")) return true;
    if (root.querySelector(".js-copy-contract-link")) return true;
    return false;
  }

  function hasLiveRefreshMarkers(scope) {
    const root = scope && scope.querySelector ? scope : document;
    return !!root.querySelector("[data-live-refresh='1']");
  }

  function hasClientesListMarkers(scope) {
    const root = scope && scope.querySelector ? scope : document;
    return !!(root.querySelector("#clientesSearchForm") || root.querySelector("#deleteClienteFromListModalShared"));
  }

  function getLazyRegionState(region, createIfMissing) {
    if (!region) return null;
    let state = lazyFetches.get(region) || region.__adminLazyState || null;
    if (!state && createIfMissing) {
      state = {};
      lazyFetches.set(region, state);
      region.__adminLazyState = state;
    }
    return state;
  }

  function clearLazyRegionState(region) {
    if (!region) return;
    const state = lazyFetches.get(region) || region.__adminLazyState || null;
    if (state && state.controller && typeof state.controller.abort === "function" && !state.controller.signal?.aborted) {
      state.controller.abort();
    }
    lazyFetches.delete(region);
    if (region.__adminLazyState) {
      region.__adminLazyState = null;
    }
    if (region.dataset) {
      region.dataset.lazyInflight = "0";
    }
  }

  function abortLazyFragments(scope) {
    const root = scope && scope.querySelector ? scope : null;
    for (const [region, state] of lazyFetches.entries()) {
      if (!region) continue;
      if (root && region.isConnected && typeof root.contains === "function" && root.contains(region)) {
        continue;
      }
      if (state && state.controller && typeof state.controller.abort === "function" && !state.controller.signal?.aborted) {
        state.controller.abort();
      }
      lazyFetches.delete(region);
      if (region.__adminLazyState) {
        region.__adminLazyState = null;
      }
      if (region.dataset) {
        region.dataset.lazyInflight = "0";
      }
    }
  }

  function getLazyRegion(node) {
    const root = node && node.closest ? node : null;
    if (!root) return null;
    return root.closest("[data-admin-lazy-fragment-url]");
  }

  function setLazyRegionStatus(region, state, message) {
    if (!region) return;
    const nextState = String(state || "idle");
    region.dataset.lazyState = nextState;
    region.setAttribute("aria-busy", nextState === "loading" ? "true" : "false");
    const status = region.querySelector("[data-admin-lazy-status]");
    if (status && typeof message === "string" && message) {
      status.textContent = message;
    }
  }

  function renderLazyRegionError(region, message) {
    if (!region) return;
    const text = String(message || "No se pudo cargar esta sección.");
    const retryLabel = String(region.getAttribute("data-admin-lazy-retry-label") || "Reintentar");
    region.innerHTML = [
      '<div class="alert alert-warning py-2 px-3 mb-0" role="alert" aria-live="polite">',
      `<div class="small mb-2">${text}</div>`,
      `<button type="button" class="btn btn-outline-secondary btn-sm" data-admin-lazy-retry>${retryLabel}</button>`,
      "</div>",
    ].join("");
    setLazyRegionStatus(region, "error", text);
  }

  async function loadLazyFragment(region, options) {
    const target = region && region.closest ? region : null;
    if (!target || !target.isConnected) return false;
    const force = !!(options && options.force);
    const url = String(target.getAttribute("data-admin-lazy-fragment-url") || "").trim();
    if (!url) return false;
    if (target.dataset.lazyInflight === "1") return false;
    if (!force && (target.dataset.lazyLoaded === "1" || target.dataset.lazyState === "error")) return false;

    target.dataset.lazyInflight = "1";
    setLazyRegionStatus(target, "loading", String(target.getAttribute("data-admin-lazy-loading-label") || "Cargando..."));
    const state = getLazyRegionState(target, true);
    const controller = window.AbortController ? new window.AbortController() : null;
    if (state) {
      state.controller = controller;
    }
    target.__adminLazyAbortController = controller;
    try {
      const resp = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "text/html,*/*",
        },
        signal: controller ? controller.signal : undefined,
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const html = await resp.text();
      const selector = "#" + target.id;
      let replaced = false;
      if (window.AdminAsync && typeof window.AdminAsync.replaceTargetHtml === "function") {
        replaced = window.AdminAsync.replaceTargetHtml(selector, html, { preserveScroll: true });
      } else {
        target.innerHTML = html;
        document.dispatchEvent(new CustomEvent("admin:content-updated", {
          detail: { targetSelector: selector, container: target },
        }));
        replaced = true;
      }
      if (replaced) {
        target.dataset.lazyLoaded = "1";
        target.dataset.lazyState = "loaded";
        target.removeAttribute("data-admin-lazy-fragment-url");
      }
      return replaced;
    } catch (err) {
      if (err && (err.name === "AbortError" || String(err.message || "").indexOf("AbortError") >= 0)) {
        target.dataset.lazyLoaded = "0";
        target.dataset.lazyState = "idle";
        return false;
      }
      target.dataset.lazyLoaded = "0";
      renderLazyRegionError(target, String(target.getAttribute("data-admin-lazy-error-label") || "No se pudo cargar esta sección. Reintenta."));
      return false;
    } finally {
      target.dataset.lazyInflight = "0";
      if (state) {
        state.controller = null;
      }
      target.__adminLazyAbortController = null;
      lazyFetches.delete(target);
    }
  }

  function bindLazyFragments(scope) {
    const root = scope && scope.querySelector ? scope : document;
    if (observer) {
      root.querySelectorAll("[data-admin-lazy-fragment-url]").forEach((region) => {
        if (!region.id) return;
        if (region.dataset.lazyLoaded === "1" || region.dataset.lazyInflight === "1" || region.dataset.lazyObserved === "1") return;
        region.dataset.lazyObserved = "1";
        observer.observe(region);
      });
      return;
    }
    root.querySelectorAll("[data-admin-lazy-fragment-url]").forEach((region) => {
      if (!region.id) return;
      if (region.dataset.lazyLoaded === "1" || region.dataset.lazyInflight === "1") return;
      scheduleIdle(function () {
        loadLazyFragment(region).catch(function () {});
      }, 1400);
    });
  }

  function buildClientesListCleanupCodeHelp(rawCode) {
    const compact = String(rawCode || "").replace(/[\s,.]/g, "");
    if (/^\d+$/.test(compact)) {
      const visible = Number(compact).toLocaleString("en-US");
      if (visible !== compact) {
        return `Código requerido: ${visible} o el código exacto visible. También se acepta ${compact}.`;
      }
      return `Código requerido: ${visible} o el código exacto visible.`;
    }
    return `Código requerido: ${String(rawCode || "—")} o el código exacto visible.`;
  }

  function bindClientesListDeleteModal(scope) {
    const root = scope && scope.querySelector ? scope : document;
    const modal = root.querySelector("#deleteClienteFromListModalShared") || document.getElementById("deleteClienteFromListModalShared");
    if (!modal) return;
    if (modal.dataset.clientesListModalBound === "1") return;
    modal.dataset.clientesListModalBound = "1";

    if (modal.parentElement !== document.body) {
      document.body.appendChild(modal);
    }

    const form = modal.querySelector("#deleteClienteFromListSharedForm");
    const input = modal.querySelector("#delete_cliente_list_confirm_input_shared");
    const cleanupInput = modal.querySelector("#delete_cliente_list_full_cleanup_shared");
    const cleanupCodeInput = modal.querySelector("#delete_cliente_list_cleanup_code_shared");
    const nextInput = modal.querySelector("#deleteClienteFromListSharedNext");
    const nameNode = modal.querySelector("#deleteClienteFromListSharedNombre");
    const codeNode = modal.querySelector("#deleteClienteFromListSharedCodigo");
    const codeHelpNode = modal.querySelector("#deleteClienteFromListSharedCodeHelp");
    const segBackdrop = document.getElementById("segCandidatasBackdrop");
    const segDrawer = document.getElementById("segCandidatasDrawer");
    const segBodyOpenClass = "seg-drawer-open";

    modal.addEventListener("show.bs.modal", function (ev) {
      const trigger = ev && ev.relatedTarget ? ev.relatedTarget : null;
      if (!trigger) return;

      const actionUrl = String(trigger.getAttribute("data-delete-action-url") || "").trim();
      const nextUrl = String(trigger.getAttribute("data-delete-next-url") || "").trim();
      const clienteNombre = String(trigger.getAttribute("data-delete-cliente-nombre") || "—");
      const clienteCodigo = String(trigger.getAttribute("data-delete-cliente-codigo") || "—");

      if (form && actionUrl) form.setAttribute("action", actionUrl);
      if (nextInput && nextUrl) nextInput.value = nextUrl;
      if (nameNode) nameNode.textContent = clienteNombre;
      if (codeNode) codeNode.textContent = clienteCodigo;
      if (codeHelpNode) codeHelpNode.textContent = buildClientesListCleanupCodeHelp(clienteCodigo);
      if (input) input.value = "";
      if (cleanupInput) cleanupInput.checked = false;
      if (cleanupCodeInput) cleanupCodeInput.value = "";

      if (segBackdrop && segBackdrop.hidden === false) segBackdrop.hidden = true;
      if (segDrawer && segDrawer.hidden === false) segDrawer.hidden = true;
      if (document.body) document.body.classList.remove(segBodyOpenClass);

      modal.style.setProperty("z-index", "2000");
      window.setTimeout(function () {
        const backdrops = Array.prototype.slice.call(document.querySelectorAll(".modal-backdrop"));
        if (!backdrops.length) return;
        const activeBackdrop = backdrops[backdrops.length - 1];
        if (!activeBackdrop) return;
        activeBackdrop.style.setProperty("z-index", "1990");
        activeBackdrop.style.setProperty("pointer-events", "auto");
      }, 0);
    });

    modal.addEventListener("shown.bs.modal", function () {
      if (input) input.focus();
    });

    modal.addEventListener("click", function (ev) {
      const dismissBtn = ev && ev.target && ev.target.closest
        ? ev.target.closest('[data-bs-dismiss="modal"]')
        : null;
      if (!dismissBtn) return;
      if (!(window.bootstrap && window.bootstrap.Modal)) return;
      ev.preventDefault();
      const instance = window.bootstrap.Modal.getInstance(modal) || new window.bootstrap.Modal(modal);
      instance.hide();
    });
  }

  function evaluate(scope) {
    const root = scope && scope.querySelector ? scope : document;
    const scriptSolicitud = String(document.body?.getAttribute("data-lazy-script-solicitud-detail-ui") || "").trim();
    const scriptLiveRefresh = String(document.body?.getAttribute("data-lazy-script-live-refresh") || "").trim();
    const candidataScriptNode = root.querySelector("[data-lazy-script-candidata-detail-ui]");
    const scriptCandidataDetail = candidataScriptNode
      ? String(candidataScriptNode.getAttribute("data-lazy-script-candidata-detail-ui") || "").trim()
      : "";

    if (scriptSolicitud && hasSolicitudDetailMarkers(root)) {
      loadScriptOnce(scriptSolicitud).catch(function () {});
    }
    if (scriptLiveRefresh && hasLiveRefreshMarkers(root)) {
      loadScriptOnce(scriptLiveRefresh).catch(function () {});
    }
    if (scriptCandidataDetail && root.querySelector("[data-candidata-center]")) {
      loadScriptOnce(scriptCandidataDetail).catch(function () {});
    }
    if (hasClientesListMarkers(root)) {
      bindClientesListDeleteModal(root);
    }
    bindLazyFragments(root);
  }

  function boot() {
    bindClientesListDeleteModal(document);
    bindLazyFragments(document);
    evaluate(document);
    scheduleIdle(() => evaluate(document), 1400);

    if (!document.__adminLazyRetryBound) {
      document.__adminLazyRetryBound = true;
      document.addEventListener("click", function (ev) {
        const btn = ev && ev.target && ev.target.closest ? ev.target.closest("[data-admin-lazy-retry]") : null;
        if (!btn) return;
        const region = getLazyRegion(btn);
        if (!region) return;
        ev.preventDefault();
        loadLazyFragment(region, { force: true }).catch(function () {});
      });
    }

    if (typeof window.IntersectionObserver === "function") {
      observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          if (observer && entry.target) {
            observer.unobserve(entry.target);
          }
          loadLazyFragment(entry.target).catch(function () {});
        });
      }, observerOptions);
      document.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
    }

    document.addEventListener("admin:content-updated", function (ev) {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      abortLazyFragments(detail.container || document);
      evaluate(detail.container || document);
      if (observer && detail.container && detail.container.querySelectorAll) {
        detail.container.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
      }
    });

    document.addEventListener("admin:navigation-complete", function (ev) {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      const scope = detail.viewport || document;
      abortLazyFragments(scope);
      evaluate(scope);
      if (observer && scope && scope.querySelectorAll) {
        scope.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
      }
    });
    window.addEventListener("beforeunload", function () {
      abortLazyFragments(document);
      if (observer) observer.disconnect();
    });
  }

  window.AdminLazyScripts = {
    evaluate,
    loadScriptOnce,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
