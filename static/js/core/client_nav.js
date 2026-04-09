(function () {
  "use strict";

  if (window.ClientNav) return;

  const VIEWPORT_SELECTOR = "#clientMainViewport";
  const SHELL_SELECTOR = "[data-client-shell-persistent='true']";
  const LINK_SELECTOR = "a[data-client-nav='true']";
  const FORM_SELECTOR = "form[data-client-nav='true']";
  const STATE_KEY = "__client_nav_pilot";
  const LOADING_CLASS = "is-client-nav-loading";
  const FALLBACK_EVENT = "client:navigation-fallback";
  const NAV_SOURCE = "client-nav";
  const PILOT_PATHS = [
    /^\/clientes\/dashboard\/?$/,
    /^\/clientes\/solicitudes\/?$/,
    /^\/clientes\/solicitudes\/\d+\/?$/,
    /^\/clientes\/informacion\/?$/,
    /^\/clientes\/planes\/?$/,
    /^\/clientes\/ayuda\/?$/,
    /^\/clientes\/proceso\/?$/,
  ];
  let isInitialized = false;
  let isNavigating = false;
  let isFallingBack = false;
  let pendingPopstate = null;
  const runtime = window.__clientNavRuntime = window.__clientNavRuntime || {
    enabled: false,
    successCount: 0,
    fallbackCount: 0,
    lastSuccess: null,
    lastFallback: null,
    recent: [],
    updatedAt: Date.now(),
  };

  function dispatchLifecycle(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
  }

  function debugLog(label, detail) {
    try {
      if (window.localStorage && window.localStorage.getItem("client_nav_debug") === "1") {
        console.log(label, detail || {});
      }
    } catch (_e) {
      // no-op
    }
  }

  function emitRuntime(kind, detail) {
    const evtDetail = detail || {};
    if (kind === "success") {
      runtime.successCount += 1;
      runtime.lastSuccess = evtDetail;
    } else if (kind === "fallback") {
      runtime.fallbackCount += 1;
      runtime.lastFallback = evtDetail;
    }
    runtime.updatedAt = Date.now();
    runtime.recent.push({
      kind: String(kind || ""),
      from: String(evtDetail.from || ""),
      to: String(evtDetail.to || ""),
      reason: String(evtDetail.reason || ""),
      ts: runtime.updatedAt,
    });
    if (runtime.recent.length > 40) runtime.recent.splice(0, runtime.recent.length - 40);
    window.dispatchEvent(new CustomEvent("client-nav:runtime", { detail: {
      kind: kind,
      successCount: runtime.successCount,
      fallbackCount: runtime.fallbackCount,
      lastSuccess: runtime.lastSuccess,
      lastFallback: runtime.lastFallback,
      updatedAt: runtime.updatedAt,
    } }));
  }

  function normalizePath(pathname) {
    const path = String(pathname || "").trim() || "/";
    if (path.length > 1) return path.replace(/\/+$/, "");
    return "/";
  }

  function isPilotPath(pathname) {
    const path = normalizePath(pathname);
    return PILOT_PATHS.some(function (re) { return re.test(path); });
  }

  function setLoadingUi(loading) {
    const root = document.documentElement;
    const body = document.body;
    if (loading) {
      root.classList.add(LOADING_CLASS);
      if (body) body.classList.add(LOADING_CLASS);
      return;
    }
    root.classList.remove(LOADING_CLASS);
    if (body) body.classList.remove(LOADING_CLASS);
  }

  function getCurrentScrollY() {
    return Math.max(0, Number(window.scrollY || window.pageYOffset || 0));
  }

  function updateCurrentHistoryScrollY() {
    const state = (history.state && typeof history.state === "object") ? history.state : {};
    history.replaceState({ ...state, scrollY: getCurrentScrollY() }, "", window.location.href);
  }

  function getViewport(doc) {
    return (doc || document).querySelector(VIEWPORT_SELECTOR);
  }

  function parseHtml(text) {
    try {
      const parser = new DOMParser();
      return parser.parseFromString(String(text || ""), "text/html");
    } catch (_e) {
      return null;
    }
  }

  function fallbackLoad(url) {
    const target = String(url || "").trim() || window.location.href;
    window.location.assign(target);
  }

  function emitFallback(reason, toUrl, options) {
    const opts = options || {};
    if (isFallingBack && opts.reload !== false) return;
    isFallingBack = true;
    pendingPopstate = null;
    const detail = {
      reason: String(reason || "unknown"),
      from: window.location.href,
      to: String(toUrl || "").trim(),
      source: NAV_SOURCE,
      route: window.location.pathname,
      ts: Date.now(),
    };

    try {
      console.warn("[ClientNav:fallback]", detail);
    } catch (_e) {
      // no-op
    }
    debugLog("[ClientNav:fallback]", detail);
    emitRuntime("fallback", detail);

    document.dispatchEvent(new CustomEvent(FALLBACK_EVENT, { detail: detail }));
    setLoadingUi(false);
    if (opts.reload !== false) {
      fallbackLoad(detail.to);
      return;
    }
    isFallingBack = false;
  }

  function shouldForceFallback(resp, doc) {
    if (!resp) return "fetch_error";
    if (resp.status === 401 || resp.status === 403) return "auth_required";
    if (!resp.ok) return "http_error";

    const finalUrl = new URL(resp.url || window.location.href, window.location.origin);
    if (/^\/clientes\/login\/?$/.test(finalUrl.pathname)) return "redirect_login";

    const hasLoginForm = Boolean(doc && doc.querySelector("form[action*='/clientes/login']"));
    if (hasLoginForm) return "redirect_login";

    return "";
  }

  function syncNavActive(url) {
    const finalUrl = new URL(url || window.location.href, window.location.origin);
    const finalPath = normalizePath(finalUrl.pathname);
    document.querySelectorAll(LINK_SELECTOR).forEach(function (node) {
      const href = node.getAttribute("href") || "";
      if (!href) return;
      try {
        const hrefPath = normalizePath(new URL(href, window.location.origin).pathname);
        const active = hrefPath === finalPath;
        node.classList.toggle("active", active);
        if (active) node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
      } catch (_e) {
        // no-op
      }
    });
  }

  function isEnabled() {
    const body = document.body;
    if (!body) return false;
    return String(body.getAttribute("data-client-partial-nav-enabled") || "0") === "1";
  }

  function getHashTarget(finalUrl) {
    const hash = (new URL(finalUrl, window.location.origin)).hash || "";
    if (!hash || hash.length < 2) return null;
    return document.getElementById(hash.slice(1));
  }

  function setFocusTemporarily(el) {
    if (!el || typeof el.focus !== "function") return;
    const hadTabindex = el.hasAttribute("tabindex");
    if (!hadTabindex) el.setAttribute("tabindex", "-1");
    try {
      el.focus({ preventScroll: true });
    } catch (_e) {}
    if (!hadTabindex) {
      window.setTimeout(function () {
        try { el.removeAttribute("tabindex"); } catch (_e) {}
      }, 0);
    }
  }

  function resolveFocusTarget(viewport, hashTarget) {
    if (hashTarget) return hashTarget;
    const anchor = viewport && viewport.querySelector ? viewport.querySelector("[data-client-focus-anchor]") : null;
    if (anchor) return anchor;
    const heading = viewport && viewport.querySelector ? viewport.querySelector("h1, h2") : null;
    if (heading) return heading;
    return viewport || null;
  }

  function applyScrollAndFocus(finalUrl, viewport, options) {
    const opts = options || {};
    const hashTarget = getHashTarget(finalUrl);
    if (hashTarget) {
      try {
        hashTarget.scrollIntoView({ behavior: "auto", block: "start" });
      } catch (_e) {
        hashTarget.scrollIntoView();
      }
    } else if (opts.fromPopstate && Number.isFinite(opts.restoreScrollY)) {
      window.scrollTo({ top: Math.max(0, Number(opts.restoreScrollY) || 0), left: 0, behavior: "auto" });
    } else {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    setFocusTemporarily(resolveFocusTarget(viewport, hashTarget));
  }

  async function navigateTo(url, options) {
    const opts = options || {};
    const requestedUrl = String(url || "").trim();
    if (!requestedUrl) return false;
    const fromUrl = window.location.href;
    if (isFallingBack) return false;

    if (isNavigating) {
      if (opts.fromPopstate) {
        pendingPopstate = {
          url: requestedUrl,
          restoreScrollY: Number.isFinite(opts.restoreScrollY) ? Number(opts.restoreScrollY) : null,
        };
      }
      return false;
    }
    isNavigating = true;
    setLoadingUi(true);

    try {
      const targetUrl = new URL(requestedUrl, window.location.origin);
      if (!isPilotPath(targetUrl.pathname)) {
        emitFallback("out_of_pilot", targetUrl.toString(), opts);
        return false;
      }

      const currentViewport = getViewport(document);
      if (!currentViewport) {
        emitFallback("missing_viewport", requestedUrl, opts);
        return false;
      }
      if (!opts.fromPopstate) updateCurrentHistoryScrollY();

      dispatchLifecycle("client:navigation-start", {
        from: fromUrl,
        to: targetUrl.toString(),
        source: NAV_SOURCE,
        fromPopstate: !!opts.fromPopstate,
      });

      const response = await fetch(requestedUrl, {
        method: "GET",
        credentials: "same-origin",
        redirect: "follow",
        headers: {
          "Accept": "text/html,application/xhtml+xml",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const html = await response.text();
      const nextDoc = parseHtml(html);
      if (!nextDoc) {
        emitFallback("parse_incompatible", requestedUrl, opts);
        return false;
      }

      const fallbackReason = shouldForceFallback(response, nextDoc);
      if (fallbackReason) {
        emitFallback(fallbackReason, response.url || requestedUrl, opts);
        return false;
      }

      const nextViewport = getViewport(nextDoc);
      if (!nextViewport) {
        emitFallback("invalid_html", response.url || requestedUrl, opts);
        return false;
      }

      currentViewport.innerHTML = nextViewport.innerHTML;
      if (nextDoc.title) document.title = nextDoc.title;

      const finalUrl = String(response.url || requestedUrl);
      if (!opts.fromPopstate && opts.pushState !== false) {
        history.pushState({ [STATE_KEY]: 1, url: finalUrl, scrollY: 0 }, "", finalUrl);
      }
      applyScrollAndFocus(finalUrl, currentViewport, opts);
      syncNavActive(finalUrl);
      dispatchLifecycle("client:content-updated", {
        container: currentViewport,
        targetSelector: VIEWPORT_SELECTOR,
        url: finalUrl,
        source: NAV_SOURCE,
      });
      dispatchLifecycle("client:navigation-complete", {
        from: fromUrl,
        to: finalUrl,
        source: NAV_SOURCE,
        fromPopstate: !!opts.fromPopstate,
      });
      emitRuntime("success", {
        from: fromUrl,
        to: finalUrl,
        route: window.location.pathname,
        fromPopstate: !!opts.fromPopstate,
        ts: Date.now(),
      });
      debugLog("[ClientNav:success]", { from: fromUrl, to: finalUrl, fromPopstate: !!opts.fromPopstate });
      return true;
    } catch (_e) {
      emitFallback("fetch_error", requestedUrl, opts);
      return false;
    } finally {
      isNavigating = false;
      setLoadingUi(false);
      if (pendingPopstate && pendingPopstate.url) {
        const queued = pendingPopstate;
        pendingPopstate = null;
        window.setTimeout(function () {
          navigateTo(queued.url, {
            fromPopstate: true,
            pushState: false,
            restoreScrollY: Number.isFinite(queued.restoreScrollY) ? queued.restoreScrollY : null,
          });
        }, 0);
      }
    }
  }

  function isInterceptableClick(event, link) {
    if (!event || !link) return false;
    if (event.defaultPrevented) return false;
    if (event.button !== 0) return false;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    if (!isEnabled()) return false;

    const href = String(link.getAttribute("href") || "").trim();
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return false;

    try {
      const targetUrl = new URL(href, window.location.origin);
      if (targetUrl.origin !== window.location.origin) return false;
      if (!isPilotPath(window.location.pathname)) return false;
      if (!isPilotPath(targetUrl.pathname)) return false;

      const samePath = normalizePath(targetUrl.pathname) === normalizePath(window.location.pathname);
      if (samePath && targetUrl.search === window.location.search && !targetUrl.hash) return false;
      if (targetUrl.hash && samePath) return false;
      return true;
    } catch (_e) {
      return false;
    }
  }

  function buildGetFormUrl(form) {
    const action = String(form.getAttribute("action") || window.location.href).trim() || window.location.href;
    const targetUrl = new URL(action, window.location.origin);
    const formData = new FormData(form);
    const params = new URLSearchParams();
    formData.forEach(function (value, key) {
      if (typeof value === "string") {
        params.append(key, value);
      }
    });
    targetUrl.search = params.toString();
    return targetUrl;
  }

  function isInterceptableGetFormSubmit(event, form) {
    if (!event || !form) return false;
    if (event.defaultPrevented) return false;
    if (!isEnabled()) return false;
    if (form.target && form.target !== "_self") return false;
    const method = String(form.getAttribute("method") || "get").toLowerCase();
    if (method !== "get") return false;
    try {
      const targetUrl = buildGetFormUrl(form);
      if (targetUrl.origin !== window.location.origin) return false;
      if (!isPilotPath(window.location.pathname)) return false;
      if (!isPilotPath(targetUrl.pathname)) return false;
      return true;
    } catch (_e) {
      return false;
    }
  }

  function bindLinkInterception() {
    document.addEventListener("click", function (event) {
      const link = event.target && event.target.closest ? event.target.closest(LINK_SELECTOR) : null;
      if (!link) return;
      if (!isInterceptableClick(event, link)) return;
      event.preventDefault();
      navigateTo(link.href, { pushState: true });
    }, true);
  }

  function bindGetFormInterception() {
    document.addEventListener("submit", function (event) {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (!form.matches(FORM_SELECTOR)) return;
      if (!isInterceptableGetFormSubmit(event, form)) return;
      event.preventDefault();
      const targetUrl = buildGetFormUrl(form);
      navigateTo(targetUrl.toString(), { pushState: true });
    }, true);
  }

  function bindPopstate() {
    window.addEventListener("popstate", function (event) {
      if (!isEnabled()) return;
      const targetUrl = window.location.href;
      if (!isPilotPath(window.location.pathname)) {
        emitFallback("out_of_pilot", targetUrl, { reload: true });
        return;
      }
      const state = (event && event.state && typeof event.state === "object") ? event.state : {};
      navigateTo(targetUrl, {
        fromPopstate: true,
        pushState: false,
        restoreScrollY: Number.isFinite(state.scrollY) ? Number(state.scrollY) : null,
      });
    });
  }

  function bootstrap() {
    if (isInitialized) return;

    const body = document.body;
    if (!body) return;
    runtime.enabled = isEnabled();
    runtime.updatedAt = Date.now();
    if (!runtime.enabled) return;

    const shell = document.querySelector(SHELL_SELECTOR);
    const viewport = getViewport(document);
    if (!shell || !viewport) {
      emitFallback("missing_viewport", window.location.href, { reload: false });
      return;
    }

    bindLinkInterception();
    bindGetFormInterception();
    bindPopstate();
    const currState = (history.state && typeof history.state === "object") ? history.state : {};
    if (!currState[STATE_KEY]) {
      history.replaceState({ ...currState, [STATE_KEY]: 1, url: window.location.href, scrollY: getCurrentScrollY() }, "", window.location.href);
    }
    syncNavActive(window.location.href);

    isInitialized = true;
    dispatchLifecycle("client:navigation-start", {
      from: window.location.href,
      to: window.location.href,
      source: NAV_SOURCE,
      bootstrap: true,
    });
    dispatchLifecycle("client:content-updated", {
      container: viewport,
      targetSelector: VIEWPORT_SELECTOR,
      url: window.location.href,
      source: NAV_SOURCE,
      bootstrap: true,
    });
    dispatchLifecycle("client:navigation-complete", {
      from: window.location.href,
      to: window.location.href,
      source: NAV_SOURCE,
      bootstrap: true,
    });
  }

  window.ClientNav = {
    bootstrap: bootstrap,
    navigateTo: navigateTo,
    fallback: emitFallback,
    getViewport: getViewport,
    isEnabled: isEnabled,
    isPilotPath: isPilotPath,
    getRuntime: function () { return runtime; },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    bootstrap();
  }
})();
