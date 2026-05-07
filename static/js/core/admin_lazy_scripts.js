// static/js/core/admin_lazy_scripts.js
// Carga diferida de scripts admin cuando el DOM realmente los necesita.
(function () {
  "use strict";

  if (window.AdminLazyScripts) return;

  const loaded = new Set();
  const pending = new Map();
  const scheduleIdle = (cb, timeout = 900) => {
    if (typeof window.requestIdleCallback === "function") {
      return window.requestIdleCallback(cb, { timeout });
    }
    return window.setTimeout(cb, 80);
  };
  let observer = null;

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

  function evaluate(scope) {
    const root = scope && scope.querySelector ? scope : document;
    const scriptSolicitud = String(document.body?.getAttribute("data-lazy-script-solicitud-detail-ui") || "").trim();
    const scriptLiveRefresh = String(document.body?.getAttribute("data-lazy-script-live-refresh") || "").trim();

    if (scriptSolicitud && hasSolicitudDetailMarkers(root)) {
      loadScriptOnce(scriptSolicitud).catch(function () {});
    }
    if (scriptLiveRefresh && hasLiveRefreshMarkers(root)) {
      loadScriptOnce(scriptLiveRefresh).catch(function () {});
    }
  }

  function boot() {
    scheduleIdle(() => evaluate(document), 1400);

    if (typeof window.IntersectionObserver === "function") {
      observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          evaluate(entry.target);
          observer.unobserve(entry.target);
        });
      }, { rootMargin: "180px 0px" });
      document.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
    }

    document.addEventListener("admin:content-updated", function (ev) {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      evaluate(detail.container || document);
      if (observer && detail.container && detail.container.querySelectorAll) {
        detail.container.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
      }
    });

    document.addEventListener("admin:navigation-complete", function (ev) {
      const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
      const scope = detail.viewport || document;
      evaluate(scope);
      if (observer && scope && scope.querySelectorAll) {
        scope.querySelectorAll("[data-admin-lazy-fragment-url], [data-live-refresh='1']").forEach((node) => observer.observe(node));
      }
    });
    window.addEventListener("beforeunload", function () {
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
