(function () {
  function getMeta(name) {
    const node = document.querySelector('meta[name="' + name + '"]');
    return node ? String(node.content || '') : '';
  }

  function inferActionHint(pathname) {
    const p = String(pathname || '').toLowerCase();
    if (p.indexOf('matching') >= 0) return 'matching';
    if (p.indexOf('entrevistas') >= 0) return 'interview';
    if (p.indexOf('entrevista') >= 0) return 'editing_interview';
    if (p.indexOf('referencias') >= 0) return 'references';
    if (p.indexOf('referencia') >= 0) return 'editing_references';
    if (p.indexOf('solicitudes') >= 0) return 'solicitudes';
    if (p.indexOf('solicitud') >= 0) return 'editing_request';
    if (p.indexOf('pago') >= 0) return 'pagos';
    if (p.indexOf('editar') >= 0 || p.indexOf('edit') >= 0) return 'editing';
    if (p.indexOf('buscar') >= 0) return 'searching';
    return 'browsing';
  }

  function inferEntity(pathname, search) {
    const params = new URLSearchParams(search || '');
    const out = { candidata_id: '', solicitud_id: '', cliente_id: '' };
    out.candidata_id = params.get('candidata_id') || '';
    out.solicitud_id = params.get('solicitud_id') || '';
    out.cliente_id = params.get('cliente_id') || '';

    const p = String(pathname || '').toLowerCase();
    if (!out.candidata_id) {
      const m = p.match(/\/candidatas?\/([a-z0-9_-]+)/);
      if (m) out.candidata_id = m[1];
    }
    if (!out.solicitud_id) {
      const m = p.match(/\/solicitudes?\/([a-z0-9_-]+)/);
      if (m) out.solicitud_id = m[1];
    }
    return out;
  }

  const body = document.body;
  if (!body) return;
  const endpoint = body.getAttribute('data-live-presence-url') || '';
  const enabled = body.getAttribute('data-live-presence-enabled') === '1';
  if (!enabled || !endpoint) return;

  const csrfToken = getMeta('csrf-token');
  const HEARTBEAT_MS = 5000;
  const QUICK_RETRY_MS = 1200;
  const MAX_BACKOFF_MS = 60000;
  const MAX_QUICK_RETRIES = 2;

  let timer = null;
  let backoffMs = HEARTBEAT_MS;
  let quickRetriesLeft = MAX_QUICK_RETRIES;
  let lastWakeAt = Date.now();
  let lastHint = inferActionHint(window.location.pathname || '');
  let knownEntity = inferEntity(window.location.pathname || '', window.location.search || '');
  let sentOpenEntity = false;

  async function sendEvent(eventType, extras) {
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    const entity = inferEntity(window.location.pathname || '', window.location.search || '');
    knownEntity = entity;
    const hint = inferActionHint(window.location.pathname || '');
    const payload = Object.assign({
      event_type: eventType,
      current_path: (window.location.pathname || '') + (window.location.search || ''),
      page_title: document.title || '',
      action_hint: hint,
      candidata_id: entity.candidata_id || undefined,
      solicitud_id: entity.solicitud_id || undefined,
      cliente_id: entity.cliente_id || undefined,
    }, extras || {});
    const resp = await fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp;
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(runHeartbeat, ms);
  }

  function fire(eventType, extras) {
    sendEvent(eventType, extras).catch(function () {});
  }

  async function runHeartbeat() {
    try {
      await sendEvent('heartbeat');
      backoffMs = HEARTBEAT_MS;
      quickRetriesLeft = MAX_QUICK_RETRIES;
      schedule(HEARTBEAT_MS);
    } catch (_) {
      if (quickRetriesLeft > 0) {
        quickRetriesLeft -= 1;
        schedule(QUICK_RETRY_MS);
        return;
      }
      backoffMs = Math.min(MAX_BACKOFF_MS, Math.max(backoffMs * 2, HEARTBEAT_MS));
      schedule(backoffMs);
    }
  }

  function maybeEmitIntentChange() {
    const hint = inferActionHint(window.location.pathname || '');
    if (hint !== lastHint) {
      lastHint = hint;
      fire('intent_change', { action_hint: hint });
    }
  }

  function initNavigationWatch() {
    window.addEventListener('popstate', function () {
      fire('page_load');
      maybeEmitIntentChange();
    });
    window.addEventListener('hashchange', function () {
      fire('page_load');
      maybeEmitIntentChange();
    });
  }

  function initSubmitWatch() {
    document.addEventListener('submit', function () {
      fire('submit');
    }, true);
  }

  function initSearchWatch() {
    document.addEventListener('input', function (ev) {
      const target = ev.target;
      if (!target) return;
      const tag = (target.tagName || '').toLowerCase();
      if (tag !== 'input') return;
      const type = String(target.type || '').toLowerCase();
      if (type === 'search' || String(target.name || '').toLowerCase().indexOf('search') >= 0 || String(target.name || '').toLowerCase().indexOf('q') === 0) {
        if (lastHint !== 'searching') {
          lastHint = 'searching';
          fire('intent_change', { action_hint: 'searching' });
        }
      }
    }, true);
  }

  function initWakeWatch() {
    setInterval(function () {
      const now = Date.now();
      if ((now - lastWakeAt) > 20000) {
        fire('tab_focus');
      }
      lastWakeAt = now;
    }, 10000);
  }

  function initConnectivityWatch() {
    window.addEventListener('online', function () {
      backoffMs = HEARTBEAT_MS;
      quickRetriesLeft = MAX_QUICK_RETRIES;
      fire('tab_focus');
      schedule(0);
    });
  }

  fire('page_load');
  if ((knownEntity.candidata_id || knownEntity.solicitud_id || knownEntity.cliente_id) && !sentOpenEntity) {
    sentOpenEntity = true;
    fire('open_entity');
  }
  initNavigationWatch();
  initSubmitWatch();
  initSearchWatch();
  initWakeWatch();
  initConnectivityWatch();

  window.addEventListener('focus', function () { fire('tab_focus'); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) fire('tab_focus'); });

  schedule(0);
})();
