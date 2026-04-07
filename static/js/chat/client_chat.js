(function () {
  "use strict";

  const root = document.getElementById("clientChatRoot");
  if (!root) return;

  const listNode = document.getElementById("clientChatConversationList");
  const messagesNode = document.getElementById("clientChatMessages");
  const form = document.getElementById("clientChatComposeForm");
  const bodyInput = document.getElementById("clientChatBody");
  const sendBtn = document.getElementById("clientChatSendBtn");
  const markReadBtn = document.getElementById("clientChatMarkReadBtn");
  const threadSubjectNode = document.getElementById("clientChatThreadSubject");
  const threadStatusNode = document.getElementById("clientChatThreadStatus");
  const loadOlderBtn = document.getElementById("clientChatLoadOlderBtn");
  const historyStateNode = document.getElementById("clientChatHistoryState");

  const conversationsUrl = String(root.getAttribute("data-conversations-url") || "").trim();
  const messagesTpl = String(root.getAttribute("data-messages-url-template") || "").trim();
  const sendTpl = String(root.getAttribute("data-send-url-template") || "").trim();
  const readTpl = String(root.getAttribute("data-read-url-template") || "").trim();

  const PAGE_SIZE = 50;

  let selectedConversationId = Number(root.getAttribute("data-selected-conversation-id") || 0) || 0;
  let loadingLatest = false;
  let loadingOlder = false;
  let nextBeforeId = 0;
  let hasMoreHistory = false;

  function getCSRFToken() {
    const input = document.querySelector('input[name="csrf_token"]');
    if (input && input.value) return String(input.value || "").trim();
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? String(meta.getAttribute("content") || "").trim() : "";
  }

  function endpointFor(template, conversationId) {
    const id = Number(conversationId || 0);
    return String(template || "").replace("/0/", "/" + String(id) + "/");
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

  function renderConversations(items) {
    if (!listNode) return;
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      listNode.innerHTML = '<div class="p-3 text-muted small">No hay conversaciones todavía.</div>';
      return;
    }
    listNode.innerHTML = rows.map(function (row) {
      const active = Number(row.id || 0) === Number(selectedConversationId || 0);
      const unread = Math.max(0, Number(row.cliente_unread_count || 0));
      const st = String(row.status || "open").toLowerCase();
      const solicitudLabel = row.solicitud_codigo ? ('<div class="small text-muted">Solicitud #' + esc(row.solicitud_codigo) + '</div>') : '';
      return [
        '<a class="list-group-item list-group-item-action client-chat-conv' + (active ? ' active' : '') + '" href="' + esc(row.thread_url || '#') + '" data-conversation-id="' + esc(row.id) + '">',
        '<div class="d-flex justify-content-between gap-2 align-items-start">',
        '<div class="fw-semibold text-truncate">' + esc(row.subject || 'Soporte') + '</div>',
        '<span class="badge ' + statusClass(st) + '">' + statusText(st) + '</span>',
        unread > 0 ? '<span class="badge rounded-pill text-bg-danger">' + String(unread) + '</span>' : '',
        '</div>',
        solicitudLabel,
        '<div class="small text-muted text-truncate">' + esc(row.last_message_preview || 'Sin mensajes') + '</div>',
        '</a>'
      ].join('');
    }).join('');
  }

  function messageHtml(m) {
    const mine = Boolean(m && m.is_mine);
    return [
      '<article class="client-chat-msg ' + (mine ? 'mine' : 'theirs') + '" data-message-id="' + esc(m.id) + '">',
      '<div class="bubble">' + esc((m && m.body) || '') + '</div>',
      '<div class="meta">' + esc(fmtDate(m && m.created_at)) + ' · ' + esc((m && m.sender_name) || (mine ? 'Tú' : 'Soporte')) + '</div>',
      '</article>'
    ].join('');
  }

  function historyAnchorNode() {
    if (!messagesNode) return null;
    return messagesNode.querySelector("#clientChatHistoryControls");
  }

  function firstMessageNode() {
    if (!messagesNode) return null;
    return messagesNode.querySelector("article[data-message-id]");
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
    const nodes = messagesNode.querySelectorAll("article[data-message-id], #clientChatEmpty");
    nodes.forEach(function (node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
  }

  function clearEmptyState() {
    if (!messagesNode) return;
    const empty = messagesNode.querySelector("#clientChatEmpty");
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  }

  function showEmptyState() {
    if (!messagesNode) return;
    if (getMessageCount() > 0) return;
    clearEmptyState();
    const div = document.createElement("div");
    div.id = "clientChatEmpty";
    div.className = "text-muted small p-3";
    div.textContent = "Escribe el primer mensaje para iniciar la conversación.";
    const anchor = historyAnchorNode();
    if (anchor && anchor.parentNode === messagesNode) {
      anchor.insertAdjacentElement("afterend", div);
    } else {
      messagesNode.appendChild(div);
    }
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
    const anchor = historyAnchorNode();
    const first = firstMessageNode();
    let added = 0;
    list.forEach(function (m) {
      const id = Number((m && m.id) || 0) || 0;
      if (!id || hasMessageId(id)) return;
      const html = messageHtml(m);
      if (first && first.parentNode === messagesNode) {
        first.insertAdjacentHTML("beforebegin", html);
      } else if (anchor && anchor.parentNode === messagesNode) {
        anchor.insertAdjacentHTML("afterend", html);
      } else {
        messagesNode.insertAdjacentHTML("afterbegin", html);
      }
      added += 1;
    });
    return added;
  }

  function updateThreadHeader(conversation) {
    if (!conversation || typeof conversation !== "object") return;
    if (threadSubjectNode) {
      threadSubjectNode.textContent = String(conversation.subject || "Soporte");
    }
    if (threadStatusNode) {
      const st = String(conversation.status || "open").toLowerCase();
      threadStatusNode.className = "badge " + statusClass(st);
      threadStatusNode.textContent = statusText(st);
    }
  }

  function updateHistoryControls() {
    const count = getMessageCount();
    if (loadOlderBtn) {
      const showBtn = Boolean(hasMoreHistory) && count > 0;
      loadOlderBtn.classList.toggle("d-none", !showBtn);
      loadOlderBtn.disabled = loadingOlder;
      if (loadingOlder) {
        loadOlderBtn.textContent = "Cargando...";
      } else {
        loadOlderBtn.textContent = "Cargar mensajes anteriores";
      }
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
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
    clearThreadMessagesOnly();

    rows.forEach(function (m) {
      messagesNode.insertAdjacentHTML("beforeend", messageHtml(m));
    });
    if (!rows.length) showEmptyState();
    setPaginationFromPayload(payload);
    updateThreadHeader(payload && payload.conversation);
    messagesNode.scrollTop = messagesNode.scrollHeight;
  }

  function syncLatestMessages(payload) {
    if (!messagesNode) return;
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
    const shouldStickBottom = isNearBottom();
    appendMessages(rows);
    if (getMessageCount() <= 0) showEmptyState();
    updateThreadHeader(payload && payload.conversation);
    if (shouldStickBottom) {
      messagesNode.scrollTop = messagesNode.scrollHeight;
    }
    updateHistoryControls();
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

  async function refreshConversations(opts) {
    if (!conversationsUrl) return null;
    try {
      const payload = await fetchJson(conversationsUrl);
      renderConversations(payload.items || []);
      return payload;
    } catch (_e) {
      if (!(opts && opts.silent) && listNode) {
        listNode.innerHTML = '<div class="p-3 text-danger small">No se pudo cargar conversaciones.</div>';
      }
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
        messagesNode.innerHTML = '<div class="text-danger small p-3">No se pudo cargar mensajes.</div>';
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

  if (bodyInput && form) {
    const markDirty = function () { form.setAttribute("data-client-live-dirty", "1"); };
    const resetDirty = function () { form.setAttribute("data-client-live-dirty", "0"); };
    bodyInput.addEventListener("input", markDirty);
    form.addEventListener("submit", resetDirty);
  }

  if (form && sendBtn) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const cid = Number(selectedConversationId || (messagesNode && messagesNode.getAttribute("data-conversation-id")) || 0) || 0;
      if (!cid || !bodyInput) return;
      const text = String(bodyInput.value || "").trim();
      if (!text) return;
      const csrfToken = getCSRFToken();
      sendBtn.disabled = true;
      sendBtn.classList.add("is-loading");
      try {
        const body = new URLSearchParams();
        body.set("csrf_token", csrfToken);
        body.set("body", text);
        await fetchJson(endpointFor(sendTpl, cid), {
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
        form.setAttribute("data-client-live-dirty", "0");
        await refreshMessages(cid, { silent: true, mode: "sync" });
      } catch (_e) {
        // no-op, degradacion silenciosa
      } finally {
        sendBtn.disabled = false;
        sendBtn.classList.remove("is-loading");
      }
    });
  }

  window.ClientChat = {
    refreshConversations,
    refreshMessages: function (conversationId, opts) {
      const cid = Number(conversationId || selectedConversationId || 0) || 0;
      if (!cid) return Promise.resolve(null);
      return refreshMessages(cid, Object.assign({ mode: "sync" }, opts || {}));
    },
    loadOlderMessages,
    selectedConversationId: function () { return Number(selectedConversationId || 0); },
  };

  refreshConversations({ silent: true });
  if (selectedConversationId > 0) {
    refreshMessages(selectedConversationId, { silent: true, mode: "reset" });
  }
})();
