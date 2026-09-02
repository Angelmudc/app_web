// static/js/admin/candidatas_operativo_detail_ui.js
// UI helper para candidatas_operativo_detail (idempotente y compatible con PJAX).
(function () {
  "use strict";

  if (window.AdminCandidataDetailUI) return;

  function getDetailRoot(scope) {
    const root = scope && scope.querySelector ? scope : document;
    return root.querySelector("[data-candidata-center]");
  }

  function showStickyIdentityBar(detailRoot) {
    const sticky = detailRoot.querySelector("[data-cand-identity-sticky]");
    const hero = detailRoot.querySelector(".detail-hero");
    if (!sticky || !hero) return;

    const cleanup = typeof window.__candIdentityStickyCleanup === "function" ? window.__candIdentityStickyCleanup : null;
    if (cleanup) cleanup();

    let observer = null;
    let scrollHandler = null;
    let resizeHandler = null;
    let rafId = 0;
    let hidden = true;

    function setVisible(visible) {
      const nextVisible = !!visible;
      if (hidden === !nextVisible) return;
      hidden = !nextVisible;
      sticky.hidden = !nextVisible;
      sticky.setAttribute("aria-hidden", nextVisible ? "false" : "true");
      sticky.classList.toggle("is-visible", nextVisible);
    }

    function updateFromHero() {
      const rect = hero.getBoundingClientRect();
      const heroVisible = rect.bottom > 0 && rect.top < window.innerHeight;
      setVisible(!heroVisible);
    }

    if (typeof window.IntersectionObserver === "function") {
      observer = new IntersectionObserver((entries) => {
        const entry = entries && entries[0] ? entries[0] : null;
        if (!entry) return;
        setVisible(!entry.isIntersecting);
      }, { threshold: 0 });
      observer.observe(hero);
      updateFromHero();
    } else {
      scrollHandler = function () {
        if (rafId) return;
        rafId = window.requestAnimationFrame(function () {
          rafId = 0;
          updateFromHero();
        });
      };
      resizeHandler = scrollHandler;
      window.addEventListener("scroll", scrollHandler, { passive: true });
      window.addEventListener("resize", resizeHandler);
      updateFromHero();
    }

    window.__candIdentityStickyCleanup = function () {
      if (observer) observer.disconnect();
      if (scrollHandler) window.removeEventListener("scroll", scrollHandler);
      if (resizeHandler) window.removeEventListener("resize", resizeHandler);
      if (rafId) window.cancelAnimationFrame(rafId);
      sticky.hidden = true;
      sticky.setAttribute("aria-hidden", "true");
      sticky.classList.remove("is-visible");
    };
  }

  function bindInlineSearch(detailRoot) {
    const shell = detailRoot.querySelector("[data-cand-inline-search]");
    if (!shell || shell.dataset.inlineSearchBound === "1") return;
    shell.dataset.inlineSearchBound = "1";
    window.__candInlineSearchShell = shell;

    const input = shell.querySelector("input");
    const results = shell.querySelector('[role="listbox"]');
    const searchUrl = shell.dataset.searchUrl || "";
    let timer = null;
    let items = [];
    let active = 0;
    let controller = null;
    let requestSeq = 0;

    function hide() {
      results.hidden = true;
    }

    function openItem(index) {
      const item = items[index];
      if (item && item.detail_url) {
        if (window.AdminNav && typeof window.AdminNav.navigateTo === "function") {
          window.AdminNav.navigateTo(item.detail_url);
        } else {
          window.location.href = item.detail_url;
        }
      }
    }

    function render() {
      results.innerHTML = "";
      if (!items.length) {
        hide();
        return;
      }
      items.forEach((item, index) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = index === active ? "is-active" : "";
        btn.setAttribute("role", "option");
        btn.setAttribute("aria-selected", index === active ? "true" : "false");
        const meta = [item.codigo || "sin código", item.edad ? item.edad + " años" : "", item.telefono || "", item.estado_label || ""]
          .filter(Boolean)
          .join(" · ");
        btn.innerHTML = "<strong></strong><div class=\"small cand-muted\"></div>";
        btn.querySelector("strong").textContent = item.nombre || "Sin nombre";
        btn.querySelector(".small").textContent = meta;
        btn.addEventListener("click", () => openItem(index));
        results.appendChild(btn);
      });
      results.hidden = false;
    }

    function search() {
      const q = input.value.trim();
      const seq = ++requestSeq;
      if (q.length < 2 || !searchUrl) {
        if (controller) controller.abort();
        items = [];
        render();
        return;
      }
      if (controller) controller.abort();
      controller = new AbortController();
      const url = new URL(searchUrl, window.location.origin);
      url.searchParams.set("q", q);
      url.searchParams.set("limit", "7");
      fetch(url.toString(), { headers: { Accept: "application/json" }, signal: controller.signal })
        .then((resp) => resp.ok ? resp.json() : Promise.reject(new Error("search_failed")))
        .then((payload) => {
          if (seq !== requestSeq) return;
          items = Array.isArray(payload.items) ? payload.items : [];
          active = 0;
          render();
        })
        .catch((err) => {
          if (err && err.name === "AbortError") return;
          if (seq !== requestSeq) return;
          items = [];
          render();
        });
    }

    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 220);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        hide();
      } else if (event.key === "ArrowDown" && items.length) {
        event.preventDefault();
        active = Math.min(items.length - 1, active + 1);
        render();
      } else if (event.key === "ArrowUp" && items.length) {
        event.preventDefault();
        active = Math.max(0, active - 1);
        render();
      } else if (event.key === "Enter" && items.length && !results.hidden) {
        event.preventDefault();
        openItem(active);
      }
    });
    if (!document.__candInlineSearchDocBound) {
      document.__candInlineSearchDocBound = true;
      document.addEventListener("click", (event) => {
        const activeShell = window.__candInlineSearchShell;
        if (!activeShell || !activeShell.contains || activeShell.contains(event.target)) return;
        const activeResults = activeShell.querySelector('[role="listbox"]');
        if (activeResults) activeResults.hidden = true;
      });
    }
  }

  function csrfToken() {
    const meta = document.querySelector("meta[name='csrf-token']");
    return meta ? String(meta.content || "") : "";
  }

  function setFeedback(form, message, ok) {
    const el = form.querySelector("[data-feedback]");
    if (!el) return;
    el.textContent = message || "";
    el.className = "small cand-feedback " + (ok ? "text-success" : "text-danger");
  }

  function clearErrors(form) {
    form.querySelectorAll("[data-error-for]").forEach((el) => { el.textContent = ""; });
  }

  function paintErrors(form, errors) {
    Object.keys(errors || {}).forEach((key) => {
      const el = form.querySelector('[data-error-for="' + key + '"]');
      if (el) el.textContent = errors[key] || "";
    });
  }

  function clearGlobalLoaders() {
    try {
      if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
        window.AppLoader.hideAll();
      } else if (window.AppLoader && typeof window.AppLoader.hide === "function") {
        window.AppLoader.hide();
      }
    } catch (_) {}
    ["globalLoader", "appGlobalLoader", "loader", "pageLoader", "loadingOverlay", "overlayLoader"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.style.display = "none";
    });
    document.documentElement.classList.remove("is-loading");
    if (document.body) document.body.classList.remove("is-loading");
  }

  function setFormBusy(form, isBusy, submitter) {
    if (!form) return;
    if (isBusy) {
      form.dataset.quickBusy = "1";
      form.setAttribute("aria-busy", "true");
    } else {
      delete form.dataset.quickBusy;
      form.removeAttribute("aria-busy");
    }
    form.querySelectorAll("button, input, select, textarea").forEach((field) => {
      if (field.type === "hidden") return;
      if (isBusy) {
        field.dataset.quickPrevDisabled = field.disabled ? "1" : "0";
        field.disabled = true;
      } else {
        if (field.dataset.quickPrevDisabled === "0") field.disabled = false;
        delete field.dataset.quickPrevDisabled;
      }
    });
    if (submitter && submitter.tagName === "BUTTON") {
      if (isBusy) {
        submitter.dataset.quickPrevText = submitter.textContent || "";
        submitter.textContent = "Guardando...";
      } else if (submitter.dataset.quickPrevText) {
        submitter.textContent = submitter.dataset.quickPrevText;
        delete submitter.dataset.quickPrevText;
      }
    }
  }

  async function fetchJsonWithTimeout(url, options, timeoutMs) {
    const controller = window.AbortController ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs || 15000) : null;
    try {
      const resp = await fetch(url, Object.assign({}, options || {}, {
        signal: controller ? controller.signal : undefined,
      }));
      const raw = await resp.text();
      let payload = null;
      try {
        payload = raw ? JSON.parse(raw) : {};
      } catch (_) {
        payload = {
          ok: false,
          message: resp.ok ? "Respuesta inválida del servidor." : "No se pudo guardar.",
          errors: {},
        };
      }
      return { resp, payload };
    } finally {
      if (timer) window.clearTimeout(timer);
    }
  }

  function renderKv(target, values, fallback) {
    if (!target) return;
    const entries = Object.entries(values || {}).filter(([, value]) => String(value || "").trim() !== "");
    if (!entries.length && fallback) {
      target.innerHTML = "";
      target.textContent = fallback;
      return;
    }
    target.innerHTML = "";
    entries.forEach(([label, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = String(value || "");
      target.appendChild(dt);
      target.appendChild(dd);
    });
  }

  function ensureFinanceIdempotencyKeys(detailRoot) {
    detailRoot.querySelectorAll('[data-finance-panel] input[name="idempotency_key"]').forEach((input) => {
      if (!input.value) {
        input.value = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
      }
    });
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch] || ch));
  }

  function renderReferenceCardHtml(title, fullText, emptyLabel, cardKey) {
    const text = String(fullText || "").trim();
    if (text) {
      return [
        `<details class="cand-ref-card" data-cand-ref-card="${escapeHtml(cardKey || "")}">`,
        '<summary class="cand-ref-card__summary">',
        '<div class="cand-ref-card__summary-main">',
        `<strong class="cand-ref-card__title">${escapeHtml(title)}</strong>`,
        "</div>",
        '<span class="cand-ref-card__toggle" aria-hidden="true">',
        '<span class="cand-ref-card__toggle-closed">Ver completa</span>',
        '<span class="cand-ref-card__toggle-open">Ver menos</span>',
        "</span>",
        "</summary>",
        '<div class="cand-ref-card__body">',
        `<div class="cand-ref-card__text white-space-pre-line">${escapeHtml(text)}</div>`,
        "</div>",
        "</details>",
      ].join("");
    }
    return [
      `<article class="cand-ref-card cand-ref-card--empty" data-cand-ref-card="${escapeHtml(cardKey || "")}">`,
      '<div class="cand-ref-card__summary-main">',
      `<strong class="cand-ref-card__title">${escapeHtml(title)}</strong>`,
      "</div>",
      `<div class="cand-muted small">${escapeHtml(emptyLabel || "No informado")}</div>`,
      "</article>",
    ].join("");
  }

  function refreshReferenceCards(detailRoot, display) {
    const refs = display || {};
    if (Object.prototype.hasOwnProperty.call(refs, "references")) {
      [
        ["form-laboral", (refs.references || {}).laboral_full || "", "Laboral"],
        ["form-familiar", (refs.references || {}).familiar_full || "", "Familiar"],
      ].forEach(([key, fullText, title]) => {
        const card = detailRoot.querySelector('[data-cand-ref-card="' + key + '"]');
        if (!card) return;
        card.outerHTML = renderReferenceCardHtml(title, fullText, "No informado", key);
      });
    }
    if (Object.prototype.hasOwnProperty.call(refs, "secretary_references")) {
      [
        ["secretary-laboral", (refs.secretary_references || {}).laboral_full || "", "Laboral"],
        ["secretary-familiar", (refs.secretary_references || {}).familiar_full || "", "Familiar"],
      ].forEach(([key, fullText, title]) => {
        const card = detailRoot.querySelector('[data-cand-ref-card="' + key + '"]');
        if (!card) return;
        card.outerHTML = renderReferenceCardHtml(title, fullText, "No informado", key);
      });
    }
  }

  function syncIdentity(detailRoot, payload) {
    const header = payload.header || {};
    const name = detailRoot.querySelector('[data-cand-header="nombre"]');
    const age = detailRoot.querySelector('[data-cand-header="edad"]');
    const phone = detailRoot.querySelector('[data-cand-header="telefono"]');
    const code = detailRoot.querySelector('[data-cand-header="codigo"]');
    const stickyName = detailRoot.querySelector("[data-cand-identity-name]");
    const stickyCode = detailRoot.querySelector("[data-cand-identity-code]");
    const stickyState = detailRoot.querySelector("[data-cand-identity-state]");
    const breadcrumbName = detailRoot.querySelector("[data-cand-breadcrumb-name]");
    if (name) name.textContent = header.nombre || "";
    if (age) age.textContent = header.edad || "edad no informada";
    if (phone) phone.textContent = header.telefono || "sin teléfono";
    if (code) code.textContent = header.codigo || "sin código";
    if (stickyName) stickyName.textContent = header.nombre || "";
    if (stickyCode) stickyCode.textContent = header.codigo || "sin código";
    if (stickyState) stickyState.textContent = header.estado_label || header.estado || "";
    if (breadcrumbName) breadcrumbName.textContent = header.nombre || "";
    if (header.nombre) document.title = header.nombre + " · Domésticas";
  }

  function refreshFinance(detailRoot, payload) {
    const finance = payload.porciento || {};
    renderKv(detailRoot.querySelector('[data-display="porciento"]'), {
      "Monto total": finance.monto_total || "—",
      "Pagado": finance.pagado || "—",
      "Pendiente": finance.pendiente || "—",
      "Último pago": finance.ultimo_pago || "—",
      "Estado": finance.state || "—",
    }, "");
    const badge = detailRoot.querySelector("[data-porciento-state]");
    if (badge) {
      badge.textContent = finance.state || "Sin cálculo de porciento";
      badge.className = "badge " + (
        finance.state === "Pagado"
          ? "text-bg-success"
          : finance.configurado
            ? "text-bg-warning"
            : "text-bg-secondary"
      );
    }
    const registerButton = detailRoot.querySelector('[data-finance-open="pago"]');
    if (registerButton) {
      registerButton.disabled = !finance.configurado || finance.state === "Pagado";
    }
    const historyRoot = detailRoot.querySelector("[data-finance-history]");
    if (historyRoot) {
      const items = Array.isArray(finance.history) ? finance.history : [];
      if (!items.length) {
        historyRoot.innerHTML = '<div class="cand-muted small">Sin pagos registrados.</div>';
      } else {
        const list = document.createElement("ul");
        list.className = "mb-0 small";
        items.forEach((item) => {
          const li = document.createElement("li");
          li.className = "mb-2";
          const line = document.createElement("div");
          line.innerHTML = "";
          const strong = document.createElement("strong");
          strong.textContent = item.fecha || "—";
          line.appendChild(strong);
          line.appendChild(document.createTextNode(" · " + (item.monto || "—")));
          li.appendChild(line);
          const meta = document.createElement("div");
          meta.className = "cand-muted";
          meta.textContent = (item.metodo || "Sin método") + " · Registrado por " + (item.actor || "staff");
          li.appendChild(meta);
          if (item.detalle) {
            const detail = document.createElement("div");
            detail.className = "cand-muted";
            detail.textContent = item.detalle;
            li.appendChild(detail);
          }
          list.appendChild(li);
        });
        historyRoot.innerHTML = "";
        historyRoot.appendChild(list);
      }
    }
    ensureFinanceIdempotencyKeys(detailRoot);
  }

  function refreshFormValues(detailRoot, payload) {
    const values = payload.values || {};
    Object.keys(values).forEach((section) => {
      const panel = detailRoot.querySelector('[data-edit-section="' + section + '"]');
      if (!panel) return;
      const sectionValues = values[section] || {};
      Object.keys(sectionValues).forEach((name) => {
        const field = panel.querySelector('[name="' + name + '"]');
        if (field) field.value = sectionValues[name] || "";
      });
    });
  }

  function refreshReadiness(detailRoot, readiness) {
    if (!readiness) return;
    const badge = detailRoot.querySelector("[data-readiness-badge]");
    if (badge) {
      badge.textContent = readiness.ready ? "Lista" : "Pendiente";
      badge.className = "badge " + (readiness.ready ? "text-bg-success" : "text-bg-warning");
    }
    const count = detailRoot.querySelector("[data-readiness-count]");
    if (count) {
      count.textContent = (readiness.completed || 0) + "/" + (readiness.total || 0) + " completos";
    }
    const flagsRoot = detailRoot.querySelector("[data-readiness-flags]");
    if (flagsRoot) {
      flagsRoot.innerHTML = "";
      Object.keys(readiness.flags || {}).forEach((key) => {
        const ok = Boolean(readiness.flags[key]);
        const row = document.createElement("div");
        const label = document.createElement("span");
        const status = document.createElement("strong");
        row.className = "cand-check";
        label.textContent = (readiness.labels || {})[key] || key;
        status.className = ok ? "text-success" : "text-danger";
        status.textContent = ok ? "✓" : "Falta";
        row.appendChild(label);
        row.appendChild(status);
        flagsRoot.appendChild(row);
      });
    }
    const reasonsRoot = detailRoot.querySelector("[data-readiness-reasons]");
    if (reasonsRoot) {
      const reasons = readiness.reasons || [];
      reasonsRoot.classList.toggle("d-none", reasons.length === 0);
      const list = reasonsRoot.querySelector("ul");
      if (list) {
        list.innerHTML = "";
        reasons.forEach((reason) => {
          const li = document.createElement("li");
          li.textContent = reason;
          list.appendChild(li);
        });
      }
    }
  }

  function refreshStateCapabilities(detailRoot, payload) {
    const caps = payload.state_capabilities || {};
    const prep = caps.preparation || {};
    const assignment = caps.assignment || {};
    const situation = caps.situation || {};
    const process = caps.process || {};
    const actions = caps.actions || {};
    const reasons = caps.reasons || {};

    const processEl = detailRoot.querySelector("[data-state-process]");
    const prepEl = detailRoot.querySelector("[data-state-preparation]");
    const missingEl = detailRoot.querySelector("[data-state-missing]");
    const situationEl = detailRoot.querySelector("[data-state-situation]");
    const noteEl = detailRoot.querySelector("[data-state-note]");
    if (processEl) processEl.textContent = process.label || "";
    if (prepEl) prepEl.textContent = (prep.label || "") + " materiales";
    if (missingEl) {
      const missing = prep.missing || [];
      const labels = prep.labels || {};
      missingEl.textContent = missing.length
        ? "Falta " + missing.slice(0, 2).map((key) => labels[key] || key).join(", ")
        : "Requisitos materiales completos";
    }
    if (situationEl) situationEl.textContent = situation.label || "";
    if (noteEl) noteEl.textContent = situation.nota_descalificacion ? "Motivo: " + situation.nota_descalificacion : "";

    const blockers = detailRoot.querySelector("[data-state-blockers]");
    if (blockers) {
      blockers.innerHTML = "";
      const items = prep.operational_blockers || [];
      (items.length ? items : ["Sin bloqueos operativos."]).forEach((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        blockers.appendChild(li);
      });
    }

    const assignmentEl = detailRoot.querySelector("[data-state-assignment]");
    if (assignmentEl) {
      const sol = assignment.solicitud || null;
      assignmentEl.textContent = sol
        ? "Solicitud: " + (sol.codigo || ("#" + sol.id)) + " · Estado: " + (sol.estado || "") + (sol.status ? " · " + sol.status : "")
        : "Sin asignación activa.";
    }

    const actionMap = {
      can_mark_ready: "estado/lista",
      can_mark_working: "estado/trabajando",
      can_reactivate: "estado/reactivar",
    };
    Object.keys(actionMap).forEach((key) => {
      detailRoot.querySelectorAll("[data-state-action]").forEach((form) => {
        const endpoint = form.getAttribute("data-endpoint") || "";
        if (!endpoint.includes(actionMap[key])) return;
        const button = form.querySelector('button[type="submit"]');
        if (button) button.disabled = !actions[key];
      });
    });
    const disqualify = detailRoot.querySelector('[data-bs-target="#candDisqualifyModal"]');
    if (disqualify) disqualify.disabled = !actions.can_disqualify;

    const reasonsRoot = detailRoot.querySelector("[data-state-action-reasons]");
    if (reasonsRoot) {
      reasonsRoot.innerHTML = "";
      Object.keys(reasons).forEach((key) => {
        const list = reasons[key] || [];
        if (!list.length) return;
        const div = document.createElement("div");
        div.setAttribute("data-action-reason", key);
        div.textContent = list.join(" ");
        reasonsRoot.appendChild(div);
      });
      reasonsRoot.classList.toggle("d-none", reasonsRoot.children.length === 0);
    }
  }

  function refreshStatusBadges(detailRoot, payload) {
    const target = detailRoot.querySelector("[data-status-badges]");
    if (!target) return;
    const badges = payload.status_badges || {};
    const header = payload.header || {};
    const state = (header.estado_label || (payload.candidate || {}).estado || header.estado || "").trim();
    target.innerHTML = "";
    function addBadge(text, klass) {
      const span = document.createElement("span");
      span.className = "badge " + klass;
      span.textContent = text;
      target.appendChild(span);
    }
    if (state) addBadge(state.replaceAll("_", " "), "text-bg-light border");
    if (badges.inscrita && state !== "Inscrita") addBadge("Inscrita", "text-bg-primary");
    if (badges.lista) addBadge("Lista", "text-bg-success");
    if (badges.trabajando && state !== "Trabajando") addBadge("Trabajando", "text-bg-info");
    if (badges.descalificada && state !== "Descalificada") addBadge("Descalificada", "text-bg-danger");
  }

  function refreshRecentCalls(detailRoot, calls) {
    if (!Array.isArray(calls)) return;
    const list = detailRoot.querySelector("[data-recent-calls]");
    if (!list) return;
    list.innerHTML = "";
    calls.forEach((call) => {
      const li = document.createElement("li");
      const strong = document.createElement("strong");
      strong.textContent = call.fecha || "";
      li.appendChild(strong);
      li.appendChild(document.createTextNode(" · " + (call.agente || "") + " · " + (call.resultado || "")));
      if (call.notas) {
        const notes = document.createElement("div");
        notes.className = "cand-muted";
        notes.textContent = call.notas;
        li.appendChild(notes);
      }
      list.appendChild(li);
    });
    const empty = detailRoot.querySelector("[data-no-calls]");
    if (empty) empty.classList.toggle("d-none", calls.length > 0);
  }

  function refreshDocuments(detailRoot, payload) {
    if (!payload || !payload.doc_flags) return;
    const flags = payload.doc_flags || {};
    detailRoot.querySelectorAll("[data-doc-upload-form]").forEach((form) => {
      const key = form.getAttribute("data-doc-key") || "";
      const ok = Boolean(flags[key]);
      const status = form.querySelector("[data-doc-status]");
      const hint = form.querySelector("[data-doc-hint]");
      const view = form.querySelector('[data-doc-action="view"]');
      const download = form.querySelector('[data-doc-action="download"]');
      const pick = form.querySelector('[data-doc-action="pick"]');
      form.classList.toggle("is-available", ok);
      if (status) {
        status.textContent = ok ? "Disponible" : "Pendiente";
        status.className = "badge " + (ok ? "text-bg-success" : "text-bg-warning");
      }
      if (hint) {
        hint.textContent = ok ? "Arrastra otro archivo para reemplazarlo." : "Arrastra aquí o haz clic para subir.";
      }
      if (view) view.classList.toggle("d-none", !ok);
      if (download) download.classList.toggle("d-none", !ok);
      if (pick) pick.textContent = ok ? "Reemplazar" : "Subir";
    });
  }

  function toggleFinancePanel(detailRoot, key) {
    const panel = detailRoot.querySelector('[data-finance-panel="' + key + '"]');
    if (!panel) return;
    panel.open = true;
    panel.scrollIntoView({ block: "center", behavior: "smooth" });
    const first = panel.querySelector("input, textarea, select, button");
    if (first) window.setTimeout(() => first.focus(), 120);
  }

  function applyPayload(detailRoot, payload) {
    syncIdentity(detailRoot, payload);

    const display = payload.display || {};
    if (Object.prototype.hasOwnProperty.call(display, "personal")) {
      renderKv(detailRoot.querySelector('[data-display="personal"]'), display.personal, "");
    }
    if (Object.prototype.hasOwnProperty.call(display, "labor")) {
      renderKv(detailRoot.querySelector('[data-display="labor"]'), display.labor, "Sin información laboral registrada.");
    }
    if (Object.prototype.hasOwnProperty.call(display, "references")) {
      renderKv(detailRoot.querySelector('[data-display="references"]'), {
        Laborales: (display.references || {}).laboral || "No informado",
        Familiares: (display.references || {}).familiar || "No informado",
      }, "");
      refreshReferenceCards(detailRoot, display);
    }
    if (Object.prototype.hasOwnProperty.call(display, "secretary_references")) {
      renderKv(detailRoot.querySelector('[data-display="secretary-references"]'), {
        Laborales: (display.secretary_references || {}).laboral || "No informado",
        Familiares: (display.secretary_references || {}).familiar || "No informado",
      }, "");
      refreshReferenceCards(detailRoot, display);
    }
    if (Object.prototype.hasOwnProperty.call(payload, "inscription")) {
      const inscription = payload.inscription || {};
      renderKv(detailRoot.querySelector('[data-display="inscription"]'), {
        Código: inscription.codigo || "—",
        Estado: inscription.inscrita ? "Inscrita" : "No inscrita",
        "Medio de pago": inscription.medio || "—",
        "Pago de inscripción": inscription.monto || "—",
        "Fecha de inscripción": inscription.fecha || "—",
        "Inscrita por": inscription.inscrita_por || "—",
      }, "");
    }
    if (Object.prototype.hasOwnProperty.call(payload, "porciento")) refreshFinance(detailRoot, payload);
    if (Object.prototype.hasOwnProperty.call(payload, "doc_flags")) refreshDocuments(detailRoot, payload);
    if (Object.prototype.hasOwnProperty.call(payload, "readiness")) refreshReadiness(detailRoot, payload.readiness);
    if (Object.prototype.hasOwnProperty.call(payload, "state_capabilities")) refreshStateCapabilities(detailRoot, payload);
    if (Object.prototype.hasOwnProperty.call(payload, "status_badges")) refreshStatusBadges(detailRoot, payload);
    if (Object.prototype.hasOwnProperty.call(payload, "recent_calls")) refreshRecentCalls(detailRoot, payload.recent_calls);
    refreshFormValues(detailRoot, payload);
  }

  function bindDetail(detailRoot) {
    const root = detailRoot;
    if (!root || root.dataset.candDetailUiBound === "1") return;
    root.dataset.candDetailUiBound = "1";

    showStickyIdentityBar(root);
    bindInlineSearch(root);

    if (!document.__candIdentityStickyNavBound) {
      document.__candIdentityStickyNavBound = true;
      document.addEventListener("admin:navigation-complete", function (ev) {
        const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
        const nextRoot = detail.viewport || document;
        const fresh = getDetailRoot(nextRoot);
        if (fresh) bindDetail(fresh);
      });
      document.addEventListener("admin:content-updated", function (ev) {
        const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
        const nextRoot = detail.container || document;
        const fresh = getDetailRoot(nextRoot);
        if (fresh) bindDetail(fresh);
      });
    }

    root.addEventListener("click", (event) => {
      const financeOpen = event.target.closest("[data-finance-open]");
      if (financeOpen) {
        toggleFinancePanel(root, financeOpen.getAttribute("data-finance-open") || "");
      }
      const financeClose = event.target.closest("[data-finance-close]");
      if (financeClose) {
        const panel = financeClose.closest("[data-finance-panel]");
        if (panel) panel.open = false;
      }
      const toggle = event.target.closest("[data-edit-toggle]");
      if (toggle) {
        const panel = toggle.closest("[data-edit-section]");
        if (panel) panel.classList.add("cand-editing");
      }
      const shortcut = event.target.closest("[data-edit-shortcut]");
      if (shortcut) {
        const key = shortcut.getAttribute("data-edit-shortcut") || "";
        const panel = root.querySelector('[data-edit-section="' + key + '"]');
        if (panel) {
          panel.classList.add("cand-editing");
          panel.scrollIntoView({ block: "center", behavior: "smooth" });
          const first = panel.querySelector("input, textarea, select, button");
          if (first) window.setTimeout(() => first.focus(), 180);
        }
      }
      const cancel = event.target.closest("[data-edit-cancel]");
      if (cancel) {
        const panel = cancel.closest("[data-edit-section]");
        if (panel) panel.classList.remove("cand-editing");
      }
    });

    root.querySelectorAll("[data-quick-form]").forEach((form) => {
      if (form.dataset.quickFormBound === "1") return;
      form.dataset.quickFormBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.quickBusy === "1") return;
        const submitter = event.submitter || form.querySelector('button[type="submit"]');
        clearErrors(form);
        setFeedback(form, "Guardando...", true);
        const formData = new FormData(form);
        setFormBusy(form, true, submitter);
        const headers = { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" };
        const csrf = csrfToken();
        if (csrf) headers["X-CSRFToken"] = csrf;
        try {
          const { resp, payload } = await fetchJsonWithTimeout(form.getAttribute("data-endpoint"), {
            method: "POST",
            credentials: "same-origin",
            headers,
            body: formData,
          }, 15000);
          if (!resp.ok || !payload.ok) {
            paintErrors(form, payload.errors || {});
            setFeedback(form, payload.message || "No se pudo guardar.", false);
            return;
          }
          applyPayload(root, payload);
          if (form.closest("[data-finance-panel]")) {
            form.reset();
            ensureFinanceIdempotencyKeys(root);
            const panel = form.closest("[data-finance-panel]");
            if (panel) panel.open = false;
          }
          setFeedback(form, payload.message || "Guardado.", true);
          if (form.closest('[data-edit-section="calls"]')) form.reset();
          if (form.closest("[data-doc-upload-form]")) form.reset();
          const panel = form.closest("[data-edit-section]");
          if (panel) panel.classList.remove("cand-editing");
        } catch (err) {
          setFeedback(form, err && err.name === "AbortError" ? "El servidor tardó demasiado. Intenta de nuevo." : "No se pudo guardar. Intenta de nuevo.", false);
        } finally {
          setFormBusy(form, false, submitter);
          clearGlobalLoaders();
        }
      });
    });

    root.querySelectorAll("[data-state-action]").forEach((form) => {
      if (form.dataset.stateFormBound === "1") return;
      form.dataset.stateFormBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.quickBusy === "1") return;
        const submitter = event.submitter || form.querySelector('button[type="submit"]');
        clearErrors(form);
        const requiredMotivo = form.querySelector('textarea[name="motivo"][required], input[name="motivo"][required]');
        if (requiredMotivo && !String(requiredMotivo.value || "").trim()) {
          paintErrors(form, { motivo: "Debes indicar el motivo de descalificación." });
          const feedback = root.querySelector("[data-state-feedback]");
          if (feedback) {
            feedback.textContent = "Debes indicar el motivo de descalificación.";
            feedback.className = "small mt-2 cand-feedback text-danger";
          }
          requiredMotivo.focus();
          clearGlobalLoaders();
          return;
        }
        const formData = new FormData(form);
        setFormBusy(form, true, submitter);
        const feedback = root.querySelector("[data-state-feedback]");
        if (feedback) {
          feedback.textContent = "Guardando...";
          feedback.className = "small mt-2 cand-feedback text-success";
        }
        const headers = { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" };
        const csrf = csrfToken();
        if (csrf) headers["X-CSRFToken"] = csrf;
        try {
          const { resp, payload } = await fetchJsonWithTimeout(form.getAttribute("data-endpoint"), {
            method: "POST",
            credentials: "same-origin",
            headers,
            body: formData,
          }, 15000);
          if (!resp.ok || !payload.ok) {
            paintErrors(form, payload.errors || {});
            if (feedback) {
              feedback.textContent = payload.message || "No se pudo actualizar el estado.";
              feedback.className = "small mt-2 cand-feedback text-danger";
            }
            if (payload.state_capabilities) refreshStateCapabilities(root, payload);
            return;
          }
          applyPayload(root, payload);
          if (feedback) {
            feedback.textContent = payload.message || "Estado actualizado.";
            feedback.className = "small mt-2 cand-feedback text-success";
          }
          if (form.closest(".modal")) {
            const modalEl = form.closest(".modal");
            const modal = window.bootstrap && window.bootstrap.Modal ? window.bootstrap.Modal.getInstance(modalEl) : null;
            if (modal) modal.hide();
            form.reset();
          }
        } catch (err) {
          if (feedback) {
            feedback.textContent = err && err.name === "AbortError" ? "El servidor tardó demasiado. Intenta de nuevo." : "No se pudo actualizar el estado.";
            feedback.className = "small mt-2 cand-feedback text-danger";
          }
        } finally {
          setFormBusy(form, false, submitter);
          clearGlobalLoaders();
        }
      });
    });

    root.querySelectorAll("[data-doc-upload-form]").forEach((form) => {
      if (form.dataset.docFormBound === "1") return;
      form.dataset.docFormBound = "1";
      const input = form.querySelector("[data-doc-file-input]");
      const pick = form.querySelector('[data-doc-action="pick"]');
      const dropzone = form.querySelector("[data-doc-dropzone]");
      function openPicker() {
        if (input && !input.disabled) input.click();
      }
      function submitIfFileSelected() {
        if (!input || !input.files || !input.files.length) return;
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit(form.querySelector('button[type="submit"]'));
        } else {
          form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        }
      }
      if (pick) {
        pick.addEventListener("click", (event) => {
          event.preventDefault();
          openPicker();
        });
      }
      if (dropzone) {
        dropzone.addEventListener("click", openPicker);
        dropzone.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openPicker();
          }
        });
        dropzone.addEventListener("dragenter", (event) => {
          event.preventDefault();
          form.classList.add("is-dragover");
        });
        dropzone.addEventListener("dragover", (event) => {
          event.preventDefault();
          form.classList.add("is-dragover");
        });
        dropzone.addEventListener("dragleave", () => {
          form.classList.remove("is-dragover");
        });
        dropzone.addEventListener("drop", (event) => {
          event.preventDefault();
          form.classList.remove("is-dragover");
          const files = event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files : null;
          if (!files || !files.length || !input) return;
          const dt = new DataTransfer();
          dt.items.add(files[0]);
          input.files = dt.files;
          submitIfFileSelected();
        });
      }
      if (input) {
        input.addEventListener("change", () => submitIfFileSelected());
      }
    });

    ensureFinanceIdempotencyKeys(root);
  }

  function boot(scope) {
    const detailRoot = getDetailRoot(scope);
    if (!detailRoot) return;
    bindDetail(detailRoot);
  }

  function init() {
    boot(document);
    if (!document.__candDetailUiLifecycleBound) {
      document.__candDetailUiLifecycleBound = true;
      document.addEventListener("admin:navigation-complete", (ev) => {
        const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
        boot(detail.viewport || document);
      });
      document.addEventListener("admin:content-updated", (ev) => {
        const detail = ev && ev.detail && typeof ev.detail === "object" ? ev.detail : {};
        boot(detail.container || document);
      });
    }
  }

  window.AdminCandidataDetailUI = {
    init,
    boot,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
