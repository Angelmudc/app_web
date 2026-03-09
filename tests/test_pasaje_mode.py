# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from utils.pasaje_mode import (
    apply_pasaje_to_solicitud,
    normalize_pasaje_mode_text,
    read_pasaje_mode_text,
    strip_pasaje_marker_from_note,
)


def test_normalize_pasaje_mode_text_for_incluido_and_aparte():
    mode_i, text_i = normalize_pasaje_mode_text("incluido", "texto ignorado")
    mode_a, text_a = normalize_pasaje_mode_text("aparte", "texto ignorado")

    assert mode_i == "incluido"
    assert text_i == ""
    assert mode_a == "aparte"
    assert text_a == ""


def test_normalize_pasaje_mode_text_for_otro_keeps_text():
    mode, text = normalize_pasaje_mode_text("otro", "pasaje los sabados")
    assert mode == "otro"
    assert text == "pasaje los sabados"


def test_apply_pasaje_to_solicitud_saves_otro_in_pasaje_storage_not_note():
    solicitud = SimpleNamespace(
        pasaje_aporte=False,
        detalles_servicio=None,
        nota_cliente="nota original",
    )

    mode, text = apply_pasaje_to_solicitud(
        solicitud,
        mode_raw="otro",
        text_raw="transporte hasta 2000",
    )

    assert mode == "otro"
    assert text == "transporte hasta 2000"
    assert solicitud.pasaje_aporte is False
    assert solicitud.detalles_servicio == {
        "pasaje": {"mode": "otro", "text": "transporte hasta 2000"}
    }
    assert solicitud.nota_cliente == "nota original"


def test_read_pasaje_mode_text_prefers_pasaje_storage_for_edit_prefill():
    mode, text = read_pasaje_mode_text(
        pasaje_aporte=False,
        detalles_servicio={"pasaje": {"mode": "otro", "text": "pasaje nocturno"}},
        nota_cliente="",
    )

    assert mode == "otro"
    assert text == "pasaje nocturno"


def test_read_pasaje_mode_text_supports_legacy_note_marker_without_breaking_old_data():
    mode, text = read_pasaje_mode_text(
        pasaje_aporte=False,
        detalles_servicio=None,
        nota_cliente="Nota cliente\nPasaje (otro): pasaje especial\nOtro detalle",
    )

    assert mode == "otro"
    assert text == "pasaje especial"


def test_strip_pasaje_marker_from_note_removes_only_legacy_pasaje_line():
    note = "Linea 1\nPasaje (otro): pasaje especial\nLinea 2"
    cleaned = strip_pasaje_marker_from_note(note)

    assert cleaned == "Linea 1\nLinea 2"


def test_read_pasaje_mode_text_keeps_legacy_boolean_pasaje_when_no_otro_data():
    mode_true, text_true = read_pasaje_mode_text(
        pasaje_aporte=True,
        detalles_servicio=None,
        nota_cliente="",
    )
    mode_false, text_false = read_pasaje_mode_text(
        pasaje_aporte=False,
        detalles_servicio=None,
        nota_cliente="",
    )

    assert (mode_true, text_true) == ("aparte", "")
    assert (mode_false, text_false) == ("incluido", "")
