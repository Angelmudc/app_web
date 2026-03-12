(function () {
  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? (meta.getAttribute("content") || "") : "";
  }

  var card = document.getElementById("homePublicNotifCard");
  if (!card) return;

  var countUrl = card.getAttribute("data-count-url") || "";
  var listUrl = card.getAttribute("data-list-url") || "";
  var readBaseUrl = card.getAttribute("data-read-base-url") || "";
  var unreadNode = document.getElementById("homePublicNotifUnread");
  var listNode = document.getElementById("homePublicNotifList");
  var trayNode = document.getElementById("homePublicNotifTray");
  var toggleBtn = document.getElementById("homePublicNotifToggle");
  var refreshBtn = document.getElementById("homePublicNotifRefresh");
  var csrfToken = getCsrfToken();

  var REQUEST_TIMEOUT_MS = 8000;
  var POLL_MS = 10000;
  var trayOpen = false;
  var listLoading = false;
  var lastUnread = 0;
  var lastSnapshot = {
    pending_items: [],
    reviewed_items: [],
    has_more_pending: false,
    has_more_reviewed: false,
  };

  if (!unreadNode || !listNode || !trayNode || !toggleBtn || !countUrl || !listUrl) {
    if (listNode) {
      listNode.innerHTML = '<div class="home-notif-empty">No se pudo inicializar notificaciones.</div>';
    }
    return;
  }

  function readUrlFor(id) {
    var target = "/0/leer";
    if (readBaseUrl.indexOf(target) === -1) return "";
    return readBaseUrl.replace(target, "/" + String(id) + "/leer");
  }

  function fmtDate(iso) {
    if (!iso) return "Sin fecha";
    var dt = new Date(iso);
    if (isNaN(dt.getTime())) return "Sin fecha";
    return dt.toLocaleString("es-DO");
  }

  function esc(str) {
    var s = String(str || "");
    return s
      .split("&").join("&amp;")
      .split("<").join("&lt;")
      .split(">")
      .join("&gt;")
      .split('"').join("&quot;")
      .split("'").join("&#39;");
  }

  function fetchJson(url) {
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (ctrl) ctrl.abort();
    }, REQUEST_TIMEOUT_MS);

    return fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: ctrl ? ctrl.signal : undefined,
    }).then(function (resp) {
      clearTimeout(timer);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }).then(function (data) {
      if (!data || typeof data !== "object") {
        throw new Error("Invalid JSON payload");
      }
      return data;
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  function postRead(id) {
    var url = readUrlFor(id);
    if (!url) return Promise.resolve({});

    var headers = { Accept: "application/json" };
    if (csrfToken) headers["X-CSRFToken"] = csrfToken;

    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: headers,
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }

  function setUnreadBadge(unread) {
    var count = Number(unread || 0);
    if (!isFinite(count) || count < 0) count = 0;
    lastUnread = count;
    unreadNode.textContent = String(count);
    unreadNode.classList.toggle("bg-danger", count > 0);
    unreadNode.classList.toggle("bg-secondary", count <= 0);
  }

  function renderItem(item, isRead) {
    var badge = isRead
      ? '<span class="badge bg-secondary">Revisada</span>'
      : '<span class="badge bg-danger">Pendiente</span>';

    var markBtn = isRead
      ? ""
      : '<button class="btn btn-sm js-mark-read home-notif-mark-read-btn" data-id="' + String(item.id) + '">Marcar leída</button>';

    var reviewUrl = esc(item.review_url || "#");
    var rowClass = isRead ? "home-notif-item-read" : "home-notif-item-unread";

    return (
      '<div class="home-notif-item border rounded p-2 mb-2 ' + rowClass + '">' +
        '<div class="d-flex justify-content-between align-items-center gap-2 mb-1">' +
          '<strong class="home-notif-title">' + esc(item.titulo || "Notificación") + '</strong>' +
          badge +
        '</div>' +
        '<div class="home-notif-message mb-2">' + esc(item.mensaje || "Sin detalle") + '</div>' +
        '<div class="d-flex justify-content-between align-items-center gap-2 flex-wrap">' +
          '<small class="home-notif-date">' + esc(fmtDate(item.created_at)) + '</small>' +
          '<div class="d-flex gap-2">' +
            '<a class="btn btn-sm btn-primary" href="' + reviewUrl + '">Revisar</a>' +
            markBtn +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function normalizeSnapshot(payload) {
    var data = payload || {};

    var pending = Array.isArray(data.pending_items) ? data.pending_items : null;
    var reviewed = Array.isArray(data.reviewed_items) ? data.reviewed_items : null;

    if (!pending || !reviewed) {
      var items = Array.isArray(data.items) ? data.items : [];
      pending = items.filter(function (it) { return it && !it.is_read; });
      reviewed = items.filter(function (it) { return it && it.is_read; });
    }

    return {
      pending_items: pending,
      reviewed_items: reviewed,
      has_more_pending: Boolean(data.has_more_pending),
      has_more_reviewed: Boolean(data.has_more_reviewed),
    };
  }

  function renderList(snapshot, errorText) {
    var pendingItems = snapshot.pending_items || [];
    var reviewedItems = snapshot.reviewed_items || [];
    var parts = [];

    if (errorText) {
      parts.push('<div class="alert alert-warning py-1 px-2 mb-2 small" role="alert">' + esc(errorText) + '</div>');
    }

    parts.push('<div class="home-notif-group-title">Pendientes</div>');
    if (pendingItems.length === 0) {
      parts.push('<div class="home-notif-empty mb-2">No hay notificaciones pendientes.</div>');
    } else {
      pendingItems.forEach(function (item) {
        parts.push(renderItem(item, false));
      });
      if (snapshot.has_more_pending) {
        parts.push('<div class="home-notif-empty mb-2">Hay más notificaciones pendientes.</div>');
      }
    }

    parts.push('<div class="home-notif-group-title mt-3">Revisadas</div>');
    if (reviewedItems.length === 0) {
      parts.push('<div class="home-notif-empty">Aún no tienes notificaciones revisadas.</div>');
    } else {
      reviewedItems.forEach(function (item) {
        parts.push(renderItem(item, true));
      });
      if (snapshot.has_more_reviewed) {
        parts.push('<div class="home-notif-empty">Hay más notificaciones revisadas.</div>');
      }
    }

    listNode.innerHTML = parts.join("");
  }

  function setTrayOpen(nextOpen) {
    trayOpen = Boolean(nextOpen);
    trayNode.classList.toggle("d-none", !trayOpen);
    toggleBtn.setAttribute("aria-expanded", trayOpen ? "true" : "false");
  }

  function refreshCount() {
    return fetchJson(countUrl).then(function (payload) {
      var unread = Number(payload && payload.unread);
      if (isFinite(unread) && unread >= 0) {
        setUnreadBadge(unread);
      } else {
        setUnreadBadge(lastUnread);
      }
    }).catch(function () {
      setUnreadBadge(lastUnread);
    });
  }

  function refreshList(reason) {
    if (!trayOpen || listLoading) return Promise.resolve();

    listLoading = true;
    if (refreshBtn) refreshBtn.disabled = true;

    var sep = listUrl.indexOf("?") !== -1 ? "&" : "?";
    var url = listUrl + sep + "limit=10";

    return fetchJson(url).then(function (payload) {
      var unread = Number(payload && payload.unread);
      if (isFinite(unread) && unread >= 0) {
        setUnreadBadge(unread);
      }

      lastSnapshot = normalizeSnapshot(payload);
      renderList(lastSnapshot, "");
    }).catch(function () {
      if ((lastSnapshot.pending_items || []).length > 0 || (lastSnapshot.reviewed_items || []).length > 0) {
        renderList(lastSnapshot, "Mostrando último estado por error de red.");
      } else {
        var msg = reason === "open"
          ? "No se pudo cargar notificaciones. Intenta actualizar."
          : "No se pudo actualizar notificaciones.";
        listNode.innerHTML = '<div class="home-notif-empty">' + esc(msg) + '</div>';
      }
    }).finally(function () {
      listLoading = false;
      if (refreshBtn) refreshBtn.disabled = false;
    });
  }

  toggleBtn.addEventListener("click", function () {
    setTrayOpen(!trayOpen);
    if (trayOpen) {
      refreshList("open");
    }
  });

  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      if (!trayOpen) return;
      refreshList("manual");
    });
  }

  document.addEventListener("click", function (ev) {
    if (!trayOpen) return;
    if (card.contains(ev.target)) return;
    setTrayOpen(false);
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && trayOpen) {
      setTrayOpen(false);
    }
  });

  listNode.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".js-mark-read");
    if (!btn) return;

    var id = Number(btn.getAttribute("data-id") || 0);
    if (!id) return;

    btn.disabled = true;
    postRead(id).then(function () {
      return refreshList("mark-read");
    }).then(function () {
      return refreshCount();
    }).catch(function () {
      btn.disabled = false;
    });
  });

  refreshCount();
  setInterval(function () {
    refreshCount();
  }, POLL_MS);
})();
