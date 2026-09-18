(function () {
  'use strict';

  var hint = document.getElementById('readingHint');
  var progress = document.getElementById('readingProgress');
  var ticking = false;
  var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (!hint) return;

  function updateReadingState() {
    ticking = false;
    var doc = document.documentElement;
    var scrollable = doc.scrollHeight > window.innerHeight + 24;
    var nearEnd = window.scrollY + window.innerHeight >= doc.scrollHeight - 80;
    hint.hidden = !scrollable || nearEnd;
    if (progress) {
      var max = Math.max(1, doc.scrollHeight - window.innerHeight);
      progress.style.width = Math.min(100, Math.max(0, (window.scrollY / max) * 100)) + '%';
    }
  }

  function scheduleUpdate() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(updateReadingState);
  }

  hint.addEventListener('click', function () {
    window.scrollTo({
      top: Math.min(document.documentElement.scrollHeight, window.scrollY + Math.max(window.innerHeight * 0.72, 280)),
      behavior: reducedMotion ? 'auto' : 'smooth'
    });
  });
  window.addEventListener('scroll', scheduleUpdate, { passive: true });
  window.addEventListener('resize', scheduleUpdate, { passive: true });
  window.addEventListener('orientationchange', scheduleUpdate, { passive: true });
  if (window.ResizeObserver) new window.ResizeObserver(scheduleUpdate).observe(document.documentElement);
  updateReadingState();
})();
