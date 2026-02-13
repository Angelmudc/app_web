// Public web JS only (landing / public pages)
(() => {
  "use strict";

  // CAMBIA ESTE NÚMERO POR TU WHATSAPP REAL (solo dígitos, con código de país)
  const WHATSAPP_NUMBER_RAW = "18094296892";
  const WHATSAPP_NUMBER = (WHATSAPP_NUMBER_RAW || "").replace(/\D/g, "") || WHATSAPP_NUMBER_RAW;

  // Mensajes base (puedes ajustarlos sin tocar lógica)
  const MSG_GENERAL =
    "Hola, vi la página de Doméstica del Cibao y necesito información sobre una doméstica.";
  const MSG_SOLICITAR =
    "Hola, estoy interesado(a) en contratar una doméstica. Quiero que me orienten con las opciones que tienen disponibles.";

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const openWhatsApp = (message) => {
    const text = (message || "").toString().trim();
    if (!WHATSAPP_NUMBER || !text) return;

    const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(text)}`;

    // noopener/noreferrer: evita que la pestaña nueva pueda acceder a window.opener
    const w = window.open(url, "_blank", "noopener,noreferrer");
    if (w) w.opener = null;
  };

  const safeText = (value) => (value == null ? "" : String(value)).trim();

  document.addEventListener("DOMContentLoaded", () => {
    // Año dinámico en el footer
    const yearSpan = qs("#year");
    if (yearSpan) yearSpan.textContent = String(new Date().getFullYear());

    // Menú responsive
    const navToggle = qs("#navToggle");
    const navLinks = qs("#navLinks");
    if (navToggle && navLinks) {
      navToggle.addEventListener("click", (e) => {
        e.preventDefault();
        navLinks.classList.toggle("show");
      });
    }

    // Botón flotante de WhatsApp (abre chat general)
    const whatsappFloat = qs("#whatsappFloat");
    if (whatsappFloat) {
      whatsappFloat.addEventListener("click", (e) => {
        e.preventDefault();
        openWhatsApp(MSG_GENERAL);
      });
    }

    // Botones genéricos "data-solicitar-domestica"
    qsa("[data-solicitar-domestica]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        openWhatsApp(MSG_SOLICITAR);
      });
    });

    // Botones de "Ofrecer empleo a esta candidata"
    qsa(".btn-oferta").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();

        const codigo = safeText(btn.dataset.codigo);
        const nombre = safeText(btn.dataset.nombre);

        const message =
          `Hola, vi la candidata ${nombre || "(sin nombre)"} (código ${codigo || "N/D"}) en la página de Doméstica del Cibao ` +
          `y quiero ofrecerle un empleo.` +
          `\n\nCiudad: \nSector: \nModalidad (con dormida / sin dormida / por días): \nHorario: \nSueldo ofrecido: ` +
          `\n\nPor favor confirmen si está disponible y cómo podemos seguir.`;

        openWhatsApp(message);
      });
    });

    // Formulario de contacto → mandar por WhatsApp
    const btnContacto = qs("#enviarContacto");
    if (btnContacto) {
      btnContacto.addEventListener("click", (e) => {
        e.preventDefault();

        const nombre = safeText(qs("#nombre")?.value);
        const telefono = safeText(qs("#telefono")?.value);
        const ciudad = safeText(qs("#ciudad")?.value);
        const servicio = safeText(qs("#servicio")?.value);
        const mensaje = safeText(qs("#mensaje")?.value);

        const text =
          `Hola, quiero información para contratar una doméstica.` +
          `\n\nNombre: ${nombre || "No indicado"}` +
          `\nTeléfono: ${telefono || "No indicado"}` +
          `\nCiudad / sector: ${ciudad || "No indicado"}` +
          `\nServicio que busco: ${servicio || "No especificado"}` +
          `\n\nDetalle: ${mensaje || "Sin mensaje adicional"}`;

        openWhatsApp(text);
      });
    }
  });

  // ===== Modal de imagen (solo si existe en el HTML) =====
  // Nota: mantenemos los nombres globales por compatibilidad con onClick en templates,
  // pero están blindados para no romper si el modal no existe.

  window.verImagenCompleta = (src) => {
    const modal = document.getElementById("modalImagen");
    const imgGrande = document.getElementById("imgModalGrande");
    if (!modal || !imgGrande) return;

    modal.style.display = "block";
    imgGrande.src = src || "";
  };

  window.cerrarImagen = () => {
    const modal = document.getElementById("modalImagen");
    if (!modal) return;
    modal.style.display = "none";
  };
})();