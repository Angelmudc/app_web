/* Backward-compatible bridge for the two public request forms. */
(function (window, document) {
  'use strict';
  var ASSET_VERSION = '20261002-audit1';
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
      document.head.appendChild(controller);
    }
    var vue = document.createElement('script');
    vue.src = '/static/js/vendor/vue-3.5.13.global.prod.js?v=' + ASSET_VERSION;
    vue.dataset.publicFormVue = '3.5.13';
    vue.onload = loadController;
    vue.onerror = function () { console.error('No se pudo cargar Vue local; el formulario nativo sigue disponible.'); };
    document.head.appendChild(vue);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(window, document);
