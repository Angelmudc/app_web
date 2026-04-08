(function () {
  let activeController = null;

  function createController() {
    const body = document.body;
    if (!body) return null;

    const lockUrl = String(body.getAttribute('data-entity-lock-url') || '');
    const takeoverUrl = String(body.getAttribute('data-entity-lock-takeover-url') || '');
    const role = String(body.getAttribute('data-staff-role') || '').toLowerCase();
    if (!lockUrl) return null;

    const path = window.location.pathname || '';
    const params = new URLSearchParams(window.location.search || '');

    function detectEntity() {
      const candidataId = params.get('candidata_id') || params.get('fila') || '';
      const solicitudId = params.get('solicitud_id') || '';

      const mSolEdit = path.match(/\/clientes\/\d+\/solicitudes\/(\d+)\/editar/);
      if (mSolEdit && mSolEdit[1]) {
        return { entity_type: 'solicitud', entity_id: String(mSolEdit[1]) };
      }

      if (solicitudId) {
        return { entity_type: 'solicitud', entity_id: String(solicitudId) };
      }
      if (candidataId) {
        return { entity_type: 'candidata', entity_id: String(candidataId) };
      }

      if (path.indexOf('/entrevista') >= 0 || path.indexOf('/referencias') >= 0 || path.indexOf('/buscar') >= 0) {
        const hiddenCandidate = document.querySelector('input[name="candidata_id"]');
        if (hiddenCandidate && hiddenCandidate.value) {
          return { entity_type: 'candidata', entity_id: String(hiddenCandidate.value) };
        }
      }

      return null;
    }

    const entity = detectEntity();
    if (!entity || !entity.entity_id) return null;

    const forms = Array.from(document.querySelectorAll('form'));
    if (!forms.length) return null;

    let state = 'owner';
    let banner = null;
    let pingInterval = null;

    function csrfToken() {
      const meta = document.querySelector('meta[name="csrf-token"]');
      return meta ? String(meta.content || '') : '';
    }

    function setFormsReadonly(readonly) {
      forms.forEach((form) => {
        const fields = form.querySelectorAll('input, textarea, select, button');
        fields.forEach((el) => {
          if (el.type === 'hidden' || el.hasAttribute('data-lock-ignore')) return;
          if (el.tagName === 'BUTTON' || el.type === 'submit') {
            el.disabled = !!readonly;
          } else if (readonly) {
            el.setAttribute('readonly', 'readonly');
            el.setAttribute('disabled', 'disabled');
          } else {
            el.removeAttribute('readonly');
            el.removeAttribute('disabled');
          }
        });
      });
    }

    function renderBanner(message, canTakeover) {
      if (!banner) {
        banner = document.createElement('div');
        banner.className = 'alert alert-warning border-0 m-3';
        banner.style.position = 'sticky';
        banner.style.top = '8px';
        banner.style.zIndex = '1040';
        const main = document.querySelector('main.container') || document.body;
        main.insertBefore(banner, main.firstChild);
      }

      const takeoverBtn = (canTakeover && takeoverUrl)
        ? '<button type="button" class="btn btn-sm btn-dark ms-2" id="lockTakeoverBtn">Tomar control</button>'
        : '';

      banner.innerHTML = '<strong>Solo lectura.</strong> ' + message + takeoverBtn;

      const btn = document.getElementById('lockTakeoverBtn');
      if (btn) {
        btn.addEventListener('click', async function () {
          const reason = window.prompt('Motivo para tomar control de la edición:') || '';
          try {
            const headers = { 'Content-Type': 'application/json' };
            const csrf = csrfToken();
            if (csrf) headers['X-CSRFToken'] = csrf;
            const resp = await fetch(takeoverUrl, {
              method: 'POST',
              credentials: 'same-origin',
              headers,
              body: JSON.stringify({
                entity_type: entity.entity_type,
                entity_id: entity.entity_id,
                reason: reason,
              }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            state = 'owner';
            if (banner) banner.remove();
            banner = null;
            setFormsReadonly(false);
          } catch (_) {
            window.alert('No se pudo tomar control en este momento.');
          }
        });
      }
    }

    async function pingLock() {
      try {
        const headers = { 'Content-Type': 'application/json' };
        const csrf = csrfToken();
        if (csrf) headers['X-CSRFToken'] = csrf;
        const resp = await fetch(lockUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers,
          body: JSON.stringify({
            entity_type: entity.entity_type,
            entity_id: entity.entity_id,
            current_path: window.location.pathname + window.location.search,
          }),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        if (!data || !data.ok) {
          return;
        }
        if (data.state === 'readonly') {
          state = 'readonly';
          setFormsReadonly(true);
          renderBanner(data.message || 'Otra usuaria está editando esta información.', !!data.can_takeover && role === 'admin');
        } else {
          state = 'owner';
          setFormsReadonly(false);
          if (banner) {
            banner.remove();
            banner = null;
          }
        }
      } catch (_) {
        if (!window.__lockWarned) {
          window.__lockWarned = true;
          console.warn('No se pudo validar lock de edición. Se mantiene flujo normal para no bloquear guardado.');
        }
      }
    }

    function start() {
      if (pingInterval) {
        window.clearInterval(pingInterval);
      }
      pingInterval = window.setInterval(pingLock, 15000);
      pingLock();
    }

    function cleanup() {
      if (pingInterval) {
        window.clearInterval(pingInterval);
        pingInterval = null;
      }
      if (banner) {
        banner.remove();
        banner = null;
      }
      setFormsReadonly(false);
    }

    return {
      start,
      cleanup,
    };
  }

  function reinitialize() {
    if (activeController && typeof activeController.cleanup === 'function') {
      activeController.cleanup();
    }
    activeController = createController();
    if (!activeController || typeof activeController.start !== 'function') return;
    activeController.start();
  }

  document.addEventListener('admin:navigation-complete', reinitialize);
  window.addEventListener('beforeunload', function () {
    if (activeController && typeof activeController.cleanup === 'function') {
      activeController.cleanup();
    }
  });

  reinitialize();
})();
