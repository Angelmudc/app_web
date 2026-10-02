from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_both_public_forms_use_the_shared_vue_entrypoint_without_error_summary():
    existing = read("templates/clientes/solicitud_form_publica.html")
    new = read("templates/clientes/solicitud_form_publica_nueva.html")

    for html, form_id in ((existing, "publicSolicitudForm"), (new, "publicSolicitudNuevaForm")):
        assert f'id="{form_id}VueRoot"' in html
        assert f'id="{form_id}" data-form-ux="vue"' in html
        assert "Revisa los campos marcados para continuar con el envío." not in html
        assert "public_form_ux.js" in html
        assert "unpkg.com/vue" not in html


def test_public_form_uses_versioned_local_vue_and_single_controller_bridge():
    bridge = read("static/js/clientes/public_form_ux.js")
    vue_asset = read("static/js/vendor/vue-3.5.13.global.prod.js")

    assert "/static/js/vendor/vue-3.5.13.global.prod.js" in bridge
    assert "https://unpkg.com" not in bridge
    assert "data-public-form-controller" in bridge
    assert "createApp" in vue_asset


def test_public_form_assets_are_cache_busted_and_reconciled_after_mobile_restore():
    bridge = read("static/js/clientes/public_form_ux.js")
    shared = read("templates/clientes/_solicitud_form_fields.html")
    for html in (read("templates/clientes/solicitud_form_publica.html"), read("templates/clientes/solicitud_form_publica_nueva.html")):
        assert "public_form_ux.js', v='20261002-audit1'" in html
    assert "ASSET_VERSION = '20261002-audit1'" in bridge
    assert "public_form_vue.css?v=' + ASSET_VERSION" in bridge
    assert "public_form_vue.js?v=' + ASSET_VERSION" in bridge
    assert "vue-3.5.13.global.prod.js?v=' + ASSET_VERSION" in bridge
    assert "reconcileConditionalState" in shared
    assert "window.addEventListener('pageshow', reconcileConditionalState)" in shared
    assert "document.visibilityState === 'visible'" in shared


def test_cleaning_reveal_uses_one_delegated_change_path():
    shared = read("templates/clientes/_solicitud_form_fields.html")
    setup = shared.split("function setupHomeStructureVisibility()", 1)[1].split("function setupSalarySuggestion()", 1)[0]
    assert "var functionControls = hostForm.querySelectorAll('input[name=\"funciones\"]')" not in setup
    assert "The delegated form change handler below is the single event path" in setup


def test_typescript_source_is_the_controller_source_of_truth():
    source = read("static/ts/clientes/public_form_vue.ts")
    config = read("tsconfig.public-form.json")
    generated = read("static/js/clientes/public_form_vue.js")

    assert '"strict": true' in config
    assert '"noEmitOnError": true' in config
    assert "visualViewport" in source
    assert "collectErrors" in source
    assert "public-form-progress" not in source
    assert "public-form-progress" not in generated
    assert "public-form-vue-status" in generated


def test_vue_controller_keeps_draft_non_sensitive_and_expires_after_24_hours():
    source = read("static/js/clientes/public_form_vue.js")

    assert "24 * 60 * 60 * 1000" in source
    assert "csrf_token" in source
    assert "terms_accepted" in source
    assert "email_contacto" in source
    assert "telefono_contacto" in source
    assert "sessionStorage.setItem" in source
    assert "sessionStorage.removeItem" in source
    assert "DRAFT_SCHEMA_VERSION = 3" in source
    assert "LEGACY_DRAFT_SCHEMA_VERSION = 2" in source
    assert "first.multiple" in source
    assert "token" in source


def test_vue_controller_owns_progressive_validation_and_double_submit_guard():
    source = read("static/js/clientes/public_form_vue.js")

    assert "scrollIntoView" in source
    assert "preventDefault" in source
    assert "state.submitting" in source
    assert "public-form-vue-status" in source
    assert "public-form-vue-field-error" not in source
    assert "public-vue-field-error" in source


def test_public_name_validation_is_explicit_in_both_vue_assets():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    for asset in (source, generated):
        assert "isValidPersonName" in asset
        assert "nombre_completo" in asset
        assert "Escribe un nombre válido usando solo letras, espacios, guiones o apóstrofes." in asset
        assert "setCustomValidity" in asset
    assert 'id="nombre_cliente"' in read("templates/clientes/solicitud_form_publica.html")
    assert 'form.nombre_completo.id' in read("templates/clientes/solicitud_form_publica_nueva.html")


def test_selection_groups_are_validated_once_and_not_by_each_required_checkbox():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")

    for name in ("edad_requerida", "experiencia_visual", "funciones", "envejeciente_tipo_cuidado", "areas_comunes", "modalidad_grupo", "pasaje_mode"):
        assert f"name: '{name}'" in source
        assert f"name: '{name}'" in generated
    assert "isSelectionControl(element) && SELECTION_GROUP_NAMES.has(element.name)" in source
    assert "normalizeSelectionGroupNativeValidity" in source
    for asset in (source, generated):
        assert "requiresElderCareType" in asset
        assert "elderCareResponsibilitiesIssue" in asset
        assert "Indica al menos una responsabilidad de cuidado o selecciona que será solo acompañamiento/supervisión." in asset
        assert "Indica si el envejeciente es independiente o está encamado." in asset
        assert "name === 'envejeciente_tipo_cuidado'" in asset


def test_grouped_controls_cannot_trigger_a_native_pre_submit_block():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    fields = read("templates/clientes/_solicitud_form_fields.html")
    for html in (read("templates/clientes/solicitud_form_publica.html"), read("templates/clientes/solicitud_form_publica_nueva.html")):
        assert 'data-form-ux="vue"' in html
        assert "novalidate" in html
    assert "normalizeSelectionGroupNativeValidity" in source
    assert "element.required = false" in source
    assert "element.required = false" in generated
    assert "sub(class='form-check-input', required=False" in fields
    assert 'name="modalidad_grupo" value="{{ group_value }}" required' not in fields
    assert "hidden select must not be a second" in source


def test_pending_navigation_has_one_shared_visible_target_resolver():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")

    for asset in (source, generated):
        assert "function navigationTarget" in asset
        assert "function scrollToNavigableTarget" in asset
        assert "isNavigableControl" in asset
        assert "scrollIntoView({ behavior: 'smooth', block: 'center' })" in asset
        assert "target.focus({ preventScroll: true })" in asset
        assert "compareDocumentPosition" in asset


def test_top_progress_is_removed_but_bottom_status_navigation_remains():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    for asset in (source, generated):
        assert "Paso {{" not in asset
        assert "100% completo" not in asset
        assert "Ir al pendiente" in asset
        assert "Solicitud completa. Ya puedes enviarla." in asset
    for html in (read("templates/clientes/solicitud_form_publica.html"), read("templates/clientes/solicitud_form_publica_nueva.html")):
        assert 'aria-label="Estado de la solicitud"' in html


def test_pending_button_does_not_treat_vue_click_event_as_form_error():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")

    # A method reference in Vue receives the MouseEvent as its first argument.
    # The handler must call focusFirst() explicitly and also remain defensive
    # for cached/generated assets that still pass the event through.
    assert '@click="focusFirst()"' in source
    assert "preferred && 'element' in preferred ? preferred : state.errors[0]" in source
    assert "preferred && 'element' in preferred ? preferred : state.errors[0]" in generated


def test_conditions_modal_submit_does_not_start_validation_scroll_before_modal_bridge():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    assert "!conditionsForm.__publicConditionsConfirmed" in source
    for asset in (generated,):
        assert "form.hasAttribute('data-conditions-modal-id')" in asset
        assert "!form.__publicConditionsConfirmed" in asset
        assert "Safari" in asset
    assert "form.hasAttribute('data-conditions-modal-id')" in source
    assert "Safari" in source


def test_terms_pending_is_part_of_both_forms_and_is_not_the_conditions_modal():
    source = read("static/ts/clientes/public_form_vue.ts")
    old_form = read("templates/clientes/solicitud_form_publica.html")
    new_form = read("templates/clientes/solicitud_form_publica_nueva.html")

    for html, checkbox_id, modal_id in (
        (old_form, "acepta_politica", "publicEmploymentConditionsModal"),
        (new_form, "acepta_politica_nueva", "publicEmploymentConditionsNuevaModal"),
    ):
        assert 'class="public-terms-wrap"' in html
        assert f'id="{checkbox_id}" required' in html
        assert f'id="{modal_id}"' in html
    assert "public-terms-wrap" in source
    assert "MESSAGES[element.id]" in source
    assert "statusRoot.querySelector('.public-form-vue-status')" in source


def test_pending_order_is_document_order_and_group_navigation_skips_hidden_controls():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")

    for asset in (source, generated):
        assert "DOCUMENT_POSITION_FOLLOWING" in asset
        assert "[role=\"group\"], [role=\"radiogroup\"], [role=\"groupbox\"]" in asset
        assert "!element.disabled && element.type !== 'hidden'" in asset


def test_draft_restore_overwrites_select_values_before_revalidating():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")

    assert "control.value = expected[0] || '';" in source
    assert "dispatchDraftDependencies" in source
    assert "setDraftValues(form, envelope.values);" in generated


def test_draft_serializes_conditional_and_multiple_controls_without_sensitive_values():
    source = read("static/ts/clientes/public_form_vue.ts")

    assert "disabled" not in source.split("function serializeDraft", 1)[1].split("function saveDraft", 1)[0]
    assert "first.multiple" in source
    assert "values[name] = named.filter" in source
    assert "acepta_politica" in source
    assert "nombre_cliente" in source
    assert "Array.isArray(envelope.values)" in source
    assert "removeItem(legacyDraftKey(form))" in source


def test_cleaning_household_requirement_is_one_named_navigable_composite_error():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    for asset in (source, generated):
        assert "houseStructureIssue" in asset
        assert "cleaningRequiresHouseStructure" in asset
        assert "wrap_home_structure_conditional" in asset
        assert "Describe tu hogar para continuar." not in asset
        assert "Indica cuántas habitaciones tiene el hogar." in asset
        assert "targetName" in asset
        assert "error.element.id === 'wrap_home_structure_conditional'" in asset
        assert "window.requestAnimationFrame(() => {" in asset


def test_cleaning_household_controls_are_excluded_from_anonymous_native_duplicates():
    source = read("static/ts/clientes/public_form_vue.ts")
    fields = read("templates/clientes/_solicitud_form_fields.html")
    assert "HOUSE_STRUCTURE_FIELD_NAMES" in source
    assert "filter((element) => !(houseActive && isHouseStructureControl(element)))" in source
    assert "if (houseActive && name === 'areas_comunes') continue;" in source
    assert 'id="wrap_home_structure_conditional"' in fields
    assert 'name="habitaciones_selector"' in fields
    assert 'name="banos_selector"' in fields


def test_success_redirect_clears_only_public_form_drafts():
    source = read("templates/clientes/solicitud_plan_select_public.html")

    assert "public_saved" in source
    assert "draft.' + version" in source
    assert "['v2', 'v3']" in source


def test_schedule_validation_uses_canonical_fields_for_both_public_forms():
    source = read("static/ts/clientes/public_form_vue.ts")
    generated = read("static/js/clientes/public_form_vue.js")
    fields = read("templates/clientes/_solicitud_form_fields.html")
    for asset in (source, generated):
        assert "function scheduleIssue" in asset
        assert "horario_dormida_entrada" in asset
        assert "horario_dormida_salida" in asset
        assert "horario_dias_trabajo" in asset
        assert "name=\"horario\"" in asset
    assert "SolicitudPublicaForm" in read("clientes/forms.py")
    assert "SolicitudClienteNuevoPublicaForm" in read("clientes/forms.py")
    assert "require_structured=True" in read("clientes/routes.py")
    assert "dispatchEvent(new Event('change'" in fields
    assert 'dias: "1 día a la semana"' in fields
    assert 'dias: "4 días a la semana"' in fields


def test_backend_rejects_modalidad_group_specific_tampering():
    source = read("clientes/forms.py")
    assert "split_modalidad_for_ui" in source
    assert "La modalidad específica no corresponde a la modalidad seleccionada." in source
