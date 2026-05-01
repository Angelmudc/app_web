(function () {
  function buildClientPublicFormMessageNuevo(link) {
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

  function buildClientPublicFormMessageExistente(link) {
    return [
      "Te comparto este formulario para registrar una nueva solicitud en Doméstica del Cibao A&D.",
      "",
      "Este enlace ya está asociado a tu registro, solo debes completar los detalles del servicio que necesitas.",
      "",
      String(link || "").trim(),
      "",
      "Cuando lo completes, avísame que ya terminaste."
    ].join("\n");
  }

  // Compatibilidad legacy: por defecto mantiene el mensaje de cliente nuevo.
  window.buildClientPublicFormMessage = buildClientPublicFormMessageNuevo;
  window.buildClientPublicFormMessageNuevo = buildClientPublicFormMessageNuevo;
  window.buildClientPublicFormMessageExistente = buildClientPublicFormMessageExistente;
})();
