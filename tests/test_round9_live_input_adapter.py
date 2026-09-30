from scripts.local.round9_live_input_adapter import (
    LIVE_PREFIX,
    client_plan_input_cases,
    content_type_cases,
    live_prefix,
    public_live_input_cases,
)


def test_live_fixture_prefix_isolated_from_other_rounds():
    prefix = live_prefix()
    assert prefix.startswith(LIVE_PREFIX)
    assert not prefix.startswith("QA-REEMP-")
    assert not prefix.startswith("QA-E2E-")


def test_live_content_type_matrix_has_all_required_cases():
    cases = content_type_cases("cliente_plan", valid_payload={"tipo_plan": "premium"})
    assert [case.name for case in cases] == [
        "json_valido",
        "json_invalido",
        "form_urlencoded",
        "multipart",
        "text_plain",
        "sin_content_type",
    ]
    assert all(case.category in {"valid", "valid-but-unsupported", "invalid", "unsupported"} for case in cases)


def test_live_input_matrix_cubre_validacion_sin_redefinir_contrato():
    names = {case.name for case in client_plan_input_cases()}
    assert names == {
        "required_ausente",
        "null",
        "tipo_incorrecto",
        "enum_invalido",
        "string_largo",
        "unicode",
        "campo_extra",
        "array_en_escalar",
        "objeto_en_escalar",
    }
    assert all(case.expected_mutation is False for case in client_plan_input_cases() if case.name != "campo_extra")
    assert next(case for case in client_plan_input_cases() if case.name == "campo_extra").expected_mutation is True


def test_live_adapter_no_ejecuta_sin_explicit_execute():
    from scripts.local.round9_live_input_adapter import main

    assert main([]) == 0


def test_public_live_input_matrix_restringe_eventos_y_permite_unicode_controlado():
    cases = {case.name: case for case in public_live_input_cases("/qa-r9c-live-test-")}
    assert cases["unicode"].expected_mutation is True
    assert cases["campo_extra"].expected_mutation is True
    assert cases["array_en_escalar"].expected_mutation is False
    assert cases["objeto_en_escalar"].expected_mutation is False
