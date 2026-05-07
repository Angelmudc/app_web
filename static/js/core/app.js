/* static/js/core/app.js
   Boot principal: aquí se inicializa todo lo global.
*/

(function () {
  "use strict";

  // Helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const scheduleIdle = (cb, timeout = 800) => {
    if (typeof window.requestIdleCallback === "function") {
      return window.requestIdleCallback(cb, { timeout });
    }
    return window.setTimeout(cb, 80);
  };

  // ====== Anti-freeze en DESCARGAS / target=_blank ======
  // Problema típico: el loader global se muestra al hacer click, pero en descargas
  // (o enlaces que abren en otra pestaña) no hay navegación y el loader se queda pegado.
  function initNoLoaderOnDownloads() {
    if (window.__noLoaderDownloadsBound) return;
    window.__noLoaderDownloadsBound = true;

    const hideLoader = () => {
      // Si existe el loader global de la app, úsalo primero
      try {
        if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
          window.AppLoader.hideAll();
        }
      } catch (e) {}

      // Intentos “best-effort” sin depender de IDs exactos
      const candidates = [
        "#globalLoader",
        "#pageLoader",
        ".global-loader",
        ".page-loader",
        ".loader-overlay",
        "[data-loader]",
        "[data-loader-overlay]",
      ];

      let el = null;
      for (const sel of candidates) {
        el = document.querySelector(sel);
        if (el) break;
      }

      // Si hay overlay, lo escondemos
      if (el) {
        try {
          el.classList.remove("show", "active", "is-active", "visible");
          el.style.display = "none";
          el.setAttribute("aria-hidden", "true");
        } catch (e) {}
      }

      // Y liberamos el body por si quedó bloqueado
      try {
        document.documentElement.classList.remove("loading");
        document.body && document.body.classList.remove("loading");
        if (document.body) {
          document.body.style.pointerEvents = "";
          document.body.style.overflow = "";
        }
      } catch (e) {}
    };

    const isNoLoaderClick = (target) => {
      if (!target) return false;

      // Helpers
      const isDownloadHref = (href) => {
        if (!href) return false;
        const h = String(href).toLowerCase().trim();
        if (h.startsWith("blob:")) return true;
        if (h.startsWith("data:")) return true;
        if (h.endsWith(".pdf")) return true;
        if (h.endsWith(".xlsx") || h.endsWith(".xls") || h.endsWith(".csv")) return true;
        if (h.endsWith(".doc") || h.endsWith(".docx")) return true;
        if (h.endsWith(".zip") || h.endsWith(".rar")) return true;
        if (h.includes("/descargar") || h.includes("download") || h.includes("export")) return true;
        if (h.includes("pdf=") || h.includes("/pdf")) return true;
        return false;
      };

      const hasNoLoaderMarker = (el) => {
        if (!el || !el.getAttribute) return false;
        const v = (el.getAttribute("data-no-loader") || "").toLowerCase();
        if (v === "1" || v === "true") return true;
        const vd = (el.getAttribute("data-download") || "").toLowerCase();
        if (vd === "1" || vd === "true") return true;
        const va = (el.getAttribute("data-action") || "").toLowerCase();
        if (va === "download") return true;
        return false;
      };

      // Links
      const a = target.closest ? target.closest("a") : null;
      if (a) {
        const href = a.getAttribute("href") || "";
        const targetAttr = (a.getAttribute("target") || "").toLowerCase();

        if (a.hasAttribute("download")) return true;
        if (targetAttr && targetAttr !== "_self") return true;
        if (hasNoLoaderMarker(a)) return true;
        if (a.classList && (a.classList.contains("no-loader") || a.classList.contains("download"))) return true;
        if (isDownloadHref(href)) return true;

        // Si cualquier padre marca no-loader/descarga
        if (a.closest && a.closest('[data-download="true"], [data-action="download"], [data-no-loader], [data-no-loader="true"]')) {
          return true;
        }
      }

      // Botones
      const btn = target.closest ? target.closest("button") : null;
      if (btn) {
        if (hasNoLoaderMarker(btn)) return true;
        if (btn.classList && (btn.classList.contains("no-loader") || btn.classList.contains("download"))) return true;

        // Si cualquier padre marca no-loader/descarga
        if (btn.closest && btn.closest('[data-download="true"], [data-action="download"], [data-no-loader], [data-no-loader="true"]')) {
          return true;
        }
      }

      return false;
    };

    // Capturamos clicks para, si el loader se activa por otro script,
    // apagarlos inmediatamente (en descargas no hay navegación).
    document.addEventListener(
      "click",
      (e) => {
        const t = e.target;
        if (!isNoLoaderClick(t)) return;

        // Apaga el loader enseguida y otra vez al próximo tick
        hideLoader();
        setTimeout(hideLoader, 0);
        setTimeout(hideLoader, 120);
        setTimeout(hideLoader, 600);
        setTimeout(hideLoader, 1400);
      },
      true
    );

    // Si el loader se prendió por un submit que dispara descarga, igual lo apagamos
    document.addEventListener(
      "submit",
      (e) => {
        const form = e.target;
        if (!form) return;

        // Si el submitter (botón que se presionó) es una descarga, mata loader
        const submitter = e.submitter;
        if (submitter && isNoLoaderClick(submitter)) {
          hideLoader();
          setTimeout(hideLoader, 0);
          setTimeout(hideLoader, 120);
          setTimeout(hideLoader, 600);
          setTimeout(hideLoader, 1400);
          return;
        }

        // Si el form tiene data-no-loader / data-download, respetar
        const v = (form.getAttribute && (form.getAttribute("data-no-loader") || form.getAttribute("data-download"))) || "";
        const vv = String(v).toLowerCase();
        if (vv === "1" || vv === "true") {
          hideLoader();
          setTimeout(hideLoader, 0);
          setTimeout(hideLoader, 120);
          setTimeout(hideLoader, 600);
          setTimeout(hideLoader, 1400);
        }
      },
      true
    );
  }

  // ====== Select2 (si existe) ======
  function initSelect2(root = document) {
    if (!(root && root.querySelector && root.querySelector(".select2"))) return;
    // OJO: Select2 requiere jQuery.
    if (!window.$ || !$.fn || !$.fn.select2) return;
    try {
      $$(".select2", root).forEach((el) => {
        if (el.dataset.select2Bound === "1") return;
        window.$(el).select2({ width: "100%" });
        el.dataset.select2Bound = "1";
      });
    } catch (e) {}
  }

  // ====== Init general ======
  function initAll(root = document) {
    initNoLoaderOnDownloads();
    scheduleIdle(() => initSelect2(root), 1200);
  }

  document.addEventListener("admin:navigation-complete", (ev) => {
    const root = ev?.detail?.viewport || document;
    initAll(root);
  });

  window.AppCore = {
    initAll,
  };

  // Espera a que el DOM cargue
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
