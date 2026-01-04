// static/js/forms/validate.js
// Validación simple: required + minlength + email.
// Usa atributos HTML: required, minlength, data-email="1"
(function () {
  function isEmail(s) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(s || "").trim());
  }

  function mark(el, ok, msg) {
    el.classList.toggle("is-invalid", !ok);
    el.classList.toggle("is-valid", ok);

    let fb = el.parentElement?.querySelector(".invalid-feedback");
    if (!fb) {
      fb = document.createElement("div");
      fb.className = "invalid-feedback";
      el.parentElement?.appendChild(fb);
    }
    fb.textContent = ok ? "" : (msg || "Campo inválido");
  }

  function validateForm(form) {
    let ok = true;
    const fields = form.querySelectorAll("input,textarea,select");
    fields.forEach((el) => {
      if (!(el instanceof HTMLElement)) return;
      const input = el;

      const required = input.hasAttribute("required");
      const min = input.getAttribute("minlength");
      const needEmail = input.getAttribute("data-email") === "1";

      const value = (input.value || "").trim();

      if (required && !value) {
        ok = false;
        mark(input, false, "Este campo es obligatorio.");
        return;
      }
      if (min && value && value.length < parseInt(min, 10)) {
        ok = false;
        mark(input, false, `Mínimo ${min} caracteres.`);
        return;
      }
      if (needEmail && value && !isEmail(value)) {
        ok = false;
        mark(input, false, "Email inválido.");
        return;
      }

      // si pasó
      if (required || min || needEmail) mark(input, true, "");
    });

    return ok;
  }

  function boot() {
    document.querySelectorAll("form[data-validate='1']").forEach((form) => {
      form.addEventListener("submit", (ev) => {
        if (!validateForm(form)) {
          ev.preventDefault();
          window.AppToast?.show?.("Revisa los campos marcados en rojo.", "warning");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();