(function () {
  var body = document.body;
  if (!body) return;

  var enabled = String(body.getAttribute("data-client-public-message-island-enabled") || "") === "1";
  if (!enabled) return;

  var btn = document.getElementById("clientPublicMessageIslandBtn");
  var feedbackNode = document.getElementById("clientPublicMessageIslandFeedback");
  var linkUrl = String(body.getAttribute("data-client-public-message-link-url") || "").trim();
  if (!btn || !feedbackNode || !linkUrl) return;

  var feedbackTimer = null;
  var inFlight = false;
  var cooldownUntilMs = 0;
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

  async function copyTextSafe(text) {
    var value = String(text || "").trim();
    if (!value) return false;

    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }

    var tmp = document.createElement("textarea");
    tmp.value = value;
    tmp.setAttribute("readonly", "");
    tmp.style.position = "absolute";
    tmp.style.left = "-9999px";
    document.body.appendChild(tmp);
    tmp.select();
    tmp.setSelectionRange(0, value.length);
    var ok = document.execCommand("copy");
    document.body.removeChild(tmp);
    return !!ok;
  }

  function buildProfessionalMessage(link) {
    return [
      "Este es el formulario de Doméstica del Cibao A&D para registrar tu solicitud.",
      "",
      "Ahí puedes colocar tus datos y lo que necesitas, para poder ayudarte mejor.",
      "",
      link,
      "",
      "Cuando lo completes, envíame tu nombre y dime que ya terminaste."
    ].join("\n");
  }

  async function handleCopyClick() {
    if (inFlight) return;
    if (Date.now() < cooldownUntilMs) return;

    inFlight = true;
    cooldownUntilMs = Date.now() + 5000;
    btn.disabled = true;
    setButtonLabel("Generando enlace...");
    showFeedback("Generando enlace...");

    try {
      var resp = await fetch(linkUrl, {
        method: "GET",
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });
      var payload = {};
      try {
        payload = await resp.json();
      } catch (_err) {
        payload = {};
      }

      if (!resp.ok || !payload.ok || !payload.link_publico) {
        throw new Error("link-generation-failed");
      }

      var message = buildProfessionalMessage(String(payload.link_publico));
      var copied = await copyTextSafe(message);
      if (!copied) {
        throw new Error("copy-failed");
      }
      showFeedback("Mensaje copiado");
    } catch (_err) {
      cooldownUntilMs = Date.now() + 2500;
      showFeedback("No se pudo copiar");
    } finally {
      inFlight = false;
      restoreButtonWhenReady();
    }
  }

  btn.addEventListener("click", function () {
    handleCopyClick();
  });
})();
