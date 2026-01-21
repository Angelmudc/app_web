// static/js/core/live-refresh.js
// Auto-refresh silencioso de una parte del DOM (sin recargar la pagina).
// Requiere que el HTML tenga:
// - data-live-refresh="1"
// - data-refresh-url="/ruta/partial"
// - data-refresh-target="#idDelContenedor"
// Opcional:
// - data-refresh-interval="8000" (ms)

(function () {
  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  async function fetchText(url, signal) {
    const res = await fetch(url, {
      method: "GET",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html,*/*",
      },
      cache: "no-store",
      credentials: "same-origin",
      signal,
    });

    // Si el servidor redirige a login (muy comun), evitamos reventar el DOM
    // y dejamos que el user siga normal.
    if (res.redirected) {
      throw new Error("REDIRECT");
    }

    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.text();
  }

  function normalizeHTML(x) {
    return (x || "").replace(/\s+/g, " ").trim();
  }

  function sameHTML(a, b) {
    return normalizeHTML(a) === normalizeHTML(b);
  }

  function looksLikeFullDocument(html) {
    const t = (html || "").slice(0, 500).toLowerCase();
    return t.includes("<!doctype") || t.includes("<html") || t.includes("<head") || t.includes("<body");
  }

  function looksLikeLogin(html) {
    const t = (html || "").toLowerCase();
    // heuristica simple, no rompe nada si no aplica
    return t.includes("name=\"password\"") || t.includes("iniciar sesi") || t.includes("login");
  }

  async function startLiveRefresh(el) {
    const url = el.getAttribute("data-refresh-url");
    const targetSel = el.getAttribute("data-refresh-target");
    const baseInterval = parseInt(el.getAttribute("data-refresh-interval") || "8000", 10);

    if (!url || !targetSel) return;

    const target = document.querySelector(targetSel);
    if (!target) return;

    // Si el usuario esta escribiendo en un input dentro del target, no refrescamos
    function isUserTypingInsideTarget() {
      const active = document.activeElement;
      if (!active) return false;
      const tag = (active.tagName || "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || active.isContentEditable) {
        return target.contains(active);
      }
      return false;
    }

    let lastHTML = target.innerHTML;

    // Backoff si hay errores (para no spamear el server)
    let errorStreak = 0;

    // Abort controller por ciclo (evita requests colgados)
    let controller = null;

    // Si el usuario navega, cancelamos
    window.addEventListener("beforeunload", function () {
      try {
        if (controller) controller.abort();
      } catch (_) {}
    });

    while (true) {
      // Si el nodo se removio del DOM, paramos el loop
      if (!document.contains(el) || !document.contains(target)) return;

      const interval = clamp(baseInterval + errorStreak * 2000, 4000, 30000);
      await sleep(interval);

      if (document.hidden) continue;
      if (isUserTypingInsideTarget()) continue;

      try {
        // cache-buster
        const bust = (url.includes("?") ? "&" : "?") + "_ts=" + Date.now();

        controller = new AbortController();
        const html = await fetchText(url + bust, controller.signal);

        // Evitar que por accidente metamos una pagina completa dentro del div
        if (looksLikeFullDocument(html) || looksLikeLogin(html)) {
          // Si esto pasa, lo mas probable es que el endpoint no es partial o la sesion expiro
          // No tocamos el DOM.
          errorStreak = clamp(errorStreak + 1, 0, 10);
          continue;
        }

        if (!sameHTML(lastHTML, html)) {
          target.innerHTML = html;
          lastHTML = html;
        }

        errorStreak = 0;
      } catch (e) {
        // Silencioso
        errorStreak = clamp(errorStreak + 1, 0, 10);
      } finally {
        controller = null;
      }
    }
  }

  function init() {
    const nodes = document.querySelectorAll("[data-live-refresh='1']");
    nodes.forEach((el) => startLiveRefresh(el));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();