(function () {
  var root = document.querySelector('[data-practice-root="1"]');
  if (!root) return;
  var conversationId = root.getAttribute('data-conversation-id');
  if (!conversationId) return;

  var isDemoMode = root.getAttribute('data-practice-demo-mode') === '1';
  var log = document.getElementById('practiceMessages');
  var input = document.getElementById('practice-body');
  var sendBtn = document.getElementById('practice-send');
  var composer = document.querySelector('.practice-chat-composer');
  var headerStats = document.getElementById('practice-header-stats');
  var stepLine = document.getElementById('practice-step-line');
  var progressLine = document.getElementById('practice-progress-line');
  var humanLine = document.getElementById('practice-human-line');
  var summaryLine = document.getElementById('practice-summary-line');
  var draftLine = document.getElementById('practice-draft-line');
  var debugState = document.getElementById('practice-debug-state');
  var entities = document.getElementById('practice-entities');
  var future = document.getElementById('practice-future');
  var corrections = document.getElementById('practice-corrections');
  var suggested = document.getElementById('practice-suggested');
  var metadata = document.getElementById('practice-metadata');
  var metadataCard = document.getElementById('practice-metadata-card');
  var toggleMeta = document.getElementById('practice-toggle-metadata');
  var entityName = document.getElementById('practice-entity-name');
  var entityAge = document.getElementById('practice-entity-age');
  var entityCity = document.getElementById('practice-entity-city');
  var entityWorkType = document.getElementById('practice-entity-work-type');
  var csrfToken = ((document.querySelector('meta[name="csrf-token"]') || {}).content || '').trim();
  var isSending = false;
  var typingIndicatorId = 'practice-local-typing-indicator';
  var activeSendToken = 0;

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function pretty(obj) {
    try { return JSON.stringify(obj || {}, null, 2); } catch (e) { return '{}'; }
  }

  function bubble(kind, title, text, meta) {
    var row = document.createElement('div');
    row.className = 'practice-row ' + kind;
    var body = document.createElement('div');
    body.className = 'practice-bubble ' + kind;
    var metaTxt = isDemoMode ? '' : (meta ? ' · ' + esc(meta) : '');
    body.innerHTML = '<div class="practice-meta"><strong>' + esc(title) + '</strong>' + metaTxt + '</div><div>' + esc(text) + '</div>';
    row.appendChild(body);
    return row;
  }

  function botSuggestedBubble(msg) {
    var row = document.createElement('div');
    row.className = 'practice-row bot-suggested';
    var body = document.createElement('div');
    body.className = 'practice-bubble bot-suggested';
    var meta = esc((msg && msg.created_at) || '');
    var source = esc((msg && msg.source) || 'protocol');
    var isAi = !!(msg && msg.ai_reply_used);
    var badgeLabel = isAi ? 'IA sugerida' : 'Bot sugerido';
    body.innerHTML =
      '<div class="practice-meta"><span class="practice-suggested-badge">' + badgeLabel + '</span>' +
      ((isDemoMode || !meta) ? '' : (' · ' + meta)) + '</div>' +
      '<div>' + esc((msg && msg.text) || '') + '</div>' +
      '<div class="practice-suggested-note">No enviado · ' + source + '</div>';
    row.appendChild(body);
    return row;
  }

  function pendingBubble(text) {
    var row = document.createElement('div');
    row.className = 'practice-row inbound';
    var body = document.createElement('div');
    body.className = 'practice-bubble inbound';
    body.innerHTML =
      '<div class="practice-meta"><strong>Candidata</strong>' + (isDemoMode ? '' : ' · enviando...') + '</div>' +
      '<div>' + esc(text || '') + '</div>';
    row.appendChild(body);
    return row;
  }

  function showTypingIndicator() {
    if (!log) return;
    if (document.getElementById(typingIndicatorId)) return;
    var row = document.createElement('div');
    row.className = 'practice-row system';
    row.id = typingIndicatorId;
    var body = document.createElement('div');
    body.className = 'practice-bubble system practice-typing-indicator';
    body.textContent = 'Bot escribiendo...';
    row.appendChild(body);
    log.appendChild(row);
    scrollLogToBottom();
  }

  function clearTypingIndicator() {
    var node = document.getElementById(typingIndicatorId);
    if (node && node.parentNode) {
      node.parentNode.removeChild(node);
    }
  }

  function isNearBottom() {
    if (!log) return true;
    var distance = log.scrollHeight - (log.scrollTop + log.clientHeight);
    return distance < 120;
  }

  function scrollLogToBottom() {
    if (!log) return;
    requestAnimationFrame(function () {
      log.scrollTop = log.scrollHeight;
    });
  }

  function setComposerLoading(loading) {
    isSending = !!loading;
    sendBtn.disabled = isSending;
    input.disabled = isSending;
    if (isSending) {
      sendBtn.textContent = '...';
      sendBtn.setAttribute('aria-busy', 'true');
    } else {
      sendBtn.textContent = '➤';
      sendBtn.removeAttribute('aria-busy');
    }
  }

  function renderSendError(message) {
    if (!log) return;
    clearTypingIndicator();
    var keepBottom = isNearBottom();
    log.appendChild(bubble('warn', 'Error', message || 'No se pudo enviar el mensaje.', 'envio'));
    if (keepBottom) {
      scrollLogToBottom();
    }
  }

  function autoGrowTextarea() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
  }

  function renderCorrections(items) {
    if (!corrections) return;
    var container = corrections;
    container.innerHTML = '';
    var list = Array.isArray(items) ? items : [];
    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'practice-correction-empty';
      empty.textContent = 'Sin correcciones pendientes.';
      container.appendChild(empty);
      return;
    }
    for (var i = 0; i < list.length; i += 1) {
      var c = list[i] || {};
      var card = document.createElement('div');
      card.className = 'practice-correction-card';
      card.innerHTML =
        '<div class="d-flex justify-content-between align-items-center mb-1">' +
        '<span class="practice-correction-field">' + esc(c.field || 'campo') + '</span>' +
        '<span class="badge">' + esc(c.status || 'pending_human') + '</span>' +
        '</div>' +
        '<div class="practice-correction-values">Anterior: ' + esc(c.old_value || 'N/A') + '</div>' +
        '<div class="practice-correction-values">Nuevo: ' + esc(c.new_value || 'N/A') + '</div>';
      container.appendChild(card);
    }
  }

  function setSimpleSidebar(state) {
    if (!isDemoMode) return;
    var p = state.progress || {};
    stepLine.textContent = 'Etapa actual: ' + (state.current_step || 'N/A');
    progressLine.textContent = 'Progreso: ' + (p.percent || 0) + '%';

    var e = state.protocol_entities || {};
    if (entityName) entityName.textContent = 'Nombre: ' + (e.full_name || e.name || 'N/A');
    if (entityAge) entityAge.textContent = 'Edad: ' + (e.age || 'N/A');
    if (entityCity) entityCity.textContent = 'Ciudad: ' + (e.city || 'N/A');
    if (entityWorkType) entityWorkType.textContent = 'Modalidad: ' + (e.work_type || 'N/A');

    if (humanLine) {
      humanLine.textContent = state.requires_human ? 'Estado: requiere revisión humana' : 'Estado: sin revisión humana';
    }
  }

  function renderState(state, forceScrollBottom) {
    if (!state) return;
    var keepBottom = isNearBottom();
    clearTypingIndicator();
    log.innerHTML = '';
    var items = Array.isArray(state.chat_items) ? state.chat_items : [];
    if (!items.length) {
      var messages = state.messages || [];
      for (var i = 0; i < messages.length; i += 1) {
        var m = messages[i];
        var kind = m.direction === 'inbound' ? 'inbound' : 'outbound';
        var title = m.direction === 'inbound' ? 'Candidata' : 'Staff';
        log.appendChild(bubble(kind, title, m.text_body || '[sin texto]', m.created_at || ''));
      }
    }
    for (var j = 0; j < items.length; j += 1) {
      var item = items[j] || {};
      if (item.role === 'bot_suggested') {
        log.appendChild(botSuggestedBubble(item));
      } else if (item.role === 'candidate') {
        log.appendChild(bubble('inbound', 'Candidata', item.text || '[sin texto]', item.created_at || ''));
      } else if (item.role === 'staff' && !isDemoMode) {
        log.appendChild(bubble('outbound', 'Staff', item.text || '[sin texto]', item.created_at || ''));
      }
    }
    if (!isDemoMode && state.requires_human) {
      log.appendChild(bubble('warn', 'Requiere revisión humana', 'Este mensaje/etapa necesita validación manual.', state.current_step || ''));
    }
    if (forceScrollBottom || keepBottom) {
      scrollLogToBottom();
    }

    var progress = state.progress || {};
    headerStats.textContent = 'Etapa: ' + (state.current_step || 'N/A') + ' · Progreso: ' + (progress.current || 0) + '/' + (progress.total || 0) + ' (' + (progress.percent || 0) + '%)';

    if (isDemoMode) {
      setSimpleSidebar(state);
      return;
    }

    stepLine.textContent = 'Etapa actual: ' + (state.current_step || 'N/A') + ' · Siguiente: ' + (state.next_step || 'FIN');
    progressLine.textContent = 'Progreso: ' + (progress.current || 0) + ' / ' + (progress.total || 0) + ' (' + (progress.percent || 0) + '%)';
    humanLine.textContent = 'requires_human: ' + (state.requires_human ? 'true' : 'false') + ' · step_requires_human: ' + (state.protocol_step_requires_human ? 'true' : 'false');
    if (summaryLine) summaryLine.textContent = 'summary_status: ' + (state.summary_status || 'N/A');
    if (draftLine) draftLine.textContent = 'draft_possible: ' + pretty(state.draft_possible || {});

    if (entities) entities.textContent = pretty(state.protocol_entities || {});
    if (future) future.textContent = pretty(state.protocol_future_entities || {});
    renderCorrections(state.pending_corrections || []);
    if (suggested) {
      suggested.textContent =
        'Sugerida activa:\n' + (state.suggested_reply || state.last_prompt || '') +
        '\n\nBase protocolo:\n' + (state.base_suggested_reply || '') +
        '\n\nIA sugerida:\n' + (state.ai_suggested_reply || '') +
        '\n\nai_reply_used: ' + (state.ai_reply_used ? 'true' : 'false') +
        '\nai_reply_safety_status: ' + (state.ai_reply_safety_status || '') +
        '\nai_reply_fallback_reason: ' + (state.ai_reply_fallback_reason || '');
    }
    if (metadata) metadata.textContent = pretty(state.metadata_json || {});
    if (debugState) {
      var dbg = state.debug_protocol_state || {};
      debugState.textContent = pretty({
        current_step: state.current_step || '',
        last_completed_step: dbg.last_completed_step || '',
        suggested_step_used: dbg.suggested_step_used || '',
        requires_human: !!state.requires_human,
        blocking_reason: dbg.blocking_reason || '',
        protocol_entities: state.protocol_entities || {}
      });
    }
  }

  async function fetchState() {
    var r = await fetch('/admin/bot/practica/' + conversationId + '/estado', { credentials: 'same-origin' });
    var data = await r.json();
    if (r.ok && data.ok) renderState(data);
  }

  async function sendMessage() {
    if (isSending) return;
    var body = (input.value || '').trim();
    if (!body) return;
    activeSendToken += 1;
    var sendToken = activeSendToken;
    setComposerLoading(true);
    var keepBottom = isNearBottom();
    log.appendChild(pendingBubble(body));
    showTypingIndicator();
    if (keepBottom) scrollLogToBottom();
    try {
      var headers = { 'Content-Type': 'application/json' };
      if (csrfToken) headers['X-CSRFToken'] = csrfToken;
      var r = await fetch('/admin/bot/practica/' + conversationId + '/mensaje', {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers,
        body: JSON.stringify({ text: body })
      });
      var data = await r.json().catch(function () { return {}; });
      if (r.ok && data.ok) {
        input.value = '';
        autoGrowTextarea();
        if (sendToken === activeSendToken) clearTypingIndicator();
        renderState(data, true);
      } else {
        renderSendError('No se pudo enviar el mensaje. Revisa tu conexión local.');
      }
    } catch (err) {
      renderSendError('No se pudo enviar el mensaje. Revisa tu conexión local.');
    } finally {
      if (sendToken === activeSendToken) {
        clearTypingIndicator();
        setComposerLoading(false);
        input.focus();
      }
    }
  }

  async function controlAction(action) {
    var headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    var r = await fetch('/admin/bot/practica/' + conversationId + '/control', {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify({ action: action })
    });
    var data = await r.json();
    if (r.ok && data.ok) {
      if (data.redirect_url) {
        window.location.assign(data.redirect_url);
        return;
      }
      renderState(data);
    } else {
      alert((data && data.error) ? data.error : 'Acción no disponible');
    }
  }

  sendBtn.addEventListener('click', sendMessage);
  if (composer) {
    composer.addEventListener('submit', function (ev) {
      ev.preventDefault();
      sendMessage();
    });
  }
  input.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      sendMessage();
    }
  });
  input.addEventListener('input', autoGrowTextarea);

  document.querySelectorAll('[data-control-action]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      controlAction(btn.getAttribute('data-control-action'));
    });
  });

  if (toggleMeta && metadataCard) {
    toggleMeta.addEventListener('click', function () {
      metadataCard.classList.toggle('d-none');
    });
  }

  var initScript = document.getElementById('practice-initial-state');
  if (initScript) {
    try { renderState(JSON.parse(initScript.textContent), true); } catch (e) {}
  }
  autoGrowTextarea();

  setInterval(fetchState, 2500);
})();
