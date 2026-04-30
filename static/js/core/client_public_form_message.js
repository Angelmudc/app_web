(function () {
  function buildClientPublicFormMessage(link) {
    return [
      "Este es el formulario de Doméstica del Cibao A&D para registrar tu solicitud.",
      "",
      "Ahí puedes colocar tus datos y lo que necesitas, para poder ayudarte mejor.",
      "",
      String(link || "").trim(),
      "",
      "Cuando lo completes, envíame tu nombre y dime que ya terminaste."
    ].join("\n");
  }

  window.buildClientPublicFormMessage = buildClientPublicFormMessage;
})();
