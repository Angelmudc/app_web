(function () {
  var body = document.body;
  if (!body) return;

  var enabled = String(body.getAttribute("data-client-public-message-island-enabled") || "") === "1";
  if (!enabled) return;

  var btn = document.getElementById("clientPublicMessageIslandBtn");
  var feedbackNode = document.getElementById("clientPublicMessageIslandFeedback");
  var manualPanel = document.getElementById("clientPublicMessageIslandManual");
  var manualStatus = document.getElementById("clientPublicMessageIslandManualStatus");
  var manualMessageInput = document.getElementById("clientPublicMessageIslandManualMessage");
  var manualLinkInput = document.getElementById("clientPublicMessageIslandManualLink");
  var retryCopyBtn = document.getElementById("clientPublicMessageIslandRetryCopyBtn");
  var closeManualBtn = document.getElementById("clientPublicMessageIslandCloseManualBtn");
  var linkUrl = String(body.getAttribute("data-client-public-message-link-url") || "").trim();
  if (!btn || !feedbackNode || !linkUrl) return;

  var feedbackTimer = null;
  var inFlight = false;
  var cooldownUntilMs = 0;
  var lastGeneratedLink = "";
  var lastGeneratedMessage = "";
  var labelNode = btn.querySelector(".cpmi-label");
  var defaultLabel = labelNode ? String(labelNode.textContent || "").trim() : "";

  function setButtonLabel(text) {
    if (!labelNode) return;
    labelNode.textContent = text;
  }

  function restoreButtonWhenReady() {
    var waitMs = Math.max(0, cooldownUntilMs - Date.now());
    window.setTimeout(function () {
      if (!inFlight && Date.now() >= cooldownUntilMs) {
        btn.disabled = false;
        setButtonLabel(defaultLabel || "Copiar formulario cliente");
      }
    }, waitMs);
  }

  function showFeedback(text) {
    feedbackNode.textContent = text || "Mensaje copiado";
    feedbackNode.classList.add("is-visible");
    if (feedbackTimer) {
      window.clearTimeout(feedbackTimer);
    }
    feedbackTimer = window.setTimeout(function () {
      feedbackNode.classList.remove("is-visible");
      feedbackTimer = null;
    }, 1700);
  }

  function copyWithExecCommand(value) {
    if (!value) return false;
    var tmp = document.createElement("textarea");
    tmp.value = value;
    tmp.setAttribute("readonly", "");
    tmp.style.position = "fixed";
    tmp.style.opacity = "0";
    tmp.style.pointerEvents = "none";
    tmp.style.left = "0";
    tmp.style.top = "0";
    document.body.appendChild(tmp);
    tmp.focus();
    tmp.select();
    tmp.setSelectionRange(0, value.length);
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_err) {
      ok = false;
    }
    document.body.removeChild(tmp);
    return !!ok;
  }

  async function copyTextSafe(text) {
    var value = String(text || "").trim();
    if (!value) return false;

    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (err) {
        console.error("[client-public-message-island] navigator.clipboard.writeText failed", err);
      }
    }
    return copyWithExecCommand(value);
  }

  function showManualPanel(link, message, statusText) {
    lastGeneratedLink = String(link || "").trim();
    lastGeneratedMessage = String(message || "").trim();
    if (!manualPanel || !manualLinkInput || !lastGeneratedLink) return;
    if (manualStatus) {
      manualStatus.textContent = String(statusText || "No se pudo copiar automáticamente. Puedes copiar manualmente o reintentar.");
    }
    if (manualMessageInput) {
      manualMessageInput.value = lastGeneratedMessage;
    }
    manualLinkInput.value = lastGeneratedLink;
    manualPanel.classList.remove("d-none");
  }

  function hideManualPanel() {
    if (!manualPanel) return;
    manualPanel.classList.add("d-none");
  }

  async function handleCopyClick(forceGenerate) {
    if (inFlight) return;
    if (Date.now() < cooldownUntilMs) return;

    inFlight = true;
    cooldownUntilMs = Date.now() + 5000;
    btn.disabled = true;
    hideManualPanel();

    try {
      var linkPublico = lastGeneratedLink;
      if (!linkPublico || forceGenerate) {
        setButtonLabel("Generando enlace...");
        showFeedback("Generando enlace...");
        console.info("[client-public-message-island] requesting endpoint", {
          url: linkUrl,
          method: "GET"
        });

        var resp = await fetch(linkUrl, {
          method: "GET",
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" }
        });
        var payload = {};
        try {
          payload = await resp.json();
        } catch (jsonErr) {
          console.error("[client-public-message-island] failed to parse JSON", jsonErr);
          payload = {};
        }
        console.info("[client-public-message-island] endpoint response", {
          status: resp.status,
          ok: resp.ok,
          payload: payload
        });

        if (!resp.ok || !payload.ok || !payload.link_publico) {
          var generationError = new Error("link-generation-failed");
          generationError.details = {
            status: resp.status,
            payload: payload
          };
          throw generationError;
        }
        linkPublico = String(payload.link_publico || "").trim();
        if (!linkPublico) {
          throw new Error("link-generation-failed");
        }
        lastGeneratedLink = linkPublico;
      }

      var messageBuilder = window.buildClientPublicFormMessage;
      var message = typeof messageBuilder === "function"
        ? messageBuilder(linkPublico)
        : [
            "Este es el formulario de Doméstica del Cibao A&D para registrar tu solicitud.",
            "",
            "Ahí puedes colocar tus datos y lo que necesitas, para poder ayudarte mejor.",
            "",
            linkPublico,
            "",
            "Cuando lo completes, envíame tu nombre y dime que ya terminaste."
          ].join("\n");
      lastGeneratedMessage = message;
      var copied = await copyTextSafe(message);
      if (!copied) {
        throw new Error("copy-failed");
      }
      hideManualPanel();
      showFeedback("Mensaje copiado");
    } catch (err) {
      cooldownUntilMs = Date.now() + 2500;
      if (String((err && err.message) || "") === "link-generation-failed") {
        console.error("[client-public-message-island] link generation failed", {
          error: err,
          details: err && err.details ? err.details : null
        });
        showFeedback("No se pudo generar el enlace");
      } else {
        console.error("[client-public-message-island] copy failed after link generation", {
          error: err,
          link: lastGeneratedLink
        });
        showFeedback("Enlace generado, pero no se pudo copiar automáticamente");
        showManualPanel(
          lastGeneratedLink,
          lastGeneratedMessage,
          "Enlace generado, pero no se pudo copiar automáticamente. Puedes copiar manualmente o reintentar."
        );
      }
    } finally {
      inFlight = false;
      restoreButtonWhenReady();
    }
  }

  btn.addEventListener("click", function () {
    handleCopyClick(!lastGeneratedLink);
  });

  if (retryCopyBtn) {
    retryCopyBtn.addEventListener("click", function () {
      if (!lastGeneratedMessage && !lastGeneratedLink) return;
      handleCopyClick(false);
    });
  }

  if (closeManualBtn) {
    closeManualBtn.addEventListener("click", function () {
      hideManualPanel();
    });
  }
})();
