/* ==========================================================
   ui/theme.js
   Tema Día / Noche (VERSIÓN ULTRA ROBUSTA)
   - Delegación global para que el botón SIEMPRE funcione
   - Evita conflictos / reentradas
   - Corrige color-scheme
   ========================================================== */

(function () {
  "use strict";

  const THEME_KEY = "theme"; // "light" | "dark"
  const root = document.documentElement;

  function getBody() {
    return document.body || null;
  }

  function systemPrefersDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function getSavedTheme() {
    try {
      const v = localStorage.getItem(THEME_KEY);
      return v === "dark" || v === "light" ? v : null;
    } catch (_) {
      return null;
    }
  }

  function setSavedTheme(theme) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) {}
  }

  function clearSavedTheme() {
    try { localStorage.removeItem(THEME_KEY); } catch (_) {}
  }

  function updateToggleIcon(theme) {
    const btn = document.getElementById("themeToggle") || document.querySelector('[data-theme-toggle="true"]');
    if (!btn) return;

    btn.innerHTML = theme === "dark"
      ? '<i class="fa-solid fa-sun"></i>'
      : '<i class="fa-solid fa-moon"></i>';

    btn.setAttribute("aria-label", theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro");
  }

  function applyTheme(theme) {
    const t = theme === "dark" ? "dark" : "light";
    if (window.__appApplyingTheme) return;

    const current = root.getAttribute("data-theme");
    if (current === t) {
      updateToggleIcon(t);
      return;
    }

    window.__appApplyingTheme = true;
    try {
      const body = getBody();

      // data-theme
      root.setAttribute("data-theme", t);
      if (body) body.setAttribute("data-theme", t);

      // color-scheme (inputs/scrollbars)
      root.style.colorScheme = t;
      if (body) body.style.colorScheme = t;

      // clases por compatibilidad
      root.classList.toggle("theme-dark", t === "dark");
      root.classList.toggle("theme-light", t === "light");
      root.classList.toggle("dark", t === "dark");
      root.classList.toggle("light", t === "light");

      if (body) {
        body.classList.toggle("theme-dark", t === "dark");
        body.classList.toggle("theme-light", t === "light");
        body.classList.toggle("dark", t === "dark");
        body.classList.toggle("light", t === "light");
      }

      // meta theme-color
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", t === "dark" ? "#0c111b" : "#ffffff");

      updateToggleIcon(t);
    } finally {
      window.__appApplyingTheme = false;
    }
  }

  function toggleTheme() {
    const saved = getSavedTheme();
    const current = saved ? saved : (root.getAttribute("data-theme") === "dark" ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    setSavedTheme(next);
    applyTheme(next);
  }

  // Si algún overlay se queda pegado (loader/backdrop), esto lo tumba
  function killOverlaysIfAny() {
    try {
      const ids = [
        "appGlobalLoader", "appGlobalLoader_text",
        "globalLoader", "loader", "pageLoader",
        "loadingOverlay", "overlayLoader"
      ];
      ids.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = "none";
      });

      document.querySelectorAll(".modal-backdrop").forEach((el) => el.remove());
      root.classList.remove("modal-open", "is-loading");
      if (document.body) document.body.classList.remove("modal-open", "is-loading");

      // restaura scroll si algo lo bloqueó
      root.style.removeProperty("overflow");
      if (document.body) {
        document.body.style.removeProperty("overflow");
        document.body.style.removeProperty("padding-right");
      }

      if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
        try { window.AppLoader.hideAll(); } catch (_) {}
      } else if (window.AppLoader && typeof window.AppLoader.hide === "function") {
        try { window.AppLoader.hide(); } catch (_) {}
      }
    } catch (_) {}
  }

  function exposeApi() {
    window.setTheme = function (theme) {
      const t = theme === "dark" ? "dark" : "light";
      setSavedTheme(t);
      applyTheme(t);
    };

    window.toggleTheme = function () {
      toggleTheme();
    };

    window.setThemeAuto = function () {
      clearSavedTheme();
      applyTheme(systemPrefersDark() ? "dark" : "light");
    };
  }

  function initTheme() {
    // Tema inicial
    const saved = getSavedTheme();
    applyTheme(saved ? saved : (systemPrefersDark() ? "dark" : "light"));

    // Marca el botón si existe
    const btn = document.getElementById("themeToggle");
    if (btn) {
      btn.setAttribute("data-theme-toggle", "true");
      btn.setAttribute("data-no-loader", "true");
      btn.type = "button";
    }

    // ✅ Delegación global: funciona aunque el botón cambie/sea re-renderizado
    if (!window.__themeDelegationBound) {
      window.__themeDelegationBound = true;

      document.addEventListener("click", (ev) => {
        const tbtn = ev.target && (ev.target.closest ? ev.target.closest('#themeToggle,[data-theme-toggle="true"]') : null);
        if (!tbtn) return;

        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation();

        toggleTheme();

        // limpia overlays pegados
        killOverlaysIfAny();
        setTimeout(killOverlaysIfAny, 0);
        setTimeout(killOverlaysIfAny, 120);
        setTimeout(killOverlaysIfAny, 300);
      }, true);
    }

    // Cambios del sistema SOLO en modo auto
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const onChange = (e) => {
        if (getSavedTheme()) return;
        applyTheme(e.matches ? "dark" : "light");
      };
      if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
      else if (typeof mq.addListener === "function") mq.addListener(onChange);
    }

    exposeApi();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTheme);
  } else {
    initTheme();
  }
})();