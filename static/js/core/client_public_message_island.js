(function () {
  var body = document.body;
  if (!body) return;

  var DEBUG_STORAGE_KEY = "cpmi_debug_enabled";

  function persistDebugFlag(value) {
    try {
      if (value) {
        window.sessionStorage.setItem(DEBUG_STORAGE_KEY, "1");
      } else {
        window.sessionStorage.removeItem(DEBUG_STORAGE_KEY);
      }
    } catch (_err) {
      // noop
    }
  }

  function readPersistedDebugFlag() {
    try {
      return window.sessionStorage.getItem(DEBUG_STORAGE_KEY) === "1";
    } catch (_err) {
      return false;
    }
  }

  function installDebugFlagBridge() {
    if (window.__CPMIDebugBridgeInstalled) return;
    window.__CPMIDebugBridgeInstalled = true;
    try {
      Object.defineProperty(window, "__CPMI_DEBUG", {
        configurable: true,
        enumerable: false,
        get: function () {
          return readPersistedDebugFlag();
        },
        set: function (value) {
          persistDebugFlag(!!value);
        }
      });
    } catch (_err) {
      // noop
    }
  }

  installDebugFlagBridge();

  var enabled = String(body.getAttribute("data-client-public-message-island-enabled") || "") === "1";
  if (!enabled) return;

  var btn = document.getElementById("clientPublicMessageIslandBtn");
  var feedbackNode = document.getElementById("clientPublicMessageIslandFeedback");
  var manualPanel = document.getElementById("clientPublicMessageIslandManual");
  var manualStatus = document.getElementById("clientPublicMessageIslandManualStatus");
  var manualLinkInput = document.getElementById("clientPublicMessageIslandManualLink");
  var selectLinkBtn = document.getElementById("clientPublicMessageIslandSelectLinkBtn");
  var retryCopyBtn = document.getElementById("clientPublicMessageIslandRetryCopyBtn");
  var closeManualBtn = document.getElementById("clientPublicMessageIslandCloseManualBtn");
  var linkUrl = String(body.getAttribute("data-client-public-message-link-url") || "").trim();
  var debugEnabled = String(body.getAttribute("data-client-public-message-debug") || "") === "1" || readPersistedDebugFlag();
  if (!btn || !feedbackNode || !linkUrl) return;

  var feedbackTimer = null;
  var inFlight = false;
  var cooldownUntilMs = 0;
  var lastGeneratedLink = "";
  var lastGeneratedMessage = "";
  var labelNode = btn.querySelector(".cpmi-label");
  var iconNode = btn.querySelector(".cpmi-icon");
  var defaultLabel = labelNode ? String(labelNode.textContent || "").trim() : "";
  var lastCopyDiagnosis = null;

  function getUserActivationSnapshot() {
    return {
      isActive: !!(navigator.userActivation && navigator.userActivation.isActive),
      hasBeenActive: !!(navigator.userActivation && navigator.userActivation.hasBeenActive)
    };
  }

  function debugClipboard(eventName, details) {
    if (!debugEnabled || !window.console || typeof window.console.info !== "function") return;
    console.info("[client-public-message-island]", eventName, details || {});
  }

  function updateCopyDebugState(method, error, diagnostics) {
    try {
      window.__CPMI_LAST_COPY_METHOD = String(method || "");
      window.__CPMI_LAST_COPY_ERROR = error ? {
        name: String((error && error.name) || "Error"),
        message: String((error && error.message) || "unknown")
      } : null;
      window.__CPMI_LAST_COPY_DIAGNOSIS = diagnostics || null;
    } catch (_err) {
      // noop
    }
  }

  function reportClipboardDiagnosis(stage, diagnostics) {
    if (!debugEnabled || !window.console || typeof window.console.info !== "function") return;
    console.info("[client-public-message-island] clipboard diagnosis", {
      stage: stage,
      secureContext: !!(diagnostics && diagnostics.secureContext),
      clipboardAvailable: !!(diagnostics && diagnostics.clipboardAvailable),
      clipboardWriteAvailable: !!(diagnostics && diagnostics.clipboardWriteAvailable),
      clipboardItemAvailable: !!(diagnostics && diagnostics.clipboardItemAvailable),
      writePromiseAttempted: !!(diagnostics && diagnostics.writePromiseAttempted),
      writePromiseSuccess: diagnostics ? diagnostics.writePromiseSuccess : null,
      writePromiseErrorName: diagnostics && diagnostics.clipboardPromiseError ? diagnostics.clipboardPromiseError.name : "",
      writePromiseErrorMessage: diagnostics && diagnostics.clipboardPromiseError ? diagnostics.clipboardPromiseError.message : "",
      writeTextAttempted: !!(diagnostics && diagnostics.writeTextAttempted),
      writeTextSuccess: diagnostics ? diagnostics.writeTextSuccess : null,
      writeTextErrorName: diagnostics && diagnostics.clipboardError ? diagnostics.clipboardError.name : "",
      writeTextErrorMessage: diagnostics && diagnostics.clipboardError ? diagnostics.clipboardError.message : "",
      execCommandSupported: diagnostics ? diagnostics.execCommandSupported : null,
      execCommandAttempted: !!(diagnostics && diagnostics.execCommandAttempted),
      execCommandSuccess: diagnostics ? diagnostics.execCommandSucceeded : null,
      execCommandErrorName: diagnostics && diagnostics.execCommandError ? diagnostics.execCommandError.name : "",
      execCommandErrorMessage: diagnostics && diagnostics.execCommandError ? diagnostics.execCommandError.message : "",
      textareaInserted: !!(diagnostics && diagnostics.textareaInserted),
      textareaSelected: !!(diagnostics && diagnostics.textareaSelected),
      activeElementIsTextarea: !!(diagnostics && diagnostics.activeElementIsTextarea),
      userActivationAtAttempt: diagnostics ? diagnostics.userActivationAtAttempt : null,
      userActivationAtWritePromise: diagnostics ? diagnostics.userActivationAtWritePromise : null,
      userActivationAtWriteText: diagnostics ? diagnostics.userActivationAtWriteText : null,
      userActivationAtExecCommand: diagnostics ? diagnostics.userActivationAtExecCommand : null,
      userAgent: diagnostics ? diagnostics.userAgent : "unknown"
    });
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

  function selectManualLinkInput() {
    if (!manualLinkInput || !manualLinkInput.value) return false;
    try {
      manualLinkInput.focus();
      manualLinkInput.removeAttribute("readonly");
      manualLinkInput.select();
      manualLinkInput.setSelectionRange(0, manualLinkInput.value.length);
      manualLinkInput.setAttribute("readonly", "readonly");
      return true;
    } catch (_err) {
      try {
        manualLinkInput.setAttribute("readonly", "readonly");
      } catch (_err2) {
        // noop
      }
      return false;
    }
  }

  function copyWithExecCommand(value) {
    if (!value) return { ok: false, method: "execCommand", error: null };
    var tmp = document.createElement("textarea");
    var activeElement = document.activeElement;
    var selected = false;
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
    var activeElementIsTextarea = false;
    var ok = false;
    var execError = null;
    try {
      tmp.focus();
      activeElementIsTextarea = document.activeElement === tmp;
      tmp.select();
      tmp.setSelectionRange(0, value.length);
      selected = true;
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
      error: execError,
      diagnostics: {
        textareaInserted: true,
        textareaSelected: selected,
        activeElementIsTextarea: activeElementIsTextarea
      }
    };
  }

  function createCopyDiagnostics() {
    return {
      clipboardAvailable: !!(navigator.clipboard && typeof navigator.clipboard.writeText === "function"),
      clipboardWriteAvailable: !!(navigator.clipboard && typeof navigator.clipboard.write === "function"),
      clipboardItemAvailable: typeof window.ClipboardItem === "function",
      secureContext: !!window.isSecureContext,
      userAgent: summarizeUserAgent(),
      writePromiseAttempted: false,
      writePromiseSuccess: null,
      writeTextAttempted: false,
      writeTextSuccess: null,
      execCommandAttempted: false,
      execCommandSupported: null,
      execCommandSucceeded: null,
      textareaInserted: false,
      textareaSelected: false,
      activeElementIsTextarea: false,
      userActivationAtAttempt: getUserActivationSnapshot(),
      userActivationAtWritePromise: null,
      userActivationAtWriteText: null,
      userActivationAtExecCommand: null
    };
  }

  function createTextClipboardBlob(value) {
    return new Blob([String(value || "")], { type: "text/plain" });
  }

  function copyTextFromPromiseWithinGesture(textPromise) {
    if (!navigator.clipboard || typeof navigator.clipboard.write !== "function" || typeof window.ClipboardItem !== "function") {
      return null;
    }

    var diagnostics = createCopyDiagnostics();
    diagnostics.writePromiseAttempted = true;
    diagnostics.userActivationAtWritePromise = getUserActivationSnapshot();

    try {
      var item = new window.ClipboardItem({
        "text/plain": Promise.resolve(textPromise).then(function (value) {
          return createTextClipboardBlob(value);
        })
      });
      return navigator.clipboard.write([item]).then(function () {
        diagnostics.writePromiseSuccess = true;
        lastCopyDiagnosis = diagnostics;
        updateCopyDebugState("clipboard-write", null, diagnostics);
        debugClipboard("copy-success", {
          method: "clipboard-write",
          secureContext: diagnostics.secureContext,
          clipboardAvailable: diagnostics.clipboardAvailable,
          clipboardWriteAvailable: diagnostics.clipboardWriteAvailable,
          clipboardItemAvailable: diagnostics.clipboardItemAvailable,
          userAgent: diagnostics.userAgent
        });
        reportClipboardDiagnosis("clipboard-write-success", diagnostics);
        return {
          ok: true,
          method: "clipboard-write",
          error: null,
          diagnostics: diagnostics
        };
      }).catch(function (err) {
        diagnostics.writePromiseSuccess = false;
        diagnostics.clipboardPromiseError = {
          name: String((err && err.name) || "Error"),
          message: String((err && err.message) || "unknown")
        };
        lastCopyDiagnosis = diagnostics;
        updateCopyDebugState("clipboard-write", err || null, diagnostics);
        debugClipboard("clipboard-write-failed", {
          clipboardAvailable: diagnostics.clipboardAvailable,
          clipboardWriteAvailable: diagnostics.clipboardWriteAvailable,
          clipboardItemAvailable: diagnostics.clipboardItemAvailable,
          secureContext: diagnostics.secureContext,
          errorName: diagnostics.clipboardPromiseError.name,
          errorMessage: diagnostics.clipboardPromiseError.message,
          userAgent: diagnostics.userAgent
        });
        reportClipboardDiagnosis("clipboard-write-failed", diagnostics);
        return {
          ok: false,
          method: "clipboard-write",
          error: err || null,
          diagnostics: diagnostics
        };
      });
    } catch (err) {
      diagnostics.writePromiseSuccess = false;
      diagnostics.clipboardPromiseError = {
        name: String((err && err.name) || "Error"),
        message: String((err && err.message) || "unknown")
      };
      lastCopyDiagnosis = diagnostics;
      updateCopyDebugState("clipboard-write", err || null, diagnostics);
      reportClipboardDiagnosis("clipboard-write-failed", diagnostics);
      return Promise.resolve({
        ok: false,
        method: "clipboard-write",
        error: err || null,
        diagnostics: diagnostics
      });
    }
  }

  async function copyTextSafe(text) {
    var value = String(text || "").trim();
    var diagnostics = createCopyDiagnostics();
    if (!value) {
      lastCopyDiagnosis = diagnostics;
      reportClipboardDiagnosis("empty-text", diagnostics);
      return {
        ok: false,
        method: "none",
        error: null,
        diagnostics: diagnostics
      };
    }

    debugClipboard("copy-attempt", diagnostics);

    if (diagnostics.clipboardAvailable) {
      diagnostics.writeTextAttempted = true;
      diagnostics.userActivationAtWriteText = getUserActivationSnapshot();
      try {
        await navigator.clipboard.writeText(value);
        diagnostics.writeTextSuccess = true;
        updateCopyDebugState("clipboard", null, diagnostics);
        debugClipboard("copy-success", {
          method: "clipboard",
          secureContext: diagnostics.secureContext,
          clipboardAvailable: diagnostics.clipboardAvailable,
          userAgent: diagnostics.userAgent
        });
        lastCopyDiagnosis = diagnostics;
        reportClipboardDiagnosis("clipboard-write-success", diagnostics);
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
        diagnostics.writeTextSuccess = false;
        updateCopyDebugState("clipboard", err || null, diagnostics);
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
    diagnostics.execCommandAttempted = true;
    diagnostics.userActivationAtExecCommand = getUserActivationSnapshot();
    diagnostics.execCommandSupported = typeof document.execCommand === "function";
    diagnostics.execCommandSucceeded = !!fallbackResult.ok;
    if (fallbackResult.diagnostics) {
      diagnostics.textareaInserted = !!fallbackResult.diagnostics.textareaInserted;
      diagnostics.textareaSelected = !!fallbackResult.diagnostics.textareaSelected;
      diagnostics.activeElementIsTextarea = !!fallbackResult.diagnostics.activeElementIsTextarea;
    }
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
    lastCopyDiagnosis = diagnostics;
    updateCopyDebugState(fallbackResult.ok ? "execCommand" : "none", fallbackResult.error || null, diagnostics);
    reportClipboardDiagnosis(
      fallbackResult.ok ? "execCommand-copy-success" : "execCommand-copy-failed",
      diagnostics
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
      manualStatus.textContent = String(statusText || "Safari no permitió copiar automáticamente. Copia el enlace manualmente.");
    }
    manualLinkInput.value = lastGeneratedLink;
    manualPanel.classList.remove("d-none");
    window.setTimeout(function () {
      selectManualLinkInput();
    }, 40);
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
    reportClipboardDiagnosis("link-response", {
      secureContext: !!window.isSecureContext,
      clipboardAvailable: !!(navigator.clipboard && typeof navigator.clipboard.writeText === "function"),
      clipboardWriteAvailable: !!(navigator.clipboard && typeof navigator.clipboard.write === "function"),
      clipboardItemAvailable: typeof window.ClipboardItem === "function",
      writePromiseAttempted: false,
      writePromiseSuccess: null,
      writeTextAttempted: false,
      writeTextSuccess: null,
      execCommandSupported: typeof document.queryCommandSupported === "function" ? document.queryCommandSupported("copy") : null,
      execCommandAttempted: false,
      execCommandSucceeded: null,
      textareaInserted: false,
      textareaSelected: false,
      activeElementIsTextarea: false,
      userActivationAtAttempt: null,
      userActivationAtWritePromise: null,
      userActivationAtWriteText: null,
      userActivationAtExecCommand: null,
      userAgent: summarizeUserAgent()
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
      var linkPromise = generateNewPublicLink();
      var gestureCopyPromise = copyTextFromPromiseWithinGesture(
        linkPromise.then(function (linkPublico) {
          return buildMessageForLink(linkPublico);
        })
      );
      await linkPromise;
      var copyResult = gestureCopyPromise ? await gestureCopyPromise : null;
      if (!copyResult || !copyResult.ok) {
        copyResult = await copyLastGeneratedLink();
      }
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
        reportClipboardDiagnosis("copy-failed-after-link-generation", lastCopyDiagnosis);
        setButtonVisualState("error");
        setButtonLabel("Enlace generado, pero no se pudo copiar automáticamente");
        showFeedback("Enlace generado, pero no se pudo copiar automáticamente");
        showManualPanel(
          lastGeneratedLink,
          lastGeneratedMessage,
          "Safari no permitió copiar automáticamente. Copia el enlace manualmente."
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
      setButtonLabel("Enlace generado, pero no se pudo copiar automáticamente");
      showFeedback("Enlace generado, pero no se pudo copiar automáticamente");
      showManualPanel(
        lastGeneratedLink,
        lastGeneratedMessage,
        "Safari no permitió copiar automáticamente. Copia el enlace manualmente."
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

  if (selectLinkBtn) {
    selectLinkBtn.addEventListener("click", function () {
      selectManualLinkInput();
    });
  }

  if (closeManualBtn) {
    closeManualBtn.addEventListener("click", function () {
      hideManualPanel();
    });
  }
})();
