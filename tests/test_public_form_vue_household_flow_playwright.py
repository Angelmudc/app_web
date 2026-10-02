from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


HTML = """
<form id="publicSolicitudForm" data-form-ux="vue" novalidate>
  <section class="public-form-card">
    <input type="checkbox" name="funciones" value="limpieza" checked>
  </section>
  <section class="public-form-card">
    <div id="wrap_home_structure_conditional" class="d-none" aria-hidden="true">
      <select name="tipo_lugar" required><option value="">Selecciona</option><option value="casa">Casa</option></select>
      <input id="habitaciones_hidden" name="habitaciones" value="" disabled>
      <label><input type="radio" name="habitaciones_selector" value="3">3</label>
      <input id="banos_hidden" name="banos" value="" disabled>
      <label><input type="radio" name="banos_selector" value="2">2</label>
      <label><input type="checkbox" name="areas_comunes" value="sala">Sala</label>
    </div>
  </section>
  <div id="publicSolicitudFormVueRoot"></div>
</form>
"""


def test_cleaning_household_error_reveals_navigates_and_clears_in_real_dom():
    with sync_playwright() as playwright:
        try:
            candidates = list(Path.home().glob("Library/Caches/ms-playwright/**/chrome"))
            candidates += list(Path.home().glob("Library/Caches/ms-playwright/**/chrome-headless-shell"))
            launch_options = {"headless": True}
            if candidates:
                launch_options["executable_path"] = str(candidates[0])
            browser = playwright.chromium.launch(**launch_options)
        except Exception as exc:  # pragma: no cover - environment-specific browser availability
            pytest.skip(f"Chromium no disponible: {exc}")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_content(HTML)
        page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
        page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))

        page.evaluate(
            """
            () => {
              const form = document.querySelector('form');
              const sync = (name, hidden) => form.querySelectorAll(`[name="${name}"]`).forEach((node) =>
                node.addEventListener('change', () => {
                  if (node.checked) form.querySelector(`[name="${hidden}"]`).value = node.value;
                }));
              sync('habitaciones_selector', 'habitaciones');
              sync('banos_selector', 'banos');
            }
            """
        )

        page.get_by_role("button", name="Ir al pendiente").click()
        page.wait_for_timeout(700)
        assert page.locator("#wrap_home_structure_conditional").get_attribute("aria-hidden") == "false"
        assert page.locator("#wrap_home_structure_conditional .public-vue-field-error").inner_text() == "Indica qué tipo de lugar es."
        assert page.locator("select[name='tipo_lugar']").evaluate("el => document.activeElement === el")

        page.locator("select[name='tipo_lugar']").select_option("casa")
        page.locator("input[name='habitaciones_selector']").check()
        page.locator("input[name='banos_selector']").check()
        page.locator("input[name='areas_comunes']").check()
        page.wait_for_timeout(250)
        assert "Solicitud completa" in page.locator(".public-form-vue-status").inner_text()
        assert page.locator(".public-vue-field-error").count() == 0

        page.locator("input[name='funciones']").uncheck()
        page.locator("input[name='funciones']").check()
        page.wait_for_timeout(250)
        assert "Faltan 1" not in page.locator(".public-form-vue-status").inner_text()
        assert page.locator(".public-vue-field-error").count() == 0
        browser.close()
