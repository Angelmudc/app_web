(function () {
  function getMeta(name) {
    const node = document.querySelector('meta[name="' + name + '"]');
    return node ? String(node.content || '') : '';
  }

  function inferActionHint(pathname) {
    const p = String(pathname || '').toLowerCase();
    if (p.indexOf('matching') >= 0) return 'matching';
    if (p.indexOf('entrevista') >= 0) return 'editing_interview';
    if (p.indexOf('referencia') >= 0) return 'editing_references';
    if (p.indexOf('solicitud') >= 0) return 'editing_request';
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
  const basePath = window.location.pathname || '';
  const baseQuery = window.location.search || '';
  const entity = inferEntity(basePath, baseQuery);
  const actionHint = inferActionHint(basePath);

  let backoffMs = 8000;
  let timer = null;
  let sentOpenEntity = false;

  async function sendEvent(eventType) {
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    const payload = {
      event_type: eventType,
      current_path: (window.location.pathname || '') + (window.location.search || ''),
      page_title: document.title || '',
      action_hint: inferActionHint(window.location.pathname || ''),
      candidata_id: entity.candidata_id || undefined,
      solicitud_id: entity.solicitud_id || undefined,
      cliente_id: entity.cliente_id || undefined,
    };
    await fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify(payload),
    });
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(runHeartbeat, ms);
  }

  async function runHeartbeat() {
    try {
      await sendEvent('heartbeat');
      backoffMs = 8000;
    } catch (_) {
      backoffMs = Math.min(60000, backoffMs * 2);
    } finally {
      schedule(backoffMs);
    }
  }

  function sendSilently(eventType) {
    sendEvent(eventType).catch(function () {});
  }

  sendSilently('page_load');
  if ((entity.candidata_id || entity.solicitud_id || entity.cliente_id) && !sentOpenEntity) {
    sentOpenEntity = true;
    sendSilently('open_entity');
  }
  schedule(0);

  window.addEventListener('focus', function () {
    sendSilently('tab_focus');
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) sendSilently('tab_focus');
  });
})();
