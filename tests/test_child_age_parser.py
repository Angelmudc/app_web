# -*- coding: utf-8 -*-

from utils.child_age_parser import has_child_age_five_or_less


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
