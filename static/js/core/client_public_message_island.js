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
  var debugEnabled = String(body.getAttribute("data-client-public-message-debug") || "") === "1";
  if (!btn || !feedbackNode || !linkUrl) return;

  var feedbackTimer = null;
  var inFlight = false;
  var cooldownUntilMs = 0;
  var lastGeneratedLink = "";
  var lastGeneratedMessage = "";
  var labelNode = btn.querySelector(".cpmi-label");
  var iconNode = btn.querySelector(".cpmi-icon");
  var defaultLabel = labelNode ? String(labelNode.textContent || "").trim() : "";

  function debugClipboard(eventName, details) {
    if (!debugEnabled || !window.console || typeof window.console.info !== "function") return;
    console.info("[client-public-message-island]", eventName, details || {});
  }

  function summarizeUserAgent() {
    var ua = String((window.navigator && window.navigator.userAgent) || "").trim();
    if (!ua) return "unknown";
    return ua.slice(0, 120);
  }

  function setButtonLabel(text) {
    if (!labelNode) return;
    labelNode.textContent = text;
  }

  function setButtonVisualState(state) {
    btn.classList.remove("is-loading", "is-success", "is-error");
    if (state) {
      btn.classList.add("is-" + state);
    }
    if (iconNode) {
      iconNode.setAttribute("aria-hidden", "true");
    }
  }

  function restoreButtonWhenReady() {
    var waitMs = Math.max(0, cooldownUntilMs - Date.now());
    window.setTimeout(function () {
      if (!inFlight && Date.now() >= cooldownUntilMs) {
        btn.disabled = false;
        setButtonVisualState("");
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
    if (!value) return { ok: false, method: "execCommand", error: null };
    var tmp = document.createElement("textarea");
    var activeElement = document.activeElement;
    tmp.value = value;
    tmp.setAttribute("aria-hidden", "true");
    tmp.style.position = "fixed";
    tmp.style.opacity = "0";
    tmp.style.pointerEvents = "none";
    tmp.style.left = "-9999px";
    tmp.style.top = "0";
    tmp.style.fontSize = "16px";
    tmp.style.contain = "strict";
    document.body.appendChild(tmp);
    var ok = false;
    var execError = null;
    try {
      tmp.focus();
      tmp.select();
      tmp.setSelectionRange(0, value.length);
      ok = document.execCommand("copy");
    } catch (err) {
      execError = err || null;
      ok = false;
    }
    document.body.removeChild(tmp);
    if (activeElement && typeof activeElement.focus === "function") {
      try {
        activeElement.focus();
      } catch (_focusErr) {
        // noop
      }
    }
    return {
      ok: !!ok,
      method: "execCommand",
      error: execError
    };
  }

  async function copyTextSafe(text) {
    var value = String(text || "").trim();
    var diagnostics = {
      clipboardAvailable: !!(navigator.clipboard && typeof navigator.clipboard.writeText === "function"),
      secureContext: !!window.isSecureContext,
      userAgent: summarizeUserAgent()
    };
    if (!value) {
      return {
        ok: false,
        method: "none",
        error: null,
        diagnostics: diagnostics
      };
    }

    debugClipboard("copy-attempt", diagnostics);

    if (diagnostics.clipboardAvailable) {
      try {
        await navigator.clipboard.writeText(value);
        debugClipboard("copy-success", {
          method: "clipboard",
          secureContext: diagnostics.secureContext,
          clipboardAvailable: diagnostics.clipboardAvailable,
          userAgent: diagnostics.userAgent
        });
        return {
          ok: true,
          method: "clipboard",
          error: null,
          diagnostics: diagnostics
        };
      } catch (err) {
        diagnostics.clipboardError = {
          name: String((err && err.name) || "Error"),
          message: String((err && err.message) || "unknown")
        };
        debugClipboard("clipboard-write-failed", {
          clipboardAvailable: diagnostics.clipboardAvailable,
          secureContext: diagnostics.secureContext,
          errorName: diagnostics.clipboardError.name,
          errorMessage: diagnostics.clipboardError.message,
          userAgent: diagnostics.userAgent
        });
      }
    }
    var fallbackResult = copyWithExecCommand(value);
    diagnostics.execCommandSupported = typeof document.execCommand === "function";
    diagnostics.execCommandSucceeded = !!fallbackResult.ok;
    if (fallbackResult.error) {
      diagnostics.execCommandError = {
        name: String((fallbackResult.error && fallbackResult.error.name) || "Error"),
        message: String((fallbackResult.error && fallbackResult.error.message) || "unknown")
      };
    }
    debugClipboard(
      fallbackResult.ok ? "execCommand-copy-success" : "execCommand-copy-failed",
      {
        clipboardAvailable: diagnostics.clipboardAvailable,
        secureContext: diagnostics.secureContext,
        execCommandSupported: diagnostics.execCommandSupported,
        execCommandSucceeded: diagnostics.execCommandSucceeded,
        errorName: diagnostics.execCommandError ? diagnostics.execCommandError.name : "",
        errorMessage: diagnostics.execCommandError ? diagnostics.execCommandError.message : "",
        userAgent: diagnostics.userAgent
      }
    );
    return {
      ok: !!fallbackResult.ok,
      method: fallbackResult.ok ? "execCommand" : "none",
      error: fallbackResult.error || null,
      diagnostics: diagnostics
    };
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

  function buildMessageForLink(linkPublico) {
    var messageBuilder = window.buildClientPublicFormMessage;
    return typeof messageBuilder === "function"
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
  }

  async function generateNewPublicLink() {
    showFeedback("Generando enlace...");
    debugClipboard("request-link", {
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
    debugClipboard("link-response", {
      status: resp.status,
      ok: resp.ok,
      payloadOk: !!payload.ok,
      hasLink: !!payload.link_publico
    });

    if (!resp.ok || !payload.ok || !payload.link_publico) {
      var generationError = new Error("link-generation-failed");
      generationError.details = {
        status: resp.status,
        payload: payload
      };
      throw generationError;
    }
    var linkPublico = String(payload.link_publico || "").trim();
    if (!linkPublico) {
      throw new Error("link-generation-failed");
    }
    lastGeneratedLink = linkPublico;
    lastGeneratedMessage = buildMessageForLink(linkPublico);
    return linkPublico;
  }

  function resolveGenerationErrorMessage(err) {
    var fallback = "No se pudo generar el enlace";
    var details = err && err.details ? err.details : null;
    var payload = details && details.payload ? details.payload : null;
    var message = String((payload && payload.message) || "").trim();
    if (!message) return fallback;

    var retryAfterSec = parseInt((payload && payload.retry_after_sec), 10);
    if (Number.isFinite(retryAfterSec) && retryAfterSec > 0) {
      return message + " (" + retryAfterSec + "s)";
    }
    return message;
  }

  async function copyLastGeneratedLink() {
    if (!lastGeneratedLink) {
      return {
        ok: false,
        method: "none",
        error: null,
        diagnostics: null
      };
    }
    if (!lastGeneratedMessage) {
      lastGeneratedMessage = buildMessageForLink(lastGeneratedLink);
    }
    return copyTextSafe(lastGeneratedMessage);
  }

  async function handleGenerateClick() {
    if (inFlight) return;
    if (Date.now() < cooldownUntilMs) return;

    inFlight = true;
    cooldownUntilMs = Date.now() + 5000;
    btn.disabled = true;
    setButtonVisualState("loading");
    setButtonLabel("Generando enlace...");
    hideManualPanel();

    try {
      await generateNewPublicLink();
      var copyResult = await copyLastGeneratedLink();
      if (!copyResult.ok) {
        throw new Error("copy-failed");
      }
      hideManualPanel();
      setButtonVisualState("success");
      setButtonLabel("Mensaje copiado");
      showFeedback("Mensaje copiado");
      cooldownUntilMs = Date.now() + 1400;
    } catch (err) {
      cooldownUntilMs = Date.now() + 2500;
      if (String((err && err.message) || "") === "link-generation-failed") {
        console.error("[client-public-message-island] link generation failed", {
          error: err,
          details: err && err.details ? err.details : null
        });
        var generationErrorMessage = resolveGenerationErrorMessage(err);
        setButtonVisualState("error");
        setButtonLabel(generationErrorMessage);
        showFeedback(generationErrorMessage);
      } else {
        console.error("[client-public-message-island] copy failed after link generation", {
          error: err
        });
        setButtonVisualState("error");
        setButtonLabel("Copia manual disponible");
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

  async function handleRetryCopyClick() {
    if (!lastGeneratedLink && !lastGeneratedMessage) return;
    if (inFlight) return;
    if (Date.now() < cooldownUntilMs) return;

    inFlight = true;
    cooldownUntilMs = Date.now() + 2500;
    btn.disabled = true;
    setButtonVisualState("loading");
    setButtonLabel("Copiando mensaje...");

    try {
      var copyResult = await copyLastGeneratedLink();
      if (!copyResult.ok) {
        throw new Error("copy-failed");
      }
      hideManualPanel();
      setButtonVisualState("success");
      setButtonLabel("Mensaje copiado");
      showFeedback("Mensaje copiado");
      cooldownUntilMs = Date.now() + 1400;
    } catch (err) {
      setButtonVisualState("error");
      setButtonLabel("Copia manual disponible");
      showFeedback("Enlace generado, pero no se pudo copiar automáticamente");
      showManualPanel(
        lastGeneratedLink,
        lastGeneratedMessage,
        "Enlace generado, pero no se pudo copiar automáticamente. Puedes copiar manualmente o reintentar."
      );
    } finally {
      inFlight = false;
      restoreButtonWhenReady();
    }
  }

  btn.addEventListener("click", function () {
    handleGenerateClick();
  });

  if (retryCopyBtn) {
    retryCopyBtn.addEventListener("click", function () {
      handleRetryCopyClick();
    });
  }

  if (closeManualBtn) {
    closeManualBtn.addEventListener("click", function () {
      hideManualPanel();
    });
  }
})();
