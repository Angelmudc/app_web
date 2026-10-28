import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright

from app import app as flask_app


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


def wait_for_scroll_settled(page):
    page.evaluate("""() => new Promise(resolve => {
      let previous = window.scrollY;
      let stableFrames = 0;
      const started = performance.now();
      const check = now => {
        const current = window.scrollY;
        stableFrames = Math.abs(current - previous) < 1 ? stableFrames + 1 : 0;
        previous = current;
        if (stableFrames >= 8 || now - started > 2500) resolve();
        else requestAnimationFrame(check);
      };
      requestAnimationFrame(check);
    })""")


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


@pytest.fixture(scope="module")
def rendered_forms():
    previous_testing = flask_app.config.get("TESTING")
    previous_csrf = flask_app.config.get("WTF_CSRF_ENABLED")
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = flask_app.test_client()
    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        new_html = client.get("/clientes/solicitudes/nueva-publica/synthetic-token").get_data(as_text=True)

    synthetic_client = SimpleNamespace(
        id=7, codigo="CL-TEST", nombre_completo="Synthetic Client",
        email="synthetic@example.test", is_active=True, updated_at=None,
        total_solicitudes=0, fecha_ultima_solicitud=None, fecha_ultima_actividad=None,
    )
    with patch("clientes.routes._ensure_public_token_usage_table", return_value=True), \
         patch("clientes.routes._resolve_public_link_token", return_value=(synthetic_client, "", {"legacy_token": False})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes.log_action"):
        existing_html = client.get("/clientes/solicitudes/publica/synthetic-token").get_data(as_text=True)
    flask_app.config.update(TESTING=previous_testing, WTF_CSRF_ENABLED=previous_csrf)

    # Keep the actual Flask-rendered markup and inline service behavior, while
    # replacing external/local script loading with the checked-in local assets.
    def strip_script_src(markup):
        return re.sub(r"<script\b[^>]*\bsrc=[^>]*>\s*</script>", "", markup, flags=re.I)

    return {"existing": strip_script_src(existing_html), "new": strip_script_src(new_html)}


@pytest.mark.parametrize("client_type", CLIENT_TYPES)
@pytest.mark.parametrize("service", SERVICES)
def test_real_rendered_form_selects_service_and_exposes_linked_errors_without_posting(browser, rendered_forms, client_type, service):
    viewport = {"width": 390, "height": 844} if service in {"NINERA", "CHOFER", "CONSERJERIA_RESIDENCIAL"} else {"width": 1440, "height": 1000}
    page = browser.new_page(viewport=viewport, reduced_motion="reduce")
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.set_content(rendered_forms[client_type], wait_until="domcontentloaded")
    form_id = "publicSolicitudForm" if client_type == "existing" else "publicSolicitudNuevaForm"
    root_id = f"{form_id}VueRoot"
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_conditions_modal.js"))
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    page.wait_for_selector(f"#{root_id} .public-form-vue-status")

    # Prevent any test submission from reaching the synthetic form action.
    page.evaluate("""() => document.querySelector('form').addEventListener('submit', event => event.preventDefault())""")
    page.locator(f"#{form_id}").locator(f'[data-service-code="{service}"]').click()
    page.locator("#publicContinueService").click()
    assert page.locator(f'input[name="tipo_servicio"][value="{service}"]').count() == 1
    assert page.locator(f'[data-service-panel="{service}"]:not(.d-none)').count() == (0 if service == "DOMESTICA_LIMPIEZA" else 1)
    assert page.locator(f"#{form_id} [name='direccion_precisa']").count() == 0

    # Typing alone stays quiet. The attempted submit reveals every current
    # required error, inline messages, and links into the actual Flask form.
    status = page.locator(f"#{root_id} .public-form-vue-status")
    assert "Solicitud completa" not in status.inner_text()
    assert page.locator(f"#{root_id} .public-form-vue-error-summary").count() == 0
    page.locator(f"#{form_id} button[type='submit']").last.click()
    summary = page.locator(f"#{root_id} .public-form-vue-error-summary")
    assert summary.is_visible(), f"{client_type}/{service} no mostró el resumen de errores"
    assert "Faltan datos para continuar" in summary.inner_text()
    assert summary.locator("a[href^='#']").count() > 0
    assert page.locator(f"#{form_id} [aria-invalid='true']").count() > 0
    assert page.locator(f"#{form_id} .public-vue-field-error").count() > 0
    assert "Solicitud enviada" not in status.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    first_error_link = summary.locator("a[href^='#']").first
    first_error_link.click()
    target_id = first_error_link.get_attribute("href")[1:]
    page.wait_for_function("id => document.activeElement?.id === id", arg=target_id)
    assert page_errors == [], f"Errores de JavaScript en {client_type}/{service}: {page_errors}"
    page.close()


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("viewport", [
    {"width": 390, "height": 844},
    {"width": 1440, "height": 900},
])
def test_modality_error_keeps_label_choices_and_message_visible(browser, rendered_forms, viewport, service):
    page = browser.new_page(viewport=viewport)
    page.set_default_timeout(3000)
    page.set_content(rendered_forms["existing"], wait_until="domcontentloaded")
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_conditions_modal.js"))
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    page.wait_for_selector("#publicSolicitudFormVueRoot .public-form-vue-status")
    page.locator(f'#publicSolicitudForm [data-service-code="{service}"]').click()
    page.locator("#publicContinueService").click()
    for selector, value in [
        ("#ciudad_input_ui", "Santiago"),
        ("#sector_input_ui", "Centro"),
    ]:
        page.locator(selector).fill(value)

    page.locator('#publicSolicitudForm button[type="submit"]').last.click()
    wait_for_scroll_settled(page)

    result = page.evaluate("""() => {
      const field = document.querySelector('#modalidad_trabajo_hidden').closest('.public-field');
      const label = field.querySelector('.public-field-label');
      const choices = field.querySelector('.public-segmented');
      const error = field.querySelector('.public-vue-field-error');
      const rects = [label, choices, error].map(node => node.getBoundingClientRect());
      return {top: Math.min(...rects.map(rect => rect.top)), bottom: Math.max(...rects.map(rect => rect.bottom)), height: innerHeight};
    }""")
    if service == "NINERA":
        page.screenshot(path=f"/tmp/public-form-modality-{viewport['width']}.png")
    assert result["top"] >= 12, f"{service}: la etiqueta/modalidad quedó recortada: {result}"
    assert result["bottom"] <= result["height"] - 12, f"{service}: opciones o error fuera de vista: {result}"
    page.close()


@pytest.mark.parametrize("error_text,region_selector", [
    ("rango de edad", '[name="edad_requerida"]'),
    ("opción de experiencia", '[name="experiencia_visual"]'),
])
def test_selection_group_error_navigation_keeps_label_and_error_visible(browser, rendered_forms, error_text, region_selector):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(3000)
    page.set_content(rendered_forms["existing"], wait_until="domcontentloaded")
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_conditions_modal.js"))
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    page.wait_for_selector("#publicSolicitudFormVueRoot .public-form-vue-status")
    page.locator('#publicSolicitudForm [data-service-code="NINERA"]').click()
    page.locator("#publicContinueService").click()
    page.locator("#ciudad_input_ui").fill("Santiago")
    page.locator("#sector_input_ui").fill("Centro")
    page.locator('#publicSolicitudForm button[type="submit"]').last.click()
    page.locator("#publicSolicitudFormVueRoot .public-form-vue-error-summary").get_by_role("link", name=re.compile(error_text, re.I)).click()
    wait_for_scroll_settled(page)
    result = page.evaluate("""selector => {
      const control = document.querySelector(selector);
      const region = control.closest('.public-field');
      const label = region.querySelector('.public-field-label, legend');
      const message = region.querySelector('.public-vue-field-error');
      return [label, control.closest('[role="group"], [role="radiogroup"]') || control, message]
        .filter(Boolean).map(node => node.getBoundingClientRect().toJSON());
    }""", region_selector)
    assert result and min(rect["top"] for rect in result) >= 12, f"Etiqueta/grupo recortado: {result}"
    assert max(rect["bottom"] for rect in result) <= 832, f"Grupo/mensaje fuera del viewport: {result}"
    page.close()


def test_required_conditional_text_error_is_revealed_and_navigable(browser, rendered_forms):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(3000)
    page.set_content(rendered_forms["existing"], wait_until="domcontentloaded")
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_conditions_modal.js"))
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    page.wait_for_selector("#publicSolicitudFormVueRoot .public-form-vue-status")
    page.locator('#publicSolicitudForm [data-service-code="NINERA"]').click()
    page.locator("#publicContinueService").click()
    page.locator("#ciudad_input_ui").fill("Santiago")
    page.locator("#sector_input_ui").fill("Centro")
    page.locator('#publicSolicitudForm input[name="svc_ninera_special_need"][value="si"]').check()
    conditional = page.locator("#svc_ninera_special_care")
    assert conditional.is_visible() and conditional.is_enabled() and conditional.get_attribute("required") is not None
    page.locator('#publicSolicitudForm button[type="submit"]').last.click()
    page.locator("#publicSolicitudFormVueRoot .public-form-vue-error-summary").locator('a[href="#svc_ninera_special_care"]').click()
    wait_for_scroll_settled(page)
    result = page.evaluate("""() => {
      const field = document.querySelector('#svc_ninera_special_care').closest('.public-field');
      return [field.querySelector('label'), field.querySelector('textarea'), field.querySelector('.public-vue-field-error')]
        .map(node => node.getBoundingClientRect().toJSON());
    }""")
    assert min(rect["top"] for rect in result) >= 12, f"Campo condicional quedó recortado: {result}"
    assert max(rect["bottom"] for rect in result) <= 832, f"Campo/error condicional fuera de vista: {result}"
    page.close()


def test_select_error_navigation_keeps_label_and_message_visible(browser, rendered_forms):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(3000)
    page.set_content(rendered_forms["existing"], wait_until="domcontentloaded")
    page.add_style_tag(path=str(ROOT / "static/css/clientes/public_form_vue.css"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_conditions_modal.js"))
    page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
    page.wait_for_selector("#publicSolicitudFormVueRoot .public-form-vue-status")
    page.locator('#publicSolicitudForm [data-service-code="DOMESTICA_LIMPIEZA"]').click()
    page.locator("#publicContinueService").click()
    page.locator("#ciudad_input_ui").fill("Santiago")
    page.locator("#sector_input_ui").fill("Centro")
    page.locator('#modalidad_grupo_wrap label').filter(has_text="Salida diaria").click()
    page.locator('#publicSolicitudForm button[type="submit"]').last.click()
    page.locator('#publicSolicitudFormVueRoot .public-form-vue-error-summary a[href="#modalidad_especifica_select"]').click()
    wait_for_scroll_settled(page)
    result = page.evaluate("""() => {
      const select = document.querySelector('#modalidad_especifica_select');
      const field = select.closest('.public-field');
      return [field.querySelector('label'), select, field.querySelector('.public-vue-field-error')]
        .map(node => node.getBoundingClientRect().toJSON());
    }""")
    assert min(rect["top"] for rect in result) >= 12, f"Select y etiqueta recortados: {result}"
    assert max(rect["bottom"] for rect in result) <= 832, f"Select/error fuera de vista: {result}"
    assert page.evaluate("document.activeElement.id") == "modalidad_especifica_select"
    page.close()
