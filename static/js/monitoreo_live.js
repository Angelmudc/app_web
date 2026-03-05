(function () {
  const root = document.querySelector('[data-monitoreo-page]');
  if (!root) return;

  const page = root.dataset.monitoreoPage || 'dashboard';
  const streamUrl = root.dataset.streamUrl || '';
  const logsUrl = root.dataset.logsUrl || '';
  const summaryUrl = root.dataset.summaryUrl || '';
  const productivityUrl = root.dataset.productivityUrl || '';
  const presenceUrl = root.dataset.presenceUrl || '';
  const presencePingUrl = root.dataset.presencePingUrl || '';
  const hasFilters = String(root.dataset.hasFilters || '0') === '1';

  const tableBody = document.querySelector('#liveLogsTable tbody');
  const liveStatus = document.getElementById('liveStatus');
  const liveToggleBtn = document.getElementById('liveToggleBtn');
  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  let lastId = Number(root.dataset.initialLastId || 0);
  let paused = false;
  let sse = null;
  let logsPollTimer = null;
  let summaryPollTimer = null;
  let productivityPollTimer = null;
  let presencePollTimer = null;
  let presencePingTimer = null;
  let presencePingDelayMs = 10000;
  let presencePingStatus = 'live';

  function setLiveStatus(isLive) {
    if (!liveStatus || !liveToggleBtn) return;
    if (isLive && !paused && presencePingStatus !== 'paused') {
      liveStatus.textContent = '● EN VIVO';
      liveStatus.classList.remove('paused');
      liveStatus.classList.add('live');
      liveToggleBtn.textContent = 'Pausar';
      return;
    }
    liveStatus.textContent = '● PAUSADO';
    liveStatus.classList.remove('live');
    liveStatus.classList.add('paused');
    liveToggleBtn.textContent = 'Reanudar';
  }

  function updatePresencePingState(nextState, reason) {
    if (presencePingStatus === nextState) return;
    presencePingStatus = nextState;
    if (nextState === 'paused') {
      setLiveStatus(false);
      console.warn('[monitoreo] presence ping pausado: ' + reason);
      return;
    }
    if (!paused) setLiveStatus(true);
    console.warn('[monitoreo] presence ping reanudado');
  }

  function formatDate(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  }

  function resultBadge(ok) {
    return ok ? '<span class="badge bg-success">OK</span>' : '<span class="badge bg-danger">ERROR</span>';
  }

  function insertLogRow(item) {
    if (!tableBody || !item || !item.id) return;
    if (tableBody.querySelector('tr[data-log-id="' + item.id + '"]')) return;

    const tr = document.createElement('tr');
    tr.dataset.logId = String(item.id);
    tr.classList.add('log-new');
    tr.innerHTML = [
      '<td>' + formatDate(item.created_at) + '</td>',
      '<td>' + (item.actor_username || '-') + '</td>',
      '<td>' + (item.action_human || item.action_type || '-') + '</td>',
      '<td>' + (item.entity_display || ((item.entity_type || '-') + ' ' + (item.entity_id || ''))) + '</td>',
      '<td>' + (item.summary || '-') + '</td>',
      '<td>' + resultBadge(Boolean(item.success)) + '</td>'
    ].join('');

    const emptyRow = tableBody.querySelector('tr td[colspan]');
    if (emptyRow && emptyRow.parentElement) {
      emptyRow.parentElement.remove();
    }

    tableBody.insertBefore(tr, tableBody.firstChild);
    const rows = tableBody.querySelectorAll('tr');
    if (rows.length > 300) {
      for (let i = 300; i < rows.length; i += 1) rows[i].remove();
    }
  }

  function updateTopList(top) {
    const list = document.getElementById('topList');
    if (!list) return;
    list.innerHTML = '';
    if (!Array.isArray(top) || !top.length) {
      list.innerHTML = '<li class="list-group-item text-muted">Sin actividad.</li>';
      return;
    }
    top.forEach((row) => {
      const li = document.createElement('li');
      li.className = 'list-group-item d-flex justify-content-between align-items-center';
      li.innerHTML = '<a href="/admin/monitoreo/secretarias/' + row.user_id + '">' + (row.username || '-') + '</a><span class="badge bg-dark">' + (row.total_actions || 0) + '</span>';
      list.appendChild(li);
    });
  }

  function updatePresenceTable(presence) {
    const tbody = document.querySelector('#presenceTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!Array.isArray(presence) || !presence.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-muted">Sin presencia reciente.</td></tr>';
      return;
    }
    presence.forEach((p) => {
      const tr = document.createElement('tr');
      const badge = p.status === 'active' ? 'bg-success' : 'bg-secondary';
      tr.innerHTML = [
        '<td>' + (p.username || '-') + ' <small class="text-muted">(' + (p.role || '-') + ')</small></td>',
        '<td><span class="badge ' + badge + '">' + String(p.status || '').toUpperCase() + '</span></td>',
        '<td><small>' + (p.route_human || '-') + '</small></td>',
        '<td><small title="' + (p.current_path || '-') + '">' + (p.current_path || '-') + '</small></td>',
        '<td><small>' + (p.current_action_human || p.last_action_type || p.last_action_hint || 'sin acciones registradas') + '</small></td>',
        '<td><small>' + (p.entity_display || '-') + '</small></td>',
        '<td>' + (p.last_seen_seconds || 0) + 's</td>'
      ].join('');
      tbody.appendChild(tr);
    });
  }

  function updateOperations(metrics) {
    const src = metrics || {};
    ['active_secretarias', 'candidatas_editing_now', 'solicitudes_en_proceso', 'entrevistas_hoy', 'matching_hoy'].forEach((k) => {
      const el = document.querySelector('[data-op-metric="' + k + '"]');
      if (el && src[k] !== undefined && src[k] !== null) el.textContent = String(src[k]);
    });
  }

  function updateConflicts(conflicts) {
    const box = document.getElementById('conflictsBox');
    if (!box) return;
    const rows = Array.isArray(conflicts) ? conflicts : [];
    if (!rows.length) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = rows.map((c) => {
      const users = Array.isArray(c.users) ? c.users.join(', ') : '';
      return '<div class="alert alert-warning py-2 mb-2"><strong>Conflicto:</strong> ' + (c.message || 'Conflicto de edicion') + ' - ' + (c.entity_display || '-') + (users ? ' (' + users + ')' : '') + '</div>';
    }).join('');
  }

  function updateActivityStream(items) {
    const root = document.getElementById('activityStream');
    if (!root) return;
    const rows = Array.isArray(items) ? items.slice(-20).reverse() : [];
    if (!rows.length) {
      root.innerHTML = '<div class="text-muted">Sin actividad reciente.</div>';
      return;
    }
    root.innerHTML = rows.map((item) => {
      return [
        '<div class="border rounded p-2 mb-2" data-activity-id="' + (item.id || '') + '">',
        '<div><strong>' + (item.actor_username || '-') + '</strong> - ' + (item.action_human || item.action_type || '-') + '</div>',
        '<div class="small text-muted">' + (item.entity_display || '-') + ' | ' + (item.route_human || item.route || '-') + '</div>',
        '</div>',
      ].join('');
    }).join('');
  }

  function updateProductivity(payload) {
    const tbody = document.querySelector('#productivityTable tbody');
    const topBadge = document.getElementById('productivityTopBadge');
    if (!tbody) return;

    const users = (payload && Array.isArray(payload.users)) ? payload.users : [];
    const previousTotals = {};
    tbody.querySelectorAll('tr[data-user-id]').forEach((tr) => {
      const uid = Number(tr.getAttribute('data-user-id') || 0);
      previousTotals[uid] = Number(tr.getAttribute('data-total') || 0);
    });
    tbody.innerHTML = '';

    if (!users.length) {
      if (topBadge) topBadge.style.display = 'none';
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted">Sin actividad operativa hoy.</td></tr>';
      return;
    }

    if (topBadge) topBadge.style.display = '';
    users.forEach((row, idx) => {
      const userId = Number(row.user_id || 0);
      const prevTotal = Object.prototype.hasOwnProperty.call(previousTotals, userId) ? previousTotals[userId] : Number(row.total || 0);
      const changed = Object.prototype.hasOwnProperty.call(previousTotals, userId) && prevTotal !== Number(row.total || 0);
      const tr = document.createElement('tr');
      tr.dataset.userId = String(userId);
      tr.dataset.total = String(Number(row.total || 0));
      if (idx === 0) tr.classList.add('table-warning');
      if (changed) tr.classList.add('table-info');
      tr.innerHTML = [
        '<td>' + (row.username || '-') + ' <small class="text-muted">(' + (row.role || '-') + ')</small>' + (idx === 0 ? '<span class="badge bg-dark ms-1">Top</span>' : '') + '</td>',
        '<td>' + Number(row.edits || 0) + '</td>',
        '<td>' + Number(row.interviews || 0) + '</td>',
        '<td>' + Number(row.sent || 0) + '</td>',
        '<td><strong>' + Number(row.total || 0) + '</strong></td>'
      ].join('');
      tbody.appendChild(tr);
      if (changed) {
        setTimeout(() => tr.classList.remove('table-info'), 1200);
      }
    });
  }

  function updateMetrics(summary) {
    if (!summary) return;
    const map = {
      'today.total_actions': summary.today && summary.today.total_actions,
      'week.total_actions': summary.week && summary.week.total_actions,
      'month.total_actions': summary.month && summary.month.total_actions,
      'week.candidatas_enviadas': summary.week && summary.week.candidatas_enviadas,
    };

    Object.keys(map).forEach((k) => {
      const el = document.querySelector('[data-metric="' + k + '"]');
      if (el && map[k] !== undefined && map[k] !== null) el.textContent = String(map[k]);
    });

    updateTopList(summary.top || []);
    if (Array.isArray(summary.presence)) {
      updatePresenceTable(summary.presence);
    }
    if (summary.productivity) {
      updateProductivity(summary.productivity);
    }
    if (summary.operations) {
      updateOperations(summary.operations);
    }
    if (summary.presence_conflicts) {
      updateConflicts(summary.presence_conflicts);
    }
    if (summary.activity_stream) {
      updateActivityStream(summary.activity_stream);
    }
  }

  async function fetchJson(url) {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  }

  async function pollLogs() {
    if (paused || !logsUrl) return;
    const params = new URLSearchParams(window.location.search);
    params.set('since_id', String(lastId || 0));
    params.set('limit', '50');
    const data = await fetchJson(logsUrl + '?' + params.toString());
    (data.items || []).forEach((item) => {
      insertLogRow(item);
      lastId = Math.max(lastId, Number(item.id || 0));
    });
    if (data.last_id) lastId = Math.max(lastId, Number(data.last_id));
  }

  async function pollSummary() {
    if (paused || !summaryUrl) return;
    const data = await fetchJson(summaryUrl);
    updateMetrics(data);
  }

  async function pollPresence() {
    if (paused || !presenceUrl) return;
    const data = await fetchJson(presenceUrl);
    updatePresenceTable((data && data.items) || []);
  }

  async function pollProductivity() {
    if (paused || !productivityUrl) return;
    const data = await fetchJson(productivityUrl);
    updateProductivity(data || {});
  }

  function stopSSE() {
    if (sse) {
      sse.close();
      sse = null;
    }
  }

  function stopPolling() {
    if (logsPollTimer) clearInterval(logsPollTimer);
    if (summaryPollTimer) clearInterval(summaryPollTimer);
    if (productivityPollTimer) clearInterval(productivityPollTimer);
    if (presencePollTimer) clearInterval(presencePollTimer);
    logsPollTimer = null;
    summaryPollTimer = null;
    productivityPollTimer = null;
    presencePollTimer = null;
  }

  function startPolling() {
    stopPolling();
    setLiveStatus(true);
    pollLogs().catch(() => {});
    pollSummary().catch(() => {});
    pollProductivity().catch(() => {});
    pollPresence().catch(() => {});
    logsPollTimer = setInterval(() => pollLogs().catch(() => {}), 4000);
    summaryPollTimer = setInterval(() => pollSummary().catch(() => {}), 10000);
    productivityPollTimer = setInterval(() => pollProductivity().catch(() => {}), 15000);
    presencePollTimer = setInterval(() => pollPresence().catch(() => {}), 2000);
  }

  function startSSE() {
    if (!streamUrl || paused) return;
    stopSSE();
    const url = streamUrl + '?last_id=' + encodeURIComponent(String(lastId || 0));
    sse = new EventSource(url, { withCredentials: true });

    sse.addEventListener('log', (ev) => {
      try {
        const item = JSON.parse(ev.data || '{}');
        insertLogRow(item);
        lastId = Math.max(lastId, Number(item.id || 0));
      } catch (_) {}
    });

    sse.addEventListener('summary', (ev) => {
      try {
        const data = JSON.parse(ev.data || '{}');
        updateMetrics(data);
      } catch (_) {}
    });

    sse.addEventListener('presence', (ev) => {
      try {
        const data = JSON.parse(ev.data || '{}');
        updatePresenceTable((data && data.items) || []);
      } catch (_) {}
    });

    sse.addEventListener('active_snapshot', (ev) => {
      try {
        const data = JSON.parse(ev.data || '{}');
        updatePresenceTable((data && data.items) || []);
        updateConflicts((data && data.conflicts) || []);
      } catch (_) {}
    });

    sse.addEventListener('operations', (ev) => {
      try {
        const data = JSON.parse(ev.data || '{}');
        updateOperations((data && data.metrics) || {});
      } catch (_) {}
    });

    sse.addEventListener('activity', (ev) => {
      try {
        const data = JSON.parse(ev.data || '{}');
        updateActivityStream((data && data.items) || []);
      } catch (_) {}
    });

    sse.addEventListener('heartbeat', () => {
      setLiveStatus(true);
    });

    sse.onerror = function () {
      stopSSE();
      startPolling();
    };

    setLiveStatus(true);
  }

  async function presencePing() {
    if (!presencePingUrl) return;
    const body = {
      current_path: window.location.pathname + window.location.search,
      page_title: document.title || '',
    };
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    const resp = await fetch(presencePingUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
  }

  function schedulePresencePing(delayMs) {
    if (presencePingTimer) clearTimeout(presencePingTimer);
    presencePingTimer = setTimeout(runPresencePing, delayMs);
  }

  async function runPresencePing() {
    if (!presencePingUrl) return;
    try {
      await presencePing();
      presencePingDelayMs = 10000;
      updatePresencePingState('live', 'ok');
    } catch (err) {
      presencePingDelayMs = Math.min(60000, presencePingDelayMs * 2);
      const reason = (err && err.message) ? err.message : 'network_error';
      updatePresencePingState('paused', reason);
    } finally {
      schedulePresencePing(presencePingDelayMs);
    }
  }

  function startPresencePing() {
    if (document.body && document.body.getAttribute('data-live-presence-enabled') === '1') return;
    if (!presencePingUrl) return;
    presencePingDelayMs = 10000;
    schedulePresencePing(0);
  }

  if (liveToggleBtn) {
    liveToggleBtn.addEventListener('click', function () {
      paused = !paused;
      if (paused) {
        stopSSE();
        stopPolling();
        setLiveStatus(false);
      } else {
        if (page === 'dashboard' && !hasFilters) {
          startSSE();
        } else {
          startPolling();
        }
      }
    });
  }

  startPresencePing();
  if (page === 'dashboard' && !hasFilters) {
    startSSE();
  } else {
    startPolling();
  }
})();
