from types import SimpleNamespace

from utils.horario_mode import apply_horario_to_solicitud, build_horario_from_form


def test_horario_salida_diaria_compone_texto_correcto():
    horario, payload, errors = build_horario_from_form(
        modalidad_group="con_salida_diaria",
        modalidad_trabajo="Salida diaria - lunes a viernes",
        dias_trabajo="Lunes a viernes",
        hora_entrada="8:00 AM",
        hora_salida="5:00 PM",
        dormida_entrada="",
        dormida_salida="",
        horario_legacy="",
    )
    assert not errors
    assert horario == "Lunes a viernes, de 8:00 AM a 5:00 PM"
    assert payload["horario_tipo"] == "salida_diaria"


def test_horario_con_dormida_compone_texto_correcto():
    horario, payload, errors = build_horario_from_form(
        modalidad_group="con_dormida",
        modalidad_trabajo="Con dormida 💤 lunes a sábado",
        dias_trabajo="",
        hora_entrada="",
        hora_salida="",
        dormida_entrada="lunes 8:00 AM",
        dormida_salida="sábado 12:00 PM",
        horario_legacy="",
    )
    assert not errors
    assert horario == "Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM"
    assert payload["horario_tipo"] == "con_dormida"


def test_apply_horario_guarda_en_horario_y_detalles():
    s = SimpleNamespace(horario="", detalles_servicio=None)
    apply_horario_to_solicitud(
        s,
        modalidad_group="con_salida_diaria",
        modalidad_trabajo="Salida diaria",
        dias_trabajo="Lunes a viernes",
        hora_entrada="8:00 AM",
        hora_salida="5:00 PM",
        dormida_entrada="",
        dormida_salida="",
        horario_legacy="",
    )
    assert s.horario == "Lunes a viernes, de 8:00 AM a 5:00 PM"
    assert (s.detalles_servicio or {}).get("dias_trabajo") == "Lunes a viernes"
