(function () {
  "use strict";

  function initSecretariasSolicitudesAsync() {
    const form = document.querySelector('form[data-secretarias-async="1"]');
    const results = document.getElementById("secretariasSolicitudesResults");
    if (!form || !results || !window.fetch) return;

    const busyButtonsSelector = "button, input[type='submit'], a.btn";
    const debounceFields = new Set(["q", "ciudad_sector", "ruta", "experiencia", "modalidad", "edad_texto"]);
    const cache = new Map();
    const CACHE_TTL_MS = 90000;
    const metrics = (window.__secretariasAsyncMetrics = window.__secretariasAsyncMetrics || {
      cacheHits: 0,
      fetchLoads: 0,
      instantRestores: 0,
    });
    let currentController = null;
    let debounceTimer = null;

    function activeSelector() {
      const active = document.activeElement;
      if (!active || active === document.body) return "";
      if (active.id) return `#${active.id}`;
      const name = String(active.getAttribute && active.getAttribute("name") || "").trim();
      return name ? `${active.tagName.toLowerCase()}[name="${name.replace(/"/g, '\\"')}"]` : "";
    }

    function cacheGet(url) {
      const key = String(url || "").trim();
      if (!key) return null;
      const hit = cache.get(key);
      if (!hit) return null;
      if ((Date.now() - Number(hit.ts || 0)) > CACHE_TTL_MS) {
        cache.delete(key);
        return null;
      }
      return hit;
    }

    function cacheSet(url, html) {
      const key = String(url || "").trim();
      if (!key || typeof html !== "string") return;
      cache.set(key, { html, ts: Date.now() });
      if (cache.size > 30) {
        let oldestKey = "";
        let oldestTs = Infinity;
        cache.forEach((entry, k) => {
          const ts = Number(entry && entry.ts) || 0;
          if (ts < oldestTs) {
            oldestTs = ts;
            oldestKey = k;
          }
        });
        if (oldestKey) cache.delete(oldestKey);
      }
    }

    function captureSnapshot() {
      const openDetailIds = [];
      const loadedTexts = [];
      results.querySelectorAll("tr[id^='drow-']").forEach((row) => {
        if (row.hidden) return;
        openDetailIds.push(row.id);
        const id = row.id.replace("drow-", "");
        const pre = document.getElementById(`t${id}`);
        if (pre && pre.dataset.loaded === "1") {
          loadedTexts.push({ id, html: String(pre.innerHTML || "") });
        }
      });
      return { openDetailIds, loadedTexts };
    }

    function restoreSnapshot(snapshot) {
      const openDetailIds = Array.isArray(snapshot && snapshot.openDetailIds) ? snapshot.openDetailIds : [];
      openDetailIds.forEach((id) => {
        const row = document.getElementById(id);
        if (row) row.hidden = false;
      });
      const loadedTexts = Array.isArray(snapshot && snapshot.loadedTexts) ? snapshot.loadedTexts : [];
      loadedTexts.forEach((entry) => {
        const pre = document.getElementById(`t${entry.id}`);
        if (!pre) return;
        pre.innerHTML = entry.html;
        pre.dataset.loaded = "1";
      });
    }

    function setBusy(isBusy) {
      results.setAttribute("aria-busy", isBusy ? "true" : "false");
      results.classList.toggle("is-loading", isBusy);
      form.querySelectorAll(busyButtonsSelector).forEach((el) => {
        if (isBusy) {
          el.dataset._prevDisabled = el.disabled ? "1" : "0";
          el.disabled = true;
        } else {
          if (el.dataset._prevDisabled === "0") el.disabled = false;
          delete el.dataset._prevDisabled;
        }
      });
    }

    function swapHtmlSmooth(html, preserveScroll) {
      const snapshot = captureSnapshot();
      const beforeRect = results.getBoundingClientRect();
      const beforeScrollY = window.scrollY || window.pageYOffset || 0;
      const beforeHeight = Math.max(0, results.offsetHeight || 0);
      if (beforeHeight > 0) results.style.minHeight = `${beforeHeight}px`;
      results.style.opacity = "0.72";
      results.style.transition = "opacity 120ms ease";
      results.innerHTML = html;
      window.requestAnimationFrame(() => {
        restoreSnapshot(snapshot);
        results.style.opacity = "1";
        results.style.minHeight = "";
        if (preserveScroll) {
          const afterRect = results.getBoundingClientRect();
          const delta = afterRect.top - beforeRect.top;
          if (Math.abs(delta) > 1) {
            window.scrollTo({ top: beforeScrollY + delta, behavior: "auto" });
          }
        }
      });
    }

    function buildUrl() {
      const action = form.getAttribute("action") || window.location.pathname;
      const data = new FormData(form);
      const qs = new URLSearchParams();
      data.forEach((value, key) => {
        if (value === null || value === undefined) return;
        const text = String(value);
        if (!text.trim()) return;
        qs.append(key, text);
      });
      const query = qs.toString();
      return query ? `${action}?${query}` : action;
    }

    function syncFormWithUrl(url) {
      const u = new URL(url, window.location.origin);
      const params = u.searchParams;
      Array.from(form.elements || []).forEach((el) => {
        if (!el.name) return;
        if (el.type === "checkbox") {
          const values = params.getAll(el.name);
          el.checked = values.includes(el.value);
          return;
        }
        if (el.tagName === "SELECT" || el.tagName === "INPUT") {
          el.value = params.get(el.name) || "";
        }
      });
    }

    async function fetchAndRender(url, pushHistory, opts) {
      const preserveScroll = !(opts && opts.preserveScroll === false);
      const allowCached = !!(opts && opts.allowCached);
      const cached = allowCached ? cacheGet(url) : null;
      if (cached && typeof cached.html === "string") {
        swapHtmlSmooth(cached.html, preserveScroll);
        if (pushHistory) {
          history.pushState({ secretariasAsync: 1, html: cached.html, url, scrollY: window.scrollY || 0, activeSel: activeSelector() }, "", url);
        }
        metrics.cacheHits += 1;
        return;
      }

      if (currentController) currentController.abort();
      currentController = new AbortController();
      setBusy(true);

      try {
        const resp = await fetch(url, {
          method: "GET",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
          },
          signal: currentController.signal,
        });
        if (!resp.ok) throw new Error(`http_${resp.status}`);
        const html = await resp.text();
        metrics.fetchLoads += 1;
        swapHtmlSmooth(html, preserveScroll);
        cacheSet(url, html);
        if (pushHistory) {
          history.pushState({ secretariasAsync: 1, html, url, scrollY: window.scrollY || 0, activeSel: activeSelector() }, "", url);
        } else {
          history.replaceState({ secretariasAsync: 1, html, url, scrollY: window.scrollY || 0, activeSel: activeSelector() }, "", url);
        }
      } catch (err) {
        if (err && err.name === "AbortError") return;
        results.innerHTML = '<div class="alert alert-warning">No se pudo cargar resultados ahora. Intenta de nuevo.</div>';
      } finally {
        setBusy(false);
      }
    }

    function triggerDebouncedSubmit() {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        fetchAndRender(buildUrl(), true, { preserveScroll: true, allowCached: false });
      }, 300);
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      fetchAndRender(buildUrl(), true, { preserveScroll: true, allowCached: false });
    });

    form.addEventListener("input", function (ev) {
      const target = ev.target;
      if (!target || !target.name) return;
      if (debounceFields.has(target.name)) {
        triggerDebouncedSubmit();
      }
    });

    form.addEventListener("change", function (ev) {
      const target = ev.target;
      if (!target || !target.name) return;
      if (target.type === "checkbox" || target.tagName === "SELECT") {
        fetchAndRender(buildUrl(), true, { preserveScroll: true, allowCached: false });
      }
    });

    results.addEventListener("click", function (ev) {
      const link = ev.target && ev.target.closest ? ev.target.closest("a.page-link") : null;
      if (!link) return;
      const href = link.getAttribute("href") || "";
      if (!href || href === "#") return;
      ev.preventDefault();
      fetchAndRender(href, true, { preserveScroll: true, allowCached: true });
    });

    window.addEventListener("popstate", function (ev) {
      const state = ev.state || {};
      syncFormWithUrl(window.location.href);
      if (state.secretariasAsync && typeof state.html === "string") {
        swapHtmlSmooth(state.html, true);
        cacheSet(state.url || window.location.href, state.html);
        if (Number.isFinite(Number(state.scrollY))) {
          window.scrollTo({ top: Math.max(0, Number(state.scrollY) || 0), behavior: "auto" });
        }
        if (state.activeSel) {
          const focusEl = document.querySelector(String(state.activeSel));
          if (focusEl && typeof focusEl.focus === "function") {
            try { focusEl.focus({ preventScroll: true }); } catch (_) {}
          }
        }
        metrics.instantRestores += 1;
        return;
      }
      fetchAndRender(window.location.href, false, { preserveScroll: true, allowCached: true });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSecretariasSolicitudesAsync);
  } else {
    initSecretariasSolicitudesAsync();
  }
})();
