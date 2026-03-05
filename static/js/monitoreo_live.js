(function () {
  const root = document.querySelector('[data-monitoreo-page]');
  if (!root) return;

  const page = root.dataset.monitoreoPage || 'dashboard';
  const streamUrl = root.dataset.streamUrl || '';
  const logsUrl = root.dataset.logsUrl || '';
  const summaryUrl = root.dataset.summaryUrl || '';
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
  let presencePollTimer = null;
  let presencePingTimer = null;

  function setLiveStatus(isLive) {
    if (!liveStatus || !liveToggleBtn) return;
    if (isLive && !paused) {
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
      '<td><code>' + (item.action_type || '-') + '</code></td>',
      '<td>' + (item.entity_type || '-') + ' ' + (item.entity_id || '') + '</td>',
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
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted">Sin presencia reciente.</td></tr>';
      return;
    }
    presence.forEach((p) => {
      const tr = document.createElement('tr');
      const badge = p.status === 'active' ? 'bg-success' : 'bg-secondary';
      tr.innerHTML = [
        '<td>' + (p.username || '-') + ' <small class="text-muted">(' + (p.role || '-') + ')</small></td>',
        '<td><span class="badge ' + badge + '">' + String(p.status || '').toUpperCase() + '</span></td>',
        '<td><small>' + (p.current_path || '-') + '</small></td>',
        '<td><small>' + (p.last_action_type || p.last_action_hint || 'sin acciones registradas') + (p.last_action_summary ? ' — ' + p.last_action_summary : '') + '</small></td>',
        '<td>' + (p.last_seen_seconds || 0) + 's</td>'
      ].join('');
      tbody.appendChild(tr);
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

  function stopSSE() {
    if (sse) {
      sse.close();
      sse = null;
    }
  }

  function stopPolling() {
    if (logsPollTimer) clearInterval(logsPollTimer);
    if (summaryPollTimer) clearInterval(summaryPollTimer);
    if (presencePollTimer) clearInterval(presencePollTimer);
    logsPollTimer = null;
    summaryPollTimer = null;
    presencePollTimer = null;
  }

  function startPolling() {
    stopPolling();
    setLiveStatus(true);
    pollLogs().catch(() => {});
    pollSummary().catch(() => {});
    pollPresence().catch(() => {});
    logsPollTimer = setInterval(() => pollLogs().catch(() => {}), 4000);
    summaryPollTimer = setInterval(() => pollSummary().catch(() => {}), 10000);
    presencePollTimer = setInterval(() => pollPresence().catch(() => {}), 10000);
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
    await fetch(presencePingUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: JSON.stringify(body),
    });
  }

  function startPresencePing() {
    if (!presencePingUrl) return;
    presencePing().catch(() => {});
    if (presencePingTimer) clearInterval(presencePingTimer);
    presencePingTimer = setInterval(() => presencePing().catch(() => {}), 10000);
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
