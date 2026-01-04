// static/js/core/modal.js
// Modal confirm/prompt simple (usa Bootstrap si está; fallback básico).
(function () {
  function confirm({ title = "Confirmar", message = "¿Estás seguro?", okText = "Sí", cancelText = "Cancelar" } = {}) {
    return new Promise((resolve) => {
      const useBootstrap = !!window.bootstrap;

      const id = "appConfirmModal_" + Math.random().toString(16).slice(2);
      const wrapper = document.createElement("div");

      wrapper.innerHTML = `
        <div class="modal fade" tabindex="-1" id="${id}">
          <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">${title}</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button>
              </div>
              <div class="modal-body">
                <p class="mb-0">${message}</p>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-outline-secondary" data-cancel>${cancelText}</button>
                <button type="button" class="btn btn-primary" data-ok>${okText}</button>
              </div>
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(wrapper);
      const modalEl = wrapper.querySelector(".modal");
      const okBtn = wrapper.querySelector("[data-ok]");
      const cancelBtn = wrapper.querySelector("[data-cancel]");

      const cleanup = () => {
        try { wrapper.remove(); } catch (_) {}
      };

      if (!useBootstrap) {
        // Fallback: confirm nativo
        cleanup();
        resolve(window.confirm(message));
        return;
      }

      const modal = new bootstrap.Modal(modalEl);
      okBtn.addEventListener("click", () => {
        resolve(true);
        modal.hide();
      });
      cancelBtn.addEventListener("click", () => {
        resolve(false);
        modal.hide();
      });
      modalEl.addEventListener("hidden.bs.modal", cleanup);
      modal.show();
    });
  }

  window.AppModal = { confirm };
})();