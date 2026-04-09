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
  const threadStatusHintNode = document.getElementById("clientChatThreadStatusHint");
  const typingSlotNode = document.getElementById("clientChatTypingSlot");
  const typingIndicatorNode = document.getElementById("clientChatTypingIndicator");
  const typingIndicatorLabelNode = document.getElementById("clientChatTypingLabel");
  const supportPresencePillNode = document.getElementById("clientChatSupportPresencePill");
  const loadOlderBtn = document.getElementById("clientChatLoadOlderBtn");
  const historyStateNode = document.getElementById("clientChatHistoryState");
  const charCountNode = document.getElementById("clientChatCharCount");
  const sendBtnDefaultHtml = sendBtn ? sendBtn.innerHTML : "";

  const conversationsUrl = String(root.getAttribute("data-conversations-url") || "").trim();
  const messagesTpl = String(root.getAttribute("data-messages-url-template") || "").trim();
  const sendTpl = String(root.getAttribute("data-send-url-template") || "").trim();
  const readTpl = String(root.getAttribute("data-read-url-template") || "").trim();
  const typingTpl = String(root.getAttribute("data-typing-url-template") || "").trim();
  const presencePingUrl = String(root.getAttribute("data-presence-ping-url") || "").trim();

  const PAGE_SIZE = 50;

  let selectedConversationId = Number(root.getAttribute("data-selected-conversation-id") || 0) || 0;
  let loadingLatest = false;
  let loadingOlder = false;
  let sendingMessage = false;
  let nextBeforeId = 0;
  let hasMoreHistory = false;
  let conversationsRefreshTimer = null;
  let conversationsRefreshInflight = false;
  let conversationsRefreshQueued = false;
  let messageSyncTimer = null;
  let markReadTimer = null;
  let presencePingTimer = null;
  let lastPresencePingAt = 0;
  let selectedConversationPayload = null;
  let localTypingActive = false;
  let lastTypingEmitAt = 0;
  let typingPulseTimer = null;
  let remoteTypingHideTimer = null;
  let remoteTypingMutedUntil = 0;

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

  function conversationIdFromFormAction(actionUrl) {
    const raw = String(actionUrl || "").trim();
    if (!raw) return 0;
    const m = raw.match(/\/chat\/conversations\/(\d+)\/messages(?:\/)?(?:\?|#|$)/);
    return m ? (Number(m[1] || 0) || 0) : 0;
  }

  function statusClass(status) {
    const s = String(status || "open").toLowerCase();
    if (s === "closed") return "text-bg-secondary";
    if (s === "pending") return "text-bg-warning";
    return "text-bg-success";
  }

  function statusText(status) {
    const s = String(status || "open").toLowerCase();
    if (s === "closed") return "CERRADO";
    if (s === "pending") return "EN ESPERA";
    return "ABIERTO";
  }

  function statusHintText(status) {
    const s = String(status || "open").toLowerCase();
    if (s === "closed") return "Esta conversación está cerrada. Puedes abrir un chat nuevo si necesitas ayuda.";
    if (s === "pending") return "Tu caso está en seguimiento. Te avisaremos cuando haya actualización.";
    return "Conversación abierta. Te responderemos por este mismo chat.";
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

  function clearGlobalLoaderState() {
    try {
      if (window.AppLoader && typeof window.AppLoader.hideAll === "function") {
        window.AppLoader.hideAll();
      }
    } catch (_e) {}
    document.documentElement.classList.remove("is-loading");
    if (document.body) document.body.classList.remove("is-loading");
  }

  function updateSupportPresence(conversation) {
    if (!supportPresencePillNode || !conversation || typeof conversation !== "object") return;
    const inThisChat = Boolean(conversation.staff_in_this_chat);
    const label = String(conversation.staff_presence_label || "").trim();
    if (inThisChat) {
      supportPresencePillNode.classList.remove("d-none");
      supportPresencePillNode.innerHTML = '<span class="client-chat-support-dot"></span>' + esc(label || "Soporte en este chat");
      return;
    }
    supportPresencePillNode.classList.add("d-none");
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

  function setRemoteStaffTyping(typingOn, label, expiresInSeconds, opts) {
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
      typingIndicatorLabelNode.textContent = String(label || "Soporte está escribiendo...");
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

  async function pingChatPresence(eventType) {
    if (!presencePingUrl) return;
    const cid = Number(selectedConversationId || 0) || 0;
    if (!cid) return;
    const csrfToken = getCSRFToken();
    const payload = {
      current_path: String(window.location.pathname || "") + String(window.location.search || ""),
      event_type: String(eventType || "heartbeat").trim().toLowerCase() || "heartbeat",
      action_hint: "chat_viewing",
      conversation_id: cid,
      solicitud_id: Number((selectedConversationPayload && selectedConversationPayload.solicitud_id) || 0) || null,
    };
    try {
      await fetch(presencePingUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      lastPresencePingAt = Date.now();
    } catch (_e) {
      // no-op
    }
  }

  function startChatPresenceLoop() {
    if (!presencePingUrl) return;
    if (presencePingTimer) window.clearInterval(presencePingTimer);
    pingChatPresence("chat_open").catch(function () {});
    presencePingTimer = window.setInterval(function () {
      if (document.hidden) return;
      if ((Date.now() - lastPresencePingAt) < 1500) return;
      pingChatPresence("heartbeat").catch(function () {});
    }, 5000);
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
      const statusBadge = row.querySelector(".badge");
      badge = document.createElement("span");
      badge.className = "badge rounded-pill text-bg-danger " + className;
      if (statusBadge && statusBadge.parentNode) {
        statusBadge.insertAdjacentElement("afterend", badge);
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
      const statusBadge = row.querySelector(".badge");
      if (statusBadge) {
        statusBadge.className = "badge " + statusClass(statusRaw);
        statusBadge.textContent = statusText(statusRaw);
      }
      if (Number(conversationId || 0) === Number(selectedConversationId || 0)) {
        updateThreadHeader({
          subject: threadSubjectNode ? threadSubjectNode.textContent : "Soporte",
          status: statusRaw,
        });
      }
    }
    if (Object.prototype.hasOwnProperty.call(payload, "cliente_unread_count")) {
      setConversationUnreadBadge(row, payload.cliente_unread_count, "client-chat-unread-badge");
    }
    const preview = String(payload.preview || "").trim();
    if (preview) {
      const previewNode = row.querySelector(".small.text-muted.text-truncate");
      if (previewNode) previewNode.textContent = preview;
    }
    const senderType = String(payload.sender_type || "").trim().toLowerCase();
    if (senderType) {
      const signalNode = row.querySelector(".small:not(.text-muted.text-truncate)");
      if (signalNode) {
        if (senderType === "staff") {
          signalNode.innerHTML = '<span class="text-success">Respondido por soporte</span>';
        } else if (senderType === "cliente") {
          signalNode.innerHTML = '<span class="text-muted">Esperando respuesta</span>';
        }
      }
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
          scheduleConversationsRefresh(120);
        }
      });
    }, wait);
  }

  function scheduleMessageSync(conversationId, delayMs, retriesLeft) {
    const cid = Number(conversationId || 0) || 0;
    if (!cid) return;
    const retries = Math.max(0, Number(retriesLeft || 0) || 0);
    if (messageSyncTimer) window.clearTimeout(messageSyncTimer);
    messageSyncTimer = window.setTimeout(function () {
      messageSyncTimer = null;
      if (loadingLatest) {
        if (retries > 0) {
          scheduleMessageSync(cid, 140, retries - 1);
        }
        return;
      }
      refreshMessages(cid, { silent: true, mode: "sync", postSync: false }).catch(function () {
        if (retries > 0) {
          scheduleMessageSync(cid, 160, retries - 1);
        }
      });
    }, Math.max(60, Number(delayMs || 0) || 0));
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
    }, Math.max(80, Number(delayMs || 0) || 0));
  }

  function setSendButtonState(button, isSending) {
    if (!button) return;
    button.disabled = Boolean(isSending);
    if (isSending) {
      button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
      button.setAttribute("aria-label", "Enviando...");
      return;
    }
    button.innerHTML = sendBtnDefaultHtml;
    button.setAttribute("aria-label", "Enviar");
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
      const lastSender = String(row.last_message_sender_type || "").toLowerCase();
      const supportSignal = lastSender === "staff"
        ? '<span class="text-success">Respondido por soporte</span>'
        : (lastSender === "cliente" ? '<span class="text-muted">Esperando respuesta</span>' : '<span class="text-muted">Sin actividad reciente</span>');
      return [
        '<a class="list-group-item list-group-item-action client-chat-conv' + (active ? ' active' : '') + '" href="' + esc(row.thread_url || '#') + '" data-conversation-id="' + esc(row.id) + '">',
        '<div class="d-flex justify-content-between gap-2 align-items-start">',
        '<div class="fw-semibold text-truncate">' + esc(row.subject || 'Soporte') + '</div>',
        '<span class="badge ' + statusClass(st) + '">' + statusText(st) + '</span>',
        unread > 0 ? '<span class="badge rounded-pill text-bg-danger client-chat-unread-badge">' + String(unread) + '</span>' : '',
        '</div>',
        solicitudLabel,
        '<div class="small text-muted text-truncate">' + esc(row.last_message_preview || 'Sin mensajes') + '</div>',
        '<div class="small">' + supportSignal + '</div>',
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
    if (threadStatusHintNode) {
      threadStatusHintNode.textContent = statusHintText(conversation.status || "open");
    }
    if (
      Object.prototype.hasOwnProperty.call(conversation, "staff_presence_state")
      || Object.prototype.hasOwnProperty.call(conversation, "staff_presence_label")
      || Object.prototype.hasOwnProperty.call(conversation, "staff_in_this_chat")
    ) {
      updateSupportPresence(conversation);
    }
    if (
      Object.prototype.hasOwnProperty.call(conversation, "staff_typing_in_this_chat")
      || Object.prototype.hasOwnProperty.call(conversation, "staff_typing_label")
      || Object.prototype.hasOwnProperty.call(conversation, "staff_typing_expires_in")
    ) {
      setRemoteStaffTyping(
        Boolean(conversation.staff_typing_in_this_chat),
        String(conversation.staff_typing_label || ""),
        Number(conversation.staff_typing_expires_in || 0),
      );
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
    const shouldHideTyping = rows.some(function (m) {
      return String((m && m.sender_type) || "").trim().toLowerCase() === "staff";
    });
    if (shouldHideTyping) {
      muteRemoteTyping(300);
      setRemoteStaffTyping(false, "", 0, { force: true });
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
    setPaginationFromPayload(payload);
    selectedConversationPayload = (payload && payload.conversation && typeof payload.conversation === "object")
      ? payload.conversation
      : selectedConversationPayload;
    updateThreadHeader(payload && payload.conversation);
    messagesNode.scrollTop = messagesNode.scrollHeight;
  }

  function syncLatestMessages(payload) {
    if (!messagesNode) return;
    const rows = Array.isArray(payload && payload.items) ? payload.items : [];
    const shouldHideTyping = rows.some(function (m) {
      return String((m && m.sender_type) || "").trim().toLowerCase() === "staff";
    });
    if (shouldHideTyping) {
      muteRemoteTyping(300);
      setRemoteStaffTyping(false, "", 0, { force: true });
    }
    const shouldStickBottom = isNearBottom();
    appendMessages(rows);
    if (getMessageCount() <= 0) showEmptyState();
    selectedConversationPayload = (payload && payload.conversation && typeof payload.conversation === "object")
      ? payload.conversation
      : selectedConversationPayload;
    updateThreadHeader(payload && payload.conversation);
    if (shouldStickBottom) {
      messagesNode.scrollTop = messagesNode.scrollHeight;
    }
    updateHistoryControls();
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

  async function refreshConversations(opts) {
    if (!conversationsUrl) return null;
    try {
      const payload = await fetchJson(conversationsUrl);
      renderConversations(payload.items || []);
      const unreadCount = Math.max(0, Number((payload && payload.unread_count) || 0));
      window.dispatchEvent(new CustomEvent("client-chat:unread-updated", { detail: { unread_count: unreadCount } }));
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
    const postSync = !opts || opts.postSync !== false;
    if (!cid || loadingLatest) return null;
    loadingLatest = true;
    try {
      const payload = await fetchJson(endpointFor(messagesTpl, cid) + "?limit=" + String(PAGE_SIZE));
      selectedConversationId = cid;
      selectedConversationPayload = (payload && payload.conversation && typeof payload.conversation === "object")
        ? payload.conversation
        : selectedConversationPayload;
      root.setAttribute("data-selected-conversation-id", String(cid));
      if (messagesNode) messagesNode.setAttribute("data-conversation-id", String(cid));

      const mustReset = mode === "reset" || previousConversationId !== Number(cid);
      if (mustReset) {
        renderMessagesReset(payload || {});
      } else {
        syncLatestMessages(payload || {});
      }

      if (postSync) {
        scheduleMarkRead(cid, 150, 1);
        scheduleConversationsRefresh(220);
      }
      pingChatPresence(previousConversationId !== Number(cid) ? "chat_switch" : "heartbeat").catch(function () {});
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
      stopLocalTypingSignal();
      setRemoteStaffTyping(false, "", 0);
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
      markRead(cid).then(function () { scheduleConversationsRefresh(120); }).catch(function () {});
    });
  }

  if (bodyInput && form) {
    const markDirty = function () { form.setAttribute("data-client-live-dirty", "1"); };
    const resetDirty = function () { form.setAttribute("data-client-live-dirty", "0"); };
    bodyInput.addEventListener("input", markDirty);
    bodyInput.addEventListener("input", function () {
      scheduleLocalTypingSignal();
    });
    bodyInput.addEventListener("blur", function () {
      stopLocalTypingSignal();
    });
    bodyInput.addEventListener("input", function () {
      if (!charCountNode) return;
      const len = Number((bodyInput.value || "").length);
      const maxLen = Number(bodyInput.getAttribute("maxlength") || root.getAttribute("data-message-max-len") || 1800) || 1800;
      charCountNode.textContent = String(len) + " / " + String(maxLen);
    });
    bodyInput.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" || ev.shiftKey) return;
      ev.preventDefault();
      if (sendBtn && !sendBtn.disabled) sendBtn.click();
    });
    form.addEventListener("submit", resetDirty);
  }

  document.addEventListener("submit", async function (ev) {
    const submitForm = ev.target;
    if (!(submitForm instanceof HTMLFormElement)) return;
    if (submitForm.id !== "clientChatComposeForm") return;
    const activeRoot = document.getElementById("clientChatRoot");
    if (!activeRoot || !activeRoot.contains(submitForm)) return;

    ev.preventDefault();

    const activeMessagesNode = document.getElementById("clientChatMessages");
    const activeBodyInput = submitForm.querySelector("#clientChatBody, textarea[name='body']");
    const activeSendBtn = submitForm.querySelector("#clientChatSendBtn, button[type='submit']");
    const cid = Number(
      selectedConversationId
      || (activeMessagesNode && activeMessagesNode.getAttribute("data-conversation-id"))
      || conversationIdFromFormAction(submitForm.getAttribute("action"))
      || 0
    ) || 0;
    if (!cid || !activeBodyInput) return;
    if (sendingMessage) return;
    const text = String(activeBodyInput.value || "").trim();
    if (!text) return;
    stopLocalTypingSignal();

    const csrfToken = getCSRFToken();
    sendingMessage = true;
    setSendButtonState(activeSendBtn, true);
    clearGlobalLoaderState();

    try {
      const body = new URLSearchParams();
      body.set("csrf_token", csrfToken);
      body.set("body", text);
      const sendUrl = endpointFor(sendTpl, cid) || submitForm.getAttribute("action") || "";
      const payload = await fetchJson(sendUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body: body.toString(),
      });
      activeBodyInput.value = "";
      postTypingState(cid, false).catch(function () {});
      if (charCountNode) {
        const maxLen = Number(activeBodyInput.getAttribute("maxlength") || root.getAttribute("data-message-max-len") || 1800) || 1800;
        charCountNode.textContent = "0 / " + String(maxLen);
      }
      submitForm.setAttribute("data-client-live-dirty", "0");
      if (activeMessagesNode && payload && payload.message) {
        const activeEmpty = activeMessagesNode.querySelector("#clientChatEmpty");
        if (activeEmpty && activeEmpty.parentNode) activeEmpty.parentNode.removeChild(activeEmpty);
        activeMessagesNode.insertAdjacentHTML("beforeend", messageHtml(payload.message));
        activeMessagesNode.scrollTop = activeMessagesNode.scrollHeight;
      }
      setSendButtonState(activeSendBtn, false);
      sendingMessage = false;
      clearGlobalLoaderState();
      scheduleMarkRead(cid, 120, 1);
      scheduleConversationsRefresh(180);
    } catch (_e) {
      // no-op, degradacion silenciosa
    } finally {
      setSendButtonState(activeSendBtn, false);
      sendingMessage = false;
      clearGlobalLoaderState();
    }
  }, true);

  window.ClientChat = {
    refreshConversations,
    refreshMessages: function (conversationId, opts) {
      const cid = Number(conversationId || selectedConversationId || 0) || 0;
      if (!cid) return Promise.resolve(null);
      return refreshMessages(cid, Object.assign({ mode: "sync" }, opts || {}));
    },
    loadOlderMessages,
    scheduleConversationsRefresh,
    applyLiveEvent: function (evt) {
      const rawEvt = evt && typeof evt === "object" ? evt : {};
      const payload = rawEvt.payload && typeof rawEvt.payload === "object" ? rawEvt.payload : {};
      const target = rawEvt.target && typeof rawEvt.target === "object" ? rawEvt.target : {};
      const eventType = String(rawEvt.event_type || "").trim().toLowerCase();
      const cid = Number(payload.conversation_id || target.conversation_id || 0) || 0;
      const activeCid = Number(
        selectedConversationId
        || (messagesNode && messagesNode.getAttribute("data-conversation-id"))
        || 0
      ) || 0;
      if (!cid) {
        scheduleConversationsRefresh(220);
        return;
      }

      patchConversationRowFromPayload(cid, payload);
      const selectedCid = activeCid;
      if (cid !== selectedCid) {
        if (eventType === "cliente.chat.message_created" && selectedCid > 0) {
          scheduleMessageSync(selectedCid, 120, 3);
        }
        scheduleConversationsRefresh(260);
        return;
      }

      if (eventType === "cliente.chat.typing") {
        const actorType = String(payload.actor_type || "").trim().toLowerCase();
        if (actorType === "staff") {
          setRemoteStaffTyping(
            Boolean(payload.is_typing),
            "Soporte está escribiendo...",
            Number(payload.typing_expires_in || 0),
          );
        }
        return;
      }

      if (eventType === "cliente.chat.message_created") {
        const senderType = String(payload.sender_type || "").trim().toLowerCase();
        if (senderType !== "cliente") {
          muteRemoteTyping(300);
          setRemoteStaffTyping(false, "", 0, { force: true });
          const liveMessage = (payload.message && typeof payload.message === "object") ? payload.message : null;
          let insertedLiveMessage = false;
          if (liveMessage) {
            const liveMessageId = Number(liveMessage.id || 0) || 0;
            if (liveMessageId > 0) {
              const shouldStickBottom = isNearBottom();
              const normalizedLiveMessage = Object.assign({}, liveMessage);
              if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "is_mine")) {
                normalizedLiveMessage.is_mine = false;
              }
              if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "sender_name")) {
                normalizedLiveMessage.sender_name = "Soporte";
              }
              if (!Object.prototype.hasOwnProperty.call(normalizedLiveMessage, "conversation_id")) {
                normalizedLiveMessage.conversation_id = cid;
              }
              insertedLiveMessage = appendMessages([normalizedLiveMessage]) > 0;
              if (insertedLiveMessage && shouldStickBottom && messagesNode) {
                messagesNode.scrollTop = messagesNode.scrollHeight;
              }
            }
          }
          if (!insertedLiveMessage) {
            scheduleMessageSync(cid, 90, 3);
          }
        }
        scheduleMarkRead(cid, 140, 1);
        scheduleConversationsRefresh(220);
        return;
      }

      if (eventType === "cliente.chat.status_changed" || eventType === "cliente.chat.read") {
        if (String(payload.status || "").trim()) {
          updateThreadHeader({
            subject: threadSubjectNode ? threadSubjectNode.textContent : "Soporte",
            status: String(payload.status || "").trim().toLowerCase(),
          });
        }
        scheduleConversationsRefresh(220);
        return;
      }

      scheduleConversationsRefresh(260);
    },
    selectedConversationId: function () { return Number(selectedConversationId || 0); },
  };

  refreshConversations({ silent: true });
  if (messagesNode && getMessageCount() > 0) {
    messagesNode.scrollTop = messagesNode.scrollHeight;
  }
  if (selectedConversationId > 0) {
    refreshMessages(selectedConversationId, { silent: true, mode: "reset" });
  }
  startChatPresenceLoop();
  window.addEventListener("beforeunload", function () {
    stopLocalTypingSignal();
    if (presencePingTimer) {
      window.clearInterval(presencePingTimer);
      presencePingTimer = null;
    }
    if (remoteTypingHideTimer) {
      window.clearTimeout(remoteTypingHideTimer);
      remoteTypingHideTimer = null;
    }
  });
  if (charCountNode && bodyInput) {
    const maxLen = Number(bodyInput.getAttribute("maxlength") || root.getAttribute("data-message-max-len") || 1800) || 1800;
    charCountNode.textContent = "0 / " + String(maxLen);
  }
})();
