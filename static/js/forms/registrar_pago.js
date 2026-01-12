// static/js/forms/registrar_pago.js
// JS limpio y seguro para el formulario de registrar pago
// Evita doble envío y controla loader sin romper la UI

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {

    // El formulario debe tener este atributo en el HTML:
    // <form method="POST" data-form="registrar-pago">
    const form = document.querySelector('form[data-form="registrar-pago"]');
    if (!form) return;

    const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');

    form.addEventListener('submit', function () {
      // Bloquea el botón para evitar doble envío
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('is-loading');
      }

      // Muestra loader global si existe
      if (window.Loader && typeof window.Loader.show === 'function') {
        window.Loader.show();
      }
    });

    // Cuando la página vuelve (redirect o error backend),
    // se asegura de restaurar el estado
    window.addEventListener('pageshow', function () {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove('is-loading');
      }

      if (window.Loader && typeof window.Loader.hide === 'function') {
        window.Loader.hide();
      }
    });

  });
})();