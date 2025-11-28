// CAMBIA ESTE NÚMERO POR TU WHATSAPP REAL (solo dígitos, con código de país)
const WHATSAPP_NUMBER = "18094296892";

document.addEventListener("DOMContentLoaded", () => {
  // Año dinámico en el footer
  const yearSpan = document.getElementById("year");
  if (yearSpan) {
    yearSpan.textContent = new Date().getFullYear();
  }

  // Menú responsive
  const navToggle = document.getElementById("navToggle");
  const navLinks = document.getElementById("navLinks");

  if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
      navLinks.classList.toggle("show");
    });
  }

  // Botón flotante de WhatsApp (abre chat general)
  const whatsappFloat = document.getElementById("whatsappFloat");
  if (whatsappFloat) {
    whatsappFloat.addEventListener("click", (e) => {
      e.preventDefault();
      const message = "Hola, vi la página de Doméstica del Cibao y necesito información sobre una doméstica.";
      const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
      window.open(url, "_blank");
    });
  }

  // Botones genéricos "data-solicitar-domestica"
  document.querySelectorAll("[data-solicitar-domestica]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const message =
        "Hola, estoy interesado(a) en contratar una doméstica. Quiero que me orienten con las opciones que tienen disponibles.";
      const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
      window.open(url, "_blank");
    });
  });

  // Botones de "Ofrecer empleo a esta candidata"
  document.querySelectorAll(".btn-oferta").forEach((btn) => {
    btn.addEventListener("click", () => {
      const codigo = btn.dataset.codigo || "";
      const nombre = btn.dataset.nombre || "";
      const message =
        `Hola, vi la candidata ${nombre} (código ${codigo}) en la página de Doméstica del Cibao ` +
        `y quiero ofrecerle un empleo. ` +
        `\n\nCiudad: \nSector: \nModalidad (con dormida / sin dormida / por días): \nHorario: \nSueldo ofrecido: ` +
        `\n\nPor favor confirmen si está disponible y cómo podemos seguir.`;
      const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
      window.open(url, "_blank");
    });
  });

  // Formulario de contacto → mandar por WhatsApp
  const btnContacto = document.getElementById("enviarContacto");
  if (btnContacto) {
    btnContacto.addEventListener("click", () => {
      const nombre = document.getElementById("nombre")?.value || "";
      const telefono = document.getElementById("telefono")?.value || "";
      const ciudad = document.getElementById("ciudad")?.value || "";
      const servicio = document.getElementById("servicio")?.value || "";
      const mensaje = document.getElementById("mensaje")?.value || "";

      const text =
        `Hola, quiero información para contratar una doméstica.` +
        `\n\nNombre: ${nombre}` +
        `\nTeléfono: ${telefono}` +
        `\nCiudad / sector: ${ciudad}` +
        `\nServicio que busco: ${servicio || "No especificado"}` +
        `\n\nDetalle: ${mensaje || "Sin mensaje adicional"}`;

      const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(text)}`;
      window.open(url, "_blank");
    });
  }
});
function verImagenCompleta(src) {
  const modal = document.getElementById("modalImagen");
  const imgGrande = document.getElementById("imgModalGrande");
  modal.style.display = "block";
  imgGrande.src = src;
}

function cerrarImagen() {
  document.getElementById("modalImagen").style.display = "none";
}
