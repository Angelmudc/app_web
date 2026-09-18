(function (window, document) {
  'use strict';

  var TOLERANCE = 10;
  var controllers = [];
  var fallbackBackdrop = null;

  function nextFrame(callback) {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(callback);
    });
  }

  function createController(form, modal) {
    var body = modal.querySelector('.employment-conditions-body');
    var confirmButton = modal.querySelector('.employment-conditions-confirm');
    var indicator = modal.querySelector('.employment-conditions-read-more');
    var bootstrapModal = null;
    var fallbackOpen = false;
    var active = false;
    var readCompleted = false;
    var hasOverflow = false;
    var measuredOverflow = false;
    var resizeQueued = false;

    if (!form || !modal || !body || !confirmButton) return null;

    function setConfirmEnabled(enabled) {
      confirmButton.disabled = !enabled;
      confirmButton.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    }

    function renderIndicator() {
      var visible = active && hasOverflow && !readCompleted;
      if (!indicator) return;
      indicator.hidden = !visible;
      indicator.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    function measure() {
      if (!body || !active) return;
      var nextOverflow = body.scrollHeight > body.clientHeight + TOLERANCE;
      if (nextOverflow && !measuredOverflow) readCompleted = false;
      hasOverflow = nextOverflow;
      measuredOverflow = nextOverflow;
      if (!hasOverflow) {
        readCompleted = true;
        setConfirmEnabled(true);
      } else if (!readCompleted) {
        setConfirmEnabled(false);
      }
      renderIndicator();
    }

    function handleScroll() {
      if (!active || !body || readCompleted) return;
      if (body.scrollTop + body.clientHeight >= body.scrollHeight - TOLERANCE) {
        readCompleted = true;
        setConfirmEnabled(true);
        renderIndicator();
      }
    }

    function queueMeasure() {
      if (resizeQueued) return;
      resizeQueued = true;
      window.requestAnimationFrame(function () {
        resizeQueued = false;
        measure();
      });
    }

    function focusInitialControl() {
      if (!active) return;
      if (hasOverflow && !readCompleted && indicator) indicator.focus();
      else confirmButton.focus();
    }

    function resetForOpen() {
      active = true;
      readCompleted = false;
      hasOverflow = false;
      measuredOverflow = false;
      body.scrollTop = 0;
      setConfirmEnabled(false);
      renderIndicator();
      nextFrame(function () {
        measure();
        focusInitialControl();
      });
    }

    function removeFallbackBackdrop() {
      if (fallbackBackdrop && fallbackBackdrop.parentNode) fallbackBackdrop.parentNode.removeChild(fallbackBackdrop);
      fallbackBackdrop = null;
    }

    function closeFallback() {
      fallbackOpen = false;
      active = false;
      modal.classList.remove('public-conditions-fallback-open', 'show');
      modal.style.display = 'none';
      modal.setAttribute('aria-hidden', 'true');
      removeFallbackBackdrop();
    }

    function close() {
      if (bootstrapModal) bootstrapModal.hide();
      else if (fallbackOpen) closeFallback();
    }

    function openFallback() {
      fallbackOpen = true;
      modal.classList.add('public-conditions-fallback-open', 'show');
      modal.style.display = 'block';
      modal.setAttribute('aria-hidden', 'false');
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      if (!fallbackBackdrop) {
        fallbackBackdrop = document.createElement('div');
        fallbackBackdrop.className = 'public-conditions-fallback-backdrop';
        fallbackBackdrop.addEventListener('click', close);
        document.body.appendChild(fallbackBackdrop);
      }
      resetForOpen();
    }

    function open() {
      if (bootstrapModal) {
        resetForOpen();
        bootstrapModal.show();
      } else {
        openFallback();
      }
    }

    function confirm() {
      if (confirmButton.disabled || form.__publicConditionsConfirmed) return;
      form.__publicConditionsConfirmed = true;
      if (bootstrapModal) bootstrapModal.hide();
      else closeFallback();
      window.setTimeout(function () {
        form.requestSubmit();
      }, 0);
    }

    function onKeydown(event) {
      if (!active) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === 'Enter' && confirmButton.disabled && event.target !== indicator) {
        event.preventDefault();
      }
    }

    function attachBootstrap() {
      if (bootstrapModal || !(window.bootstrap && window.bootstrap.Modal)) return;
      bootstrapModal = window.bootstrap.Modal.getOrCreateInstance(modal);
      modal.addEventListener('shown.bs.modal', function () {
        resetForOpen();
      });
      modal.addEventListener('hidden.bs.modal', function () {
        active = false;
        renderIndicator();
      });
    }

    body.addEventListener('scroll', handleScroll, { passive: true });
    confirmButton.addEventListener('click', confirm);
    modal.addEventListener('keydown', onKeydown);
    if (indicator) {
      indicator.addEventListener('click', function () {
        body.scrollTo({ top: body.scrollHeight, behavior: 'smooth' });
      });
    }
    modal.querySelectorAll('[data-bs-dismiss="modal"]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (!bootstrapModal) closeFallback();
      });
    });
    window.addEventListener('resize', queueMeasure, { passive: true });
    window.addEventListener('orientationchange', queueMeasure, { passive: true });
    if (window.ResizeObserver) {
      new window.ResizeObserver(queueMeasure).observe(body);
    }

    return {
      open: open,
      close: close,
      confirm: confirm,
      attachBootstrap: attachBootstrap,
      isReadCompleted: function () { return readCompleted; },
      hasOverflow: function () { return hasOverflow; },
    };
  }

  function setup(form) {
    if (!form || form.__publicConditionsController) return form && form.__publicConditionsController;
    var modalId = form.getAttribute('data-conditions-modal-id');
    var modal = modalId && document.getElementById(modalId);
    if (!modal) return null;
    var controller = createController(form, modal);
    if (controller) {
      form.__publicConditionsController = controller;
      controllers.push(controller);
      controller.attachBootstrap();
    }
    return controller;
  }

  window.PublicConditionsModal = {
    setup: setup,
    attachBootstrapAll: function () {
      controllers.forEach(function (controller) { controller.attachBootstrap(); });
    },
  };

  document.querySelectorAll('form[data-conditions-modal-id]').forEach(setup);
})(window, document);
