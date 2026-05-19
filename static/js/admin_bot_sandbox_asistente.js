(function () {
  const root = document.getElementById('sandbox-assisted-root');
  if (!root) return;

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const pendingList = document.getElementById('pending-list');
  const chatThread = document.getElementById('chat-thread');
  const chatMeta = document.getElementById('chat-meta');
  const reviewPanel = document.getElementById('review-panel');
  const btnApprove = document.getElementById('btn-approve');
  const btnEditApprove = document.getElementById('btn-edit-approve');
  const btnReject = document.getElementById('btn-reject');
  const btnBlock = document.getElementById('btn-block');
  const btnWorker = document.getElementById('btn-worker');
  const btnPause = document.getElementById('btn-pause-real-sandbox');
  const btnResume = document.getElementById('btn-resume-real-sandbox');
  const btnOutboxArchive = document.getElementById('btn-outbox-archive');
  const btnPauseAutoReply = document.getElementById('btn-pause-auto-reply');
  const btnResumeAutoReply = document.getElementById('btn-resume-auto-reply');
  const btnResetConversation = document.getElementById('btn-reset-conversation');
  const btnRefresh = document.getElementById('btn-refresh-list');
  const btnDebug = document.getElementById('btn-debug');

  let currentReviewId = parseInt(root.dataset.initialReviewId || '0', 10) || null;
  let currentReview = null;
  const sendMode = (root.dataset.sendMode || 'fake').toLowerCase();
  let autoRefreshTimer = null;
  const AUTO_REFRESH_MS = 2500;
  let lastRenderedMessageId = 0;
  let currentConversationId = parseInt(root.dataset.initialConversationId || '0', 10) || null;

  function badge(status) {
    const map = {
      pending_review: 'warning',
      edited: 'primary',
      approved: 'success',
      rejected: 'danger',
      blocked: 'dark',
      simulated_sent: 'info'
    };
    const color = map[status] || 'secondary';
    return `<span class="badge text-bg-${color}">${status || 'unknown'}</span>`;
  }

  function jsonFetch(url, opts) {
    const options = opts || {};
    options.headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, options.headers || {});
    return fetch(url, options).then((r) => r.json().then((j) => ({ status: r.status, body: j })));
  }

  function showToast(message, type) {
    if (window.AppToast && typeof window.AppToast.show === 'function') {
      window.AppToast.show(message, type || 'primary');
      return;
    }
    alert(message);
  }

  function renderPending(items) {
    pendingList.innerHTML = '';
    (items || []).forEach((item) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'list-group-item list-group-item-action text-start';
      if (currentReviewId === item.id) btn.classList.add('active');
      const mediaBadge = item.is_media ? `<span class="badge text-bg-info ms-1">${item.message_type}</span>` : '';
      const humanBadge = item.requires_human ? '<span class="badge text-bg-warning ms-1">requires_human</span>' : '';
      btn.innerHTML = `<div class="d-flex justify-content-between"><strong>#${item.id}</strong>${badge(item.status)}</div><div class="small">${item.conversation_phone || ''}</div><div class="small text-truncate">${item.inbound_text || '(media)'}</div>${mediaBadge}${humanBadge}`;
      btn.addEventListener('click', function () { loadReview(item.id); });
      pendingList.appendChild(btn);
    });
  }

  function renderChat(messages, review) {
    const prevLast = lastRenderedMessageId;
    chatThread.innerHTML = '';
    let nextLast = 0;
    (messages || []).forEach((m) => {
      const msgId = parseInt(m.id || '0', 10) || 0;
      if (msgId > nextLast) nextLast = msgId;
      const wrap = document.createElement('div');
      const inbound = m.direction === 'inbound';
      wrap.className = 'mb-2 d-flex ' + (inbound ? 'justify-content-start' : 'justify-content-end');
      wrap.innerHTML = `<div class="p-2 rounded ${inbound ? 'bg-light border' : 'bg-success-subtle border'}" style="max-width:85%;"><div class="small">${(m.text_body || '[' + (m.message_type || 'text') + ']').replace(/</g, '&lt;')}</div><div class="text-muted" style="font-size:11px;">${m.created_at || ''}</div></div>`;
      chatThread.appendChild(wrap);
    });
    lastRenderedMessageId = nextLast;
    if (nextLast > prevLast || prevLast === 0) {
      chatThread.scrollTop = chatThread.scrollHeight;
    }
    const modeLabel = sendMode === 'real_sandbox' ? 'Sandbox real habilitado' : 'No enviado real';
    chatMeta.textContent = `Review #${review.id} · conversation #${review.conversation_id} · Sandbox · ${modeLabel}`;
    currentConversationId = parseInt(review.conversation_id || '0', 10) || currentConversationId;
  }

  function renderPanel(review) {
    const mediaAlert = review.is_media ? '<div class="alert alert-warning py-1 px-2 mb-2">Media detectada: revisión humana obligatoria.</div>' : '';
    const validationAlert = review.validation_error
      ? `<div class="alert alert-danger py-1 px-2 mb-2"><strong>Respuesta inválida detectada</strong><div class="small mt-1">Error: ${(review.validation_error || '').replace(/</g, '&lt;')}</div><div class="small">Última respuesta: ${((review.last_invalid_answer || '-').replace(/</g, '&lt;'))}</div></div>`
      : '';
    const fallback = review.fallback_reason ? `<div class="small text-muted">IA fallback: ${review.fallback_reason}</div>` : '';
    reviewPanel.innerHTML = `${mediaAlert}${validationAlert}
      <div class="mb-2">${badge(review.status)} ${review.requires_human ? '<span class="badge text-bg-danger">requires_human</span>' : ''}</div>
      <div class="mb-2"><strong>Último mensaje candidata:</strong><div class="small">${(review.inbound_text || '(sin texto)').replace(/</g, '&lt;')}</div></div>
      <div class="mb-2"><strong>Base protocol reply:</strong><div class="small">${(review.base_protocol_reply || '').replace(/</g, '&lt;')}</div></div>
      <div class="mb-2"><strong>IA reply:</strong><div class="small">${(review.ai_reply || '').replace(/</g, '&lt;')}</div></div>
      <div class="mb-2"><strong>Respuesta sugerida editable:</strong><textarea id="edited-reply" class="form-control form-control-sm" rows="4">${(review.final_suggested_reply || '').replace(/</g, '&lt;')}</textarea></div>
      <div class="mb-2"><strong>Safety status:</strong> <span class="badge text-bg-info">${review.safety_status || 'pending'}</span></div>
      <div class="mb-2"><strong>Delivery:</strong> <span class="badge text-bg-secondary">${review.delivery_status || 'queued'}</span></div>
      <div class="mb-2"><strong>Enviado:</strong> <span class="badge text-bg-${review.sent ? 'success' : 'secondary'}">${review.sent ? 'sí' : 'no'}</span></div>
      <div class="mb-2"><strong>Modo review:</strong> <span class="badge text-bg-light">${(review.review_mode || 'manual')}</span></div>
      <div class="mb-2"><strong>Auto sent at:</strong> <span class="small">${(review.auto_sent_at || '-')}</span></div>
      <div class="mb-2"><strong>Outbox ID:</strong> <code>${String(review.outbox_id || '-')}</code></div>
      <div class="mb-2"><strong>WAMID:</strong> <code>${(review.wamid || '-').replace(/</g, '&lt;')}</code></div>
      <div class="mb-2"><strong>Provider summary:</strong> <span class="small">${(review.provider_response_summary || '-').replace(/</g, '&lt;')}</span></div>
      <div class="mb-2"><strong>Last HTTP status:</strong> <span class="badge text-bg-light">${String(review.last_http_status || '-')}</span></div>
      <div class="mb-2"><strong>Meta error code:</strong> <span class="small">${(review.meta_error_code || '-').replace(/</g, '&lt;')}</span></div>
      <div class="mb-2"><strong>Meta error message:</strong> <span class="small">${(review.meta_error_message || '-').replace(/</g, '&lt;')}</span></div>
      <hr />
      <div class="mb-2"><strong>Datos capturados:</strong><pre class="small mb-0">${JSON.stringify(review.interview_collected_data || {}, null, 2)}</pre></div>
      <div class="mb-2"><strong>Datos detectados (futuros):</strong><pre class="small mb-0">${JSON.stringify(review.interview_detected_future_data || {}, null, 2)}</pre></div>
      <div class="mb-2"><strong>Candidata borrador creada:</strong> <span class="badge text-bg-${review.draft_candidate_created ? 'success' : 'secondary'}">${review.draft_candidate_created ? 'sí' : 'no'}</span></div>
      <div class="mb-2 d-flex gap-2">
        <a class="btn btn-outline-dark btn-sm ${review.draft_candidate_created ? '' : 'disabled'}" ${review.draft_candidate_created ? `href="/admin/bot/conversaciones/${review.conversation_id}"` : 'href="#"'}>Ver borrador</a>
        <button class="btn btn-outline-primary btn-sm" id="btn-create-draft-candidate" ${review.draft_candidate_created ? 'disabled' : ''}>Crear/Actualizar borrador</button>
      </div>
      <div class="mb-2"><strong>Outbox state:</strong> <span class="badge text-bg-dark">${review.outbound_state || 'queued'}</span></div>
      <div class="mb-2"><strong>Modo:</strong> <span class="badge text-bg-light">${review.outbound_mode || (sendMode === 'real_sandbox' ? 'real_sandbox' : 'offline')}</span></div>
      <div class="mb-2"><strong>Provider:</strong> <span class="badge text-bg-light">${review.outbound_provider || 'fake'}</span></div>
      <div class="mb-2"><strong>Fail reason:</strong> <span class="small">${review.outbound_failure_reason || '-'}</span></div>
      <div class="mb-2"><strong>Número:</strong> <code>${review.outbound_phone_masked || ''}</code></div>
      <div class="mb-2"><strong>Último webhook:</strong> <span class="small">${review.last_webhook || '-'}</span></div>
      ${fallback}
      <div class="small mt-2"><strong>Historial básico:</strong></div>
      <ul class="small">${(review.events || []).slice(-5).map((e) => `<li>${(e.event_type || 'event')} · ${(e.ts || '')}</li>`).join('')}</ul>
    `;

    const actionable = ['pending_review', 'edited'].includes(review.status);
    btnApprove.disabled = !actionable;
    btnEditApprove.disabled = !actionable;
    btnReject.disabled = review.status !== 'pending_review';
    btnBlock.disabled = !['pending_review', 'edited', 'approved'].includes(review.status);
    btnWorker.disabled = !review.can_send_real;
    btnDebug.href = `/admin/bot/conversaciones/${review.conversation_id}`;
    const btnCreateDraft = document.getElementById('btn-create-draft-candidate');
    if (btnCreateDraft) {
      btnCreateDraft.addEventListener('click', function () {
        btnCreateDraft.disabled = true;
        jsonFetch(`/admin/bot/sandbox/asistente/conversation/${review.conversation_id}/draft-candidate`, {
          method: 'POST',
          body: JSON.stringify({})
        }).then((res) => {
          if (!res.body || !res.body.ok) {
            showToast((res.body && res.body.error) || 'Error creando borrador', 'danger');
            return;
          }
          showToast(`Borrador listo #${res.body.draft_id}`, 'success');
          refreshAll();
        }).finally(() => { btnCreateDraft.disabled = false; });
      });
    }
  }

  function disableAfterClick(btn) {
    btn.disabled = true;
  }

  function runAction(path, payload, clickedBtn) {
    if (!currentReviewId) return Promise.resolve();
    disableAfterClick(clickedBtn);
    return jsonFetch(`/admin/bot/sandbox/asistente/review/${currentReviewId}/${path}`, {
      method: 'POST',
      body: JSON.stringify(payload || {})
    }).then((res) => {
      if (!res.body || !res.body.ok) {
        alert((res.body && res.body.error) || 'Error');
      }
      return refreshAll();
    });
  }

  function loadReview(id) {
    currentReviewId = id;
    return jsonFetch(`/admin/bot/sandbox/asistente/review/${id}.json`).then((res) => {
      if (!res.body || !res.body.ok) return;
      currentReview = res.body.review;
      renderChat(res.body.messages || [], currentReview);
      renderPanel(currentReview);
      refreshList();
    });
  }

  function refreshList() {
    return jsonFetch('/admin/bot/sandbox/asistente/pending.json').then((res) => {
      if (!res.body || !res.body.ok) return;
      renderPending(res.body.items || []);
    });
  }

  function refreshAll() {
    return refreshList().then(() => {
      if (currentReviewId) return loadReview(currentReviewId);
      return Promise.resolve();
    });
  }

  function startAutoRefresh() {
    if (autoRefreshTimer) return;
    autoRefreshTimer = window.setInterval(function () {
      if (document.hidden) return;
      refreshAll();
      if (window.__chatGlobalBadgeRuntime && typeof window.__chatGlobalBadgeRuntime.refreshNow === 'function') {
        window.__chatGlobalBadgeRuntime.refreshNow();
      }
    }, AUTO_REFRESH_MS);
  }

  btnRefresh.addEventListener('click', function () { refreshAll(); });
  btnApprove.addEventListener('click', function () { runAction('approve', {}, btnApprove); });
  btnEditApprove.addEventListener('click', function () {
    const txt = ((document.getElementById('edited-reply') || {}).value || '').trim();
    if (!txt) return alert('Texto requerido para editar y aprobar.');
    runAction('edit-approve', { edited_text: txt }, btnEditApprove);
  });
  btnReject.addEventListener('click', function () {
    const reason = prompt('Motivo de rechazo:');
    if (!reason || !reason.trim()) return;
    runAction('reject', { reason: reason.trim() }, btnReject);
  });
  btnBlock.addEventListener('click', function () {
    const reason = prompt('Motivo de bloqueo:');
    if (!reason || !reason.trim()) return;
    runAction('block', { reason: reason.trim() }, btnBlock);
  });
  btnWorker.addEventListener('click', function () {
    if (!currentReview) return;
    btnWorker.disabled = true;
    jsonFetch('/admin/bot/sandbox/asistente/worker/run', { method: 'POST', body: JSON.stringify({ batch_size: 50, review_id: currentReviewId || 0, conversation_id: (currentReview && currentReview.conversation_id) || 0, confirm_global: false }) })
      .then((res) => {
        if (!res.body || !res.body.ok) alert((res.body && res.body.error) || 'Error worker');
        return refreshAll();
      })
      .finally(() => { btnWorker.disabled = false; });
  });
  if (btnOutboxArchive) {
    btnOutboxArchive.addEventListener('click', function () {
      btnOutboxArchive.disabled = true;
      jsonFetch('/admin/bot/sandbox/asistente/outbox/housekeeping', { method: 'POST', body: JSON.stringify({ action: 'archive_old_pending', older_than_hours: 6, limit: 500 }) })
        .then((res) => {
          if (!res.body || !res.body.ok) alert((res.body && res.body.error) || 'Error housekeeping');
          return refreshAll();
        })
        .finally(() => { btnOutboxArchive.disabled = false; });
    });
  }

  if (btnPause) {
    btnPause.addEventListener('click', function () {
      btnPause.disabled = true;
      jsonFetch('/admin/bot/sandbox/asistente/real-sandbox/pause', { method: 'POST', body: JSON.stringify({}) })
        .then(() => refreshAll())
        .finally(() => { btnPause.disabled = false; });
    });
  }
  if (btnResume) {
    btnResume.addEventListener('click', function () {
      btnResume.disabled = true;
      jsonFetch('/admin/bot/sandbox/asistente/real-sandbox/resume', { method: 'POST', body: JSON.stringify({}) })
        .then(() => refreshAll())
        .finally(() => { btnResume.disabled = false; });
    });
  }
  if (btnPauseAutoReply) {
    btnPauseAutoReply.addEventListener('click', function () {
      btnPauseAutoReply.disabled = true;
      jsonFetch('/admin/bot/sandbox/asistente/auto-reply/pause', { method: 'POST', body: JSON.stringify({}) })
        .then(() => refreshAll())
        .finally(() => { btnPauseAutoReply.disabled = false; });
    });
  }
  if (btnResumeAutoReply) {
    btnResumeAutoReply.addEventListener('click', function () {
      btnResumeAutoReply.disabled = true;
      jsonFetch('/admin/bot/sandbox/asistente/auto-reply/resume', { method: 'POST', body: JSON.stringify({}) })
        .then(() => refreshAll())
        .finally(() => { btnResumeAutoReply.disabled = false; });
    });
  }
  if (btnResetConversation) {
    btnResetConversation.addEventListener('click', function () {
      const conversationId = parseInt(((currentReview && currentReview.conversation_id) || currentConversationId || root.dataset.initialConversationId || '0'), 10) || 0;
      if (!conversationId) {
        showToast('No se pudo determinar la conversación actual para reiniciar.', 'danger');
        return;
      }
      const confirmed = window.confirm('¿Confirmas reiniciar la conversación actual desde cero?');
      if (!confirmed) return;
      btnResetConversation.disabled = true;
      jsonFetch(`/admin/bot/sandbox/asistente/conversation/${conversationId}/reset`, {
        method: 'POST',
        body: JSON.stringify({ confirm: true, archive_pending: true })
      }).then((res) => {
        if (!res.body || !res.body.ok) {
          showToast((res.body && res.body.error) || 'Error reset', 'danger');
          return Promise.resolve();
        }
        showToast((res.body && res.body.message) || 'Conversación reiniciada correctamente. Lista para nueva prueba.', 'success');
        return refreshAll();
      }).finally(() => { btnResetConversation.disabled = false; });
    });
  }

  refreshList().then(() => {
    if (currentReviewId) loadReview(currentReviewId);
  });
  startAutoRefresh();
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refreshAll();
  });
})();
