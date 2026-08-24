(function () {
  const body = document.body;
  const searchUrl = body ? String(body.dataset.candidataQuickSearchUrl || "") : "";
  if (!searchUrl) return;

  const style = document.createElement("style");
  style.textContent = `
    .cand-palette-backdrop{position:fixed;inset:0;background:rgba(15,23,42,.38);z-index:1095;display:none;align-items:flex-start;justify-content:center;padding:12vh 1rem 1rem}
    .cand-palette-backdrop.is-open{display:flex}
    .cand-palette{width:min(640px,100%);background:var(--bs-body-bg);color:var(--bs-body-color);border:1px solid var(--bs-border-color);border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.28);overflow:hidden}
    .cand-palette input{border:0;border-bottom:1px solid var(--bs-border-color);border-radius:0;padding:.85rem 1rem;font-size:1rem}
    .cand-palette input:focus{box-shadow:none}
    .cand-palette-list{max-height:360px;overflow:auto;padding:.35rem}
    .cand-palette-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.75rem;width:100%;border:0;background:transparent;color:inherit;text-align:left;border-radius:6px;padding:.65rem .75rem}
    .cand-palette-item:hover,.cand-palette-item.is-active{background:var(--bs-tertiary-bg)}
    .cand-palette-meta{color:var(--bs-secondary-color);font-size:.85rem}
    .cand-palette-empty{padding:1rem;color:var(--bs-secondary-color)}
  `;
  document.head.appendChild(style);

  const backdrop = document.createElement("div");
  backdrop.className = "cand-palette-backdrop";
  backdrop.innerHTML = `
    <div class="cand-palette" role="dialog" aria-modal="true" aria-labelledby="candPaletteLabel">
      <label class="visually-hidden" id="candPaletteLabel" for="candPaletteInput">Buscar candidata</label>
      <input id="candPaletteInput" class="form-control" autocomplete="off" placeholder="Buscar candidata..." aria-controls="candPaletteList">
      <div class="cand-palette-list" id="candPaletteList" role="listbox" aria-label="Resultados de candidatas"></div>
    </div>
  `;
  document.body.appendChild(backdrop);

  const input = backdrop.querySelector("#candPaletteInput");
  const list = backdrop.querySelector("#candPaletteList");
  let items = [];
  let active = 0;
  let timer = null;
  let controller = null;
  let requestSeq = 0;

  function render(message) {
    list.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "cand-palette-empty";
      empty.textContent = message || "Sin resultados.";
      list.appendChild(empty);
      return;
    }
    const title = document.createElement("div");
    title.className = "cand-palette-meta px-2 py-1";
    title.textContent = input.value.trim() ? "Resultados" : "Recientes";
    list.appendChild(title);
    items.forEach((item, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cand-palette-item" + (index === active ? " is-active" : "");
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", index === active ? "true" : "false");
      btn.dataset.index = String(index);
      const meta = [item.codigo || "sin código", item.edad ? item.edad + " años" : "", item.telefono || "", item.estado_label || ""].filter(Boolean).join(" · ");
      btn.innerHTML = `<span><strong></strong><div class="cand-palette-meta"></div></span><span class="cand-palette-meta">Abrir</span>`;
      btn.querySelector("strong").textContent = item.nombre || "Sin nombre";
      btn.querySelector(".cand-palette-meta").textContent = meta;
      btn.addEventListener("click", () => openItem(index));
      list.appendChild(btn);
    });
  }

  async function fetchItems(q) {
    const seq = ++requestSeq;
    if (controller) controller.abort();
    controller = new AbortController();
    const url = new URL(searchUrl, window.location.origin);
    if (q) url.searchParams.set("q", q);
    url.searchParams.set("limit", "8");
    const response = await fetch(url.toString(), { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) throw new Error("search_failed");
    const payload = await response.json();
    if (seq !== requestSeq) return;
    items = Array.isArray(payload.items) ? payload.items : [];
    active = 0;
    render(q ? "" : "Sin recientes.");
  }

  function debouncedFetch() {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      fetchItems(input.value.trim()).catch((err) => {
        if (err && err.name === "AbortError") return;
        items = [];
        render("No se pudo buscar ahora.");
      });
    }, 220);
  }

  function open() {
    backdrop.classList.add("is-open");
    input.value = "";
    items = [];
    active = 0;
    render("Cargando recientes...");
    setTimeout(() => input.focus(), 0);
    fetchItems("").catch(() => render("No se pudo cargar recientes."));
  }

  function close() {
    if (controller) controller.abort();
    backdrop.classList.remove("is-open");
  }

  function openItem(index) {
    const item = items[index];
    if (!item || !item.detail_url) return;
    window.location.href = item.detail_url;
  }

  input.addEventListener("input", debouncedFetch);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      active = Math.min(items.length - 1, active + 1);
      render();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      active = Math.max(0, active - 1);
      render();
    } else if (event.key === "Enter") {
      event.preventDefault();
      openItem(active);
    }
  });
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      open();
    }
  });
})();
