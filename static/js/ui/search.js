// static/js/ui/search.js
// Filtro rápido de listas/tablas con input.
// Uso: <input data-search="#tablaID" ...> o data-search=".items"
(function () {
  function normalize(s) {
    return (s || "")
      .toString()
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  function applySearch(input) {
    const targetSel = input.getAttribute("data-search");
    if (!targetSel) return;

    const target = document.querySelector(targetSel);
    if (!target) return;

    const q = normalize(input.value);

    // Si es tabla, filtra tbody tr
    if (target.tagName === "TABLE") {
      const rows = target.querySelectorAll("tbody tr");
      rows.forEach((tr) => {
        const text = normalize(tr.innerText);
        tr.style.display = !q || text.includes(q) ? "" : "none";
      });
      return;
    }

    // Si es lista, filtra hijos directos
    const children = target.children ? Array.from(target.children) : [];
    children.forEach((node) => {
      const text = normalize(node.innerText);
      node.style.display = !q || text.includes(q) ? "" : "none";
    });
  }

  document.addEventListener("input", (ev) => {
    const el = ev.target;
    if (!(el instanceof HTMLInputElement)) return;
    if (!el.hasAttribute("data-search")) return;
    applySearch(el);
  });
})();