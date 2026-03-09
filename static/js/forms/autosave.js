// static/js/forms/autosave.js
// Autosave localStorage por formulario.
// Uso: <form data-autosave="clave_unica">...</form>
(function () {
  let STORAGE = null;
  try {
    STORAGE = window.sessionStorage;
  } catch (_) {
    STORAGE = null;
  }
  if (!STORAGE) return;
  const TTL_MS = 30 * 60 * 1000; // 30 minutos
  const SENSITIVE_TOKENS = [
    "password", "clave", "token", "csrf", "cedula", "telefono", "phone",
    "whatsapp", "direccion", "address", "email", "correo"
  ];

  function keyFor(form) {
    const k = form.getAttribute("data-autosave");
    if (!k) return null;
    return "autosave:" + k;
  }

  function isSensitiveName(name) {
    const txt = String(name || "").toLowerCase();
    return SENSITIVE_TOKENS.some((token) => txt.includes(token));
  }

  function restore(form) {
    const k = keyFor(form);
    if (!k) return;
    const raw = STORAGE.getItem(k);
    if (!raw) return;

    try {
      const envelope = JSON.parse(raw);
      const savedAt = Number(envelope.saved_at || 0);
      if (!savedAt || (Date.now() - savedAt) > TTL_MS) {
        STORAGE.removeItem(k);
        return;
      }
      const data = envelope.data || {};
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
      if (isSensitiveName(el.name)) return;

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
            STORAGE.setItem(k, JSON.stringify({ saved_at: Date.now(), data: snapshot(form) }));
          } catch (_) {}
        }, 450);
      });

      form.addEventListener("submit", () => {
        try { STORAGE.removeItem(k); } catch (_) {}
      });
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
