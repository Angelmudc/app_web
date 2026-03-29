// static/js/core/admin_nav.js
// Navegacion parcial PJAX liviana (piloto opt-in).
(function () {
  "use strict";

  if (window.AdminNav) return;

  const VIEWPORT_SELECTOR = "[data-admin-nav-viewport='true']";
  const LINK_SELECTOR = "a[data-admin-nav='true']";
  const STATE_KEY = "__admin_pjax_pilot";
  const LOADING_CLASS = "is-admin-nav-loading";
  const PROGRESS_ID = "adminNavProgressBar";
  const PROGRESS_ACTIVE_CLASS = "is-active";
  const FALLBACK_EVENT = "admin:navigation-fallback";
  let isNavigating = false;
  let pendingPopstate = null;

  function isPilotPath(pathname) {
    const path = String(pathname || "");
    if (/^\/admin\/solicitudes\/?$/.test(path)) return true;
    if (/^\/admin\/clientes\/\d+\/?$/.test(path)) return true;
    if (/^\/admin\/clientes\/\d+\/solicitudes\/\d+\/?$/.test(path)) return true;
    return false;
  }

  function getViewport(doc) {
    return (doc || document).querySelector(VIEWPORT_SELECTOR);
  }

  function parseHtml(text) {
    const parser = new DOMParser();
    return parser.parseFromString(String(text || ""), "text/html");
  }

  function fallbackLoad(url) {
    window.location.assign(url);
  }

  function getCurrentScrollY() {
    return Math.max(0, Number(window.scrollY || window.pageYOffset || 0));
  }

  function updateCurrentHistoryScrollY() {
    const state = (history.state && typeof history.state === "object") ? history.state : {};
    history.replaceState({ ...state, scrollY: getCurrentScrollY() }, "", window.location.href);
  }

  function ensureProgressBar() {
    let bar = document.getElementById(PROGRESS_ID);
    if (bar) return bar;
    bar = document.createElement("div");
    bar.id = PROGRESS_ID;
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    return bar;
  }

  function setLoadingUi(loading) {
    const root = document.documentElement;
    const body = document.body;
    const bar = ensureProgressBar();
    if (loading) {
      root.classList.add(LOADING_CLASS);
      if (body) body.classList.add(LOADING_CLASS);
      bar.classList.add(PROGRESS_ACTIVE_CLASS);
      return;
    }
    root.classList.remove(LOADING_CLASS);
    if (body) body.classList.remove(LOADING_CLASS);
    bar.classList.remove(PROGRESS_ACTIVE_CLASS);
  }

  function clearModalState() {
    try {
      document.querySelectorAll(".modal").forEach((modalEl) => {
        if (!(window.bootstrap && window.bootstrap.Modal)) return;
        const instance = window.bootstrap.Modal.getInstance(modalEl);
        if (instance && typeof instance.hide === "function") instance.hide();
        if (instance && typeof instance.dispose === "function") instance.dispose();
      });
    } catch (_) {}

    try {
      document.querySelectorAll(".modal-backdrop").forEach((node) => node.remove());
      document.documentElement.classList.remove("modal-open");
      document.body.classList.remove("modal-open");
      document.documentElement.style.removeProperty("overflow");
      document.body.style.removeProperty("overflow");
      document.documentElement.style.removeProperty("padding-right");
      document.body.style.removeProperty("padding-right");
    } catch (_) {}
  }

  function dispatchNavigationComplete(detail) {
    const eventDetail = detail || {};
    document.dispatchEvent(new CustomEvent("admin:content-updated", {
      detail: {
        targetSelector: VIEWPORT_SELECTOR,
        container: eventDetail.viewport || getViewport(document),
        source: "admin-nav",
      },
    }));
    document.dispatchEvent(new CustomEvent("admin:navigation-complete", {
      detail: eventDetail,
    }));
  }

  function getHashTarget(finalUrl) {
    const hash = (new URL(finalUrl, window.location.origin)).hash || "";
    if (!hash || hash.length < 2) return null;
    return document.getElementById(hash.slice(1));
  }

  function getStickyTopOffset() {
    const nav = document.querySelector(".navbar");
    if (!nav) return 0;
    const style = window.getComputedStyle(nav);
    if (!style) return 0;
    const isTopFixed = style.position === "fixed" || style.position === "sticky";
    if (!isTopFixed) return 0;
    return Math.max(0, Math.ceil(nav.getBoundingClientRect().height || 0) + 8);
  }

  function scrollToHashTarget(target) {
    if (!target) return false;
    const offset = getStickyTopOffset();
    const y = Math.max(0, Math.floor((target.getBoundingClientRect().top + window.pageYOffset) - offset));
    window.scrollTo({ top: y, left: 0, behavior: "auto" });
    return true;
  }

  function setFocusTemporarily(el) {
    if (!el || typeof el.focus !== "function") return;
    const hadTabindex = el.hasAttribute("tabindex");
    if (!hadTabindex) {
      el.setAttribute("tabindex", "-1");
    }
    try { el.focus({ preventScroll: true }); } catch (_) {}
    if (!hadTabindex) {
      window.setTimeout(() => {
        try { el.removeAttribute("tabindex"); } catch (_) {}
      }, 0);
    }
  }

  function hasActiveEditableElement() {
    const active = document.activeElement;
    if (!active || active === document.body) return false;
    if (!active.isConnected) return false;
    if (active.matches && active.matches("input, textarea, select, [contenteditable='true']")) {
      return true;
    }
    return false;
  }

  function resolveFocusTarget(viewport, hashTarget) {
    if (hashTarget) return hashTarget;
    const heading = viewport && viewport.querySelector ? viewport.querySelector("h1, h2") : null;
    if (heading) return heading;
    const anchor = viewport && viewport.querySelector ? viewport.querySelector("[data-admin-focus-anchor]") : null;
    if (anchor) return anchor;
    return viewport || null;
  }

  function applyScrollPolicy(finalUrl, viewport, options) {
    const opts = options || {};
    const hashTarget = getHashTarget(finalUrl);

    if (hashTarget) {
      scrollToHashTarget(hashTarget);
    } else if (opts.fromPopstate) {
      if (Number.isFinite(opts.restoreScrollY)) {
        window.scrollTo({ top: Math.max(0, Number(opts.restoreScrollY) || 0), left: 0, behavior: "auto" });
      }
    } else {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }

    if (hasActiveEditableElement()) return;
    const focusTarget = resolveFocusTarget(viewport, hashTarget);
    setFocusTemporarily(focusTarget);
  }

  function emitFallback(reason, toUrl) {
    const fromUrl = window.location.href;
    const detail = {
      reason: String(reason || "unknown"),
      from: fromUrl,
      to: String(toUrl || ""),
    };
    try {
      console.warn("[AdminNav:fallback]", detail);
    } catch (_) {}
    document.dispatchEvent(new CustomEvent(FALLBACK_EVENT, { detail }));
  }

  function shouldForceFullRedirect(resp, doc) {
    if (!resp) return true;
    if (resp.status === 401 || resp.status === 403) return true;
    if (!resp.ok) return true;

    const finalUrl = new URL(resp.url || window.location.href, window.location.origin);
    if (/^\/admin\/login\/?$/.test(finalUrl.pathname)) return true;

    const hasLoginForm = !!(doc && doc.querySelector("form[action*='/admin/login']"));
    return hasLoginForm;
  }

  async function navigateTo(url, options) {
    const opts = options || {};
    const requestedUrl = String(url || "").trim();
    if (!requestedUrl) return false;

    if (isNavigating) {
      if (opts.fromPopstate) {
        pendingPopstate = {
          url: requestedUrl,
          restoreScrollY: opts.restoreScrollY,
        };
      }
      return false;
    }
    isNavigating = true;
    setLoadingUi(true);

    try {
      const currentViewport = getViewport(document);
      if (!currentViewport) {
        emitFallback("missing_viewport", requestedUrl);
        fallbackLoad(requestedUrl);
        return false;
      }

      const response = await fetch(requestedUrl, {
        method: "GET",
        credentials: "same-origin",
        redirect: "follow",
        headers: {
          "Accept": "text/html,application/xhtml+xml",
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      const finalUrl = response.url || requestedUrl;
      const text = await response.text();
      const nextDoc = parseHtml(text);

      if (shouldForceFullRedirect(response, nextDoc)) {
        const reason = response.status === 401
          ? "http_401"
          : response.status === 403
            ? "http_403"
            : "auth_or_incompatible_response";
        emitFallback(reason, finalUrl);
        fallbackLoad(finalUrl);
        return false;
      }

      const nextViewport = getViewport(nextDoc);
      if (!nextViewport) {
        emitFallback("missing_target_viewport", finalUrl);
        fallbackLoad(finalUrl);
        return false;
      }

      clearModalState();
      currentViewport.style.opacity = "0.78";
      currentViewport.style.transition = "opacity 100ms ease";
      currentViewport.innerHTML = nextViewport.innerHTML;
      window.requestAnimationFrame(() => {
        currentViewport.style.opacity = "1";
      });

      const nextTitle = (nextDoc.title || "").trim();
      if (nextTitle) {
        document.title = nextTitle;
      }

      const state = {
        ...(history.state && typeof history.state === "object" ? history.state : {}),
        [STATE_KEY]: true,
        url: finalUrl,
        scrollY: opts.fromPopstate && Number.isFinite(opts.restoreScrollY)
          ? Math.max(0, Number(opts.restoreScrollY) || 0)
          : 0,
      };
      if (opts.fromPopstate) {
        history.replaceState(state, "", finalUrl);
      } else if (opts.replaceState) {
        history.replaceState(state, "", finalUrl);
      } else {
        history.pushState(state, "", finalUrl);
      }

      applyScrollPolicy(finalUrl, currentViewport, {
        fromPopstate: !!opts.fromPopstate,
        restoreScrollY: opts.restoreScrollY,
      });
      dispatchNavigationComplete({
        url: finalUrl,
        title: document.title,
        viewport: currentViewport,
        fromPopstate: !!opts.fromPopstate,
      });
      return true;
    } catch (_) {
      emitFallback("fetch_or_parse_error", requestedUrl);
      fallbackLoad(requestedUrl);
      return false;
    } finally {
      isNavigating = false;
      setLoadingUi(false);
      if (pendingPopstate && pendingPopstate.url) {
        const queued = pendingPopstate;
        pendingPopstate = null;
        navigateTo(queued.url, {
          fromPopstate: true,
          replaceState: true,
          restoreScrollY: queued.restoreScrollY,
        });
      }
    }
  }

  function shouldHandleLink(link, ev) {
    if (!link || !ev) return false;
    if (ev.defaultPrevented) return false;
    if (ev.button !== 0) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    if ((link.getAttribute("target") || "").toLowerCase() === "_blank") return false;
    if (link.hasAttribute("download")) return false;

    const href = link.getAttribute("href") || "";
    if (!href || href === "#" || href.startsWith("javascript:")) return false;

    const to = new URL(href, window.location.origin);
    if (to.origin !== window.location.origin) return false;
    return isPilotPath(to.pathname);
  }

  function onClick(ev) {
    const link = ev.target && ev.target.closest ? ev.target.closest(LINK_SELECTOR) : null;
    if (!shouldHandleLink(link, ev)) return;

    ev.preventDefault();
    updateCurrentHistoryScrollY();
    navigateTo(link.href);
  }

  function onPopstate(ev) {
    const state = ev && ev.state;
    if (!state || state[STATE_KEY] !== true) return;
    const stateUrl = String(state.url || "").trim();
    if (!stateUrl) return;
    let pathname = "";
    try {
      pathname = new URL(stateUrl, window.location.origin).pathname;
    } catch (_) {
      return;
    }
    if (!isPilotPath(pathname)) return;
    navigateTo(stateUrl, {
      fromPopstate: true,
      replaceState: true,
      restoreScrollY: Number(state.scrollY),
    });
  }

  function init() {
    const here = new URL(window.location.href);
    if (!isPilotPath(here.pathname)) return;

    const state = {
      ...(history.state && typeof history.state === "object" ? history.state : {}),
      [STATE_KEY]: true,
      url: here.href,
      scrollY: getCurrentScrollY(),
    };
    history.replaceState(state, "", here.href);

    document.addEventListener("click", onClick, true);
    window.addEventListener("popstate", onPopstate);
  }

  window.AdminNav = {
    init,
    navigateTo,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
