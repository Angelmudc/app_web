// static/js/forms/autosave.js
// Autosave localStorage por formulario.
// Uso: <form data-autosave="clave_unica">...</form>
(function () {
  function keyFor(form) {
    const k = form.getAttribute("data-autosave");
    if (!k) return null;
    return "autosave:" + k;
  }

  function restore(form) {
    const k = keyFor(form);
    if (!k) return;
    const raw = localStorage.getItem(k);
    if (!raw) return;

    try {
      const data = JSON.parse(raw);
      Object.keys(data).forEach((name) => {
        const el = form.querySelector(`[name="${CSS.escape(name)}"]`);
        if (!el) return;

        if (el.type === "checkbox") el.checked = !!data[name];
        else if (el.type === "radio") {
          const r = form.querySelector(`[name="${CSS.escape(name)}"][value="${CSS.escape(data[name])}"]`);
          if (r) r.checked = true;
        } else {
          el.value = data[name];
        }
      });
      window.AppToast?.show?.("Borrador restaurado (autosave).", "info", 2200);
    } catch (_) {}
  }

  function snapshot(form) {
    const data = {};
    form.querySelectorAll("input,textarea,select").forEach((el) => {
      if (!el.name) return;

      if (el.type === "password") return; // nunca guardar passwords

      if (el.type === "checkbox") data[el.name] = el.checked;
      else if (el.type === "radio") {
        if (el.checked) data[el.name] = el.value;
      } else data[el.name] = el.value;
    });
    return data;
  }

  function boot() {
    document.querySelectorAll("form[data-autosave]").forEach((form) => {
      const k = keyFor(form);
      if (!k) return;

      restore(form);

      let t = null;
      form.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => {
          try {
            localStorage.setItem(k, JSON.stringify(snapshot(form)));
          } catch (_) {}
        }, 450);
      });

      form.addEventListener("submit", () => {
        try { localStorage.removeItem(k); } catch (_) {}
      });
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();