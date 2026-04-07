(function () {
  "use strict";

  const body = document.body;
  if (!body) return;
  if (String(body.getAttribute("data-chat-global-badge-enabled") || "0").trim() !== "1") return;

  const link = document.getElementById("adminChatGlobalBadgeLink");
  const badge = document.getElementById("adminChatGlobalBadge");
  if (!link || !badge) return;

  const badgeUrl = String(body.getAttribute("data-chat-global-badge-url") || "").trim();
  const streamUrl = String(body.getAttribute("data-chat-global-stream-url") || "").trim();
  const pollUrl = String(body.getAttribute("data-chat-global-poll-url") || "").trim();
  if (!badgeUrl) return;

  let eventSource = null;
  let pollTimer = null;
  let reconnectTimer = null;
  let refreshTimer = null;
  let afterId = 0;
  let lastStreamId = "$";

  const seenEvents = new Map();
  const EVENT_DEDUPE_TTL_MS = 8 * 60 * 1000;

  function setCount(value) {
    const count = Math.max(0, Number(value || 0) || 0);
    badge.textContent = String(count);
    badge.classList.toggle("d-none", count <= 0);
    link.setAttribute("data-chat-unread-conversations", String(count));
  }

  function isDuplicateEvent(eventId) {
    const key = String(eventId || "").trim();
    if (!key) return false;
    const now = Date.now();
    const prev = seenEvents.get(key);
    if (prev && (now - prev) <= EVENT_DEDUPE_TTL_MS) return true;
    seenEvents.set(key, now);
    if (seenEvents.size > 500) {
      const entries = Array.from(seenEvents.entries()).sort(function (a, b) { return a[1] - b[1]; });
      const removeCount = Math.max(1, entries.length - 500);
      for (let i = 0; i < removeCount; i += 1) seenEvents.delete(entries[i][0]);
    }
    return false;
  }

  async function fetchJson(url) {
    const resp = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  function scheduleRefresh(delayMs) {
    if (refreshTimer) window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(function () {
      refreshBadge().catch(function () {});
    }, Math.max(80, Number(delayMs || 0) || 0));
  }

  async function refreshBadge() {
    const payload = await fetchJson(badgeUrl);
    setCount(payload && payload.unread_conversations);
    return payload;
  }

  function isChatInvalidation(evt) {
    if (!evt || typeof evt !== "object") return false;
    const target = (evt.target && typeof evt.target === "object") ? evt.target : {};
    return String(target.entity_type || "").trim().toLowerCase() === "chat_conversation";
  }

  function handleLiveEvent(evt) {
    if (!evt || typeof evt !== "object") return;
    if (isDuplicateEvent(evt.event_id)) return;
    if (evt.stream_id) lastStreamId = String(evt.stream_id || lastStreamId);
    afterId = Math.max(afterId, Number(evt.outbox_id || 0) || 0);
    if (!isChatInvalidation(evt)) return;
    scheduleRefresh(120);
  }

  async function pollOnce() {
    if (!pollUrl) return;
    const u = new URL(pollUrl, window.location.origin);
    u.searchParams.set("after_id", String(Math.max(0, afterId)));
    u.searchParams.set("limit", "40");
    u.searchParams.set("view", "chat_inbox");
    const payload = await fetchJson(u.toString());
    const items = Array.isArray(payload && payload.items) ? payload.items : [];
    items.forEach(handleLiveEvent);
    afterId = Math.max(afterId, Number((payload && payload.next_after_id) || 0) || 0);
  }

  function startPolling() {
    if (pollTimer || !pollUrl) return;
    pollTimer = window.setInterval(function () {
      pollOnce().catch(function () {});
    }, 10000);
    pollOnce().catch(function () {});
  }

  function stopPolling() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function closeSSE() {
    if (!eventSource) return;
    try { eventSource.close(); } catch (_e) {}
    eventSource = null;
  }

  function scheduleReconnect() {
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(function () {
      startSSE();
    }, 12000);
  }

  function startSSE() {
    closeSSE();
    if (!("EventSource" in window) || !streamUrl) {
      startPolling();
      return;
    }

    const u = new URL(streamUrl, window.location.origin);
    u.searchParams.set("last_stream_id", String(lastStreamId || "$"));
    eventSource = new EventSource(u.toString(), { withCredentials: true });

    eventSource.addEventListener("invalidation", function (ev) {
      try {
        const data = JSON.parse(String(ev.data || "{}"));
        handleLiveEvent(data);
      } catch (_e) {}
    });
    eventSource.addEventListener("heartbeat", function (ev) {
      try {
        const hb = JSON.parse(String((ev && ev.data) || "{}"));
        if (hb && hb.last_stream_id) lastStreamId = String(hb.last_stream_id || lastStreamId);
      } catch (_e) {}
    });
    eventSource.onerror = function () {
      closeSSE();
      startPolling();
      scheduleReconnect();
    };

    startPolling();
  }

  window.addEventListener("beforeunload", function () {
    closeSSE();
    stopPolling();
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    if (refreshTimer) window.clearTimeout(refreshTimer);
  });

  refreshBadge().catch(function () {});
  startSSE();
})();
