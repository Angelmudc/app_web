// static/js/core/toast.js
// Toasts en JS (además de los flash de Flask). Usa Bootstrap si está.
(function () {
  function show(message, type = "primary", timeout = 4000) {
    // type: primary | success | warning | danger | info
    const useBootstrap = !!window.bootstrap;

    let host = document.getElementById("appToastHost");
    if (!host) {
      host = document.createElement("div");
      host.id = "appToastHost";
      host.style.position = "fixed";
      host.style.top = "12px";
      host.style.left = "50%";
      host.style.transform = "translateX(-50%)";
      host.style.zIndex = "2100";
      host.style.display = "flex";
      host.style.flexDirection = "column";
      host.style.gap = "10px";
      host.style.width = "min(520px, calc(100% - 24px))";
      document.body.appendChild(host);
    }

    const el = document.createElement("div");
    el.className = `toast align-items-center text-bg-${type} show`;
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "assertive");
    el.setAttribute("aria-atomic", "true");

    el.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${String(message || "")}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" aria-label="Cerrar"></button>
      </div>
    `;

    host.appendChild(el);

    const closeBtn = el.querySelector("button");
    closeBtn.addEventListener("click", () => {
      el.remove();
    });

    if (useBootstrap) {
      try {
        const t = new bootstrap.Toast(el, { delay: timeout });
        t.show();
        el.addEventListener("hidden.bs.toast", () => el.remove());
        return;
      } catch (_) {}
    }

    // Fallback sin bootstrap
    setTimeout(() => {
      try { el.remove(); } catch (_) {}
    }, timeout);
  }

  window.AppToast = { show };
})();