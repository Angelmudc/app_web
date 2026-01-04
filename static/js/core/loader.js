// static/js/core/loader.js
// Loader global (ROBUSTO, anti "cargando infinito")
// - Muestra overlay SOLO cuando hay navegación real (links/form submit)
// - Ignora acciones JS-only: cambio de tema, botones internos, etc.

(function () {
  "use strict";

  // Evitar doble carga
  if (window.__coreLoaderLoaded) return;
  window.__coreLoaderLoaded = true;

  const ID = "appGlobalLoader";

  function ensure() {
    let el = document.getElementById(ID);
    if (el) return el;

    el = document.createElement("div");
    el.id = ID;
    el.style.position = "fixed";
    el.style.inset = "0";
    el.style.zIndex = "2000";
    el.style.display = "none";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.backdropFilter = "blur(6px)";
    el.style.background = "rgba(0,0,0,.25)";

    const box = document.createElement("div");
    box.style.padding = "16px 18px";
    box.style.borderRadius = "14px";
    box.style.border = "1px solid rgba(255,255,255,.25)";
    box.style.background = "rgba(15,23,42,.85)";
    box.style.color = "#fff";
    box.style.display = "flex";
    box.style.gap = "10px";
    box.style.alignItems = "center";
    box.style.boxShadow = "0 18px 45px rgba(0,0,0,.35)";

    const spinner = document.createElement("div");
    spinner.style.width = "18px";
    spinner.style.height = "18px";
    spinner.style.borderRadius = "999px";
    spinner.style.border = "2px solid rgba(255,255,255,.35)";
    spinner.style.borderTopColor = "#fff";
    spinner.style.animation = "appSpin .8s linear infinite";

    const text = document.createElement("div");
    text.id = ID + "_text";
    text.style.fontWeight = "700";
    text.textContent = "Cargando...";

    // keyframes (una sola vez)
    if (!document.getElementById("appSpinStyle")) {
      const style = document.createElement("style");
      style.id = "appSpinStyle";
      style.textContent = "@keyframes appSpin{to{transform:rotate(360deg)}}";
      document.head.appendChild(style);
    }

    box.appendChild(spinner);
    box.appendChild(text);
    el.appendChild(box);
    document.body.appendChild(el);
    return el;
  }

  function show(msg = "Cargando...") {
    const el = ensure();
    const t = document.getElementById(ID + "_text");
    if (t) t.textContent = msg;
    el.style.display = "flex";
  }

  function hide() {
    const el = document.getElementById(ID);
    if (el) el.style.display = "none";
  }

  function hideAll() {
    hide();
    // Por si algún loader alterno existe
    ["globalLoader", "loader", "pageLoader", "loadingOverlay", "overlayLoader"].forEach((id) => {
      const x = document.getElementById(id);
      if (x) x.style.display = "none";
    });
    document.documentElement.classList.remove("is-loading");
    if (document.body) document.body.classList.remove("is-loading");
  }

  // Nunca te quedes con overlay pegado
  window.addEventListener("pageshow", hideAll);
  window.addEventListener("error", hideAll);
  window.addEventListener("unhandledrejection", hideAll);

  // Delegación: solo mostrar loader cuando de verdad hay navegación
  if (!window.__coreLoaderDelegationBound) {
    window.__coreLoaderDelegationBound = true;

    document.addEventListener(
      "click",
      (e) => {
        // ✅ Tema: JAMÁS loader. Y si estaba prendido por error, apágalo.
        const themeBtn = e.target && (e.target.closest ? e.target.closest('#themeToggle, [data-theme-toggle="true"]') : null);
        if (themeBtn) {
          hideAll();
          return;
        }

        // Escape hatch
        const noLoader = e.target && (e.target.closest ? e.target.closest('[data-no-loader="true"]') : null);
        if (noLoader) return;

        // Solo links
        const a = e.target && (e.target.closest ? e.target.closest("a") : null);
        if (!a) return;

        const href = a.getAttribute("href");
        if (!href || href === "#" || href.startsWith("javascript:")) return;

        // Nueva pestaña / descarga
        const target = a.getAttribute("target");
        if (target && target !== "_self") return;
        if (a.hasAttribute("download")) return;

        // Confirm lo maneja el modal
        if (a.hasAttribute("data-confirm")) return;

        show("Cargando...");
      },
      true
    );

    document.addEventListener(
      "submit",
      (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;

        const submitter = e.submitter;
        const themeBtn = submitter && (submitter.closest ? submitter.closest('#themeToggle, [data-theme-toggle="true"]') : null);
        if (themeBtn) {
          hideAll();
          return;
        }

        const noLoader = submitter && (submitter.closest ? submitter.closest('[data-no-loader="true"]') : null);
        if (noLoader) return;

        show("Cargando...");
      },
      true
    );
  }

  // API única (si otra parte ya creó AppLoader, no la rompemos; solo aseguramos hideAll)
  if (!window.AppLoader) {
    window.AppLoader = { show, hide, hideAll };
  } else {
    window.AppLoader.show = window.AppLoader.show || show;
    window.AppLoader.hide = window.AppLoader.hide || hide;
    window.AppLoader.hideAll = hideAll;
  }
})();