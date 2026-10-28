from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from test_public_form_scenario_matrix_playwright import CLIENT_TYPES, SERVICES, SERVICE_GROUPS, scenario_html


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
VIEWPORTS = (
    {"width": 390, "height": 844},
    {"width": 1440, "height": 900},
)


@pytest.fixture(scope="module")
def webkit():
    with sync_playwright() as playwright:
        browser = playwright.webkit.launch(headless=True)
        yield browser
        browser.close()


def mount_fallback(browser, client_type, service, viewport, *, vue_available=False):
    form_id = "publicSolicitudNuevaForm" if client_type == "new" else "publicSolicitudForm"
    # scenario_html returns only a form fragment. Give WebKit a real document
    # base so the bridge's dynamically loaded static assets resolve locally.
    html = '<!doctype html><html><head><base href="http://127.0.0.1/"></head><body>'
    html += scenario_html(service, client_type)
    # In the real flow inactive conditional controls are disabled; this
    # synthetic fixture keeps the same native-validity behavior.
    html = html.replace(
        'id="inactive_required" name="inactive_required" required',
        'id="inactive_required" name="inactive_required" required disabled',
    )
    html += '</body></html>'
    html = html.replace(
        '<input name="horario_dias_trabajo">',
        '<input type="hidden" name="horario" value="Horario sintético">'
        '<input name="horario_dias_trabajo">',
    )
    page = browser.new_page(viewport=viewport, reduced_motion="reduce")
    page.set_default_timeout(5000)

    def route_asset(route):
        path = urlparse(route.request.url).path
        if path.endswith("/vue-3.5.13.global.prod.js") and not vue_available:
            route.abort()
            return
        relative = path.removeprefix("/static/")
        asset = (STATIC / relative).resolve()
        if asset.is_relative_to(STATIC) and asset.is_file():
            route.fulfill(path=str(asset))
        else:
            route.fulfill(status=404, body="not found")

    page.route("http://127.0.0.1/static/**", route_asset)
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("""formId => {
      window.__modalReached = false;
      const form = document.getElementById(formId);
      form.addEventListener('submit', event => {
        if (!event.defaultPrevented) window.__modalReached = true;
        event.preventDefault();
      });
    }""", form_id)
    page.add_script_tag(path=str(STATIC / "js/clientes/public_form_ux.js"))
    if vue_available:
        page.wait_for_function(
            "id => !!document.querySelector(`#${id}VueRoot .public-form-vue-status`)",
            arg=form_id,
        )
    else:
        page.wait_for_function(
            "id => document.getElementById(id).dataset.publicFormFallback === 'true'",
            arg=form_id,
        )
    page.wait_for_function("typeof window.PublicFormValidation === 'object'")
    page.evaluate("""formId => {
      const form = document.getElementById(formId);
      form.querySelector('#nombre_completo, #nombre_cliente').value = 'María Pérez';
      form.querySelector('#email_contacto').value = 'prueba@example.test';
      const terms = form.querySelector('#acepta_politica');
      if (terms) terms.checked = true;
      form.querySelector('input[name="modalidad_grupo"]').checked = true;
      const specific = form.querySelector('select[name="modalidad_especifica"]');
      if (specific) specific.value = 'diaria';
      ['horario_dias_trabajo', 'horario_hora_entrada', 'horario_hora_salida'].forEach(name => {
        const input = form.querySelector(`[name="${name}"]`);
        if (input) input.value = 'Valor sintético';
      });
      const canonical = form.querySelector('[name="horario"]');
      if (canonical) canonical.value = 'Lunes a viernes, 8 AM a 4 PM';
      const hiddenCanonical = form.querySelector('[name="horario_canonico"]');
      if (hiddenCanonical) hiddenCanonical.value = 'Lunes a viernes, 8 AM a 4 PM';
      const serviceGroup = form.querySelector('input[name="' + form.dataset.requiredGroup + '"]');
      if (serviceGroup) serviceGroup.checked = true;
      const experiences = [...form.querySelectorAll('input[name="experiencia_visual"]')];
      if (form.dataset.requiredGroup !== 'experiencia_visual' && experiences.length) experiences[0].checked = true;
      const functionGroup = [...form.querySelectorAll('input[name="funciones"]')];
      if (form.dataset.requiredGroup !== 'funciones' && functionGroup.length) functionGroup[0].checked = true;
      const radios = [...form.querySelectorAll('input[type="radio"]')];
      const groups = [...new Set(radios.map(input => input.name))];
      for (const name of groups) {
        if (name === form.dataset.requiredGroup || name === 'modalidad_grupo' || form.querySelector(`[name="${CSS.escape(name)}"]:checked`)) continue;
        const control = radios.find(input => input.name === name);
        if (control) control.checked = true;
      }
      for (const input of form.querySelectorAll('input[type="checkbox"]')) {
        if (input.name === form.dataset.requiredGroup || input.name === 'funciones' || input.name === 'acepta_politica') continue;
        if (input.required && !form.querySelector(`[name="${CSS.escape(input.name)}"]:checked`)) input.checked = true;
      }
      for (const select of form.querySelectorAll('select[required]')) {
        if (!select.value) select.value = [...select.options].find(option => option.value && !option.disabled)?.value || '';
      }
      for (const field of form.querySelectorAll('input, select, textarea')) {
        field.dispatchEvent(new Event('input', {bubbles:true}));
        field.dispatchEvent(new Event('change', {bubbles:true}));
      }
    }""", form_id)
    return page, form_id


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("client_type", CLIENT_TYPES)
@pytest.mark.parametrize("service", SERVICES)
def test_vue_fallback_blocks_modal_for_each_missing_service_group(webkit, client_type, service, viewport):
    page, form_id = mount_fallback(webkit, client_type, service, viewport)
    group_name = SERVICE_GROUPS[service][0]
    page.evaluate("""({formId, groupName}) => {
      const form = document.getElementById(formId);
      form.dataset.requiredGroup = groupName;
      form.querySelectorAll(`[name="${CSS.escape(groupName)}"]`).forEach(control => {
        if ('checked' in control) control.checked = false;
      });
    }""", {"formId": form_id, "groupName": group_name})

    page.locator(f"#{form_id} button[type='submit']").click()

    summary = page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary")
    assert summary.is_visible(), f"{client_type}/{service}: faltó el resumen para {group_name}"
    assert page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary").count() == 1
    assert summary.get_by_role("link").count() >= 1
    assert page.evaluate("window.__modalReached === false"), f"{client_type}/{service}: abrió el modal incompleto"
    assert page.locator(f"#{form_id} [aria-invalid='true']").count() >= 1
    page.wait_for_function("document.activeElement.matches('[aria-invalid=true]')")
    assert page.locator(f"#{form_id} [aria-invalid='true'][name='{group_name}']").count() >= 1
    if service == "DOMESTICA_LIMPIEZA":
        assert page.locator(f"#{form_id} [name='experiencia_visual'][aria-invalid='true']").count() == 0
    page.close()


def test_vue_active_uses_only_its_summary_without_fallback_duplication(webkit):
    page, form_id = mount_fallback(
        webkit, "new", "DOMESTICA_LIMPIEZA", VIEWPORTS[0], vue_available=True
    )
    page.evaluate("""id => {
      const form = document.getElementById(id);
      form.dataset.requiredGroup = 'funciones';
      form.querySelectorAll('[name="funciones"]').forEach(control => control.checked = false);
    }""", form_id)
    page.locator(f"#{form_id} button[type='submit']").click()
    summary = page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary")
    assert summary.is_visible()
    assert summary.count() == 1
    assert page.locator(f"#{form_id}").get_attribute("data-public-form-fallback") is None
    assert page.evaluate("window.__modalReached === false")
    page.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("client_type", CLIENT_TYPES)
def test_fallback_errors_update_reactively_without_clearing_other_errors(webkit, client_type, viewport):
    page, form_id = mount_fallback(webkit, client_type, "DOMESTICA_LIMPIEZA", viewport)
    page.evaluate("""formId => {
      const form = document.getElementById(formId);
      form.dataset.requiredGroup = 'funciones';
      form.querySelectorAll('[name="funciones"], [name="experiencia_visual"]').forEach(control => {
        if ('checked' in control) control.checked = false;
      });
    }""", form_id)
    page.locator(f"#{form_id} button[type='submit']").click()

    summary = page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary")
    assert summary.is_visible()
    assert "función" in summary.inner_text().lower()
    assert "experiencia" in summary.inner_text().lower()

    page.locator(f"#{form_id} input[name='funciones']").first.check()
    page.wait_for_function(
        "id => !document.querySelector(`#${id}VueRoot .public-form-vue-error-summary`)?.textContent.includes('función')",
        arg=form_id,
    )
    assert summary.is_visible()
    assert "experiencia" in summary.inner_text().lower()

    page.locator(f"#{form_id} input[name='experiencia_visual']").first.check()
    page.wait_for_function("id => !document.querySelector(`#${id}VueRoot .public-form-vue-error-summary`)", arg=form_id)
    assert page.locator(f"#{form_id} [aria-invalid='true']").count() == 0

    page.locator(f"#{form_id} input[name='funciones']").first.uncheck()
    page.wait_for_selector(f"#{form_id}VueRoot .public-form-vue-error-summary")
    assert "función" in page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary").inner_text().lower()
    page.close()


def test_fallback_blocks_incomplete_schedule_but_allows_modal_when_schedule_is_complete(webkit):
    page, form_id = mount_fallback(webkit, "existing", "DOMESTICA_LIMPIEZA", VIEWPORTS[0])
    page.locator(f"#{form_id} [name='funciones']").first.check()
    page.locator(f"#{form_id} [name='experiencia_visual']").first.check()
    page.locator(f"#{form_id} [name='modalidad_especifica']").select_option("diaria")
    page.locator(f"#{form_id} [name='horario_hora_salida']").fill("")
    page.locator(f"#{form_id} button[type='submit']").click()

    summary = page.locator(f"#{form_id}VueRoot .public-form-vue-error-summary")
    assert summary.is_visible()
    assert "hora de salida" in summary.inner_text().lower()
    assert page.evaluate("window.__modalReached === false")

    page.locator(f"#{form_id} [name='horario_hora_salida']").fill("4:00 PM")
    page.wait_for_function("id => !document.querySelector(`#${id}VueRoot .public-form-vue-error-summary`)", arg=form_id)
    page.locator(f"#{form_id} button[type='submit']").click()
    assert page.evaluate("window.__modalReached === true")
    page.close()
