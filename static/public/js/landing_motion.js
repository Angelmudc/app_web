(() => {
  "use strict";

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const prefersReducedMotion = () =>
    Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const animateIfPossible = (node, keyframes, options) => {
    if (!node || typeof node.animate !== "function" || prefersReducedMotion()) return null;
    return node.animate(keyframes, options);
  };

  const initGlobalRevealSystem = () => {
    const nodes = qsa("[data-reveal]");
    if (!nodes.length) return;

    const reducedMotion = prefersReducedMotion();
    const groupedNodes = new Set();
    qsa("[data-reveal-group]").forEach((group) => {
      const groupNodes = qsa("[data-reveal]", group).filter(
        (node) => node.closest("[data-reveal-group]") === group
      );
      groupNodes.forEach((node, index) => {
        groupedNodes.add(node);
        if (!node.style.getPropertyValue("--reveal-delay")) {
          node.style.setProperty("--reveal-delay", `${Math.min(index * 70, 280)}ms`);
        }
      });
    });

    nodes.forEach((node) => {
      if (groupedNodes.has(node) || node.style.getPropertyValue("--reveal-delay")) return;
      const explicitDelay = Number.parseInt(node.dataset.revealDelay || "", 10);
      node.style.setProperty("--reveal-delay", `${Number.isFinite(explicitDelay) ? explicitDelay : 0}ms`);
    });

    const revealNode = (node) => {
      node.classList.add("is-visible");
    };

    const hideNode = (node) => {
      if (node.dataset.motionOnce === "true") return;
      node.classList.remove("is-visible");
    };

    if (reducedMotion || !("IntersectionObserver" in window)) {
      nodes.forEach(revealNode);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && entry.intersectionRatio >= 0.08) {
            revealNode(entry.target);
            if (entry.target.dataset.motionOnce === "true") {
              observer.unobserve(entry.target);
            }
            return;
          }
          hideNode(entry.target);
        });
      },
      { threshold: [0, 0.08, 0.2], rootMargin: "0px 0px 8% 0px" }
    );

    nodes.forEach((node) => observer.observe(node));
  };

  const initSectionSpy = () => {
    const links = qsa("a[data-nav-section]");
    const pill = qs("[data-nav-active-pill]");
    const findSectionAnchor = (section) =>
      section?.querySelector("[data-section-anchor]") ||
      section?.querySelector(".section-heading") ||
      section?.querySelector("h1, h2") ||
      section;
    const linkById = new Map(
      links
        .map((link) => [((link.getAttribute("data-nav-section") || "").trim()), link])
        .filter(([id, link]) => id && link)
    );

    const primarySections = links
      .map((link) => {
        const id = (link.getAttribute("data-nav-section") || "").trim();
        const section = id ? document.getElementById(id) : null;
        const anchor = findSectionAnchor(section);
        return { id, link, section, anchor };
      })
      .filter((item) => item.section);

    const groupedSections = qsa("[data-nav-group]")
      .map((section) => {
        const id = (section.getAttribute("data-nav-group") || "").trim();
        const link = linkById.get(id) || null;
        return {
          id,
          link,
          section,
          anchor: findSectionAnchor(section),
        };
      })
      .filter((item) => item.id && item.link && item.section);

    const sections = [...primarySections, ...groupedSections];
    if (!sections.length) return;

    const navbar = qs(".navbar");
    let syncFrame = 0;

    const syncPill = (link) => {
      links.forEach((item) => item.classList.toggle("is-current", item === link));
      if (!pill || !link) return;
      const pillParent = link.closest(".nav-links-shell") || link.parentElement?.parentElement;
      const parentRect = pillParent?.getBoundingClientRect();
      const rect = link.getBoundingClientRect();
      if (!parentRect || !rect.width) {
        pill.style.opacity = "0";
        return;
      }
      pill.style.width = `${rect.width}px`;
      pill.style.transform = `translateX(${rect.left - parentRect.left}px)`;
      pill.style.opacity = "1";
    };

    let activeId = "";
    const setActiveById = (id) => {
      if (!id || activeId === id) return;
      activeId = id;
      syncPill(linkById.get(id) || null);
    };

    const getHeaderOffset = () => {
      const navbarHeight = navbar?.getBoundingClientRect().height || 0;
      return navbarHeight + 16;
    };

    const getSectionTop = (section) => section.getBoundingClientRect().top + window.scrollY;
    const getAnchorRect = (item) => (item.anchor || item.section).getBoundingClientRect();
    const getSectionsInScrollOrder = () =>
      sections
        .slice()
        .sort((a, b) => getSectionTop(a.section) - getSectionTop(b.section));

    const detectActiveSection = () => {
      const orderedSections = getSectionsInScrollOrder();
      const scrollY = window.scrollY || window.pageYOffset || 0;
      const headerOffset = getHeaderOffset();
      const topLockLimit = Math.max(96, headerOffset + 24);

      if (scrollY <= topLockLimit) {
        return orderedSections[0]?.id || "";
      }

      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      const referenceY = headerOffset + Math.min(viewportHeight * 0.18, 156);
      const docBottom = scrollY + viewportHeight;
      const pageBottom = document.documentElement.scrollHeight - 4;

      if (docBottom >= pageBottom) {
        return orderedSections[orderedSections.length - 1]?.id || "";
      }

      const scoredSections = orderedSections
        .map((item, index) => {
          const sectionRect = item.section.getBoundingClientRect();
          const anchorRect = getAnchorRect(item);
          const distance = Math.abs(anchorRect.top - referenceY);
          const intersectsReference =
            sectionRect.top <= referenceY && sectionRect.bottom >= headerOffset;
          const isVisible =
            sectionRect.bottom > headerOffset && sectionRect.top < viewportHeight;
          const aheadOfReference = anchorRect.top > referenceY;
          const tieBreaker = aheadOfReference ? 8 : 0;

          return {
            id: item.id,
            index,
            distance,
            tieBreaker,
            isVisible,
            intersectsReference,
          };
        })
        .filter((item) => item.isVisible);

      const activeCandidate = scoredSections.sort((a, b) => {
        if (a.intersectsReference !== b.intersectsReference) {
          return a.intersectsReference ? -1 : 1;
        }
        if (a.distance !== b.distance) return a.distance - b.distance;
        if (a.tieBreaker !== b.tieBreaker) return a.tieBreaker - b.tieBreaker;
        return a.index - b.index;
      })[0];

      return activeCandidate?.id || orderedSections[0]?.id || "";
    };

    const syncActiveSection = () => {
      syncFrame = 0;
      setActiveById(detectActiveSection());
    };

    const requestSync = () => {
      if (syncFrame) return;
      syncFrame = window.requestAnimationFrame(syncActiveSection);
    };

    const initialId = sections[0]?.id || "";
    if (initialId) setActiveById(initialId);
    requestSync();
    window.addEventListener("scroll", requestSync, { passive: true });
    window.addEventListener("resize", requestSync, { passive: true });
    window.addEventListener("load", requestSync, { passive: true });
    window.addEventListener("hashchange", requestSync, { passive: true });
  };

  const initServicePreview = () => {
    const shell = qs("[data-service-shell]");
    const preview = qs("[data-service-preview]");
    const chips = qsa("[data-service-option]", shell || document);
    if (!shell || !preview || !chips.length) return;

    const title = qs("[data-service-preview-title]", preview);
    const text = qs("[data-service-preview-text]", preview);
    const microcopy = qs("[data-service-preview-microcopy]", preview);
    const context = qs("[data-service-preview-context]", preview);
    const stat = qs("[data-service-preview-stat]", preview);
    const status = qs("[data-service-preview-status]", preview);
    const label = qs("[data-service-preview-label]", preview);
    let currentOption = "";

    const applyPreview = (chip) => {
      if (!chip) return;
      const nextOption = (chip.dataset.serviceOption || "").trim();
      const nextDescription = (chip.dataset.serviceDescription || "").trim();
      const nextMicrocopy = (chip.dataset.serviceMicrocopy || "").trim();
      const nextContext = (chip.dataset.serviceContext || "").trim();
      const nextStat = (chip.dataset.serviceStat || "").trim();
      const nextTone = (chip.dataset.serviceTone || "").trim();
      if (!nextOption || currentOption === nextOption) return;

      currentOption = nextOption;
      preview.classList.add("is-transitioning");
      preview.style.setProperty(
        "--service-accent",
        ({
          resident: "rgba(21, 83, 183, 0.2)",
          routine: "rgba(21, 83, 183, 0.14)",
          flex: "rgba(26, 161, 189, 0.18)",
          care: "rgba(43, 183, 195, 0.2)",
          senior: "rgba(83, 133, 214, 0.2)",
        }[nextTone] || "rgba(21, 83, 183, 0.18)")
      );

      animateIfPossible(
        preview,
        [
          { opacity: 0.78, transform: "translateY(6px) scale(0.985)" },
          { opacity: 1, transform: "translateY(0) scale(1)" },
        ],
        { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );

      if (title) title.textContent = nextOption;
      if (text) text.textContent = nextDescription || "Cuéntanos tu caso y te orientamos según tu hogar, horario y presupuesto.";
      if (microcopy) microcopy.textContent = nextMicrocopy || "La recomendación visual cambia con cada selección para que la decisión se sienta más concreta.";
      if (context) context.textContent = nextContext || "Rutina del hogar · Horario · Presupuesto";
      if (stat) stat.textContent = nextStat || "Contexto inicial preparado";
      if (status) status.textContent = nextTone === "senior" ? "Enfoque sensible" : "Listo para orientar";
      if (label) label.textContent = nextTone === "care" ? "Niños" : nextTone === "senior" ? "Apoyo" : "Tu hogar";

      window.setTimeout(() => preview.classList.remove("is-transitioning"), 340);
    };

    const activeChip = qs(".service-chip.is-active", shell) || chips[0];
    applyPreview(activeChip);
    chips.forEach((chip) => {
      chip.addEventListener("click", () => applyPreview(chip));
      chip.addEventListener("mouseenter", () => {
        if (window.innerWidth < 920) return;
        animateIfPossible(
          chip,
          [{ transform: "translateY(0)" }, { transform: "translateY(-2px)" }],
          { duration: 180, easing: "ease-out" }
        );
      });
    });
  };

  const initProcessTimeline = () => {
    const shell = qs("[data-process-shell]");
    const steps = qsa("[data-process-step]", shell || document);
    if (!shell || !steps.length) return;

    const totalSteps = steps.length;
    const reducedMotion = prefersReducedMotion();
    let activeIndex = -1;
    let manualLockUntil = 0;

    const getDocumentTop = (node) => node.getBoundingClientRect().top + window.scrollY;
    const cancelStepAnimations = (step) => {
      if (!step || typeof step.getAnimations !== "function") return;
      step.getAnimations().forEach((animation) => animation.cancel());
    };

    const syncProgress = (progress) => {
      const next = `${(progress * 100).toFixed(2)}%`;
      shell.style.setProperty("--process-progress", next);
      shell.style.setProperty("--process-progress-scale", String(progress));
    };

    const revealStep = (index) => {
      const step = steps[index];
      if (!step || step.classList.contains("is-revealed")) return;

      step.classList.add("is-revealed");
      if (!reducedMotion) {
        animateIfPossible(
          step,
          [
            { opacity: 0, transform: "translate3d(0, 42px, 0) scale(0.975)" },
            { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" },
          ],
          { duration: 460, easing: "cubic-bezier(0.22, 1, 0.36, 1)", fill: "both" }
        );
      }
    };

    const syncReveal = () => {
      if (reducedMotion) {
        steps.forEach((step) => step.classList.add("is-revealed"));
        return totalSteps;
      }

      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      // Use a lower viewport anchor so the first card appears as the section enters,
      // instead of waiting until each step reaches the top of the screen.
      const revealLine = window.scrollY + Math.max(viewportHeight * 0.88, viewportHeight - 96);
      let nextRevealedCount = 0;

      steps.forEach((step, index) => {
        const trigger = getDocumentTop(step) + step.offsetHeight * 0.08;
        const shouldReveal = revealLine >= trigger;
        if (shouldReveal) {
          revealStep(index);
          nextRevealedCount = index + 1;
          return;
        }
        cancelStepAnimations(step);
        step.classList.remove("is-revealed");
      });

      return nextRevealedCount;
    };

    const clearActiveStep = () => {
      if (activeIndex === -1 && !steps.some((step) => step.classList.contains("is-active"))) return;
      activeIndex = -1;
      steps.forEach((step) => {
        step.classList.remove("is-active");
        step.removeAttribute("aria-current");
      });
    };

    const activateStep = (index, options = {}) => {
      const nextIndex = Math.max(0, Math.min(index, totalSteps - 1));
      const nextStep = steps[nextIndex];
      if (!nextStep) return;

      const changed = nextIndex !== activeIndex;
      activeIndex = nextIndex;

      steps.forEach((step, stepIndex) => {
        const isActive = stepIndex === nextIndex;
        step.classList.toggle("is-active", isActive);
        if (isActive) step.setAttribute("aria-current", "step");
        else step.removeAttribute("aria-current");
      });

      if (changed) {
        revealStep(nextIndex);
        animateIfPossible(
          nextStep,
          [
            { opacity: 0.88, transform: "translateY(10px) scale(0.992)" },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
        );
      }

      if (options.scrollToStep) {
        manualLockUntil = window.performance.now() + 1200;
        nextStep.scrollIntoView({
          block: "center",
          behavior: prefersReducedMotion() ? "auto" : "smooth",
        });
      }
    };

    const syncFromScroll = () => {
      const revealedCount = syncReveal();

      if (window.performance.now() < manualLockUntil && activeIndex >= 0) {
        syncProgress(totalSteps === 1 ? 1 : activeIndex / (totalSteps - 1));
        return;
      }

      const viewportCenter = window.scrollY + window.innerHeight * 0.52;
      const firstCenter = getDocumentTop(steps[0]) + steps[0].offsetHeight * 0.5;
      const lastCenter = getDocumentTop(steps[totalSteps - 1]) + steps[totalSteps - 1].offsetHeight * 0.5;
      const distance = Math.max(lastCenter - firstCenter, 1);
      const progress = Math.min(Math.max((viewportCenter - firstCenter) / distance, 0), 1);

      if (!revealedCount) {
        syncProgress(0);
        clearActiveStep();
        return;
      }

      let closestIndex = Math.max(0, Math.min(revealedCount - 1, totalSteps - 1));
      let closestDistance = Number.POSITIVE_INFINITY;
      const activePool = steps.slice(0, Math.max(revealedCount, 1));
      activePool.forEach((step, index) => {
        const center = getDocumentTop(step) + step.offsetHeight * 0.5;
        const delta = Math.abs(viewportCenter - center);
        if (delta < closestDistance) {
          closestDistance = delta;
          closestIndex = index;
        }
      });

      syncProgress(progress);
      activateStep(closestIndex);
    };

    if ("IntersectionObserver" in window) {
      const shellObserver = new IntersectionObserver(
        (entries) => {
          shell.classList.toggle("is-inview", Boolean(entries[0]?.isIntersecting));
          if (entries[0]?.isIntersecting) {
            window.requestAnimationFrame(syncFromScroll);
          }
        },
        { threshold: 0.08 }
      );
      shellObserver.observe(shell);
    } else {
      steps.forEach((step) => step.classList.add("is-revealed"));
      shell.classList.add("is-inview");
    }

    steps.forEach((step, index) => {
      step.style.setProperty("--step-order", String(index));
      step.addEventListener("click", () => activateStep(index, { scrollToStep: true }));
      step.addEventListener("keydown", (event) => {
        if (
          event.key !== "ArrowRight" &&
          event.key !== "ArrowLeft" &&
          event.key !== "ArrowDown" &&
          event.key !== "ArrowUp" &&
          event.key !== "Enter" &&
          event.key !== " "
        ) return;
        event.preventDefault();
        if (event.key === "Enter" || event.key === " ") {
          activateStep(index, { scrollToStep: true });
          return;
        }
        const direction = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
        const nextIndex = Math.max(0, Math.min(index + direction, totalSteps - 1));
        steps[nextIndex]?.focus();
        activateStep(nextIndex, { scrollToStep: true });
      });
      step.addEventListener("focusin", () => activateStep(index));
    });

    syncFromScroll();
    let ticking = false;
    const requestSync = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(() => {
        syncFromScroll();
        ticking = false;
      });
    };

    window.addEventListener("scroll", requestSync, { passive: true });
    window.addEventListener("resize", requestSync, { passive: true });
  };

  const initHeroNetwork = () => {
    const hero = qs(".hero");
    const canvas = qs("[data-hero-network]");
    if (!hero || !canvas || prefersReducedMotion()) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;
    let rafId = 0;
    let running = true;
    let inView = true;
    let pointerX = 0;
    let pointerY = 0;

    const nodes = Array.from({ length: 14 }, (_, index) => ({
      seed: index * 91,
      size: 1.2 + (index % 4) * 0.55,
      speed: 0.18 + (index % 5) * 0.03,
      baseX: 0.08 + ((index * 0.07) % 0.84),
      baseY: 0.16 + ((index * 0.11) % 0.66),
    }));

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const drawNode = (x, y, radius) => {
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius * 7);
      gradient.addColorStop(0, "rgba(21, 83, 183, 0.28)");
      gradient.addColorStop(1, "rgba(21, 83, 183, 0)");
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(x, y, radius * 7, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    };

    const render = (now) => {
      if (!running || !inView) return;

      const time = now * 0.001;
      const positions = nodes.map((node, index) => {
        const driftX = Math.sin(time * node.speed + node.seed) * 22 + pointerX * (0.018 + index * 0.0014);
        const driftY = Math.cos(time * (node.speed * 0.9) + node.seed) * 16 + pointerY * (0.014 + index * 0.0011);
        return {
          x: node.baseX * width + driftX,
          y: node.baseY * height + driftY,
          radius: node.size,
        };
      });

      ctx.clearRect(0, 0, width, height);

      for (let i = 0; i < positions.length; i += 1) {
        for (let j = i + 1; j < positions.length; j += 1) {
          const a = positions[i];
          const b = positions[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          if (distance > 185) continue;
          const alpha = 1 - distance / 185;
          ctx.strokeStyle = `rgba(21, 83, 183, ${0.09 * alpha})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }

      positions.forEach((position) => drawNode(position.x, position.y, position.radius));
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

    hero.addEventListener("pointermove", (event) => {
      const rect = hero.getBoundingClientRect();
      pointerX = event.clientX - rect.left - rect.width / 2;
      pointerY = event.clientY - rect.top - rect.height / 2;
    });

    hero.addEventListener("pointerleave", () => {
      pointerX = 0;
      pointerY = 0;
    });

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => {
          const entry = entries[0];
          inView = Boolean(entry?.isIntersecting);
          if (inView) start();
          else stop();
        },
        { threshold: 0.04 }
      );
      observer.observe(hero);
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
    if (!document.body.classList.contains("home-landing")) return;
    initGlobalRevealSystem();
    initSectionSpy();
    initServicePreview();
    initProcessTimeline();
    initHeroNetwork();
  });
})();
