(function () {
  const bell = document.getElementById("clientNotifBell");
  const overlay = document.getElementById("clientNotifOverlay");
  const closeBtn = document.getElementById("clientNotifClose");
  const listNode = document.getElementById("clientNotifList");
  const badge = document.getElementById("clientNotifBadge");
  if (!bell || !overlay || !listNode || !badge) return;

  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

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
      return d.toLocaleString();
    } catch (_e) {
      return iso;
    }
  }

  function notifItemHtml(item) {
    const unreadCls = item.is_read ? "" : " unread";
    const stateLabel = item.is_read ? "Leida" : "No leida";
    return [
      `<article class="client-notif-item${unreadCls}" data-id="${item.id}">`,
      `<div class="fw-semibold">${item.title || "Notificacion"}</div>`,
      `<div class="small text-muted">${item.body || ""}</div>`,
      `<div class="client-notif-meta mt-1">${fmtDate(item.created_at)} · ${stateLabel}</div>`,
      `<div class="client-notif-actions">`,
      `<button type="button" class="btn btn-sm btn-primary" data-action="view" data-id="${item.id}" data-url="${item.url || "#"}">Ver</button>`,
      `<button type="button" class="btn btn-sm btn-outline-danger" data-action="delete" data-id="${item.id}">Eliminar</button>`,
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
    if (!res.ok) throw new Error("Accion no disponible");
    return res.json();
  }

  async function refresh() {
    listNode.innerHTML = '<div class="text-muted small py-3">Cargando notificaciones...</div>';
    try {
      const payload = await fetchList();
      renderList(payload);
    } catch (_e) {
      listNode.innerHTML = '<div class="text-danger small py-3">No se pudo cargar la bandeja.</div>';
    }
  }

  function openModal() {
    overlay.classList.remove("d-none");
    overlay.setAttribute("aria-hidden", "false");
    refresh();
  }

  function closeModal() {
    overlay.classList.add("d-none");
    overlay.setAttribute("aria-hidden", "true");
  }

  bell.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click", function (ev) {
    if (ev.target === overlay) closeModal();
  });

  listNode.addEventListener("click", async function (ev) {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const action = btn.getAttribute("data-action");
    if (!id || !action) return;

    if (action === "view") {
      btn.disabled = true;
      try {
        const payload = await postAction(`/clientes/notificaciones/${id}/ver`);
        setUnreadCount(payload.unread_count || 0);
        window.location.href = payload.redirect_url || btn.getAttribute("data-url") || "/clientes/solicitudes";
      } catch (_e) {
        btn.disabled = false;
      }
      return;
    }

    if (action === "delete") {
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
        btn.disabled = false;
      }
    }
  });
})();
