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
    assert BASE_SALARY_MAP["sd_l_v"] == 16000
    assert BASE_SALARY_MAP["sd_l_s"] == 17000
    assert BASE_SALARY_MAP["sd_fin_semana"] == 11000
    assert BASE_SALARY_MAP["cd_l_v"] == 20000
    assert BASE_SALARY_MAP["cd_l_s"] == 22000
    assert BASE_SALARY_MAP["cd_quincenal"] == 25000
    assert BASE_SALARY_MAP["cd_fin_semana"] == 14000


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

