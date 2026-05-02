# -*- coding: utf-8 -*-

from utils.sueldo_sugerido import BASE_SALARY_MAP, analyze_salary_suggestion


def _base_payload(**overrides):
    data = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "2",
        "banos": "2",
        "pisos": "1",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": ["sala", "comedor"],
        "ninos": "0",
        "edades_ninos": "",
        "adultos": "2",
        "sueldo": "18000",
        "envejeciente_tipo_cuidado": "",
        "envejeciente_responsabilidades": [],
    }
    data.update(overrides)
    return data


def test_base_salary_map_expected_values():
    assert BASE_SALARY_MAP["sd_1_dia"] == 5000
    assert BASE_SALARY_MAP["sd_2_dias"] == 9500
    assert BASE_SALARY_MAP["sd_3_dias"] == 12500
    assert BASE_SALARY_MAP["sd_4_dias"] == 15000
    assert BASE_SALARY_MAP["sd_l_v"] == 16000
    assert BASE_SALARY_MAP["sd_l_s"] == 17000
    assert BASE_SALARY_MAP["sd_fin_semana"] == 11000
    assert BASE_SALARY_MAP["cd_l_v"] == 20000
    assert BASE_SALARY_MAP["cd_l_s"] == 22000
    assert BASE_SALARY_MAP["cd_quincenal"] == 25000
    assert BASE_SALARY_MAP["cd_fin_semana"] == 14000


def test_sd_1_dia_solo_ninera_base_4500():
    r = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - 1 día a la semana",
            funciones=["ninos"],
            tipo_lugar="casa",
            ninos="1",
            edades_ninos="7",
            horario="Lunes, de 8:00 AM a 5:00 PM",
        )
    )
    assert r["can_suggest"] is True
    assert r["base_salary"] == 4500


def test_sd_2_dias_solo_ninera_base_8000():
    r = analyze_salary_suggestion(
        _base_payload(modalidad_trabajo="Salida diaria - 2 días a la semana", funciones=["ninos"], ninos="1", edades_ninos="7")
    )
    assert r["can_suggest"] is True
    assert r["base_salary"] == 8000


def test_sd_3_dias_solo_ninera_base_10500():
    r = analyze_salary_suggestion(
        _base_payload(modalidad_trabajo="Salida diaria - 3 días a la semana", funciones=["ninos"], ninos="1", edades_ninos="7")
    )
    assert r["can_suggest"] is True
    assert r["base_salary"] == 10500


def test_sd_4_dias_solo_ninera_base_12500():
    r = analyze_salary_suggestion(
        _base_payload(modalidad_trabajo="Salida diaria - 4 días a la semana", funciones=["ninos"], ninos="1", edades_ninos="7")
    )
    assert r["can_suggest"] is True
    assert r["base_salary"] == 12500


def test_sd_5_dias_solo_ninera_base_15000():
    r = analyze_salary_suggestion(_base_payload(modalidad_trabajo="Salida diaria - lunes a viernes", funciones=["ninos"], ninos="1", edades_ninos="7"))
    assert r["can_suggest"] is True
    assert r["base_salary"] == 15000


def test_sd_5_dias_domestica_base_18000():
    r = analyze_salary_suggestion(_base_payload(modalidad_trabajo="Salida diaria - lunes a viernes", funciones=["limpieza"]))
    assert r["can_suggest"] is True
    assert r["base_salary"] == 18000


def test_sd_5_dias_domestica_y_ninera_base_20000():
    r = analyze_salary_suggestion(_base_payload(modalidad_trabajo="Salida diaria - lunes a viernes", funciones=["limpieza", "ninos"], ninos="1", edades_ninos="7"))
    assert r["can_suggest"] is True
    assert r["base_salary"] == 20000


def test_no_sugerir_para_modalidad_otro_y_tipo_lugar_ambiguo_y_horario_incompleto():
    r1 = analyze_salary_suggestion(_base_payload(modalidad_trabajo="Salida diaria otro"))
    r2 = analyze_salary_suggestion(_base_payload(tipo_lugar="oficina"))
    r3 = analyze_salary_suggestion(_base_payload(tipo_lugar="otro"))
    r4 = analyze_salary_suggestion(_base_payload(horario=""))
    assert r1["can_suggest"] is False
    assert r2["can_suggest"] is False
    assert r3["can_suggest"] is False
    assert r4["can_suggest"] is False


def test_ajustes_planchado_casa_ninos_horario_envejeciente_y_adultos():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar", "planchar", "ninos", "envejeciente"],
            habitaciones="4",
            banos="4",
            pisos="3+",
            ninos="2",
            edades_ninos="2 y 5",
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="8:00 PM",
            adultos="6",
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["pampers", "higiene"],
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_min"] > result["base_salary"]
    assert result["load_level"] in {"alta", "muy_alta"}
    assert result["warnings"]


def test_ninos_grandes_no_suben_fuerte():
    base = analyze_salary_suggestion(_base_payload(funciones=["limpieza", "cocinar", "lavar", "ninos"], ninos="2", edades_ninos="8 y 10"))
    small = analyze_salary_suggestion(_base_payload(funciones=["limpieza", "cocinar", "lavar", "ninos"], ninos="2", edades_ninos="3 y 5"))
    assert base["can_suggest"] is True and small["can_suggest"] is True
    assert small["suggested_min"] > base["suggested_min"]


def test_nino_7_anos_no_aumenta_fuerte():
    with_nino_7 = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="1", edades_ninos="7")
    )
    with_nino_1 = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="1", edades_ninos="1")
    )
    assert with_nino_7["can_suggest"] is True
    assert with_nino_7["suggested_min"] < with_nino_1["suggested_min"]
    assert "mas de supervision que de atencion directa" in with_nino_7["message"].lower()


def test_ninos_8_y_10_no_aumentan_fuerte():
    with_ninos_mayores = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="2", edades_ninos="8 y 10")
    )
    with_ninos_pequenos = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="2", edades_ninos="2 y 4")
    )
    assert with_ninos_mayores["can_suggest"] is True
    assert with_ninos_mayores["suggested_min"] < with_ninos_pequenos["suggested_min"]


def test_ninos_3_y_7_si_cuenta_por_nino_pequeno():
    mayores = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="2", edades_ninos="7 y 8")
    )
    mixto = analyze_salary_suggestion(
        _base_payload(funciones=["ninos"], ninos="2", edades_ninos="1 y 7")
    )
    assert mixto["can_suggest"] is True and mayores["can_suggest"] is True
    assert mixto["suggested_min"] > mayores["suggested_min"]


def test_todas_las_funciones_con_ninos_grandes_no_dispara_rango():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar", "planchar", "ninos"],
            ninos="2",
            edades_ninos="8 y 10",
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="5:00 PM",
            habitaciones="2",
            banos="2",
            adultos="2",
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_max"] <= 23000


def test_cuidar_ninos_y_cocinar_sin_limpieza_se_mantiene_como_ninera_con_ajuste_leve():
    base = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["ninos"],
            ninos="1",
            edades_ninos="8",
        )
    )
    extra = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["ninos", "cocinar"],
            ninos="1",
            edades_ninos="8",
        )
    )
    assert base["can_suggest"] is True and extra["can_suggest"] is True
    assert base["base_salary"] == 15000
    assert extra["base_salary"] == 15000
    assert (extra["suggested_min"] - base["suggested_min"]) <= 1000


def test_sin_limpieza_con_ninera_valida_si_sugiere():
    r = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - 2 días a la semana",
            funciones=["ninos", "cocinar"],
            ninos="1",
            edades_ninos="7",
            tipo_lugar="casa",
        )
    )
    assert r["can_suggest"] is True


def test_envejeciente_independiente_sin_limpieza_si_sugiere():
    r = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="independiente",
            tipo_lugar="casa",
        )
    )
    assert r["can_suggest"] is True
    assert r["suggested_min"] > r["base_salary"]


def test_envejeciente_encamado_sin_limpieza_sube_mas_que_independiente():
    indep = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="independiente",
            tipo_lugar="casa",
        )
    )
    enc = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            tipo_lugar="casa",
        )
    )
    assert indep["can_suggest"] is True and enc["can_suggest"] is True
    assert enc["suggested_min"] > indep["suggested_min"]


def test_ninera_sin_habitaciones_banos_sigue_sugiriendo():
    r = analyze_salary_suggestion(
        _base_payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="7",
            habitaciones="",
            banos="",
            tipo_lugar="",
        )
    )
    assert r["can_suggest"] is True


def test_envejeciente_con_cocinar_sin_limpieza_sugiere():
    r = analyze_salary_suggestion(
        _base_payload(
            funciones=["envejeciente", "cocinar"],
            envejeciente_tipo_cuidado="independiente",
            habitaciones="",
            banos="",
            tipo_lugar="",
        )
    )
    assert r["can_suggest"] is True


def test_encamado_con_responsabilidades_sube_fuerte():
    base_enc = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=[],
            tipo_lugar="casa",
        )
    )
    strong_enc = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["pampers", "higiene", "medicamentos"],
            tipo_lugar="casa",
        )
    )
    assert strong_enc["suggested_min"] > base_enc["suggested_min"]
    assert strong_enc["warnings"]


def test_encamado_solo_acompanamiento_no_sube_como_encamado_completo():
    solo = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_solo_acompanamiento="y",
            envejeciente_responsabilidades=[],
            tipo_lugar="casa",
        )
    )
    full = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["pampers", "higiene"],
            tipo_lugar="casa",
        )
    )
    assert solo["can_suggest"] is True and full["can_suggest"] is True
    assert solo["suggested_min"] < full["suggested_min"]


def test_envejeciente_y_limpieza_marca_perfil_combinado():
    r = analyze_salary_suggestion(
        _base_payload(
            modalidad_trabajo="Salida diaria - lunes a viernes",
            funciones=["limpieza", "envejeciente"],
            envejeciente_tipo_cuidado="independiente",
            tipo_lugar="casa",
        )
    )
    assert r["can_suggest"] is True
    labels = [a.get("label", "") for a in (r.get("adjustments") or [])]
    assert any("Perfil combinado: domestica + cuidado de envejeciente." in l for l in labels)


def test_offer_status_competitiva_baja_muy_baja():
    payload = _base_payload(sueldo="16000")
    r = analyze_salary_suggestion(payload)
    payload_baja = _base_payload(sueldo=str(max(1, r["suggested_min"] - 1500)))
    payload_muy_baja = _base_payload(sueldo=str(max(1, r["suggested_min"] - 3500)))
    r_comp = analyze_salary_suggestion(_base_payload(sueldo=str(r["suggested_min"])))
    r_baja = analyze_salary_suggestion(payload_baja)
    r_muy_baja = analyze_salary_suggestion(payload_muy_baja)
    assert r_comp["offer_status"] == "competitiva"
    assert r_baja["offer_status"] == "baja"
    assert r_muy_baja["offer_status"] == "muy_baja"


def test_salary_message_includes_intro_why_warning_and_flexible_close():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar", "planchar", "ninos", "envejeciente"],
            habitaciones="4",
            banos="4",
            pisos="3+",
            ninos="2",
            edades_ninos="2 y 5",
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="8:00 PM",
            adultos="6",
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["pampers", "higiene"],
            sueldo="13000",
        )
    )
    msg = result["message"]
    assert "Para este tipo de solicitud, el sueldo suele estar entre RD$" in msg
    assert "¿Por que?" in msg
    assert "- " in msg
    assert "puede dificultar encontrar una candidata disponible o adecuada." in msg
    assert "Puedes ajustar el monto segun tu presupuesto" in msg


def test_salary_message_low_load_uses_relaxed_wording():
    result = analyze_salary_suggestion(_base_payload(funciones=["limpieza"], ninos="0", adultos="1"))
    msg = result["message"]
    assert "Por el nivel de exigencia" not in msg
    assert "Ofrecer menos puede dificultar encontrar una candidata disponible o adecuada." in msg


def test_sd_lv_10h_is_moderate():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar"],
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="6:00 PM",
            adultos="2",
            ninos="0",
            habitaciones="2",
            banos="2",
            pisos="1",
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_max"] <= 21000


def test_sd_lv_11h_is_reasonable():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar"],
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="7:00 PM",
            adultos="2",
            ninos="0",
            habitaciones="2",
            banos="2",
            pisos="1",
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_max"] <= 21000


def test_sd_lv_12h_without_other_heavy_loads_keeps_cap_near_21k():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar"],
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="8:00 PM",
            adultos="2",
            ninos="0",
            habitaciones="2",
            banos="2",
            pisos="1",
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_max"] <= 21000


def test_sd_lv_12h_with_heavy_loads_can_exceed_21k():
    result = analyze_salary_suggestion(
        _base_payload(
            funciones=["limpieza", "cocinar", "lavar", "planchar", "ninos"],
            horario_hora_entrada="8:00 AM",
            horario_hora_salida="8:00 PM",
            ninos="2",
            edades_ninos="2 y 4",
            habitaciones="4",
            banos="4",
            pisos="3+",
            adultos="6",
        )
    )
    assert result["can_suggest"] is True
    assert result["suggested_max"] > 21000


def test_salary_message_always_mentions_pasaje():
    result = analyze_salary_suggestion(_base_payload())
    assert result["can_suggest"] is True
    assert "ayuda de pasaje" in result["message"].lower()
