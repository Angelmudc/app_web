(function () {
  "use strict";

  const body = document.body;
  if (!body) return;
  const enabled = String(body.getAttribute("data-seg-candidatas-island-enabled") || "") === "1";
  if (!enabled) return;

  const btn = document.getElementById("segCandidatasIslandBtn");
  const drawer = document.getElementById("segCandidatasDrawer");
  const backdrop = document.getElementById("segCandidatasBackdrop");
  const closeBtn = document.getElementById("segCandidatasCloseBtn");
  const countNode = document.getElementById("segCandidatasIslandCount");
  const loadingNode = document.getElementById("segCandidatasLoading");
  const errorNode = document.getElementById("segCandidatasError");
  const contentNode = document.getElementById("segCandidatasContent");
  const kpisNode = document.getElementById("segCandidatasKpis");
  const criticalListNode = document.getElementById("segCandidatasCriticalList");
  const mineListNode = document.getElementById("segCandidatasMineList");
  const fullBtn = document.getElementById("segCandidatasFullBtn");
  const quickCreateBtn = document.getElementById("segCandidatasQuickCreateBtn");
  const createForm = document.getElementById("segCandidatasQuickCreateForm");
  const createSubmitBtn = document.getElementById("segQuickCreateSubmitBtn");
  const createResetBtn = document.getElementById("segQuickCreateResetBtn");
  const createFeedback = document.getElementById("segQuickCreateFeedback");
  const tabButtons = Array.from(document.querySelectorAll("[data-seg-tab-btn]"));
  const tabPanes = Array.from(document.querySelectorAll("[data-seg-tab-pane]"));

  if (!btn || !drawer || !backdrop || !closeBtn || !countNode || !loadingNode || !errorNode || !contentNode || !kpisNode || !criticalListNode || !mineListNode) return;

  const queueUrl = String(body.getAttribute("data-seg-candidatas-queue-url") || "").trim();
  const badgeUrl = String(body.getAttribute("data-seg-candidatas-badge-url") || "").trim();
  const fullUrl = String(body.getAttribute("data-seg-candidatas-full-url") || "").trim();
  const createUrl = String(body.getAttribute("data-seg-candidatas-create-url") || "").trim();
  const staffUsername = String(body.getAttribute("data-staff-username") || "").trim();
  const csrfToken = ((document.querySelector('meta[name="csrf-token"]') || {}).content || "").trim();

  let open = false;
  let refreshTimer = null;
  let isQueueLoading = false;
  let pendingReload = false;
  let previousActive = null;
  let queueState = null;
  let lastQueueSignature = null;
  let lastKpiSig = "";
  let lastCriticalSig = "";
  let lastMineSig = "";
  let suppressInvalidationUntilMs = 0;
  const queueQuickUrl = queueUrl ? (queueUrl + (queueUrl.indexOf("?") >= 0 ? "&" : "?") + "quick=1&limit=80") : "";

  function esc(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setCount(value) {
    const n = Number(value);
    countNode.textContent = Number.isFinite(n) && n >= 0 ? String(Math.floor(n)) : "0";
  }

  function toDueLabel(raw) {
    if (!raw) return "Sin vencimiento";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return "Sin vencimiento";
    return d.toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" });
  }

  function setFeedback(node, type, msg) {
    if (!node) return;
    node.classList.remove("d-none", "alert-success", "alert-danger", "alert-warning", "alert-info");
    node.classList.add("alert-" + type);
    node.textContent = msg;
  }

  function clearFeedback(node) {
    if (!node) return;
    node.classList.add("d-none");
    node.textContent = "";
  }

  function showLoading() {
    loadingNode.classList.remove("d-none");
    errorNode.classList.add("d-none");
    contentNode.classList.add("d-none");
  }
  function showError(msg) {
    loadingNode.classList.add("d-none");
    contentNode.classList.add("d-none");
    errorNode.classList.remove("d-none");
    errorNode.textContent = msg || "No se pudo cargar el seguimiento ahora.";
  }
  function showContent() {
    loadingNode.classList.add("d-none");
    errorNode.classList.add("d-none");
    contentNode.classList.remove("d-none");
  }

  function getHeaders() {
    const out = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    if (csrfToken) out["X-CSRFToken"] = csrfToken;
    return out;
  }

  async function postJson(url, payload) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: getHeaders(),
      body: JSON.stringify(payload || {}),
    });
    let p = {};
    try { p = await r.json(); } catch (_e) {}
    if (!r.ok || !p || p.ok !== true) {
      const error = (p && p.error) || ("HTTP_" + r.status);
      throw new Error(String(error));
    }
    return p;
  }

  function setTab(tab) {
    tabButtons.forEach(function (b) {
      b.classList.toggle("is-active", b.getAttribute("data-seg-tab-btn") === tab);
    });
    tabPanes.forEach(function (pane) {
      pane.classList.toggle("is-active", pane.getAttribute("data-seg-tab-pane") === tab);
    });
  }

  function caseCard(item) {
    const id = Number(item && item.id);
    if (!Number.isFinite(id) || id <= 0) return "";
    const detailUrl = "/admin/seguimiento-candidatas/casos/" + encodeURIComponent(id);
    const title = item.nombre_contacto || item.telefono_norm || item.public_id || ("Caso #" + id);
    return (
      '<article class="seg-case" data-case-id="' + esc(id) + '">' +
      '<div class="seg-case-top"><div class="seg-case-name">' + esc(title) + '</div><div class="seg-case-due">' + esc(toDueLabel(item.due_at)) + "</div></div>" +
      '<div class="seg-case-meta">Estado: ' + esc(item.estado || "sin_estado") + "</div>" +
      '<div class="seg-case-meta">Proxima accion: ' + esc(item.proxima_accion_tipo || "sin accion") + "</div>" +
      '<div class="seg-case-meta">Prioridad: ' + esc(item.prioridad || "normal") + "</div>" +
      '<div class="seg-case-meta">Responsable: ' + esc(item.owner_staff_username || "Sin responsable") + "</div>" +
      '<div class="seg-actions mt-2">' +
      '<button type="button" class="btn btn-sm btn-outline-light" data-seg-action="take" data-case-id="' + esc(id) + '">Tomar</button>' +
      '<button type="button" class="btn btn-sm btn-outline-light" data-seg-action="note" data-case-id="' + esc(id) + '">Nota rapida</button>' +
      '<button type="button" class="btn btn-sm btn-outline-light" data-seg-action="next" data-case-id="' + esc(id) + '">Proxima accion</button>' +
      '<button type="button" class="btn btn-sm btn-outline-warning" data-seg-action="close" data-case-id="' + esc(id) + '">Cerrar</button>' +
      '<a class="btn btn-sm btn-outline-secondary" href="' + detailUrl + '">Abrir detalle</a>' +
      "</div>" +
      '<div class="seg-case-feedback d-none" data-seg-case-feedback="' + esc(id) + '"></div>' +
      '<div class="seg-inline-action d-none" data-seg-action-form="note" data-case-id="' + esc(id) + '">' +
      '<label class="form-label form-label-sm mb-1 mt-2">Nota rápida</label>' +
      '<textarea class="form-control form-control-sm mb-2" rows="2" maxlength="4000" data-seg-note-text="' + esc(id) + '"></textarea>' +
      '<div class="d-flex gap-2"><button type="button" class="btn btn-sm btn-primary" data-seg-submit="note" data-case-id="' + esc(id) + '">Guardar nota</button><button type="button" class="btn btn-sm btn-outline-secondary" data-seg-cancel-form data-case-id="' + esc(id) + '">Cancelar</button></div>' +
      "</div>" +
      '<div class="seg-inline-action d-none" data-seg-action-form="next" data-case-id="' + esc(id) + '">' +
      '<label class="form-label form-label-sm mb-1 mt-2">Próxima acción</label>' +
      '<input class="form-control form-control-sm mb-2" maxlength="40" value="' + esc(item.proxima_accion_tipo || "") + '" data-seg-next-action="' + esc(id) + '">' +
      '<label class="form-label form-label-sm mb-1">Vencimiento</label>' +
      '<input class="form-control form-control-sm mb-2" type="datetime-local" value="' + esc(item.due_at ? new Date(item.due_at).toISOString().slice(0, 16) : "") + '" data-seg-next-due="' + esc(id) + '">' +
      '<div class="d-flex gap-2"><button type="button" class="btn btn-sm btn-primary" data-seg-submit="next" data-case-id="' + esc(id) + '">Guardar acción</button><button type="button" class="btn btn-sm btn-outline-secondary" data-seg-cancel-form data-case-id="' + esc(id) + '">Cancelar</button></div>' +
      "</div>" +
      '<div class="seg-inline-action d-none" data-seg-action-form="close" data-case-id="' + esc(id) + '">' +
      '<label class="form-label form-label-sm mb-1 mt-2">Razón de cierre</label>' +
      '<input class="form-control form-control-sm mb-2" maxlength="255" data-seg-close-reason="' + esc(id) + '">' +
      '<div class="d-flex gap-2"><button type="button" class="btn btn-sm btn-warning" data-seg-submit="close" data-case-id="' + esc(id) + '">Cerrar caso</button><button type="button" class="btn btn-sm btn-outline-secondary" data-seg-cancel-form data-case-id="' + esc(id) + '">Cancelar</button></div>' +
      "</div>" +
      "</article>"
    );
  }

  function itemId(item) {
    const id = Number(item && item.id);
    return Number.isFinite(id) && id > 0 ? id : 0;
  }

  function buildCritical(buckets) {
    const vencidos = Array.isArray(buckets && buckets.vencidos) ? buckets.vencidos : [];
    const hoy = Array.isArray(buckets && buckets.hoy) ? buckets.hoy : [];
    const sinResponsable = Array.isArray(buckets && buckets.sin_responsable) ? buckets.sin_responsable : [];
    const byId = new Map();
    vencidos.concat(hoy).concat(sinResponsable).forEach(function (item) {
      const id = itemId(item);
      if (id <= 0) return;
      if (!byId.has(id)) byId.set(id, item);
    });
    return Array.from(byId.values()).sort(function (a, b) {
      const ad = a && a.due_at ? new Date(a.due_at).getTime() : Number.POSITIVE_INFINITY;
      const bd = b && b.due_at ? new Date(b.due_at).getTime() : Number.POSITIVE_INFINITY;
      return ad - bd;
    }).slice(0, 25);
  }

  function buildMine(items) {
    return (Array.isArray(items) ? items : []).filter(function (i) {
      return !!staffUsername && String(i && i.owner_staff_username || "").trim().toLowerCase() === staffUsername.toLowerCase() && i && i.is_open;
    });
  }

  function listSignature(items) {
    return (Array.isArray(items) ? items : []).map(function (i) {
      return [
        itemId(i),
        String(i && i.estado || ""),
        String(i && i.proxima_accion_tipo || ""),
        String(i && i.due_at || ""),
        String(i && i.owner_staff_username || ""),
      ].join("|");
    }).join(";");
  }

  function getQueueSignature(data) {
    const payload = (data && typeof data === "object") ? data : {};
    const buckets = (payload.buckets && typeof payload.buckets === "object") ? payload.buckets : {};
    const items = Array.isArray(payload.items) ? payload.items : [];
    const critical = buildCritical(buckets);
    const mine = buildMine(items);
    return JSON.stringify({
      total: items.length,
      overdue: Array.isArray(buckets.vencidos) ? buckets.vencidos.length : 0,
      mine: mine.map(function (x) { return itemId(x); }),
      critical: critical.map(function (x) { return itemId(x); }),
    });
  }

  function render(payload) {
    const buckets = (payload && payload.buckets) || {};
    const items = Array.isArray(payload && payload.items) ? payload.items : [];
    const vencidos = Array.isArray(buckets.vencidos) ? buckets.vencidos : [];
    const hoy = Array.isArray(buckets.hoy) ? buckets.hoy : [];
    const sinResponsable = Array.isArray(buckets.sin_responsable) ? buckets.sin_responsable : [];
    const misCasos = buildMine(items);
    const critical = buildCritical(buckets);

    const kpis = [
      ["Vencidos", vencidos.length],
      ["Hoy", hoy.length],
      ["Sin responsable", sinResponsable.length],
      ["Mis casos", misCasos.length],
    ];
    const kpiSig = kpis.map(function (pair) { return pair[0] + ":" + pair[1]; }).join("|");
    if (kpiSig !== lastKpiSig) {
      kpisNode.innerHTML = kpis.map(function (pair) {
      return '<div class="seg-kpi"><div class="seg-kpi-label">' + esc(pair[0]) + '</div><div class="seg-kpi-value">' + esc(pair[1]) + "</div></div>";
      }).join("");
      lastKpiSig = kpiSig;
    }

    const criticalSig = listSignature(critical);
    if (criticalSig !== lastCriticalSig) {
      criticalListNode.innerHTML = critical.length ? critical.map(caseCard).join("") : '<div class="small text-muted">No hay casos pendientes críticos.</div>';
      lastCriticalSig = criticalSig;
    }

    const mineSig = listSignature(misCasos);
    if (mineSig !== lastMineSig) {
      mineListNode.innerHTML = misCasos.length ? misCasos.map(caseCard).join("") : '<div class="small text-muted">No tienes casos activos.</div>';
      lastMineSig = mineSig;
    }
  }

  async function refreshBadge() {
    if (!badgeUrl) return;
    try {
      const r = await fetch(badgeUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const p = await r.json();
      setCount((p && p.overdue_count) || 0);
    } catch (_e) {
      setCount(0);
    }
  }

  async function loadQueue() {
    if (!queueQuickUrl) return;
    if (isQueueLoading) {
      pendingReload = true;
      return;
    }
    isQueueLoading = true;
    pendingReload = false;
    showLoading();
    try {
      const r = await fetch(queueQuickUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      if (!r.ok) throw new Error("HTTP_" + r.status);
      const p = await r.json();
      if (!p || p.ok !== true) throw new Error("PAYLOAD");
      const sig = getQueueSignature(p);
      const changed = sig !== lastQueueSignature;
      if (changed) {
        queueState = p;
        lastQueueSignature = sig;
        render(p);
        showContent();
        setCount(((p.buckets && p.buckets.vencidos) || []).length);
      } else {
        if (open) showContent();
        if (typeof console !== "undefined" && console && typeof console.debug === "function") {
          console.debug("queue skipped (no changes)");
        }
      }
    } catch (_e) {
      showError("No se pudo cargar seguimiento de candidatas. Intenta de nuevo.");
    } finally {
      isQueueLoading = false;
      if (pendingReload) {
        pendingReload = false;
        if (queueState) loadQueue().catch(function () {});
      }
    }
  }

  async function reloadData() {
    await loadQueue();
    await refreshBadge();
  }

  function upsertCaseInQueueState(newCase) {
    if (!newCase || !itemId(newCase)) return;
    if (!queueState || typeof queueState !== "object") {
      queueState = { ok: true, items: [], buckets: { vencidos: [], hoy: [], sin_responsable: [] } };
    }
    if (!Array.isArray(queueState.items)) queueState.items = [];
    const cid = itemId(newCase);
    queueState.items = queueState.items.filter(function (x) { return itemId(x) !== cid; });
    queueState.items.unshift(newCase);
    const isOverdue = !!newCase.due_at && (new Date(newCase.due_at).getTime() < Date.now());
    if (!queueState.buckets || typeof queueState.buckets !== "object") queueState.buckets = {};
    if (!Array.isArray(queueState.buckets.vencidos)) queueState.buckets.vencidos = [];
    if (isOverdue) {
      queueState.buckets.vencidos = queueState.buckets.vencidos.filter(function (x) { return itemId(x) !== cid; });
      queueState.buckets.vencidos.unshift(newCase);
    }
    lastQueueSignature = getQueueSignature(queueState);
  }

  function startOpenRefresh() {
    stopOpenRefresh();
    refreshTimer = window.setInterval(function () {
      if (!open) return;
      loadQueue().catch(function () {});
      refreshBadge().catch(function () {});
    }, 25000);
  }
  function stopOpenRefresh() {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  function openDrawer() {
    if (open) return;
    open = true;
    previousActive = document.activeElement;
    drawer.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("seg-drawer-open");
    btn.setAttribute("aria-expanded", "true");
    closeBtn.focus();
    setTab("pendientes");
    loadQueue().catch(function () {});
    startOpenRefresh();
  }

  function closeDrawer() {
    if (!open) return;
    open = false;
    drawer.hidden = true;
    backdrop.hidden = true;
    document.body.classList.remove("seg-drawer-open");
    btn.setAttribute("aria-expanded", "false");
    stopOpenRefresh();
    if (previousActive && typeof previousActive.focus === "function") previousActive.focus();
    else btn.focus();
  }

  async function onQuickCreateSubmit(ev) {
    ev.preventDefault();
    if (!createForm || !createUrl) return;
    clearFeedback(createFeedback);
    createSubmitBtn.disabled = true;
    createSubmitBtn.textContent = "Guardando...";
    const fd = new FormData(createForm);
    const quePidio = String(fd.get("que_pidio") || "").trim();
    const nextAction = String(fd.get("proxima_accion_tipo") || "").trim();
    const detail = String(fd.get("proxima_accion_detalle") || "").trim();
    const mergedDetail = (quePidio ? ("Que pidio: " + quePidio) : "") + (detail ? ("\nNota inicial: " + detail) : "");
    const payload = {
      nombre_contacto: String(fd.get("nombre_contacto") || "").trim(),
      telefono_norm: String(fd.get("telefono_norm") || "").trim(),
      canal_origen: "otro",
      proxima_accion_tipo: nextAction,
      proxima_accion_detalle: mergedDetail.trim(),
      prioridad: String(fd.get("prioridad") || "normal").trim(),
    };
    try {
      const p = await postJson(createUrl, payload);
      let msg = "Caso creado correctamente.";
      if (p.duplicate_detected && p.existing_case_id) {
        msg = "Ya existe un caso parecido.";
        setFeedback(createFeedback, "warning", msg);
        createFeedback.innerHTML = 'Ya existe un caso parecido. <a class="btn btn-sm btn-outline-dark ms-2" href="/admin/seguimiento-candidatas/casos/' + esc(p.existing_case_id) + '">Abrir existente</a>';
      } else {
        setFeedback(createFeedback, "success", msg);
        if (p.case && typeof p.case === "object") {
          upsertCaseInQueueState(p.case);
          render(queueState || { items: [], buckets: {} });
          showContent();
          suppressInvalidationUntilMs = Date.now() + 1200;
        }
      }
      createForm.reset();
      if (typeof p.overdue_count === "number") setCount(p.overdue_count);
      refreshBadge().catch(function () {});
      window.setTimeout(function () {
        loadQueue().catch(function () {});
      }, 250);
    } catch (e) {
      setFeedback(createFeedback, "danger", "No se pudo crear el caso: " + esc(e && e.message ? e.message : "error"));
    } finally {
      createSubmitBtn.disabled = false;
      createSubmitBtn.textContent = "Guardar caso";
    }
  }

  function setCaseFeedback(caseId, type, msg) {
    const node = drawer.querySelector('[data-seg-case-feedback="' + String(caseId) + '"]');
    if (!node) return;
    node.className = "seg-case-feedback alert alert-" + type;
    node.textContent = msg;
  }

  function hideCaseForms(caseId) {
    const forms = drawer.querySelectorAll('[data-seg-action-form][data-case-id="' + String(caseId) + '"]');
    forms.forEach(function (n) { n.classList.add("d-none"); });
  }

  function showCaseForm(caseId, formName) {
    hideCaseForms(caseId);
    const node = drawer.querySelector('[data-seg-action-form="' + formName + '"][data-case-id="' + String(caseId) + '"]');
    if (node) node.classList.remove("d-none");
  }

  async function onCaseActionClick(target) {
    const action = String(target.getAttribute("data-seg-action") || "");
    const caseId = Number(target.getAttribute("data-case-id") || 0);
    if (!action || !Number.isFinite(caseId) || caseId <= 0) return;
    target.disabled = true;
    try {
      if (action === "take") {
        await postJson("/admin/seguimiento-candidatas/casos/" + caseId + "/tomar", {});
        setCaseFeedback(caseId, "success", "Caso tomado.");
      } else if (action === "note") {
        showCaseForm(caseId, "note");
      } else if (action === "next") {
        showCaseForm(caseId, "next");
      } else if (action === "close") {
        showCaseForm(caseId, "close");
      }
      if (action === "take") await reloadData();
    } catch (e) {
      setCaseFeedback(caseId, "danger", "Error: " + esc(e && e.message ? e.message : "request_failed"));
    } finally {
      target.disabled = false;
    }
  }

  async function onCaseInlineSubmit(target) {
    const action = String(target.getAttribute("data-seg-submit") || "");
    const caseId = Number(target.getAttribute("data-case-id") || 0);
    if (!action || !Number.isFinite(caseId) || caseId <= 0) return;
    target.disabled = true;
    const prevText = target.textContent;
    target.textContent = "Guardando...";
    try {
      if (action === "note") {
        const noteInput = drawer.querySelector('[data-seg-note-text="' + String(caseId) + '"]');
        const note = String((noteInput && noteInput.value) || "").trim();
        if (!note) throw new Error("note_required");
        await postJson("/admin/seguimiento-candidatas/casos/" + caseId + "/nota", { note: note });
        setCaseFeedback(caseId, "success", "Nota agregada.");
      } else if (action === "next") {
        const actionInput = drawer.querySelector('[data-seg-next-action="' + String(caseId) + '"]');
        const dueInput = drawer.querySelector('[data-seg-next-due="' + String(caseId) + '"]');
        const actionType = String((actionInput && actionInput.value) || "").trim();
        const dueAt = String((dueInput && dueInput.value) || "").trim();
        if (!actionType || !dueAt) throw new Error("action_and_due_required");
        await postJson("/admin/seguimiento-candidatas/casos/" + caseId + "/proxima-accion", {
          proxima_accion_tipo: actionType,
          due_at: dueAt,
        });
        setCaseFeedback(caseId, "success", "Próxima acción actualizada.");
      } else if (action === "close") {
        const reasonInput = drawer.querySelector('[data-seg-close-reason="' + String(caseId) + '"]');
        const reason = String((reasonInput && reasonInput.value) || "").trim();
        if (!reason) throw new Error("close_reason_required");
        await postJson("/admin/seguimiento-candidatas/casos/" + caseId + "/cerrar", {
          estado: "cerrado_no_exitoso",
          close_reason: reason,
        });
        setCaseFeedback(caseId, "success", "Caso cerrado.");
      }
      hideCaseForms(caseId);
      await reloadData();
    } catch (e) {
      setCaseFeedback(caseId, "danger", "Error: " + esc(e && e.message ? e.message : "request_failed"));
    } finally {
      target.disabled = false;
      target.textContent = prevText;
    }
  }

  if (fullBtn && fullUrl) fullBtn.href = fullUrl;

  btn.addEventListener("click", function () { if (open) closeDrawer(); else openDrawer(); });
  closeBtn.addEventListener("click", closeDrawer);
  backdrop.addEventListener("click", closeDrawer);

  if (quickCreateBtn) quickCreateBtn.addEventListener("click", function () { setTab("create"); });
  tabButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      const tab = String(b.getAttribute("data-seg-tab-btn") || "pendientes");
      setTab(tab);
    });
  });
  if (createForm) createForm.addEventListener("submit", onQuickCreateSubmit);
  if (createResetBtn) createResetBtn.addEventListener("click", function () {
    if (!createForm) return;
    createForm.reset();
    clearFeedback(createFeedback);
  });

  drawer.addEventListener("click", function (ev) {
    const t = ev.target && ev.target.closest && ev.target.closest("[data-seg-action]");
    if (t) {
      onCaseActionClick(t).catch(function () {});
      return;
    }
    const s = ev.target && ev.target.closest && ev.target.closest("[data-seg-submit]");
    if (s) {
      onCaseInlineSubmit(s).catch(function () {});
      return;
    }
    const c = ev.target && ev.target.closest && ev.target.closest("[data-seg-cancel-form]");
    if (!c) return;
    const caseId = Number(c.getAttribute("data-case-id") || 0);
    if (!Number.isFinite(caseId) || caseId <= 0) return;
    hideCaseForms(caseId);
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && open) {
      ev.preventDefault();
      closeDrawer();
    }
  });

  document.addEventListener("admin:live-invalidation-event", function (ev) {
    const detail = (ev && ev.detail && ev.detail.event) || null;
    const t = String((detail && detail.event_type) || "").toLowerCase();
    if (t.indexOf("staff.case_tracking.") !== 0) return;
    if (Date.now() < suppressInvalidationUntilMs) return;
    refreshBadge().catch(function () {});
    if (open) loadQueue().catch(function () {});
  });

  refreshBadge().catch(function () {});
})();
