/* Backward-compatible bridge for the two public request forms. */
(function (window, document) {
  'use strict';
  var ASSET_VERSION = '20261028-validation-v1';
  var controllerRequested = false;

  function forms() {
    return Array.prototype.slice.call(document.querySelectorAll('form[data-form-ux="vue"]'));
  }

  function rootFor(form) {
    return document.getElementById(form.id + 'VueRoot');
  }

  function vueReady(form) {
    var root = rootFor(form);
    return !!(root && root.querySelector('.public-form-vue-status'));
  }

  function activateFallback(form) {
    if (!form || vueReady(form)) {
      if (form) delete form.dataset.publicFormFallback;
      return;
    }
    if (form.dataset.publicFormFallback === 'true') return;
    form.dataset.publicFormFallback = 'true';
    document.dispatchEvent(new CustomEvent('public-form-draft-ready', {
      detail: { formId: form.id, restored: false, validationFallback: true }
    }));
  }

  function addStylesheet() {
    if (document.querySelector('link[data-public-form-vue-css="1"]')) return;
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/css/clientes/public_form_vue.css?v=' + ASSET_VERSION;
    css.dataset.publicFormVueCss = '1';
    document.head.appendChild(css);
  }

  function loadController() {
    if (controllerRequested || document.querySelector('script[data-public-form-controller="1"]')) return;
    controllerRequested = true;
    var controller = document.createElement('script');
    controller.src = '/static/js/clientes/public_form_vue.js?v=' + ASSET_VERSION;
    controller.dataset.publicFormController = '1';
    controller.onerror = function () {
      forms().forEach(activateFallback);
    };
    controller.onload = function () {
      window.setTimeout(function () {
        forms().forEach(function (form) {
          if (!vueReady(form)) activateFallback(form);
          else delete form.dataset.publicFormFallback;
        });
      }, 0);
    };
    document.head.appendChild(controller);
  }

  function activateFallbackForAll() {
    forms().forEach(activateFallback);
    loadController();
  }

  function fieldLabel(control) {
    var label = control.labels && control.labels.length ? control.labels[0] : null;
    var field = control.closest('.public-field, .public-input-block, .public-terms-wrap');
    var heading = field && field.querySelector('legend, .public-field-label, label');
    return String((label && label.textContent) || (heading && heading.textContent) || control.name || 'este dato')
      .replace(/\s+/g, ' ').trim();
  }

  function visibleControl(control) {
    if (!control.willValidate || control.disabled || control.type === 'hidden' || !control.getClientRects().length) return false;
    for (var node = control; node; node = node.parentElement) {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.classList.contains('d-none')) return false;
      var style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      if (node.tagName === 'FORM') break;
    }
    return true;
  }

  function collectErrors(form) {
    var api = window.PublicFormValidation;
    if (!api || typeof api.collect !== 'function') return null;
    var errors = api.collect(form);
    var nativeValid = form.checkValidity();
    if (nativeValid) return errors;

    Array.prototype.forEach.call(form.querySelectorAll('input, select, textarea'), function (control) {
      if (!visibleControl(control) || control.checkValidity()) return;
      var field = control.closest('.public-field, .public-input-block, .public-terms-wrap');
      var covered = errors.some(function (error) {
        return error.element === control || (field && error.element.closest && error.element.closest('.public-field, .public-input-block, .public-terms-wrap') === field);
      });
      if (!covered) errors.push({ element: control, message: 'Completa «' + fieldLabel(control) + '» para continuar.' });
    });
    if (!errors.length) {
      var root = rootFor(form);
      errors.push({ element: root || form, message: 'Revisa los campos obligatorios antes de continuar.' });
    }
    return errors;
  }

  function removeFallbackSummary(root) {
    if (!root) return;
    root.querySelectorAll('[data-public-fallback-summary], [data-public-fallback-engine-error]').forEach(function (node) {
      node.remove();
    });
  }

  function showSummary(form, errors) {
    var root = rootFor(form);
    var api = window.PublicFormValidation;
    if (!root || !api || typeof api.render !== 'function') return false;
    api.render(form, errors, true);
    removeFallbackSummary(root);
    if (!errors.length) return true;

    var summary = document.createElement('section');
    summary.className = 'public-form-vue-error-summary';
    summary.dataset.publicFallbackSummary = 'true';
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

    errors.forEach(function (error) {
      var item = document.createElement('li');
      var link = document.createElement('a');
      link.href = error.targetId ? '#' + error.targetId : '#';
      link.textContent = error.message;
      link.addEventListener('click', function (event) {
        event.preventDefault();
        api.focus(error);
      });
      item.appendChild(link);
      list.appendChild(item);
    });
    return true;
  }

  function showEngineError(form) {
    var root = rootFor(form);
    if (!root) return;
    removeFallbackSummary(root);
    var notice = document.createElement('p');
    notice.className = 'public-form-vue-error-summary';
    notice.dataset.publicFallbackEngineError = 'true';
    notice.setAttribute('role', 'alert');
    notice.textContent = 'No se pudo iniciar la validación. Recarga el formulario para continuar de forma segura.';
    root.appendChild(notice);
  }

  function syncFallback(form, focusFirst) {
    if (!form || form.dataset.publicFormFallback !== 'true' || form.dataset.publicFallbackAttempted !== 'true') return [];
    var errors;
    try {
      errors = collectErrors(form);
    } catch (error) {
      errors = null;
    }
    if (!errors) {
      showEngineError(form);
      return null;
    }
    showSummary(form, errors);
    if (focusFirst && errors.length && window.PublicFormValidation) window.PublicFormValidation.focus(errors[0]);
    return errors;
  }

  function onSubmit(event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches('form[data-form-ux="vue"]')) return;
    if (vueReady(form)) {
      delete form.dataset.publicFormFallback;
      return;
    }
    activateFallback(form);
    var errors;
    try {
      errors = collectErrors(form);
    } catch (error) {
      errors = null;
    }
    if (!errors) {
      event.preventDefault();
      form.dataset.publicFallbackAttempted = 'true';
      showEngineError(form);
      return;
    }
    form.dataset.publicFallbackAttempted = 'true';
    showSummary(form, errors);
    if (errors.length) {
      event.preventDefault();
      window.setTimeout(function () {
        if (errors.length && window.PublicFormValidation) window.PublicFormValidation.focus(errors[0]);
      }, 0);
    }
  }

  function onEdit(event) {
    var control = event.target;
    var form = control && control.form;
    if (!form || form.dataset.publicFormFallback !== 'true' || form.dataset.publicFallbackAttempted !== 'true') return;
    window.setTimeout(function () { syncFallback(form, false); }, 0);
  }

  function start() {
    if (!forms().length) return;
    addStylesheet();
    document.addEventListener('submit', onSubmit, true);
    document.addEventListener('input', onEdit, true);
    document.addEventListener('change', onEdit, true);
    document.addEventListener('blur', onEdit, true);

    var vue = document.createElement('script');
    vue.src = '/static/js/vendor/vue-3.5.13.global.prod.js?v=' + ASSET_VERSION;
    vue.dataset.publicFormVue = '3.5.13';
    vue.onload = loadController;
    vue.onerror = function () {
      console.error('No se pudo cargar Vue local; se activa la validación compartida sin interfaz Vue.');
      activateFallbackForAll();
    };
    document.head.appendChild(vue);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})(window, document);
