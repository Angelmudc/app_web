(function () {
  const bell = document.getElementById("clientNotifBell");
  const overlay = document.getElementById("clientNotifOverlay");
  const closeBtn = document.getElementById("clientNotifClose");
  const listNode = document.getElementById("clientNotifList");
  const badge = document.getElementById("clientNotifBadge");
  if (!bell || !overlay || !listNode || !badge) return;

  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const RD_TIMEZONE = "America/Santo_Domingo";

  function setUnreadCount(n) {
    const val = Math.max(0, Number(n || 0));
    badge.textContent = String(val);
    if (val > 0) badge.classList.remove("d-none");
    else badge.classList.add("d-none");
    bell.setAttribute("data-unread", String(val));
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString("es-DO", { timeZone: RD_TIMEZONE });
    } catch (_e) {
      return iso;
    }
  }

  function escapeHtml(raw) {
    return String(raw || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function safeUrl(raw) {
    try {
      const u = new URL(String(raw || ""), window.location.origin);
      if (u.origin !== window.location.origin) return "#";
      return u.pathname + u.search + u.hash;
    } catch (_e) {
      return "#";
    }
  }

  function notifItemHtml(item) {
    const unreadCls = item.is_read ? "" : " unread";
    const stateLabel = item.is_read ? "Leída" : "No leída";
    const markReadBtn = item.is_read
      ? ""
      : `<button type="button" class="btn btn-sm btn-outline-secondary" data-action="mark-read" data-id="${escapeHtml(item.id)}">Marcar leída</button>`;
    return [
      `<article class="client-notif-item${unreadCls}" data-id="${escapeHtml(item.id)}">`,
      `<div class="fw-semibold">${escapeHtml(item.title || "Notificacion")}</div>`,
      `<div class="small text-muted">${escapeHtml(item.body || "")}</div>`,
      `<div class="client-notif-meta mt-1">${escapeHtml(fmtDate(item.created_at))} · ${escapeHtml(stateLabel)}</div>`,
      `<div class="client-notif-actions">`,
      `<button type="button" class="btn btn-sm btn-primary" data-action="view" data-id="${escapeHtml(item.id)}" data-url="${escapeHtml(safeUrl(item.url || "#"))}">Ver detalle</button>`,
      markReadBtn,
      `<button type="button" class="btn btn-sm btn-outline-danger" data-action="delete" data-id="${escapeHtml(item.id)}">Eliminar</button>`,
      `</div>`,
      `</article>`,
    ].join("");
  }

  async function fetchList() {
    const res = await fetch("/clientes/notificaciones.json?limit=10", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error("No se pudo cargar notificaciones");
    return res.json();
  }

  function renderList(payload) {
    const items = Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) {
      listNode.innerHTML = '<div class="text-muted small py-3">No tienes notificaciones recientes.</div>';
    } else {
      listNode.innerHTML = items.map(notifItemHtml).join("");
    }
    setUnreadCount(payload.unread_count || 0);
  }

  async function postAction(url) {
    const body = new URLSearchParams();
    body.set("csrf_token", csrfToken);
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      },
      body: body.toString(),
    });
    if (!res.ok) {
      let raw = "";
      try {
        raw = await res.text();
      } catch (_e) {
        raw = "";
      }
      const msg = `Notificacion fetch error status=${res.status} body=${(raw || "").slice(0, 300)}`;
      console.error(msg);
      throw new Error(msg);
    }
    return res.json();
  }

  let isOpen = false;

  async function refresh(opts) {
    const options = opts || {};
    if (!options.silent) {
      listNode.innerHTML = '<div class="text-muted small py-3">Cargando notificaciones...</div>';
    }
    try {
      const payload = await fetchList();
      renderList(payload);
      return payload;
    } catch (_e) {
      if (!options.silent) {
        listNode.innerHTML = '<div class="text-danger small py-3">No se pudo cargar la bandeja.</div>';
      }
      return null;
    }
  }

  function openModal() {
    isOpen = true;
    overlay.classList.remove("d-none");
    overlay.setAttribute("aria-hidden", "false");
    refresh();
  }

  function closeModal() {
    isOpen = false;
    overlay.classList.add("d-none");
    overlay.setAttribute("aria-hidden", "true");
  }

  bell.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click", function (ev) {
    if (ev.target === overlay) closeModal();
  });

  window.ClientNotifications = {
    refresh,
    isOpen: function () { return Boolean(isOpen); },
    setUnreadCount,
  };
  window.addEventListener("client-notifications:refresh", function () {
    refresh({ silent: !isOpen });
  });

  listNode.addEventListener("click", async function (ev) {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const action = btn.getAttribute("data-action");
    if (!id || !action) return;

    if (action === "view") {
      btn.classList.add("is-loading");
      btn.disabled = true;
      try {
        const payload = await postAction(`/clientes/notificaciones/${id}/ver`);
        setUnreadCount(payload.unread_count || 0);
        window.location.href = payload.redirect_url || btn.getAttribute("data-url") || "/clientes/solicitudes";
      } catch (_e) {
        // no-op: se registra en postAction
      } finally {
        btn.classList.remove("is-loading");
        btn.disabled = false;
      }
      return;
    }

    if (action === "delete") {
      btn.classList.add("is-loading");
      btn.disabled = true;
      try {
        const payload = await postAction(`/clientes/notificaciones/${id}/eliminar`);
        const row = listNode.querySelector(`[data-id="${id}"]`);
        if (row) row.remove();
        setUnreadCount(payload.unread_count || 0);
        if (!listNode.querySelector(".client-notif-item")) {
          listNode.innerHTML = '<div class="text-muted small py-3">No tienes notificaciones recientes.</div>';
        }
      } catch (_e) {
        // no-op: se registra en postAction
      } finally {
        btn.classList.remove("is-loading");
        btn.disabled = false;
      }
    }

    if (action === "mark-read") {
      btn.classList.add("is-loading");
      btn.disabled = true;
      try {
        const payload = await postAction(`/clientes/notificaciones/${id}/marcar-leida`);
        setUnreadCount(payload.unread_count || 0);
        const row = listNode.querySelector(`.client-notif-item[data-id="${id}"]`);
        if (row) {
          row.classList.remove("unread");
          const meta = row.querySelector(".client-notif-meta");
          if (meta) {
            const txt = String(meta.textContent || "");
            meta.textContent = txt.replace("No leída", "Leída").replace("No leida", "Leída");
          }
          const markBtn = row.querySelector('button[data-action="mark-read"]');
          if (markBtn) markBtn.remove();
        }
      } catch (_e) {
        // no-op: se registra en postAction
      } finally {
        btn.classList.remove("is-loading");
        btn.disabled = false;
      }
    }
  });
})();
