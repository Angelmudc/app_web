// static/js/core/live_invalidation.js
// F4-C1/F4-C2/F4-C3: invalidacion dirigida para solicitud_detail, cliente_detail
// y summaries de listados operativos.

(function () {
  "use strict";

  const root = document.querySelector('[data-live-invalidation-scope="1"]');
  if (!root) return;

  const view = String(root.getAttribute("data-live-invalidation-view") || "").trim();
  if (
    view !== "solicitud_detail"
    && view !== "cliente_detail"
    && view !== "solicitudes_summary"
    && view !== "solicitudes_prioridad_summary"
  ) return;

  const streamUrl = String(root.getAttribute("data-live-invalidation-stream-url") || "").trim();
  const pollUrlBase = String(root.getAttribute("data-live-invalidation-poll-url") || "").trim();
  const sourceUrl = String(root.getAttribute("data-live-invalidation-source-url") || window.location.pathname).trim();
  const observabilityUrl = String(root.getAttribute("data-live-observability-url") || "/admin/live/observability").trim();
  const csrfToken = ((document.querySelector('meta[name="csrf-token"]') || {}).content || "").trim();

  const currentSolicitudId = Number(root.getAttribute("data-live-solicitud-id") || 0);
  const knownSolicitudIds = new Set();

  const regionMap = new Map();
  const defaultRegionUrl = sourceUrl || window.location.pathname;
  function setRegion(selector, attrName) {
    const sel = String(selector || "").trim();
    if (!sel) return;
    const url = String(root.getAttribute(attrName) || defaultRegionUrl).trim() || defaultRegionUrl;
    regionMap.set(sel, url);
  }

  if (view === "solicitud_detail") {
    setRegion("#solicitudSummaryAsyncRegion", "data-live-region-summary-url");
    setRegion("#solicitudOperativaCoreAsyncRegion", "data-live-region-operativa-url");
  } else if (view === "cliente_detail") {
    setRegion("#clienteSummaryAsyncRegion", "data-live-region-summary-url");
    setRegion("#clienteSolicitudesAsyncRegion", "data-live-region-solicitudes-url");
  } else if (view === "solicitudes_summary") {
    setRegion("#solicitudesSummaryAsyncRegion", "data-live-region-summary-url");
  } else {
    setRegion("#prioridadSummaryAsyncRegion", "data-live-region-summary-url");
    setRegion("#prioridadResponsablesAsyncRegion", "data-live-region-responsables-url");
  }

  if (!regionMap.size) return;

  const EVENT_DEDUPE_MAX = 1200;
  const EVENT_DEDUPE_TTL_MS = 10 * 60 * 1000;
  const ENTITY_DEBOUNCE_MS = 500;
  const REGION_THROTTLE_MS = 1800;
  const POLL_INTERVAL_MS = 25000;
  const SSE_RETRY_MS = 60000;

  const seenEvents = new Map();
  const pendingByEntity = new Map(); // entityKey -> { regions:Set, timer:number|null }
  const regionState = new Map(); // selector -> { lastAt, timer, inFlight, queued }
  let eventSource = null;
  let pollTimer = null;
  let sseRetryTimer = null;
  let afterId = 0;
  let fallbackMode = false;

  function isViewActive() {
    return Boolean(root && root.isConnected && document.contains(root));
  }

  function nowMs() {
    return Date.now();
  }

  function reportObservability(eventName, payload) {
    if (!observabilityUrl || !eventName || !isViewActive()) return;
    const body = JSON.stringify(Object.assign({ event: String(eventName) }, payload || {}));
    fetch(observabilityUrl, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
      },
      body,
    }).catch(function () {
      try {
        if (navigator.sendBeacon && window.Blob) {
          const blob = new Blob([body], { type: "application/json" });
          navigator.sendBeacon(observabilityUrl, blob);
        }
      } catch (_) {}
    });
  }

  function normalizeSolicitudId(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? Math.floor(n) : 0;
  }

  function collectClienteSolicitudIds() {
    knownSolicitudIds.clear();
    const rows = document.querySelectorAll('#clienteSolicitudesAsyncRegion [id^="sol-"]');
    rows.forEach((node) => {
      const raw = String(node.id || "").replace(/^sol-/, "");
      const sid = normalizeSolicitudId(raw);
      if (sid > 0) knownSolicitudIds.add(sid);
    });
  }

  if (view === "cliente_detail") {
    collectClienteSolicitudIds();
  }

  function isDuplicateEvent(eventId) {
    const key = String(eventId || "").trim();
    if (!key) return false;
    const current = nowMs();
    const prev = seenEvents.get(key);
    if (prev && (current - prev) <= EVENT_DEDUPE_TTL_MS) return true;
    seenEvents.set(key, current);

    if (seenEvents.size > EVENT_DEDUPE_MAX) {
      const entries = Array.from(seenEvents.entries()).sort((a, b) => a[1] - b[1]);
      const removeCount = Math.max(1, entries.length - EVENT_DEDUPE_MAX);
      for (let i = 0; i < removeCount; i += 1) {
        seenEvents.delete(entries[i][0]);
      }
    }
    return false;
  }

  function parseInvalidationEvent(data) {
    if (!data || typeof data !== "object") return null;
    const target = (data.target && typeof data.target === "object") ? data.target : {};
    const agg = (data.aggregate && typeof data.aggregate === "object") ? data.aggregate : {};
    const sid = normalizeSolicitudId(target.solicitud_id || agg.id);
    if (sid <= 0) return null;
    return {
      eventId: String(data.event_id || "").trim(),
      eventType: String(data.event_type || "").trim().toUpperCase(),
      solicitudId: sid,
      aggregateType: String(agg.type || "").trim(),
    };
  }

  function isRelevantForCurrentView(evt) {
    if (!isViewActive()) return false;
    if (!evt || evt.solicitudId <= 0) return false;
    if (view === "solicitud_detail") {
      return evt.solicitudId === currentSolicitudId;
    }
    if (view === "solicitudes_summary" || view === "solicitudes_prioridad_summary") {
      return true;
    }
    return knownSolicitudIds.has(evt.solicitudId);
  }

  function regionsForEventType(_eventType) {
    if (view === "solicitud_detail") {
      return ["#solicitudSummaryAsyncRegion", "#solicitudOperativaCoreAsyncRegion"];
    }
    if (view === "solicitudes_summary") {
      return ["#solicitudesSummaryAsyncRegion"];
    }
    if (view === "solicitudes_prioridad_summary") {
      return ["#prioridadSummaryAsyncRegion", "#prioridadResponsablesAsyncRegion"];
    }
    return ["#clienteSummaryAsyncRegion", "#clienteSolicitudesAsyncRegion"];
  }

  async function fetchRegionHtml(url, selector) {
    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html,*/*",
      },
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    if (response.redirected) throw new Error("REDIRECT");
    const html = await response.text();
    const fragmentRegion = String(response.headers.get("X-Async-Fragment-Region") || "").trim().toLowerCase();
    const selectorRegion = String(selector || "").replace(/^#/, "").trim().toLowerCase();
    if (fragmentRegion && fragmentRegion === selectorRegion) {
      return html;
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    const node = doc.querySelector(selector);
    if (!node) throw new Error("MISSING_TARGET");
    return node.innerHTML;
  }

  async function refreshRegion(selector) {
    if (!isViewActive()) return false;
    const target = document.querySelector(selector);
    if (!target) return false;
    const url = regionMap.get(selector) || defaultRegionUrl;
    if (!url) return false;
    const startedAt = nowMs();
    const html = await fetchRegionHtml(url, selector);
    if (window.AdminAsync && typeof window.AdminAsync.replaceTargetHtml === "function") {
      window.AdminAsync.replaceTargetHtml(selector, html, { preserveScroll: true });
    } else {
      target.innerHTML = html;
    }
    if (view === "cliente_detail" && selector === "#clienteSolicitudesAsyncRegion") {
      collectClienteSolicitudIds();
    }
    reportObservability("refetch_region", {
      region: String(selector || "").replace(/^#/, ""),
      duration_ms: Math.max(0, nowMs() - startedAt),
      ok: true,
    });
    return true;
  }

  function getRegionState(selector) {
    if (!regionState.has(selector)) {
      regionState.set(selector, {
        lastAt: 0,
        timer: null,
        inFlight: false,
        queued: false,
      });
    }
    return regionState.get(selector);
  }

  function scheduleRegionRefresh(selector) {
    if (!isViewActive()) {
      cleanup();
      return;
    }
    const state = getRegionState(selector);
    const execute = async function () {
      state.timer = null;
      if (!isViewActive()) return;
      if (state.inFlight) {
        state.queued = true;
        return;
      }

      const elapsed = nowMs() - state.lastAt;
      if (elapsed < REGION_THROTTLE_MS) {
        const wait = Math.max(120, REGION_THROTTLE_MS - elapsed);
        if (!state.timer) {
          state.timer = window.setTimeout(execute, wait);
        }
        return;
      }

      state.inFlight = true;
      try {
        await refreshRegion(selector);
      } catch (_) {
        reportObservability("refetch_region", {
          region: String(selector || "").replace(/^#/, ""),
          duration_ms: 0,
          ok: false,
        });
        // no-op: degradacion silenciosa.
      } finally {
        state.lastAt = nowMs();
        state.inFlight = false;
        if (state.queued) {
          state.queued = false;
          scheduleRegionRefresh(selector);
        }
      }
    };

    if (!state.timer) {
      state.timer = window.setTimeout(execute, 10);
    } else {
      state.queued = true;
    }
  }

  function queueEntityInvalidation(entityKey, selectors) {
    const key = String(entityKey || "").trim();
    if (!key) return;
    const existing = pendingByEntity.get(key) || { regions: new Set(), timer: null };
    selectors.forEach((sel) => {
      if (regionMap.has(sel)) existing.regions.add(sel);
    });
    if (existing.timer) window.clearTimeout(existing.timer);
    existing.timer = window.setTimeout(function () {
      existing.timer = null;
      const regions = Array.from(existing.regions.values());
      existing.regions.clear();
      pendingByEntity.delete(key);
      regions.forEach((sel) => scheduleRegionRefresh(sel));
    }, ENTITY_DEBOUNCE_MS);
    pendingByEntity.set(key, existing);
  }

  function processInvalidationPayload(payload) {
    if (!isViewActive()) {
      cleanup();
      return;
    }
    const evt = parseInvalidationEvent(payload);
    if (!evt) return;
    if (evt.eventId && isDuplicateEvent(evt.eventId)) return;
    if (!isRelevantForCurrentView(evt)) return;
    const regions = regionsForEventType(evt.eventType);
    queueEntityInvalidation("solicitud:" + String(evt.solicitudId), regions);
  }

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      fallbackMode = false;
    }
  }

  function stopSseRetry() {
    if (sseRetryTimer) {
      window.clearTimeout(sseRetryTimer);
      sseRetryTimer = null;
    }
  }

  function closeSSE() {
    if (eventSource) {
      try { eventSource.close(); } catch (_) {}
      eventSource = null;
    }
  }

  async function pollOnce() {
    if (!isViewActive()) return;
    if (!pollUrlBase) return;
    const url = new URL(pollUrlBase, window.location.origin);
    url.searchParams.set("after_id", String(afterId || 0));
    url.searchParams.set("limit", "25");
    if (view) {
      url.searchParams.set("view", view);
    }
    const res = await fetch(url.toString(), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const items = Array.isArray(data && data.items) ? data.items : [];
    items.forEach((item) => processInvalidationPayload(item));
    const nextAfterId = Number((data && data.next_after_id) || 0);
    if (Number.isFinite(nextAfterId) && nextAfterId > afterId) {
      afterId = Math.floor(nextAfterId);
    }
  }

  function ensureSseRetry() {
    if (sseRetryTimer) return;
    sseRetryTimer = window.setTimeout(function () {
      sseRetryTimer = null;
      startSSE();
    }, SSE_RETRY_MS);
  }

  function startPolling() {
    if (!isViewActive()) return;
    if (!pollUrlBase || pollTimer) return;
    if (!fallbackMode) {
      fallbackMode = true;
      reportObservability("fallback_entered", {});
    }
    pollOnce().catch(function () {});
    pollTimer = window.setInterval(function () {
      pollOnce().catch(function () {});
    }, POLL_INTERVAL_MS);
    ensureSseRetry();
  }

  function startSSE() {
    if (!isViewActive()) return;
    if (!streamUrl || !window.EventSource) {
      startPolling();
      return;
    }

    closeSSE();
    stopSseRetry();

    try {
      eventSource = new EventSource(streamUrl, { withCredentials: true });
    } catch (_) {
      startPolling();
      return;
    }

    eventSource.addEventListener("invalidation", function (ev) {
      try {
        processInvalidationPayload(JSON.parse(ev.data || "{}"));
      } catch (_) {}
    });

    eventSource.addEventListener("heartbeat", function (ev) {
      try {
        const payload = JSON.parse(ev.data || "{}");
        if (String((payload && payload.mode) || "") === "heartbeat_only") {
          startPolling();
        }
      } catch (_) {}
    });

    eventSource.onopen = function () {
      stopPolling();
      stopSseRetry();
      reportObservability("sse_open", {});
    };

    eventSource.onerror = function () {
      closeSSE();
      startPolling();
    };
  }

  function cleanup() {
    closeSSE();
    stopPolling();
    stopSseRetry();
    pendingByEntity.forEach((state) => {
      if (state && state.timer) window.clearTimeout(state.timer);
    });
    pendingByEntity.clear();
    regionState.forEach((state) => {
      if (state && state.timer) window.clearTimeout(state.timer);
    });
    regionState.clear();
  }

  window.addEventListener("beforeunload", cleanup);
  startSSE();
})();
