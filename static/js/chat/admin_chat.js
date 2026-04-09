(function () {
  "use strict";

  const root = document.getElementById("adminChatRoot");
  if (!root) return;

  const listNode = document.getElementById("adminChatConversationList");
  const messagesNode = document.getElementById("adminChatMessages");
  const form = document.getElementById("adminChatComposeForm");
  const bodyInput = document.getElementById("adminChatBody");
  const charCountNode = document.getElementById("adminChatCharCount");
  const sendBtn = document.getElementById("adminChatSendBtn");
  const markReadBtn = document.getElementById("adminChatMarkReadBtn");
  const takeBtn = document.getElementById("adminChatTakeBtn");
  const releaseBtn = document.getElementById("adminChatReleaseBtn");
  const reassignSelect = document.getElementById("adminChatReassignSelect");
  const reassignBtn = document.getElementById("adminChatReassignBtn");
  const markPendingBtn = document.getElementById("adminChatMarkPendingBtn");
  const markClosedBtn = document.getElementById("adminChatMarkClosedBtn");
  const reopenBtn = document.getElementById("adminChatReopenBtn");
  const threadTitle = document.getElementById("adminChatThreadTitle");
  const threadStatus = document.getElementById("adminChatThreadStatus");
  const threadMeta = document.getElementById("adminChatThreadMeta");
  const clientePresenceNode = document.getElementById("adminChatClientePresence");
  const typingSlotNode = document.getElementById("adminChatTypingSlot");
  const typingIndicatorNode = document.getElementById("adminChatTypingIndicator");
  const typingIndicatorLabelNode = document.getElementById("adminChatTypingLabel");
  const goClienteLink = document.getElementById("adminChatGoClienteLink");
  const goSolicitudLink = document.getElementById("adminChatGoSolicitudLink");
  const threadAssignmentMeta = document.getElementById("adminChatThreadAssignmentMeta");
  const threadSlaSummary = document.getElementById("adminChatThreadSlaSummary");
  const ownershipWarning = document.getElementById("adminChatOwnershipWarning");
  const ownershipWarningName = document.getElementById("adminChatOwnershipWarningName");
  const filterForm = document.getElementById("adminChatFilterForm");
  const loadOlderBtn = document.getElementById("adminChatLoadOlderBtn");
  const historyStateNode = document.getElementById("adminChatHistoryState");
  const quickRepliesNode = document.getElementById("adminChatQuickReplies");
  const quickRepliesToggleBtn = document.getElementById("adminChatQuickRepliesToggle");
  const quickRepliesBodyNode = document.getElementById("adminChatQuickRepliesBody");
  const sendBtnDefaultHtml = sendBtn ? sendBtn.innerHTML : "";

  const conversationsUrl = String(root.getAttribute("data-conversations-url") || "").trim();
  const messagesTpl = String(root.getAttribute("data-messages-url-template") || "").trim();
  const sendTpl = String(root.getAttribute("data-send-url-template") || "").trim();
  const readTpl = String(root.getAttribute("data-read-url-template") || "").trim();
  const typingTpl = String(root.getAttribute("data-typing-url-template") || "").trim();
  const takeTpl = String(root.getAttribute("data-take-url-template") || "").trim();
  const releaseTpl = String(root.getAttribute("data-release-url-template") || "").trim();
  const assignTpl = String(root.getAttribute("data-assign-url-template") || "").trim();
  const markPendingTpl = String(root.getAttribute("data-mark-pending-url-template") || "").trim();
  const markClosedTpl = String(root.getAttribute("data-mark-closed-url-template") || "").trim();
  const reopenTpl = String(root.getAttribute("data-reopen-url-template") || "").trim();
  const streamUrl = String(root.getAttribute("data-stream-url") || "").trim();
  const pollUrl = String(root.getAttribute("data-poll-url") || "").trim();
  const initialQuery = String(root.getAttribute("data-query") || "").trim();
  const initialOnlyUnread = String(root.getAttribute("data-only-unread") || "0").trim() === "1";
  const initialStatusFilter = String(root.getAttribute("data-status-filter") || "open").trim().toLowerCase();
  const initialAssignmentFilter = String(root.getAttribute("data-assignment-filter") || "all").trim().toLowerCase();
  const canReassign = String(root.getAttribute("data-can-reassign") || "0").trim() === "1";
  const messageMaxLen = Math.max(1, Number(root.getAttribute("data-chat-message-max-len") || 1800) || 1800);

  const PAGE_SIZE = 50;
  const POLL_INTERVAL_MS = 3000;
  const POLL_HIDDEN_INTERVAL_MS = 12000;
  const COMPOSE_MIN_HEIGHT_PX = 44;
  const COMPOSE_MAX_HEIGHT_PX = 168;

  let selectedConversationId = Number(root.getAttribute("data-selected-conversation-id") || 0) || 0;
  let loadingLatest = false;
  let loadingOlder = false;
  let eventSource = null;
  let pollTimer = null;
  let pollLoopActive = false;
  let pollInFlight = false;
  let pollCooldownUntil = 0;
  let pollErrorStreak = 0;
  let reconnectTimer = null;
  let sseDisabledByMode = false;
  let streamModeProbe = null;
  let nextBeforeId = 0;
  let hasMoreHistory = false;
  let sendingMessage = false;
  let composeStatusTimer = null;
  let localTypingActive = false;
  let lastTypingEmitAt = 0;
  let typingPulseTimer = null;
  let remoteTypingHideTimer = null;
  let remoteTypingMutedUntil = 0;
  let conversationsRefreshTimer = null;
  let conversationsRefreshInflight = false;
  let conversationsRefreshQueued = false;
  let messageSyncTimer = null;
  let markReadTimer = null;
  const initialAfterId = Math.max(0, Number(root.getAttribute("data-initial-after-id") || 0) || 0);
  let afterId = initialAfterId;
  let lastStreamId = "$";
  const SSE_MODE_STORAGE_KEY = "admin_live_invalidation_mode";

  function readStoredSseMode() {
    try {
      return String(window.sessionStorage.getItem(SSE_MODE_STORAGE_KEY) || "").trim().toLowerCase();
    } catch (_e) {
      return "";
    }
  }

  function markPollOnlyMode() {
    sseDisabledByMode = true;
    try {
      window.sessionStorage.setItem(SSE_MODE_STORAGE_KEY, "poll_only");
    } catch (_e) {}
  }

  if (readStoredSseMode() === "poll_only") {
    sseDisabledByMode = true;
  }

  const seen = new Map();
  const EVENT_DEDUPE_TTL_MS = 8 * 60 * 1000;

  function getCSRFToken() {
    const input = document.querySelector('input[name="csrf_token"]');
    if (input && input.value) return String(input.value || "").trim();
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? String(meta.getAttribute("content") || "").trim() : "";
  }

  function endpointFor(template, conversationId) {
    return String(template || "").replace("/0/", "/" + String(Number(conversationId || 0)) + "/");
  }

  function statusClass(status) {
    const s = String(status || "open").toLowerCase();
    if (s === "closed") return "text-bg-secondary";
    if (s === "pending") return "text-bg-warning";
    return "text-bg-success";
  }

  function statusText(status) {
    const s = String(status || "open").toLowerCase();
    if (s === "closed") return "CERRADA";
    if (s === "pending") return "PENDIENTE";
    return "ABIERTA";
  }

  function slaLevel(row) {
    const raw = String((row && row.sla_level) || "").trim().toLowerCase();
    if (raw === "overdue" || raw === "warning" || raw === "normal") return raw;
    return "none";
  }

  function slaClass(row) {
    const explicit = String((row && row.sla_badge_class) || "").trim();
    if (explicit) return explicit;
    const st = String((row && row.status) || "open").trim().toLowerCase();
    const level = slaLevel(row);
    if (st === "closed") return "text-bg-secondary";
    if (st === "pending") return "chat-badge-sla-pending";
    if (level === "overdue") return "text-bg-danger";
    if (level === "warning") return "text-bg-warning";
    return "text-bg-info";
  }

  function slaLabel(row) {
    const explicit = String((row && row.sla_label) || "").trim();
    if (explicit) return explicit;
    const st = String((row && row.status) || "open").trim().toLowerCase();
    if (st === "closed") return "Cerrada";
    if (st === "pending") return "Reciente";
    return "Reciente";
  }

  function slaSummary(row) {
    return String((row && row.sla_summary) || "").trim();
  }

  function assignmentBadgeHtml(row) {
    const assignedId = Number((row && row.assigned_staff_user_id) || 0) || 0;
    if (assignedId <= 0) {
      return '<span class="badge chat-badge-neutral admin-chat-assignment-badge">Sin asignar</span>';
    }
    const username = String((row && row.assigned_staff_username) || ("Staff #" + String(assignedId)));
    return '<span class="badge bg-primary admin-chat-assignment-badge">Asignada a: ' + esc(username) + "</span>";
  }

  function currentFilters() {
    if (!filterForm) {
      return {
        q: initialQuery,
        status: initialStatusFilter || "open",
        assignment: initialAssignmentFilter || "all",
        onlyUnread: initialOnlyUnread,
      };
    }
    const qNode = filterForm.querySelector('input[name="q"]');
    const statusNode = filterForm.querySelector('select[name="status"]');
    const assignmentNode = filterForm.querySelector('select[name="assignment"]');
    const unreadNode = filterForm.querySelector('input[name="only_unread"]');
    return {
      q: String((qNode && qNode.value) || "").trim(),
      status: String((statusNode && statusNode.value) || "open").trim().toLowerCase(),
      assignment: String((assignmentNode && assignmentNode.value) || "all").trim().toLowerCase(),
      onlyUnread: Boolean(unreadNode && unreadNode.checked),
    };
  }

  function conversationsApiUrl() {
    const u = new URL(conversationsUrl, window.location.origin);
    const f = currentFilters();
    if (f.q) u.searchParams.set("q", f.q);
    if (f.onlyUnread) u.searchParams.set("only_unread", "1");
    if (f.status) u.searchParams.set("status", f.status);
    if (f.assignment) u.searchParams.set("assignment", f.assignment);
    return u.toString();
  }

  function threadUrlForConversation(conversationId) {
    const u = new URL(window.location.pathname, window.location.origin);
    const f = currentFilters();
    u.searchParams.set("conversation_id", String(Number(conversationId || 0) || 0));
    if (f.q) u.searchParams.set("q", f.q);
    if (f.onlyUnread) u.searchParams.set("only_unread", "1");
    if (f.status) u.searchParams.set("status", f.status);
    if (f.assignment) u.searchParams.set("assignment", f.assignment);
    return u.pathname + u.search;
  }

  function clienteUrlForConversation(clienteId) {
    const cid = Number(clienteId || 0) || 0;
    if (cid <= 0) return "";
    return "/admin/clientes/" + String(cid);
  }

  function solicitudUrlForConversation(clienteId, solicitudId) {
    const cid = Number(clienteId || 0) || 0;
    const sid = Number(solicitudId || 0) || 0;
    if (cid <= 0 || sid <= 0) return "";
    return "/admin/clientes/" + String(cid) + "/solicitudes/" + String(sid);
  }

  function esc(raw) {
    return String(raw || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function fmtDate(iso) {
    if (!iso) return "-";
    try {
      return new Date(iso).toLocaleString("es-DO", { timeZone: "America/Santo_Domingo" });
    } catch (_e) {
      return String(iso || "");
    }
  }

  function isDuplicateEvent(eventId) {
    const key = String(eventId || "").trim();
    if (!key) return false;
    const now = Date.now();
    const prev = seen.get(key);
    if (prev && (now - prev) <= EVENT_DEDUPE_TTL_MS) return true;
    seen.set(key, now);
    if (seen.size > 800) {
      const entries = Array.from(seen.entries()).sort(function (a, b) { return a[1] - b[1]; });
      const remove = Math.max(1, entries.length - 800);
      for (let i = 0; i < remove; i += 1) seen.delete(entries[i][0]);
    }
    return false;
  }

  async function fetchJson(url, opts) {
    const resp = await fetch(url, Object.assign({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    }, opts || {}));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  function clearGlobalLoaderState() {
    try {
      if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
        window.AppLoader.hideAll();
      }
    } catch (_e) {}
    document.documentElement.classList.remove("is-loading");
    if (document.body) document.body.classList.remove("is-loading");
  }

  function rowForConversation(conversationId) {
    if (!listNode) return null;
    const cid = Number(conversationId || 0) || 0;
    if (!cid) return null;
    return listNode.querySelector('[data-conversation-id="' + String(cid) + '"]');
  }

  function setConversationUnreadBadge(row, unreadCount, className) {
    if (!row) return;
    const count = Math.max(0, Number(unreadCount || 0) || 0);
    let badge = row.querySelector("." + className);
    if (count <= 0) {
      if (badge && badge.parentNode) badge.parentNode.removeChild(badge);
      return;
    }
    if (!badge) {
      const topRight = row.querySelector(".admin-chat-conv-top-right");
      badge = document.createElement("span");
      badge.className = "badge rounded-pill text-bg-danger " + className;
      if (topRight) {
        topRight.appendChild(badge);
      } else {
        row.appendChild(badge);
      }
    }
    badge.textContent = String(count);
  }

  function patchConversationRowFromPayload(conversationId, payload) {
    const row = rowForConversation(conversationId);
    if (!row || !payload || typeof payload !== "object") return false;

    const statusRaw = String(payload.status || "").trim().toLowerCase();
    if (statusRaw) {
      const statusBadge = row.querySelector(".admin-chat-conv-status");
      if (statusBadge) {
        statusBadge.className = "badge admin-chat-conv-status " + statusClass(statusRaw);
        statusBadge.textContent = statusText(statusRaw);
      }
    }
    if (Object.prototype.hasOwnProperty.call(payload, "staff_unread_count")) {
      setConversationUnreadBadge(row, payload.staff_unread_count, "admin-chat-unread-badge");
    }
    const preview = String(payload.preview || "").trim();
    if (preview) {
      const previewNode = row.querySelector(".admin-chat-conv-preview");
      if (previewNode) previewNode.textContent = preview;
    }
    if (row.parentNode === listNode) {
      listNode.insertAdjacentElement("afterbegin", row);
    }
    return true;
  }

  function scheduleConversationsRefresh(delayMs) {
    const wait = Math.max(40, Number(delayMs || 0) || 0);
    if (conversationsRefreshTimer) window.clearTimeout(conversationsRefreshTimer);
    conversationsRefreshTimer = window.setTimeout(function () {
      conversationsRefreshTimer = null;
      if (conversationsRefreshInflight) {
        conversationsRefreshQueued = true;
        return;
      }
      conversationsRefreshInflight = true;
      refreshConversations({ silent: true }).catch(function () {
        // no-op
      }).finally(function () {
        conversationsRefreshInflight = false;
        if (conversationsRefreshQueued) {
          conversationsRefreshQueued = false;
          scheduleConversationsRefresh(140);
        }
      });
    }, wait);
  }

  function scheduleMessageSync(conversationId, delayMs) {
    const cid = Number(conversationId || 0) || 0;
    if (!cid) return;
    if (messageSyncTimer) window.clearTimeout(messageSyncTimer);
    const attemptSync = function (retriesLeft) {
      if (loadingLatest) {
        if ((Number(retriesLeft || 0) || 0) > 0) {
          messageSyncTimer = window.setTimeout(function () {
            attemptSync(Number(retriesLeft || 0) - 1);
          }, 90);
        } else {
          messageSyncTimer = null;
        }
        return;
      }
      messageSyncTimer = null;
      refreshMessages(cid, { silent: true, mode: "sync", postSync: false }).catch(function () {});
    };
    messageSyncTimer = window.setTimeout(function () {
      attemptSync(2);
    }, Math.max(50, Number(delayMs || 0) || 0));
  }

  function resizeComposeInput() {
    if (!bodyInput) return;
    bodyInput.style.height = "auto";
    const minH = Math.max(36, Number(COMPOSE_MIN_HEIGHT_PX || 0) || 44);
    const maxH = Math.max(minH, Number(COMPOSE_MAX_HEIGHT_PX || 0) || 168);
    const next = Math.max(minH, Math.min(maxH, Number(bodyInput.scrollHeight || minH)));
    bodyInput.style.height = String(next) + "px";
    bodyInput.style.overflowY = Number(bodyInput.scrollHeight || 0) > maxH ? "auto" : "hidden";
    updateComposeCharCount();
  }

  function updateComposeCharCount() {
    if (!charCountNode || !bodyInput) return;
    const len = String(bodyInput.value || "").length;
    charCountNode.textContent = String(len) + " / " + String(messageMaxLen);
  }

  function scheduleMarkRead(conversationId, delayMs, retriesLeft) {
    const cid = Number(conversationId || 0) || 0;
    if (!cid) return;
    if (markReadTimer) window.clearTimeout(markReadTimer);
    markReadTimer = window.setTimeout(function () {
      markReadTimer = null;
      markRead(cid).then(function (ok) {
        if (ok) {
          scheduleConversationsRefresh(180);
          return;
        }
        if ((Number(retriesLeft || 0) || 0) > 0) {
          scheduleMarkRead(cid, 500, Number(retriesLeft || 0) - 1);
        }
      }).catch(function () {
        if ((Number(retriesLeft || 0) || 0) > 0) {
          scheduleMarkRead(cid, 500, Number(retriesLeft || 0) - 1);
        }
      });
    }, Math.max(90, Number(delayMs || 0) || 0));
  }

  function parseQuickReplyBody(raw) {
    const value = String(raw || "").trim();
    if (!value) return "";
    try {
      const parsed = JSON.parse(value);
      return String(parsed || "").trim();
    } catch (_e) {
      return value;
    }
  }

  function setQuickRepliesCollapsed(collapsed) {
    if (!quickRepliesNode) return;
    const isCollapsed = Boolean(collapsed);
    quickRepliesNode.classList.toggle("is-collapsed", isCollapsed);
    if (quickRepliesBodyNode) {
      quickRepliesBodyNode.setAttribute("aria-hidden", isCollapsed ? "true" : "false");
    }
    if (quickRepliesToggleBtn) {
      quickRepliesToggleBtn.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
      const labelNode = quickRepliesToggleBtn.querySelector("span");
      if (labelNode) labelNode.textContent = isCollapsed ? "Abrir" : "Cerrar";
    }
  }

  function ensureComposeStatusNode() {
    if (!form) return null;
    let node = document.getElementById("adminChatComposeStatus");
    if (node) return node;
    node = document.createElement("div");
    node.id = "adminChatComposeStatus";
    node.className = "small mt-2 d-none";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    form.appendChild(node);
    return node;
  }

  function setComposeStatus(kind, text, autoClearMs) {
    const node = ensureComposeStatusNode();
    if (!node) return;
    if (composeStatusTimer) {
      window.clearTimeout(composeStatusTimer);
      composeStatusTimer = null;
    }
    const message = String(text || "").trim();
    if (!message) {
      node.textContent = "";
      node.className = "small mt-2 d-none";
      return;
    }
    const cls = kind === "error" ? "text-danger" : (kind === "success" ? "text-success" : "text-muted");
    node.textContent = message;
    node.className = "small mt-2 " + cls;
    const ms = Number(autoClearMs || 0) || 0;
    if (ms > 0) {
      composeStatusTimer = window.setTimeout(function () {
        node.textContent = "";
        node.className = "small mt-2 d-none";
        composeStatusTimer = null;
      }, ms);
    }
  }

  function setSendButtonState(isSending) {
    if (!sendBtn) return;
    sendBtn.disabled = Boolean(isSending);
    if (isSending) {
      sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
      sendBtn.setAttribute("aria-label", "Enviando...");
      return;
    }
    sendBtn.innerHTML = sendBtnDefaultHtml;
    sendBtn.setAttribute("aria-label", "Enviar");
  }

  function typingAnchorNode() {
    if (typingSlotNode && typingSlotNode.parentNode === messagesNode) return typingSlotNode;
    if (typingIndicatorNode && typingIndicatorNode.parentNode === messagesNode) return typingIndicatorNode;
    return null;
  }

  function muteRemoteTyping(ms) {
    const until = Date.now() + Math.max(0, Number(ms || 0) || 0);
    remoteTypingMutedUntil = Math.max(remoteTypingMutedUntil, until);
  }

  function setRemoteClientTyping(typingOn, label, expiresInSeconds, opts) {
    if (!typingIndicatorNode) return;
    const options = (opts && typeof opts === "object") ? opts : {};
    const force = Boolean(options.force);
    const on = Boolean(typingOn);
    if (on && !force && Date.now() < remoteTypingMutedUntil) return;
    if (remoteTypingHideTimer) {
      window.clearTimeout(remoteTypingHideTimer);
      remoteTypingHideTimer = null;
    }
    if (!on) {
      if (typingIndicatorLabelNode) typingIndicatorLabelNode.textContent = "";
      typingIndicatorNode.classList.add("d-none");
      typingIndicatorNode.setAttribute("aria-hidden", "true");
      return;
    }
    if (typingIndicatorLabelNode) {
      typingIndicatorLabelNode.textContent = String(label || "Cliente está escribiendo...");
    }
    typingIndicatorNode.classList.remove("d-none");
    typingIndicatorNode.setAttribute("aria-hidden", "false");
    const ttlMs = Math.max(300, (Math.max(1, Number(expiresInSeconds || 0) || 0) * 1000) + 300);
    remoteTypingHideTimer = window.setTimeout(function () {
      if (typingIndicatorLabelNode) typingIndicatorLabelNode.textContent = "";
      typingIndicatorNode.classList.add("d-none");
      typingIndicatorNode.setAttribute("aria-hidden", "true");
      remoteTypingHideTimer = null;
    }, ttlMs);
  }

  async function postTypingState(conversationId, isTyping) {
    const cid = Number(conversationId || 0) || 0;
    if (!typingTpl || !cid) return;
    const csrfToken = getCSRFToken();
    try {
      await fetch(endpointFor(typingTpl, cid), {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({
          is_typing: Boolean(isTyping),
          expires_in: 5,
        }),
      });
      lastTypingEmitAt = Date.now();
    } catch (_e) {
      // no-op
    }
  }

  function stopLocalTypingSignal() {
    if (typingPulseTimer) {
      window.clearTimeout(typingPulseTimer);
      typingPulseTimer = null;
    }
    if (!localTypingActive) return;
    localTypingActive = false;
    postTypingState(Number(selectedConversationId || 0), false).catch(function () {});
  }

  function scheduleLocalTypingSignal() {
    const cid = Number(selectedConversationId || 0) || 0;
    if (!cid || !bodyInput) return;
    const hasText = String(bodyInput.value || "").trim().length > 0;
    if (!hasText) {
      stopLocalTypingSignal();
      return;
    }
    const now = Date.now();
    if (!localTypingActive || (now - lastTypingEmitAt) >= 1200) {
      localTypingActive = true;
      postTypingState(cid, true).catch(function () {});
    }
    if (typingPulseTimer) window.clearTimeout(typingPulseTimer);
    typingPulseTimer = window.setTimeout(function () {
      if (document.hidden) return;
      scheduleLocalTypingSignal();
    }, 1400);
  }

  function insertIntoComposer(text) {
    if (!bodyInput) return;
    const snippet = String(text || "").trim();
    if (!snippet) return;
    const start = Number(bodyInput.selectionStart);
    const end = Number(bodyInput.selectionEnd);
    const hasRange = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end >= 0;
    const current = String(bodyInput.value || "");
    if (!hasRange) {
      bodyInput.value = current ? (current + "\n\n" + snippet) : snippet;
      bodyInput.focus();
      bodyInput.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    const left = current.slice(0, start);
    const right = current.slice(end);
    const merged = left + snippet + right;
    bodyInput.value = merged;
    const caretPos = left.length + snippet.length;
    bodyInput.focus();
    bodyInput.setSelectionRange(caretPos, caretPos);
    bodyInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function renderConversations(items) {
    if (!listNode) return;
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      listNode.innerHTML = '<div class="p-3 text-muted small">No hay conversaciones para mostrar.</div>';
      return;
    }
    listNode.innerHTML = rows.map(function (row) {
      const active = Number(row.id || 0) === Number(selectedConversationId || 0);
      const unread = Math.max(0, Number(row.staff_unread_count || 0));
      const solicitud = row.solicitud_codigo ? ('<div class="admin-chat-conv-linkmeta text-muted">Solicitud #' + esc(row.solicitud_codigo) + "</div>") : "";
      const st = String(row.status || "open").toLowerCase();
      const sLevel = slaLevel(row);
      return [
        '<a class="list-group-item list-group-item-action admin-chat-conv admin-chat-sla-' + esc(sLevel) + (active ? ' active' : '') + '" href="' + esc(threadUrlForConversation(row.id)) + '" data-no-loader="true" data-conversation-id="' + esc(row.id) + '">',
        '<div class="admin-chat-conv-top">',
        '<div class="admin-chat-conv-party">',
        '<div class="admin-chat-conv-name text-truncate">' + esc(row.cliente_nombre || ("Cliente #" + String(row.cliente_id || ""))) + "</div>",
        '<div class="admin-chat-conv-code">' + esc(row.cliente_codigo || "") + "</div>",
        "</div>",
        '<div class="admin-chat-conv-top-right">',
        unread > 0 ? '<span class="badge rounded-pill text-bg-danger admin-chat-unread-badge">' + String(unread) + '</span>' : '',
        '</div>',
        "</div>",
        '<div class="admin-chat-conv-subject text-truncate">' + esc(row.subject || "Soporte general") + "</div>",
        '<div class="admin-chat-conv-tags">',
        '<span class="badge admin-chat-conv-status ' + statusClass(st) + '">' + statusText(st) + "</span>",
        '<span class="badge ' + slaClass(row) + '">' + esc(slaLabel(row)) + "</span>",
        assignmentBadgeHtml(row),
        "</div>",
        '<div class="admin-chat-conv-summary text-muted">' + esc(slaSummary(row)) + "</div>",
        solicitud,
        '<div class="admin-chat-conv-preview text-muted text-truncate">' + esc(row.last_message_preview || "Sin mensajes todavía.") + "</div>",
        "</a>",
      ].join("");
    }).join('');
  }

  function messageHtml(m) {
    const mine = Boolean(m && m.is_mine);
    return [
      '<article class="admin-chat-msg ' + (mine ? 'mine' : 'theirs') + '" data-message-id="' + esc(m.id) + '">',
      '<div class="bubble">' + esc((m && m.body) || '') + '</div>',
      '<div class="meta">' + esc(fmtDate(m && m.created_at)) + ' · ' + esc((m && m.sender_name) || (mine ? 'Tú' : 'Cliente')) + '</div>',
      '</article>'
    ].join('');
  }

  function hasMessageId(messageId) {
    if (!messagesNode) return false;
    const id = Number(messageId || 0) || 0;
    if (!id) return false;
    return Boolean(messagesNode.querySelector('article[data-message-id="' + String(id) + '"]'));
  }

  function getMessageCount() {
    if (!messagesNode) return 0;
    return messagesNode.querySelectorAll("article[data-message-id]").length;
  }

  function clearThreadMessagesOnly() {
    if (!messagesNode) return;
    const nodes = messagesNode.querySelectorAll("article[data-message-id], #adminChatEmpty");
    nodes.forEach(function (node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
  }

  function clearEmptyState() {
    if (!messagesNode) return;
    const empty = messagesNode.querySelector("#adminChatEmpty");
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  }

  function showEmptyState() {
    if (!messagesNode) return;
    if (getMessageCount() > 0) return;
    clearEmptyState();
    const div = document.createElement("div");
    div.id = "adminChatEmpty";
    div.className = "p-3 text-muted small";
    div.textContent = "Aún no hay mensajes en esta conversación.";
    messagesNode.appendChild(div);
  }

  function appendMessages(rows) {
    if (!messagesNode) return 0;
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) return 0;
    clearEmptyState();
    let added = 0;
    list.forEach(function (m) {
      const id = Number((m && m.id) || 0) || 0;
      if (!id || hasMessageId(id)) return;
      const anchor = typingAnchorNode();
      if (anchor) {
        anchor.insertAdjacentHTML("beforebegin", messageHtml(m));
      } else {
        messagesNode.insertAdjacentHTML("beforeend", messageHtml(m));
      }
      added += 1;
    });
    return added;
  }

  function prependMessages(rows) {
    if (!messagesNode) return 0;
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) return 0;
    clearEmptyState();
    const first = messagesNode.querySelector("article[data-message-id]");
    let added = 0;
    list.forEach(function (m) {
      const id = Number((m && m.id) || 0) || 0;
      if (!id || hasMessageId(id)) return;
      const html = messageHtml(m);
      if (first && first.parentNode === messagesNode) {
        first.insertAdjacentHTML("beforebegin", html);
      } else {
        messagesNode.insertAdjacentHTML("beforeend", html);
      }
      added += 1;
    });
    return added;
  }

  function updateThreadMetaFromConversation(conv) {
    if (!conv || typeof conv !== "object") return;
    if (threadTitle) threadTitle.textContent = String(conv.subject || "Soporte");
    if (threadStatus) {
      const st = String(conv.status || "open").toLowerCase();
      threadStatus.className = "badge " + statusClass(st);
      threadStatus.textContent = statusText(st);
      if (markPendingBtn) markPendingBtn.classList.toggle("d-none", st === "pending");
      if (markClosedBtn) markClosedBtn.classList.toggle("d-none", st === "closed");
      if (reopenBtn) reopenBtn.classList.toggle("d-none", st === "open");
    }
    if (threadMeta) {
      const parts = [];
      if (conv.cliente_nombre) parts.push(String(conv.cliente_nombre));
      if (conv.solicitud_codigo) parts.push("Solicitud #" + String(conv.solicitud_codigo));
      threadMeta.textContent = parts.join(" · ");
    }
    if (clientePresenceNode) {
      const label = String(conv.cliente_presence_label || "").trim();
      if (label) {
        clientePresenceNode.textContent = label;
        clientePresenceNode.classList.remove("d-none");
      } else {
        clientePresenceNode.textContent = "";
        clientePresenceNode.classList.add("d-none");
      }
    }
    if (
      Object.prototype.hasOwnProperty.call(conv, "cliente_typing_in_this_chat")
      || Object.prototype.hasOwnProperty.call(conv, "cliente_typing_label")
      || Object.prototype.hasOwnProperty.call(conv, "cliente_typing_expires_in")
    ) {
      setRemoteClientTyping(
        Boolean(conv.cliente_typing_in_this_chat),
        String(conv.cliente_typing_label || ""),
        Number(conv.cliente_typing_expires_in || 0),
      );
    }
    const clienteUrl = clienteUrlForConversation(conv.cliente_id);
    const solicitudUrl = solicitudUrlForConversation(conv.cliente_id, conv.solicitud_id);
    if (goClienteLink) {
      if (clienteUrl) {
        goClienteLink.setAttribute("href", clienteUrl);
        goClienteLink.classList.remove("d-none");
      } else {
        goClienteLink.setAttribute("href", "#");
        goClienteLink.classList.add("d-none");
      }
    }
    if (goSolicitudLink) {
      if (solicitudUrl) {
        goSolicitudLink.setAttribute("href", solicitudUrl);
        goSolicitudLink.classList.remove("d-none");
      } else {
        goSolicitudLink.setAttribute("href", "#");
        goSolicitudLink.classList.add("d-none");
      }
    }
    const assignedId = Number(conv.assigned_staff_user_id || 0) || 0;
    const assignedUsername = String(conv.assigned_staff_username || "");
    if (threadAssignmentMeta) {
      if (assignedId > 0) {
        const safeName = esc(assignedUsername || ("Staff #" + String(assignedId)));
        threadAssignmentMeta.innerHTML = '<span class="badge bg-primary">Asignada a: ' + safeName + "</span>";
      } else {
        threadAssignmentMeta.innerHTML = '<span class="badge chat-badge-neutral">Sin asignar</span>';
      }
    }
    if (threadSlaSummary) {
      const summary = slaSummary(conv);
      threadSlaSummary.innerHTML = '<span class="badge ' + slaClass(conv) + '">' + esc(slaLabel(conv)) + '</span>'
        + (summary ? ('<span class="text-muted ms-1">' + esc(summary) + "</span>") : "");
    }
    if (ownershipWarning) {
      const isAssignedToOther = Boolean(conv.is_assigned_to_other) || (assignedId > 0 && !Boolean(conv.is_assigned_to_me));
      ownershipWarning.classList.toggle("d-none", !isAssignedToOther);
      if (isAssignedToOther && ownershipWarningName) {
        ownershipWarningName.textContent = assignedUsername || ("Staff #" + String(assignedId));
      }
    }
    if (takeBtn) takeBtn.classList.toggle("d-none", Boolean(conv.is_assigned_to_me));
    if (releaseBtn) {
      const hideRelease = assignedId <= 0 || (Boolean(conv.is_assigned_to_other) && !canReassign);
      releaseBtn.classList.toggle("d-none", hideRelease);
    }
    if (reassignBtn) reassignBtn.classList.toggle("d-none", !canReassign);
  }

  function updateHistoryControls() {
    const count = getMessageCount();
    if (loadOlderBtn) {
      const showBtn = Boolean(hasMoreHistory) && count > 0;
      loadOlderBtn.classList.toggle("d-none", !showBtn);
      loadOlderBtn.disabled = loadingOlder;
      loadOlderBtn.textContent = loadingOlder ? "Cargando..." : "Cargar mensajes anteriores";
    }
    if (!historyStateNode) return;
    if (loadingOlder) {
      historyStateNode.textContent = "Cargando historial...";
      return;
    }
    if (count <= 0) {
      historyStateNode.textContent = "";
      return;
    }
    historyStateNode.textContent = hasMoreHistory ? "" : "No hay más historial.";
  }

  function setPaginationFromPayload(payload) {
    const p = payload && typeof payload === "object" ? payload : {};
    hasMoreHistory = Boolean(p.has_more);
    nextBeforeId = Number(p.next_before_id || 0) || 0;
    updateHistoryControls();
  }

  function isNearBottom() {
    if (!messagesNode) return true;
    const remaining = messagesNode.scrollHeight - (messagesNode.scrollTop + messagesNode.clientHeight);
    return remaining <= 60;
  }

  function renderMessagesReset(payload) {
    if (!messagesNode) return;
    clearThreadMessagesOnly();
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
    const shouldHideTyping = rows.some(function (m) {
      return String((m && m.sender_type) || "").trim().toLowerCase() === "cliente";
    });
    if (shouldHideTyping) {
      muteRemoteTyping(300);
      setRemoteClientTyping(false, "", 0, { force: true });
    }
    rows.forEach(function (m) {
      const anchor = typingAnchorNode();
      if (anchor) {
        anchor.insertAdjacentHTML("beforebegin", messageHtml(m));
      } else {
        messagesNode.insertAdjacentHTML("beforeend", messageHtml(m));
      }
    });
    if (!rows.length) showEmptyState();
    setPaginationFromPayload(payload || {});
    updateThreadMetaFromConversation(payload && payload.conversation);
    messagesNode.scrollTop = messagesNode.scrollHeight;
  }

  function syncLatestMessages(payload) {
    if (!messagesNode) return;
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
    const shouldHideTyping = rows.some(function (m) {
      return String((m && m.sender_type) || "").trim().toLowerCase() === "cliente";
    });
    if (shouldHideTyping) {
      muteRemoteTyping(300);
      setRemoteClientTyping(false, "", 0, { force: true });
    }
    const stickBottom = isNearBottom();
    appendMessages(rows);
    if (getMessageCount() <= 0) showEmptyState();
    updateThreadMetaFromConversation(payload && payload.conversation);
    if (stickBottom) {
      messagesNode.scrollTop = messagesNode.scrollHeight;
    }
    updateHistoryControls();
  }

  async function refreshConversations(opts) {
    if (!conversationsUrl) return null;
    try {
      const payload = await fetchJson(conversationsApiUrl());
      renderConversations(payload.items || []);
      return payload;
    } catch (_e) {
      if (!(opts && opts.silent) && listNode) {
        listNode.innerHTML = '<div class="p-3 text-danger small">No se pudo cargar conversaciones.</div>';
      }
      return null;
    }
  }

  async function markRead(conversationId) {
    if (!conversationId) return false;
    const csrfToken = getCSRFToken();
    try {
      await fetchJson(endpointFor(readTpl, conversationId), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body: "csrf_token=" + encodeURIComponent(csrfToken),
      });
      return true;
    } catch (_e) {
      return false;
    }
  }

  async function changeStatus(conversationId, template) {
    if (!conversationId || !template) return null;
    const csrfToken = getCSRFToken();
    try {
      return await fetchJson(endpointFor(template, conversationId), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body: "csrf_token=" + encodeURIComponent(csrfToken),
      });
    } catch (_e) {
      return null;
    }
  }

  async function assignConversation(conversationId, assignedStaffUserId) {
    if (!conversationId || !assignTpl) return null;
    const staffId = Number(assignedStaffUserId || 0) || 0;
    if (!staffId) return null;
    const csrfToken = getCSRFToken();
    const body = new URLSearchParams();
    body.set("csrf_token", csrfToken);
    body.set("assigned_staff_user_id", String(staffId));
    try {
      return await fetchJson(endpointFor(assignTpl, conversationId), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body: body.toString(),
      });
    } catch (_e) {
      return null;
    }
  }

  async function refreshMessages(conversationId, opts) {
    const previousConversationId = Number(selectedConversationId || 0) || 0;
    const cid = Number(conversationId || previousConversationId || 0);
    const mode = String((opts && opts.mode) || "sync").toLowerCase();
    const postSync = !opts || opts.postSync !== false;
    if (!cid || loadingLatest) return null;
    loadingLatest = true;
    try {
      const payload = await fetchJson(endpointFor(messagesTpl, cid) + "?limit=" + String(PAGE_SIZE));
      selectedConversationId = cid;
      root.setAttribute("data-selected-conversation-id", String(cid));
      if (messagesNode) messagesNode.setAttribute("data-conversation-id", String(cid));

      const mustReset = mode === "reset" || previousConversationId !== Number(cid);
      if (mustReset) {
        renderMessagesReset(payload || {});
      } else {
        syncLatestMessages(payload || {});
      }

      if (postSync) {
        scheduleMarkRead(cid, 160, 1);
        scheduleConversationsRefresh(240);
      }
      return payload;
    } catch (_e) {
      if (!(opts && opts.silent) && messagesNode) {
        messagesNode.innerHTML = '<div class="p-3 text-danger small">No se pudo cargar mensajes.</div>';
      }
      return null;
    } finally {
      loadingLatest = false;
    }
  }

  async function loadOlderMessages() {
    const cid = Number(selectedConversationId || 0);
    if (!cid || !messagesNode || loadingOlder || loadingLatest || !hasMoreHistory || !nextBeforeId) return null;

    loadingOlder = true;
    updateHistoryControls();
    const prevHeight = messagesNode.scrollHeight;
    const prevTop = messagesNode.scrollTop;

    try {
      const url = endpointFor(messagesTpl, cid)
        + "?limit=" + String(PAGE_SIZE)
        + "&before_id=" + String(Number(nextBeforeId || 0));
      const payload = await fetchJson(url);
      const rows = Array.isArray(payload && payload.items) ? payload.items : [];
      prependMessages(rows);
      setPaginationFromPayload(payload || {});

      const newHeight = messagesNode.scrollHeight;
      messagesNode.scrollTop = prevTop + (newHeight - prevHeight);
      return payload;
    } catch (_e) {
      return null;
    } finally {
      loadingOlder = false;
      updateHistoryControls();
    }
  }

  if (listNode) {
    listNode.addEventListener("click", function (ev) {
      const link = ev.target.closest("[data-conversation-id]");
      if (!link) return;
      ev.preventDefault();
      const cid = Number(link.getAttribute("data-conversation-id") || 0) || 0;
      if (!cid) return;
      stopLocalTypingSignal();
      setRemoteClientTyping(false, "", 0);
      selectedConversationId = cid;
      nextBeforeId = 0;
      hasMoreHistory = false;
      if (window.history && typeof window.history.replaceState === "function") {
        const url = new URL(window.location.href);
        url.searchParams.set("conversation_id", String(cid));
        window.history.replaceState({}, "", url.toString());
      }
      refreshMessages(cid, { silent: false, mode: "reset" });
    });
  }

  if (loadOlderBtn) {
    loadOlderBtn.addEventListener("click", function () {
      loadOlderMessages().catch(function () {});
    });
  }

  if (markReadBtn) {
    markReadBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      markRead(cid).then(function () { scheduleConversationsRefresh(120); }).catch(function () {});
    });
  }

  if (takeBtn) {
    takeBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      changeStatus(cid, takeTpl).then(function () {
        return refreshMessages(cid, { silent: true, mode: "sync" });
      }).catch(function () {});
    });
  }

  if (releaseBtn) {
    releaseBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      changeStatus(cid, releaseTpl).then(function () {
        return refreshMessages(cid, { silent: true, mode: "sync" });
      }).catch(function () {});
    });
  }

  if (reassignBtn && reassignSelect) {
    reassignBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      const toStaffId = Number(reassignSelect.value || 0) || 0;
      if (!cid || !toStaffId) return;
      assignConversation(cid, toStaffId).then(function () {
        return refreshMessages(cid, { silent: true, mode: "sync" });
      }).catch(function () {});
    });
  }

  if (markPendingBtn) {
    markPendingBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      changeStatus(cid, markPendingTpl).then(function () { return refreshMessages(cid, { silent: true, mode: "sync" }); }).catch(function () {});
    });
  }

  if (markClosedBtn) {
    markClosedBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      changeStatus(cid, markClosedTpl).then(function () { return refreshMessages(cid, { silent: true, mode: "sync" }); }).catch(function () {});
    });
  }

  if (reopenBtn) {
    reopenBtn.addEventListener("click", function () {
      const cid = Number(selectedConversationId || 0);
      if (!cid) return;
      changeStatus(cid, reopenTpl).then(function () { return refreshMessages(cid, { silent: true, mode: "sync" }); }).catch(function () {});
    });
  }

  if (quickRepliesNode && bodyInput) {
    quickRepliesNode.addEventListener("click", function (ev) {
      const btn = ev.target.closest("[data-quick-reply-body]");
      if (!btn) return;
      const text = parseQuickReplyBody(btn.getAttribute("data-quick-reply-body"));
      if (!text) return;
      insertIntoComposer(text);
    });
  }

  if (quickRepliesToggleBtn && quickRepliesNode) {
    setQuickRepliesCollapsed(quickRepliesNode.classList.contains("is-collapsed"));
    quickRepliesToggleBtn.addEventListener("click", function () {
      setQuickRepliesCollapsed(!quickRepliesNode.classList.contains("is-collapsed"));
    });
  }

  if (bodyInput) {
    bodyInput.addEventListener("input", function () {
      scheduleLocalTypingSignal();
      resizeComposeInput();
    });
    bodyInput.addEventListener("blur", function () {
      stopLocalTypingSignal();
    });
    bodyInput.addEventListener("keydown", function (ev) {
      if (ev.isComposing) return;
      if (ev.key !== "Enter" || ev.shiftKey) return;
      ev.preventDefault();
      if (sendBtn && !sendBtn.disabled) sendBtn.click();
    });
    resizeComposeInput();
  }

  if (form && bodyInput && sendBtn) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (sendingMessage) return;
      const cid = Number(selectedConversationId || (messagesNode && messagesNode.getAttribute("data-conversation-id")) || 0) || 0;
      if (!cid) return;
      const text = String(bodyInput.value || "").trim();
      if (!text) return;
      stopLocalTypingSignal();
      const csrfToken = getCSRFToken();
      sendingMessage = true;
      setSendButtonState(true);
      clearGlobalLoaderState();
      setComposeStatus("pending", "Enviando...");
      try {
        const body = new URLSearchParams();
        body.set("csrf_token", csrfToken);
        body.set("body", text);
        const sendPayload = await fetchJson(endpointFor(sendTpl, cid), {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          },
          body: body.toString(),
        });
        bodyInput.value = "";
        resizeComposeInput();
        postTypingState(cid, false).catch(function () {});
        const sentMessage = sendPayload && sendPayload.message;
        const sentConversation = sendPayload && sendPayload.conversation;
        if (sentMessage && typeof sentMessage === "object") {
          appendMessages([sentMessage]);
          if (messagesNode) messagesNode.scrollTop = messagesNode.scrollHeight;
        }
        if (sentConversation && typeof sentConversation === "object") {
          updateThreadMetaFromConversation(sentConversation);
        }
        const warning = sendPayload && sendPayload.assignment_warning;
        if (warning && ownershipWarning) {
          ownershipWarning.classList.remove("d-none");
          if (ownershipWarningName) {
            ownershipWarningName.textContent = String(warning.assigned_staff_username || ("Staff #" + String(warning.assigned_staff_user_id || "")));
          }
        }
        setComposeStatus("success", "Mensaje enviado.", 1800);
        clearGlobalLoaderState();
        scheduleMarkRead(cid, 120, 1);
        scheduleConversationsRefresh(180);
      } catch (e) {
        const reason = (e && e.message) ? (" (" + String(e.message) + ")") : "";
        setComposeStatus("error", "No se pudo enviar el mensaje. Intenta de nuevo." + reason);
      } finally {
        sendingMessage = false;
        setSendButtonState(false);
        clearGlobalLoaderState();
      }
    });
  }

  function handleLiveEvent(evt) {
    if (!evt || typeof evt !== "object") return;
    if (isDuplicateEvent(evt.event_id)) return;
    afterId = Math.max(afterId, Number(evt.outbox_id || 0));
    if (evt.stream_id) {
      lastStreamId = String(evt.stream_id);
    }
    const target = (evt.target && typeof evt.target === "object") ? evt.target : {};
    if (String(target.entity_type || "") !== "chat_conversation") return;
    const payload = (evt.payload && typeof evt.payload === "object") ? evt.payload : {};
    const eventType = String(evt.event_type || "").trim().toLowerCase();
    const cid = Number(payload.conversation_id || target.conversation_id || 0) || 0;
    if (!cid) return;

    if (eventType === "chat_conversation_typing") {
      if (cid === Number(selectedConversationId || 0)) {
        const actorType = String(payload.actor_type || "").trim().toLowerCase();
        if (actorType === "cliente") {
          setRemoteClientTyping(
            Boolean(payload.is_typing),
            "Cliente está escribiendo...",
            Number(payload.typing_expires_in || 0),
          );
        }
      }
      return;
    }

    patchConversationRowFromPayload(cid, payload);
    scheduleConversationsRefresh(280);

    if (cid > 0 && cid === Number(selectedConversationId || 0)) {
      const senderType = String(payload.sender_type || "").trim().toLowerCase();
      const isChatMessageCreated = eventType === "chat_message_created";
      let insertedLiveMessage = false;
      if (isChatMessageCreated && (senderType === "cliente" || (senderType === "staff" && !sendingMessage))) {
        if (senderType === "cliente") {
          muteRemoteTyping(300);
          setRemoteClientTyping(false, "", 0, { force: true });
        }
        const liveMessage = (payload.message && typeof payload.message === "object") ? payload.message : null;
        if (liveMessage) {
          const liveMessageId = Number(liveMessage.id || 0) || 0;
          if (liveMessageId > 0) {
            const stickBottom = isNearBottom();
            const normalizedLiveMessage = Object.assign({}, liveMessage);
            if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "is_mine")) {
              normalizedLiveMessage.is_mine = false;
            }
            if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "sender_name")) {
              normalizedLiveMessage.sender_name = senderType === "cliente" ? "Cliente" : "Soporte";
            }
            if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "conversation_id")) {
              normalizedLiveMessage.conversation_id = cid;
            }
            insertedLiveMessage = appendMessages([normalizedLiveMessage]) > 0;
            if (insertedLiveMessage && stickBottom && messagesNode) {
              messagesNode.scrollTop = messagesNode.scrollHeight;
            }
          }
        }
      }
      if (isChatMessageCreated && !insertedLiveMessage && (senderType === "cliente" || (senderType === "staff" && !sendingMessage))) {
        if (senderType === "cliente") {
          muteRemoteTyping(300);
          setRemoteClientTyping(false, "", 0, { force: true });
        }
        scheduleMessageSync(cid, 70);
      }
      if (eventType === "chat_conversation_status_changed" && String(payload.status || "").trim()) {
        const st = String(payload.status || "").trim().toLowerCase();
        if (threadStatus) {
          threadStatus.className = "badge " + statusClass(st);
          threadStatus.textContent = statusText(st);
        }
      }
      scheduleMarkRead(cid, 160, 1);
    }
  }

  function pollDelayMs() {
    const base = document.hidden ? POLL_HIDDEN_INTERVAL_MS : POLL_INTERVAL_MS;
    const cooldownLeft = Math.max(0, Number(pollCooldownUntil || 0) - Date.now());
    const errorPenalty = pollErrorStreak > 0 ? Math.min(12000, pollErrorStreak * 900) : 0;
    return Math.max(base, cooldownLeft, errorPenalty, 120);
  }

  function schedulePollTick(delayMs) {
    if (!pollLoopActive) return;
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(function () {
      runPollLoop().catch(function () {});
    }, Math.max(60, Number(delayMs || 0) || 0));
  }

  async function pollOnce() {
    if (!pollUrl) return { ok: false };
    const u = new URL(pollUrl, window.location.origin);
    u.searchParams.set("after_id", String(Math.max(0, afterId)));
    u.searchParams.set("limit", "60");
    u.searchParams.set("view", "chat_inbox");
    const resp = await fetch(u.toString(), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    const authFailed = resp.redirected
      || resp.status === 401
      || resp.status === 403
      || /\/admin\/login(?:[/?#]|$)/i.test(String(resp.url || ""));
    if (authFailed) {
      stopPolling();
      closeSSE();
      clearReconnectTimer();
      return { ok: false, stop: true };
    }
    if (resp.status === 429) {
      const retryAfterRaw = String(resp.headers.get("Retry-After") || "").trim();
      const retryAfterSec = Math.max(1, Number.parseInt(retryAfterRaw, 10) || 1);
      pollCooldownUntil = Date.now() + (retryAfterSec * 1000);
      return { ok: false, rateLimited: true, retryAfterMs: retryAfterSec * 1000 };
    }
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const payload = await resp.json();
    if (String((payload && payload.mode) || "").trim().toLowerCase() === "poll_only") {
      markPollOnlyMode();
    }
    const items = Array.isArray(payload && payload.items) ? payload.items : [];
    items.forEach(handleLiveEvent);
    afterId = Math.max(afterId, Number((payload && payload.next_after_id) || 0));
    return { ok: true };
  }

  async function runPollLoop() {
    if (!pollLoopActive) return;
    if (pollInFlight) {
      schedulePollTick(180);
      return;
    }
    pollInFlight = true;
    let nextDelay = pollDelayMs();
    try {
      const result = await pollOnce();
      if (result && result.stop) return;
      if (result && result.rateLimited) {
        pollErrorStreak = Math.min(8, pollErrorStreak + 1);
      } else {
        pollErrorStreak = 0;
      }
      nextDelay = pollDelayMs();
    } catch (_e) {
      pollErrorStreak = Math.min(8, pollErrorStreak + 1);
      nextDelay = pollDelayMs();
    } finally {
      pollInFlight = false;
    }
    schedulePollTick(nextDelay);
  }

  function startPolling() {
    if (pollLoopActive || !pollUrl) return;
    pollLoopActive = true;
    schedulePollTick(40);
  }

  function stopPolling() {
    pollLoopActive = false;
    pollInFlight = false;
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function closeSSE() {
    if (eventSource) {
      try { eventSource.close(); } catch (_e) {}
      eventSource = null;
    }
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  async function probePollOnlyMode() {
    if (sseDisabledByMode || !streamUrl) return sseDisabledByMode;
    if (streamModeProbe) return streamModeProbe;
    streamModeProbe = (async function () {
      const ctl = new AbortController();
      const timeoutId = window.setTimeout(function () {
        try { ctl.abort(); } catch (_e) {}
      }, 2200);
      try {
        const u = new URL(streamUrl, window.location.origin);
        u.searchParams.set("probe", "1");
        const resp = await fetch(u.toString(), {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          signal: ctl.signal,
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        if (resp.redirected || resp.status === 401 || resp.status === 403) {
          markPollOnlyMode();
          return true;
        }
        if (resp.status !== 503) return false;
        const headerMode = String(resp.headers.get("X-Live-Invalidation-Mode") || "").trim().toLowerCase();
        let bodyMode = "";
        try {
          const payload = await resp.json();
          bodyMode = String((payload && payload.mode) || "").trim().toLowerCase();
        } catch (_e) {}
        if (headerMode === "poll_only" || bodyMode === "poll_only") {
          markPollOnlyMode();
          return true;
        }
        return false;
      } catch (_e) {
        return false;
      } finally {
        window.clearTimeout(timeoutId);
        streamModeProbe = null;
      }
    })();
    return streamModeProbe;
  }

  function scheduleReconnect() {
    if (sseDisabledByMode) return;
    clearReconnectTimer();
    reconnectTimer = window.setTimeout(function () {
      startSSE();
    }, 12000);
  }

  function startSSE() {
    closeSSE();
    if (sseDisabledByMode || !("EventSource" in window) || !streamUrl) {
      startPolling();
      return;
    }
    probePollOnlyMode().then(function (pollOnly) {
      if (pollOnly) {
        closeSSE();
        clearReconnectTimer();
        startPolling();
        return;
      }
      if (sseDisabledByMode || !("EventSource" in window) || !streamUrl) {
        startPolling();
        return;
      }
      const u = new URL(streamUrl, window.location.origin);
      u.searchParams.set("last_stream_id", String(lastStreamId || "$"));
      eventSource = new EventSource(u.toString(), { withCredentials: true });
      eventSource.onopen = function () {
        pollErrorStreak = 0;
        pollCooldownUntil = 0;
        stopPolling();
      };

      eventSource.addEventListener("invalidation", function (ev) {
        stopPolling();
        try {
          const data = JSON.parse(String(ev.data || "{}"));
          handleLiveEvent(data);
        } catch (_e) {}
      });
      eventSource.addEventListener("heartbeat", function (_ev) {
        stopPolling();
        try {
          const hb = JSON.parse(String(_ev && _ev.data || "{}"));
          if (hb && hb.last_stream_id) {
            lastStreamId = String(hb.last_stream_id || lastStreamId);
          }
        } catch (_e) {}
      });
      eventSource.onerror = function () {
        closeSSE();
        startPolling();
        probePollOnlyMode().then(function (isPollOnly) {
          if (isPollOnly) {
            clearReconnectTimer();
            return;
          }
          scheduleReconnect();
        }).catch(function () {
          scheduleReconnect();
        });
      };
    }).catch(function () {
      startPolling();
      scheduleReconnect();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!pollLoopActive) return;
    schedulePollTick(document.hidden ? POLL_HIDDEN_INTERVAL_MS : 120);
  });

  window.addEventListener("beforeunload", function () {
    stopLocalTypingSignal();
    closeSSE();
    stopPolling();
    clearReconnectTimer();
    if (remoteTypingHideTimer) {
      window.clearTimeout(remoteTypingHideTimer);
      remoteTypingHideTimer = null;
    }
  });

  refreshConversations({ silent: true });
  if (selectedConversationId > 0) refreshMessages(selectedConversationId, { silent: true, mode: "reset" });
  startSSE();
})();
