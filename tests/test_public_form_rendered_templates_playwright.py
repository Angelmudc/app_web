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
