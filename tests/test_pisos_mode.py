from types import SimpleNamespace

from utils.pisos_mode import apply_pisos_to_solicitud, read_pisos_value


def test_apply_pisos_to_solicitud_guarda_cantidad_pisos_en_detalles():
    s = SimpleNamespace(dos_pisos=False, detalles_servicio=None)
    out = apply_pisos_to_solicitud(s, pisos_raw="3+")
    assert out == "3+"
    assert s.dos_pisos is True
    assert (s.detalles_servicio or {}).get("cantidad_pisos") == "3+"


def test_read_pisos_value_usa_detalles_servicio_antes_que_booleano():
    out = read_pisos_value(
        dos_pisos=True,
        detalles_servicio={"cantidad_pisos": "1"},
        nota_cliente="",
    )
    assert out == "1"


def test_read_pisos_value_acepta_legacy_marker_en_nota():
    out = read_pisos_value(
        dos_pisos=False,
        detalles_servicio=None,
        nota_cliente="Observacion\nPisos reportados: 3+.",
    )
    assert out == "3+"
