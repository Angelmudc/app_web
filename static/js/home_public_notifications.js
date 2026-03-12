(function () {
  const card = document.getElementById("homePublicNotifCard");
  if (!card) return;

  const countUrl = card.getAttribute("data-count-url") || "";
  const listUrl = card.getAttribute("data-list-url") || "";
  const readBaseUrl = card.getAttribute("data-read-base-url") || "";
  const unreadNode = document.getElementById("homePublicNotifUnread");
  const listNode = document.getElementById("homePublicNotifList");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  function readUrlFor(id) {
    const target = "/0/leer";
    if (!readBaseUrl.includes(target)) return "";
    return readBaseUrl.replace(target, "/" + String(id) + "/leer");
  }

  function fmtDate(iso) {
    if (!iso) return "Sin fecha";
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return "Sin fecha";
    return dt.toLocaleString("es-DO");
  }

  function esc(str) {
    const s = String(str || "");
    return s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  async function fetchJson(url) {
    const resp = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  async function postRead(id) {
    const url = readUrlFor(id);
    if (!url) return;
    const headers = { Accept: "application/json" };
    if (csrfToken) headers["X-CSRFToken"] = csrfToken;
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers,
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  function renderList(items) {
    if (!Array.isArray(items) || items.length === 0) {
      listNode.innerHTML = '<div class="text-muted">No hay notificaciones recientes.</div>';
      return;
    }
    const html = items.map((item) => {
      const isRead = Boolean(item.is_read);
      const badge = isRead
        ? '<span class="badge bg-secondary">Leída</span>'
        : '<span class="badge bg-danger">Nueva</span>';
      const markBtn = isRead
        ? ""
        : '<button class="btn btn-sm btn-outline-secondary js-mark-read" data-id="' + String(item.id) + '">Marcar leída</button>';
      const reviewUrl = esc(item.review_url || "#");
      return (
        '<div class="border rounded p-2 mb-2">' +
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
    }).join("");
    listNode.innerHTML = html;
  }

  async function refresh() {
    try {
      const [countData, listData] = await Promise.all([
        fetchJson(countUrl),
        fetchJson(listUrl + (listUrl.includes("?") ? "&" : "?") + "limit=10"),
      ]);
      unreadNode.textContent = String((countData && countData.unread) || 0);
      renderList((listData && listData.items) || []);
    } catch (_) {
      listNode.innerHTML = '<div class="text-muted">No se pudo cargar notificaciones.</div>';
    }
  }

  listNode.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".js-mark-read");
    if (!btn) return;
    const id = Number(btn.getAttribute("data-id") || 0);
    if (!id) return;
    btn.disabled = true;
    try {
      await postRead(id);
      await refresh();
    } catch (_) {
      btn.disabled = false;
    }
  });

  refresh().catch(() => {});
  setInterval(() => refresh().catch(() => {}), 10000);
})();
