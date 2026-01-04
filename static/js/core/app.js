/* static/js/core/app.js
   Boot principal: aquí se inicializa todo lo global.
*/

(function () {
  "use strict";

  // Helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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