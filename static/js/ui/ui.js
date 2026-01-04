// static/js/ui/ui.js
// UI core + Global Loader (ESTABLE)
// - Sin loops
// - Sin loaders eternos
// - El botón de tema JAMÁS activa loader

(function () {
  "use strict";

  // Evitar doble carga
  if (window.__uiCoreLoaded) return;
  window.__uiCoreLoaded = true;

  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ─────────────────────────────────────────────
     TOASTS (Bootstrap)
  ───────────────────────────────────────────── */
  function showToast(el) {
    if (!el || el.__toastBooted) return;
    if (!window.bootstrap || !window.bootstrap.Toast) return;
    try {
      el.__toastBooted = true;
      new window.bootstrap.Toast(el).show();
    } catch (_) {}
  }

  function bootToasts(root = document) {
    $$(".toast", root).forEach(showToast);
  }

  /* ─────────────────────────────────────────────
     SCROLL TOP
  ───────────────────────────────────────────── */
  function bootScrollTop() {
    const btn = document.getElementById("scrollTopBtn");
    if (!btn || btn.__booted) return;
    btn.__booted = true;

    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        btn.classList.toggle("show", window.scrollY > 300);
      });
    };

    window.addEventListener("scroll", onScroll, { passive: true });

    document.addEventListener(
      "click",
      (e) => {
        const t = e.target.closest?.("#scrollTopBtn");
        if (!t) return;
        e.preventDefault();
        window.scrollTo({ top: 0, behavior: "smooth" });
      },
      true
    );

    onScroll();
  }

  /* ─────────────────────────────────────────────
     CONFIRM LINKS
  ───────────────────────────────────────────── */
  function bootConfirmLinks() {
    if (window.__confirmLinksBound) return;
    window.__confirmLinksBound = true;

    document.addEventListener(
      "click",
      async (e) => {
        const a = e.target.closest?.("a[data-confirm]");
        if (!a) return;
        if (!window.AppModal?.confirm) return;

        e.preventDefault();
        const msg = a.getAttribute("data-confirm") || "¿Seguro?";
        try {
          const ok = await window.AppModal.confirm({ message: msg });
          if (!ok) return;
          const target = a.getAttribute("target");
          target && target !== "_self" ? window.open(a.href, target) : (window.location.href = a.href);
        } catch (_) {}
      },
      true
    );
  }

  /* ─────────────────────────────────────────────
     GLOBAL LOADER (ANTI-FREEZE)
  ───────────────────────────────────────────── */
  const LOADER_ID = "globalLoader";

  function ensureLoader() {
    let el = document.getElementById(LOADER_ID);
    if (el) return el;

    el = document.createElement("div");
    el.id = LOADER_ID;
    el.style.cssText = "position:fixed;inset:0;display:none;z-index:9999;background:rgba(0,0,0,.35);backdrop-filter:blur(3px);align-items:center;justify-content:center";

    const box = document.createElement("div");
    box.style.cssText = "background:#0a1024;color:#fff;padding:14px 18px;border-radius:14px;display:flex;gap:10px;align-items:center;font-weight:600";

    const spin = document.createElement("div");
    spin.style.cssText = "width:18px;height:18px;border-radius:50%;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;animation:spin .8s linear infinite";

    const txt = document.createElement("div");
    txt.textContent = "Cargando...";

    box.appendChild(spin);
    box.appendChild(txt);
    el.appendChild(box);
    document.body.appendChild(el);

    if (!document.getElementById("loaderSpinKF")) {
      const st = document.createElement("style");
      st.id = "loaderSpinKF";
      st.textContent = "@keyframes spin{to{transform:rotate(360deg)}}";
      document.head.appendChild(st);
    }

    return el;
  }

  function showLoader() {
    ensureLoader().style.display = "flex";
  }

  function hideLoader() {
    const el = document.getElementById(LOADER_ID);
    if (el) el.style.display = "none";
  }

  window.addEventListener("pageshow", hideLoader);
  window.addEventListener("error", hideLoader);
  window.addEventListener("unhandledrejection", hideLoader);

  function bootLoader() {
    if (window.__loaderBound) return;
    window.__loaderBound = true;

    document.addEventListener(
      "click",
      (e) => {
        // ❌ JAMÁS loader para el tema
        if (e.target.closest?.('#themeToggle, [data-theme-toggle="true"]')) {
          hideLoader();
          return;
        }

        if (e.target.closest?.('[data-no-loader="true"]')) return;

        const a = e.target.closest?.("a");
        if (!a) return;
        const href = a.getAttribute("href");
        if (!href || href === "#" || href.startsWith("javascript:")) return;
        if (a.hasAttribute("download")) return;
        const target = a.getAttribute("target");
        if (target && target !== "_self") return;
        if (a.hasAttribute("data-confirm")) return;

        showLoader();
      },
      true
    );

    document.addEventListener(
      "submit",
      (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        const btn = e.submitter;
        if (btn?.closest?.('#themeToggle, [data-theme-toggle="true"]')) {
          hideLoader();
          return;
        }
        if (btn?.closest?.('[data-no-loader="true"]')) return;
        showLoader();
      },
      true
    );
  }

  /* ─────────────────────────────────────────────
     INIT
  ───────────────────────────────────────────── */
  function init() {
    bootToasts();
    bootScrollTop();
    bootConfirmLinks();
    bootLoader();
  }

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", init)
    : init();
})();