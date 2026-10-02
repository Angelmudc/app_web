(function (window, document) {
  'use strict';

  var TOLERANCE = 10;
  var controllers = [];

  function createController(form, modal) {
    var dialog = modal.querySelector('.employment-conditions-dialog');
    var body = modal.querySelector('.employment-conditions-body');
    var confirmButton = modal.querySelector('.employment-conditions-confirm');
    var indicator = modal.querySelector('.employment-conditions-read-more');
    var dismissButtons = modal.querySelectorAll('[data-public-conditions-dismiss]');
    var active = false;
    var readCompleted = false;
    var hasOverflow = false;
    var measuredOverflow = false;
    var resizeQueued = false;
    var trigger = null;
    var previousFocus = null;
    var focusableSelector = 'a[href], area[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    if (!form || !modal || !dialog || !body || !confirmButton) return null;

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
      if (!active) return;
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
      if (!active || readCompleted) return;
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

    function focusWithoutDocumentScroll(element) {
      if (element && typeof element.focus === 'function') element.focus({ preventScroll: true });
    }

    function focusInitialControl() {
      focusWithoutDocumentScroll(hasOverflow && !readCompleted && indicator ? indicator : confirmButton);
    }

    function focusables() {
      return Array.prototype.filter.call(modal.querySelectorAll(focusableSelector), function (element) {
        return !element.hidden && element.offsetParent !== null;
      });
    }

    function isScrollKey(event) {
      return ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].indexOf(event.key) !== -1;
    }

    function onKeydown(event) {
      if (!active) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === 'Tab') {
        var elements = focusables();
        if (!elements.length) return;
        var first = elements[0];
        var last = elements[elements.length - 1];
        if (event.shiftKey && event.target === first) {
          event.preventDefault();
          focusWithoutDocumentScroll(last);
        } else if (!event.shiftKey && event.target === last) {
          event.preventDefault();
          focusWithoutDocumentScroll(first);
        }
        return;
      }
      if (event.key === 'Enter' && confirmButton.disabled && event.target !== indicator) {
        event.preventDefault();
        return;
      }
      if (isScrollKey(event) && !body.contains(event.target)) event.preventDefault();
    }

    function onWheelOrTouchMove(event) {
      if (active && !body.contains(event.target)) event.preventDefault();
    }

    function onBackdropClick(event) {
      if (event.target === modal) close();
    }

    function addActiveListeners() {
      modal.addEventListener('keydown', onKeydown, true);
      modal.addEventListener('wheel', onWheelOrTouchMove, { passive: false });
      modal.addEventListener('touchmove', onWheelOrTouchMove, { passive: false });
      modal.addEventListener('click', onBackdropClick);
    }

    function removeActiveListeners() {
      modal.removeEventListener('keydown', onKeydown, true);
      modal.removeEventListener('wheel', onWheelOrTouchMove, { passive: false });
      modal.removeEventListener('touchmove', onWheelOrTouchMove, { passive: false });
      modal.removeEventListener('click', onBackdropClick);
    }

    function resetForOpen() {
      active = true;
      readCompleted = false;
      hasOverflow = false;
      measuredOverflow = false;
      body.scrollTop = 0;
      setConfirmEnabled(false);
      modal.classList.add('public-conditions-open');
      modal.style.display = 'grid';
      modal.setAttribute('aria-hidden', 'false');
      addActiveListeners();
      measure();
      renderIndicator();
      focusInitialControl();
      queueMeasure();
    }

    function close() {
      if (!active) return;
      active = false;
      removeActiveListeners();
      modal.classList.remove('public-conditions-open');
      modal.style.display = 'none';
      modal.setAttribute('aria-hidden', 'true');
      renderIndicator();
      focusWithoutDocumentScroll(trigger || previousFocus);
      trigger = null;
      previousFocus = null;
    }

    function open() {
      if (active) return;
      trigger = form.querySelector('button[type="submit"]');
      previousFocus = document.activeElement;
      resetForOpen();
    }

    function confirm() {
      if (confirmButton.disabled || form.__publicConditionsConfirmed) return;
      form.__publicConditionsConfirmed = true;
      close();
      window.setTimeout(function () { form.requestSubmit(); }, 0);
    }

    body.addEventListener('scroll', handleScroll, { passive: true });
    confirmButton.addEventListener('click', confirm);
    if (indicator) indicator.addEventListener('click', function () { body.scrollTop = body.scrollHeight; });
    Array.prototype.forEach.call(dismissButtons, function (button) { button.addEventListener('click', close); });
    form.addEventListener('submit', function (event) {
      if (form.__publicConditionsConfirmed && event.defaultPrevented) form.__publicConditionsConfirmed = false;
    });
    window.addEventListener('resize', queueMeasure, { passive: true });
    window.addEventListener('orientationchange', queueMeasure, { passive: true });
    if (window.ResizeObserver) new window.ResizeObserver(queueMeasure).observe(body);

    return {
      open: open,
      close: close,
      confirm: confirm,
      attachBootstrap: function () {},
      isReadCompleted: function () { return readCompleted; },
      hasOverflow: function () { return hasOverflow; },
      isActive: function () { return active; },
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
    }
    return controller;
  }

  window.PublicConditionsModal = { setup: setup, attachBootstrapAll: function () {} };
  document.querySelectorAll('form[data-conditions-modal-id]').forEach(setup);
})(window, document);
