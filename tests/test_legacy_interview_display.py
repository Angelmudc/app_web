# -*- coding: utf-8 -*-

from utils.legacy_interview_display import build_legacy_interview_display


def test_legacy_interview_display_hides_empty_values():
    for value in (None, "", "   ", "{}", "[]", "null", "none", "[QA-REEMP-SEED] candidata=OLD-1"):
        display = build_legacy_interview_display(value)
        assert display.has_content is False
        assert display.entries == ()


def test_legacy_interview_display_parses_json_dict_and_list():
    dict_display = build_legacy_interview_display(
        '{"pregunta_1": "Respuesta 1", "pregunta_2": "Respuesta 2"}'
    )
    assert dict_display.has_content is True
    assert [entry.label for entry in dict_display.entries] == ["Pregunta 1", "Pregunta 2"]
    assert [entry.value for entry in dict_display.entries] == ["Respuesta 1", "Respuesta 2"]

    list_display = build_legacy_interview_display(
        '[{"pregunta": "Edad", "respuesta": "24"}, {"pregunta": "Experiencia", "respuesta": "5 años"}]'
    )
    assert list_display.has_content is True
    assert [entry.label for entry in list_display.entries] == ["Edad", "Experiencia"]
    assert [entry.value for entry in list_display.entries] == ["24", "5 años"]


def test_legacy_interview_display_parses_plain_text():
    display = build_legacy_interview_display(
        "Nombre completo: Ana Perez\nDireccion: Calle 1\nObservacion libre"
    )
    assert display.has_content is True
    assert [entry.label for entry in display.entries] == ["Nombre completo", "Direccion", "Entrevista histórica"]
    assert display.entries[0].value == "Ana Perez"
    assert display.entries[1].value == "Calle 1"
    assert display.entries[2].value == "Observacion libre"


def test_legacy_interview_display_falls_back_for_malformed_json():
    display = build_legacy_interview_display("{bad json")
    assert display.has_content is True
    assert display.entries
    assert display.entries[0].value == "{bad json"
