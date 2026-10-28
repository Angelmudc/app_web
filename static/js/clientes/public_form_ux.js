/* Backward-compatible bridge for the two public request forms. */
(function (window, document) {
  'use strict';
  var ASSET_VERSION = '20261028-validation-v1';

  function fallbackFieldLabel(control) {
    var field = control.closest('.public-field, .public-input-block, .public-terms-wrap');
    var label = control.labels && control.labels.length ? control.labels[0] : null;
    var text = label && label.textContent;
    if (!text && field) {
      var heading = field.querySelector('legend, .public-field-label, label');
      text = heading && heading.textContent;
    }
    return String(text || control.name || 'este dato').replace(/\s+/g, ' ').trim();
  }

  function fallbackMessage(control, label) {
    if (control.validity.valueMissing) return 'Completa «' + label + '» para continuar.';
    if (control.validity.typeMismatch || control.validity.patternMismatch) return 'Revisa el formato de «' + label + '».';
    if (control.validity.rangeUnderflow || control.validity.rangeOverflow || control.validity.stepMismatch) return 'Revisa el valor de «' + label + '».';
    if (control.validity.tooShort || control.validity.tooLong) return 'Revisa la longitud de «' + label + '».';
    return 'Revisa «' + label + '» para continuar.';
  }

  function visibleFallbackControl(control) {
    if (!control.willValidate || control.disabled || control.type === 'hidden' || !control.getClientRects().length) return false;
    for (var node = control; node; node = node.parentElement) {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.classList.contains('d-none')) return false;
      var style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      if (node.tagName === 'FORM') break;
    }
    return true;
  }

  function clearFallbackErrors(form) {
    form.querySelectorAll('[data-public-fallback-error]').forEach(function (node) { node.remove(); });
    form.querySelectorAll('[data-public-fallback-invalid="true"]').forEach(function (control) {
      control.removeAttribute('aria-invalid');
      control.removeAttribute('data-public-fallback-invalid');
      var describedBy = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter(function (id) {
        return id && !/^public-fallback-error-/.test(id);
      });
      if (describedBy.length) control.setAttribute('aria-describedby', describedBy.join(' '));
      else control.removeAttribute('aria-describedby');
      control.classList.remove('public-vue-error-target');
    });
  }

  function activateFallback(form) {
    if (!form || form.dataset.publicFormFallback === 'true') return;
    form.dataset.publicFormFallback = 'true';
    document.dispatchEvent(new CustomEvent('public-form-draft-ready', {
      detail: { formId: form.id, restored: false, validationFallback: true }
    }));
  }

  function showFallbackErrors(form, errors) {
    var root = document.getElementById(form.id + 'VueRoot');
    if (!root) return;
    clearFallbackErrors(form);
    root.querySelectorAll('[data-public-fallback-summary]').forEach(function (node) { node.remove(); });

    var summary = document.createElement('section');
    summary.className = 'public-form-vue-error-summary';
    summary.setAttribute('data-public-fallback-summary', 'true');
    summary.setAttribute('role', 'alert');
    summary.setAttribute('aria-labelledby', 'public-fallback-summary-title-' + form.id);
    summary.tabIndex = -1;
    var title = document.createElement('h2');
    title.id = 'public-fallback-summary-title-' + form.id;
    title.textContent = 'Faltan datos para continuar';
    var intro = document.createElement('p');
    intro.textContent = 'Revisa estos campos y vuelve a intentar:';
    var list = document.createElement('ul');
    summary.appendChild(title);
    summary.appendChild(intro);
    summary.appendChild(list);
    root.appendChild(summary);

    errors.forEach(function (error, index) {
      var control = error.control;
      var id = control.id || (control.id = 'public-fallback-control-' + form.id + '-' + index);
      var field = control.closest('.public-field, .public-input-block, .public-terms-wrap') || control.parentElement;
      var message = document.createElement('div');
      message.className = 'public-vue-field-error';
      message.id = 'public-fallback-error-' + form.id + '-' + index;
      message.setAttribute('data-public-fallback-error', 'true');
      message.textContent = error.message;
      control.setAttribute('aria-invalid', 'true');
      control.setAttribute('data-public-fallback-invalid', 'true');
      control.classList.add('public-vue-error-target', 'public-form-vue-navigation-target');
      var descriptions = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
      descriptions.push(message.id);
      control.setAttribute('aria-describedby', descriptions.join(' '));
      field.appendChild(message);

      var item = document.createElement('li');
      var link = document.createElement('a');
      link.href = '#' + id;
      link.textContent = error.message;
      link.addEventListener('click', function (event) {
        event.preventDefault();
        control.focus({ preventScroll: true });
        control.scrollIntoView({
          behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
          block: 'center'
        });
      });
      item.appendChild(link);
      list.appendChild(item);
    });

    var first = errors[0].control;
    first.focus({ preventScroll: true });
    first.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'center'
    });
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.matches('form[data-form-ux="vue"]')) return;
    var root = document.getElementById(form.id + 'VueRoot');
    if (form.dataset.publicFormFallback !== 'true' && root && root.querySelector('.public-form-vue-status')) return;
    // A failed or delayed Vue bootstrap must not fall through to the inline
    // reportValidity() bridge, which opens browser-specific native tooltips.
    activateFallback(form);
    var errors = [];
    var seen = Object.create(null);
    Array.prototype.forEach.call(form.querySelectorAll('input, select, textarea'), function (control) {
      if (!visibleFallbackControl(control) || control.checkValidity()) return;
      var groupKey = /^(radio|checkbox)$/.test(control.type) && control.name ? control.type + ':' + control.name : control.id || control.name;
      if (seen[groupKey]) return;
      seen[groupKey] = true;
      var label = fallbackFieldLabel(control);
      errors.push({ control: control, message: fallbackMessage(control, label) });
    });
    if (!errors.length) return;
    event.preventDefault();
    form.dataset.publicFallbackAttempted = 'true';
    showFallbackErrors(form, errors);
  }, true);

  function start() {
    if (!document.querySelector('form[data-form-ux="vue"]')) return;
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/css/clientes/public_form_vue.css?v=' + ASSET_VERSION;
    document.head.appendChild(css);
    function loadController() {
      if (document.querySelector('script[data-public-form-controller="1"]')) return;
      var controller = document.createElement('script');
      controller.src = '/static/js/clientes/public_form_vue.js?v=' + ASSET_VERSION;
      controller.dataset.publicFormController = '1';
      controller.onerror = function () {
        document.querySelectorAll('form[data-form-ux="vue"]').forEach(activateFallback);
      };
      controller.onload = function () {
        window.setTimeout(function () {
          document.querySelectorAll('form[data-form-ux="vue"]').forEach(function (form) {
            var root = document.getElementById(form.id + 'VueRoot');
            if (!root || !root.querySelector('.public-form-vue-status')) activateFallback(form);
          });
        }, 0);
      };
      document.head.appendChild(controller);
    }
    var vue = document.createElement('script');
    vue.src = '/static/js/vendor/vue-3.5.13.global.prod.js?v=' + ASSET_VERSION;
    vue.dataset.publicFormVue = '3.5.13';
    vue.onload = loadController;
    vue.onerror = function () {
      console.error('No se pudo cargar Vue local; el formulario nativo sigue disponible.');
      document.querySelectorAll('form[data-form-ux="vue"]').forEach(function (form) {
        activateFallback(form);
      });
    };
    document.head.appendChild(vue);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(window, document);
