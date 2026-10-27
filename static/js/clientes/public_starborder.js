/* Decorative StarBorder speed-up; never handles activation or form state. */
(function () {
  'use strict';

  if (window.__publicStarBorderInitialized) return;
  window.__publicStarBorderInitialized = true;

  var selector = [
    '#publicServicePicker #publicContinueService',
    '.public-flow-page .public-submit-btn',
    '.public-flow-page #funciones_planchar_continue',
    '.share-landing-page #continueBtn'
  ].join(',');
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  var states = new WeakMap();
  var visibility = new WeakMap();
  var cruiseDuration = 4000;
  var fastRate = 3;
  var easeDuration = 320;
  var extraLapDuration = (
    cruiseDuration - (((fastRate + 1) / 2) - 1) * easeDuration
  ) / (fastRate - 1);

  function isDisabled(button) {
    return button.matches(':disabled, [aria-disabled="true"]');
  }

  function cruiseAnimations(button) {
    return button.getAnimations({ subtree: true }).filter(function (animation) {
      return animation.animationName === 'public-star-cruise';
    });
  }

  function cancelRestore(state) {
    if (state.timer) window.clearTimeout(state.timer);
    if (state.frame) window.cancelAnimationFrame(state.frame);
    state.timer = 0;
    state.frame = 0;
  }

  function restoreRate(button, state) {
    var animations = cruiseAnimations(button);
    var started = performance.now();
    function step(now) {
      var progress = Math.min(1, (now - started) / easeDuration);
      var easedRemaining = (1 - progress) * (1 - progress);
      var rate = 1 + (fastRate - 1) * easedRemaining;
      animations.forEach(function (animation) {
        if (typeof animation.updatePlaybackRate === 'function') {
          animation.updatePlaybackRate(rate);
        } else {
          animation.playbackRate = rate;
        }
      });
      if (progress < 1) state.frame = window.requestAnimationFrame(step);
      else state.frame = 0;
    }

    state.frame = window.requestAnimationFrame(step);
  }

  function accelerateOneLap(button) {
    if (reducedMotion.matches || isDisabled(button)) return;
    var animations = cruiseAnimations(button);
    if (!animations.length) return;

    var state = states.get(button);
    if (!state) {
      state = { timer: 0, frame: 0 };
      states.set(button, state);
    }
    cancelRestore(state);
    animations.forEach(function (animation) {
      if (typeof animation.updatePlaybackRate === 'function') {
        animation.updatePlaybackRate(fastRate);
      } else {
        animation.playbackRate = fastRate;
      }
    });
    state.timer = window.setTimeout(function () {
      state.timer = 0;
      restoreRate(button, state);
    }, extraLapDuration);
  }

  var buttons = Array.prototype.slice.call(document.querySelectorAll(selector));
  buttons.forEach(function (button) {
    button.addEventListener('pointerenter', function (event) {
      if (event.pointerType !== 'touch') accelerateOneLap(button);
    });
    button.addEventListener('focusin', function () {
      accelerateOneLap(button);
    });
  });

  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        visibility.set(entry.target, entry.isIntersecting);
        cruiseAnimations(entry.target).forEach(function (animation) {
          if (reducedMotion.matches || isDisabled(entry.target) || !entry.isIntersecting) {
            animation.pause();
          } else {
            animation.play();
          }
        });
      });
    }, { rootMargin: '48px' });
    buttons.forEach(function (button) { observer.observe(button); });
  }

  if (typeof reducedMotion.addEventListener === 'function') {
    reducedMotion.addEventListener('change', function (event) {
      buttons.forEach(function (button) {
        var state = states.get(button);
        if (state) cancelRestore(state);
        cruiseAnimations(button).forEach(function (animation) {
          if (event.matches || isDisabled(button) || visibility.get(button) === false) {
            animation.pause();
          } else {
            animation.play();
          }
        });
      });
    });
  }
}());
