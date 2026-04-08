(function () {
  "use strict";

  const root = document.getElementById("adminChatRoot");
  if (!root) return;

  const listNode = document.getElementById("adminChatConversationList");
  const messagesNode = document.getElementById("adminChatMessages");
  const form = document.getElementById("adminChatComposeForm");
  const bodyInput = document.getElementById("adminChatBody");
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

  const conversationsUrl = String(root.getAttribute("data-conversations-url") || "").trim();
  const messagesTpl = String(root.getAttribute("data-messages-url-template") || "").trim();
  const sendTpl = String(root.getAttribute("data-send-url-template") || "").trim();
  const readTpl = String(root.getAttribute("data-read-url-template") || "").trim();
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

  const PAGE_SIZE = 50;

  let selectedConversationId = Number(root.getAttribute("data-selected-conversation-id") || 0) || 0;
  let loadingLatest = false;
  let loadingOlder = false;
  let eventSource = null;
  let pollTimer = null;
  let reconnectTimer = null;
  let nextBeforeId = 0;
  let hasMoreHistory = false;
  const initialAfterId = Math.max(0, Number(root.getAttribute("data-initial-after-id") || 0) || 0);
  let afterId = initialAfterId;
  let lastStreamId = "$";

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
    return String(status || "open").toUpperCase();
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
    if (st === "pending") return "text-bg-light border text-muted";
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
      return '<span class="badge text-bg-light border text-muted">Sin asignar</span>';
    }
    const username = String((row && row.assigned_staff_username) || ("Staff #" + String(assignedId)));
    return '<span class="badge bg-primary">Asignada: ' + esc(username) + '</span>';
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
      const solicitud = row.solicitud_codigo ? ('<div class="small text-muted">Solicitud #' + esc(row.solicitud_codigo) + '</div>') : '';
      const st = String(row.status || "open").toLowerCase();
      const sLevel = slaLevel(row);
      return [
        '<a class="list-group-item list-group-item-action admin-chat-conv admin-chat-sla-' + esc(sLevel) + (active ? ' active' : '') + '" href="' + esc(threadUrlForConversation(row.id)) + '" data-conversation-id="' + esc(row.id) + '">',
        '<div class="d-flex justify-content-between align-items-start gap-2">',
        '<div><div class="fw-semibold text-truncate">' + esc(row.cliente_nombre || ("Cliente #" + String(row.cliente_id || ""))) + '</div>',
        '<div class="small text-muted">' + esc(row.cliente_codigo || '') + '</div></div>',
        '<span class="badge ' + statusClass(st) + '">' + statusText(st) + '</span>',
        '<span class="badge ' + slaClass(row) + '">' + esc(slaLabel(row)) + '</span>',
        unread > 0 ? '<span class="badge rounded-pill text-bg-danger admin-chat-unread-badge">' + String(unread) + '</span>' : '',
        '</div>',
        '<div class="small mt-1">' + esc(row.subject || 'Soporte general') + '</div>',
        solicitud,
        '<div class="small mt-1">' + assignmentBadgeHtml(row) + '</div>',
        '<div class="small mt-1 text-muted">' + esc(slaSummary(row)) + '</div>',
        '<div class="small text-muted text-truncate">' + esc(row.last_message_preview || 'Sin mensajes') + '</div>',
        '</a>'
      ].join('');
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
      messagesNode.insertAdjacentHTML("beforeend", messageHtml(m));
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
        threadAssignmentMeta.innerHTML = '<span class="badge bg-primary">Asignada: ' + safeName + "</span>";
      } else {
        threadAssignmentMeta.innerHTML = '<span class="badge text-bg-light border text-muted">Sin asignar</span>';
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
    rows.forEach(function (m) {
      messagesNode.insertAdjacentHTML("beforeend", messageHtml(m));
    });
    if (!rows.length) showEmptyState();
    setPaginationFromPayload(payload || {});
    updateThreadMetaFromConversation(payload && payload.conversation);
    messagesNode.scrollTop = messagesNode.scrollHeight;
  }

  function syncLatestMessages(payload) {
    if (!messagesNode) return;
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
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
    if (!conversationId) return;
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
    } catch (_e) {}
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

      await markRead(cid);
      await refreshConversations({ silent: true });
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
      selectedConversationId = cid;
      nextBeforeId = 0;
      hasMoreHistory = false;
      if (window.history && typeof window.history.replaceState === "function") {
        const url = new URL(window.location.href);
        url.searchParams.set("conversation_id", String(cid));
        window.history.replaceState({}, "", url.toString());
      }
      refreshMessages(cid, { silent: false, mode: "reset" });
      refreshConversations({ silent: true });
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
      markRead(cid).then(function () { return refreshConversations({ silent: true }); }).catch(function () {});
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

  if (form && bodyInput && sendBtn) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const cid = Number(selectedConversationId || (messagesNode && messagesNode.getAttribute("data-conversation-id")) || 0) || 0;
      if (!cid) return;
      const text = String(bodyInput.value || "").trim();
      if (!text) return;
      const csrfToken = getCSRFToken();
      sendBtn.disabled = true;
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
        const warning = sendPayload && sendPayload.assignment_warning;
        if (warning && ownershipWarning) {
          ownershipWarning.classList.remove("d-none");
          if (ownershipWarningName) {
            ownershipWarningName.textContent = String(warning.assigned_staff_username || ("Staff #" + String(warning.assigned_staff_user_id || "")));
          }
        }
        await refreshMessages(cid, { silent: true, mode: "sync" });
      } catch (_e) {
        // no-op
      } finally {
        sendBtn.disabled = false;
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
    const cid = Number(target.conversation_id || 0) || 0;
    refreshConversations({ silent: true });
    if (cid > 0 && cid === Number(selectedConversationId || 0)) {
      refreshMessages(cid, { silent: true, mode: "sync" });
    }
  }

  async function pollOnce() {
    if (!pollUrl) return;
    const u = new URL(pollUrl, window.location.origin);
    u.searchParams.set("after_id", String(Math.max(0, afterId)));
    u.searchParams.set("limit", "60");
    u.searchParams.set("view", "chat_inbox");
    const payload = await fetchJson(u.toString());
    const items = Array.isArray(payload && payload.items) ? payload.items : [];
    items.forEach(handleLiveEvent);
    afterId = Math.max(afterId, Number((payload && payload.next_after_id) || 0));
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(function () {
      pollOnce().catch(function () {});
    }, 7000);
    pollOnce().catch(function () {});
  }

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function closeSSE() {
    if (eventSource) {
      try { eventSource.close(); } catch (_e) {}
      eventSource = null;
    }
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
    eventSource.addEventListener("heartbeat", function (_ev) {
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
      scheduleReconnect();
    };

    startPolling();
  }

  window.addEventListener("beforeunload", function () {
    closeSSE();
    stopPolling();
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
  });

  refreshConversations({ silent: true });
  if (selectedConversationId > 0) refreshMessages(selectedConversationId, { silent: true, mode: "reset" });
  startSSE();
})();
