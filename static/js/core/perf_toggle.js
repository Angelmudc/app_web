// static/js/core/perf_toggle.js
// Toggle de modo rendimiento para admin shell.
(function () {
  "use strict";

  const KEY = "perf_mode";

  function init() {
    const body = document.body;
    const btn = document.getElementById("perfToggle");
    if (!body || !btn) return;

    let stored = "";
    try {
      stored = String(window.localStorage.getItem(KEY) || "");
    } catch (_e) {
      stored = "";
    }

    if (stored === "on") {
      body.classList.add("perf");
      btn.classList.add("active");
    }

    btn.addEventListener("click", function () {
      const on = body.classList.toggle("perf");
      try {
        window.localStorage.setItem(KEY, on ? "on" : "off");
      } catch (_e) {}
      btn.classList.toggle("active", on);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
