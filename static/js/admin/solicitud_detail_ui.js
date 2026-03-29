// static/js/admin/solicitud_detail_ui.js
// UI helper para solicitud_detail (idempotente y compatible con PJAX).
(function () {
  "use strict";

  if (window.AdminSolicitudDetailUI) return;

  function showFeedback(message, category, inlineSelector, root) {
    const type = String(category || "info").toLowerCase();
    if (window.AppToast && typeof window.AppToast.show === "function") {
      window.AppToast.show(message, type === "danger" ? "danger" : type);
    }
    if (!inlineSelector) return;
    const scope = root && root.querySelector ? root : document;
    const box = scope.querySelector(inlineSelector) || document.querySelector(inlineSelector);
    if (!box) return;
    box.classList.remove("d-none", "alert-success", "alert-danger", "alert-warning", "alert-info");
    box.classList.add(
      type === "success"
        ? "alert-success"
        : (type === "warning" ? "alert-warning" : (type === "danger" ? "alert-danger" : "alert-info"))
    );
    box.textContent = message;
  }

  function fallbackCopy(text) {
    try {
      const ta = document.createElement("textarea");
      ta.value = String(text || "");
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length || 0);
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (_) {
      return false;
    }
  }

  function copyTextWithFallback(text) {
    if (navigator.clipboard && window.isSecureContext && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(String(text || "")).then(
        () => true,
        () => fallbackCopy(text)
      );
    }
    return Promise.resolve(fallbackCopy(text));
  }

  async function copyResumenCliente(root) {
    const scope = root && root.querySelector ? root : document;
    const textarea = scope.querySelector("#resumenCliente") || document.querySelector("#resumenCliente");
    if (!textarea) return false;

    const copied = await copyTextWithFallback(textarea.value || "");
    if (copied) {
      showFeedback("Resumen copiado al portapapeles.", "success", "#resumenCopyFeedback", scope);
      return true;
    }
    textarea.focus();
    textarea.select();
    showFeedback("No se pudo copiar automáticamente. Usa Ctrl/Cmd+C para copiar manualmente.", "warning", "#resumenCopyFeedback", scope);
    return false;
  }

  function bindResumenCopyButtons(root) {
    const scope = root && root.querySelector ? root : document;
    scope.querySelectorAll(".js-copy-resumen-cliente").forEach((btn) => {
      if (btn.dataset.copyBound === "1") return;
      btn.dataset.copyBound = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        copyResumenCliente(scope);
      });
    });
  }

  function bindInternalCopyButtons(root) {
    const scope = root && root.querySelector ? root : document;
    scope.querySelectorAll(".copy-btn-interno").forEach((btn) => {
      if (btn.dataset.copyBound === "1") return;
      btn.dataset.copyBound = "1";
      btn.addEventListener("click", async () => {
        let text = "";
        try {
          text = JSON.parse(btn.dataset.orderText || "\"\"");
          if (typeof text !== "string") text = String(text ?? "");
        } catch (_) {
          text = btn.dataset.orderText || "";
        }

        const copied = await copyTextWithFallback(text);
        if (!copied) {
          showFeedback("No se pudo copiar automáticamente. Intenta de nuevo o copia manualmente.", "warning", "#resumenCopyFeedback", scope);
          return;
        }

        showFeedback("Copiado al portapapeles.", "success", "#resumenCopyFeedback", scope);
        btn.innerHTML = '<i class="bi bi-check-circle-fill me-1"></i> Copiado';
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-success");
        btn.disabled = true;
        window.setTimeout(() => {
          btn.disabled = false;
          btn.classList.remove("btn-success");
          btn.classList.add("btn-primary");
          btn.innerHTML = '<i class="bi bi-clipboard-check me-1"></i> Copiar interno';
        }, 2000);
      });
    });
  }

  function bindContractCopyButtons(root) {
    const scope = root && root.querySelector ? root : document;
    scope.querySelectorAll(".js-copy-contract-link").forEach((btn) => {
      if (btn.dataset.copyBound === "1") return;
      btn.dataset.copyBound = "1";
      btn.addEventListener("click", async () => {
        const targetSelector = String(btn.dataset.copyTarget || "").trim();
        const inlineSelector = String(btn.dataset.inlineFeedbackTarget || "").trim();
        let text = String(btn.dataset.copyText || "").trim();

        if (targetSelector) {
          const targetInput = document.querySelector(targetSelector);
          text = String((targetInput && targetInput.value) || "").trim();
        }
        if (!text) {
          showFeedback("No hay link disponible para copiar todavía.", "warning", inlineSelector, scope);
          return;
        }

        const copied = await copyTextWithFallback(text);
        if (copied) {
          showFeedback("Link del contrato copiado.", "success", inlineSelector, scope);
          return;
        }

        if (targetSelector) {
          const targetInput = document.querySelector(targetSelector);
          if (targetInput) {
            targetInput.focus();
            targetInput.select();
          }
        }
        showFeedback("No se pudo copiar automáticamente. Usa Ctrl/Cmd+C sobre el link visible.", "warning", inlineSelector, scope);
      });
    });
  }

  function boot(root) {
    const scope = root && root.querySelector ? root : document;
    if (!scope.querySelector("#resumenCliente") && !scope.querySelector(".copy-btn-interno") && !scope.querySelector(".js-copy-contract-link")) {
      return;
    }
    bindResumenCopyButtons(scope);
    bindInternalCopyButtons(scope);
    bindContractCopyButtons(scope);
  }

  function init() {
    boot(document);
    document.addEventListener("admin:content-updated", (ev) => {
      const detail = ev && ev.detail ? ev.detail : {};
      boot(detail.container || document);
    });
    document.addEventListener("admin:navigation-complete", (ev) => {
      const detail = ev && ev.detail ? ev.detail : {};
      boot(detail.viewport || document);
    });
  }

  window.copiarResumenCliente = function () {
    return copyResumenCliente(document);
  };

  window.AdminSolicitudDetailUI = {
    init,
    boot,
    copiarResumenCliente: window.copiarResumenCliente,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
