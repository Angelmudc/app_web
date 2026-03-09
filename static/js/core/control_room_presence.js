(function () {
  function getMeta(name) {
    const node = document.querySelector('meta[name="' + name + '"]');
    return node ? String(node.content || '') : '';
  }

  function toText(value) {
    return String(value || '').trim();
  }

  function cleanName(value) {
    const txt = toText(value).replace(/\s+/g, ' ');
    if (!txt) return '';
    const normalized = txt.replace(/^cliente:\s*/i, '').replace(/^candidata:\s*/i, '').trim();
    if (!normalized) return '';
    const lower = normalized.toLowerCase();
    if (lower.indexOf('buscar candidata') >= 0) return '';
    return normalized.slice(0, 120);
  }

  function inferActionHint(pathname, entityType) {
    const p = String(pathname || '').toLowerCase();
    if (p.indexOf('matching') >= 0) return 'matching';
    if (p.indexOf('entrevistas') >= 0) return 'interview';
    if (p.indexOf('entrevista') >= 0) return 'editing_interview';
    if (p.indexOf('referencias') >= 0) return 'references';
    if (p.indexOf('referencia') >= 0) return 'editing_references';
    if (p.indexOf('solicitudes') >= 0) return 'solicitudes';
    if (p.indexOf('solicitud') >= 0) return 'editing_request';
    if (p.indexOf('/clientes/') >= 0) return 'viewing_client';
    if (p.indexOf('pago') >= 0) return 'pagos';
    if (p.indexOf('/buscar') >= 0 && entityType === 'candidata') return 'editing_candidate';
    if (p.indexOf('editar') >= 0 || p.indexOf('edit') >= 0) {
      if (entityType === 'candidata') return 'editing_candidate';
      if (entityType === 'solicitud') return 'editing_request';
      return 'editing';
    }
    if (p.indexOf('buscar') >= 0) return 'searching';
    return 'browsing';
  }

  function routeLabel(pathname) {
    const p = String(pathname || '').toLowerCase();
    if (p.indexOf('/admin/matching') >= 0) return 'Matching';
    if (p.indexOf('/admin/solicitudes') >= 0) return 'Solicitudes';
    if (p.indexOf('/admin/clientes') >= 0) return 'Clientes';
    if (p.indexOf('/admin/monitoreo') >= 0) return 'Control Room';
    if (p.indexOf('/buscar') >= 0) return 'Buscar / Editar candidata';
    if (p.indexOf('/entrevista') >= 0) return 'Entrevistas';
    if (p.indexOf('/referencias') >= 0) return 'Referencias';
    return 'App';
  }

  function entityLabel(entity) {
    const t = toText(entity && entity.entity_type);
    const name = cleanName(entity && entity.entity_name);
    const code = toText(entity && entity.entity_code);
    const id = toText(entity && entity.entity_id);
    if (!t || (!id && !name)) return '';
    const prefix = t === 'candidata' ? 'candidata' : (t === 'solicitud' ? 'solicitud' : (t === 'cliente' ? 'cliente' : 'entidad'));
    if (name && code) return prefix + ' ' + name + ' (' + code + ')';
    if (name) return prefix + ' ' + name;
    if (code) return prefix + ' ' + code;
    return prefix + ' ' + id;
  }

  function actionLabel(hint, entity) {
    const label = entityLabel(entity);
    const h = toText(hint).toLowerCase();
    if (h === 'editing_candidate') return label ? ('Editando ' + label) : 'Editando candidata';
    if (h === 'editing_request') return label ? ('Editando ' + label) : 'Editando solicitud';
    if (h === 'editing_interview') return label ? ('Editando entrevista de ' + label) : 'Editando entrevista';
    if (h === 'editing_references') return label ? ('Editando referencias de ' + label) : 'Editando referencias';
    if (h === 'viewing_client') return label ? ('Viendo ' + label) : 'Viendo cliente';
    if (h === 'matching') return label ? ('Trabajando en matching de ' + label) : 'Trabajando en matching';
    if (h === 'solicitudes') return label ? ('Revisando ' + label) : 'Revisando solicitudes';
    if (h === 'interview') return label ? ('En entrevistas de ' + label) : 'En entrevistas';
    if (h === 'references') return label ? ('Revisando referencias de ' + label) : 'Revisando referencias';
    if (h === 'searching') return 'Buscando';
    return label ? ('Navegando: ' + label) : 'Navegando';
  }

  function parsePathEntity(pathname) {
    const p = String(pathname || '').toLowerCase();
    let m = p.match(/\/clientes\/(\d+)\/solicitudes\/(\d+)/);
    if (m && m[2]) return { entity_type: 'solicitud', entity_id: m[2] };
    m = p.match(/\/matching\/solicitudes\/(\d+)/);
    if (m && m[1]) return { entity_type: 'solicitud', entity_id: m[1] };
    m = p.match(/\/solicitudes\/(\d+)/);
    if (m && m[1]) return { entity_type: 'solicitud', entity_id: m[1] };
    m = p.match(/\/clientes\/(\d+)/);
    if (m && m[1]) return { entity_type: 'cliente', entity_id: m[1] };
    m = p.match(/\/monitoreo\/candidatas\/([a-z0-9_-]+)/);
    if (m && m[1]) return { entity_type: 'candidata', entity_id: m[1] };
    m = p.match(/\/candidatas?\/([a-z0-9_-]+)/);
    if (m && m[1]) return { entity_type: 'candidata', entity_id: m[1] };
    m = p.match(/\/compatibilidad\/(\d+)\/(\d+)/);
    if (m && m[1] && m[2]) return { entity_type: 'candidata', entity_id: m[2], cliente_id: m[1] };
    return null;
  }

  function parseUrlEntity(pathname, search) {
    const params = new URLSearchParams(search || '');
    const candidataId = toText(params.get('candidata_id') || params.get('fila'));
    const solicitudId = toText(params.get('solicitud_id'));
    const clienteId = toText(params.get('cliente_id'));
    if (solicitudId) return { entity_type: 'solicitud', entity_id: solicitudId };
    if (candidataId) return { entity_type: 'candidata', entity_id: candidataId };
    if (clienteId) return { entity_type: 'cliente', entity_id: clienteId };
    return parsePathEntity(pathname);
  }

  function getFormEntity(form) {
    if (!form) return null;
    const byName = function (name) {
      const el = form.querySelector('[name="' + name + '"]');
      if (!el) return '';
      return toText(el.value);
    };
    const solicitud = byName('solicitud_id');
    if (solicitud) return { entity_type: 'solicitud', entity_id: solicitud };
    const cliente = byName('cliente_id');
    if (cliente) return { entity_type: 'cliente', entity_id: cliente };
    const candidata = byName('candidata_id') || byName('fila');
    if (candidata) return { entity_type: 'candidata', entity_id: candidata };
    const action = toText(form.getAttribute('action'));
    if (action) {
      try {
        const parsed = new URL(action, window.location.origin);
        const fromAction = parseUrlEntity(parsed.pathname, parsed.search);
        if (fromAction && fromAction.entity_id) return fromAction;
      } catch (_) {}
    }
    return null;
  }

  function inferEntity(pathname, search) {
    const body = document.body;
    const bodyType = body ? toText(body.getAttribute('data-presence-entity-type')).toLowerCase() : '';
    const bodyId = body ? toText(body.getAttribute('data-presence-entity-id')) : '';
    const bodyEntity = (bodyType && bodyId)
      ? {
        entity_type: bodyType,
        entity_id: bodyId,
        entity_name: cleanName(body.getAttribute('data-presence-entity-name')),
        entity_code: toText(body.getAttribute('data-presence-entity-code')),
      }
      : null;

    const explicit = document.querySelector('[data-presence-entity-type][data-presence-entity-id]');
    if (explicit) {
      const explicitType = toText(explicit.getAttribute('data-presence-entity-type')).toLowerCase();
      const explicitId = toText(explicit.getAttribute('data-presence-entity-id'));
      if (explicitType && explicitId) {
        return {
          entity_type: explicitType,
          entity_id: explicitId,
          entity_name: cleanName(explicit.getAttribute('data-presence-entity-name')),
          entity_code: toText(explicit.getAttribute('data-presence-entity-code')),
        };
      }
    }

    const fromUrl = parseUrlEntity(pathname, search);
    const focused = document.activeElement;
    const focusedForm = focused && focused.closest ? focused.closest('form') : null;
    const fromFocused = getFormEntity(focusedForm);
    const fromMainForm = getFormEntity(document.querySelector('form#formEditar, form#solicitud-form, form'));
    const fromUrlOrDom = fromUrl || fromFocused || fromMainForm || bodyEntity || null;
    const base = fromUrlOrDom || { entity_type: '', entity_id: '' };

    const out = {
      entity_type: toText(base.entity_type).toLowerCase(),
      entity_id: toText(base.entity_id),
      entity_name: '',
      entity_code: '',
    };

    if (out.entity_type === 'candidata') {
      out.entity_name = cleanName(
        (document.querySelector('input[name="nombre"]') || {}).value
        || (document.querySelector('h1') || {}).textContent
      );
      out.entity_code = toText((document.getElementById('codigo') || {}).value);
    } else if (out.entity_type === 'cliente') {
      out.entity_name = cleanName((document.querySelector('h1') || {}).textContent);
      const codeNode = document.querySelector('.text-info');
      out.entity_code = toText((codeNode || {}).textContent);
    } else if (out.entity_type === 'solicitud') {
      const h = toText((document.querySelector('h1') || {}).textContent);
      const m = h.match(/([A-Z]{2,5}-\d+[A-Z]?)/);
      out.entity_code = m ? toText(m[1]) : '';
      out.entity_name = cleanName(h);
    }

    return out;
  }

  function currentLocation() {
    return {
      pathname: window.location.pathname || '',
      search: window.location.search || '',
    };
  }

  function entitySignature(entity) {
    return [
      toText(entity && entity.entity_type).toLowerCase(),
      toText(entity && entity.entity_id),
      cleanName(entity && entity.entity_name),
      toText(entity && entity.entity_code),
    ].join('|');
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
  const INTERACTION_ACTIVE_WINDOW_SECONDS = 60;

  let timer = null;
  let backoffMs = HEARTBEAT_MS;
  let quickRetriesLeft = MAX_QUICK_RETRIES;
  let lastWakeAt = Date.now();
  let lastInteractionAt = new Date().toISOString();
  const loc0 = currentLocation();
  let knownEntity = inferEntity(loc0.pathname, loc0.search);
  let lastHint = inferActionHint(loc0.pathname || '', knownEntity.entity_type);
  let lastEntitySig = entitySignature(knownEntity);
  let syncTimer = null;

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

  async function sendEvent(eventType, extras) {
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    const loc = currentLocation();
    const entity = inferEntity(loc.pathname, loc.search);
    knownEntity = entity;
    const hint = inferActionHint(loc.pathname || '', entity.entity_type);
    const rLabel = routeLabel(loc.pathname || '');
    const aLabel = actionLabel(hint, entity);
    const payload = Object.assign({
      event_type: eventType,
      current_path: (loc.pathname || '') + (loc.search || ''),
      page_title: document.title || '',
      action_hint: hint,
      action_label: aLabel,
      route_label: rLabel,
      last_interaction_at: lastInteractionAt,
      client_status: currentClientStatus(),
      entity_type: entity.entity_type || undefined,
      entity_id: entity.entity_id || undefined,
      entity_name: entity.entity_name || undefined,
      entity_code: entity.entity_code || undefined,
      candidata_id: entity.entity_type === 'candidata' ? entity.entity_id : undefined,
      solicitud_id: entity.entity_type === 'solicitud' ? entity.entity_id : undefined,
      cliente_id: entity.entity_type === 'cliente' ? entity.entity_id : undefined,
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

  function queueContextSync() {
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = setTimeout(function () {
      const loc = currentLocation();
      const entity = inferEntity(loc.pathname, loc.search);
      const sig = entitySignature(entity);
      const hint = inferActionHint(loc.pathname || '', entity.entity_type);
      const entityChanged = sig !== lastEntitySig;
      const hintChanged = hint !== lastHint;

      if (entityChanged) {
        lastEntitySig = sig;
        knownEntity = entity;
        fire('open_entity', {
          action_hint: hint,
          entity_type: entity.entity_type || '',
          entity_id: entity.entity_id || '',
          entity_name: entity.entity_name || '',
          entity_code: entity.entity_code || '',
        });
      }
      if (hintChanged) {
        lastHint = hint;
        fire('intent_change', { action_hint: hint });
      }
    }, 220);
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
    const loc = currentLocation();
    const entity = inferEntity(loc.pathname, loc.search);
    const hint = inferActionHint(loc.pathname || '', entity.entity_type);
    if (hint !== lastHint) {
      lastHint = hint;
      fire('intent_change', { action_hint: hint });
    }
  }

  function initNavigationWatch() {
    const originalPushState = history.pushState;
    const originalReplaceState = history.replaceState;
    history.pushState = function () {
      originalPushState.apply(history, arguments);
      fire('page_load');
      queueContextSync();
      maybeEmitIntentChange();
    };
    history.replaceState = function () {
      originalReplaceState.apply(history, arguments);
      fire('page_load');
      queueContextSync();
      maybeEmitIntentChange();
    };
    window.addEventListener('popstate', function () {
      fire('page_load');
      queueContextSync();
      maybeEmitIntentChange();
    });
    window.addEventListener('hashchange', function () {
      fire('page_load');
      queueContextSync();
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
      queueContextSync();
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
      queueContextSync();
      schedule(0);
    });
  }

  function initContextWatch() {
    document.addEventListener('change', queueContextSync, true);
    document.addEventListener('input', queueContextSync, true);
    document.addEventListener('click', queueContextSync, true);
    const root = document.querySelector('main') || document.body;
    if (!root || !window.MutationObserver) return;
    const observer = new MutationObserver(queueContextSync);
    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['value', 'data-presence-entity-id', 'data-presence-entity-type'],
    });
  }

  startActivityTracking();
  fire('page_load');
  if (knownEntity.entity_id) {
    fire('open_entity');
  }
  queueContextSync();
  initNavigationWatch();
  initSubmitWatch();
  initSearchWatch();
  initWakeWatch();
  initConnectivityWatch();
  initContextWatch();

  window.addEventListener('focus', function () { fire('tab_focus'); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) fire('tab_focus'); });

  schedule(0);
})();
