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
  const fullBtn = document.getElementById("segCandidatasFullBtn");

  if (!btn || !drawer || !backdrop || !closeBtn || !countNode || !loadingNode || !errorNode || !contentNode || !kpisNode || !criticalListNode) {
    return;
  }

  const queueUrl = String(body.getAttribute("data-seg-candidatas-queue-url") || "").trim();
  const badgeUrl = String(body.getAttribute("data-seg-candidatas-badge-url") || "").trim();
  const fullUrl = String(body.getAttribute("data-seg-candidatas-full-url") || "").trim();
  const staffUsername = String(body.getAttribute("data-staff-username") || "").trim();

  if (fullBtn && fullUrl) {
    fullBtn.href = fullUrl;
  }

  let open = false;
  let refreshTimer = null;
  let inFlight = false;
  let previousActive = null;

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function dueLabel(raw) {
    if (!raw) return "Sin vencimiento";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return "Sin vencimiento";
    return d.toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" });
  }

  function setCount(value) {
    const n = Number(value);
    countNode.textContent = Number.isFinite(n) && n >= 0 ? String(Math.floor(n)) : "0";
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

  function render(payload) {
    const buckets = (payload && payload.buckets) || {};
    const items = Array.isArray(payload && payload.items) ? payload.items : [];

    const vencidos = Array.isArray(buckets.vencidos) ? buckets.vencidos : [];
    const hoy = Array.isArray(buckets.hoy) ? buckets.hoy : [];
    const sinResponsable = Array.isArray(buckets.sin_responsable) ? buckets.sin_responsable : [];
    const misCasos = items.filter(function (i) {
      return !!staffUsername && String(i && i.owner_staff_username || "").trim().toLowerCase() === staffUsername.toLowerCase();
    });

    const kpis = [
      ["Vencidos", vencidos.length],
      ["Hoy", hoy.length],
      ["Sin responsable", sinResponsable.length],
      ["Mis casos", misCasos.length],
    ];

    kpisNode.innerHTML = kpis.map(function (pair) {
      return '<div class="seg-kpi"><div class="seg-kpi-label">' + escapeHtml(pair[0]) + '</div><div class="seg-kpi-value">' + escapeHtml(pair[1]) + '</div></div>';
    }).join("");

    const byId = new Map();
    vencidos.concat(hoy).concat(sinResponsable).forEach(function (item) {
      if (!item || !item.id) return;
      if (!byId.has(item.id)) byId.set(item.id, item);
    });

    const critical = Array.from(byId.values()).sort(function (a, b) {
      const ad = a && a.due_at ? new Date(a.due_at).getTime() : Number.POSITIVE_INFINITY;
      const bd = b && b.due_at ? new Date(b.due_at).getTime() : Number.POSITIVE_INFINITY;
      return ad - bd;
    }).slice(0, 10);

    if (!critical.length) {
      criticalListNode.innerHTML = '<div class="small text-muted">No hay casos críticos ahora.</div>';
      return;
    }

    criticalListNode.innerHTML = critical.map(function (item) {
      const detailUrl = '/admin/seguimiento-candidatas/casos/' + encodeURIComponent(item.id);
      const title = item.nombre_contacto || item.telefono_norm || item.public_id || ('Caso #' + item.id);
      const estado = item.estado || 'sin_estado';
      const accion = item.proxima_accion_tipo || 'sin accion';
      const owner = item.owner_staff_username || 'Sin responsable';
      return (
        '<article class="seg-case">' +
          '<div class="seg-case-top">' +
            '<div class="seg-case-name">' + escapeHtml(title) + '</div>' +
            '<div class="seg-case-due">' + escapeHtml(dueLabel(item.due_at)) + '</div>' +
          '</div>' +
          '<div class="seg-case-meta">Estado: ' + escapeHtml(estado) + '</div>' +
          '<div class="seg-case-meta">Proxima accion: ' + escapeHtml(accion) + '</div>' +
          '<div class="seg-case-meta">Responsable: ' + escapeHtml(owner) + '</div>' +
          '<a class="btn btn-sm btn-outline-secondary mt-2" href="' + detailUrl + '">Abrir detalle</a>' +
        '</article>'
      );
    }).join("");
  }

  async function loadQueue() {
    if (!queueUrl || inFlight) return;
    inFlight = true;
    showLoading();
    try {
      const r = await fetch(queueUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const p = await r.json();
      if (!p || p.ok !== true) throw new Error("PAYLOAD");
      render(p);
      showContent();
      setCount(((p.buckets && p.buckets.vencidos) || []).length);
    } catch (_e) {
      showError("No se pudo cargar seguimiento de candidatas. Intenta de nuevo.");
    } finally {
      inFlight = false;
    }
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
    if (previousActive && typeof previousActive.focus === "function") {
      previousActive.focus();
    } else {
      btn.focus();
    }
  }

  btn.addEventListener("click", function () {
    if (open) {
      closeDrawer();
      return;
    }
    openDrawer();
  });

  closeBtn.addEventListener("click", closeDrawer);
  backdrop.addEventListener("click", closeDrawer);

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
    refreshBadge().catch(function () {});
    if (open) {
      loadQueue().catch(function () {});
    }
  });

  refreshBadge().catch(function () {});
})();
