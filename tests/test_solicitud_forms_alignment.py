# -*- coding: utf-8 -*-

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_public_admin_and_client_templates_use_shared_core_partial():
    public_tpl = _read("templates/clientes/solicitud_form_publica.html")
    public_new_tpl = _read("templates/clientes/solicitud_form_publica_nueva.html")
    admin_tpl = _read("templates/admin/solicitud_form.html")
    cliente_tpl = _read("templates/clientes/solicitud_form.html")

    include_stmt = "{% include 'clientes/_solicitud_form_fields.html' %}"
    assert include_stmt in public_tpl
    assert include_stmt in public_new_tpl
    assert include_stmt in admin_tpl
    assert include_stmt in cliente_tpl
    assert 'class="admin-solicitud-form"' in admin_tpl


def test_client_template_includes_soft_wizard_shell_and_hidden_step_field():
    cliente_tpl = _read("templates/clientes/solicitud_form.html")
    assert "id=\"solicitud-soft-wizard\"" in cliente_tpl
    assert "id=\"wizard_step\"" in cliente_tpl
    assert "name=\"wizard_step\"" in cliente_tpl
    assert "id=\"wizard-prev-btn\"" in cliente_tpl
    assert "id=\"wizard-next-btn\"" in cliente_tpl


def test_shared_partial_keeps_core_order_aligned():
    partial = _read("templates/clientes/_solicitud_form_fields.html")

    idx_ciudad = partial.find("{{ render_field(form.ciudad_sector) }}")
    idx_rutas = partial.find("{{ render_field(form.rutas_cercanas) }}")
    idx_modalidad = partial.find("id=\"wrap_modalidad_guiada\"")
    idx_horario = partial.find("id=\"wrap_horario_inteligente\"")
    assert -1 not in (idx_ciudad, idx_rutas, idx_modalidad, idx_horario)
    assert idx_ciudad < idx_rutas < idx_modalidad < idx_horario

    idx_edad = partial.find("{{ render_field(form.edad_requerida")
    idx_exp = partial.find("{{ render_field(form.experiencia")
    idx_func = partial.find("{{ render_field(form.funciones")
    assert -1 not in (idx_edad, idx_exp, idx_func)
    assert idx_edad < idx_exp < idx_func

    idx_tl = partial.find("{{ render_field(form.tipo_lugar) }}")
    idx_hab = partial.find("id=\"wrap_habitaciones_selector\"")
    idx_banos = partial.find("id=\"wrap_banos_selector\"")
    idx_pisos = partial.find("Cantidad de pisos")
    idx_areas = partial.find("{{ render_field(form.areas_comunes")
    idx_ad = partial.find("wrapper_id='wrap_adultos'")
    idx_ni = partial.find("{{ render_field(form.ninos, wrapper_id='wrap_ninos') }}")
    idx_ed = partial.find("{{ render_field(form.edades_ninos, wrapper_id='wrap_edades_ninos') }}")
    idx_mas = partial.find("{{ render_field(form.mascota, wrapper_id='wrap_mascota') }}")
    assert -1 not in (idx_tl, idx_hab, idx_banos, idx_pisos, idx_areas, idx_ad, idx_ni, idx_ed, idx_mas)
    assert idx_tl < idx_hab < idx_banos < idx_pisos < idx_areas < idx_ad < idx_ni < idx_ed < idx_mas


def test_shared_partial_ruta_santiago_notice_and_house_selectors_are_present():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"wrap_rutas_santiago_notice\"" in partial
    assert "En Santiago, la ruta de transporte cercana nos ayuda a ubicar mejor el servicio" in partial
    assert "name=\"habitaciones_selector\"" in partial
    assert "['1', '2', '3', '4', '5']" in partial
    assert "name=\"habitaciones_selector\" value=\"otro\"" in partial
    assert "id=\"wrap_habitaciones_otro\"" in partial
    assert "name=\"banos_selector\"" in partial
    assert "['1', '1.5', '2', '2.5', '3', '3.5', '4', '4.5', '5', '5.5']" in partial
    assert "name=\"banos_selector\" value=\"otro\"" in partial
    assert "id=\"wrap_banos_otro\"" in partial
    assert "function syncSantiagoRutaNotice()" in partial
    assert "function syncAdultosRules(fromUserEvent)" in partial


def test_shared_partial_renders_pasaje_three_options_and_otro_field():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "name=\"pasaje_mode\" value=\"incluido\"" in partial
    assert "name=\"pasaje_mode\" value=\"aparte\"" in partial
    assert "name=\"pasaje_mode\" value=\"otro\"" in partial
    assert "name=\"pasaje_otro_text\"" in partial


def test_shared_partial_renders_guided_modalidad_groups_and_dynamic_hooks():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "name=\"modalidad_grupo\"" in partial
    assert "('con_dormida', 'Con dormida 💤')" in partial
    assert "('con_salida_diaria', 'Salida diaria')" in partial
    assert "'label': 'Con dormida 💤 quincenal'" in partial
    assert "'label': 'Salida diaria - 1 día a la semana'" in partial
    assert "'label': 'Salida diaria - lunes a viernes'" in partial
    assert "id=\"modalidad_especifica_select\"" in partial
    assert "id=\"wrap_modalidad_otro\"" in partial
    assert "id='modalidad_trabajo_hidden'" in partial
    assert "function parseStoredModalidad(rawValue)" in partial
    assert "function composeModalidadValue(group, specific, otherText)" in partial
    assert "function normalizeModalidadOtherText(raw, group)" in partial
    assert "replace(/\\s{2,}/g, ' ')" in partial
    assert "'label': 'Salida diaria - viernes a lunes'" not in partial
    assert "'label': 'Con dormida 💤 viernes a lunes'" not in partial
    assert "Salida Quincenal, sale viernes después del medio día" in partial
    assert "Lunes a sábado, sale sábado después del medio día" in partial


def test_shared_partial_hides_edades_ninos_until_rules_apply_and_removes_optional_copy():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "wrapper_id='wrap_edades_ninos'" in partial
    assert "function syncNinosRules(fromUserEvent)" in partial
    assert "name=\"horario_dias_trabajo\"" in partial
    assert "name=\"horario_hora_entrada\"" in partial
    assert "name=\"horario_hora_salida\"" in partial
    assert "name=\"horario_dormida_entrada\"" in partial
    assert "name=\"horario_dormida_salida\"" in partial


def test_shared_partial_shows_modalidad_otro_input_when_otro_option_selected():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "function isModalidadOtherOption(value)" in partial
    assert "v === 'otro' || v.indexOf(' otro') >= 0 || v.indexOf('otro ') === 0" in partial
    assert "var isOther = isModalidadOtherOption(selectedSpecific);" in partial
    assert "wrapOther.style.display = (group && isOther) ? '' : 'none';" in partial
    assert "return storageClean(o ? (gLabel + ' ' + o) : gLabel);" in partial


def test_shared_partial_clears_modalidad_otro_input_when_switching_to_non_otro():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "if (!isOther && fromUserEvent) otherInput.value = '';" in partial


def test_shared_partial_planchar_uses_centered_modal_and_required_actions():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"funciones_planchar_modal\"" in partial
    assert "Planchado seleccionado" in partial
    assert "id=\"funciones_planchar_continue\">Continuar</button>" in partial
    assert "id=\"funciones_planchar_cancel\">Quitar opción</button>" in partial
    assert "function showModal()" in partial
    assert "function hideModal()" in partial


def test_shared_partial_planchar_modal_reuses_existing_notice_text_without_duplication():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"funciones_planchar_notice_text\"" in partial
    assert "id=\"funciones_planchar_modal_text\"" in partial
    assert "modalTextNode.textContent = warningTextNode.textContent.trim();" in partial
    assert partial.count(
        "La solicitud de planchado suele reducir la disponibilidad de candidatas."
    ) == 1


def test_shared_partial_planchar_modal_buttons_apply_expected_checkbox_behavior():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "plancharInput.checked = true;" in partial
    assert "plancharInput.checked = false;" in partial
    assert "showModal();" in partial
    assert "hideModal();" in partial
    assert "showWarning();" in partial
    assert "hideWarning();" in partial


def test_shared_partial_contains_smart_alert_for_ninos_y_limpieza():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"wrap_ninos_limpieza_smart_alert\"" in partial
    assert "Esta solicitud puede requerir aclaración." in partial
    assert "id=\"ninos_limpieza_smart_alert_ack\">Entendido</button>" in partial
    assert "function setupNinosLimpiezaSmartAlert()" in partial


def test_shared_partial_smart_alert_requires_exact_three_conditions():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "var hasCuidarNinos = hasSelectedValue('funciones', 'ninos');" in partial
    assert "var hasLimpiezaGeneral = hasSelectedValue('funciones', 'limpieza');" in partial
    assert "var hasNinoLe5 = hasNinoAgeFiveOrLess(edadesInput ? edadesInput.value : '');" in partial
    assert "var shouldShow = !!(hasCuidarNinos && hasLimpiezaGeneral && hasNinoLe5);" in partial


def test_shared_partial_smart_alert_uses_age_parser_for_free_text():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "function parseNinosAgesFromFreeText(rawText)" in partial
    assert "var direct = txt.match(/\\b(\\d{1,2})\\s*anos?\\b/g) || [];" in partial
    assert "function hasNinoAgeFiveOrLess(rawText)" in partial


def test_shared_partial_contains_mascota_guidance_notes():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "id=\"wrap_mascota_main_note\"" in partial
    assert "Si hay mascotas en el hogar, por favor indícalo." in partial
    assert "id=\"wrap_mascota_secondary_note\"" in partial
    assert "Si la doméstica no tendrá responsabilidades relacionadas con la mascota" in partial


def test_shared_partial_mascota_secondary_note_is_conditional_and_does_not_edit_notes():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "function setupMascotaGuidance()" in partial
    assert "function isMascotaDeclared(txt)" in partial
    assert "secondaryWrap.classList.toggle('d-none', !show);" in partial
    assert "name === '{{ form.mascota.name if form.mascota is defined else \"\" }}'" in partial


def test_shared_partial_contains_salary_suggestion_box_and_actions():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "Analisis de sueldo sugerido" in partial
    assert "id=\"salarySuggestionBox\"" in partial
    assert "id=\"salarySuggestionUseBtn\">Usar sueldo sugerido</button>" in partial
    assert "id=\"salarySuggestionManualBtn\">Escribir otro monto</button>" in partial
    assert "function setupSalarySuggestion()" in partial
    assert "fetch('/clientes/api/sueldo-sugerido?'" in partial


def test_shared_partial_salary_suggestion_is_non_blocking():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "hostForm.addEventListener('submit'" in partial
    assert "No se pudo calcular la sugerencia en este momento." in partial
    assert "renderNoSuggest(result.reason_no_suggestion" in partial
