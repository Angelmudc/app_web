(function () {
  var body = document.body;
  var token = body ? body.getAttribute('data-store-token') : '';
  var toastRoot = document.getElementById('ps-toast-root');
  var cart = document.querySelector('.ps-cart-cta');
  var cartCountEl = document.querySelector('.ps-cart-count');
  var cartValueEl = document.querySelector('.ps-cart-value');
  var bottomBar = document.querySelector('.ps-mobile-bottom-bar');
  var bottomSelectionEl = document.querySelector('[data-bottom-selection]');
  var knownAvailableIds = null;

  var drawer = document.querySelector('[data-filter-drawer]');
  var drawerOverlay = document.querySelector('[data-filter-overlay]');
  var openButtons = document.querySelectorAll('[data-filter-open]');
  var closeButtons = document.querySelectorAll('[data-filter-close]');

  function showToast(type, message) {
    if (!toastRoot || !message) return;
    var el = document.createElement('div');
    el.className = 'ps-toast ' + (type || 'info');
    el.textContent = message;
    toastRoot.appendChild(el);
    window.setTimeout(function () { el.remove(); }, 2600);
  }

  function toInt(v) {
    var n = parseInt(v, 10);
    return isNaN(n) ? 0 : n;
  }

  function setCartCount(count) {
    var safe = toInt(count);
    if (cartCountEl) cartCountEl.textContent = String(safe);
    if (cartValueEl) cartValueEl.textContent = 'Mi selección (' + safe + ')';
    if (bottomSelectionEl) bottomSelectionEl.textContent = 'Selección (' + safe + ')';
    if (cart) {
      cart.setAttribute('data-selection-count', String(safe));
      cart.classList.remove('is-bump');
      void cart.offsetWidth;
      cart.classList.add('is-bump');
    }
    if (bottomBar) {
      bottomBar.setAttribute('data-selection-count', String(safe));
      bottomBar.classList.remove('is-bump');
      void bottomBar.offsetWidth;
      bottomBar.classList.add('is-bump');
    }
  }

  function updateSelectedButtons(selectedIds) {
    if (!Array.isArray(selectedIds)) return;
    var map = {};
    selectedIds.forEach(function (x) { map[String(x)] = true; });

    var addButtons = document.querySelectorAll('[data-add-button]');
    addButtons.forEach(function (btn) {
      var id = btn.getAttribute('data-add-button');
      if (map[id]) {
        btn.textContent = 'Ya en selección';
        btn.disabled = true;
        btn.classList.remove('ps-btn-add');
        btn.classList.add('ps-btn-selected');
      }
    });

    var removeForms = document.querySelectorAll('form[data-store-action="remove"]');
    removeForms.forEach(function (form) {
      var id = form.getAttribute('data-candidata-id');
      if (!map[String(id)]) {
        var row = form.closest('.ps-item-row');
        if (row) row.remove();
      }
    });
  }

  function requestJSON(url, formData) {
    var csrf = document.querySelector('meta[name="csrf-token"]');
    return fetch(url, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json',
        'X-CSRFToken': csrf ? csrf.getAttribute('content') : ''
      },
      body: formData,
      credentials: 'same-origin'
    });
  }

  function submitFallback(form) {
    if (form.dataset.psBypass === '1') return;
    form.dataset.psBypass = '1';
    form.submit();
  }

  function setDrawer(open) {
    if (!drawer || !drawerOverlay) return;
    if (open) {
      drawer.classList.add('is-open');
      drawerOverlay.classList.add('is-open');
      drawerOverlay.hidden = false;
    } else {
      drawer.classList.remove('is-open');
      drawerOverlay.classList.remove('is-open');
      window.setTimeout(function () { drawerOverlay.hidden = true; }, 180);
    }
    openButtons.forEach(function (btn) { btn.setAttribute('aria-expanded', open ? 'true' : 'false'); });
  }

  function bindDrawer() {
    if (!drawer || !drawerOverlay || openButtons.length === 0) return;
    drawerOverlay.hidden = true;

    openButtons.forEach(function (btn) {
      btn.addEventListener('click', function () { setDrawer(true); });
    });
    closeButtons.forEach(function (btn) {
      btn.addEventListener('click', function () { setDrawer(false); });
    });
    drawerOverlay.addEventListener('click', function () { setDrawer(false); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') setDrawer(false);
    });
  }

  function bindCheckoutSubmitUX() {
    var form = document.getElementById('ps-checkout-form');
    if (!form) return;
    var desktopBtn = form.querySelector('[data-checkout-submit]');
    var mobileBtn = document.querySelector('[data-checkout-submit-mobile]');
    var lock = false;

    form.addEventListener('submit', function () {
      if (lock) return;
      lock = true;
      if (desktopBtn) {
        desktopBtn.disabled = true;
        desktopBtn.textContent = 'Enviando solicitud...';
      }
      if (mobileBtn) {
        mobileBtn.disabled = true;
        mobileBtn.textContent = 'Enviando solicitud...';
      }
    });
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var action = form.getAttribute('data-store-action');
    if (!action || form.dataset.psBypass === '1') return;

    event.preventDefault();
    requestJSON(form.action, new FormData(form))
      .then(function (resp) {
        var isJSON = (resp.headers.get('content-type') || '').indexOf('application/json') >= 0;
        if (!isJSON) {
          submitFallback(form);
          return null;
        }
        return resp.json().then(function (data) { return { status: resp.status, data: data }; });
      })
      .then(function (result) {
        if (!result || !result.data) return;
        var data = result.data;
        if (typeof data.selection_count === 'number') setCartCount(data.selection_count);
        if (Array.isArray(data.selected_ids)) updateSelectedButtons(data.selected_ids);
        if (Array.isArray(data.removed_unavailable_ids) && data.removed_unavailable_ids.length > 0) {
          showToast('warning', 'Una candidata ya no está disponible.');
        }
        if (!data.ok) {
          showToast('error', data.message || 'No se pudo actualizar la selección.');
          return;
        }
        showToast('success', data.message || 'Selección actualizada');
      })
      .catch(function () {
        submitFallback(form);
      });
  });

  function pollState() {
    if (!token) return;
    fetch('/tienda/' + encodeURIComponent(token) + '/estado.json', {
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json'
      },
      credentials: 'same-origin'
    })
      .then(function (resp) {
        if (!resp.ok) return null;
        return resp.json();
      })
      .then(function (data) {
        if (!data || !data.ok) return;
        if (typeof data.selection_count === 'number') setCartCount(data.selection_count);
        if (Array.isArray(data.selected_ids)) updateSelectedButtons(data.selected_ids);
        if (Array.isArray(data.removed_unavailable_ids) && data.removed_unavailable_ids.length > 0) {
          showToast('warning', 'Una candidata ya no está disponible.');
        }
        if (Array.isArray(data.available_ids)) {
          var next = data.available_ids.slice().sort().join(',');
          if (knownAvailableIds !== null && knownAvailableIds !== next) {
            showToast('info', 'Hay perfiles nuevos disponibles.');
          }
          knownAvailableIds = next;
        }
      })
      .catch(function () {});
  }

  bindDrawer();
  bindCheckoutSubmitUX();
  window.setTimeout(function () {
    pollState();
    window.setInterval(pollState, 25000);
  }, 800);
})();
