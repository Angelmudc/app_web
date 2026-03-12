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
  var csrfToken = getCsrfToken();
  var REQUEST_TIMEOUT_MS = 8000;

  if (!unreadNode || !listNode || !trayNode || !toggleBtn || !countUrl || !listUrl) {
    if (listNode) {
      listNode.innerHTML = '<div class="text-muted">No se pudo inicializar notificaciones.</div>';
    }
    return;
  }

  var trayOpen = false;

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
      .split(">").join("&gt;")
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
      headers,
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }

  function setUnreadBadge(unread) {
    var count = Number(unread || 0);
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
      : '<button class="btn btn-sm btn-outline-secondary js-mark-read" data-id="' + String(item.id) + '">Marcar leída</button>';
    var reviewUrl = esc(item.review_url || "#");
    var rowClass = isRead ? "home-notif-item-read" : "home-notif-item-unread";
    return (
      '<div class="home-notif-item border rounded p-2 mb-2 ' + rowClass + '">' +
        '<div class="d-flex justify-content-between align-items-center gap-2 mb-1">' +
          '<strong>' + esc(item.titulo || "Notificación") + "</strong>" +
          badge +
        "</div>" +
        '<div class="text-muted mb-2">' + esc(item.mensaje || "Sin detalle") + "</div>" +
        '<div class="d-flex justify-content-between align-items-center gap-2 flex-wrap">' +
          '<small class="text-muted">' + esc(fmtDate(item.created_at)) + "</small>" +
          '<div class="d-flex gap-2">' +
            '<a class="btn btn-sm btn-primary" href="' + reviewUrl + '">Revisar</a>' +
            markBtn +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function renderList(items) {
    if (!Array.isArray(items) || items.length === 0) {
      listNode.innerHTML = '<div class="home-notif-empty text-muted">No hay notificaciones recientes.</div>';
      return;
    }
    var unreadItems = [];
    var readItems = [];
    items.forEach(function (item) {
      if (item && item.is_read) {
        readItems.push(item);
      } else {
        unreadItems.push(item);
      }
    });

    var parts = [];
    parts.push('<div class="home-notif-group-title">Pendientes</div>');
    if (unreadItems.length === 0) {
      parts.push('<div class="home-notif-empty text-muted mb-2">No hay notificaciones pendientes.</div>');
    } else {
      unreadItems.forEach(function (item) {
        parts.push(renderItem(item, false));
      });
    }
    parts.push('<div class="home-notif-group-title mt-3">Revisadas</div>');
    if (readItems.length === 0) {
      parts.push('<div class="home-notif-empty text-muted">Aún no tienes notificaciones revisadas.</div>');
    } else {
      readItems.forEach(function (item) {
        parts.push(renderItem(item, true));
      });
    }

    var html = parts.join("");
    listNode.innerHTML = html;
  }

  function setTrayOpen(nextOpen) {
    trayOpen = Boolean(nextOpen);
    trayNode.classList.toggle("d-none", !trayOpen);
    toggleBtn.setAttribute("aria-expanded", trayOpen ? "true" : "false");
  }

  function refresh() {
    return Promise.all([
      fetchJson(countUrl),
      fetchJson(listUrl + (listUrl.indexOf("?") !== -1 ? "&" : "?") + "limit=10"),
    ]).then(function (pair) {
      var countData = pair[0] || {};
      var listData = pair[1] || {};
      setUnreadBadge((countData && countData.unread) || 0);
      var items = (listData && listData.items) || (listData && listData.data && listData.data.items) || [];
      renderList(items);
    }).catch(function () {
      setUnreadBadge(unreadNode.textContent || "0");
      listNode.innerHTML = '<div class="text-muted">No se pudo cargar notificaciones.</div>';
    });
  }

  toggleBtn.addEventListener("click", function () {
    setTrayOpen(!trayOpen);
    if (trayOpen) {
      refresh();
    }
  });

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
      return refresh();
    }).catch(function () {
      btn.disabled = false;
    });
  });

  refresh();
  setInterval(function () { refresh(); }, 10000);
})();
