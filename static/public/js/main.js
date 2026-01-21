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

    // PRO: Refresco en vivo solo del grid de domésticas
    initLiveDomesticasGrid();
  });
  // ===== Live refresh PRO: actualiza solo el grid de /domesticas sin recargar página =====
  const initLiveDomesticasGrid = () => {
    // Solo aplica a la página pública de domésticas
    if (window.location.pathname !== "/domesticas") return;

    const grid = document.getElementById("domesticasGrid");
    if (!grid) return;

    const statusEl = document.getElementById("liveStatus");

    const REFRESH_EVERY_MS = 30000; // 30s
    const IDLE_REQUIRED_MS = 2500;  // 2.5s sin actividad para refrescar
    const LIMIT = 9;               // igual a tu grilla inicial

    let lastAction = Date.now();
    let lastHash = null;
    let running = false;

    // Actividad del usuario para no interrumpir
    ["scroll", "mousemove", "keydown", "touchstart", "click"].forEach((evt) => {
      window.addEventListener(
        evt,
        () => {
          lastAction = Date.now();
        },
        { passive: true }
      );
    });

    const setStatus = (text, show = true) => {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.style.display = show ? "block" : "none";
    };

    const isModalOpen = () => {
      const modal = document.getElementById("modalImagen");
      if (modal && modal.style.display === "block") return true;
      const openModal = document.querySelector(".modal.show, .modal[style*='display: block']");
      return !!openModal;
    };

    const buildHash = (items) => {
      // Hash estable: si esto cambia, refrescamos el grid
      return JSON.stringify(
        (items || []).map((i) => [
          i.public_id || "",
          i.nombre || "",
          i.edad || "",
          i.modalidad || "",
          i.ciudad || "",
          i.sector || "",
          i.sueldo || "",
          i.destacada ? 1 : 0,
          i.disponible_inmediato ? 1 : 0,
          i.frase_destacada || "",
          i.tags || "",
        ])
      );
    };

    const fetchLiveSnapshot = async () => {
      const url = new URL("/domesticas/live", window.location.origin);
      url.searchParams.set("limit", String(LIMIT));

      // Si en algún momento usas búsqueda por querystring (?q=...)
      const q = new URLSearchParams(window.location.search).get("q");
      if (q) url.searchParams.set("q", q);

      const res = await fetch(url.toString(), { cache: "no-store" });
      return res.json();
    };

    const fetchDomesticasHTML = async () => {
      // Pedimos la misma página (server-render) y extraemos solo el grid
      const url = new URL(window.location.href);
      url.searchParams.set("_ts", String(Date.now())); // evita cache

      const res = await fetch(url.toString(), {
        cache: "no-store",
        headers: { "X-Requested-With": "fetch" },
      });

      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const newGrid = doc.getElementById("domesticasGrid");
      return newGrid ? newGrid.innerHTML : null;
    };

    const swapGridHTML = (newInnerHTML) => {
      if (typeof newInnerHTML !== "string") return;
      // Preservar scroll
      const y = window.scrollY;

      // Swap (suave y rápido)
      grid.style.transition = "opacity 120ms ease";
      grid.style.opacity = "0.92";

      setTimeout(() => {
        grid.innerHTML = newInnerHTML;
        // Restaurar scroll y opacidad
        window.scrollTo(0, y);
        grid.style.opacity = "1";
      }, 140);
    };

    const tick = async () => {
      if (running) return;
      running = true;

      try {
        // No molestar si el usuario está activo
        const idleFor = Date.now() - lastAction;
        if (idleFor < IDLE_REQUIRED_MS) return;

        // No refrescar si hay modal abierto
        if (isModalOpen()) return;

        const live = await fetchLiveSnapshot();
        if (!live || live.ok !== true) return;

        const items = Array.isArray(live.items) ? live.items : [];
        const newHash = buildHash(items);

        // Primera corrida: solo fija hash sin tocar DOM
        if (lastHash === null) {
          lastHash = newHash;
          return;
        }

        // Si no cambió nada, no hacemos nada
        if (newHash === lastHash) return;

        // Cambió: extraemos HTML del servidor y reemplazamos SOLO el grid
        setStatus("Actualizando listado...", true);
        const newInner = await fetchDomesticasHTML();

        if (newInner !== null) {
          swapGridHTML(newInner);
          lastHash = newHash;
          setStatus("Listado actualizado.", true);
          setTimeout(() => setStatus("", false), 800);
        } else {
          setStatus("", false);
        }
      } catch (err) {
        // Silencioso: no rompemos la página
        setStatus("", false);
      } finally {
        running = false;
      }
    };

    // Arranca: fija hash inicial rápido
    tick();
    setInterval(tick, REFRESH_EVERY_MS);
  };

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
