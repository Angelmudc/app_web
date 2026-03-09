# -*- coding: utf-8 -*-

from __future__ import annotations

import clientes.routes as clientes_routes


CHOICES = [
    ("sala", "Sala"),
    ("comedor", "Comedor"),
    ("cocina", "Cocina"),
    ("todas_anteriores", "Todas las anteriores"),
    ("otro", "Otro"),
]


def test_areas_todas_anteriores_expands_to_real_options_only():
    out = clientes_routes._normalize_areas_comunes_selected(
        ["todas_anteriores"],
        CHOICES,
    )

    assert "todas_anteriores" not in out
    assert set(out) == {"sala", "comedor", "cocina"}


def test_areas_todas_anteriores_never_persists_even_if_combined_with_other_values():
    out = clientes_routes._normalize_areas_comunes_selected(
        ["todas_anteriores", "sala", "otro"],
        CHOICES,
    )

    assert "todas_anteriores" not in out
    assert "sala" in out
    assert "otro" in out


def test_areas_legacy_value_todas_anteriores_can_still_be_read_compatibly():
    legacy = ["todas_anteriores", "cocina"]
    out = clientes_routes._normalize_areas_comunes_selected(legacy, CHOICES)

    assert "todas_anteriores" not in out
    assert set(out) >= {"sala", "comedor", "cocina"}
