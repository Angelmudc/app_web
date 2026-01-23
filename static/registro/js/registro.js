/* =========================================================
   REGISTRO WEB – JS AISLADO (PRO)
   - Formato cédula
   - Limpia teléfono
   - Validación con highlight + scroll
   - Spinner + deshabilitar botón
========================================================= */

(function () {
  'use strict';

  function qs(id){ return document.getElementById(id); }

  function softTrim(el){
    if (!el || typeof el.value !== 'string') return;
    el.value = el.value.trim();
  }

  function setInvalid(field, on){
    if (!field) return;
    const wrap = field.closest('.field');
    if (!wrap) return;
    if (on) wrap.classList.add('is-invalid');
    else wrap.classList.remove('is-invalid');
  }

  function normalizePhone(value){
    // deja dígitos y + al inicio
    if (!value) return '';
    let v = String(value).trim();
    v = v.replace(/[^\d+]/g,'').replace(/(?!^)\+/g,'');
    return v;
  }

  function formatCedula(value){
    if (!value) return '';
    const digits = String(value).replace(/\D/g,'').slice(0,11);
    if (digits.length === 11){
      return `${digits.slice(0,3)}-${digits.slice(3,10)}-${digits.slice(10)}`;
    }
    return value.trim();
  }

  document.addEventListener('DOMContentLoaded', function () {
    const form = qs('registroForm');
    if (!form) return;

    const submitBtn = qs('submitBtn');
    const spinner = qs('spinner');
    const btnText = qs('btnText');
    const errorSummary = qs('errorSummary');

    const nombre = qs('nombre_completo');
    const edad = qs('edad');
    const tel = qs('numero_telefono');
    const ced = qs('cedula');

    // Focus inicial
    if (nombre) nombre.focus();

    // Teléfono: limpiar al salir
    if (tel){
      tel.addEventListener('blur', function(){
        tel.value = normalizePhone(tel.value);
      });
      tel.addEventListener('input', function(){
        setInvalid(tel, false);
      });
    }

    // Cédula: formatear al salir
    if (ced){
      ced.addEventListener('blur', function(){
        ced.value = formatCedula(ced.value);
      });
      ced.addEventListener('input', function(){
        setInvalid(ced, false);
      });
    }

    // Edad: evitar negativos
    if (edad){
      edad.addEventListener('input', function(){
        if (!edad.value) return;
        const n = parseInt(edad.value, 10);
        edad.value = String(Math.max(0, isNaN(n) ? 0 : n));
        setInvalid(edad, false);
      });
    }

    // Quitar invalid al escribir
    if (nombre){
      nombre.addEventListener('input', function(){ setInvalid(nombre, false); });
    }

    // Validación
    function validate(){
      let ok = true;

      if (errorSummary) errorSummary.classList.add('d-none');

      // limpiar estado anterior
      form.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));

      // required normales
      const requiredFields = form.querySelectorAll('[required]');
      requiredFields.forEach(function(field){
        const type = (field.getAttribute('type') || '').toLowerCase();

        if (type === 'radio'){
          // radios por nombre
          const name = field.getAttribute('name');
          if (!name) return;
          const checked = form.querySelector(`input[type="radio"][name="${CSS.escape(name)}"]:checked`);
          if (!checked){
            ok = false;
            // marcar el primer radio del grupo
            setInvalid(field, true);
          }
          return;
        }

        if (type === 'checkbox'){
          // checkbox required no lo usas realmente aquí, pero por si acaso:
          if (!field.checked){
            ok = false;
            setInvalid(field, true);
          }
          return;
        }

        // text/textarea/number/tel
        const val = (field.value || '').trim();
        if (!val){
          ok = false;
          setInvalid(field, true);
        }
      });

      // Reglas extra: cédula 11 dígitos (si la escribieron completa)
      if (ced){
        const digits = (ced.value || '').replace(/\D/g,'');
        if (!digits || digits.length < 11){
          ok = false;
          setInvalid(ced, true);
        }
      }

      // Edad razonable
      if (edad){
        const n = parseInt(edad.value || '', 10);
        if (!edad.value || isNaN(n) || n <= 0){
          ok = false;
          setInvalid(edad, true);
        }
      }

      if (!ok){
        if (errorSummary) errorSummary.classList.remove('d-none');

        // scroll al primer inválido
        const firstInvalid = form.querySelector('.is-invalid');
        if (firstInvalid){
          firstInvalid.scrollIntoView({ behavior:'smooth', block:'center' });
        }
      }

      return ok;
    }

    // Submit
    form.addEventListener('submit', function (e) {
      // limpiar espacios
      softTrim(nombre); softTrim(tel); softTrim(ced);

      // normalizar teléfono + cédula
      if (tel) tel.value = normalizePhone(tel.value);
      if (ced) ced.value = formatCedula(ced.value);

      if (!validate()){
        e.preventDefault();
        return;
      }

      // UI enviando
      if (submitBtn) submitBtn.disabled = true;
      if (spinner) spinner.classList.remove('d-none');
      if (btnText) btnText.textContent = 'Enviando…';
    });
  });
})();