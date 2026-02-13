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
  function initForms() {
    d.addEventListener(
      'submit',
      (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;

        if (!form.closest('body.clientes,[data-portal="clientes"],.clientes')) return;
        if (form.hasAttribute('data-no-lock')) return;

        if (form.__submitting__) {
          e.preventDefault();
          return;
        }
        form.__submitting__ = true;

        const submits = $$('button[type="submit"], input[type="submit"]', form);
        submits.forEach((btn) => {
          btn.__oldText = btn.tagName === 'BUTTON' ? btn.textContent : btn.value;
          if (btn.tagName === 'BUTTON') btn.textContent = 'Guardando…';
          else btn.value = 'Guardando…';
          btn.disabled = true;
        });

        setTimeout(() => {
          form.__submitting__ = false;
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

    const autosize = (ta) => {
      if (!ta) return;
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 560) + 'px';
    };

    const initTextareas = () => {
      $$('textarea', d).forEach((ta) => {
        if (!ta.closest('body.clientes,[data-portal="clientes"],.clientes')) return;
        autosize(ta);
        ta.addEventListener('input', throttle(() => autosize(ta), 80), { passive: true });
      });
    };

    initTextareas();

    if ('MutationObserver' in w) {
      const mo = new MutationObserver(
        debounce(() => {
          initTextareas();
        }, 250)
      );
      mo.observe(d.body, { childList: true, subtree: true });
    }
  }

  // ===== Autosave de formularios (opt-in) =====
  // Para activarlo en un form: data-autosave-key="solicitud_form"
  function initAutosave() {
    const forms = $$('form[data-autosave-key]');
    if (!forms.length) return;

    const safeSet = (key, value) => {
      try {
        const str = JSON.stringify(value);
        if (str.length > CONFIG.maxAutosaveBytes) return;
        localStorage.setItem(key, str);
      } catch (_) {}
    };

    const safeGet = (key) => {
      try {
        const v = localStorage.getItem(key);
        return v ? JSON.parse(v) : null;
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
        if (el.name === 'csrf_token') return;

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
          localStorage.removeItem(key);
        } catch (_) {}
      });
    });
  }

  // ===== Prefetch de links internos (hover/focus) =====
  function initPrefetch() {
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
  }

  // ===== Lazy images (data-src) =====
  function initLazyImages() {
    const imgs = $$('img[data-src]');
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

      imgs.forEach((img) => io.observe(img));
    } else {
      imgs.forEach(loadImg);
    }
  }

  // ===== Online/Offline indicator =====
  function initNetworkHints() {
    const on = () => toast('Conexión restaurada ✅', 'success', { duration: 2000 });
    const off = () =>
      toast('Sin internet. Algunos cambios podrían no guardarse.', 'warning', { duration: 3500 });
    w.addEventListener('online', on, { passive: true });
    w.addEventListener('offline', off, { passive: true });
  }

  // ===== Confirmaciones por data-confirm =====
  function initConfirmLinks() {
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
  }

  // ===== QoL: scroll suave a errores =====
  function scrollToFirstError() {
    const err = $('.field-error, .error, .invalid-feedback, .form-error, [data-error="1"]');
    if (!err) return;
    const card = err.closest('.c-card,.card,.panel,.form-card') || err;
    try {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (_) {
      card.scrollIntoView();
    }
  }

  // ===== Init =====
  function init() {
    const t0 = now();

    setAriaCurrent();
    initForms();
    initConfirmLinks();

    setTimeout(() => {
      initAutosave();
      initLazyImages();
      initNetworkHints();
      initPrefetch();
      scrollToFirstError();
    }, 0);

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
  w.ClientesApp = Object.freeze({
    toast,
    confirmBox,
    debounce,
    throttle,
  });
})();