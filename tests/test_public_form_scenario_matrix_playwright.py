from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    "DOMESTICA_LIMPIEZA",
    "NINERA",
    "ENFERMERA",
    "CHOFER",
    "LIMPIEZA_ESTABLECIMIENTOS",
    "CONSERJERIA_RESIDENCIAL",
)
CLIENT_TYPES = ("existing", "new")
SCENARIOS = (
    "initial_status",
    "empty_submit_summary",
    "summary_link_focus",
    "inline_aria",
    "typing_quiet",
    "invalid_email_on_submit",
    "hidden_conditional_ignored",
    "server_error_recovery",
    "save_failure_message",
    "partial_schedule",
    "double_submit_guard",
    "valid_fields_not_submission",
)
SERVICE_GROUPS = {
    "DOMESTICA_LIMPIEZA": ("funciones", ("cocinar", "otro")),
    "NINERA": ("edad_requerida", ("26-35", "36-45")),
    "ENFERMERA": ("svc_care_assistance", ("acompanamiento", "enfermeria")),
    "CHOFER": ("pasaje_mode", ("empleador", "empleada")),
    "LIMPIEZA_ESTABLECIMIENTOS": ("experiencia_visual", ("Limpieza", "otro")),
    "CONSERJERIA_RESIDENCIAL": ("areas_comunes", ("sala", "pasillos")),
}


def scenario_html(service, client_type, server_postback=False, server_failure=False):
    form_id = "publicSolicitudNuevaForm" if client_type == "new" else "publicSolicitudForm"
    group_name, group_values = SERVICE_GROUPS[service]
    group_controls = "".join(
        f'<label><input type="checkbox" name="{group_name}" value="{value}"> {value}</label>'
        for value in group_values
    )
    if group_name == "svc_care_assistance":
        group_controls = "".join(
            f'<label><input type="radio" name="{group_name}" value="{value}"> {value}</label>'
            for value in group_values
        )
    group_controls += ''.join(
        f'<label><input type="radio" name="experiencia_visual" value="{value}"> {value}</label>'
        for value in ("General", "Especializada")
    )
    if server_postback:
        email_markup = '<input id="email_contacto" name="email_contacto" type="email" value="bad" required><div data-public-wtforms-error>Revisa el correo electrónico.</div>'
    else:
        email_markup = '<input id="email_contacto" name="email_contacto" type="email" required>'
    flash = '<div class="public-flash danger">No se pudo guardar la solicitud. Inténtalo de nuevo.</div>' if server_failure else ''
    return f'''{flash}<form id="{form_id}" data-form-ux="vue" data-server-postback="{'true' if server_postback else 'false'}" novalidate>
      <input type="hidden" name="tipo_servicio" value="{service}">
      <div class="public-form-sections">
        <section class="public-form-card public-form-card--profile"><span class="public-form-card-step">Paso 1</span>
          <div class="public-field"><label for="nombre_completo">Nombre</label><input id="nombre_completo" name="nombre_completo" required></div>
          <div class="public-field"><label for="email_contacto">Correo</label>{email_markup}</div>
          <div class="public-field"><label for="horario">Horario</label><input id="horario" name="horario"></div>
        </section>
        <section class="public-form-card public-form-card--service" data-service-panel="{service}"><span class="public-form-card-step">Paso 2</span>
          <div class="public-field" role="group"><span>Servicio</span>{group_controls}</div>
          <div class="public-field" role="group"><span>Modalidad</span><label><input type="radio" name="modalidad_grupo" value="con_salida_diaria"> Salida diaria</label><label><input type="radio" name="modalidad_grupo" value="con_dormida"> Con dormida</label></div>
          <div class="public-field"><label for="modalidad_especifica">Modalidad específica</label><select id="modalidad_especifica" name="modalidad_especifica"><option value="">Selecciona</option><option value="diaria">Salida diaria</option></select></div>
          <input name="horario_dias_trabajo"><input name="horario_hora_entrada"><input name="horario_hora_salida">
          <input name="horario_dormida_entrada"><input name="horario_dormida_salida"><input type="hidden" name="horario_canonico">
        </section>
        <section class="public-form-card public-form-card--home"><span class="public-form-card-step">Paso 3</span>
          <div class="d-none" aria-hidden="true"><input id="inactive_required" name="inactive_required" required></div>
        </section>
      </div>
      <section class="public-terms-wrap"><label><input id="acepta_politica" name="acepta_politica" type="checkbox" required> Acepto la política</label></section>
      <button type="submit">Enviar solicitud</button><div id="{form_id}VueRoot"></div></form>'''


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


def mount(browser, service, client_type, *, server_postback=False, server_failure=False):
    page = browser.new_page(viewport={"width": 390 if service in {"NINERA", "CHOFER", "CONSERJERIA_RESIDENCIAL"} else 1440, "height": 844}, reduced_motion="reduce")
    page.set_content(scenario_html(service, client_type, server_postback, server_failure))
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    form_id = "publicSolicitudNuevaForm" if client_type == "new" else "publicSolicitudForm"
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    return page, form_id


def fill_valid(page, form_id, service):
    page.locator(f"#{form_id} #nombre_completo").fill("María Pérez")
    page.locator(f"#{form_id} #email_contacto").fill("maria@example.test")
    group_name, _ = SERVICE_GROUPS[service]
    first = page.locator(f"#{form_id} input[name='{group_name}']").first
    first.check()
    if service != "LIMPIEZA_ESTABLECIMIENTOS":
        page.locator(f"#{form_id} input[name='experiencia_visual']").first.check()
    page.locator(f"#{form_id} input[name='modalidad_grupo']").first.check()
    page.locator(f"#{form_id} #acepta_politica").check()


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("client_type", CLIENT_TYPES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_validation_scenario_matrix_by_service_and_client_type(browser, service, client_type, scenario):
    html_args = {"server_postback": scenario == "server_error_recovery", "server_failure": scenario == "save_failure_message"}
    page, form_id = mount(browser, service, client_type, **html_args)
    root_id = f"{form_id}VueRoot"
    summary = page.locator(f"#{root_id} .public-form-vue-error-summary")
    status = page.locator(f"#{root_id} .public-form-vue-status")
    if scenario == "initial_status":
        assert "Solicitud enviada" not in status.inner_text()
        assert "Solicitud completa" not in status.inner_text()
        assert not summary.is_visible()
    elif scenario == "empty_submit_summary":
        page.get_by_role("button", name="Enviar solicitud").click()
        assert summary.is_visible() and "Faltan datos para continuar" in summary.inner_text()
    elif scenario == "summary_link_focus":
        page.get_by_role("button", name="Enviar solicitud").click()
        link = summary.get_by_role("link").first
        target_id = link.get_attribute("href")[1:]
        link.click()
        page.wait_for_function("id => document.activeElement?.id === id", arg=target_id)
    elif scenario == "inline_aria":
        page.get_by_role("button", name="Enviar solicitud").click()
        invalid = page.locator(f"#{form_id} [aria-invalid='true']")
        assert invalid.count() > 0 and invalid.first.get_attribute("aria-describedby")
        assert page.locator(f"#{form_id} .public-vue-field-error").count() > 0
    elif scenario == "typing_quiet":
        page.locator(f"#{form_id} #email_contacto").fill("correo-invalido")
        assert not summary.is_visible()
        assert page.locator(f"#{form_id} .public-vue-field-error").count() == 0
        page.get_by_role("button", name="Enviar solicitud").click()
        assert summary.is_visible()
    elif scenario == "invalid_email_on_submit":
        fill_valid(page, form_id, service)
        page.locator(f"#{form_id} #email_contacto").fill("correo-invalido")
        page.get_by_role("button", name="Enviar solicitud").click()
        assert "correo válido" in summary.inner_text()
    elif scenario == "hidden_conditional_ignored":
        page.get_by_role("button", name="Enviar solicitud").click()
        links = summary.locator("a[href^='#']").evaluate_all("els => els.map(el => el.getAttribute('href'))")
        assert "#inactive_required" not in links
    elif scenario == "server_error_recovery":
        assert summary.is_visible() and "Revisa el correo electrónico." in summary.inner_text()
        email = page.locator(f"#{form_id} #email_contacto")
        email.fill("maria@example.test")
        assert email.get_attribute("aria-invalid") is None
        assert page.locator("[data-public-wtforms-error]").count() == 0
    elif scenario == "save_failure_message":
        assert "No se pudo confirmar el envío" in status.inner_text()
        assert "Solicitud enviada" not in status.inner_text()
    elif scenario == "partial_schedule":
        page.locator(f"#{form_id} input[name='modalidad_grupo'][value='con_salida_diaria']").check()
        page.locator(f"#{form_id} #modalidad_especifica").select_option("diaria")
        page.locator(f"#{form_id} input[name='horario_dias_trabajo']").fill("Lunes a viernes")
        assert not summary.is_visible()
        page.get_by_role("button", name="Enviar solicitud").click()
        assert "Indica la hora de entrada" in summary.inner_text()
    elif scenario == "double_submit_guard":
        fill_valid(page, form_id, service)
        page.evaluate("""() => { window.submitAttempts = 0; document.querySelector('form').addEventListener('submit', event => { if (!event.defaultPrevented) window.submitAttempts += 1; event.preventDefault(); }); }""")
        page.get_by_role("button", name="Enviar solicitud").click()
        page.get_by_role("button", name="Enviar solicitud").click()
        assert page.evaluate("window.submitAttempts") == 1
    elif scenario == "valid_fields_not_submission":
        fill_valid(page, form_id, service)
        page.evaluate("document.querySelector('form').addEventListener('submit', event => event.preventDefault())")
        page.get_by_role("button", name="Enviar solicitud").click()
        assert "Enviando la solicitud" in status.inner_text()
        assert "Solicitud enviada" not in status.inner_text()
        assert page.evaluate("window.location.href === 'about:blank'")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.close()
