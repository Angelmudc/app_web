(function () {
  "use strict";

  var STORAGE_KEY = "adminFloatingIslandsHidden";
  var body = document.body;
  if (!body) return;

  var shell = document.getElementById("adminFloatingIslandsShell");
  var toggleBtn = document.getElementById("adminFloatingIslandsToggle");
  var toggleText = document.getElementById("adminFloatingIslandsToggleText");
  var icon = toggleBtn ? toggleBtn.querySelector("i") : null;
  if (!shell || !toggleBtn) return;

  function readHiddenState() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === "1";
    } catch (_err) {
      return body.classList.contains("is-floating-islands-hidden");
    }
  }

  function persistHiddenState(hidden) {
    try {
      window.localStorage.setItem(STORAGE_KEY, hidden ? "1" : "0");
    } catch (_err) {}
  }

  function syncToggleUi(hidden) {
    var label = hidden ? "Mostrar accesos rápidos" : "Ocultar accesos rápidos";
    toggleBtn.setAttribute("aria-label", label);
    toggleBtn.setAttribute("title", label);
    toggleBtn.setAttribute("aria-expanded", hidden ? "false" : "true");
    if (toggleText) toggleText.textContent = label;
    if (icon) {
      icon.classList.toggle("fa-eye", hidden);
      icon.classList.toggle("fa-eye-slash", !hidden);
    }
  }

  function closeSeguimientoDrawerIfOpen() {
    var segBtn = document.getElementById("segCandidatasIslandBtn");
    if (!segBtn || segBtn.getAttribute("aria-expanded") !== "true") return;
    var closeBtn = document.getElementById("segCandidatasCloseBtn");
    if (closeBtn && typeof closeBtn.click === "function") {
      closeBtn.click();
      return;
    }
    if (typeof segBtn.click === "function") segBtn.click();
  }

  function applyHiddenState(hidden, options) {
    if (hidden) closeSeguimientoDrawerIfOpen();
    body.classList.toggle("is-floating-islands-hidden", hidden);
    shell.classList.toggle("is-hidden", hidden);
    syncToggleUi(hidden);
    if (!options || options.persist !== false) {
      persistHiddenState(hidden);
    }
  }

  toggleBtn.addEventListener("click", function () {
    applyHiddenState(!body.classList.contains("is-floating-islands-hidden"));
  });

  applyHiddenState(readHiddenState(), { persist: false });
})();
