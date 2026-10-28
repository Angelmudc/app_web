from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
FORM_HTML = """
<form id="publicSolicitudForm" data-form-ux="vue" novalidate>
  <div class="public-form-sections">
    <section class="public-form-card public-form-card--profile">
      <span class="public-form-card-step">Paso 2</span><h2>Datos de contacto</h2>
      <div class="public-field">
        <label for="nombre_completo">Nombre</label>
        <input id="nombre_completo" name="nombre_completo" required>
      </div>
      <div class="public-field">
        <label for="email_contacto">Correo</label>
        <input id="email_contacto" name="email_contacto" type="email" required>
      </div>
    </section>
    <section class="public-form-card public-form-card--service">
      <span class="public-form-card-step">Paso 1</span><h2>Servicio</h2>
      <div class="public-field" role="group" aria-required="true">
        <span>Funciones</span>
        <label><input type="checkbox" name="funciones" value="limpieza"> Limpieza</label>
        <label><input type="checkbox" name="funciones" value="cocinar"> Cocinar</label>
      </div>
    </section>
  </div>
  <button type="submit">Enviar solicitud</button>
  <div id="publicSolicitudFormVueRoot"></div>
</form>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        candidates = list(Path.home().glob("Library/Caches/ms-playwright/**/chrome"))
        candidates += list(Path.home().glob("Library/Caches/ms-playwright/**/chrome-headless-shell"))
        options = {"headless": True}
        if candidates:
            options["executable_path"] = str(candidates[0])
        try:
            instance = playwright.chromium.launch(**options)
        except Exception as exc:  # pragma: no cover - machine-specific browser availability
            pytest.skip(f"Chromium no disponible: {exc}")
        yield instance
        instance.close()


def mount(browser, html=FORM_HTML, viewport=None):
    page = browser.new_page(viewport=viewport or {"width": 1440, "height": 1000}, reduced_motion="reduce")
    page.set_content(html)
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    return page


def test_empty_submit_shows_linked_summary_and_field_error_with_focus(browser):
    page = mount(browser, viewport={"width": 390, "height": 844})
    page.get_by_role("button", name="Enviar solicitud").click()

    summary = page.get_by_role("alert")
    assert summary.is_visible()
    assert "Faltan datos para continuar" in summary.inner_text()
    links = summary.get_by_role("link")
    assert links.count() == 3
    name = page.locator("#nombre_completo")
    assert name.get_attribute("aria-invalid") == "true"
    assert name.get_attribute("aria-describedby")
    assert "Paso incompleto" in page.locator(".public-form-card-state").all_inner_texts()
    assert name.locator("xpath=.. ").locator(".public-vue-field-error").inner_text().strip()
    links.first.click()
    page.wait_for_function("document.activeElement === document.querySelector('#nombre_completo')")
    assert "Solicitud enviada" not in page.locator("#publicSolicitudFormVueRoot").inner_text()
    page.close()


def test_resolved_errors_update_and_never_claim_submission_success(browser):
    page = mount(browser)
    page.get_by_role("button", name="Enviar solicitud").click()
    assert page.get_by_role("alert").is_visible()

    page.locator("#nombre_completo").fill("María Pérez")
    page.locator("#email_contacto").fill("maria@example.com")
    page.locator("input[name='funciones'][value='limpieza']").check()
    assert not page.get_by_role("alert").is_visible()
    status = page.locator(".public-form-vue-status").inner_text()
    assert "Pulsa" in status and "Enviar solicitud" in status
    assert "Solicitud completa" not in status
    assert "Solicitud enviada" not in status
    assert page.locator(".public-vue-field-error").count() == 0
    assert page.locator(".public-form-card-state.is-complete").count() == 2
    page.close()


def test_server_wtforms_error_is_summarized_and_clears_when_corrected(browser):
    html = FORM_HTML.replace(
        '<input id="email_contacto" name="email_contacto" type="email" required>',
        '<input id="email_contacto" name="email_contacto" type="email" value="bad" class="is-invalid" required>'
        '<div data-public-wtforms-error>Escribe un correo válido.</div>',
    ).replace('data-form-ux="vue"', 'data-form-ux="vue" data-server-postback="true"')
    page = mount(browser, html)

    summary = page.get_by_role("alert")
    assert summary.is_visible()
    assert "Escribe un correo válido." in summary.inner_text()
    email = page.locator("#email_contacto")
    assert email.get_attribute("aria-invalid") == "true"
    email.fill("persona@example.com")
    assert email.get_attribute("aria-invalid") is None
    assert page.locator("[data-public-wtforms-error]").count() == 0
    assert "Escribe un correo válido." not in summary.inner_text()
    page.close()


def test_failed_server_save_is_not_presented_as_success_and_can_be_retried(browser):
    html = FORM_HTML.replace('<form id=', '<div class="public-flash danger">No se pudo guardar la solicitud. Inténtalo de nuevo.</div><form id=')
    page = mount(browser, html)
    page.locator("#nombre_completo").fill("María Pérez")
    page.locator("#email_contacto").fill("maria@example.com")
    page.locator("input[name='funciones'][value='limpieza']").check()
    status = page.locator(".public-form-vue-status").inner_text()
    assert "No se pudo confirmar el envío" in status
    assert "Solicitud enviada" not in status

    page.evaluate("""() => {
      window.submitAttempts = 0;
      document.querySelector('form').addEventListener('submit', event => {
        if (!event.defaultPrevented) window.submitAttempts += 1;
        event.preventDefault();
      });
    }""")
    page.get_by_role("button", name="Enviar solicitud").click()
    page.get_by_role("button", name="Enviar solicitud").click()
    assert page.evaluate("window.submitAttempts") == 1
    page.close()


def test_first_visit_does_not_claim_all_fields_or_request_are_complete(browser):
    page = mount(browser)
    status = page.locator(".public-form-vue-status").inner_text()
    assert "Solicitud completa" not in status
    assert "Solicitud enviada" not in status
    assert "Completa los datos obligatorios" in status
    page.close()
