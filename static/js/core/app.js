/* static/js/core/app.js
   Boot principal: aquí se inicializa todo lo global.
*/

(function () {
  "use strict";

  // Helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ====== Anti-freeze en DESCARGAS / target=_blank ======
  // Problema típico: el loader global se muestra al hacer click, pero en descargas
  // (o enlaces que abren en otra pestaña) no hay navegación y el loader se queda pegado.
  function initNoLoaderOnDownloads() {
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

  // ====== Tema (ROBUSTO) ======
  // IMPORTANTE:
  // - La lógica principal del tema vive en: static/js/ui/theme.js
  // - Este core/app.js SOLO:
  //   1) asegura sincronía html/body
  //   2) asegura el icono del botón
  //   3) asegura que el toggle funcione aunque el botón se re-renderice
  function initTheme() {
    const root = document.documentElement;

    const getTheme = () => {
      const t = root.getAttribute("data-theme") || document.body?.getAttribute("data-theme");
      if (t === "dark" || t === "light") return t;
      try {
        const saved = localStorage.getItem("theme");
        if (saved === "dark" || saved === "light") return saved;
      } catch (e) {}
      return "light";
    };

    const syncHtmlBody = (theme) => {
      const t = theme === "dark" ? "dark" : "light";

      // Anti-loop guard: evita freeze por MutationObserver
      if (window.__themeSyncing) return;
      window.__themeSyncing = true;

      try {
        root.setAttribute("data-theme", t);
        if (document.body) document.body.setAttribute("data-theme", t);

        // Clases de compatibilidad
        root.classList.toggle("light", t === "light");
        root.classList.toggle("dark", t === "dark");
        root.classList.toggle("theme-light", t === "light");
        root.classList.toggle("theme-dark", t === "dark");

        if (document.body) {
          document.body.classList.toggle("light", t === "light");
          document.body.classList.toggle("dark", t === "dark");
          document.body.classList.toggle("theme-light", t === "light");
          document.body.classList.toggle("theme-dark", t === "dark");
        }
      } finally {
        window.__themeSyncing = false;
      }
    };

    const syncMeta = (theme) => {
      const meta = document.querySelector('meta[name="theme-color"]');
      if (!meta) return;
      meta.setAttribute("content", theme === "dark" ? "#0c111b" : "#ffffff");
    };

    const syncBtnIcon = (theme) => {
      const btn = document.getElementById("themeToggle");
      if (!btn) return;
      btn.innerHTML =
        theme === "dark"
          ? '<i class="fa-solid fa-sun"></i>'
          : '<i class="fa-solid fa-moon"></i>';
      btn.setAttribute(
        "aria-label",
        theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"
      );
    };

    // 1) Inicializa estado visual con el tema actual
    const initial = getTheme();
    syncHtmlBody(initial);
    syncMeta(initial);
    syncBtnIcon(initial);

    // 2) Listener ÚNICO y robusto (delegación). Evita "funciona una vez".
    //    Si ui/theme.js existe, lo usamos. Si no, hacemos fallback local.
    if (!window.__themeDelegationBound) {
      window.__themeDelegationBound = true;

      document.addEventListener(
        "click",
        (e) => {
          const target = e.target && (e.target.closest ? e.target.closest("#themeToggle, [data-theme-toggle=\"true\"]") : null);
          if (!target) return;

          // Preferimos el controlador oficial (ui/theme.js)
          if (typeof window.toggleTheme === "function") {
            window.toggleTheme();
            // Espera a que el otro script termine de aplicar el tema
            Promise.resolve().then(() => {
              const t = getTheme();
              syncHtmlBody(t);
              syncMeta(t);
              syncBtnIcon(t);
            });
            return;
          }

          // Fallback (si ui/theme.js no está cargado)
          const current = getTheme();
          const next = current === "dark" ? "light" : "dark";
          try {
            localStorage.setItem("theme", next);
          } catch (err) {}
          syncHtmlBody(next);
          syncMeta(next);
          syncBtnIcon(next);
        },
        true
      );
    }

    // 3) Si alguien cambia data-theme por código, nos mantenemos en sync
    if (!window.__themeMutationObserverBound) {
      window.__themeMutationObserverBound = true;

      const obs = new MutationObserver(() => {
        if (window.__themeSyncing) return;
        const t = getTheme();
        // aquí NO llamamos syncHtmlBody para evitar loops
        syncMeta(t);
        syncBtnIcon(t);
      });

      obs.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    }
  }

  // ====== Toasts (Bootstrap) ======
  function initToasts() {
    if (!window.bootstrap || !bootstrap.Toast) return;
    $$(".toast").forEach((t) => {
      try {
        new bootstrap.Toast(t).show();
      } catch (e) {}
    });
  }

  // ====== Scroll to top ======
  function initScrollTop() {
    const btn = $("#scrollTopBtn");
    if (!btn) return;

    const onScroll = () => {
      if (window.scrollY > 220) btn.classList.add("show");
      else btn.classList.remove("show");
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    btn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    onScroll();
  }

  // ====== Select2 (si existe) ======
  function initSelect2() {
    // OJO: Select2 requiere jQuery.
    if (!window.$ || !$.fn || !$.fn.select2) return;
    try {
      $(".select2").select2({ width: "100%" });
    } catch (e) {}
  }

  // ====== Init general ======
  function initAll() {
    initNoLoaderOnDownloads();
    initTheme();
    initToasts();
    initScrollTop();
    initSelect2();
  }

  // Espera a que el DOM cargue
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();