from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


HTML = """
<form id="publicSolicitudForm" data-form-ux="vue" novalidate>
  <section class="public-form-card">
    <input type="radio" name="modalidad_grupo" value="con_dormida">
    <input type="radio" name="modalidad_grupo" value="con_salida_diaria">
    <select name="modalidad_especifica"><option value="">Selecciona</option><option>Con dormida 💤 lunes a viernes</option><option>Salida diaria - lunes a viernes</option></select>
    <input type="hidden" name="horario" id="horario_hidden">
    <div id="horario_sugerencias_options"></div>
    <input name="horario_dias_trabajo">
    <input name="horario_hora_entrada">
    <input name="horario_hora_salida">
    <input name="horario_dormida_entrada">
    <input name="horario_dormida_salida">
    <span id="horario_preview_text"></span>
  </section>
  <section class="public-form-card"><input type="checkbox" name="acepta_politica" required></section>
  <div id="publicSolicitudFormVueRoot"></div>
</form>
"""


def test_both_modalities_use_canonical_values_in_formdata_and_block_partial_schedule():
    with sync_playwright() as playwright:
        try:
            candidates = list(Path.home().glob("Library/Caches/ms-playwright/**/chrome"))
            candidates += list(Path.home().glob("Library/Caches/ms-playwright/**/chrome-headless-shell"))
            options = {"headless": True}
            if candidates:
                options["executable_path"] = str(candidates[0])
            browser = playwright.chromium.launch(**options)
        except Exception as exc:  # pragma: no cover - environment-specific
            pytest.skip(f"Chromium no disponible: {exc}")

        page = browser.new_page()
        page.set_content(HTML)
        page.add_script_tag(path=str(ROOT / "static/js/vendor/vue-3.5.13.global.prod.js"))
        page.add_script_tag(path=str(ROOT / "static/js/clientes/public_form_vue.js"))
        page.evaluate(
            """
            () => {
              const form = document.querySelector('form');
              const value = name => form.querySelector(`[name="${name}"]`);
              const sync = () => {
                const group = form.querySelector('[name="modalidad_grupo"]:checked')?.value;
                const specific = value('modalidad_especifica').value;
                const dIn = value('horario_dormida_entrada').value.trim();
                const dOut = value('horario_dormida_salida').value.trim();
                const days = value('horario_dias_trabajo').value.trim();
                const hIn = value('horario_hora_entrada').value.trim();
                const hOut = value('horario_hora_salida').value.trim();
                const canonical = group === 'con_dormida'
                  ? (dIn && dOut ? `Entrada: ${dIn} / Salida: ${dOut}` : '')
                  : (days && hIn && hOut ? `${days}, de ${hIn} a ${hOut}` : '');
                value('horario').value = canonical;
                document.querySelector('#horario_preview_text').textContent = canonical;
              };
              form.addEventListener('change', event => {
                if (event.target.name === 'modalidad_grupo') {
                  ['horario_dias_trabajo','horario_hora_entrada','horario_hora_salida','horario_dormida_entrada','horario_dormida_salida','horario'].forEach(name => value(name).value = '');
                }
                sync();
              });
              const suggestion = document.createElement('input');
              suggestion.type = 'radio'; suggestion.name = 'horario_sugerido_option'; suggestion.value = 'cd_lv_3';
              suggestion.addEventListener('change', () => {
                value('horario_dormida_entrada').value = 'lunes 7:30 AM';
                value('horario_dormida_salida').value = 'viernes 5:00 PM';
                sync();
                value('horario_dormida_entrada').dispatchEvent(new Event('change', {bubbles: true}));
                value('horario_dormida_salida').dispatchEvent(new Event('change', {bubbles: true}));
              });
              document.querySelector('#horario_sugerencias_options').appendChild(suggestion);
            }
            """
        )

        page.locator('input[name="modalidad_grupo"][value="con_dormida"]').check()
        page.locator('[name="modalidad_especifica"]').select_option(label="Con dormida 💤 lunes a viernes")
        page.locator('input[name="horario_sugerido_option"]').check()
        page.wait_for_timeout(200)
        assert page.locator('[name="horario_dormida_entrada"]').input_value() == "lunes 7:30 AM"
        assert page.locator('[name="horario_dormida_salida"]').input_value() == "viernes 5:00 PM"
        assert page.locator('[name="horario"]').input_value() == "Entrada: lunes 7:30 AM / Salida: viernes 5:00 PM"
        assert page.locator('#horario_preview_text').inner_text() == page.locator('[name="horario"]').input_value()
        assert page.evaluate("() => Object.fromEntries(new FormData(document.querySelector('form')).entries())['horario']") == "Entrada: lunes 7:30 AM / Salida: viernes 5:00 PM"

        page.locator('[name="horario_dormida_salida"]').fill("")
        page.wait_for_timeout(100)
        assert "Faltan" in page.locator('.public-form-vue-status').inner_text()
        assert "Indica cuándo sale de descanso." in page.locator('.public-vue-field-error').inner_text()

        page.locator('[name="horario_dormida_salida"]').fill("viernes 5:00 PM")
        page.locator('input[name="modalidad_grupo"][value="con_salida_diaria"]').check()
        page.locator('[name="modalidad_especifica"]').select_option(label="Salida diaria - lunes a viernes")
        page.locator('[name="horario_dias_trabajo"]').fill("Lunes a viernes")
        page.locator('[name="horario_hora_entrada"]').fill("7:30 AM")
        page.locator('[name="horario_hora_salida"]').fill("5:00 PM")
        page.wait_for_timeout(100)
        assert page.locator('[name="horario_dormida_entrada"]').input_value() == ""
        assert page.locator('[name="horario"]').input_value() == "Lunes a viernes, de 7:30 AM a 5:00 PM"
        assert page.evaluate("() => Object.fromEntries(new FormData(document.querySelector('form')).entries())['horario']") == "Lunes a viernes, de 7:30 AM a 5:00 PM"
        browser.close()
