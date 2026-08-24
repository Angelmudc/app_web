# -*- coding: utf-8 -*-

from utils.child_age_parser import child_care_help_factor, has_child_age_five_or_less, parse_child_age_summary


def test_detects_le5_for_one_year_and_months():
    assert has_child_age_five_or_less("1 año y 5 meses") is True


def test_detects_le5_for_comma_separated_ages():
    assert has_child_age_five_or_less("1, 3 y 4 años") is True


def test_detects_le5_for_repeated_year_units():
    assert has_child_age_five_or_less("2 años y 3 años") is True


def test_does_not_trigger_for_age_above_five():
    assert has_child_age_five_or_less("6 años") is False


def test_detects_le5_in_mixed_text():
    assert has_child_age_five_or_less("moco 1 año y 5 meses") is True


def test_empty_text_does_not_trigger():
    assert has_child_age_five_or_less("") is False


def test_numeric_single_age_two_is_small_child():
    s = parse_child_age_summary("2")
    assert s["small_count"] == 1
    assert s["teen_count"] == 0


def test_numeric_single_age_fourteen_is_teen():
    s = parse_child_age_summary("14")
    assert s["small_count"] == 0
    assert s["teen_count"] == 1


def test_two_and_four_counts_two_small_children():
    s = parse_child_age_summary("2 y 4")
    assert s["small_count"] == 2


def test_two_comma_four_counts_two_small_children():
    s = parse_child_age_summary("2, 4")
    assert s["small_count"] == 2


def test_two_years_and_four_years_counts_two_small_children():
    s = parse_child_age_summary("2 años y 4 años")
    assert s["small_count"] == 2


def test_one_year_and_five_months_is_one_child():
    s = parse_child_age_summary("1 año y 5 meses")
    assert s["small_count"] == 1
    assert s["total_children"] == 1


def test_five_months_is_small_child():
    s = parse_child_age_summary("5 meses")
    assert s["small_count"] == 1


def test_six_and_eight_are_not_small():
    s = parse_child_age_summary("6 y 8")
    assert s["small_count"] == 0
    assert s["big_count"] == 2


def test_edades_aprobadas_separan_cuidado_activo_y_supervision():
    cases = [
        ("8 y 10 años", 2, 0, 2),
        ("2 y 10 años", 2, 1, 1),
        ("2 y 3 años", 2, 2, 0),
        ("6 y 12 años", 2, 0, 2),
        ("2, 8 y 12 años", 3, 1, 2),
        ("3, 4 y 11 años", 3, 2, 1),
    ]
    for edades, declared_count, small_count, supervision_count in cases:
        summary = parse_child_age_summary(edades, declared_count=declared_count)
        assert summary["small_child_count"] == small_count
        assert summary["supervision_count"] == supervision_count


def test_mixed_two_seven_fourteen_counts_only_one_small():
    s = parse_child_age_summary("2, 7 y 14")
    assert s["small_count"] == 1
    assert s["big_count"] == 1
    assert s["teen_count"] == 1


def test_eighteen_is_not_child():
    s = parse_child_age_summary("18")
    assert s["total_children"] == 0
    assert s["adult_count"] == 1


def test_empty_text_has_no_ages():
    s = parse_child_age_summary("")
    assert s["total_children"] == 0


def test_lista_7_9_10_anos_detecta_tres_mayores():
    s = parse_child_age_summary("7, 9, 10 años", declared_count=3)
    assert [child["total_months"] for child in s["children"]] == [84, 108, 120]
    assert s["parsed_count"] == 3
    assert s["unknown_count"] == 0
    assert s["small_count"] == 0
    assert s["big_count"] == 3
    assert s["moderate_supervision_count"] == 1
    assert s["light_supervision_count"] == 2
    assert s["supervision_count"] == 3
    assert s["child_care_load"] > 0
    assert s["child_care_load"] < 0.25
    assert s["confidence"] == "high"


def test_meses_no_se_interpretan_como_anos():
    s = parse_child_age_summary("16 meses", declared_count=1)
    assert s["children"][0]["years"] == 1
    assert s["children"][0]["months"] == 4
    assert s["children"][0]["total_months"] == 16
    assert s["small_count"] == 1


def test_ano_y_meses_es_un_solo_nino():
    s = parse_child_age_summary("1 año y 7 meses", declared_count=2)
    assert s["parsed_count"] == 1
    assert s["unknown_count"] == 1
    assert s["children"][0]["total_months"] == 19
    assert s["warnings"]


def test_child_care_help_factor_respeta_minimos_para_bebes_y_toddlers():
    baby = parse_child_age_summary("8 meses", declared_count=1)
    toddler = parse_child_age_summary("2 años", declared_count=1)
    older = parse_child_age_summary("7 y 10 años", declared_count=2)

    assert child_care_help_factor("con_ayuda", baby)["factor"] == 0.30
    assert child_care_help_factor("con_ayuda", toddler)["factor"] == 0.22
    assert child_care_help_factor("con_ayuda", older)["factor"] == 1.0
    assert child_care_help_factor("ayuda_mayor", baby)["code"] == "con_ayuda"
    assert child_care_help_factor("ayuda_quehaceres", older)["warnings"] == []


def test_edad_compacta_1a_6m_es_un_solo_nino():
    s = parse_child_age_summary("1a 6m", declared_count=1)
    assert s["parsed_count"] == 1
    assert s["children"][0]["total_months"] == 18


def test_lista_meses_y_anos_detecta_dos_ninos():
    s = parse_child_age_summary("8 meses y 4 años", declared_count=2)
    assert [child["total_months"] for child in s["children"]] == [8, 48]
    assert s["small_count"] == 2


def test_gemelos_de_dos_anos_duplica_edad():
    s = parse_child_age_summary("gemelos de 2 años", declared_count=2)
    assert [child["total_months"] for child in s["children"]] == [24, 24]
    assert s["confidence"] == "high"


def test_rango_no_se_convierte_en_dos_ninos():
    s = parse_child_age_summary("entre 4 y 6 años", declared_count=1)
    assert s["parsed_count"] == 1
    assert s["unknown_count"] == 0
    assert s["children"][0]["years"] == 4


def test_mixto_2_10_15_clasifica_un_cuidado_real_y_dos_supervision():
    s = parse_child_age_summary("2, 10, 15 años", declared_count=3)
    assert s["parsed_count"] == 3
    assert s["toddler_count"] == 1
    assert s["small_child_count"] == 1
    assert s["light_supervision_count"] == 1
    assert s["minimal_supervision_count"] == 1
    assert s["supervision_count"] == 2
    assert s["child_care_load"] < parse_child_age_summary("1, 2 y 3 años", declared_count=3)["child_care_load"]
