// static/js/entrevistas/entrevistas.js
// Solo UI para entrevistas (sin romper tu backend).
(function () {
  function boot() {
    // 1) Auto-focus en el primer input del form de entrevista
    const form = document.querySelector("form[data-page='entrevista-form']");
    if (form) {
      const first = form.querySelector("input, textarea, select");
      if (first) first.focus();
    }

    // 2) Confirmación para botones peligrosos (si tú los agregas)
    document.querySelectorAll("[data-danger-confirm]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        if (!window.AppModal?.confirm) return;
        ev.preventDefault();
        const msg = btn.getAttribute("data-danger-confirm") || "¿Seguro?";
        const ok = await window.AppModal.confirm({ message: msg, okText: "Sí", cancelText: "Cancelar" });
        if (ok) {
          if (btn.tagName === "A") window.location.href = btn.href;
          else btn.closest("form")?.submit?.();
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();