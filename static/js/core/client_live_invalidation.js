(function () {
  "use strict";

  const body = document.body;
  if (!body) return;
  if (String(body.getAttribute("data-client-live-enabled") || "") !== "1") return;

  const streamUrl = String(body.getAttribute("data-client-live-stream-url") || "").trim();
  const pollUrl = String(body.getAttribute("data-client-live-poll-url") || "").trim();
  const notificationsUrl = String(body.getAttribute("data-client-live-notifications-url") || "").trim();
  const chatConversationsUrl = String(body.getAttribute("data-client-chat-conversations-url") || "").trim();
  if (!streamUrl || !pollUrl) return;

  const EVENT_DEDUPE_TTL_MS = 8 * 60 * 1000;
  const EVENT_DEDUPE_MAX = 1200;
  const REFRESH_THROTTLE_MS = 1800;
  const SSE_RETRY_MS = Math.max(300, Number(window.__CLIENT_LIVE_SSE_RETRY_MS || 12000) || 12000);
  const POLL_MS_CONNECTED = Math.max(500, Number(window.__CLIENT_LIVE_POLL_CONNECTED_MS || 1500) || 1500);
  const POLL_MS_FALLBACK = Math.max(500, Number(window.__CLIENT_LIVE_POLL_FALLBACK_MS || 1200) || 1200);

  const seen = new Map();
  const refreshTimers = new Map();
  const refreshInflight = new Map();
  const refreshQueued = new Map();
  const interactionWaiters = new Map();
  const lastRefreshAt = new Map();
  const notifiedStaffMessages = new Map();

  let eventSource = null;
  let reconnectTimer = null;
  let pollTimer = null;
  let chatUnreadRefreshTimer = null;
  let chatUnreadRefreshInflight = false;
  let chatUnreadKnownCount = Math.max(0, Number(window.__clientChatUnreadCount || 0) || 0);
  let pollIntervalMs = 0;
  let fallbackMode = false;
  let liveDisabled = false;
  let ssePermanentlyDisabled = false;
  let pausedForHidden = Boolean(document.hidden);
  const initialAfterId = Math.max(0, Number(body.getAttribute("data-client-live-after-id") || 0) || 0);
  let afterId = initialAfterId;

  const runtime = (window.__clientLiveRuntime = window.__clientLiveRuntime || {
    mode: "booting",
    fallback: false,
    pollIntervalMs: 0,
    afterId: initialAfterId,
    transitions: [],
    pollTicks: 0,
    sseErrors: 0,
    sseOpens: 0,
    lastUpdatedAt: Date.now(),
  });

  function emitRuntime() {
    runtime.fallback = Boolean(fallbackMode);
    runtime.pollIntervalMs = Number(pollIntervalMs || 0);
    runtime.afterId = Number(afterId || 0);
    runtime.lastUpdatedAt = Date.now();
    if (typeof window.dispatchEvent === "function") {
      window.dispatchEvent(new CustomEvent("client-live:transport", { detail: {
        mode: runtime.mode,
        fallback: runtime.fallback,
        pollIntervalMs: runtime.pollIntervalMs,
        sseErrors: runtime.sseErrors,
        sseOpens: runtime.sseOpens,
        pollTicks: runtime.pollTicks,
      } }));
    }
  }

  function markTransport(mode, reason) {
    runtime.mode = String(mode || "unknown");
    runtime.transitions.push({
      mode: runtime.mode,
      reason: String(reason || ""),
      ts: Date.now(),
      fallback: Boolean(fallbackMode),
      pollIntervalMs: Number(pollIntervalMs || 0),
      afterId: Number(afterId || 0),
    });
    if (runtime.transitions.length > 100) runtime.transitions.splice(0, runtime.transitions.length - 100);
    emitRuntime();
  }

  function disableRealtime(reason) {
    if (liveDisabled) return;
    liveDisabled = true;
    if (eventSource) {
      try { eventSource.close(); } catch (_e) {}
      eventSource = null;
    }
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    markTransport("stopped_auth", String(reason || "auth_failed"));
  }

  function isCriticalView(viewName) {
    const view = String(viewName || "").trim();
    return view === "dashboard" || view === "solicitudes_list" || view === "solicitud_detail" || view === "chat";
  }

  function currentViewNode() {
    return document.querySelector("[data-client-live-view]");
  }

  function currentViewName() {
    const node = currentViewNode();
    return String((node && node.getAttribute("data-client-live-view")) || "").trim();
  }

  function currentSolicitudId() {
    const node = currentViewNode();
    const raw = node ? node.getAttribute("data-solicitud-id") : "";
    const n = Number(raw || 0);
    return Number.isFinite(n) ? Math.floor(n) : 0;
  }

  function parseJson(raw) {
    try {
      return JSON.parse(raw);
    } catch (_e) {
      return null;
    }
  }

  function isDuplicateEvent(eventId) {
    const key = String(eventId || "").trim();
    if (!key) return false;
    const now = Date.now();
    const prev = seen.get(key);
    if (prev && (now - prev) <= EVENT_DEDUPE_TTL_MS) return true;
    seen.set(key, now);
    if (seen.size > EVENT_DEDUPE_MAX) {
      const stale = Array.from(seen.entries()).sort((a, b) => a[1] - b[1]);
      const removeCount = Math.max(1, stale.length - EVENT_DEDUPE_MAX);
      for (let i = 0; i < removeCount; i += 1) seen.delete(stale[i][0]);
    }
    return false;
  }

  function isDuplicateStaffReply(messageId) {
    const key = String(messageId || "").trim();
    if (!key) return false;
    const now = Date.now();
    const prev = notifiedStaffMessages.get(key);
    if (prev && (now - prev) <= EVENT_DEDUPE_TTL_MS) return true;
    notifiedStaffMessages.set(key, now);
    if (notifiedStaffMessages.size > 800) {
      const stale = Array.from(notifiedStaffMessages.entries()).sort(function (a, b) { return a[1] - b[1]; });
      const removeCount = Math.max(1, stale.length - 800);
      for (let i = 0; i < removeCount; i += 1) notifiedStaffMessages.delete(stale[i][0]);
    }
    return false;
  }

  function updateChatUnreadBadges(unreadCount, emitEvent) {
    const count = Math.max(0, Number(unreadCount || 0) || 0);
    const shouldEmit = emitEvent !== false;
    chatUnreadKnownCount = count;
    window.__clientChatUnreadCount = count;
    document.querySelectorAll("[data-client-chat-unread-badge]").forEach(function (node) {
      node.textContent = String(count);
      node.classList.toggle("d-none", count <= 0);
    });
    if (shouldEmit && typeof window.dispatchEvent === "function") {
      window.dispatchEvent(new CustomEvent("client-chat:unread-updated", { detail: { unread_count: count } }));
    }
  }

  async function refreshChatUnreadCount() {
    if (document.hidden) return;
    if (!chatConversationsUrl || chatUnreadRefreshInflight) return;
    chatUnreadRefreshInflight = true;
    try {
      const resp = await fetch(chatConversationsUrl, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return;
      const payload = await resp.json();
      updateChatUnreadBadges(Number((payload && payload.unread_count) || 0), true);
    } catch (_e) {
      // no-op
    } finally {
      chatUnreadRefreshInflight = false;
    }
  }

  function scheduleChatUnreadRefresh(delayMs) {
    if (!chatConversationsUrl) return;
    if (chatUnreadRefreshTimer) window.clearTimeout(chatUnreadRefreshTimer);
    chatUnreadRefreshTimer = window.setTimeout(function () {
      refreshChatUnreadCount().catch(function () {});
    }, Math.max(80, Number(delayMs || 0) || 0));
  }

  function truncateText(value, maxLen) {
    const txt = String(value || "").trim();
    if (!txt) return "";
    const limit = Math.max(20, Number(maxLen || 0) || 0);
    if (txt.length <= limit) return txt;
    return txt.slice(0, limit - 1) + "…";
  }

  function showStaffReplyToast(evt) {
    const payload = (evt && typeof evt.payload === "object") ? evt.payload : {};
    const senderType = String(payload.sender_type || "").trim().toLowerCase();
    if (senderType !== "staff") return;
    const messageId = String(payload.message_id || "");
    if (isDuplicateStaffReply(messageId)) return;
    if (currentViewName() === "chat" && !document.hidden) return;

    let wrap = document.querySelector(".client-chat-reply-toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "client-chat-reply-toast-wrap";
      document.body.appendChild(wrap);
    }
    const toastNode = document.createElement("div");
    toastNode.className = "toast client-chat-reply-toast";
    toastNode.setAttribute("role", "status");
    toastNode.setAttribute("aria-live", "polite");
    toastNode.setAttribute("aria-atomic", "true");
    const preview = truncateText(payload.preview || "Tienes una nueva respuesta en tu chat de soporte.", 120);
    toastNode.innerHTML = [
      '<div class="toast-body">',
      '<div class="client-chat-reply-toast-title">Nuevo mensaje de soporte</div>',
      '<div class="client-chat-reply-toast-text">' + preview.replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</div>',
      '<div class="mt-2"><a class="btn btn-sm btn-primary" href="/clientes/chat">Abrir chat</a></div>',
      '</div>',
    ].join("");
    wrap.appendChild(toastNode);
    try {
      const inst = new bootstrap.Toast(toastNode, { delay: 8500, autohide: true });
      toastNode.addEventListener("hidden.bs.toast", function () {
        if (toastNode && toastNode.parentNode) toastNode.parentNode.removeChild(toastNode);
      }, { once: true });
      inst.show();
    } catch (_e) {
      window.setTimeout(function () {
        if (toastNode && toastNode.parentNode) toastNode.parentNode.removeChild(toastNode);
      }, 9000);
    }
  }

  async function fetchHtml(url) {
    const resp = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        Accept: "text/html,*/*",
      },
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    if (resp.redirected) throw new Error("REDIRECT");
    return resp.text();
  }

  function selectorForView(view) {
    if (view === "dashboard") return '[data-client-live-view="dashboard"]';
    if (view === "solicitudes_list") return '[data-client-live-view="solicitudes_list"]';
    if (view === "solicitud_detail") return '[data-client-live-view="solicitud_detail"]';
    if (view === "chat") return '[data-client-live-view="chat"]';
    return "";
  }

  function isSecurityField(el, name, type) {
    const fieldName = String(name || "").trim().toLowerCase();
    const fieldType = String(type || "").trim().toLowerCase();
    if (!fieldName) return false;
    if (fieldName === "csrf_token") return true;
    if (fieldType !== "hidden") return false;
    return /(csrf|token|nonce|authenticity|signature|hmac|captcha|recaptcha|xsrf|security)/i.test(fieldName);
  }

  function snapshotControls(root) {
    if (!root) return { values: {}, active: null };
    const values = {};
    root.querySelectorAll("input[name], select[name], textarea[name]").forEach(function (el) {
      const name = String(el.getAttribute("name") || "").trim();
      if (!name) return;
      const tag = String(el.tagName || "").toUpperCase();
      const type = String(el.getAttribute("type") || "").toLowerCase();
      if (isSecurityField(el, name, type)) return;
      const key = name;
      if (tag === "SELECT" && el.multiple) {
        values[key] = Array.from(el.selectedOptions || []).map(function (o) { return String(o.value || ""); });
        return;
      }
      if (type === "checkbox" || type === "radio") {
        if (!values[key] || !Array.isArray(values[key])) values[key] = [];
        if (el.checked) values[key].push(String(el.value || "on"));
        return;
      }
      values[key] = String(el.value || "");
    });

    const activeEl = document.activeElement;
    let active = null;
    if (activeEl && root.contains(activeEl)) {
      const name = String(activeEl.getAttribute("name") || "").trim();
      if (name) {
        active = {
          name: name,
          selectionStart: Number(activeEl.selectionStart || 0),
          selectionEnd: Number(activeEl.selectionEnd || 0),
        };
      }
    }
    return { values: values, active: active };
  }

  function restoreControls(root, snap) {
    if (!root || !snap || !snap.values) return;
    const values = snap.values || {};
    Object.keys(values).forEach(function (name) {
      const val = values[name];
      const controls = root.querySelectorAll('[name="' + String(name).replace(/"/g, '\\"') + '"]');
      if (!controls || !controls.length) return;
      controls.forEach(function (el) {
        const tag = String(el.tagName || "").toUpperCase();
        const type = String(el.getAttribute("type") || "").toLowerCase();
        if (isSecurityField(el, name, type)) return;
        if (tag === "SELECT" && el.multiple && Array.isArray(val)) {
          const set = new Set(val.map(function (x) { return String(x || ""); }));
          Array.from(el.options || []).forEach(function (opt) {
            opt.selected = set.has(String(opt.value || ""));
          });
          return;
        }
        if (type === "checkbox" || type === "radio") {
          const selected = Array.isArray(val) ? val.map(function (x) { return String(x || ""); }) : [];
          el.checked = selected.includes(String(el.value || "on"));
          return;
        }
        if (typeof val === "string") {
          el.value = val;
        }
      });
    });

    if (snap.active && snap.active.name) {
      const target = root.querySelector('[name="' + String(snap.active.name).replace(/"/g, '\\"') + '"]');
      if (target && typeof target.focus === "function") {
        const focusTarget = function () {
          target.focus();
          try {
            if (typeof target.setSelectionRange === "function") {
              target.setSelectionRange(Number(snap.active.selectionStart || 0), Number(snap.active.selectionEnd || 0));
            }
          } catch (_e) {}
        };
        focusTarget();
        window.setTimeout(focusTarget, 30);
        try {
          window.requestAnimationFrame(focusTarget);
        } catch (_e) {}
      }
      return;
    }

    const candidateNames = Object.keys(values).filter(function (name) {
      return typeof values[name] === "string" && String(values[name] || "").trim() !== "";
    });
    for (let i = 0; i < candidateNames.length; i += 1) {
      const nm = candidateNames[i];
      const control = root.querySelector('[name="' + String(nm).replace(/"/g, '\\"') + '"]');
      if (!control || typeof control.focus !== "function") continue;
      const tag = String(control.tagName || "").toUpperCase();
      const type = String(control.getAttribute("type") || "").toLowerCase();
      if (tag === "TEXTAREA" || tag === "SELECT" || type === "text" || type === "search" || type === "date" || type === "number") {
        control.focus();
        break;
      }
    }
  }

  function hasDirtyForm(root) {
    if (!root) return false;
    const forms = root.querySelectorAll("form");
    for (let i = 0; i < forms.length; i += 1) {
      if (String(forms[i].getAttribute("data-client-live-dirty") || "") === "1") return true;
    }
    return false;
  }

  function ensureDirtyFormTracking(root) {
    if (!root) return;
    const forms = root.querySelectorAll("form");
    forms.forEach(function (form) {
      if (form.__clientLiveDirtyHooked) return;
      form.__clientLiveDirtyHooked = true;
      form.setAttribute("data-client-live-dirty", "0");
      const markDirty = function () { form.setAttribute("data-client-live-dirty", "1"); };
      const resetDirty = function () { form.setAttribute("data-client-live-dirty", "0"); };
      form.addEventListener("input", markDirty, true);
      form.addEventListener("change", markDirty, true);
      form.addEventListener("submit", resetDirty, true);
    });
  }

  async function refreshView(view) {
    const selector = selectorForView(view);
    if (!selector) return false;
    const target = document.querySelector(selector);
    if (!target) return false;
    const activeEl = document.activeElement;
    const activeTag = activeEl ? String(activeEl.tagName || "").toUpperCase() : "";
    const isInteractive = activeEl && (activeTag === "INPUT" || activeTag === "SELECT" || activeTag === "TEXTAREA");
    if (isInteractive && target.contains(activeEl)) {
      const err = new Error("ACTIVE_INTERACTION");
      err.activeEl = activeEl;
      throw err;
    }
    if (hasDirtyForm(target)) {
      throw new Error("DIRTY_FORM");
    }
    const snapshot = snapshotControls(target);
    const html = await fetchHtml(window.location.pathname + window.location.search);
    const doc = new DOMParser().parseFromString(html, "text/html");
    const next = doc.querySelector(selector);
    if (!next) throw new Error("MISSING_TARGET");
    target.innerHTML = next.innerHTML;
    ensureDirtyFormTracking(target);
    restoreControls(target, snapshot);
    if (typeof window.dispatchEvent === "function") {
      window.dispatchEvent(new CustomEvent("client-live:view-refreshed", { detail: { view } }));
    }
    return true;
  }

  function armRefreshAfterInteraction(view, activeEl) {
    if (!view || interactionWaiters.has(view)) return;
    const waiter = { activeEl: activeEl || null, timer: null, onDone: null };
    const done = function () {
      if (!interactionWaiters.has(view)) return;
      interactionWaiters.delete(view);
      if (waiter.timer) window.clearTimeout(waiter.timer);
      if (waiter.activeEl && waiter.onDone && typeof waiter.activeEl.removeEventListener === "function") {
        waiter.activeEl.removeEventListener("blur", waiter.onDone);
        waiter.activeEl.removeEventListener("change", waiter.onDone);
      }
      scheduleRefresh(view);
    };
    waiter.onDone = function () { done(); };
    if (waiter.activeEl && typeof waiter.activeEl.addEventListener === "function") {
      waiter.activeEl.addEventListener("blur", waiter.onDone);
      waiter.activeEl.addEventListener("change", waiter.onDone);
    }
    waiter.timer = window.setTimeout(done, 5000);
    interactionWaiters.set(view, waiter);
  }

  function scheduleRefresh(view) {
    if (!view) return;
    if (refreshTimers.has(view)) return;
    const last = Number(lastRefreshAt.get(view) || 0);
    const elapsed = Date.now() - last;
    const wait = elapsed >= REFRESH_THROTTLE_MS ? 10 : Math.max(120, REFRESH_THROTTLE_MS - elapsed);

    refreshTimers.set(view, window.setTimeout(async function () {
      refreshTimers.delete(view);
      if (refreshInflight.get(view)) {
        refreshQueued.set(view, true);
        return;
      }
      refreshInflight.set(view, true);
      try {
        await refreshView(view);
      } catch (err) {
        const msg = String((err && err.message) || "");
        if (msg === "ACTIVE_INTERACTION") {
          armRefreshAfterInteraction(view, err.activeEl || null);
        } else if (msg === "DIRTY_FORM") {
          armRefreshAfterInteraction(view, null);
        }
      } finally {
        lastRefreshAt.set(view, Date.now());
        refreshInflight.set(view, false);
        if (refreshQueued.get(view)) {
          refreshQueued.set(view, false);
          scheduleRefresh(view);
        }
      }
    }, wait));
  }

  function scheduleNotificationRefresh() {
    if (document.hidden) return;
    if (window.ClientNotifications && typeof window.ClientNotifications.refresh === "function") {
      window.ClientNotifications.refresh({ silent: !(window.ClientNotifications.isOpen && window.ClientNotifications.isOpen()) });
      return;
    }
    if (!notificationsUrl) return;
    fetch(notificationsUrl + "?limit=1", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    }).then(function (resp) {
      if (!resp.ok) return null;
      return resp.json();
    }).then(function (payload) {
      if (!payload) return;
      const badge = document.getElementById("clientNotifBadge");
      const unread = Math.max(0, Number((payload && payload.unread_count) || 0));
      if (badge) {
        badge.textContent = String(unread);
        badge.classList.toggle("d-none", unread <= 0);
      }
    }).catch(function () {});
  }

  function scheduleChatRefresh(evt) {
    const chatApi = window.ClientChat;
    scheduleChatUnreadRefresh(120);
    if (!chatApi) return;
    if (typeof chatApi.applyLiveEvent === "function") {
      try {
        chatApi.applyLiveEvent(evt);
        return;
      } catch (_e) {
        // fallback below
      }
    }
    if (typeof chatApi.refreshConversations !== "function") return;
    try {
      chatApi.refreshConversations({ silent: true });
      const payload = (evt && evt.payload && typeof evt.payload === "object") ? evt.payload : {};
      const targetConversationId = Number(payload.conversation_id || (((evt || {}).target || {}).conversation_id) || 0);
      const selectedConversationId = (typeof chatApi.selectedConversationId === "function")
        ? Number(chatApi.selectedConversationId() || 0)
        : 0;
      if (
        Number.isFinite(targetConversationId)
        && targetConversationId > 0
        && targetConversationId === selectedConversationId
        && typeof chatApi.refreshMessages === "function"
      ) {
        chatApi.refreshMessages(targetConversationId, { silent: true });
      } else if (selectedConversationId > 0 && typeof chatApi.refreshMessages === "function") {
        chatApi.refreshMessages(selectedConversationId, { silent: true });
      }
    } catch (_e) {}
  }

  function viewsFromEvent(evt) {
    if (!evt || typeof evt !== "object") return [];
    const v = ((evt.invalidate || {}).views || []);
    if (Array.isArray(v) && v.length) {
      return v.map(function (x) { return String(x || "").trim(); }).filter(Boolean);
    }
    return [];
  }

  function shouldRefreshDetail(evt) {
    const view = currentViewName();
    if (view !== "solicitud_detail") return false;
    const viewSid = currentSolicitudId();
    const evtSid = Number((((evt || {}).target || {}).solicitud_id) || 0);
    if (!Number.isFinite(viewSid) || viewSid <= 0) return true;
    if (!Number.isFinite(evtSid) || evtSid <= 0) return false;
    return Math.floor(viewSid) === Math.floor(evtSid);
  }

  function applyEvent(evt) {
    if (!evt || isDuplicateEvent(evt.event_id)) return;
    afterId = Math.max(afterId, Number(evt.outbox_id || 0));
    runtime.afterId = Number(afterId || 0);
    const view = currentViewName();
    const invalidateViews = new Set(viewsFromEvent(evt));
    const eventType = String((evt && evt.event_type) || "").trim().toLowerCase();
    const isChatTypingEvent = eventType === "cliente.chat.typing";

    if (view === "dashboard" && invalidateViews.has("dashboard")) scheduleRefresh("dashboard");
    if (view === "solicitudes_list" && invalidateViews.has("solicitudes_list")) scheduleRefresh("solicitudes_list");
    if (shouldRefreshDetail(evt) && invalidateViews.has("solicitud_detail")) scheduleRefresh("solicitud_detail");
    if (view === "chat" && invalidateViews.has("chat")) scheduleChatRefresh(evt);
    if (invalidateViews.has("chat") && view !== "chat" && !isChatTypingEvent) scheduleChatUnreadRefresh(120);
    if (eventType === "cliente.chat.message_created") showStaffReplyToast(evt);
    if (invalidateViews.has("notifications")) scheduleNotificationRefresh();
  }

  async function pollOnce() {
    if (liveDisabled) return;
    runtime.pollTicks += 1;
    emitRuntime();
    const u = new URL(pollUrl, window.location.origin);
    u.searchParams.set("after_id", String(Math.max(0, afterId)));
    u.searchParams.set("limit", "60");
    const resp = await fetch(u.toString(), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    const authFailed = resp.redirected
      || resp.status === 401
      || resp.status === 403
      || /\/clientes\/login(?:[/?#]|$)/i.test(String(resp.url || ""));
    if (authFailed) {
      disableRealtime("poll_auth_failed");
      return;
    }
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const contentType = String(resp.headers.get("Content-Type") || "").toLowerCase();
    if (contentType.indexOf("application/json") < 0) {
      disableRealtime("poll_non_json");
      return;
    }
    const payload = await resp.json();
    const mode = String((payload && payload.mode) || "").trim().toLowerCase();
    if (mode === "poll_only") {
      ssePermanentlyDisabled = true;
      clearReconnectTimer();
      stopSse();
      setFallbackMode(true);
    }
    const items = Array.isArray(payload.items) ? payload.items : [];
    items.forEach(applyEvent);
    afterId = Math.max(afterId, Number(payload.next_after_id || 0));
    runtime.afterId = Number(afterId || 0);
    emitRuntime();
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect() {
    if (liveDisabled) return;
    if (ssePermanentlyDisabled) return;
    if (pausedForHidden) return;
    clearReconnectTimer();
    markTransport("sse_reconnecting", "timer_scheduled");
    reconnectTimer = window.setTimeout(function () {
      startSse();
    }, SSE_RETRY_MS);
  }

  function stopPollingLoop() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    pollIntervalMs = 0;
  }

  function resolveFallbackPollInterval() {
    const view = currentViewName();
    const critical = isCriticalView(view);
    if (document.hidden) return 30000;
    if (critical) return POLL_MS_FALLBACK;
    return Math.max(POLL_MS_FALLBACK, 5000);
  }

  function startPollingLoop() {
    if (liveDisabled) return;
    if (pausedForHidden) {
      stopPollingLoop();
      markTransport("paused_hidden", "poll_suspended");
      return;
    }
    if (!fallbackMode) {
      stopPollingLoop();
      return;
    }
    const wait = resolveFallbackPollInterval();
    if (pollTimer && pollIntervalMs === wait) return;
    stopPollingLoop();
    pollIntervalMs = wait;
    markTransport("polling_fallback", "interval_set");
    pollTimer = window.setInterval(function () {
      pollOnce().catch(function () {});
    }, wait);
    pollOnce().catch(function () {});
  }

  function setFallbackMode(enabled) {
    if (liveDisabled) return;
    const next = Boolean(enabled);
    if (fallbackMode === next) return;
    fallbackMode = next;
    markTransport(next ? "polling_fallback" : "sse_connected", next ? "sse_down" : "sse_up");
    startPollingLoop();
  }

  function stopSse() {
    if (eventSource) {
      try { eventSource.close(); } catch (_e) {}
      eventSource = null;
    }
  }

  function startSse() {
    if (liveDisabled) return;
    if (ssePermanentlyDisabled) {
      setFallbackMode(true);
      startPollingLoop();
      return;
    }
    if (pausedForHidden) {
      markTransport("paused_hidden", "sse_suspended");
      return;
    }
    clearReconnectTimer();
    stopSse();
    if (!("EventSource" in window)) {
      setFallbackMode(true);
      markTransport("polling_fallback", "eventsource_unsupported");
      startPollingLoop();
      return;
    }
    const u = new URL(streamUrl, window.location.origin);
    u.searchParams.set("after_id", String(Math.max(0, afterId)));
    eventSource = new EventSource(u.toString(), { withCredentials: true });
    markTransport("sse_connecting", "eventsource_opening");

    eventSource.onopen = function () {
      runtime.sseOpens += 1;
      setFallbackMode(false);
      markTransport("sse_connected", "open");
    };
    eventSource.addEventListener("invalidation", function (ev) {
      setFallbackMode(false);
      const data = parseJson(ev.data);
      if (!data) return;
      applyEvent(data);
    });
    eventSource.addEventListener("heartbeat", function (_ev) {
      setFallbackMode(false);
      markTransport("sse_connected", "heartbeat");
    });
    eventSource.addEventListener("poll_only", function (_ev) {
      ssePermanentlyDisabled = true;
      setFallbackMode(true);
      markTransport("polling_fallback", "server_poll_only");
      clearReconnectTimer();
      stopSse();
      startPollingLoop();
    });
    eventSource.onerror = function () {
      if (ssePermanentlyDisabled) {
        setFallbackMode(true);
        stopSse();
        clearReconnectTimer();
        startPollingLoop();
        return;
      }
      runtime.sseErrors += 1;
      setFallbackMode(true);
      markTransport("polling_fallback", "sse_error");
      stopSse();
      scheduleReconnect();
      startPollingLoop();
    };
    startPollingLoop();
  }

  window.addEventListener("client-chat:unread-updated", function (ev) {
    const detail = (ev && ev.detail && typeof ev.detail === "object") ? ev.detail : {};
    if (typeof detail.unread_count === "undefined") return;
    updateChatUnreadBadges(Number(detail.unread_count || 0), false);
  });

  window.addEventListener("beforeunload", function () {
    stopSse();
    clearReconnectTimer();
    stopPollingLoop();
    if (chatUnreadRefreshTimer) window.clearTimeout(chatUnreadRefreshTimer);
    interactionWaiters.forEach(function (waiter, view) {
      if (waiter && waiter.timer) window.clearTimeout(waiter.timer);
      if (waiter && waiter.activeEl && waiter.onDone && typeof waiter.activeEl.removeEventListener === "function") {
        waiter.activeEl.removeEventListener("blur", waiter.onDone);
        waiter.activeEl.removeEventListener("change", waiter.onDone);
      }
      interactionWaiters.delete(view);
    });
  });

  runtime.forceFallbackForTest = function () {
    setFallbackMode(true);
    markTransport("polling_fallback", "forced_test");
    stopSse();
    scheduleReconnect();
    startPollingLoop();
    emitRuntime();
  };

  window.addEventListener("visibilitychange", function () {
    pausedForHidden = Boolean(document.hidden);
    if (pausedForHidden) {
      stopSse();
      clearReconnectTimer();
      stopPollingLoop();
      markTransport("paused_hidden", "document_hidden");
      return;
    }
    markTransport("resuming_visible", "document_visible");
    startSse();
  });

  ensureDirtyFormTracking(currentViewNode());
  updateChatUnreadBadges(chatUnreadKnownCount, false);
  scheduleChatUnreadRefresh(120);
  if (!pausedForHidden) startSse();
  else markTransport("paused_hidden", "boot_hidden");
})();
