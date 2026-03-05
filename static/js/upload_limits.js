(function () {
  function humanSize(bytes) {
    const n = Number(bytes || 0);
    if (!Number.isFinite(n) || n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let size = n;
    let idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    if (idx === 0) return Math.round(size) + " " + units[idx];
    return size.toFixed(1) + " " + units[idx];
  }

  function bindUploadLimits() {
    const form = document.getElementById("uploadForm");
    if (!form) return;
    const maxBytes = Number(form.dataset.maxBytes || 0) || (3 * 1024 * 1024);

    const inputs = form.querySelectorAll('input[type="file"][name]');
    inputs.forEach((input) => {
      const name = String(input.name || "").trim();
      if (!name) return;

      const zone = form.querySelector('.upload-zone[data-target="' + name + '"]');
      if (!zone) return;

      let msg = zone.querySelector(".upload-limit-message");
      if (!msg) {
        msg = document.createElement("div");
        msg.className = "upload-limit-message d-none";
        zone.appendChild(msg);
      }

      let meta = zone.querySelector(".upload-file-meta");
      if (!meta) {
        meta = document.createElement("div");
        meta.className = "upload-file-meta d-none";
        zone.appendChild(meta);
      }

      input.addEventListener("change", function () {
        msg.classList.add("d-none");
        msg.textContent = "";
        meta.classList.add("d-none");
        meta.textContent = "";

        const file = input.files && input.files[0];
        if (!file) return;

        if (Number(file.size || 0) > maxBytes) {
          input.value = "";
          msg.textContent = "Archivo demasiado pesado. Máximo " + humanSize(maxBytes) + ", seleccionado " + humanSize(file.size) + ".";
          msg.classList.remove("d-none");
          return;
        }

        meta.textContent = file.name + " (" + humanSize(file.size) + ")";
        meta.classList.remove("d-none");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindUploadLimits);
  } else {
    bindUploadLimits();
  }
})();
