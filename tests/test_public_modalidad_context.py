from utils.modalidad import (
    _MODALIDAD_SPECS,
    validate_modalidad_context,
)


def _label(group, index):
    return _MODALIDAD_SPECS[group][index]["label"]


def test_every_catalog_pair_accepts_for_its_group():
    for group, specs in _MODALIDAD_SPECS.items():
        for spec in specs:
            ok, label, reason = validate_modalidad_context(
                group,
                spec["label"],
                spec["label"],
            )
            assert (ok, reason) == (True, "")
            assert label == spec["label"]


def test_cross_group_pairs_are_rejected():
    for group, specs in _MODALIDAD_SPECS.items():
        other_group = next(name for name in _MODALIDAD_SPECS if name != group)
        for spec in specs:
            other = _label(other_group, 0)
            ok, _label_value, reason = validate_modalidad_context(
                group,
                spec["label"],
                other,
            )
            assert not ok
            assert reason == "canonica_fuera_del_grupo"


def test_original_n16_and_n36_tampering_is_rejected():
    # Exact mutation used by the final audit: the visible pair says salida
    # diaria while the hidden canonical value says con dormida.
    for specific in (
        "Salida diaria - lunes a viernes",
        "Salida diaria - 2 días a la semana",
    ):
        ok, _label_value, reason = validate_modalidad_context(
            "con_salida_diaria",
            specific,
            "Con dormida 💤 lunes a viernes",
        )
        assert not ok
        assert reason == "canonica_fuera_del_grupo"


def test_codes_aliases_and_invalid_values():
    ok, label, reason = validate_modalidad_context(
        "con_salida_diaria", "sd_l_v", "Salida diaria - lunes a viernes"
    )
    assert (ok, label, reason) == (
        True,
        "Salida diaria - lunes a viernes",
        "",
    )

    for group, specific, canonical in (
        ("", "", ""),
        ("con_salida_diaria", "", "Salida diaria - lunes a viernes"),
        ("con_salida_diaria", "not-a-real-option", "not-a-real-option"),
        ("not-a-real-group", "sd_l_v", "Salida diaria - lunes a viernes"),
    ):
        ok, _label_value, _reason = validate_modalidad_context(
            group, specific, canonical
        )
        assert not ok
