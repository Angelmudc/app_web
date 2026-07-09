# -*- coding: utf-8 -*-

from services.solicitud_atractivo_service import (
    ATTRACTIVE_VERSION,
    LABEL_ATRACTIVA,
    LABEL_DIFICIL,
    LABEL_POCO,
    evaluate_solicitud_atractivo,
)


def _payload(**overrides):
    data = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "sueldo": "18000",
        "envejeciente_tipo_cuidado": "",
        "envejeciente_responsabilidades": [],
        "funciones": ["limpieza"],
        "areas_comunes": ["sala"],
    }
    data.update(overrides)
    return data


def test_servicio_devuelve_estructura_base():
    result = evaluate_solicitud_atractivo(_payload())
    assert result["version"] == ATTRACTIVE_VERSION
    assert isinstance(result["score"], int)
    assert isinstance(result["label"], str)
    assert isinstance(result["motivos"], list)
    assert isinstance(result["componentes"], dict)


def test_1_solicitud_estandar_l_v_8_a_5_con_limpieza_es_atractiva():
    result = evaluate_solicitud_atractivo(_payload())
    assert result["label"] == LABEL_ATRACTIVA
    assert 70 <= result["score"] <= 84


def test_2_l_s_8_a_7_limpieza_cocinar_lavar_es_dificil():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Salida diaria - lunes a sábado",
            horario="Lunes a sábado, de 8:00 AM a 7:00 PM",
            horario_hora_salida="7:00 PM",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["label"] == LABEL_DIFICIL
    assert result["score"] <= 34


def test_3_con_dormida_l_v_bebe_solamente_es_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 lunes a viernes",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 5:00 PM",
            funciones=["ninos"],
            ninos="1",
            edades_ninos="8 meses",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["label"] == LABEL_ATRACTIVA
    assert result["score"] >= 70


def test_4_con_dormida_l_s_bebe_mas_hogar_completo_es_dificil():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="1",
            edades_ninos="2 años",
        )
    )
    assert result["label"] == LABEL_DIFICIL
    assert result["score"] <= 34


def test_5_encamado_mas_limpieza_cocinar_lavar_es_dificil():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
        )
    )
    assert result["label"] == LABEL_DIFICIL
    assert result["score"] <= 34


def test_6_casa_4_4_mas_4_adultos_mas_limpieza_es_poco_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            habitaciones="4",
            banos="4",
            adultos="4",
            funciones=["limpieza"],
        )
    )
    assert result["label"] == LABEL_POCO
    assert 35 <= result["score"] <= 54


def test_7_con_dormida_salida_quincenal_es_poco_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 quincenal",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 12:00 PM",
            funciones=["limpieza"],
        )
    )
    assert result["label"] == LABEL_POCO
    assert 35 <= result["score"] <= 54


def test_8_ninos_mayores_de_5_no_penalizan_por_si_solos():
    older = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="2",
            edades_ninos="7 y 10 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    small = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="2",
            edades_ninos="2 y 4 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert older["score"] > small["score"]
    assert not any(item["key"] == "combo_ninos_pequenos" and item["amount"] < 0 for item in older["componentes"]["items"])


def test_9_nino_pequeno_sin_limpieza_no_penaliza_fuerte():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="3 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] >= 70
    penalty = next(
        (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
        0,
    )
    assert penalty >= -2


def test_10_nino_pequeno_mas_limpieza_si_penaliza():
    with_small = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza"],
            ninos="1",
            edades_ninos="2 años",
        )
    )
    without_small = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza"],
            ninos="1",
            edades_ninos="7 años",
        )
    )
    assert with_small["score"] < without_small["score"]


def test_11_salario_alto_no_convierte_dificil_en_muy_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="2",
            edades_ninos="1 y 3 años",
            sueldo="50000",
        )
    )
    assert result["label"] != "Muy atractiva"
    assert result["label"] in {LABEL_DIFICIL, LABEL_POCO}
