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
      "Hola, gracias por comunicarte con Doméstica del Cibao A&D.",
      "",
      "Para continuar con el proceso, te compartimos nuestro formulario oficial de cliente. Este formulario nos permite registrar tus datos y gestionar tu solicitud de manera organizada y segura.",
      "",
      "Por favor complétalo aquí:",
      link,
      "",
      "La información enviada será usada únicamente para gestionar tu solicitud con Doméstica del Cibao A&D."
    ].join("\n");
  }

  async function handleCopyClick() {
    btn.disabled = true;
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
      showFeedback("Error al copiar");
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", function () {
    handleCopyClick();
  });
})();
