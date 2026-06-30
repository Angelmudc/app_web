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
  const MSG_SERVICIO_GENERAL =
    "Hola, me interesa solicitar un servicio doméstico. Quiero recibir orientación sobre el proceso.";

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const hasReducedMotion = () =>
    Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const openWhatsApp = (message) => {
    const text = (message || "").toString().trim();
    if (!WHATSAPP_NUMBER || !text) return;

    const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(text)}`;

    // noopener/noreferrer: evita que la pestaña nueva pueda acceder a window.opener
    const w = window.open(url, "_blank", "noopener,noreferrer");
    if (w) w.opener = null;
  };

  const safeText = (value) => (value == null ? "" : String(value)).trim();

  const initHeroMotion = (hero, body) => {
    if (!hero || !body || hasReducedMotion()) return;

    let pointerFrame = 0;
    let lastX = 0;
    let lastY = 0;

    const syncPointer = () => {
      body.style.setProperty("--hero-pointer-x", `${lastX.toFixed(1)}px`);
      body.style.setProperty("--hero-pointer-y", `${lastY.toFixed(1)}px`);
      pointerFrame = 0;
    };

    hero.addEventListener("pointermove", (event) => {
      const rect = hero.getBoundingClientRect();
      const relativeX = event.clientX - rect.left - rect.width / 2;
      const relativeY = event.clientY - rect.top - rect.height / 2;
      lastX = relativeX;
      lastY = relativeY;
      if (pointerFrame) return;
      pointerFrame = window.requestAnimationFrame(syncPointer);
    });

    hero.addEventListener("pointerleave", () => {
      lastX = 0;
      lastY = 0;
      if (pointerFrame) window.cancelAnimationFrame(pointerFrame);
      pointerFrame = window.requestAnimationFrame(syncPointer);
    });

    let scrollFrame = 0;
    const syncScroll = () => {
      const rect = hero.getBoundingClientRect();
      const progress = Math.max(-1, Math.min(1, rect.top / Math.max(rect.height, 1)));
      body.style.setProperty("--hero-scroll-shift", `${(progress * -32).toFixed(1)}px`);
      body.style.setProperty("--hero-glow-opacity", String(0.5 + (1 - Math.abs(progress)) * 0.18));
      scrollFrame = 0;
    };

    syncScroll();
    window.addEventListener(
      "scroll",
      () => {
        if (scrollFrame) return;
        scrollFrame = window.requestAnimationFrame(syncScroll);
      },
      { passive: true }
    );
  };

  const initHeroCanvas = (canvas) => {
    if (!canvas || hasReducedMotion()) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;
    let rafId = 0;
    let running = true;
    let inView = true;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * DPR);
      canvas.height = Math.floor(height * DPR);
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    };

    const drawLine = (time, yRatio, amplitude, color, offset) => {
      ctx.beginPath();
      for (let x = 0; x <= width; x += 8) {
        const wave =
          Math.sin((x + time * 0.08 + offset) / 72) * amplitude +
          Math.cos((x + time * 0.04 + offset) / 140) * (amplitude * 0.45);
        const y = height * yRatio + wave;
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    };

    const render = (now) => {
      if (!running || !inView) return;
      const time = now / 16;
      ctx.clearRect(0, 0, width, height);

      drawLine(time, 0.3, 10, "rgba(21, 83, 183, 0.18)", 0);
      drawLine(time, 0.42, 14, "rgba(39, 192, 207, 0.16)", 120);
      drawLine(time, 0.58, 11, "rgba(125, 211, 252, 0.12)", 220);

      for (let i = 0; i < 4; i += 1) {
        const x = ((time * (0.6 + i * 0.07)) + i * 120) % (width + 140) - 70;
        const y = height * (0.24 + i * 0.14) + Math.sin((time + i * 32) / 20) * 10;
        const radius = 2.2 + i * 0.35;
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius * 7);
        gradient.addColorStop(0, "rgba(125, 211, 252, 0.36)");
        gradient.addColorStop(1, "rgba(125, 211, 252, 0)");
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(x, y, radius * 7, 0, Math.PI * 2);
        ctx.fill();
      }

      rafId = window.requestAnimationFrame(render);
    };

    const start = () => {
      if (rafId || !running || !inView) return;
      rafId = window.requestAnimationFrame(render);
    };

    const stop = () => {
      if (!rafId) return;
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    };

    resize();
    start();

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => {
          inView = Boolean(entries[0]?.isIntersecting);
          if (inView) start();
          else stop();
        },
        { threshold: 0.04 }
      );
      observer.observe(canvas);
    }

    document.addEventListener("visibilitychange", () => {
      running = document.visibilityState !== "hidden";
      if (running && inView) start();
      else stop();
    });

    window.addEventListener("resize", resize, { passive: true });
    window.addEventListener(
      "pagehide",
      () => {
        running = false;
        stop();
      },
      { once: true }
    );
  };

  document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;
    const reducedMotion = hasReducedMotion();

    // Año dinámico en el footer
    const yearSpan = qs("#year");
    if (yearSpan) yearSpan.textContent = String(new Date().getFullYear());

    initHeroMotion(qs(".hero"), body);
    initHeroCanvas(qs("[data-hero-canvas]"));

    // Header premium al hacer scroll
    let scrollTicking = false;
    const syncScrollState = () => {
      if (!body) return;
      body.classList.toggle("is-scrolled", window.scrollY > 12);
      scrollTicking = false;
    };
    syncScrollState();
    window.addEventListener(
      "scroll",
      () => {
        if (scrollTicking) return;
        scrollTicking = true;
        window.requestAnimationFrame(syncScrollState);
      },
      { passive: true }
    );

    // Menú responsive
    const navToggle = qs("#navToggle");
    const navLinks = qs("#navLinks");
    if (navToggle && navLinks) {
      const syncMenuState = (isOpen) => {
        navLinks.classList.toggle("show", isOpen);
        navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        navToggle.setAttribute("aria-label", isOpen ? "Cerrar menú" : "Abrir menú");
        document.body.classList.toggle("nav-menu-open", isOpen);
      };

      const closeMenu = () => {
        syncMenuState(false);
      };

      navToggle.addEventListener("click", (e) => {
        e.preventDefault();
        syncMenuState(!navLinks.classList.contains("show"));
      });

      qsa("a", navLinks).forEach((link) => {
        link.addEventListener("click", () => closeMenu());
      });

      document.addEventListener("click", (event) => {
        if (!navLinks.classList.contains("show")) return;
        if (navLinks.contains(event.target) || navToggle.contains(event.target)) return;
        closeMenu();
      });

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && navLinks.classList.contains("show")) {
          closeMenu();
          navToggle.focus();
        }
      });

      window.addEventListener("resize", () => {
        if (window.innerWidth > 960 && navLinks.classList.contains("show")) {
          closeMenu();
        }
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

    // Scroll suave interno
    qsa("[data-scroll-target]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const target = safeText(btn.getAttribute("data-scroll-target"));
        const node = target ? qs(target) : null;
        if (!node) return;
        e.preventDefault();
        node.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    // Selector de servicio en hero
    const serviceOptions = qsa("[data-service-option]");
    const servicePreviewTitle = qs("[data-service-preview-title]");
    const servicePreviewText = qs("[data-service-preview-text]");
    let selectedServiceMessage = "";

    const syncServiceSelection = (activeBtn) => {
      serviceOptions.forEach((item) => {
        const isActive = item === activeBtn;
        item.classList.toggle("is-active", isActive);
        item.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
      selectedServiceMessage = safeText(activeBtn?.dataset.serviceMessage);
      if (servicePreviewTitle) {
        servicePreviewTitle.textContent = safeText(activeBtn?.dataset.serviceOption) || "Elige una modalidad";
      }
      if (servicePreviewText) {
        servicePreviewText.textContent =
          safeText(activeBtn?.dataset.serviceDescription) ||
          "Cuéntanos tu caso y te orientamos según tu hogar, horario y presupuesto.";
      }
    };

    syncServiceSelection(qs("[data-service-option].is-active") || serviceOptions[0]);

    serviceOptions.forEach((btn) => {
      btn.addEventListener("click", () => {
        syncServiceSelection(btn);
      });
    });

    const serviceWhatsApp = qs("[data-service-whatsapp]");
    if (serviceWhatsApp) {
      serviceWhatsApp.addEventListener("click", (e) => {
        e.preventDefault();
        openWhatsApp(selectedServiceMessage || MSG_SERVICIO_GENERAL);
      });
    }

    qsa("[data-service-cta]").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        openWhatsApp(safeText(link.dataset.serviceMessage) || MSG_SERVICIO_GENERAL);
      });
    });

    // Aparicion ligera al hacer scroll
    const revealNodes = qsa("[data-reveal]");
    if (revealNodes.length && !body.classList.contains("home-landing")) {
      revealNodes.forEach((node, index) => {
        node.style.transitionDelay = `${Math.min(index * 35, 220)}ms`;
      });

      if (reducedMotion) {
        revealNodes.forEach((node) => node.classList.add("is-visible"));
      } else if ("IntersectionObserver" in window) {
        const observer = new IntersectionObserver(
          (entries, obs) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return;
              entry.target.classList.add("is-visible");
              obs.unobserve(entry.target);
            });
          },
          { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
        );
        revealNodes.forEach((node) => observer.observe(node));
      } else {
        revealNodes.forEach((node) => node.classList.add("is-visible"));
      }
    }

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
