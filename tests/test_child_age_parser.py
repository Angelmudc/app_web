# -*- coding: utf-8 -*-

from utils.child_age_parser import has_child_age_five_or_less, parse_child_age_summary


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
