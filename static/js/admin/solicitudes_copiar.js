document.addEventListener('DOMContentLoaded', () => {
  const actionPanel = document.getElementById('contextActionPanel');
  const panelTitle = document.getElementById('contextActionPanelTitle');
  const panelClose = document.getElementById('contextActionPanelClose');
  const cancelForm = document.getElementById('cancelModalSharedForm');
  const cancelNext = document.getElementById('cancelModalSharedNext');
  const cancelRowVersion = document.getElementById('cancelModalSharedRowVersion');
  const cancelIdem = document.getElementById('cancelModalSharedIdempotencyKey');
  const cancelTextarea = cancelForm ? cancelForm.querySelector('textarea[name="motivo"]') : null;
  const paidForm = document.getElementById('paidModalSharedForm');
  const paidNext = document.getElementById('paidModalSharedNext');
  const paidRowVersion = document.getElementById('paidModalSharedRowVersion');
  const paidIdem = document.getElementById('paidModalSharedIdempotencyKey');
  const paidCandidata = document.getElementById('paidModalSharedCandidata');
  const paidSearch = document.getElementById('paidModalSharedSearch');
  const paidSearchBtn = document.getElementById('paidModalSharedSearchBtn');
  const paidClearBtn = document.getElementById('paidModalSharedClearBtn');
  const paidSearchStats = document.getElementById('paidModalSharedSearchStats');
  const paidSelected = document.getElementById('paidModalSharedSelected');
  const inlineActionForm = document.getElementById('inlineActionSharedForm');
  const inlineActionNext = document.getElementById('inlineActionSharedNext');
  const pageBasePath = `${window.location.pathname}${window.location.search}`;
  const paidLookupUrl = paidForm ? (paidForm.dataset.lookupUrl || '') : '';
  let paidCurrentCandidateId = '';
  let paidLookupSeq = 0;
  let paidLookupController = null;
  let paidSearchTimer = 0;

  function newIdempotencyKey() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
      }
    } catch (_e) {}
    return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function clearUiLoaders() {
    try {
      if (window.AppLoader && typeof window.AppLoader.hideAll === 'function') {
        window.AppLoader.hideAll();
      } else if (window.AppLoader && typeof window.AppLoader.hide === 'function') {
        window.AppLoader.hide();
      }
    } catch (_) {}
    ['globalLoader', 'appGlobalLoader', 'loader', 'pageLoader', 'loadingOverlay', 'overlayLoader'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    document.documentElement.classList.remove('is-loading');
    if (document.body) document.body.classList.remove('is-loading');
  }

  function showActionToast(message, isError = false) {
    const text = String(message || '').trim();
    if (!text) return;
    if (window.AppToast && typeof window.AppToast.show === 'function') {
      window.AppToast.show(text, isError ? 'danger' : 'success');
    }
  }

  function clearModalFeedback(form) {
    if (!form) return;
    const box = form.querySelector('.js-modal-feedback');
    if (!box) return;
    box.classList.add('d-none');
    box.classList.remove('alert-success', 'alert-danger', 'alert-warning', 'alert-info');
    box.textContent = '';
  }

  function setModalFeedback(form, message, category) {
    if (!form) return;
    const box = form.querySelector('.js-modal-feedback');
    if (!box) return;
    const level = (category || '').toLowerCase();
    const cls = level === 'success' ? 'alert-success' : (level === 'warning' ? 'alert-warning' : (level === 'info' ? 'alert-info' : 'alert-danger'));
    box.classList.remove('d-none', 'alert-success', 'alert-danger', 'alert-warning', 'alert-info');
    box.classList.add(cls);
    box.textContent = message || 'No se pudo completar la acción.';
  }

  function hasSolicitudCards() {
    return document.querySelectorAll('#copiarSolicitudesResults li[id^="sol-"]').length > 0;
  }

  function getCsrfTokenFromForm(form) {
    if (!form) return '';
    const input = form.querySelector('input[name="csrf_token"]');
    return input ? (input.value || '') : '';
  }

  async function refreshCopiarResults(url) {
    const targetUrl = (url || pageBasePath || '').trim();
    const adminAsync = window.AdminAsync;
    if (!targetUrl || !adminAsync || typeof adminAsync.request !== 'function') return false;
    const scope = document.getElementById('copiarSolicitudesScope') || document.body;
    const result = await adminAsync.request({
      url: targetUrl,
      method: 'GET',
      body: null,
      sourceEl: scope,
      busyContainer: scope,
      updateTarget: '#copiarSolicitudesResults',
      noLoader: true,
      headers: { 'X-CSRFToken': getCsrfTokenFromForm(cancelForm || paidForm || inlineActionForm) },
      preserveScroll: true,
    });
    return result === true;
  }

  function closeActionPanel() {
    if (!actionPanel) return;
    actionPanel.classList.add('d-none');
    actionPanel.dataset.solicitudId = '';
    actionPanel.dataset.mode = '';
    if (cancelForm) cancelForm.classList.add('d-none');
    if (paidForm) paidForm.classList.add('d-none');
  }

  function openActionPanelForButton(btn, onOpen) {
    const doOpen = () => {
      const solId = btn.dataset.solicitudId || '';
      const row = document.getElementById(`sol-${solId}`) || btn.closest('li[id^="sol-"]');
      if (!row || !actionPanel) return;
      row.appendChild(actionPanel);
      actionPanel.classList.remove('d-none');
      actionPanel.dataset.solicitudId = solId;
      onOpen();
    };

    const ddRoot = btn.closest('.dropdown');
    const ddToggle = ddRoot ? ddRoot.querySelector('[data-bs-toggle="dropdown"]') : null;
    if (window.bootstrap && ddRoot && ddToggle) {
      const dd = bootstrap.Dropdown.getOrCreateInstance(ddToggle);
      let opened = false;
      const onHidden = () => {
        if (opened) return;
        opened = true;
        doOpen();
      };
      ddRoot.addEventListener('hidden.bs.dropdown', onHidden, { once: true });
      dd.hide();
      setTimeout(onHidden, 180);
      return;
    }
    doOpen();
  }

  function renderPaidOptions(items, selectedValue = '') {
    if (!paidCandidata) return;
    const frag = document.createDocumentFragment();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Seleccionar…';
    frag.appendChild(placeholder);
    items.forEach((item) => {
      const opt = document.createElement('option');
      opt.value = item.value;
      opt.textContent = item.text;
      frag.appendChild(opt);
    });
    paidCandidata.innerHTML = '';
    paidCandidata.appendChild(frag);
    if (selectedValue && items.some((item) => item.value === String(selectedValue))) {
      paidCandidata.value = String(selectedValue);
    } else {
      paidCandidata.value = '';
    }
  }

  function updateSelectedCandidataText() {
    if (!paidSelected || !paidCandidata) return;
    const idx = paidCandidata.selectedIndex;
    const selectedText = (idx >= 0 && paidCandidata.options[idx]) ? paidCandidata.options[idx].textContent.trim() : '';
    if (!paidCandidata.value) {
      paidSelected.textContent = 'Seleccionada: ninguna';
      return;
    }
    paidSelected.textContent = `Seleccionada: ${selectedText}`;
  }

  function setPaidStats(message) {
    if (!paidSearchStats) return;
    paidSearchStats.textContent = message;
  }

  async function lookupPaidCandidates(showResultText = false, autoSelectFirst = false, includeCurrent = false) {
    if (!paidCandidata || !paidLookupUrl || !window.fetch) return;
    const q = ((paidSearch && paidSearch.value) || '').trim();
    const includeId = includeCurrent ? paidCurrentCandidateId : '';
    const seq = ++paidLookupSeq;
    setPaidStats('Buscando candidatas...');
    if (paidLookupController && typeof paidLookupController.abort === 'function') {
      paidLookupController.abort();
    }
    paidLookupController = (window.AbortController ? new AbortController() : null);
    try {
      const qs = new URLSearchParams();
      qs.set('q', q);
      qs.set('limit', '50');
      if (includeId) qs.set('include_id', includeId);
      const resp = await fetch(`${paidLookupUrl}?${qs.toString()}`, {
        method: 'GET',
        credentials: 'same-origin',
        signal: paidLookupController ? paidLookupController.signal : undefined,
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        }
      });
      if (seq !== paidLookupSeq) return;
      const contentType = (resp.headers.get('content-type') || '').toLowerCase();
      if (!resp.ok || !contentType.includes('application/json')) {
        setPaidStats('No se pudo consultar candidatas. Intenta de nuevo.');
        return;
      }
      const data = await resp.json();
      if (!data || !data.ok) {
        setPaidStats('No se pudo consultar candidatas. Intenta de nuevo.');
        return;
      }
      const items = Array.isArray(data.items) ? data.items : [];
      const previous = paidCandidata.value || paidCurrentCandidateId || '';
      renderPaidOptions(items, previous);
      if (autoSelectFirst && !paidCandidata.value && items.length > 0) {
        paidCandidata.value = items[0].value;
      }
      if (!q) {
        if (includeId && items.length === 1) {
          setPaidStats('Seleccionada candidata actual. Escribe para buscar otra.');
        } else {
          setPaidStats('Escribe nombre o ID y pulsa Buscar.');
        }
      } else if (items.length === 0) {
        setPaidStats('No hay coincidencias con esa búsqueda.');
      } else if (showResultText) {
        setPaidStats(`Búsqueda aplicada: ${items.length} coincidencia(s).`);
      } else {
        setPaidStats(`${items.length} coincidencia(s) encontradas.`);
      }
      updateSelectedCandidataText();
    } catch (_err) {
      if (_err && _err.name === 'AbortError') return;
      if (seq !== paidLookupSeq) return;
      setPaidStats('No se pudo consultar candidatas. Intenta de nuevo.');
    } finally {
      clearUiLoaders();
    }
  }

  function removeRowWithFade(node) {
    if (!node) return false;
    node.style.transition = 'opacity 150ms ease, transform 150ms ease';
    node.style.opacity = '0';
    node.style.transform = 'translateY(-4px)';
    window.setTimeout(() => {
      try { node.remove(); } catch (_e) {}
    }, 160);
    return true;
  }

  function removeElementBySelector(selector) {
    const sel = String(selector || '').trim();
    if (!sel) return false;
    const node = document.querySelector(sel);
    if (!node) return false;
    return removeRowWithFade(node);
  }

  function extractSolicitudIdFromAction(actionUrl) {
    const txt = String(actionUrl || '');
    const m = txt.match(/\/solicitudes\/(\d+)\//);
    return (m && m[1]) ? String(m[1]) : '';
  }

  function removeSolicitudCardFromPayload(payload, fallbackForm) {
    const removeSel = payload && payload.remove_element ? String(payload.remove_element || '') : '';
    if (removeSel && removeElementBySelector(removeSel)) return true;

    const shouldRemove = !!(payload && payload.remove_card);
    if (!shouldRemove) return false;

    const idFromExtra = (payload && payload.solicitud_id) ? String(payload.solicitud_id) : '';
    const idFromFormAction = extractSolicitudIdFromAction(fallbackForm ? fallbackForm.action : '');
    const id = idFromExtra || idFromFormAction || (actionPanel ? String(actionPanel.dataset.solicitudId || '') : '');
    if (!id) return false;
    return removeElementBySelector(`#sol-${id}`);
  }

  async function submitActionWithFetch(form, submitter) {
    if (!form || !window.fetch) return { handled: false, ok: false, payload: null };
    const data = new FormData(form);
    if (!data.has('_async_target')) data.append('_async_target', '#copiarSolicitudesResults');
    try {
      if (submitter && submitter.tagName === 'BUTTON') {
        submitter.disabled = true;
      }
      const resp = await fetch(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        body: data,
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          'X-Admin-Async': '1',
          'X-CSRFToken': getCsrfTokenFromForm(form),
        },
      });
      const contentType = (resp.headers.get('content-type') || '').toLowerCase();
      if (!contentType.includes('application/json')) {
        return { handled: true, ok: false, payload: null, status: resp.status };
      }
      const payload = await resp.json();
      const ok = Boolean(payload && (payload.success === true || payload.ok === true));
      if (submitter && submitter.tagName === 'BUTTON') submitter.disabled = false;
      return { handled: true, ok, payload, status: resp.status };
    } catch (_err) {
      if (submitter && submitter.tagName === 'BUTTON') submitter.disabled = false;
      return { handled: true, ok: false, payload: null, status: 0 };
    }
  }

  async function submitModalAjax(form, submitter) {
    if (!form) return false;
    clearUiLoaders();
    clearModalFeedback(form);
    const result = await submitActionWithFetch(form, submitter);
    if (result.ok) {
      removeSolicitudCardFromPayload(result.payload, form);
      closeActionPanel();
      if (!hasSolicitudCards()) {
        setTimeout(() => refreshCopiarResults(pageBasePath), 220);
      }
      return true;
    }
    if (result.handled) {
      const payload = result.payload || {};
      const fallback = 'No se pudo completar la acción.';
      const msg = (Array.isArray(payload.errors) && payload.errors.length > 0)
        ? payload.errors.join(' ')
        : (payload.message || fallback);
      const category = payload.category || 'danger';
      setModalFeedback(form, msg, category);
      return true;
    }
    clearUiLoaders();
    return false;
  }

  async function submitInlineActionAjax(form, submitter) {
    if (!form) return { handled: false, ok: false };
    clearUiLoaders();
    const result = await submitActionWithFetch(form, submitter);
    if (result.ok) {
      removeSolicitudCardFromPayload(result.payload, form);
      if (result.payload && result.payload.message) {
        showActionToast(result.payload.message, false);
      }
      if (!hasSolicitudCards()) {
        setTimeout(() => refreshCopiarResults(pageBasePath), 220);
      }
      return { handled: true, ok: true };
    }
    if (result.handled) {
      const payload = result.payload || {};
      const msg = (Array.isArray(payload.errors) && payload.errors.length > 0)
        ? payload.errors.join(' ')
        : (payload.message || 'No se pudo completar la acción.');
      showActionToast(msg, true);
      return { handled: true, ok: false };
    }
    clearUiLoaders();
    return { handled: false, ok: false };
  }

  function fallbackCopy(text) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (_e) {
      return false;
    }
  }

  function runInlineSharedAction(btn, submitter) {
    if (!inlineActionForm || !btn) return Promise.resolve({ handled: false, ok: false });
    const action = (btn.dataset.action || '').trim();
    if (!action) return Promise.resolve({ handled: false, ok: false });
    inlineActionForm.action = action;
    if (inlineActionNext) {
      const solId = String(btn.dataset.solicitudId || '').trim();
      inlineActionNext.value = solId ? `${pageBasePath}#sol-${solId}` : pageBasePath;
    }
    return submitInlineActionAjax(inlineActionForm, submitter || btn);
  }

  const orderTextCache = new Map();

  async function fetchOrderTextForButton(btn) {
    const solicitudId = String(btn?.dataset?.solicitudId || '').trim();
    if (solicitudId && orderTextCache.has(solicitudId)) {
      return String(orderTextCache.get(solicitudId) || '');
    }
    const textUrl = String(btn?.dataset?.textUrl || '').trim();
    if (!textUrl || !window.fetch) return '';
    const resp = await fetch(textUrl, {
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
    });
    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    if (!resp.ok || !contentType.includes('application/json')) {
      throw new Error('text_fetch_failed');
    }
    const payload = await resp.json();
    const text = String((payload && payload.order_text) || '');
    if (solicitudId) orderTextCache.set(solicitudId, text);
    return text;
  }

  function bindOnce(el, key, handler) {
    if (!el) return;
    const attr = `bound${key}`;
    if (el.dataset[attr] === '1') return;
    el.dataset[attr] = '1';
    el.addEventListener('click', handler);
  }

  function bindCopyButtons(root = document) {
    root.querySelectorAll('.copy-btn').forEach((btn) => {
      bindOnce(btn, 'Copy', async () => {
        if (btn.dataset.actionBusy === '1') return;
        btn.dataset.actionBusy = '1';
        let text = '';
        try {
          text = await fetchOrderTextForButton(btn);
        } catch (_err) {
          showActionToast('No se pudo cargar el texto para copiar.', true);
          btn.dataset.actionBusy = '';
          return;
        }
        if (!text.trim()) {
          showActionToast('No hay texto disponible para copiar.', true);
          btn.dataset.actionBusy = '';
          return;
        }

        let copied = false;
        if (navigator.clipboard && window.isSecureContext) {
          try {
            await navigator.clipboard.writeText(text);
            copied = true;
          } catch (_e) {
            copied = fallbackCopy(text);
          }
        } else {
          copied = fallbackCopy(text);
        }

        if (!copied) {
          showActionToast('No se pudo copiar automáticamente. Intenta de nuevo.', true);
          btn.dataset.actionBusy = '';
          return;
        }

        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i> Guardando...';

        btn.dataset.action = btn.dataset.copyAction || '';
        const action = await runInlineSharedAction(btn, btn);
        if (action.handled && action.ok) return;

        btn.disabled = false;
        btn.innerHTML = originalHtml;
        btn.dataset.actionBusy = '';
      });
    });
  }

  function bindInlineActionButtons(root = document) {
    root.querySelectorAll('.js-inline-async-action-btn').forEach((btn) => {
      bindOnce(btn, 'InlineAction', async (ev) => {
        ev.preventDefault();
        if (btn.dataset.actionBusy === '1') return;
        btn.dataset.actionBusy = '1';
        btn.disabled = true;
        const action = await runInlineSharedAction(btn, btn);
        btn.disabled = false;
        btn.dataset.actionBusy = '';
        if (action.handled) return;
      });
    });
  }

  function bindOpenCancelButtons(root = document) {
    root.querySelectorAll('.js-open-cancel').forEach((btn) => {
      bindOnce(btn, 'OpenCancel', (ev) => {
        ev.preventDefault();
        if (!cancelForm || !actionPanel) return;
        const code = btn.dataset.solicitudCodigo || ('SOL-' + (btn.dataset.solicitudId || ''));
        cancelForm.action = btn.dataset.action || '';
        if (cancelNext) cancelNext.value = btn.dataset.next || pageBasePath;
        if (cancelRowVersion) cancelRowVersion.value = (btn.dataset.rowVersion || '').trim();
        if (cancelIdem) cancelIdem.value = newIdempotencyKey();
        if (panelTitle) panelTitle.textContent = `Cancelar ${code}`;
        actionPanel.dataset.mode = 'cancel';
        actionPanel.dataset.solicitudId = btn.dataset.solicitudId || '';
        if (paidForm) paidForm.classList.add('d-none');
        cancelForm.classList.remove('d-none');
        clearModalFeedback(cancelForm);
        if (cancelTextarea) {
          cancelTextarea.readOnly = false;
          cancelTextarea.disabled = false;
        }
        openActionPanelForButton(btn, () => {
          if (cancelTextarea) cancelTextarea.focus();
        });
      });
    });
  }

  function bindOpenPaidButtons(root = document) {
    root.querySelectorAll('.js-open-paid').forEach((btn) => {
      bindOnce(btn, 'OpenPaid', (ev) => {
        ev.preventDefault();
        if (!paidForm || !actionPanel) return;
        const code = btn.dataset.solicitudCodigo || ('SOL-' + (btn.dataset.solicitudId || ''));
        paidForm.action = btn.dataset.action || '';
        if (paidNext) paidNext.value = btn.dataset.next || pageBasePath;
        if (paidRowVersion) paidRowVersion.value = (btn.dataset.rowVersion || '').trim();
        if (paidIdem) paidIdem.value = newIdempotencyKey();
        if (panelTitle) panelTitle.textContent = `Marcar pagado ${code}`;
        actionPanel.dataset.mode = 'paid';
        actionPanel.dataset.solicitudId = btn.dataset.solicitudId || '';
        if (cancelForm) cancelForm.classList.add('d-none');
        paidForm.classList.remove('d-none');
        paidCurrentCandidateId = (btn.dataset.currentCandidataId || '').toString();
        if (paidSearch) paidSearch.value = '';
        renderPaidOptions([]);
        setPaidStats('Escribe nombre o ID y pulsa Buscar.');
        lookupPaidCandidates(false, false, true);
        clearModalFeedback(paidForm);
        openActionPanelForButton(btn, () => {
          if (paidSearch) paidSearch.focus();
        });
      });
    });
  }

  function bindDynamicHandlers(root = document) {
    bindCopyButtons(root);
    bindInlineActionButtons(root);
    bindOpenCancelButtons(root);
    bindOpenPaidButtons(root);
  }

  bindDynamicHandlers(document);

  if (cancelForm) {
    cancelForm.addEventListener('submit', async (ev) => {
      if (cancelForm.dataset.asyncBypass === '1') {
        cancelForm.dataset.asyncBypass = '';
        return;
      }
      ev.preventDefault();
      await submitModalAjax(cancelForm, ev.submitter || cancelForm.querySelector('button[type="submit"]'));
    });
  }

  if (paidForm) {
    paidForm.addEventListener('submit', async (ev) => {
      if (paidForm.dataset.asyncBypass === '1') {
        paidForm.dataset.asyncBypass = '';
        return;
      }
      ev.preventDefault();
      await submitModalAjax(paidForm, ev.submitter || paidForm.querySelector('button[type="submit"]'));
    });
  }

  if (paidSearch) {
    paidSearch.addEventListener('input', () => {
      if (paidSearchTimer) window.clearTimeout(paidSearchTimer);
      paidSearchTimer = window.setTimeout(() => {
        const hasText = !!paidSearch.value.trim();
        lookupPaidCandidates(false, false, !hasText);
      }, 300);
    });
    paidSearch.addEventListener('search', () => {
      if (paidSearchTimer) window.clearTimeout(paidSearchTimer);
      if (!paidSearch.value.trim()) {
        lookupPaidCandidates(false, false, true);
        return;
      }
      lookupPaidCandidates(false, false, false);
    });
    paidSearch.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      lookupPaidCandidates(true, true, false);
    });
  }

  if (paidSearchBtn) {
    paidSearchBtn.addEventListener('click', () => lookupPaidCandidates(true, true, false));
  }

  if (paidClearBtn) {
    paidClearBtn.addEventListener('click', () => {
      if (paidSearch) paidSearch.value = '';
      lookupPaidCandidates(false, false, true);
      if (paidSearch) paidSearch.focus();
    });
  }

  if (paidCandidata) {
    paidCandidata.addEventListener('change', updateSelectedCandidataText);
    updateSelectedCandidataText();
  }

  if (panelClose) {
    panelClose.addEventListener('click', closeActionPanel);
  }
  document.querySelectorAll('.js-panel-close').forEach((btn) => {
    btn.addEventListener('click', closeActionPanel);
  });

  document.addEventListener('admin:content-updated', (ev) => {
    const detail = ev.detail || {};
    if (detail.targetSelector !== '#copiarSolicitudesResults') return;
    bindDynamicHandlers(detail.container || document);
    clearUiLoaders();
  });
});
