(function () {
  const root = document.querySelector('[data-monitoreo-page="candidata-historial"]');
  if (!root) return;

  const streamUrl = root.dataset.candidataStreamUrl || '';
  const logsUrl = root.dataset.candidataLogsUrl || '';
  const presencePingUrl = root.dataset.presencePingUrl || '';
  const activeFilter = root.dataset.activeFilter || '';
  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  const liveStatus = document.getElementById('liveStatus');
  const liveToggleBtn = document.getElementById('liveToggleBtn');
  const timeline = document.getElementById('candidataTimeline');

  let lastId = Number(root.dataset.initialLastId || 0);
  let paused = false;
  let sse = null;
  let pollTimer = null;
  let pingTimer = null;
  let lastInteractionAt = new Date().toISOString();
  const INTERACTION_ACTIVE_WINDOW_SECONDS = 60;
  const MONITOREO_PREFIX = '/admin/monitoreo';

  function isMonitoringPage(pathname) {
    const raw = (pathname || window.location.pathname || '').split('?')[0];
    const path = String(raw || '').trim();
    return path === MONITOREO_PREFIX || path.indexOf(MONITOREO_PREFIX + '/') === 0;
  }

  function registerInteraction() {
    lastInteractionAt = new Date().toISOString();
  }

  function currentClientStatus() {
    if (document.visibilityState === 'hidden') return 'hidden';
    const last = new Date(lastInteractionAt);
    if (Number.isNaN(last.getTime())) return 'idle';
    const deltaSeconds = Math.max(0, Math.floor((Date.now() - last.getTime()) / 1000));
    return deltaSeconds <= INTERACTION_ACTIVE_WINDOW_SECONDS ? 'active' : 'idle';
  }

  function startActivityTracking() {
    ['mousemove', 'keydown', 'click', 'scroll'].forEach((evt) => {
      window.addEventListener(evt, registerInteraction, { passive: true });
    });
    document.addEventListener('visibilitychange', registerInteraction, { passive: true });
  }

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

  function toPrettyJson(value) {
    if (!value) return '';
    try { return JSON.stringify(value, null, 2); } catch (_) { return ''; }
  }

  function resultBadge(ok) {
    return ok ? '<span class="badge bg-success">OK</span>' : '<span class="badge bg-danger">ERROR</span>';
  }

  function insertLog(item) {
    if (!timeline || !item || !item.id) return;
    if (timeline.querySelector('[data-log-id="' + item.id + '"]')) return;

    const card = document.createElement('article');
    card.className = 'card log-new';
    card.dataset.logId = String(item.id);
    const detailsChanges = item.changes_json ? '<details class="mt-2"><summary>Cambios</summary><pre class="mb-0">' + toPrettyJson(item.changes_json) + '</pre></details>' : '';
    const detailsMeta = item.metadata_json ? '<details class="mt-2"><summary>Metadata</summary><pre class="mb-0">' + toPrettyJson(item.metadata_json) + '</pre></details>' : '';

    card.innerHTML = [
      '<div class="card-body">',
      '<div class="d-flex justify-content-between align-items-start gap-2 flex-wrap">',
      '<div><div><strong>' + (item.actor_username || '-') + '</strong> <small class="text-muted">(' + (item.actor_role || '-') + ')</small></div>',
      '<div><code>' + (item.action_type || '-') + '</code> · ' + (item.summary || '-') + '</div></div>',
      '<div class="text-end"><div><small>' + formatDate(item.created_at) + '</small></div><div>' + resultBadge(Boolean(item.success)) + '</div></div>',
      '</div>',
      '<div class="text-muted"><small>' + (item.method || '-') + ' ' + (item.route || '-') + '</small></div>',
      detailsChanges,
      detailsMeta,
      '</div>'
    ].join('');

    const empty = timeline.querySelector(':scope > .text-muted');
    if (empty) empty.remove();
    timeline.insertBefore(card, timeline.firstChild);

    const rows = timeline.querySelectorAll(':scope > [data-log-id]');
    if (rows.length > 300) {
      for (let i = 300; i < rows.length; i += 1) rows[i].remove();
    }
  }

  async function fetchJson(url) {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  }

  async function pollLogs() {
    if (paused || !logsUrl || !isMonitoringPage()) return;
    const params = new URLSearchParams();
    params.set('since_id', String(lastId || 0));
    params.set('limit', '50');
    if (activeFilter) params.set('filter', activeFilter);
    const data = await fetchJson(logsUrl + '?' + params.toString());
    (data.items || []).forEach((item) => {
      insertLog(item);
      lastId = Math.max(lastId, Number(item.id || 0));
    });
    if (data.last_id) lastId = Math.max(lastId, Number(data.last_id));
  }

  function stopSSE() {
    if (sse) {
      sse.close();
      sse = null;
    }
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function startPolling() {
    if (!isMonitoringPage()) return;
    stopPolling();
    setLiveStatus(true);
    pollLogs().catch(() => {});
    pollTimer = setInterval(() => pollLogs().catch(() => {}), 5000);
  }

  function startSSE() {
    if (!streamUrl || paused || activeFilter || !isMonitoringPage()) return;
    stopSSE();
    const params = new URLSearchParams();
    params.set('last_id', String(lastId || 0));
    sse = new EventSource(streamUrl + '?' + params.toString(), { withCredentials: true });

    sse.addEventListener('candidatelog', (ev) => {
      try {
        const item = JSON.parse(ev.data || '{}');
        insertLog(item);
        lastId = Math.max(lastId, Number(item.id || 0));
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
    if (!presencePingUrl || !isMonitoringPage()) return;
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    await fetch(presencePingUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: JSON.stringify({
        current_path: window.location.pathname + window.location.search,
        page_title: document.title || '',
        last_action_hint: 'monitoreo_candidata_historial',
        last_interaction_at: lastInteractionAt,
        client_status: currentClientStatus(),
      }),
    });
  }

  function startPresencePing() {
    if (!presencePingUrl || !isMonitoringPage()) return;
    presencePing().catch(() => {});
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = setInterval(() => presencePing().catch(() => {}), 10000);
  }

  function stopPresencePing() {
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = null;
  }

  function stopMonitoringRealtime() {
    stopSSE();
    stopPolling();
    stopPresencePing();
    setLiveStatus(false);
  }

  if (liveToggleBtn) {
    liveToggleBtn.addEventListener('click', function () {
      paused = !paused;
      if (paused) {
        stopSSE();
        stopPolling();
        setLiveStatus(false);
      } else {
        if (activeFilter) {
          startPolling();
        } else {
          startSSE();
        }
      }
    });
  }

  startActivityTracking();
  if (!isMonitoringPage()) {
    stopMonitoringRealtime();
    return;
  }
  startPresencePing();
  if (activeFilter) startPolling();
  else startSSE();

  window.addEventListener('popstate', function () {
    if (!isMonitoringPage()) stopMonitoringRealtime();
  });
  window.addEventListener('hashchange', function () {
    if (!isMonitoringPage()) stopMonitoringRealtime();
  });
  window.addEventListener('pagehide', stopMonitoringRealtime);
  window.addEventListener('beforeunload', stopMonitoringRealtime);
})();
