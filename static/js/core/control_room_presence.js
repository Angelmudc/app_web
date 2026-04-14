(function () {
  var body = document.body;
  if (!body) return;

  var enabled = String(body.getAttribute('data-live-presence-enabled') || '') === '1';
  var endpoint = String(body.getAttribute('data-live-presence-url') || '').trim();
  if (!enabled || !endpoint) return;

  // Fase 2 aplica al Control Room staff/admin.
  if (endpoint.indexOf('/admin/monitoreo/presence/ping') < 0) return;
  var path = String((window.location && window.location.pathname) || '');
  if (path.indexOf('/login') === 0) return;
  if (path.indexOf('/admin/login') === 0) return;
  if (path.indexOf('/admin/mfa/') === 0) return;
  if (path.indexOf('/home') === 0) return;

  const HEARTBEAT_MS = 10000;
  var IDLE_AFTER_MS = 60000;
  var IDLE_CHECK_MS = 1000;
  var TYPING_STOP_MS = 1200;
  var ACTIVITY_THROTTLE_MS = 1200;
  var IMPORTANT_CHANGE_DEBOUNCE_MS = 250;
  var MIN_IMMEDIATE_SEND_GAP_MS = 1500;
  var MUTATION_MIN_GAP_MS = 2000;

  var typingStopTimer = null;
  var importantChangeTimer = null;
  var heartbeatTimer = null;
  var idleCheckTimer = null;
  var lastActivityUpdateMs = 0;
  var lastMutationSendMs = 0;

  var pendingReason = 'init';
  var paused = false;
  var lastSentHash = '';
  var lastSentAtMs = 0;
  var lastKnownRouteKey = '';

  var dirtyForms = new Set();

  function nowIso() {
    return new Date().toISOString();
  }

  function routeKeyFromUrlLike(urlLike) {
    try {
      var base = window.location && window.location.origin ? window.location.origin : undefined;
      var resolved = new URL(String(urlLike || window.location.href || ''), base);
      return String((resolved.pathname || '') + (resolved.search || ''));
    } catch (_) {
      return String((window.location.pathname || '') + (window.location.search || ''));
    }
  }

  function toText(value) {
    return String(value || '').trim();
  }

  function toBool(value, fallback) {
    if (typeof value === 'boolean') return value;
    if (value == null) return Boolean(fallback);
    var txt = String(value).trim().toLowerCase();
    if (txt === '1' || txt === 'true' || txt === 'yes' || txt === 'on') return true;
    if (txt === '0' || txt === 'false' || txt === 'no' || txt === 'off') return false;
    return Boolean(fallback);
  }

  function getMeta(name) {
    var node = document.querySelector('meta[name="' + name + '"]');
    return node ? String(node.content || '') : '';
  }

  function randomSessionId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
      }
    } catch (_) {}
    var seed = Math.random().toString(36).slice(2) + Date.now().toString(36);
    return 'tab-' + seed.slice(0, 24);
  }

  function getOrCreateSessionId() {
    var key = 'control_room_presence_session_id';
    var fromStorage = '';
    try {
      fromStorage = toText(window.sessionStorage.getItem(key));
    } catch (_) {
      fromStorage = '';
    }
    if (fromStorage) return fromStorage.slice(0, 120);
    var created = randomSessionId().slice(0, 120);
    try {
      window.sessionStorage.setItem(key, created);
    } catch (_) {}
    return created;
  }

  function cleanName(value) {
    var txt = toText(value).replace(/\s+/g, ' ');
    if (!txt) return '';
    var normalized = txt.replace(/^cliente:\s*/i, '').replace(/^candidata:\s*/i, '').trim();
    if (!normalized) return '';
    var lower = normalized.toLowerCase();
    if (lower.indexOf('buscar candidata') >= 0) return '';
    return normalized.slice(0, 160);
  }

  function normalizeEntityType(value) {
    var txt = toText(value).toLowerCase();
    if (txt === 'candidatas' || txt === 'candidate') return 'candidata';
    if (txt === 'solicitudes' || txt === 'request') return 'solicitud';
    if (txt === 'clientes' || txt === 'client') return 'cliente';
    if (txt === 'conversation' || txt === 'conversacion' || txt === 'chatconversation' || txt === 'chat') return 'chat_conversation';
    return txt;
  }

  function parsePathEntity(pathname) {
    var p = String(pathname || '').toLowerCase();
    var m = p.match(/\/chat\/conversations?\/([a-z0-9_-]+)/);
    if (m && m[1]) return { entity_type: 'chat_conversation', entity_id: m[1] };
    m = p.match(/\/clientes\/(\d+)\/solicitudes\/(\d+)/);
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
    return null;
  }

  function parseUrlEntity(pathname, search) {
    var params = new URLSearchParams(search || '');
    var conversationId = toText(params.get('conversation_id'));
    var candidataId = toText(params.get('candidata_id') || params.get('fila'));
    var solicitudId = toText(params.get('solicitud_id'));
    var clienteId = toText(params.get('cliente_id'));
    if (conversationId && String(pathname || '').toLowerCase().indexOf('/chat') >= 0) {
      return { entity_type: 'chat_conversation', entity_id: conversationId };
    }
    if (solicitudId) return { entity_type: 'solicitud', entity_id: solicitudId };
    if (candidataId) return { entity_type: 'candidata', entity_id: candidataId };
    if (clienteId) return { entity_type: 'cliente', entity_id: clienteId };
    return parsePathEntity(pathname);
  }

  function inferEntity() {
    var pathname = window.location.pathname || '';
    var search = window.location.search || '';

    var explicit = document.querySelector('[data-presence-entity-type][data-presence-entity-id]');
    if (explicit) {
      var t = normalizeEntityType(explicit.getAttribute('data-presence-entity-type'));
      var id = toText(explicit.getAttribute('data-presence-entity-id'));
      if (t && id) {
        return {
          entity_type: t,
          entity_id: id,
          entity_name: cleanName(explicit.getAttribute('data-presence-entity-name')),
          entity_code: toText(explicit.getAttribute('data-presence-entity-code')).slice(0, 64)
        };
      }
    }

    var fromBodyType = normalizeEntityType(body.getAttribute('data-presence-entity-type'));
    var fromBodyId = toText(body.getAttribute('data-presence-entity-id'));
    if (fromBodyType && fromBodyId) {
      return {
        entity_type: fromBodyType,
        entity_id: fromBodyId,
        entity_name: cleanName(body.getAttribute('data-presence-entity-name')),
        entity_code: toText(body.getAttribute('data-presence-entity-code')).slice(0, 64)
      };
    }

    var fromUrl = parseUrlEntity(pathname, search) || { entity_type: '', entity_id: '' };
    var entityType = normalizeEntityType(fromUrl.entity_type);
    var entityId = toText(fromUrl.entity_id);

    var entityName = '';
    var entityCode = '';
    if (entityType === 'candidata') {
      entityName = cleanName(
        (document.querySelector('input[name="nombre"]') || {}).value ||
        (document.querySelector('h1') || {}).textContent
      );
      entityCode = toText((document.getElementById('codigo') || {}).value).slice(0, 64);
    } else if (entityType === 'cliente') {
      entityName = cleanName((document.querySelector('h1') || {}).textContent);
      entityCode = toText((document.querySelector('.text-info') || {}).textContent).slice(0, 64);
    } else if (entityType === 'solicitud') {
      var h = toText((document.querySelector('h1') || {}).textContent);
      var m = h.match(/([A-Z]{2,5}-\d+[A-Z]?)/);
      entityCode = toText(m ? m[1] : '').slice(0, 64);
      entityName = cleanName(h);
    }

    return {
      entity_type: entityType,
      entity_id: entityId,
      entity_name: entityName,
      entity_code: entityCode
    };
  }

  function inferRouteLabel(pathname) {
    var p = String(pathname || '').toLowerCase();
    if (p.indexOf('/admin/chat') >= 0) return 'Chat soporte';
    if (p.indexOf('/admin/matching') >= 0) return 'Matching';
    if (p.indexOf('/admin/solicitudes') >= 0) return 'Solicitudes';
    if (p.indexOf('/admin/clientes') >= 0) return 'Clientes';
    if (p.indexOf('/admin/monitoreo') >= 0) return 'Control Room';
    if (p.indexOf('/buscar') >= 0) return 'Buscar / Editar candidata';
    if (p.indexOf('/entrevista') >= 0) return 'Entrevistas';
    if (p.indexOf('/referencias') >= 0) return 'Referencias';
    return 'App';
  }

  function inferAction(pathname, entityType) {
    var p = String(pathname || '').toLowerCase();
    if (state.is_typing) return 'typing';
    if (p.indexOf('/admin/chat') >= 0 && entityType === 'chat_conversation') return 'chatting';
    if (p.indexOf('/admin/chat') >= 0) return 'chatting';
    if (p.indexOf('matching') >= 0) return 'matching';
    if (p.indexOf('entrevista') >= 0) return 'editing_interview';
    if (p.indexOf('referencia') >= 0) return 'editing_references';
    if (p.indexOf('solicitud') >= 0 && state.has_unsaved_changes) return 'editing_request';
    if (p.indexOf('solicitud') >= 0) return 'viewing_request';
    if (p.indexOf('/clientes/') >= 0 && state.has_unsaved_changes) return 'editing_client';
    if (p.indexOf('/clientes/') >= 0) return 'viewing_client';
    if (p.indexOf('/buscar') >= 0 && entityType === 'candidata' && state.has_unsaved_changes) return 'editing_candidate';
    if (p.indexOf('/buscar') >= 0) return 'searching';
    if (state.has_unsaved_changes) return 'editing';
    return 'viewing';
  }

  function actionLabel(action, entity) {
    var et = toText(entity.entity_type);
    var name = cleanName(entity.entity_name);
    var code = toText(entity.entity_code);
    var entityDisplay = '';
    if (et) {
      var prefix = et === 'candidata' ? 'candidata' : (et === 'solicitud' ? 'solicitud' : (et === 'cliente' ? 'cliente' : et));
      if (name && code) entityDisplay = prefix + ' ' + name + ' (' + code + ')';
      else if (name) entityDisplay = prefix + ' ' + name;
      else if (code) entityDisplay = prefix + ' ' + code;
      else if (entity.entity_id) entityDisplay = prefix + ' ' + entity.entity_id;
    }

    if (action === 'typing') return entityDisplay ? ('Escribiendo en ' + entityDisplay) : 'Escribiendo';
    if (action === 'chatting') {
      if (et === 'chat_conversation' && entity.entity_id) return 'En chat #' + toText(entity.entity_id);
      return 'En chat de soporte';
    }
    if (action === 'searching') return 'Buscando';
    if (action === 'matching') return entityDisplay ? ('Trabajando en matching de ' + entityDisplay) : 'Trabajando en matching';
    if (action === 'editing_request') return entityDisplay ? ('Editando ' + entityDisplay) : 'Editando solicitud';
    if (action === 'editing_candidate') return entityDisplay ? ('Editando ' + entityDisplay) : 'Editando candidata';
    if (action === 'editing_client') return entityDisplay ? ('Editando ' + entityDisplay) : 'Editando cliente';
    if (action === 'editing_interview') return entityDisplay ? ('Editando entrevista de ' + entityDisplay) : 'Editando entrevista';
    if (action === 'editing_references') return entityDisplay ? ('Editando referencias de ' + entityDisplay) : 'Editando referencias';
    if (action === 'viewing_request') return entityDisplay ? ('Viendo ' + entityDisplay) : 'Viendo solicitud';
    if (action === 'viewing_client') return entityDisplay ? ('Viendo ' + entityDisplay) : 'Viendo cliente';
    if (action === 'editing') return entityDisplay ? ('Editando ' + entityDisplay) : 'Editando';
    return entityDisplay ? ('Viendo ' + entityDisplay) : 'Viendo';
  }

  function detectLockOwner() {
    var explicit = toText(body.getAttribute('data-lock-owner'));
    if (explicit) return explicit.slice(0, 120);

    var banner = document.querySelector('.alert.alert-warning');
    var txt = banner ? toText(banner.textContent) : '';
    if (!txt) return '';
    var m = txt.match(/solo lectura:\s*([^\.]+?)\s+est[aá]\s+editando/i);
    if (m && m[1]) return toText(m[1]).slice(0, 120);
    return '';
  }

  function isInputLike(target) {
    if (!target) return false;
    var tag = String(target.tagName || '').toLowerCase();
    if (tag === 'textarea') return true;
    if (tag !== 'input' && tag !== 'select') return Boolean(target.isContentEditable);
    var type = String(target.type || '').toLowerCase();
    return ['text', 'search', 'email', 'url', 'tel', 'number', 'password', 'date', 'time', 'datetime-local'].indexOf(type) >= 0;
  }

  function isSearchInput(target) {
    if (!target) return false;
    if (String(target.type || '').toLowerCase() === 'search') return true;
    var nm = String(target.name || '').toLowerCase();
    return nm.indexOf('search') >= 0 || nm === 'q' || nm.indexOf('query') >= 0;
  }

  function markInteraction() {
    state.last_interaction_at = nowIso();
    if (state.is_idle) {
      state.is_idle = false;
      queueImportantSend('resume');
    }
  }

  function isCurrentlyIdle() {
    var last = new Date(state.last_interaction_at);
    if (Number.isNaN(last.getTime())) return false;
    return (Date.now() - last.getTime()) >= IDLE_AFTER_MS;
  }

  function stableHash(payload) {
    var base = {
      session_id: payload.session_id,
      route: payload.route,
      route_label: payload.route_label,
      entity_type: payload.entity_type,
      entity_id: payload.entity_id,
      entity_name: payload.entity_name,
      entity_code: payload.entity_code,
      current_action: payload.current_action,
      action_label: payload.action_label,
      tab_visible: payload.tab_visible,
      is_idle: payload.is_idle,
      is_typing: payload.is_typing,
      has_unsaved_changes: payload.has_unsaved_changes,
      modal_open: payload.modal_open,
      lock_owner: payload.lock_owner
    };
    return JSON.stringify(base);
  }

  function readDynamicState() {
    var pathname = window.location.pathname || '';
    var search = window.location.search || '';

    state.route = (pathname + search).slice(0, 255);
    if (!state.route_label) state.route_label = inferRouteLabel(pathname);
    state.route_label = inferRouteLabel(pathname).slice(0, 120);

    var entity = inferEntity();
    state.entity_type = normalizeEntityType(entity.entity_type || '').slice(0, 40);
    state.entity_id = toText(entity.entity_id).slice(0, 64);
    state.entity_name = cleanName(entity.entity_name).slice(0, 160);
    state.entity_code = toText(entity.entity_code).slice(0, 64);

    state.tab_visible = document.visibilityState !== 'hidden';
    if (state.tab_visible === false) {
      state.is_idle = false;
    } else {
      state.is_idle = isCurrentlyIdle();
    }

    state.lock_owner = detectLockOwner();
    state.current_action = inferAction(pathname, state.entity_type).slice(0, 80);
    state.action_label = actionLabel(state.current_action, entity).slice(0, 120);
  }

  function buildPayload() {
    readDynamicState();
    var payload = {
      session_id: state.session_id,
      route: state.route,
      current_path: state.route,
      route_label: state.route_label,
      page_title: String(document.title || '').slice(0, 160),
      entity_type: state.entity_type,
      entity_id: state.entity_id,
      entity_name: state.entity_name,
      entity_code: state.entity_code,
      current_action: state.current_action,
      action_hint: state.current_action,
      action_label: state.action_label,
      tab_visible: Boolean(state.tab_visible),
      is_idle: Boolean(state.is_idle),
      is_typing: Boolean(state.is_typing),
      has_unsaved_changes: Boolean(state.has_unsaved_changes),
      modal_open: Boolean(state.modal_open),
      lock_owner: String(state.lock_owner || '').slice(0, 120),
      client_status: state.tab_visible ? (state.is_idle ? 'idle' : 'active') : 'hidden',
      last_interaction_at: state.last_interaction_at
    };
    payload.state_hash = stableHash(payload);
    return payload;
  }

  var csrfToken = getMeta('csrf-token');

  async function postPayload(payload) {
    var headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    return fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify(payload)
    });
  }

  async function sendSnapshot(reason, force) {
    if (paused) return;

    var payload = buildPayload();
    var hash = payload.state_hash;
    var nowMs = Date.now();
    var sinceLastSend = nowMs - lastSentAtMs;

    var changed = hash !== lastSentHash;
    var dueByHeartbeat = sinceLastSend >= HEARTBEAT_MS;

    if (!force && !changed && !dueByHeartbeat) return;
    if (force && !changed && sinceLastSend < MIN_IMMEDIATE_SEND_GAP_MS) return;

    payload.event_type = force ? 'snapshot_change' : 'heartbeat';
    payload.reason = String(reason || '').slice(0, 40);

    try {
      var resp = await postPayload(payload);
      if (!resp.ok) {
        var status = Number(resp.status || 0);
        if (status === 401 || status === 403) {
          paused = true;
        }
        if (status === 400) {
          try {
            var bodyJson = await resp.json();
            if (String((bodyJson || {}).error_code || '').toLowerCase() === 'csrf') {
              paused = true;
            }
          } catch (_) {}
        }
        return;
      }
      lastSentHash = hash;
      lastSentAtMs = nowMs;
    } catch (_) {
      // best-effort: heartbeat loop seguirá intentando.
    }
  }

  function queueImportantSend(reason) {
    pendingReason = reason || 'important_change';
    if (importantChangeTimer) clearTimeout(importantChangeTimer);
    importantChangeTimer = setTimeout(function () {
      sendSnapshot(pendingReason, true);
    }, IMPORTANT_CHANGE_DEBOUNCE_MS);
  }

  function handleUserActivity() {
    var nowMs = Date.now();
    if ((nowMs - lastActivityUpdateMs) < ACTIVITY_THROTTLE_MS) return;
    lastActivityUpdateMs = nowMs;
    markInteraction();
  }

  function startTypingPulse() {
    if (!state.is_typing) {
      state.is_typing = true;
      queueImportantSend('typing_start');
    }
    if (typingStopTimer) clearTimeout(typingStopTimer);
    typingStopTimer = setTimeout(function () {
      if (!state.is_typing) return;
      state.is_typing = false;
      queueImportantSend('typing_stop');
    }, TYPING_STOP_MS);
  }

  function updateDirtyState(target) {
    var form = target && target.closest ? target.closest('form') : null;
    if (!form) return;
    var before = dirtyForms.size > 0;
    dirtyForms.add(form);
    state.has_unsaved_changes = dirtyForms.size > 0;
    if (state.has_unsaved_changes !== before) {
      queueImportantSend(state.has_unsaved_changes ? 'dirty_on' : 'dirty_off');
    }
  }

  function clearDirtyState(form) {
    if (!form) return;
    var before = dirtyForms.size > 0;
    if (dirtyForms.has(form)) dirtyForms.delete(form);
    state.has_unsaved_changes = dirtyForms.size > 0;
    if ((dirtyForms.size > 0) !== before) {
      queueImportantSend(state.has_unsaved_changes ? 'dirty_on' : 'dirty_off');
    }
  }

  function installRouteWatch() {
    var originalPushState = history.pushState;
    var originalReplaceState = history.replaceState;
    lastKnownRouteKey = routeKeyFromUrlLike(window.location.href);

    function notifyRouteChange(urlLike) {
      var nextRouteKey = routeKeyFromUrlLike(urlLike);
      if (!nextRouteKey || nextRouteKey === lastKnownRouteKey) return;
      lastKnownRouteKey = nextRouteKey;
      markInteraction();
      queueImportantSend('route_change');
    }

    history.pushState = function () {
      originalPushState.apply(history, arguments);
      notifyRouteChange(arguments.length >= 3 ? arguments[2] : null);
    };

    history.replaceState = function () {
      originalReplaceState.apply(history, arguments);
      notifyRouteChange(arguments.length >= 3 ? arguments[2] : null);
    };

    window.addEventListener('popstate', function () {
      notifyRouteChange(window.location.href);
    });
  }

  function installActivityWatch() {
    ['mousemove', 'scroll', 'keydown', 'click', 'touchstart'].forEach(function (evt) {
      window.addEventListener(evt, handleUserActivity, { passive: true });
    });
  }

  function installFocusVisibilityWatch() {
    window.addEventListener('focus', function () {
      markInteraction();
      state.tab_visible = true;
      queueImportantSend('focus');
    });
    window.addEventListener('blur', function () {
      state.tab_visible = false;
      queueImportantSend('blur');
    });
    document.addEventListener('visibilitychange', function () {
      state.tab_visible = (document.visibilityState !== 'hidden');
      if (state.tab_visible) markInteraction();
      queueImportantSend(state.tab_visible ? 'tab_visible' : 'tab_hidden');
    });
  }

  function installFormWatch() {
    document.addEventListener('input', function (ev) {
      var target = ev.target;
      if (!target) return;
      markInteraction();
      if (isInputLike(target)) {
        startTypingPulse();
        updateDirtyState(target);
      }
      if (isSearchInput(target)) {
        state.current_action = 'searching';
        queueImportantSend('searching');
      }
    }, true);

    document.addEventListener('change', function (ev) {
      var target = ev.target;
      if (!target) return;
      markInteraction();
      updateDirtyState(target);
      queueImportantSend('form_change');
    }, true);

    document.addEventListener('submit', function (ev) {
      var form = ev.target;
      clearDirtyState(form);
      markInteraction();
      queueImportantSend('submit');
    }, true);

    document.addEventListener('reset', function (ev) {
      clearDirtyState(ev.target);
      markInteraction();
      queueImportantSend('reset');
    }, true);
  }

  function installModalWatch() {
    document.addEventListener('shown.bs.modal', function () {
      state.modal_open = true;
      markInteraction();
      queueImportantSend('modal_open');
    });

    document.addEventListener('hidden.bs.modal', function () {
      var openModals = document.querySelectorAll('.modal.show').length;
      state.modal_open = openModals > 0;
      markInteraction();
      queueImportantSend('modal_close');
    });
  }

  function installIdleWatcher() {
    idleCheckTimer = setInterval(function () {
      var wasIdle = Boolean(state.is_idle);
      var nowIdle = state.tab_visible && isCurrentlyIdle();
      if (wasIdle !== nowIdle) {
        state.is_idle = nowIdle;
        queueImportantSend(nowIdle ? 'idle' : 'resume');
      }
    }, IDLE_CHECK_MS);
  }

  function installEntityMutationWatch() {
    if (!window.MutationObserver) return;
    var root = document.body;
    if (!root) return;

    var observer = new MutationObserver(function () {
      var nowMs = Date.now();
      if ((nowMs - lastMutationSendMs) < MUTATION_MIN_GAP_MS) return;
      lastMutationSendMs = nowMs;
      queueImportantSend('context_change');
    });

    observer.observe(root, {
      attributes: true,
      attributeFilter: ['data-presence-entity-id', 'data-presence-entity-type', 'data-presence-entity-name', 'data-presence-entity-code']
    });
  }

  function startHeartbeatLoop() {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(function () {
      sendSnapshot('heartbeat', false);
    }, HEARTBEAT_MS);
  }

  var state = {
    session_id: getOrCreateSessionId(),
    route: '',
    route_label: '',
    entity_type: '',
    entity_id: '',
    entity_name: '',
    entity_code: '',
    current_action: 'viewing',
    action_label: 'Viendo',
    tab_visible: document.visibilityState !== 'hidden',
    is_idle: false,
    is_typing: false,
    has_unsaved_changes: false,
    modal_open: false,
    lock_owner: '',
    last_interaction_at: nowIso()
  };

  installRouteWatch();
  installActivityWatch();
  installFocusVisibilityWatch();
  installFormWatch();
  installModalWatch();
  installIdleWatcher();
  installEntityMutationWatch();
  startHeartbeatLoop();

  // Primer snapshot al iniciar.
  queueImportantSend('init');
})();
