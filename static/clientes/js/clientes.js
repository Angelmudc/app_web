/* static/clientes/js/clientes.js
   Portal Clientes — JS liviano, rápido y sin fricciones.
   - Sin frameworks.
   - Event delegation.
   - Debounce/Throttle.
   - Prevenir doble submit.
   - Autosave opcional por form.
   - Toasts/confirm modal simples.
   - Lazy images + prefetch links.
   - Todo encapsulado para evitar choques con la app global.
*/

(function () {
  'use strict';

  // Evita re-inicializaciones si el layout se re-renderiza.
  if (window.__CLIENTES_APP_INIT__) return;
  window.__CLIENTES_APP_INIT__ = true;

  // ===== Helpers base =====
  const d = document;
  const w = window;

  const $ = (sel, root = d) => root.querySelector(sel);
  const $$ = (sel, root = d) => Array.from(root.querySelectorAll(sel));

  const now = () => (w.performance && performance.now ? performance.now() : Date.now());

  const raf = (fn) => w.requestAnimationFrame(fn);

  const debounce = (fn, wait = 200) => {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  };

  const throttle = (fn, wait = 150) => {
    let last = 0;
    let t;
    return function (...args) {
      const ts = Date.now();
      const remaining = wait - (ts - last);
      if (remaining <= 0) {
        last = ts;
        fn.apply(this, args);
      } else {
        clearTimeout(t);
        t = setTimeout(() => {
          last = Date.now();
          fn.apply(this, args);
        }, remaining);
      }
    };
  };

  const isClientesScope = () => {
    const body = d.body;
    return !!(
      body &&
      (body.classList.contains('clientes') ||
        body.getAttribute('data-portal') === 'clientes' ||
        $('.clientes'))
    );
  };

  // Si no estamos en portal clientes, no hagas nada.
  if (!isClientesScope()) return;

  // ===== Config =====
  const CONFIG = {
    toastDuration: 3500,
    autosaveDebounceMs: 450,
    prefetchDelayMs: 120,
    maxAutosaveBytes: 150_000, // por seguridad
  };

  const RUNTIME = w.__CLIENTES_APP_RUNTIME__ = w.__CLIENTES_APP_RUNTIME__ || {
    shellInitDone: false,
    formSubmitBound: false,
    textareaObserverBound: false,
    networkHintsBound: false,
    confirmLinksBound: false,
    prefetchBound: false,
    navLifecycleBound: false,
    beforeUnloadBound: false,
    solicitudDirtyState: { dirty: false },
  };

  // ===== Accesibilidad + UX =====
  function setAriaCurrent() {
    const path = location.pathname;
    $$('.clientes a[href]').forEach((a) => {
      try {
        const href = new URL(a.getAttribute('href'), location.origin);
        if (href.origin === location.origin && href.pathname === path) {
          a.setAttribute('aria-current', 'page');
        }
      } catch (_) {}
    });
  }

  // ===== Toasts (super liviano) =====
  function ensureToastRoot() {
    let root = $('#c-toast-root');
    if (root) return root;

    root = d.createElement('div');
    root.id = 'c-toast-root';
    root.setAttribute('role', 'status');
    root.setAttribute('aria-live', 'polite');

    root.style.position = 'fixed';
    root.style.right = '16px';
    root.style.bottom = '16px';
    root.style.zIndex = '9999';
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.gap = '10px';
    root.style.maxWidth = 'min(420px, calc(100vw - 32px))';

    d.body.appendChild(root);
    return root;
  }

  function toast(message, type = 'info', opts = {}) {
    const root = ensureToastRoot();
    const el = d.createElement('div');

    el.className = 'c-toast';
    el.setAttribute('role', 'status');
    el.style.padding = '12px 14px';
    el.style.borderRadius = '12px';
    el.style.backdropFilter = 'blur(10px)';
    el.style.webkitBackdropFilter = 'blur(10px)';
    el.style.boxShadow = '0 18px 40px rgba(0,0,0,.22)';
    el.style.border = '1px solid rgba(255,255,255,.16)';
    el.style.color = '#fff';
    el.style.fontSize = '14px';
    el.style.lineHeight = '1.25';
    el.style.transform = 'translateY(8px)';
    el.style.opacity = '0';
    el.style.transition = 'opacity .18s ease, transform .18s ease';

    const bg =
      type === 'success'
        ? 'rgba(16, 185, 129, .92)'
        : type === 'error'
        ? 'rgba(239, 68, 68, .92)'
        : type === 'warning'
        ? 'rgba(245, 158, 11, .92)'
        : 'rgba(59, 130, 246, .92)';

    el.style.background = bg;
    el.textContent = String(message ?? '');

    el.addEventListener(
      'click',
      () => {
        dismissToast(el);
      },
      { passive: true }
    );

    root.appendChild(el);

    raf(() => {
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });

    const duration = typeof opts.duration === 'number' ? opts.duration : CONFIG.toastDuration;
    if (duration > 0) {
      setTimeout(() => dismissToast(el), duration);
    }
  }

  function dismissToast(el) {
    if (!el || !el.parentNode) return;
    el.style.opacity = '0';
    el.style.transform = 'translateY(8px)';
    setTimeout(() => {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  // ===== Modal confirm (simple) =====
  function ensureConfirmModal() {
    let modal = $('#c-confirm');
    if (modal) return modal;

    modal = d.createElement('div');
    modal.id = 'c-confirm';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.style.position = 'fixed';
    modal.style.inset = '0';
    modal.style.display = 'none';
    modal.style.alignItems = 'center';
    modal.style.justifyContent = 'center';
    modal.style.zIndex = '9998';

    const overlay = d.createElement('div');
    overlay.style.position = 'absolute';
    overlay.style.inset = '0';
    overlay.style.background = 'rgba(0,0,0,.48)';

    const card = d.createElement('div');
    card.style.position = 'relative';
    card.style.width = 'min(520px, calc(100vw - 28px))';
    card.style.borderRadius = '16px';
    card.style.padding = '16px';
    card.style.background = 'rgba(20, 24, 33, .95)';
    card.style.border = '1px solid rgba(255,255,255,.12)';
    card.style.boxShadow = '0 30px 80px rgba(0,0,0,.45)';
    card.style.color = '#fff';

    const title = d.createElement('div');
    title.id = 'c-confirm-title';
    title.style.fontWeight = '700';
    title.style.fontSize = '16px';
    title.style.marginBottom = '8px';

    const msg = d.createElement('div');
    msg.id = 'c-confirm-msg';
    msg.style.opacity = '.92';
    msg.style.fontSize = '14px';
    msg.style.lineHeight = '1.35';
    msg.style.marginBottom = '14px';

    const row = d.createElement('div');
    row.style.display = 'flex';
    row.style.gap = '10px';
    row.style.justifyContent = 'flex-end';

    const btnCancel = d.createElement('button');
    btnCancel.type = 'button';
    btnCancel.id = 'c-confirm-cancel';
    btnCancel.textContent = 'Cancelar';
    btnCancel.style.padding = '10px 12px';
    btnCancel.style.borderRadius = '12px';
    btnCancel.style.border = '1px solid rgba(255,255,255,.16)';
    btnCancel.style.background = 'rgba(255,255,255,.08)';
    btnCancel.style.color = '#fff';
    btnCancel.style.cursor = 'pointer';

    const btnOk = d.createElement('button');
    btnOk.type = 'button';
    btnOk.id = 'c-confirm-ok';
    btnOk.textContent = 'Confirmar';
    btnOk.style.padding = '10px 12px';
    btnOk.style.borderRadius = '12px';
    btnOk.style.border = '1px solid rgba(255,255,255,.16)';
    btnOk.style.background = 'rgba(59, 130, 246, .92)';
    btnOk.style.color = '#fff';
    btnOk.style.cursor = 'pointer';

    row.appendChild(btnCancel);
    row.appendChild(btnOk);

    card.appendChild(title);
    card.appendChild(msg);
    card.appendChild(row);

    modal.appendChild(overlay);
    modal.appendChild(card);
    d.body.appendChild(modal);

    overlay.addEventListener('click', () => closeConfirm(false), { passive: true });

    return modal;
  }

  let __confirmResolve = null;
  function confirmBox({ title = 'Confirmar', message = '¿Seguro?', okText = 'Confirmar' } = {}) {
    const modal = ensureConfirmModal();
    $('#c-confirm-title').textContent = title;
    $('#c-confirm-msg').textContent = message;
    $('#c-confirm-ok').textContent = okText;

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');

    return new Promise((resolve) => {
      __confirmResolve = resolve;
      const ok = $('#c-confirm-ok');
      const cancel = $('#c-confirm-cancel');

      const onOk = () => {
        cleanup();
        closeConfirm(true);
      };
      const onCancel = () => {
        cleanup();
        closeConfirm(false);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          cleanup();
          closeConfirm(false);
        }
      };

      function cleanup() {
        ok.removeEventListener('click', onOk);
        cancel.removeEventListener('click', onCancel);
        d.removeEventListener('keydown', onKey);
      }

      ok.addEventListener('click', onOk);
      cancel.addEventListener('click', onCancel);
      d.addEventListener('keydown', onKey);

      setTimeout(() => ok.focus(), 0);
    });
  }

  function closeConfirm(val) {
    const modal = $('#c-confirm');
    if (!modal) return;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    if (typeof __confirmResolve === 'function') {
      __confirmResolve(!!val);
      __confirmResolve = null;
    }
  }

  // ===== Form UX: evitar doble submit + loading =====
  // ===== Anti doble envío (GLOBAL + fingerprint) =====
  // - Evita doble submit por doble click/Enter/lag
  // - Evita duplicados por reintento rápido con la misma data
  // - Guarda un lock corto en sessionStorage por fingerprint
  const SUBMIT_LOCK_MS = 15000; // 15s

  function _normStr(v) {
    return String(v == null ? '' : v).trim().replace(/\s+/g, ' ');
  }

  function _hash32(str) {
    // FNV-1a 32-bit
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16);
  }

  function _formFingerprint(form) {
    try {
      const method = (form.getAttribute('method') || 'GET').toUpperCase();
      const action = form.getAttribute('action') || location.pathname;
      const fd = new FormData(form);

      // No incluimos csrf ni campos vacíos para que el fingerprint sea estable.
      const pairs = [];
      for (const [k, v] of fd.entries()) {
        const key = String(k || '');
        if (!key) continue;
        if (key === 'csrf_token') continue;

        // Normaliza valores
        const val = _normStr(v);
        if (!val) continue;
        pairs.push([key, val]);
      }

      // Orden para evitar que cambie por orden del DOM
      pairs.sort((a, b) => {
        if (a[0] === b[0]) return a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0;
        return a[0] < b[0] ? -1 : 1;
      });

      const base = `${method}|${action}|` + pairs.map((p) => `${p[0]}=${p[1]}`).join('&');
      return _hash32(base);
    } catch (_) {
      return '';
    }
  }

  function _lockKey(fp) {
    return `clientes_submit_lock:${fp}`;
  }

  function _isLocked(fp) {
    if (!fp) return false;
    try {
      const raw = sessionStorage.getItem(_lockKey(fp));
      if (!raw) return false;
      const exp = parseInt(raw, 10);
      if (!exp || Number.isNaN(exp)) {
        sessionStorage.removeItem(_lockKey(fp));
        return false;
      }
      if (Date.now() > exp) {
        sessionStorage.removeItem(_lockKey(fp));
        return false;
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  function _setLock(fp) {
    if (!fp) return;
    try {
      sessionStorage.setItem(_lockKey(fp), String(Date.now() + SUBMIT_LOCK_MS));
    } catch (_) {}
  }

  function _clearLock(fp) {
    if (!fp) return;
    try {
      sessionStorage.removeItem(_lockKey(fp));
    } catch (_) {}
  }
  function initForms() {
    const root = (arguments[0] && arguments[0].querySelectorAll) ? arguments[0] : d;

    if (!RUNTIME.formSubmitBound) {
      d.addEventListener(
        'submit',
        (e) => {
          const form = e.target;
          if (!(form instanceof HTMLFormElement)) return;

          if (!form.closest('body.clientes,[data-portal="clientes"],.clientes')) return;
          if (form.hasAttribute('data-no-lock')) return;

          // 1) Bloqueo por submit en memoria (doble click / enter / lag)
          if (form.__submitting__) {
            e.preventDefault();
            toast('Ya se está enviando…', 'warning', { duration: 1800 });
            return;
          }

          // 2) Bloqueo por fingerprint (evita duplicados del mismo payload por reintento rápido)
          const fp = _formFingerprint(form);
          if (fp && _isLocked(fp)) {
            e.preventDefault();
            toast('Ese formulario ya se envió. Espera un momento…', 'warning', { duration: 2200 });
            return;
          }

          form.__submitting__ = true;
          if (fp) _setLock(fp);
          form.__submit_fp__ = fp;

          const submits = $$('button[type="submit"], input[type="submit"]', form);
          submits.forEach((btn) => {
            btn.__oldText = btn.tagName === 'BUTTON' ? btn.textContent : btn.value;
            if (btn.tagName === 'BUTTON') btn.textContent = 'Guardando…';
            else btn.value = 'Guardando…';
            btn.disabled = true;
          });

          setTimeout(() => {
            form.__submitting__ = false;
            if (form.__submit_fp__) {
              _clearLock(form.__submit_fp__);
              form.__submit_fp__ = '';
            }
            submits.forEach((btn) => {
              try {
                btn.disabled = false;
                if (btn.tagName === 'BUTTON') btn.textContent = btn.__oldText || 'Guardar';
                else btn.value = btn.__oldText || 'Guardar';
              } catch (_) {}
            });
          }, 12000);
        },
        true
      );
      RUNTIME.formSubmitBound = true;
    }

    const autosize = (ta) => {
      if (!ta) return;
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 560) + 'px';
    };

    const initTextareas = (scopeRoot) => {
      $$('textarea', scopeRoot || d).forEach((ta) => {
        if (!ta.closest('body.clientes,[data-portal="clientes"],.clientes')) return;
        if (ta.getAttribute('data-clientes-autosize-bound') === '1') {
          autosize(ta);
          return;
        }
        ta.setAttribute('data-clientes-autosize-bound', '1');
        autosize(ta);
        ta.addEventListener('input', throttle(() => autosize(ta), 80), { passive: true });
      });
    };

    initTextareas(root);

    if (!RUNTIME.textareaObserverBound && 'MutationObserver' in w) {
      const mo = new MutationObserver(
        debounce(() => {
          initTextareas(d);
        }, 250)
      );
      mo.observe(d.body, { childList: true, subtree: true });
      RUNTIME.textareaObserverBound = true;
    }
  }

  // ===== Autosave de formularios (opt-in) =====
  // Para activarlo en un form: data-autosave-key="solicitud_form"
  function initAutosave() {
    const root = (arguments[0] && arguments[0].querySelectorAll) ? arguments[0] : d;
    const forms = $$('form[data-autosave-key]', root);
    if (!forms.length) return;
    let STORAGE = null;
    try {
      STORAGE = w.sessionStorage;
    } catch (_) {
      STORAGE = null;
    }
    if (!STORAGE) return;
    const TTL_MS = 30 * 60 * 1000;
    const SENSITIVE_TOKENS = [
      'password', 'clave', 'token', 'csrf', 'cedula', 'telefono', 'phone',
      'whatsapp', 'direccion', 'address', 'email', 'correo'
    ];
    const isSensitiveName = (name) => {
      const txt = String(name || '').toLowerCase();
      return SENSITIVE_TOKENS.some((token) => txt.includes(token));
    };

    const safeSet = (key, value) => {
      try {
        const str = JSON.stringify({ saved_at: Date.now(), data: value });
        if (str.length > CONFIG.maxAutosaveBytes) return;
        STORAGE.setItem(key, str);
      } catch (_) {}
    };

    const safeGet = (key) => {
      try {
        const v = STORAGE.getItem(key);
        if (!v) return null;
        const envelope = JSON.parse(v);
        const savedAt = Number(envelope.saved_at || 0);
        if (!savedAt || (Date.now() - savedAt) > TTL_MS) {
          STORAGE.removeItem(key);
          return null;
        }
        return envelope.data || null;
      } catch (_) {
        return null;
      }
    };

    const serialize = (form) => {
      const data = {};
      const els = Array.from(form.elements);
      els.forEach((el) => {
        if (!el.name || el.disabled) return;
        const type = (el.type || '').toLowerCase();
        if (el.name === 'csrf_token' || isSensitiveName(el.name)) return;

        if (type === 'checkbox') {
          if (!data[el.name]) data[el.name] = [];
          if (el.checked) data[el.name].push(el.value);
        } else if (type === 'radio') {
          if (el.checked) data[el.name] = el.value;
        } else if (el instanceof HTMLSelectElement && el.multiple) {
          data[el.name] = Array.from(el.selectedOptions).map((o) => o.value);
        } else {
          data[el.name] = el.value;
        }
      });
      return data;
    };

    const restore = (form, saved) => {
      if (!saved) return false;
      let applied = false;

      const els = Array.from(form.elements);
      els.forEach((el) => {
        if (!el.name || el.disabled) return;
        if (!(el.name in saved)) return;

        const val = saved[el.name];
        const type = (el.type || '').toLowerCase();

        try {
          if (type === 'checkbox') {
            const arr = Array.isArray(val) ? val : [val];
            el.checked = arr.includes(el.value);
            applied = true;
          } else if (type === 'radio') {
            el.checked = String(val) === String(el.value);
            applied = true;
          } else if (el instanceof HTMLSelectElement && el.multiple) {
            const arr = Array.isArray(val) ? val : [];
            Array.from(el.options).forEach((opt) => {
              opt.selected = arr.includes(opt.value);
            });
            applied = true;
          } else {
            if (typeof val === 'string' || typeof val === 'number') {
              el.value = String(val);
              applied = true;
            }
          }
        } catch (_) {}
      });

      return applied;
    };

    forms.forEach((form) => {
      if (!form.closest('body.clientes,[data-portal="clientes"],.clientes')) return;
      if (form.getAttribute('data-clientes-autosave-bound') === '1') return;
      form.setAttribute('data-clientes-autosave-bound', '1');

      const rawKey = form.getAttribute('data-autosave-key') || 'form';
      const key = `clientes_autosave:${location.pathname}:${rawKey}`;

      const saved = safeGet(key);
      const applied = restore(form, saved);
      if (applied) toast('Recuperé un borrador guardado ✨', 'info', { duration: 2200 });

      const onChange = debounce(() => {
        safeSet(key, serialize(form));
      }, CONFIG.autosaveDebounceMs);

      form.addEventListener('input', onChange, { passive: true });
      form.addEventListener('change', onChange, { passive: true });

      form.addEventListener('submit', () => {
        try {
          STORAGE.removeItem(key);
        } catch (_) {}
      });
    });
  }

  // ===== Prefetch de links internos (hover/focus) =====
  function initPrefetch() {
    if (RUNTIME.prefetchBound) return;
    if (!('fetch' in w) || !('requestIdleCallback' in w)) return;

    const isSameOriginHTML = (href) => {
      try {
        const u = new URL(href, location.origin);
        if (u.origin !== location.origin) return false;
        const bad = ['.pdf', '.zip', '.png', '.jpg', '.jpeg', '.webp', '.gif'];
        if (bad.some((ext) => u.pathname.toLowerCase().endsWith(ext))) return false;
        if (u.pathname.includes('/logout')) return false;
        return true;
      } catch (_) {
        return false;
      }
    };

    const seen = new Set();
    let timer;

    const schedule = (href) => {
      if (!href || seen.has(href)) return;
      if (!isSameOriginHTML(href)) return;

      clearTimeout(timer);
      timer = setTimeout(() => {
        seen.add(href);
        w.requestIdleCallback(
          () => {
            fetch(href, { credentials: 'same-origin' }).catch(() => {});
          },
          { timeout: 1200 }
        );
      }, CONFIG.prefetchDelayMs);
    };

    d.addEventListener(
      'mouseover',
      (e) => {
        const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!a) return;
        schedule(a.getAttribute('href'));
      },
      { passive: true }
    );

    d.addEventListener(
      'focusin',
      (e) => {
        const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!a) return;
        schedule(a.getAttribute('href'));
      },
      { passive: true }
    );
    RUNTIME.prefetchBound = true;
  }

  // ===== Lazy images (data-src) =====
  function initLazyImages() {
    const root = (arguments[0] && arguments[0].querySelectorAll) ? arguments[0] : d;
    const imgs = $$('img[data-src]', root).filter((img) => img.getAttribute('data-clientes-lazy-bound') !== '1');
    if (!imgs.length) return;

    const loadImg = (img) => {
      const src = img.getAttribute('data-src');
      if (!src) return;
      img.src = src;
      img.removeAttribute('data-src');
      img.classList.add('is-loaded');
    };

    if ('IntersectionObserver' in w) {
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((en) => {
            if (en.isIntersecting) {
              loadImg(en.target);
              io.unobserve(en.target);
            }
          });
        },
        { rootMargin: '220px 0px' }
      );

      imgs.forEach((img) => {
        img.setAttribute('data-clientes-lazy-bound', '1');
        io.observe(img);
      });
    } else {
      imgs.forEach((img) => {
        img.setAttribute('data-clientes-lazy-bound', '1');
        loadImg(img);
      });
    }
  }

  // ===== Online/Offline indicator =====
  function initNetworkHints() {
    if (RUNTIME.networkHintsBound) return;
    const on = () => toast('Conexión restaurada ✅', 'success', { duration: 2000 });
    const off = () =>
      toast('Sin internet. Algunos cambios podrían no guardarse.', 'warning', { duration: 3500 });
    w.addEventListener('online', on, { passive: true });
    w.addEventListener('offline', off, { passive: true });
    RUNTIME.networkHintsBound = true;
  }

  // ===== Confirmaciones por data-confirm =====
  function initConfirmLinks() {
    if (RUNTIME.confirmLinksBound) return;
    d.addEventListener(
      'click',
      async (e) => {
        const el = e.target && e.target.closest ? e.target.closest('[data-confirm]') : null;
        if (!el) return;

        if (!el.closest('body.clientes,[data-portal="clientes"],.clientes')) return;

        const msg = el.getAttribute('data-confirm') || '¿Seguro?';
        const title = el.getAttribute('data-confirm-title') || 'Confirmar acción';
        const okText = el.getAttribute('data-confirm-ok') || 'Sí, confirmar';

        if (el.matches('button[type="submit"], input[type="submit"]')) {
          e.preventDefault();
          const form = el.closest('form');
          if (!form) return;
          const ok = await confirmBox({ title, message: msg, okText });
          if (ok) form.requestSubmit ? form.requestSubmit(el) : form.submit();
          return;
        }

        if (el.matches('a[href]')) {
          e.preventDefault();
          const href = el.getAttribute('href');
          const ok = await confirmBox({ title, message: msg, okText });
          if (ok && href) location.href = href;
        }
      },
      true
    );
    RUNTIME.confirmLinksBound = true;
  }

  // ===== QoL: scroll suave a errores =====
  function scrollToFirstError() {
    const root = (arguments[0] && arguments[0].querySelector) ? arguments[0] : d;
    const err = $('.field-error, .error, .invalid-feedback, .form-error, [data-error="1"]', root);
    if (!err) return;
    const card = err.closest('.c-card,.card,.panel,.form-card') || err;
    try {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (_) {
      card.scrollIntoView();
    }
  }

  // ==========================================================
  // Solicitud Form (Clientes)
  // - Lógica del formulario sin meter JS en el template
  // - Vive aquí (clientes.js) y solo corre si existe #solicitud-form
  // ==========================================================
  function initSolicitudForm() {
    const root = (arguments[0] && arguments[0].querySelector) ? arguments[0] : d;
    const formEl = $('#solicitud-form', root);
    if (!formEl) return;
    if (formEl.getAttribute('data-clientes-solicitud-bound') === '1') return;
    formEl.setAttribute('data-clientes-solicitud-bound', '1');
    const HAS_ERRORS = (formEl.dataset && formEl.dataset.hasErrors === '1');

    // ---------- Contadores ----------
    function bindCountBySelector(inputSel, outId) {
      const el = $(inputSel, formEl);
      const out = d.getElementById(outId);
      if (!el || !out) return;

      const upd = () => {
        try {
          out.textContent = String((el.value || '').length);
        } catch (_) {}
      };

      el.addEventListener('input', upd, { passive: true });
      upd();
    }

    // Nombres típicos en WTForms
    bindCountBySelector('textarea[name="experiencia"], #experiencia', 'exp-count');
    bindCountBySelector('textarea[name="nota_cliente"], #nota_cliente', 'nota-count');

    // ---------- Limpieza de inputs en wraps ocultos ----------
    function clearWrapInputs(wrapEl) {
      if (!wrapEl) return;
      try {
        wrapEl.querySelectorAll('input, textarea, select').forEach((el) => {
          const tag = (el.tagName || '').toLowerCase();
          const type = (el.getAttribute('type') || '').toLowerCase();

          if (tag === 'select') {
            el.selectedIndex = 0;
            return;
          }

          if (type === 'checkbox' || type === 'radio') {
            el.checked = false;
            return;
          }

          el.value = '';
        });
      } catch (_) {}
    }

    // ---------- Aviso de cambios sin guardar ----------
    const dirtyState = RUNTIME.solicitudDirtyState || { dirty: false };
    RUNTIME.solicitudDirtyState = dirtyState;
    dirtyState.dirty = false;
    formEl.addEventListener('input', () => {
      dirtyState.dirty = true;
    }, { passive: true });
    if (!RUNTIME.beforeUnloadBound) {
      w.addEventListener('beforeunload', function (e) {
        if (!(RUNTIME.solicitudDirtyState && RUNTIME.solicitudDirtyState.dirty)) return;
        e.preventDefault();
        e.returnValue = '';
      });
      RUNTIME.beforeUnloadBound = true;
    }

    const submitBtn = $('#btn-submit', formEl);
    if (submitBtn) {
      submitBtn.addEventListener('click', () => {
        dirtyState.dirty = false;
      }, { passive: true });
    }
    formEl.addEventListener('submit', () => {
      dirtyState.dirty = false;
    }, { passive: true });

    // ---------- Wizard suave por pasos ----------
    const wizardShell = $('#solicitud-soft-wizard', formEl);
    const wizardNav = $('#wizard-step-nav', formEl);
    const wizardProgressBar = $('#wizard-progress-bar', formEl);
    const wizardStepStatus = $('#wizard-step-status', formEl);
    const wizardStepPercent = $('#wizard-step-percent', formEl);
    const wizardToggleBtn = $('#wizard-toggle-view', formEl);
    const wizardPrevBtn = $('#wizard-prev-btn', formEl);
    const wizardNextBtn = $('#wizard-next-btn', formEl);
    const wizardStepHidden = $('#wizard_step', formEl);
    const wizardRequiredWrap = $('#wizard-required-progress', formEl);
    const wizardRequiredBar = $('#wizard-required-bar', formEl);
    const wizardRequiredCount = $('#wizard-required-count', formEl);
    const wizardRequiredPercent = $('#wizard-required-percent', formEl);
    const wizardRequiredStatus = $('#wizard-required-status', formEl);
    const stepCards = $$('.public-form-sections > .public-form-card', formEl);
    const wizardEnabled = !!(wizardShell && stepCards.length > 1);
    const visitedSteps = new Set();
    const touchedSteps = new Set();

    if (wizardEnabled) {
      let showAll = false;
      let activeStep = 1;
      let navBuilt = false;

      function clampStep(n) {
        const max = stepCards.length;
        const parsed = parseInt(String(n || '1').trim(), 10);
        if (!Number.isFinite(parsed)) return 1;
        if (parsed < 1) return 1;
        if (parsed > max) return max;
        return parsed;
      }

      function cardTitle(card, index) {
        const t = card ? $('.public-form-card-title', card) : null;
        const txt = (t && t.textContent) ? String(t.textContent).trim() : '';
        return txt || `Paso ${index + 1}`;
      }

      function buildNav() {
        if (!wizardNav || navBuilt) return;
        wizardNav.innerHTML = '';
        stepCards.forEach((card, idx) => {
          const btn = d.createElement('button');
          const idxTag = d.createElement('span');
          const label = d.createElement('span');

          btn.type = 'button';
          btn.className = 'soft-wizard-step';
          btn.setAttribute('data-step-index', String(idx + 1));
          idxTag.className = 'soft-wizard-step-index';
          idxTag.textContent = String(idx + 1);
          label.className = 'soft-wizard-step-label';
          label.textContent = cardTitle(card, idx);
          btn.appendChild(idxTag);
          btn.appendChild(label);
          btn.addEventListener('click', () => {
            activeStep = idx + 1;
            showAll = false;
            renderWizard();
          });
          wizardNav.appendChild(btn);
        });
        navBuilt = true;
      }

      function findFirstStepWithErrors() {
        for (let i = 0; i < stepCards.length; i++) {
          const card = stepCards[i];
          if (!card) continue;
          if (card.querySelector('.is-invalid, .invalid-feedback, .field-error, .error, [aria-invalid="true"]')) {
            return i + 1;
          }
        }
        return 0;
      }

      function updateNavState() {
        const stepStats = stepCards.map((card) => computeRequiredStatsWithin(card));
        const navButtons = $$('.soft-wizard-step', wizardNav || d);
        navButtons.forEach((btn) => {
          const idx = clampStep(btn.getAttribute('data-step-index'));
          const stat = stepStats[idx - 1] || { total: 0, completed: 0 };
          const isDone = stat.total === 0 || stat.completed >= stat.total;
          const isActive = idx === activeStep;
          const isWorked = visitedSteps.has(idx) || touchedSteps.has(idx);

          btn.classList.remove('is-pending', 'is-incomplete', 'is-complete', 'is-active-complete', 'is-active-incomplete');
          btn.classList.toggle('is-active', idx === activeStep);

          if (isActive) {
            btn.classList.add(isDone ? 'is-active-complete' : 'is-active-incomplete');
          } else if (isDone) {
            btn.classList.add('is-complete');
          } else if (isWorked) {
            btn.classList.add('is-incomplete');
          } else {
            btn.classList.add('is-pending');
          }

          btn.setAttribute('aria-current', idx === activeStep ? 'step' : 'false');
        });
      }

      function updateProgress() {
        if (!wizardProgressBar) return;
        const total = Math.max(1, stepCards.length);
        const pct = Math.round((activeStep / total) * 100);
        wizardProgressBar.style.width = `${pct}%`;
        wizardProgressBar.setAttribute('aria-valuenow', String(pct));
        if (wizardStepStatus) {
          wizardStepStatus.textContent = `Paso ${activeStep} de ${total}`;
        }
        if (wizardStepPercent) {
          wizardStepPercent.textContent = `${pct}%`;
        }
      }

      function renderWizard() {
        const singleStepMode = !showAll;
        visitedSteps.add(activeStep);
        stepCards.forEach((card, idx) => {
          if (!card) return;
          const visible = singleStepMode ? ((idx + 1) === activeStep) : true;
          card.style.display = visible ? '' : 'none';
        });

        if (wizardToggleBtn) {
          wizardToggleBtn.textContent = showAll ? 'Ver por pasos' : 'Ver todo';
        }
        if (wizardPrevBtn) {
          wizardPrevBtn.disabled = showAll || activeStep <= 1;
          wizardPrevBtn.style.display = showAll ? 'none' : '';
        }
        if (wizardNextBtn) {
          wizardNextBtn.disabled = showAll || activeStep >= stepCards.length;
          wizardNextBtn.style.display = showAll ? 'none' : '';
        }
        if (wizardStepHidden) {
          wizardStepHidden.value = String(activeStep);
        }

        updateNavState();
        updateProgress();
      }

      const desiredStepFromState = clampStep(
        (wizardShell.dataset && wizardShell.dataset.initialStep)
        || (wizardStepHidden && wizardStepHidden.value)
        || '1'
      );
      const errorStep = HAS_ERRORS ? findFirstStepWithErrors() : 0;
      activeStep = clampStep(errorStep || desiredStepFromState);

      buildNav();
      renderWizard();

      if (wizardToggleBtn) {
        wizardToggleBtn.addEventListener('click', () => {
          showAll = !showAll;
          renderWizard();
        });
      }
      if (wizardPrevBtn) {
        wizardPrevBtn.addEventListener('click', () => {
          activeStep = clampStep(activeStep - 1);
          showAll = false;
          renderWizard();
        });
      }
      if (wizardNextBtn) {
        wizardNextBtn.addEventListener('click', () => {
          activeStep = clampStep(activeStep + 1);
          showAll = false;
          renderWizard();
        });
      }
    }

    // ---------- Barra de progreso por campos obligatorios ----------
      function isFillableRequiredControl(el) {
      if (!el || !el.tagName || !el.name) return false;
      if (el.disabled) return false;

      const tag = String(el.tagName).toLowerCase();
      const type = String(el.type || '').toLowerCase();

      if (!['input', 'select', 'textarea'].includes(tag)) return false;
      if (['hidden', 'submit', 'button', 'reset', 'file', 'image'].includes(type)) return false;
      if (!(el.required || String(el.getAttribute('aria-required') || '').toLowerCase() === 'true')) return false;

      const hiddenParent = el.closest('[hidden], .d-none');
      if (hiddenParent) return false;

      return true;
      }

      function computeRequiredStatsWithin(rootEl) {
        const controls = $$('input, select, textarea', rootEl || formEl).filter(isFillableRequiredControl);
        const groups = new Map();
        controls.forEach((el) => {
          if (!groups.has(el.name)) groups.set(el.name, []);
          groups.get(el.name).push(el);
        });

        let completed = 0;
        groups.forEach((arr) => {
          if (arr.length && isControlCompleted(arr[0], groups)) completed += 1;
        });

        return { total: groups.size, completed };
      }

      function stepIndexForElement(el) {
        if (!el || !wizardEnabled) return 0;
        const card = el.closest('.public-form-card');
        if (!card) return 0;
        const idx = stepCards.indexOf(card);
        return idx >= 0 ? idx + 1 : 0;
      }

      function currentRequiredControls() {
        return $$('input, select, textarea', formEl).filter(isFillableRequiredControl);
      }

    function isControlCompleted(el, controlsByName) {
      if (!el || !el.name) return false;

      const tag = String(el.tagName || '').toLowerCase();
      const type = String(el.type || '').toLowerCase();

      if (type === 'radio' || type === 'checkbox') {
        const peers = controlsByName.get(el.name) || [el];
        return peers.some((x) => !!x.checked);
      }

      if (tag === 'select') {
        return String(el.value || '').trim() !== '';
      }

      return String(el.value || '').trim() !== '';
    }

      function updateRequiredProgressUI() {
        if (!wizardRequiredWrap || !wizardRequiredBar || !wizardRequiredCount || !wizardRequiredStatus) return;

      const controls = currentRequiredControls();
      const groups = new Map();
      controls.forEach((el) => {
        if (!groups.has(el.name)) groups.set(el.name, []);
        groups.get(el.name).push(el);
      });

      const total = groups.size;
      if (total === 0) {
        wizardRequiredWrap.classList.add('d-none');
        return;
      }
      wizardRequiredWrap.classList.remove('d-none');

      let completed = 0;
      groups.forEach((arr) => {
        if (arr.length && isControlCompleted(arr[0], groups)) completed += 1;
      });

      const pct = Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
      const pending = Math.max(0, total - completed);

      wizardRequiredBar.style.width = `${pct}%`;
      wizardRequiredBar.setAttribute('aria-valuenow', String(pct));
      wizardRequiredCount.textContent = `${completed} de ${total} obligatorios`;
      if (wizardRequiredPercent) {
        wizardRequiredPercent.textContent = `${pct}%`;
      }

      if (completed >= total) {
        wizardRequiredWrap.classList.add('is-ready');
        wizardRequiredStatus.textContent = 'Lista para guardar.';
      } else {
        wizardRequiredWrap.classList.remove('is-ready');
        wizardRequiredStatus.textContent = `${pending} campo${pending === 1 ? '' : 's'} obligatorio${pending === 1 ? '' : 's'} pendiente${pending === 1 ? '' : 's'}.`;
      }
    }

    const syncWizardVisuals = debounce(() => {
      updateRequiredProgressUI();
      if (wizardEnabled) updateNavState();
    }, 80);
    formEl.addEventListener('input', (e) => {
      const idx = stepIndexForElement(e.target);
      if (idx > 0) touchedSteps.add(idx);
      syncWizardVisuals();
    }, { passive: true });
    formEl.addEventListener('change', (e) => {
      const idx = stepIndexForElement(e.target);
      if (idx > 0) touchedSteps.add(idx);
      syncWizardVisuals();
    }, { passive: true });
    syncWizardVisuals();

    // ---------- Helpers ----------
    function _normTxt(t) {
      return String(t || '').trim().toLowerCase().replace(/\s+/g, ' ');
    }

    function _stripAccents(s) {
      try {
        return String(s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '');
      } catch (_) {
        return String(s || '');
      }
    }

    function _labelTextForInput(inputEl) {
      if (!inputEl || !inputEl.id) return '';
      const lbl = d.querySelector(`label[for="${inputEl.id}"]`);
      return lbl ? lbl.textContent : '';
    }

    // ---------- Toggle "Otro" (Edad requerida) ----------
    const edadWrap = d.getElementById('wrap-edad-otro');
    const edadChecks = $$('input[name="edad_requerida"]', formEl);

    function updateEdadOtro() {
      if (!edadWrap) return;
      let checkedOtro = false;
      edadChecks.forEach((ch) => {
        if (String(ch.value || '').toLowerCase() === 'otro' && ch.checked) checkedOtro = true;
      });
      const hide = !checkedOtro;
      edadWrap.classList.toggle('d-none', hide);
      if (hide) clearWrapInputs(edadWrap);
    }

    edadChecks.forEach((ch) => ch.addEventListener('change', updateEdadOtro));
    updateEdadOtro();
    syncWizardVisuals();

    // ---------- Toggle "Otro" (Funciones) ----------
    const funcWrap = d.getElementById('wrap-funciones-otro');
    const funcChecks = $$('input[type="checkbox"][name="funciones"]', formEl);

    function updateFuncionesOtro() {
      if (!funcWrap) return;
      let checkedOtro = false;
      funcChecks.forEach((ch) => {
        if (String(ch.value || '').toLowerCase() === 'otro' && ch.checked) checkedOtro = true;
      });
      const hide = !checkedOtro;
      funcWrap.classList.toggle('d-none', hide);
      if (hide) clearWrapInputs(funcWrap);
    }

    funcChecks.forEach((ch) => ch.addEventListener('change', updateFuncionesOtro));
    updateFuncionesOtro();
    syncWizardVisuals();

    // ---------- "Todas las anteriores" (genérico) ----------
    function findTodasLasAnteriores(checkboxes) {
      let found = checkboxes.find((ch) => String(ch.value || '').toLowerCase() === 'todas_anteriores');
      if (found) return found;

      for (const ch of checkboxes) {
        const txt = _normTxt(_stripAccents(_labelTextForInput(ch)));
        if (txt === 'todas las anteriores' || txt === 'todas las anteriores.') return ch;
      }
      return null;
    }

    // ---------- "Todas las anteriores" (Funciones) ----------
    const funcAllPrev = findTodasLasAnteriores(funcChecks);

    function funcOthers() {
      return funcChecks.filter((ch) => ch !== funcAllPrev);
    }

    function funcOthersExceptOtro() {
      return funcOthers().filter((ch) => String(ch.value || '').toLowerCase() !== 'otro');
    }

    function setFuncOthersChecked(checked) {
      funcOthersExceptOtro().forEach((ch) => {
        ch.checked = !!checked;
      });
      updateFuncionesOtro();
    }

    function syncFuncAllPrevFromOthers() {
      if (!funcAllPrev) return;
      const others = funcOthersExceptOtro();
      if (!others.length) return;
      funcAllPrev.checked = others.every((ch) => ch.checked);
    }

    if (funcAllPrev) {
      funcAllPrev.addEventListener('change', function () {
        const isOn = !!funcAllPrev.checked;
        setFuncOthersChecked(isOn);
        if (!isOn) funcAllPrev.checked = false;
      });

      funcOthers().forEach((ch) => ch.addEventListener('change', syncFuncAllPrevFromOthers));
      syncFuncAllPrevFromOthers();
    }

    // ---------- Toggle "Otro" (Tipo de lugar) ----------
    const tipoSel = $('select[name="tipo_lugar"], #tipo_lugar', formEl);
    const tipoWrap = d.getElementById('wrap-tipo-lugar-otro');

    function updateTipoLugarOtro() {
      if (!tipoSel || !tipoWrap) return;
      const hide = String(tipoSel.value || '') !== 'otro';
      tipoWrap.classList.toggle('d-none', hide);
      if (hide) clearWrapInputs(tipoWrap);
    }

    if (tipoSel) {
      tipoSel.addEventListener('change', updateTipoLugarOtro);
      updateTipoLugarOtro();
      syncWizardVisuals();
    }

    // ---------- "Todas las anteriores" (Áreas comunes) + limpiar area_otro ----------
    const areaChecks = $$('input[type="checkbox"][name="areas_comunes"]', formEl);
    const areaAllPrev = findTodasLasAnteriores(areaChecks);
    const areaOtro = areaChecks.find((ch) => String(ch.value || '').toLowerCase() === 'otro');
    const areaOtroInput = $('input[name="area_otro"], #area_otro', formEl);

    function areaOthers() {
      return areaChecks.filter((ch) => ch !== areaAllPrev);
    }

    function areaOthersExceptOtro() {
      return areaOthers().filter((ch) => String(ch.value || '').toLowerCase() !== 'otro');
    }

    function setAreaOthersChecked(checked) {
      areaOthersExceptOtro().forEach((ch) => {
        ch.checked = !!checked;
      });
      if (areaOtro && areaOtroInput && !areaOtro.checked) areaOtroInput.value = '';
    }

    function syncAreaAllPrevFromOthers() {
      if (!areaAllPrev) return;
      const others = areaOthersExceptOtro();
      if (!others.length) return;
      areaAllPrev.checked = others.every((ch) => ch.checked);
    }

    if (areaAllPrev) {
      areaAllPrev.addEventListener('change', function () {
        const isOn = !!areaAllPrev.checked;
        setAreaOthersChecked(isOn);
        if (!isOn) areaAllPrev.checked = false;
      });

      areaOthers().forEach((ch) => {
        ch.addEventListener('change', function () {
          syncAreaAllPrevFromOthers();
          if (ch === areaOtro && areaOtroInput && !areaOtro.checked) areaOtroInput.value = '';
        });
      });

      syncAreaAllPrevFromOthers();
    } else if (areaOtro && areaOtroInput) {
      areaOtro.addEventListener('change', function () {
        if (!areaOtro.checked) areaOtroInput.value = '';
      });
    }

    // ---------- Edades de niños: visible + obligatoria SOLO si (cuidar niños) && (ninos > 0) ----------
    const wrapEdadesNinos = d.getElementById('wrap-edades-ninos');
    const inputNinos = $('input[name="ninos"], #ninos', formEl);
    const inputEdadesNinos = $('input[name="edades_ninos"], #edades_ninos', formEl);

    function findFuncCuidarNinos(checkboxes) {
      let found = null;
      (checkboxes || []).forEach((ch) => {
        if (found) return;
        const txt = _stripAccents(_normTxt(_labelTextForInput(ch)));
        if (txt.includes('cuidar ninos') || txt.includes('cuidado de ninos')) found = ch;
      });
      return found;
    }

    const funcCuidarNinos = findFuncCuidarNinos(funcChecks);

    function updateEdadesNinosVisibility() {
      if (!wrapEdadesNinos) return;

      let n = 0;
      try {
        n = parseInt((inputNinos && inputNinos.value) ? String(inputNinos.value).trim() : '0', 10);
        if (isNaN(n)) n = 0;
      } catch (_) {
        n = 0;
      }

      const wantsKids = !!(funcCuidarNinos && funcCuidarNinos.checked);
      const show = wantsKids && n > 0;

      wrapEdadesNinos.classList.toggle('d-none', !show);

      if (inputEdadesNinos) {
        inputEdadesNinos.required = !!show;
        inputEdadesNinos.setAttribute('aria-required', show ? 'true' : 'false');
        if (!show) inputEdadesNinos.value = '';
      }
    }

    if (funcCuidarNinos) {
      funcCuidarNinos.addEventListener('change', updateEdadesNinosVisibility);
    }

    if (inputNinos) {
      inputNinos.addEventListener('input', updateEdadesNinosVisibility, { passive: true });
      inputNinos.addEventListener('change', updateEdadesNinosVisibility, { passive: true });
    }

    updateEdadesNinosVisibility();
    syncWizardVisuals();

    // ---------- Focus primer error ----------
    if (HAS_ERRORS) {
      const firstInvalid = d.querySelector('.is-invalid, .was-validated .form-control:invalid');
      try {
        if (firstInvalid) firstInvalid.focus({ preventScroll: false });
      } catch (_) {}
    }
  }

  function initSolicitudShortlistSelection() {
    const root = (arguments[0] && arguments[0].querySelector) ? arguments[0] : d;
    const form = root.querySelector('[data-shortlist-selection-form]');
    if (!form) return;
    if (form.getAttribute('data-shortlist-bound') === '1') return;
    form.setAttribute('data-shortlist-bound', '1');

    const counter = form.querySelector('[data-shortlist-count]');
    const submitBtn = form.querySelector('[data-shortlist-submit]');
    const checkboxes = Array.from(form.querySelectorAll('input[type="checkbox"][name="candidata_ids"]'));

    const sync = () => {
      const selected = checkboxes.filter((el) => !!el.checked).length;
      if (counter) {
        counter.textContent = `${selected} seleccionada${selected === 1 ? '' : 's'}`;
      }
      if (submitBtn) {
        submitBtn.disabled = selected <= 0;
      }
    };

    form.addEventListener('change', (evt) => {
      const target = evt && evt.target;
      if (!(target instanceof HTMLInputElement)) return;
      if (target.name !== 'candidata_ids') return;
      sync();
    }, { passive: true });

    sync();
  }

  function initSolicitudShortlistPolling() {
    const root = (arguments[0] && arguments[0].querySelector) ? arguments[0] : d;
    const marker = root.querySelector('[data-shortlist-poll]');
    if (!marker) return;
    if (marker.getAttribute('data-shortlist-poll-bound') === '1') return;
    marker.setAttribute('data-shortlist-poll-bound', '1');

    const state = String(marker.getAttribute('data-shortlist-state') || '').trim().toLowerCase();
    if (!['pending', 'pending_refresh', 'stale'].includes(state)) return;

    const pollUrl = String(marker.getAttribute('data-shortlist-url') || '').trim();
    if (!pollUrl) return;

    const baseMsRaw = parseInt(String(marker.getAttribute('data-shortlist-poll-base-ms') || '4000'), 10);
    const maxAttemptsRaw = parseInt(String(marker.getAttribute('data-shortlist-poll-max-attempts') || '6'), 10);
    const baseMs = Number.isFinite(baseMsRaw) ? Math.max(1000, baseMsRaw) : 4000;
    const maxAttempts = Number.isFinite(maxAttemptsRaw) ? Math.max(1, maxAttemptsRaw) : 6;
    const msgEl = root.querySelector('[data-shortlist-status-msg]');

    let attempt = 0;
    let stopped = false;

    const nextDelay = () => {
      const factor = Math.pow(1.7, Math.max(0, attempt));
      return Math.min(30000, Math.round(baseMs * factor));
    };

    const schedule = () => {
      if (stopped) return;
      if (attempt >= maxAttempts) {
        if (msgEl) {
          msgEl.textContent = 'Seguimos preparando tu shortlist. Intenta recargar en unos minutos.';
        }
        return;
      }
      const delay = nextDelay();
      attempt += 1;
      setTimeout(runPoll, delay);
    };

    const runPoll = () => {
      fetch(pollUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then((resp) => resp.json().catch(() => null))
        .then((data) => {
          const stateCode = String((((data || {}).state || {}).code || '')).trim().toLowerCase();
          if (['ready', 'empty', 'error'].includes(stateCode)) {
            stopped = true;
            window.location.reload();
            return;
          }
          schedule();
        })
        .catch(() => {
          schedule();
        });
    };

    schedule();
  }

  function getViewportRoot() {
    return d.querySelector('#clientMainViewport') || d;
  }

  function mountViewport(root) {
    const mountRoot = (root && root.querySelectorAll) ? root : getViewportRoot();

    setAriaCurrent();
    initForms(mountRoot);
    initSolicitudForm(mountRoot);
    initSolicitudShortlistSelection(mountRoot);
    initSolicitudShortlistPolling(mountRoot);

    setTimeout(() => {
      initAutosave(mountRoot);
      initLazyImages(mountRoot);
      scrollToFirstError(mountRoot);
    }, 0);
  }

  function bindNavLifecycle() {
    if (RUNTIME.navLifecycleBound) return;
    d.addEventListener('client:navigation-complete', (evt) => {
      if (evt && evt.detail && evt.detail.bootstrap) return;
      const root = (evt && evt.detail && evt.detail.container) ? evt.detail.container : getViewportRoot();
      mountViewport(root);
    });
    RUNTIME.navLifecycleBound = true;
  }

  function initShell() {
    if (RUNTIME.shellInitDone) return;
    initConfirmLinks();
    initNetworkHints();
    initPrefetch();
    bindNavLifecycle();
    RUNTIME.shellInitDone = true;
  }

  // ===== Init =====
  function init() {
    const t0 = now();

    initShell();
    mountViewport(getViewportRoot());

    raf(() => {
      d.documentElement.classList.add('clientes-ready');
    });

    // Debug opcional
    try {
      if (localStorage.getItem('clientes_debug') === '1') {
        const t1 = now();
        console.log('[clientes.js] init ms:', Math.round(t1 - t0));
        toast('Clientes: modo debug activo', 'info', { duration: 1500 });
      }
    } catch (_) {}
  }

  if (d.readyState === 'loading') {
    d.addEventListener('DOMContentLoaded', init, { passive: true });
  } else {
    init();
  }

  // API mínima por si la llamas desde templates
  // (No expone nada sensible; solo utilidades UI)
  w.ClientesPortal = w.ClientesPortal || {};
  w.ClientesPortal.toast = toast;
  w.ClientesPortal.confirm = confirmBox;
  w.ClientesPortal.mount = (root) => mountViewport(root || getViewportRoot());

})();
