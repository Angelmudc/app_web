# -*- coding: utf-8 -*-

import math
from types import SimpleNamespace

import pytest

from services.solicitud_atractivo_service import (
    ATTRACTIVE_VERSION,
    LABEL_ATRACTIVA,
    LABEL_DIFICIL,
    LABEL_MUY_ATRACTIVA,
    LABEL_POCO,
    LABEL_REGULAR,
    apply_salary_excellence_cap,
    apply_solicitud_atractivo_to_model,
    evaluate_solicitud_atractivo,
)
import services.solicitud_atractivo_service as atractivo_service
from utils.sueldo_sugerido import analyze_salary_suggestion, classify_schedule


def _payload(**overrides):
    data = {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_tipo": "salida_diaria",
        "dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "dormida_entrada": "",
        "dormida_salida": "",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "nota_cliente": "",
        "descripcion": "",
        "observaciones": "",
        "sueldo": "18000",
        "envejeciente_tipo_cuidado": "",
        "envejeciente_responsabilidades": [],
        "funciones": ["limpieza"],
        "areas_comunes": ["sala"],
    }
    data.update(overrides)
    return data


def _ui_cliff_payload(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
        horario_hora_salida="4:00 PM",
        horario_tipo="salida_diaria",
        dias_trabajo="Lunes a viernes",
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina"],
        sueldo="20000",
        pasaje_mode="incluido",
    )
    data.update(overrides)
    return data


def _dormida_payload(**overrides):
    data = _payload(
        modalidad_trabajo="Con dormida 💤 lunes a viernes",
        horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        horario_tipo="con_dormida",
        dias_trabajo="",
        horario_hora_entrada="",
        horario_hora_salida="",
        dormida_entrada="lunes 8:00 AM",
        dormida_salida="viernes 4:00 PM",
        sueldo="25000",
        funciones=["limpieza"],
    )
    data.update(overrides)
    return data


def _weekend_payload(**overrides):
    data = _payload(
        modalidad_trabajo="Salida diaria - fin de semana",
        horario="Sábado y domingo, de 8:00 AM a 4:00 PM",
        horario_tipo="salida_diaria",
        dias_trabajo="Sábado y domingo",
        horario_hora_entrada="8:00 AM",
        horario_hora_salida="4:00 PM",
        sueldo="13500",
        funciones=["limpieza"],
    )
    data.update(overrides)
    return data


def _noncritical_monotonic_payload(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
        horario_hora_salida="4:00 PM",
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina"],
        sueldo="19500",
        pasaje_mode="no_incluido",
        detalles_servicio={"pasaje": {"mode": "no_incluido"}},
        nota_cliente="Los niños no requieren cuidado real. Solicitud no crítica.",
        descripcion="",
        observaciones="",
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
    )
    data.update(overrides)
    return data


def _ui_room_continuity_payload(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
        horario_hora_salida="5:00 PM",
        tipo_lugar="casa",
        habitaciones="3",
        banos="2",
        pisos="1",
        adultos="4",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "lavar"],
        areas_comunes=["terraza", "patio"],
        sueldo="20000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
        nota_cliente="",
        descripcion="",
        observaciones="",
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
    )
    data.update(overrides)
    return data


def _clamp(value, low=0, high=100):
    return max(float(low), min(float(high), math.floor((float(value) * 10) + 0.5) / 10))


def _component_amount(result, key):
    return next((item["amount"] for item in result["componentes"]["items"] if item["key"] == key), 0.0)


def _bucket_amount(result, bucket):
    return sum(float(item["amount"]) for item in result["componentes"]["items"] if item["bucket"] == bucket)


def _salary_relief(result):
    salary_reference = result["componentes"].get("salary_reference") or {}
    return salary_reference.get("load_relief")


def _salary_range(result):
    salary_reference = result["componentes"].get("salary_reference") or {}
    return (
        salary_reference.get("reference_min"),
        salary_reference.get("reference_max"),
    )


def _household_physical_penalty(result):
    return sum(
        float(item["amount"])
        for item in result["componentes"]["items"]
        if item["key"] in {"hogar_carga_fisica", "hogar_3_pisos"}
    )


def test_servicio_devuelve_estructura_base():
    result = evaluate_solicitud_atractivo(_payload())
    assert result["version"] == ATTRACTIVE_VERSION
    assert isinstance(result["score"], float)
    assert isinstance(result["label"], str)
    assert isinstance(result["motivos"], list)
    assert isinstance(result["componentes"], dict)


def test_persistencia_del_modelo_mantiene_entero_y_preview_sigue_decimal():
    solicitud = SimpleNamespace(
        modalidad_trabajo="Salida diaria - lunes a viernes",
        horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
        tipo_lugar="casa",
        habitaciones="2",
        banos="2",
        adultos="2",
        ninos="0",
        edades_ninos="",
        sueldo="20500",
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala"],
        detalles_servicio={"hora_entrada": "8:00 AM", "hora_salida": "4:00 PM", "pasaje": {"mode": "aparte"}},
        atractivo_score=None,
        atractivo_label=None,
        atractivo_motivos=None,
        atractivo_version=None,
        atractivo_calculated_at=None,
    )

    result = apply_solicitud_atractivo_to_model(solicitud)

    assert isinstance(result["score"], float)
    assert result["score"] != int(result["score"])
    assert solicitud.atractivo_score == int(result["score"])
    assert isinstance(solicitud.atractivo_score, int)


def _salary_cap_favorable_payload(**overrides):
    data = _payload(
        modalidad_trabajo="Salida diaria - lunes a viernes",
        horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
        horario_tipo="salida_diaria",
        dias_trabajo="Lunes a viernes",
        horario_hora_entrada="8:00 AM",
        horario_hora_salida="4:00 PM",
        tipo_lugar="apto",
        habitaciones="2",
        banos="2",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala"],
        sueldo="20000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )
    data.update(overrides)
    return data


def _cap_meta(result):
    return result["componentes"]


def _pre_salary_cap_score(result):
    return float(result["componentes"].get("score_before_salary_excellence_cap", result["score"]))


def _pre_quincenal_band_cap_score(result):
    return float(result["componentes"].get("score_before_quincenal_salary_band_cap", _pre_salary_cap_score(result)))


def _dormida_modalidad_base(**overrides):
    data = _payload(
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina"],
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
    )
    data.update(overrides)
    return data


def _dormida_mode_payload(mode: str, **overrides):
    variants = {
        "lv": {
            "modalidad_trabajo": "Con dormida 💤 lunes a viernes",
            "horario": "Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            "horario_tipo": "con_dormida",
            "dias_trabajo": "",
            "horario_hora_entrada": "",
            "horario_hora_salida": "",
            "dormida_entrada": "lunes 8:00 AM",
            "dormida_salida": "viernes 4:00 PM",
        },
        "weekend": {
            "modalidad_trabajo": "Con dormida 💤 fin de semana",
            "horario": "Entrada: viernes 5:00 PM / Salida: lunes 8:00 AM",
            "horario_tipo": "con_dormida",
            "dias_trabajo": "",
            "horario_hora_entrada": "",
            "horario_hora_salida": "",
            "dormida_entrada": "viernes 5:00 PM",
            "dormida_salida": "lunes 8:00 AM",
        },
        "ls": {
            "modalidad_trabajo": "Con dormida 💤 lunes a sábado",
            "horario": "Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
            "horario_tipo": "con_dormida",
            "dias_trabajo": "",
            "horario_hora_entrada": "",
            "horario_hora_salida": "",
            "dormida_entrada": "lunes 8:00 AM",
            "dormida_salida": "sábado 1:00 PM",
        },
        "quincenal": {
            "modalidad_trabajo": "Con dormida 💤 quincenal",
            "horario": "Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
            "horario_tipo": "con_dormida",
            "dias_trabajo": "",
            "horario_hora_entrada": "",
            "horario_hora_salida": "",
            "dormida_entrada": "lunes 8:00 AM",
            "dormida_salida": "segundo viernes 12:00 PM",
        },
    }
    data = _dormida_modalidad_base(**variants[mode])
    ref = analyze_salary_suggestion(data)
    ref_min = int(ref["suggested_min"])
    ref_max = int(ref["suggested_max"])
    data["sueldo"] = str(round((ref_min + ref_max) / 2))
    data.update(overrides)
    return data


def test_dormida_modalidades_jerarquia_base_y_continuidad():
    audited = {mode: evaluate_solicitud_atractivo(_dormida_mode_payload(mode)) for mode in ["lv", "weekend", "ls", "quincenal"]}
    raw = {mode: _pre_quincenal_band_cap_score(result) for mode, result in audited.items()}

    assert 86.0 <= audited["lv"]["score"] <= 89.0
    assert 78.0 <= audited["weekend"]["score"] <= 86.0
    assert 82.0 <= audited["ls"]["score"] <= 87.0
    assert 83.0 <= audited["quincenal"]["score"] <= 85.0
    assert audited["quincenal"]["componentes"]["quincenal_salary_band_cap_applied"] is False
    assert raw["lv"] >= raw["ls"]
    assert raw["ls"] > raw["quincenal"] > raw["weekend"]
    assert audited["lv"]["score"] > audited["ls"]["score"] > audited["quincenal"]["score"] > audited["weekend"]["score"]
    assert raw["lv"] > raw["weekend"]
    assert raw["lv"] - raw["ls"] <= 6.0
    assert 2.0 <= raw["ls"] - raw["quincenal"] <= 3.0


def test_dormida_modalidades_rangos_salariales_base():
    ranges = {}
    for mode in ["lv", "weekend", "ls", "quincenal"]:
        payload = _dormida_mode_payload(mode, sueldo="")
        ref = analyze_salary_suggestion(payload)
        ranges[mode] = (ref["suggested_min"], ref["suggested_max"], round((ref["suggested_min"] + ref["suggested_max"]) / 2))

    assert ranges["lv"] == (20000, 22000, 21000)
    assert ranges["ls"] == (21000, 23000, 22000)
    assert ranges["quincenal"] == (24000, 26000, 25000)
    assert ranges["weekend"] == (12500, 14500, 13500)


def test_dormida_modalidades_sueldo_mayor_no_reduce_y_cap_aplica_en_rango():
    for mode in ["lv", "weekend", "ls", "quincenal"]:
        payload = _dormida_mode_payload(mode, sueldo="")
        ref = analyze_salary_suggestion(payload)
        ref_min = int(ref["suggested_min"])
        ref_max = int(ref["suggested_max"])
        salaries = [ref_min, round((ref_min + ref_max) / 2), ref_max, round(ref_max * 1.03), round(ref_max * 1.05)]
        results = [evaluate_solicitud_atractivo(dict(payload, sueldo=str(salary))) for salary in salaries]
        scores = [result["score"] for result in results]

        assert scores == sorted(scores)
        assert results[2]["score"] <= 89.0
        assert results[3]["score"] <= 89.5
        assert results[4]["componentes"]["salary_excellence_cap_value"] is None


def test_dormida_modalidades_apartamento_y_cargas_no_rompen_orden_base():
    for overrides in [
        {"tipo_lugar": "apto", "habitaciones": "2", "banos": "2"},
        {"habitaciones": "4", "banos": "4"},
        {"adultos": "4"},
        {"funciones": ["limpieza", "ninos"], "ninos": "2", "edades_ninos": "7 y 10 años"},
        {"funciones": ["limpieza", "ninos"], "ninos": "2", "edades_ninos": "2 y 3 años", "ayuda_cuidado_ninos": "sin_ayuda"},
        {"funciones": ["limpieza", "ninos"], "ninos": "2", "edades_ninos": "2 y 3 años", "ayuda_cuidado_ninos": "con_ayuda"},
    ]:
        audited = {
            mode: evaluate_solicitud_atractivo(_dormida_mode_payload(mode, **overrides))
            for mode in ["lv", "weekend", "ls", "quincenal"]
        }
        raw = {mode: _pre_quincenal_band_cap_score(result) for mode, result in audited.items()}
        assert raw["lv"] >= raw["ls"]
        assert abs(raw["quincenal"] - raw["weekend"]) <= 2.0
        assert audited["quincenal"]["componentes"]["quincenal_salary_band_cap_value"] == 85.0


def test_dormida_fin_de_semana_horarios_y_cap_global():
    entries = [
        evaluate_solicitud_atractivo(_dormida_mode_payload("weekend", dormida_entrada=entry, sueldo=""))
        for entry in ["sábado 8:00 AM", "sábado 10:00 AM", "sábado 12:00 PM", "sábado 2:00 PM", "viernes 5:00 PM"]
    ]
    exits = [
        evaluate_solicitud_atractivo(_dormida_mode_payload("weekend", dormida_salida=exit_time, sueldo=""))
        for exit_time in ["lunes 8:00 AM", "lunes 10:00 AM", "lunes 12:00 PM", "lunes 2:00 PM", "lunes 4:00 PM"]
    ]
    best = evaluate_solicitud_atractivo(_dormida_mode_payload("weekend"))

    entry_scores = [_pre_salary_cap_score(item) for item in entries]
    assert entry_scores[0] > entry_scores[1] == entry_scores[2] > entry_scores[3] == entry_scores[4]
    assert [_pre_salary_cap_score(item) for item in exits] == sorted((_pre_salary_cap_score(item) for item in exits), reverse=True)
    assert best["score"] <= 89.0
    assert not any(item["key"].startswith("horario_1") for item in best["componentes"]["items"])


def test_dormida_quincenal_salida_tarde_es_gradual():
    audited = [
        evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal", dormida_salida=exit_time))
        for exit_time in ["segundo viernes 11:00 AM", "segundo viernes 12:00 PM", "segundo viernes 1:00 PM", "segundo viernes 2:00 PM", "segundo viernes 3:00 PM"]
    ]
    raw_scores = [_pre_quincenal_band_cap_score(item) for item in audited]

    assert raw_scores == sorted(raw_scores, reverse=True)
    assert raw_scores[0] > raw_scores[1] > raw_scores[2] > raw_scores[3] > raw_scores[4]
    assert raw_scores[0] - raw_scores[1] <= 0.5
    assert raw_scores[1] - raw_scores[2] <= 0.5
    assert raw_scores[2] - raw_scores[3] <= 1.5
    assert raw_scores[3] - raw_scores[4] <= 0.5
    assert raw_scores[0] - raw_scores[-1] <= 3.0


def test_dormida_quincenal_base_se_explica_sin_doble_penalizacion():
    result = evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal"))

    assert _pre_quincenal_band_cap_score(result) == 84.1
    assert result["score"] == 84.1
    assert _component_amount(result, "modalidad_cd_quincenal") == -1.5
    assert _component_amount(result, "dormida_quincenal_salida_12pm") == 1
    assert _component_amount(result, "combo_quincenal_carga_fuerte") == 0
    assert _component_amount(result, "bonus_solicitud_normal_atractiva") == 8.0
    assert _bucket_amount(result, "hogar") == -0.4
    assert _bucket_amount(result, "salario") == 6.0
    assert _component_amount(result, "bonus_pasaje") == 2.0
    assert result["componentes"]["quincenal_salary_band_cap_applied"] is False
    assert result["componentes"]["salary_excellence_cap_applied"] is False


def test_dormida_quincenal_matriz_calibrada_y_propiedades():
    base = evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal"))
    apto = evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal", tipo_lugar="apto", habitaciones="2", banos="2"))
    casa_4_4 = evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal", habitaciones="4", banos="4"))
    adultos_4 = evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal", adultos="4"))
    ninos_mayores = evaluate_solicitud_atractivo(
        _dormida_mode_payload("quincenal", funciones=["limpieza", "ninos"], ninos="2", edades_ninos="7 y 10 años")
    )
    pequenos_con_ayuda = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    pequenos_sin_ayuda = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="sin_ayuda",
        )
    )
    encamado = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            funciones=["limpieza", "cocinar", "lavar", "envejeciente"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["aseo", "medicamentos"],
        )
    )
    ref = analyze_salary_suggestion(_dormida_mode_payload("quincenal", sueldo=""))
    salary_results = [
        evaluate_solicitud_atractivo(_dormida_mode_payload("quincenal", sueldo=str(salary)))
        for salary in [
            ref["suggested_min"],
            round((ref["suggested_min"] + ref["suggested_max"]) / 2),
            ref["suggested_max"],
            round(ref["suggested_max"] * 1.05),
        ]
    ]

    assert 83.0 <= base["score"] <= 85.0
    assert 83.0 <= salary_results[0]["score"] <= 84.0
    assert 84.0 <= salary_results[1]["score"] <= 85.0
    assert 84.0 <= salary_results[2]["score"] <= 85.0
    assert salary_results[3]["score"] >= salary_results[2]["score"]
    assert [item["score"] for item in salary_results] == sorted(item["score"] for item in salary_results)
    assert _pre_quincenal_band_cap_score(casa_4_4) < _pre_quincenal_band_cap_score(base)
    assert _pre_quincenal_band_cap_score(adultos_4) < _pre_quincenal_band_cap_score(base)
    assert _pre_quincenal_band_cap_score(ninos_mayores) < _pre_quincenal_band_cap_score(base)
    assert _pre_quincenal_band_cap_score(pequenos_con_ayuda) > _pre_quincenal_band_cap_score(pequenos_sin_ayuda)
    assert _pre_quincenal_band_cap_score(encamado) < _pre_quincenal_band_cap_score(casa_4_4)
    assert _pre_quincenal_band_cap_score(apto) > _pre_quincenal_band_cap_score(base)
    assert all(item["componentes"]["salary_excellence_cap_value"] == 89.0 for item in salary_results[:3])
    assert salary_results[3]["componentes"]["salary_excellence_cap_value"] is None


def _quincenal_mid_salary_payload(**overrides):
    data = _dormida_mode_payload("quincenal", **overrides)
    ref = analyze_salary_suggestion(dict(data, sueldo=""))
    data["sueldo"] = str(round((ref["suggested_min"] + ref["suggested_max"]) / 2))
    return data


def test_dormida_quincenal_cargas_viables_quedan_en_banda_comprimida():
    audited = {
        "base": evaluate_solicitud_atractivo(_quincenal_mid_salary_payload()),
        "apto": evaluate_solicitud_atractivo(_quincenal_mid_salary_payload(tipo_lugar="apto", habitaciones="2", banos="2")),
        "casa_4_4": evaluate_solicitud_atractivo(_quincenal_mid_salary_payload(habitaciones="4", banos="4")),
        "adultos_4": evaluate_solicitud_atractivo(_quincenal_mid_salary_payload(adultos="4")),
        "ninos_mayores": evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="7 y 10 años")
        ),
        "pequenos_con_ayuda": evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "ninos"],
                ninos="2",
                edades_ninos="2 y 3 años",
                ayuda_cuidado_ninos="con_ayuda",
            )
        ),
        "pequenos_sin_ayuda": evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "ninos"],
                ninos="2",
                edades_ninos="2 y 3 años",
                ayuda_cuidado_ninos="sin_ayuda",
            )
        ),
        "encamado": evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "cocinar", "lavar", "envejeciente"],
                envejeciente_tipo_cuidado="encamado",
                envejeciente_responsabilidades=["aseo", "medicamentos"],
            )
        ),
    }

    assert 83.0 <= audited["base"]["score"] <= 85.0
    assert audited["apto"]["score"] == 85.0
    assert 81.0 <= audited["casa_4_4"]["score"] <= 83.0
    assert 81.0 <= audited["adultos_4"]["score"] <= 83.0
    assert 82.0 <= audited["ninos_mayores"]["score"] <= 84.0
    assert 81.0 <= audited["pequenos_con_ayuda"]["score"] <= 83.0
    assert 80.0 <= audited["pequenos_sin_ayuda"]["score"] <= 82.0
    assert 78.0 <= audited["encamado"]["score"] <= 80.0
    assert _pre_quincenal_band_cap_score(audited["apto"]) >= _pre_quincenal_band_cap_score(audited["base"])
    assert _pre_quincenal_band_cap_score(audited["base"]) > _pre_quincenal_band_cap_score(audited["casa_4_4"])
    assert _pre_quincenal_band_cap_score(audited["base"]) > _pre_quincenal_band_cap_score(audited["adultos_4"])
    assert _pre_quincenal_band_cap_score(audited["pequenos_con_ayuda"]) > _pre_quincenal_band_cap_score(audited["pequenos_sin_ayuda"])
    assert _pre_quincenal_band_cap_score(audited["base"]) > _pre_quincenal_band_cap_score(audited["encamado"])
    assert _component_amount(audited["casa_4_4"], "combo_quincenal_carga_fuerte") == 0.0
    assert _component_amount(audited["adultos_4"], "combo_quincenal_carga_fuerte") == 0.0
    assert _component_amount(audited["encamado"], "combo_quincenal_carga_fuerte") == 0.0


def test_dormida_quincenal_techo_salarial_no_deja_bonus_no_salariales_pasar_de_85():
    ref_payload = _dormida_mode_payload("quincenal", tipo_lugar="apto", habitaciones="2", banos="2", sueldo="")
    ref = analyze_salary_suggestion(ref_payload)
    max_salary = ref["suggested_max"]
    at_max = evaluate_solicitud_atractivo(dict(ref_payload, sueldo=str(max_salary)))
    above_3 = evaluate_solicitud_atractivo(dict(ref_payload, sueldo=str(round(max_salary * 1.03))))
    above_5 = evaluate_solicitud_atractivo(dict(ref_payload, sueldo=str(round(max_salary * 1.05))))

    assert _pre_quincenal_band_cap_score(at_max) > 85.0
    assert at_max["score"] == 85.0
    assert at_max["componentes"]["quincenal_salary_band_cap_applied"] is True
    assert at_max["componentes"]["quincenal_salary_band_cap_value"] == 85.0
    assert above_3["score"] > at_max["score"]
    assert above_3["componentes"]["quincenal_salary_band_cap_value"] == 86.5
    assert above_5["score"] >= above_3["score"]
    assert _pre_quincenal_band_cap_score(above_5) > _pre_quincenal_band_cap_score(above_3)
    assert above_5["componentes"]["quincenal_salary_band_cap_value"] == 86.5


def test_dormida_quincenal_matriz_salarial_es_monotona_y_expone_caps():
    scenarios = [
        {},
        {"tipo_lugar": "apto", "habitaciones": "2", "banos": "2"},
        {"habitaciones": "4", "banos": "4"},
        {"adultos": "4"},
        {"funciones": ["limpieza", "ninos"], "ninos": "2", "edades_ninos": "2 y 3 años", "ayuda_cuidado_ninos": "sin_ayuda"},
        {
            "funciones": ["limpieza", "cocinar", "lavar", "envejeciente"],
            "envejeciente_tipo_cuidado": "encamado",
            "envejeciente_responsabilidades": ["aseo", "medicamentos"],
        },
    ]
    for overrides in scenarios:
        ref_payload = _dormida_mode_payload("quincenal", sueldo="", **overrides)
        ref = analyze_salary_suggestion(ref_payload)
        salaries = [
            round(ref["suggested_min"] * 0.9),
            ref["suggested_min"],
            round((ref["suggested_min"] + ref["suggested_max"]) / 2),
            ref["suggested_max"],
            round(ref["suggested_max"] * 1.03),
            round(ref["suggested_max"] * 1.05),
            round(ref["suggested_max"] * 1.10),
        ]
        results = [evaluate_solicitud_atractivo(dict(ref_payload, sueldo=str(salary))) for salary in salaries]

        assert [result["score"] for result in results] == sorted(result["score"] for result in results)
        assert [result["componentes"]["score_before_salary_excellence_cap"] for result in results] == sorted(
            result["componentes"]["score_before_salary_excellence_cap"] for result in results
        )
        assert all(result["score"] <= 85.0 for result in results[1:4])
        assert results[4]["score"] >= results[3]["score"]
        assert results[5]["score"] >= results[4]["score"]
        assert results[6]["score"] >= results[5]["score"]


def test_dormida_quincenal_25000_pasaje_aparte_no_es_sueldo_bajo():
    with_pasaje = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            sueldo="25000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        )
    )
    without_pasaje = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            sueldo="25000",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
        )
    )
    at_min = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            sueldo="24000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        )
    )
    below_min = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            sueldo="23000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        )
    )
    salary_item = next(item for item in with_pasaje["componentes"]["items"] if item["key"] == "salario")

    assert with_pasaje["componentes"]["salary_reference"]["reference_min"] == 24000
    assert with_pasaje["componentes"]["salary_reference"]["reference_max"] == 26000
    assert with_pasaje["componentes"]["salary_reference"]["salary_position"] == "above_min"
    assert with_pasaje["componentes"]["salary_reference"]["offer_status"] == "competitiva"
    assert "por debajo del mínimo" not in salary_item["label"]
    assert salary_item["label"] == "El sueldo ofrecido está por encima del mínimo sugerido."
    assert _pre_quincenal_band_cap_score(with_pasaje) > _pre_quincenal_band_cap_score(at_min) > _pre_quincenal_band_cap_score(below_min)
    assert _pre_quincenal_band_cap_score(with_pasaje) > _pre_quincenal_band_cap_score(without_pasaje)
    assert with_pasaje["score"] > at_min["score"] > below_min["score"]
    assert _component_amount(with_pasaje, "bonus_pasaje") == 2.0


def test_dormida_quincenal_matriz_23000_a_28000_usa_minimo_24000():
    expected = {
        23000: ("below_min", "El sueldo ofrecido está por debajo del mínimo sugerido."),
        24000: ("at_min", "El sueldo ofrecido está dentro del rango mínimo sugerido."),
        25000: ("above_min", "El sueldo ofrecido está por encima del mínimo sugerido."),
        26000: ("at_max", "El sueldo ofrecido mejora el atractivo de la solicitud."),
        27000: ("above_max", "El sueldo ofrecido está hasta 5% por encima del máximo sugerido para quincenal."),
        28000: ("above_max", "El sueldo ofrecido está entre 5% y 15% por encima del máximo sugerido para quincenal, con rendimiento gradual."),
    }
    results = {}
    for salary, (position, label) in expected.items():
        result = evaluate_solicitud_atractivo(
            _dormida_mode_payload(
                "quincenal",
                sueldo=str(salary),
                pasaje_mode="aparte",
                detalles_servicio={"pasaje": {"mode": "aparte"}},
            )
        )
        salary_item = next(item for item in result["componentes"]["items"] if item["key"] == "salario")
        results[salary] = result

        assert result["componentes"]["salary_reference"]["reference_min"] == 24000
        assert result["componentes"]["salary_reference"]["reference_max"] == 26000
        assert result["componentes"]["salary_reference"]["salary_position"] == position
        assert salary_item["label"] == label

    assert [results[salary]["score"] for salary in sorted(results)] == sorted(
        results[salary]["score"] for salary in sorted(results)
    )
    assert results[26000]["score"] <= 85.0
    assert results[27000]["componentes"]["quincenal_salary_band_cap_value"] == 86.5
    assert results[28000]["componentes"]["quincenal_salary_band_cap_value"] == 87.5


def test_dormida_quincenal_sueldo_sobre_maximo_crece_gradual_en_payload_3h3b_2_8():
    base_payload = _dormida_mode_payload(
        "quincenal",
        horario="Entrada: lunes 7:30 AM / Salida: viernes 1:00 PM",
        dormida_entrada="lunes 7:30 AM",
        dormida_salida="viernes 1:00 PM",
        habitaciones="3",
        banos="3",
        adultos="3",
        ninos="2",
        edades_ninos="2 y 8 años",
        ayuda_cuidado_ninos="con_ayuda",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
        sueldo="",
    )
    ref = analyze_salary_suggestion(base_payload)
    assert ref["suggested_max"] == 26250
    salaries = [24000, 25000, 26000, 27000, 28000, 30000]
    results = {
        salary: evaluate_solicitud_atractivo(dict(base_payload, sueldo=str(salary)))
        for salary in salaries
    }

    assert [results[salary]["score"] for salary in salaries] == sorted(results[salary]["score"] for salary in salaries)
    assert results[26000]["score"] == 82.3
    assert results[27000]["score"] == 83.1
    assert results[28000]["score"] == 83.8
    assert results[30000]["score"] == 84.9
    assert _component_amount(results[28000], "salario") == 8.25
    assert _component_amount(results[30000], "salario") == 9.39
    assert results[28000]["componentes"]["quincenal_salary_band_cap_applied"] is False
    assert results[30000]["componentes"]["quincenal_salary_band_cap_applied"] is False


def test_dormida_quincenal_matriz_vivienda_4h4b_suavizada_y_monotona():
    base_payload = _dormida_mode_payload(
        "quincenal",
        horario="Entrada: lunes 7:30 AM / Salida: viernes 1:00 PM",
        dormida_entrada="lunes 7:30 AM",
        dormida_salida="viernes 1:00 PM",
        adultos="3",
        ninos="2",
        edades_ninos="2 y 8 años",
        ayuda_cuidado_ninos="con_ayuda",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        sueldo="25000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )
    sizes = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (4, 3), (3, 4), (5, 4), (4, 5)]
    audited = {
        size: evaluate_solicitud_atractivo(dict(base_payload, habitaciones=str(size[0]), banos=str(size[1])))
        for size in sizes
    }

    assert audited[(3, 3)]["score"] > audited[(4, 4)]["score"] > audited[(5, 5)]["score"] > audited[(6, 6)]["score"]
    assert 77.5 <= audited[(4, 4)]["score"] <= 78.5
    assert round(audited[(3, 3)]["score"] - audited[(4, 4)]["score"], 1) <= 4.0
    assert round(audited[(4, 4)]["score"] - audited[(5, 5)]["score"], 1) <= 4.5
    assert audited[(4, 3)]["score"] == audited[(3, 4)]["score"]
    assert audited[(5, 4)]["score"] == audited[(4, 5)]["score"]
    assert audited[(3, 3)]["score"] > audited[(4, 3)]["score"] > audited[(4, 4)]["score"] > audited[(5, 4)]["score"]
    assert _component_amount(audited[(4, 4)], "bonus_solicitud_normal_atractiva") == 6.0
    assert _component_amount(audited[(5, 5)], "bonus_solicitud_normal_atractiva") == 4.0
    assert _component_amount(audited[(5, 5)], "combo_quincenal_carga_fuerte") == 0.0
    assert _component_amount(audited[(6, 6)], "combo_quincenal_carga_fuerte") == -3.0


def test_dormida_quincenal_extremos_pueden_bajar_de_75():
    extreme_results = [
        evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "cocinar", "lavar", "envejeciente"],
                envejeciente_tipo_cuidado="encamado",
                habitaciones="5",
                banos="5",
            )
        ),
        evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "cocinar", "lavar", "envejeciente"],
                envejeciente_tipo_cuidado="encamado",
                adultos="5",
            )
        ),
        evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                funciones=["limpieza", "cocinar", "lavar", "ninos", "envejeciente"],
                envejeciente_tipo_cuidado="encamado",
                ninos="1",
                edades_ninos="2 años",
                ayuda_cuidado_ninos="sin_ayuda",
            )
        ),
        evaluate_solicitud_atractivo(
            _quincenal_mid_salary_payload(
                habitaciones="5",
                banos="5",
                adultos="4",
                funciones=["limpieza", "cocinar", "lavar", "ninos"],
                ninos="1",
                edades_ninos="2 años",
                ayuda_cuidado_ninos="sin_ayuda",
            )
        ),
    ]

    assert all(result["score"] < 78.5 for result in extreme_results)
    assert _component_amount(extreme_results[0], "combo_quincenal_carga_fuerte") == 0.0
    assert _component_amount(extreme_results[1], "combo_quincenal_carga_fuerte") == -3.0
    assert _component_amount(extreme_results[2], "combo_quincenal_carga_fuerte") == 0.0
    assert _component_amount(extreme_results[3], "combo_quincenal_carga_fuerte") == 0.0


def test_dormida_quincenal_dependencia_intensa_no_se_borra_con_sueldo_excelente():
    result = evaluate_solicitud_atractivo(
        _dormida_mode_payload(
            "quincenal",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
            sueldo="50000",
        )
    )

    assert result["score"] <= 73.0
    assert "encamado_dependencia_intensa" in result["componentes"]["critical_combinations"]
    assert result["componentes"]["salary_reference"]["offered"] == 50000


@pytest.mark.parametrize(
    "payload",
    [
        _salary_cap_favorable_payload(tipo_lugar="apto"),
        _salary_cap_favorable_payload(tipo_lugar="casa"),
        _salary_cap_favorable_payload(
            modalidad_trabajo="Con dormida 💤 lunes a viernes",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            horario_tipo="con_dormida",
            dias_trabajo="",
            horario_hora_entrada="",
            horario_hora_salida="",
            dormida_entrada="lunes 8:00 AM",
            dormida_salida="viernes 4:00 PM",
        ),
        _salary_cap_favorable_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
            horario_tipo="con_dormida",
            dias_trabajo="",
            horario_hora_entrada="",
            horario_hora_salida="",
            dormida_entrada="lunes 8:00 AM",
            dormida_salida="sábado 1:00 PM",
        ),
        _salary_cap_favorable_payload(
            modalidad_trabajo="Con dormida 💤 fin de semana",
            horario="Entrada: viernes 5:00 PM / Salida: lunes 8:00 AM",
            horario_tipo="con_dormida",
            dias_trabajo="",
            horario_hora_entrada="",
            horario_hora_salida="",
            dormida_entrada="viernes 5:00 PM",
            dormida_salida="lunes 8:00 AM",
        ),
        _salary_cap_favorable_payload(
            funciones=["ninos", "cocinar", "lavar"],
            ninos="2",
            edades_ninos="2 y 3 años",
            tipo_lugar="casa",
        ),
        _salary_cap_favorable_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        ),
    ],
)
def test_salary_excellence_cap_limita_sueldo_dentro_de_rango_en_escenarios_favorables(payload):
    ref = evaluate_solicitud_atractivo(payload)["componentes"]["salary_reference"]
    result = evaluate_solicitud_atractivo(dict(payload, sueldo=str(ref["reference_max"])))

    assert result["score"] <= 89.0
    assert _cap_meta(result)["salary_excellence_cap_value"] == 89.0


def test_salary_excellence_cap_aplica_en_minimo_medio_maximo_y_tres_por_ciento():
    base = _salary_cap_favorable_payload()
    ref = evaluate_solicitud_atractivo(base)["componentes"]["salary_reference"]
    ref_min = ref["reference_min"]
    ref_max = ref["reference_max"]

    minimo = evaluate_solicitud_atractivo(dict(base, sueldo=str(ref_min)))
    medio = evaluate_solicitud_atractivo(dict(base, sueldo=str(round((ref_min + ref_max) / 2))))
    maximo = evaluate_solicitud_atractivo(dict(base, sueldo=str(ref_max)))
    tres_pct = evaluate_solicitud_atractivo(dict(base, sueldo=str(round(ref_max * 1.03))))

    assert minimo["score"] == medio["score"] == maximo["score"] == 89.0
    assert tres_pct["score"] == 89.5
    assert _cap_meta(tres_pct)["salary_over_max_ratio"] == 0.03


def test_salary_excellence_cap_desbloquea_noventa_desde_cinco_por_ciento():
    base = _salary_cap_favorable_payload()
    ref = evaluate_solicitud_atractivo(base)["componentes"]["salary_reference"]
    ref_max = ref["reference_max"]

    cinco_pct = evaluate_solicitud_atractivo(dict(base, sueldo=str(round(ref_max * 1.05))))
    diez_pct = evaluate_solicitud_atractivo(dict(base, sueldo=str(round(ref_max * 1.10))))

    assert cinco_pct["score"] > 90.0
    assert diez_pct["score"] > cinco_pct["score"]
    assert _cap_meta(cinco_pct)["salary_excellence_cap_value"] is None
    assert _cap_meta(diez_pct)["salary_excellence_cap_value"] is None


def test_salary_excellence_cap_no_aplana_scores_debajo_del_techo():
    result = evaluate_solicitud_atractivo(
        _salary_cap_favorable_payload(
            tipo_lugar="casa",
            habitaciones="4",
            banos="4",
            adultos="4",
            sueldo="19000",
        )
    )

    assert result["score"] < 89.0
    assert abs(result["score"] - _cap_meta(result)["score_before_salary_excellence_cap"]) <= 0.1
    assert _cap_meta(result)["salary_excellence_cap_applied"] is False


def test_salary_excellence_cap_sin_sugerencia_confiable_es_conservador(monkeypatch):
    def fake_salary_suggestion(_payload):
        return {"can_suggest": False, "suggested_min": None, "suggested_max": None}

    monkeypatch.setattr(atractivo_service, "analyze_salary_suggestion", fake_salary_suggestion)
    result = evaluate_solicitud_atractivo(_salary_cap_favorable_payload(sueldo="50000"))

    assert _cap_meta(result)["score_before_salary_excellence_cap"] > 89.0
    assert result["score"] == 89.0
    assert _cap_meta(result)["salary_excellence_cap_value"] == 89.0


def test_apply_salary_excellence_cap_casos_directos():
    assert apply_salary_excellence_cap(91.7, 22000, 20000, 22000)["score"] == 89.0
    assert apply_salary_excellence_cap(92.3, 22660, 20000, 22000)["score"] == 89.5
    assert apply_salary_excellence_cap(92.3, 23760, 20000, 22000)["score"] == 92.3
    assert apply_salary_excellence_cap(83.5, 22000, 20000, 22000)["score"] == 83.5


def _salida_diaria_normal_payload(mode="lv", **overrides):
    variants = {
        "1": ("Salida diaria - 1 día a la semana", "Lunes", "Lunes, de 8:00 AM a 5:00 PM"),
        "2": ("Salida diaria - 2 días a la semana", "Lunes y jueves", "Lunes y jueves, de 8:00 AM a 5:00 PM"),
        "3": ("Salida diaria - 3 días a la semana", "Lunes, miércoles y viernes", "Lunes, miércoles y viernes, de 8:00 AM a 5:00 PM"),
        "4": ("Salida diaria - 4 días a la semana", "Lunes a jueves", "Lunes a jueves, de 8:00 AM a 5:00 PM"),
        "lv": ("Salida diaria - lunes a viernes", "Lunes a viernes", "Lunes a viernes, de 8:00 AM a 5:00 PM"),
        "ls": ("Salida diaria - lunes a sábado", "Lunes a sábado", "Lunes a sábado, de 8:00 AM a 5:00 PM"),
        "weekend": ("Salida diaria - fin de semana", "Sábado y domingo", "Sábado y domingo, de 8:00 AM a 5:00 PM"),
        "other": ("Salida diaria otro", "Martes y jueves", "Martes y jueves, de 8:00 AM a 5:00 PM"),
    }
    modalidad, dias, horario = variants[mode]
    data = {
        "modalidad_trabajo": modalidad,
        "horario": horario,
        "horario_tipo": "salida_diaria",
        "dias_trabajo": dias,
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "3",
        "pisos": "1",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "ayuda_cuidado_ninos": "",
        "funciones": ["limpieza", "cocinar", "lavar"],
        "areas_comunes": ["sala", "comedor", "cocina"],
        "pasaje_mode": "aparte",
        "detalles_servicio": {"pasaje": {"mode": "aparte"}},
        "envejeciente_tipo_cuidado": "",
        "envejeciente_responsabilidades": [],
    }
    data.update(overrides)
    if "sueldo" not in overrides:
        ref = analyze_salary_suggestion(data)
        data["sueldo"] = str(round((ref["suggested_min"] + ref["suggested_max"]) / 2))
    return data


def test_salida_diaria_baseline_normal_3h3b_se_calibra_en_rango_objetivo():
    expected = {
        "1": ("sd_1_dia", "salida_diaria_1_dia", 89.6),
        "2": ("sd_2_dias", "salida_diaria_2_dias", 89.1),
        "3": ("sd_3_dias", "salida_diaria_3_dias", 88.1),
        "4": ("sd_4_dias", "salida_diaria_4_dias", 87.1),
        "lv": ("sd_l_v", "salida_diaria_l_v", 86.6),
        "ls": ("sd_l_s", "salida_diaria_l_s", 85.6),
        "weekend": ("sd_fin_semana", "salida_diaria_fin_semana", 88.1),
        "other": ("sd_2_dias", "salida_diaria_2_dias", 89.1),
    }
    results = {}
    for mode, (schedule_key, mode_key, score) in expected.items():
        payload = _salida_diaria_normal_payload(mode)
        result = evaluate_solicitud_atractivo(payload)
        ctx = atractivo_service.SolicitudAtractivoService._build_context(payload)
        assert classify_schedule(payload) == (schedule_key, "ok")
        assert ctx.mode_key == mode_key
        assert result["score"] == score
        results[mode] = result

    assert results["1"]["score"] >= results["2"]["score"] >= results["3"]["score"] >= results["4"]["score"] >= results["lv"]["score"] >= results["ls"]["score"]
    assert 84.0 <= results["weekend"]["score"] <= 89.0
    assert _component_amount(results["ls"], "modalidad_sd_l_s") == -1.5


def test_sd_1_day_salary_min_mid_max_y_far_above_max_tienen_raw_creciente():
    salaries = [4000, 5000, 6000, 7000, 10000, 15000, 20000]
    audited = {
        salary: evaluate_solicitud_atractivo(_salida_diaria_normal_payload("1", sueldo=str(salary)))
        for salary in salaries
    }

    expected = {
        4000: {"raw": 73.6, "final": 73.6, "salario": -10.0, "cap": None},
        5000: {"raw": 88.6, "final": 88.6, "salario": 5.0, "cap": None},
        6000: {"raw": 89.6, "final": 89.6, "salario": 6.0, "cap": None},
        7000: {"raw": 90.6, "final": 90.6, "salario": 7.0, "cap": None},
        10000: {"raw": 96.89, "final": 96.0, "salario": 13.29, "cap": 96.0},
        15000: {"raw": 99.56, "final": 97.5, "salario": 15.96, "cap": 97.5},
        20000: {"raw": 100.45, "final": 97.5, "salario": 16.85, "cap": 97.5},
    }

    for salary, spec in expected.items():
        result = audited[salary]
        assert result["componentes"]["salary_reference"]["reference_min"] == 5000
        assert result["componentes"]["salary_reference"]["reference_max"] == 7000
        assert result["componentes"]["score_before_salary_excellence_cap"] == spec["raw"]
        assert result["score"] == spec["final"]
        assert _component_amount(result, "salario") == spec["salario"]
        assert result["componentes"]["salary_excellence_cap_value"] == spec["cap"]

    raws = [audited[salary]["componentes"]["score_before_salary_excellence_cap"] for salary in salaries]
    assert raws == sorted(raws)
    assert len(set(raws)) == len(raws)


def test_sd_1_day_same_payload_salary_5000_vs_20000_no_colapsa_en_preview_de_servicio():
    salary_min = evaluate_solicitud_atractivo(_salida_diaria_normal_payload("1", sueldo="5000"))
    salary_far_above = evaluate_solicitud_atractivo(_salida_diaria_normal_payload("1", sueldo="20000"))

    assert salary_min["componentes"]["score_before_salary_excellence_cap"] == 88.6
    assert salary_far_above["componentes"]["score_before_salary_excellence_cap"] == 100.45
    assert salary_min["score"] == 88.6
    assert salary_far_above["score"] == 97.5
    assert salary_far_above["score"] > salary_min["score"]
    assert _component_amount(salary_far_above, "salario") > _component_amount(salary_min, "salario")

    salary_item = next(item for item in salary_far_above["componentes"]["items"] if item["key"] == "salario")
    assert "muy por encima del máximo sugerido para esta frecuencia" in salary_item["label"]


def test_salida_diaria_salario_raw_crece_en_todas_las_frecuencias_validas():
    for mode in ("1", "2", "3", "4", "lv", "ls", "weekend", "other"):
        payload = _salida_diaria_normal_payload(mode, sueldo="")
        ref = analyze_salary_suggestion(payload)
        ref_min = ref["suggested_min"]
        ref_max = ref["suggested_max"]
        salaries = [
            ref_min - 500,
            ref_min,
            round((ref_min + ref_max) / 2),
            ref_max,
            round(ref_max * 1.10),
            round(ref_max * 1.25),
            round(ref_max * 1.50),
        ]
        raws = [
            evaluate_solicitud_atractivo(dict(payload, sueldo=str(salary)))["componentes"][
                "score_before_salary_excellence_cap"
            ]
            for salary in salaries
        ]

        assert raws == sorted(raws), mode
        assert len(set(raws)) == len(raws), mode


def test_salida_diaria_matriz_ninos_y_vivienda_es_monotona_sin_tocar_dormida():
    def result(mode, **overrides):
        return evaluate_solicitud_atractivo(_salida_diaria_normal_payload(mode, **overrides))

    for mode in ("1", "2", "3", "4", "lv", "ls", "weekend", "other"):
        base_sueldo = _salida_diaria_normal_payload(mode)["sueldo"]
        sin_ninos = result(mode)
        mayores = result(
            mode,
            ninos="2",
            edades_ninos="8 y 10 años",
            sueldo=base_sueldo,
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            nota_cliente="solo supervision, van al colegio",
        )
        pequeno_ayuda = result(
            mode,
            ninos="1",
            edades_ninos="2 años",
            ayuda_cuidado_ninos="con_ayuda",
            sueldo=base_sueldo,
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
        )
        pequeno_sin = result(
            mode,
            ninos="1",
            edades_ninos="2 años",
            ayuda_cuidado_ninos="sin_ayuda",
            sueldo=base_sueldo,
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
        )
        dos_ayuda = result(
            mode,
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
            sueldo=base_sueldo,
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
        )
        dos_sin = result(
            mode,
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="sin_ayuda",
            sueldo=base_sueldo,
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
        )
        casa_4_4 = result(mode, habitaciones="4", banos="4")
        casa_5_5 = result(mode, habitaciones="5", banos="5")

        assert sin_ninos["score"] >= mayores["score"] > pequeno_ayuda["score"] > pequeno_sin["score"] > dos_sin["score"]
        assert pequeno_ayuda["score"] > dos_ayuda["score"] > dos_sin["score"]
        assert sin_ninos["score"] > casa_4_4["score"] > casa_5_5["score"]
        assert _component_amount(casa_4_4, "bonus_solicitud_normal_atractiva") > 0
        assert _component_amount(casa_5_5, "bonus_solicitud_normal_atractiva") > 0


def test_salida_diaria_salario_normal_progresa_gradual():
    base = _salida_diaria_normal_payload("lv")
    ref = analyze_salary_suggestion(base)
    ref_min = ref["suggested_min"]
    ref_max = ref["suggested_max"]
    salaries = [
        ref_min - 1000,
        ref_min,
        round((ref_min + ref_max) / 2),
        ref_max,
        round(ref_max * 1.05),
        round(ref_max * 1.10),
        round(ref_max * 1.20),
    ]
    results = [evaluate_solicitud_atractivo(dict(base, sueldo=str(salary))) for salary in salaries]
    scores = [result["score"] for result in results]

    assert scores == sorted(scores)
    assert scores[:4] == [80.0, 85.6, 86.6, 87.6]
    assert round(scores[4] - scores[3], 1) == 1.7
    assert round(scores[5] - scores[4], 1) == 1.6


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (59.9, LABEL_POCO),
        (60.0, LABEL_REGULAR),
        (69.9, LABEL_REGULAR),
        (70.0, LABEL_ATRACTIVA),
        (84.9, LABEL_ATRACTIVA),
        (85.0, LABEL_ATRACTIVA),
        (89.9, LABEL_ATRACTIVA),
        (90.0, LABEL_MUY_ATRACTIVA),
        (95.0, LABEL_MUY_ATRACTIVA),
        (100.0, LABEL_MUY_ATRACTIVA),
    ],
)
def test_score_label_umbrales_visuales_actualizados(score, label):
    assert atractivo_service._score_label(score) == label


def test_salary_excellence_cap_label_y_persistencia_usan_score_final():
    payload = _salary_cap_favorable_payload(sueldo="20000")
    result = evaluate_solicitud_atractivo(payload)
    solicitud = SimpleNamespace(
        modalidad_trabajo=payload["modalidad_trabajo"],
        horario=payload["horario"],
        tipo_lugar=payload["tipo_lugar"],
        habitaciones=payload["habitaciones"],
        banos=payload["banos"],
        adultos=payload["adultos"],
        ninos=payload["ninos"],
        edades_ninos=payload["edades_ninos"],
        sueldo=payload["sueldo"],
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
        funciones=payload["funciones"],
        areas_comunes=payload["areas_comunes"],
        detalles_servicio={
            "horario_tipo": payload["horario_tipo"],
            "dias_trabajo": payload["dias_trabajo"],
            "hora_entrada": payload["horario_hora_entrada"],
            "hora_salida": payload["horario_hora_salida"],
            "pasaje": {"mode": "aparte"},
        },
        atractivo_score=None,
        atractivo_label=None,
        atractivo_motivos=None,
        atractivo_version=None,
        atractivo_calculated_at=None,
    )

    persisted = apply_solicitud_atractivo_to_model(solicitud)

    assert _cap_meta(result)["score_before_salary_excellence_cap"] > result["score"]
    assert result["score"] == 89.0
    assert result["label"] == LABEL_ATRACTIVA
    assert persisted["score"] == result["score"]
    assert solicitud.atractivo_score == 89
    assert solicitud.atractivo_label == result["label"]


def test_salary_excellence_cap_respeta_cap_critico_previo():
    result = evaluate_solicitud_atractivo(
        _salary_cap_favorable_payload(
            tipo_lugar="casa",
            habitaciones="3",
            banos="3",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            ninos="2",
            edades_ninos="8 meses y 2 años",
            sueldo="50000",
        )
    )

    assert "nino_pequeno_limpieza_cocinar_lavar" in result["componentes"]["critical_combinations"]
    assert result["score"] < 89.0
    assert _cap_meta(result)["salary_excellence_cap_value"] is None


def _dormida_lv_apto_ninos_payload(**overrides):
    data = _payload(
        modalidad_trabajo="Con dormida 💤 lunes a viernes",
        horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        horario_tipo="con_dormida",
        dias_trabajo="",
        horario_hora_entrada="",
        horario_hora_salida="",
        dormida_entrada="lunes 8:00 AM",
        dormida_salida="viernes 4:00 PM",
        tipo_lugar="apto",
        habitaciones="2",
        banos="2",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza"],
        areas_comunes=["sala", "comedor", "cocina"],
        sueldo="20000",
        pasaje_mode="incluido",
        detalles_servicio={"pasaje": {"mode": "incluido"}},
    )
    data.update(overrides)
    return data


def test_dormida_lv_apto_dos_pequenos_con_ayuda_queda_en_rango_objetivo():
    result = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )

    assert 87.0 <= result["score"] <= 88.2
    assert result["componentes"]["score_before_salary_excellence_cap"] == 87.82
    assert _component_amount(result, "combo_ninos_pequenos") == -0.7
    assert _component_amount(result, "ayuda_cuidado_ninos") == 0.0
    assert _component_amount(result, "bonus_solicitud_normal_atractiva") == 8.0
    assert result["componentes"]["salary_excellence_cap_applied"] is False


def test_dormida_lv_apto_monotonia_por_responsabilidad_no_la_oculta_el_cap():
    sin_ninos = evaluate_solicitud_atractivo(_dormida_lv_apto_ninos_payload())
    mayores = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="7 y 10 años")
    )
    pequenos_sin_ayuda = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="sin_ayuda",
        )
    )
    pequenos_con_ayuda = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    solo_ninera = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )

    assert _pre_salary_cap_score(sin_ninos) >= _pre_salary_cap_score(mayores)
    assert _pre_salary_cap_score(mayores) > _pre_salary_cap_score(pequenos_con_ayuda)
    assert _pre_salary_cap_score(pequenos_con_ayuda) > _pre_salary_cap_score(pequenos_sin_ayuda)
    assert _pre_salary_cap_score(pequenos_con_ayuda) > _pre_salary_cap_score(solo_ninera)
    assert sin_ninos["score"] >= mayores["score"] > pequenos_con_ayuda["score"] > pequenos_sin_ayuda["score"]
    assert pequenos_con_ayuda["score"] > solo_ninera["score"]


def test_dormida_lv_apto_un_segundo_pequeno_y_bajar_edad_no_aumentan_score():
    un_pequeno = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="1",
            edades_ninos="2 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    dos_pequenos = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    mixto_mayor = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="3 y 10 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )

    assert _pre_salary_cap_score(dos_pequenos) < _pre_salary_cap_score(un_pequeno)
    assert _pre_salary_cap_score(dos_pequenos) < _pre_salary_cap_score(mixto_mayor)
    assert dos_pequenos["score"] < un_pequeno["score"]
    assert dos_pequenos["score"] < mixto_mayor["score"]


def test_dormida_lv_apto_apartamento_no_elimina_diferencia_por_ninos():
    sin_ninos_apto = evaluate_solicitud_atractivo(_dormida_lv_apto_ninos_payload())
    con_ninos_apto = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    sin_ninos_casa = evaluate_solicitud_atractivo(_dormida_lv_apto_ninos_payload(tipo_lugar="casa"))
    con_ninos_casa = evaluate_solicitud_atractivo(
        _dormida_lv_apto_ninos_payload(
            tipo_lugar="casa",
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )

    assert _component_amount(con_ninos_apto, "bonus_apartamento") == 3.0
    assert _pre_salary_cap_score(con_ninos_apto) < _pre_salary_cap_score(sin_ninos_apto)
    assert _pre_salary_cap_score(con_ninos_casa) < _pre_salary_cap_score(sin_ninos_casa)


def test_score_final_coincide_con_suma_matematica_de_componentes():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            funciones=["limpieza", "cocinar", "lavar"],
            sueldo="20000",
            pasaje_mode="aparte",
        )
    )
    base = float(result["componentes"]["base"])
    total_componentes = sum(float(item["amount"]) for item in result["componentes"]["items"])
    expected = base + total_componentes
    assert abs(result["componentes"]["score_before_salary_excellence_cap"] - expected) < 0.001
    assert result["score"] == 89.0
    assert result["componentes"]["salary_excellence_cap_applied"] is True


def test_1_solicitud_estandar_l_v_8h_con_limpieza_es_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
        )
    )
    assert result["label"] == LABEL_ATRACTIVA
    assert result["score"] == 84.3


def test_1b_limpieza_sola_no_resta_ni_muestra_motivo_prohibido():
    result = evaluate_solicitud_atractivo(_payload(funciones=["limpieza"]))
    keys = {item["key"] for item in result["componentes"]["items"]}
    labels = [item["label"] for item in result["motivos"]]
    assert "func_limpieza" not in keys
    assert "Limpieza general baja algo el atractivo." not in labels


def test_1c_cocinar_solo_no_resta_ni_muestra_motivo_prohibido():
    result = evaluate_solicitud_atractivo(_payload(funciones=["cocinar"]))
    keys = {item["key"] for item in result["componentes"]["items"]}
    labels = [item["label"] for item in result["motivos"]]
    assert "func_cocinar" not in keys
    assert "Cocinar baja algo el atractivo." not in labels


def test_1d_lavar_solo_no_resta_ni_muestra_motivo_prohibido():
    result = evaluate_solicitud_atractivo(_payload(funciones=["lavar"]))
    keys = {item["key"] for item in result["componentes"]["items"]}
    labels = [item["label"] for item in result["motivos"]]
    assert "func_lavar" not in keys
    assert "Lavar baja algo el atractivo." not in labels


def test_1e_limpieza_cocinar_lavar_sin_carga_especial_no_resta():
    solo_limpieza = evaluate_solicitud_atractivo(_payload(funciones=["limpieza"]))
    result = evaluate_solicitud_atractivo(_payload(funciones=["limpieza", "cocinar", "lavar"]))
    assert result["score"] >= solo_limpieza["score"]
    assert not any(item["key"] in {"func_limpieza", "func_cocinar", "func_lavar"} for item in result["componentes"]["items"])


def test_2_l_s_8_a_7_limpieza_cocinar_lavar_es_dificil():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Salida diaria - lunes a sábado",
            horario="Lunes a sábado, de 8:00 AM a 7:00 PM",
            horario_hora_salida="7:00 PM",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["label"] in {LABEL_DIFICIL, LABEL_POCO}
    assert result["score"] <= 53


def test_3_con_dormida_l_v_bebe_solamente_es_atractiva():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            horario="Entrada: lunes 8:00 AM / Salida: viernes 5:00 PM",
            dormida_salida="viernes 5:00 PM",
            funciones=["ninos"],
            ninos="1",
            edades_ninos="8 meses",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}
    assert result["score"] >= 73


def test_4_con_dormida_l_s_bebe_mas_hogar_completo_es_dificil():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM",
            dormida_salida="sábado 12:00 PM",
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="1",
            edades_ninos="2 años",
            sueldo="18000",
        )
    )
    assert result["label"] in {LABEL_REGULAR, LABEL_POCO}
    assert 60 <= result["score"] <= 66.5


def test_5_encamado_mas_limpieza_cocinar_lavar_es_dificil():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
        )
    )
    assert result["label"] in {LABEL_DIFICIL, LABEL_POCO}
    assert result["score"] <= 40


def test_6_casa_4_4_mas_4_adultos_mas_limpieza_es_poco_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            habitaciones="4",
            banos="4",
            adultos="4",
            funciones=["limpieza"],
        )
    )
    assert result["label"] == LABEL_REGULAR
    assert 68 <= result["score"] <= 70
    assert any(item["key"] == "combo_hogar_grande_adultos" and item["amount"] == -1 for item in result["componentes"]["items"])


def test_7_con_dormida_salida_quincenal_es_poco_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 quincenal",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 12:00 PM",
            funciones=["limpieza"],
        )
    )
    assert result["label"] == LABEL_POCO
    assert 55 <= result["score"] <= 65


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
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in older["componentes"]["items"])


def test_8b_tres_ninos_7_9_10_anos_son_supervision_ligera():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="7, 9, 10 años",
            adultos="2",
        )
    )
    small_case = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="2, 4, 5 años",
            adultos="2",
        )
    )
    assert result["score"] > small_case["score"]
    assert _component_amount(result, "combo_ninos_pequenos") == 0.0
    assert _component_amount(result, "ocupacion_total") < 0


def test_8c_cuidado_infantil_mixto_usa_pequenos_reales_no_residentes():
    pequeños = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="1, 2 y 3 años",
            adultos="2",
        )
    )
    mixto = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="2, 10, 15 años",
            adultos="2",
        )
    )
    mayores = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="7, 9 y 10 años",
            adultos="2",
        )
    )

    assert mayores["score"] > mixto["score"] > pequeños["score"]
    assert _component_amount(pequeños, "combo_ninos_pequenos") == -6.54
    assert _component_amount(mixto, "combo_ninos_pequenos") == -3.61
    assert _component_amount(mayores, "combo_ninos_pequenos") == 0.0
    assert _component_amount(mixto, "ocupacion_total") == _component_amount(mayores, "ocupacion_total")


def test_8d_ui_cliff_marcar_dos_ninos_pequenos_no_baja_de_77():
    sin_ninos = evaluate_solicitud_atractivo(_ui_cliff_payload())
    con_dos_pequenos = evaluate_solicitud_atractivo(
        _ui_cliff_payload(
            funciones=["limpieza", "ninos"],
            ninos="2",
            edades_ninos="2 y 3 años",
        )
    )

    assert sin_ninos["score"] == 85.6
    assert 76.0 <= con_dos_pequenos["score"] <= 79.0
    assert sin_ninos["score"] - con_dos_pequenos["score"] < 10.0
    assert _component_amount(con_dos_pequenos, "bonus_solicitud_normal_atractiva") > 0.0


def test_8e_ui_cliff_escala_por_edades_y_desconocidos():
    scores = {
        "uno_pequeno": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="1", edades_ninos="2 años")
        ),
        "dos_pequenos": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="2 y 3 años")
        ),
        "mayores": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="7 y 10 años")
        ),
        "mixto": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="2 y 10 años")
        ),
        "desconocidos": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="")
        ),
        "full": evaluate_solicitud_atractivo(
            _ui_cliff_payload(funciones=["limpieza", "cocinar", "lavar", "ninos"], ninos="2", edades_ninos="2 y 3 años")
        ),
    }

    assert 78.5 <= scores["uno_pequeno"]["score"] <= 80.0
    assert 76.0 <= scores["dos_pequenos"]["score"] <= 79.0
    assert 85.0 <= scores["mayores"]["score"] <= 86.0
    assert 78.0 <= scores["mixto"]["score"] <= 82.0
    assert 80.0 <= scores["desconocidos"]["score"] <= 83.0
    assert 70.0 <= scores["full"]["score"] <= 76.0
    assert scores["mayores"]["score"] > scores["mixto"]["score"] > scores["dos_pequenos"]["score"]
    assert scores["dos_pequenos"]["score"] < scores["uno_pequeno"]["score"]
    assert (
        scores["uno_pequeno"]["score"] - scores["dos_pequenos"]["score"]
        < evaluate_solicitud_atractivo(_ui_cliff_payload())["score"] - scores["uno_pequeno"]["score"]
    )


def test_8f_ayuda_cuidado_ninos_sube_sin_superar_sin_ninos_y_legacy_normaliza():
    sin_ninos = evaluate_solicitud_atractivo(_ui_cliff_payload())
    audited = {
        ayuda: evaluate_solicitud_atractivo(
            _ui_cliff_payload(
                funciones=["limpieza", "ninos"],
                ninos="2",
                edades_ninos="2 y 3 años",
                ayuda_cuidado_ninos=ayuda,
            )
        )
        for ayuda in ["sin_ayuda", "con_ayuda", "ayuda_mayor"]
    }

    assert 76.0 <= audited["sin_ayuda"]["score"] <= 78.0
    assert audited["sin_ayuda"]["score"] < audited["con_ayuda"]["score"] < sin_ninos["score"]
    assert audited["ayuda_mayor"]["score"] == audited["con_ayuda"]["score"]
    assert _component_amount(audited["con_ayuda"], "combo_ninos_pequenos") > _component_amount(audited["sin_ayuda"], "combo_ninos_pequenos")


def test_8g_ayuda_no_cambia_ocupacion_y_no_borra_carga_de_bebe():
    sin_ayuda = evaluate_solicitud_atractivo(
        _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="1", edades_ninos="8 meses", ayuda_cuidado_ninos="sin_ayuda")
    )
    con_ayuda = evaluate_solicitud_atractivo(
        _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="1", edades_ninos="8 meses", ayuda_cuidado_ninos="con_ayuda")
    )

    assert con_ayuda["score"] > sin_ayuda["score"]
    assert _component_amount(con_ayuda, "combo_ninos_pequenos") < 0
    assert _component_amount(con_ayuda, "ocupacion_total") == _component_amount(sin_ayuda, "ocupacion_total")


def test_8h_mayores_y_desconocidos_ignoran_ayuda_sin_pequenos_confirmados():
    mayores = evaluate_solicitud_atractivo(
        _ui_cliff_payload(
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            ninos="3",
            edades_ninos="7, 9 y 10 años",
            ayuda_cuidado_ninos="con_ayuda",
        )
    )
    mayores_sin = evaluate_solicitud_atractivo(
        _ui_cliff_payload(
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            ninos="3",
            edades_ninos="7, 9 y 10 años",
            ayuda_cuidado_ninos="sin_ayuda",
        )
    )
    desconocidos = evaluate_solicitud_atractivo(
        _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="", ayuda_cuidado_ninos="con_ayuda")
    )
    desconocidos_sin = evaluate_solicitud_atractivo(
        _ui_cliff_payload(funciones=["limpieza", "ninos"], ninos="2", edades_ninos="", ayuda_cuidado_ninos="sin_ayuda")
    )

    assert _component_amount(mayores, "combo_ninos_pequenos") == 0.0
    assert _component_amount(mayores, "ayuda_cuidado_ninos") == 0.0
    assert mayores["score"] == mayores_sin["score"]
    assert 84.0 <= mayores["score"] <= 85.0
    assert _component_amount(desconocidos, "ayuda_cuidado_ninos") == 0.0
    assert desconocidos["score"] == desconocidos_sin["score"]
    assert 80.0 <= desconocidos["score"] <= 83.0


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
    assert result["score"] >= 78
    penalty = next(
        (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
        0,
    )
    assert penalty == 0


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
    assert result["label"] in {LABEL_DIFICIL, LABEL_POCO, LABEL_REGULAR, LABEL_ATRACTIVA}
    assert result["score"] < 85


def test_12_domestica_balanceada_con_pasaje_aparte_queda_cerca_de_85():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["score"] == 88.5
    assert result["componentes"]["score_before_salary_excellence_cap"] == 88.5
    assert result["componentes"]["salary_excellence_cap_applied"] is False
    assert result["label"] == LABEL_ATRACTIVA
    assert any(item["key"] == "bonus_solicitud_normal_atractiva" and item["amount"] == 10.5 for item in result["componentes"]["items"])
    assert any(item["key"] == "bonus_pasaje" and item["amount"] == 2 for item in result["componentes"]["items"])


def test_13_casa_4_4_con_buen_horario_y_pocos_adultos_no_baja_de_70():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza"],
        )
    )
    assert 85 <= result["score"] <= 86
    assert result["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}
    assert any(item["key"] == "salario" and item["amount"] >= 6 for item in result["componentes"]["items"])
    assert any(item["key"] == "hogar_carga_fisica" and item["amount"] == -1.4 for item in result["componentes"]["items"])


def test_14_subir_sueldo_aumenta_score_de_forma_monotona():
    low = evaluate_solicitud_atractivo(_payload(sueldo="16000"))
    near = evaluate_solicitud_atractivo(_payload(sueldo="20000"))
    medium = evaluate_solicitud_atractivo(_payload(sueldo="22000"))
    high = evaluate_solicitud_atractivo(_payload(sueldo="25000"))
    very_high = evaluate_solicitud_atractivo(_payload(sueldo="30000"))

    assert low["score"] < near["score"] < medium["score"] < high["score"] < very_high["score"]


def test_15_sueldo_125_por_ciento_o_mas_aporta_mejora_visible():
    result = evaluate_solicitud_atractivo(_payload(sueldo="25000"))
    salario_item = next(item for item in result["componentes"]["items"] if item["key"] == "salario")
    assert salario_item["amount"] >= 17


def test_16_solicitud_normal_con_sueldo_alto_puede_llegar_a_muy_atractiva():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            habitaciones="2",
            banos="2",
            adultos="2",
            sueldo="30000",
            pasaje_mode="aparte",
            funciones=["limpieza"],
        )
    )
    assert result["score"] >= 85
    assert result["label"] == LABEL_MUY_ATRACTIVA


def test_17_solicitud_critica_sigue_limitada_aunque_el_sueldo_sea_alto():
    result = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida 💤 quincenal",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 12:00 PM",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
            sueldo="50000",
        )
    )
    assert result["label"] in {LABEL_DIFICIL, LABEL_POCO, LABEL_REGULAR}
    assert result["label"] != LABEL_MUY_ATRACTIVA
    assert result["score"] < 85


def test_18_salario_puede_cambiar_categoria_en_solicitud_moderada():
    regular = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Salida diaria - lunes a sábado",
            horario="Lunes a sábado, de 8:00 AM a 5:00 PM",
            horario_hora_salida="5:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            sueldo="18000",
            funciones=["limpieza"],
        )
    )
    attractive = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Salida diaria - lunes a sábado",
            horario="Lunes a sábado, de 8:00 AM a 5:00 PM",
            horario_hora_salida="5:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            sueldo="25000",
            funciones=["limpieza"],
        )
    )
    assert regular["label"] in {LABEL_ATRACTIVA, LABEL_REGULAR}
    assert attractive["score"] > regular["score"]
    assert attractive["label"] in {LABEL_REGULAR, LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}


def test_19_apartamento_pequeno_puntua_mas_que_casa_equivalente():
    casa = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza"],
        )
    )
    apto = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza"],
        )
    )
    assert _pre_salary_cap_score(apto) == _pre_salary_cap_score(casa) + 3
    assert apto["score"] == 87.5
    assert any(item["key"] == "bonus_apartamento" and item["amount"] == 3 for item in apto["componentes"]["items"])


def test_19b_caso_objetivo_normal_sin_pasaje_queda_en_87():
    result = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="incluido",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["score"] == 86.5
    assert result["label"] == LABEL_ATRACTIVA


def test_19c_ninos_mayores_sin_cuidado_bajan_solo_por_ocupacion_del_hogar():
    base_case = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    with_older_children = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="2",
            edades_ninos="7 y 10 años",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert _pre_salary_cap_score(with_older_children) == _pre_salary_cap_score(base_case) - 0.5
    assert with_older_children["score"] == 88.0
    assert base_case["score"] == 88.5
    assert _component_amount(with_older_children, "ocupacion_total") == -0.5
    assert _component_amount(with_older_children, "combo_ninos_pequenos") == 0.0


def test_19d_apartamento_equivalente_queda_entre_88_y_90():
    result = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["score"] == 89.0
    assert result["componentes"]["score_before_salary_excellence_cap"] == 91.5


def test_19e_sueldo_por_encima_del_maximo_supera_90_en_caso_objetivo():
    result = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="22000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert result["score"] == 91.8


def test_19f_caso_objetivo_sin_pasaje_baja_exactamente_dos_puntos():
    with_pasaje = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    without_pasaje = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="incluido",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    assert _pre_salary_cap_score(with_pasaje) == _pre_salary_cap_score(without_pasaje) + 2
    assert with_pasaje["score"] == 88.5
    assert without_pasaje["score"] == 86.5


def test_19g_caso_objetivo_con_nino_pequeno_y_limpieza_baja_claramente():
    base_case = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    with_small_child_and_cleaning = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="1",
            edades_ninos="2 años",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["ninos", "limpieza"],
        )
    )
    assert _pre_salary_cap_score(with_small_child_and_cleaning) < _pre_salary_cap_score(base_case) - 5
    assert with_small_child_and_cleaning["score"] < base_case["score"]


def test_19h_caso_objetivo_con_encamado_baja_claramente():
    base_case = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    with_encamado = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene"],
        )
    )
    assert with_encamado["score"] < base_case["score"] - 25


def test_20_apartamento_3_2_tambien_recibe_bonificacion_completa():
    result = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            habitaciones="3",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza"],
        )
    )
    bonus = next(item for item in result["componentes"]["items"] if item["key"] == "bonus_apartamento")
    assert bonus["amount"] == 3


def test_20b_planchar_mantiene_penalizacion_pequena():
    result = evaluate_solicitud_atractivo(_payload(funciones=["planchar"]))
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "func_planchar")
    assert penalty["amount"] == -2


def test_20c_nino_pequeno_mas_limpieza_cocinar_lavar_resta_mas():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="1",
            edades_ninos="2 años",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -3.5


def test_20d_dos_ninos_pequenos_aplican_extra_maximo():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="2",
            edades_ninos="1 y 4 años",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -4.85


def test_20d2_tres_ninos_pequenos_topan_extra_en_menos_dos():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="3",
            edades_ninos="1, 3, 4",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -5.86


def test_20h_cuidar_ninos_con_edad_12_no_penaliza_ni_bonifica():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="12 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] == 86
    assert not any(item["key"] == "combo_ninos_pequenos" for item in result["componentes"]["items"])
    assert not any(item["key"] == "bonus_cuidado_ninos" for item in result["componentes"]["items"])
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20i_cuidar_ninos_con_edad_9_no_penaliza():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="9 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] == 86
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20j_cuidar_ninos_con_edad_6_no_penaliza():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="6 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] == 86
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20k_cuidar_ninos_con_edad_5_activa_logica_de_nino_pequeno():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="5 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert not any(item["key"] == "combo_ninos_pequenos" and item["amount"] < 0 for item in result["componentes"]["items"])


def test_20l_nota_independientes_y_estudian_elimina_penalizacion():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="",
            nota_cliente="Son independientes y estudian.",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] == 84
    assert not any(item["key"] == "combo_ninos_pequenos" for item in result["componentes"]["items"])
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20m_nota_solo_supervision_elimina_penalizacion():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="",
            nota_cliente="Solo supervisión.",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert result["score"] == 84
    assert not any(item["key"] == "combo_ninos_pequenos" for item in result["componentes"]["items"])
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20n_bebe_de_8_meses_en_nota_activa_penalizacion_aun_sin_edades():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="",
            nota_cliente="Bebé de 8 meses.",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert not any(item["key"] == "combo_ninos_pequenos" and item["amount"] < 0 for item in result["componentes"]["items"])


def test_20o_edad_estructurada_tiene_prioridad_sobre_nota_solo_supervision():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="2 años",
            nota_cliente="Solo supervisión.",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    assert not any(item["key"] == "combo_ninos_pequenos" and item["amount"] < 0 for item in result["componentes"]["items"])


def test_20p_solo_cuidado_gradiente_por_edad_y_meses():
    cases = [
        ("0 meses", 0),
        ("8 meses", 0),
        ("1 año", 0),
        ("2 años", 0),
        ("3 años", 0),
        ("4 años", 0),
        ("5 años", 0),
        ("6 años", 0),
        ("12 años", 0),
    ]
    observed = {}
    for age_text, expected_penalty in cases:
        result = evaluate_solicitud_atractivo(
            _payload(
                funciones=["ninos"],
                ninos="1",
                edades_ninos=age_text,
                tipo_lugar="",
                habitaciones="",
                banos="",
                adultos="0",
            )
        )
        penalty = next(
            (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
            0,
        )
        observed[age_text] = penalty
        assert penalty == expected_penalty

    assert len(set(observed.values())) == 1


def test_20q_ninera_pura_bebe_de_8_meses_no_penaliza():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="8 meses",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    penalty = next(
        (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
        0,
    )
    assert penalty == 0
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20r_ninera_pura_dos_anos_no_penaliza():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="2 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    penalty = next(
        (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
        0,
    )
    assert penalty == 0


def test_20s_ninera_pura_cuatro_anos_no_penaliza():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="4 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
        )
    )
    penalty = next(
        (item["amount"] for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos"),
        0,
    )
    assert penalty == 0


def test_20t_ninos_mas_limpieza_si_aplica_penalizacion_por_edad():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza"],
            ninos="1",
            edades_ninos="2 años",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -1.2


def test_20u_bebe_mas_limpieza_si_aplica_penalizacion():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza"],
            ninos="1",
            edades_ninos="8 meses",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -1.5


def test_20v_bebe_mas_limpieza_y_cocinar_aplica_combo():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos", "limpieza", "cocinar"],
            ninos="1",
            edades_ninos="8 meses",
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_ninos_pequenos")
    assert penalty["amount"] == -3.25


def test_20w_ninera_pura_l_v_con_pasaje_y_sueldo_en_rango_queda_en_rango_objetivo():
    expected_scores = {
        "8 meses": 87,
        "2 años": 88,
        "4 años": 89,
        "5 años": 89,
        "6 años": 88,
    }
    for age_text, expected_score in expected_scores.items():
        result = evaluate_solicitud_atractivo(
            _payload(
                funciones=["ninos"],
                ninos="1",
                edades_ninos=age_text,
                tipo_lugar="",
                habitaciones="",
                banos="",
                adultos="0",
                pasaje_mode="aparte",
            )
        )
        assert result["score"] == expected_score
        if age_text == "5 años":
            assert result["componentes"]["score_before_salary_excellence_cap"] == 90.0
        assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20w2_bonus_ninera_pura_varia_leve_segun_edad():
    scores = {}
    for age_text in ("8 meses", "2 años", "4 años", "5 años", "6 años"):
        result = evaluate_solicitud_atractivo(
            _payload(
                funciones=["ninos"],
                ninos="1",
                edades_ninos=age_text,
                tipo_lugar="",
                habitaciones="",
                banos="",
                adultos="0",
                pasaje_mode="aparte",
            )
        )
        scores[age_text] = result["score"]

    assert scores["8 meses"] == 87
    assert scores["2 años"] == 88
    assert scores["4 años"] == 89
    assert scores["5 años"] == 89
    assert scores["6 años"] == 88


def test_20x_ninera_pura_con_sueldo_superior_puede_superar_90():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["ninos"],
            ninos="1",
            edades_ninos="2 años",
            tipo_lugar="",
            habitaciones="",
            banos="",
            adultos="0",
            pasaje_mode="aparte",
            sueldo="25000",
        )
    )
    assert result["score"] >= 88
    assert any(item["key"] == "bonus_ninera_pura_atractiva" for item in result["componentes"]["items"])


def test_20e_encamado_con_tareas_hogar_conserva_penalizacion_combinada():
    result = evaluate_solicitud_atractivo(
        _payload(
            funciones=["envejeciente", "limpieza", "cocinar", "lavar", "planchar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "movilidad"],
        )
    )
    penalty = next(item for item in result["componentes"]["items"] if item["key"] == "combo_envejeciente")
    assert penalty["amount"] == -21


def test_20f_no_hay_doble_penalizacion_por_funciones_normales_en_casa_grande():
    result = evaluate_solicitud_atractivo(
        _payload(
            habitaciones="4",
            banos="4",
            adultos="2",
            funciones=["limpieza", "cocinar", "lavar"],
        )
    )
    keys = {item["key"] for item in result["componentes"]["items"]}
    assert "combo_hogar_grande_limpieza" not in keys
    assert "hogar_carga_fisica" in keys
    assert "hogar_4_4" not in keys
    assert "func_limpieza" not in keys
    assert "func_cocinar" not in keys
    assert "func_lavar" not in keys


def test_20f2_casa_grande_no_critica_con_sueldo_fuerte_queda_cerca_de_89():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
            habitaciones="4",
            banos="4",
            pisos="1",
            adultos="2",
            funciones=["limpieza", "cocinar", "lavar"],
            areas_comunes=[
                "sala",
                "comedor",
                "cocina",
                "salon_juegos",
                "terraza",
                "jardin",
                "estudio",
                "patio",
                "piscina",
                "marquesina",
            ],
            sueldo="25000",
            pasaje_mode="aparte",
        )
    )
    assert 84.5 <= result["score"] <= 85.0
    salario_item = next(item for item in result["componentes"]["items"] if item["key"] == "salario")
    assert salario_item["amount"] >= 14


def test_20f3_casa_grande_no_critica_subir_de_23000_a_25000_mejora_visiblemente():
    base_payload = _payload(
        horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
        habitaciones="4",
        banos="4",
        pisos="1",
        adultos="2",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=[
            "sala",
            "comedor",
            "cocina",
            "salon_juegos",
            "terraza",
            "jardin",
            "estudio",
            "patio",
            "piscina",
            "marquesina",
        ],
        pasaje_mode="aparte",
    )
    at_23 = evaluate_solicitud_atractivo(dict(base_payload, sueldo="23000"))
    at_25 = evaluate_solicitud_atractivo(dict(base_payload, sueldo="25000"))
    assert 80.0 <= at_23["score"] <= 80.3
    assert 84.5 <= at_25["score"] <= 85.0
    assert at_25["score"] > at_23["score"]


def test_20f4_casa_grande_sola_no_activa_cap_critico():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            funciones=["limpieza", "cocinar", "lavar"],
            sueldo="27000",
            pasaje_mode="aparte",
        )
    )
    assert result["componentes"]["critical_combinations"] == []
    assert 98.0 <= result["score"] <= 98.5


def test_20f5_casa_grande_con_nino_pequeno_si_baja_claramente():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            ninos="1",
            edades_ninos="2 años",
            sueldo="25000",
            pasaje_mode="aparte",
        )
    )
    assert result["score"] < 89.0


def test_20f6_casa_grande_con_encamado_baja_mucho_mas():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
            habitaciones="4",
            banos="4",
            adultos="2",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
            sueldo="25000",
            pasaje_mode="aparte",
        )
    )
    assert result["score"] < 75.0


def test_20g_motivos_visibles_no_critican_funciones_normales():
    result = evaluate_solicitud_atractivo(
        _payload(
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza", "cocinar", "lavar"],
            sueldo="20000",
        )
    )
    labels = [item["label"] for item in result["motivos"]]
    forbidden = {
        "Limpieza general baja algo el atractivo.",
        "Cocinar baja algo el atractivo.",
        "Lavar baja algo el atractivo.",
    }
    assert forbidden.isdisjoint(labels)


def test_21_apartamento_4_4_no_recibe_bonificacion_completa():
    casa = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="4",
            banos="4",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza"],
        )
    )
    apto = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            habitaciones="4",
            banos="4",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza"],
        )
    )
    bonus = next((item["amount"] for item in apto["componentes"]["items"] if item["key"] == "bonus_apartamento"), 0)
    assert bonus <= 1
    assert 0 <= (apto["score"] - casa["score"]) <= 3


def test_22_diferencia_por_tipo_de_lugar_no_supera_tres_puntos():
    casa = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            habitaciones="3",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza", "cocinar"],
        )
    )
    apto = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            habitaciones="3",
            banos="2",
            adultos="2",
            horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
            horario_hora_salida="4:00 PM",
            funciones=["limpieza", "cocinar"],
        )
    )
    assert 0 < (apto["score"] - casa["score"]) <= 3


def test_23_tipo_lugar_no_anula_penalizaciones_criticas():
    casa = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="casa",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
            habitaciones="2",
            banos="2",
            adultos="2",
            sueldo="20000",
        )
    )
    apto = evaluate_solicitud_atractivo(
        _payload(
            tipo_lugar="apto",
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "pampers", "movilidad"],
            habitaciones="2",
            banos="2",
            adultos="2",
            sueldo="20000",
        )
    )
    assert apto["score"] - casa["score"] <= 3
    assert apto["label"] in {LABEL_DIFICIL, LABEL_POCO, LABEL_REGULAR}
    assert apto["label"] != LABEL_MUY_ATRACTIVA


def test_24_dormida_l_v_8am_a_4pm_no_calcula_jornada_larga_y_es_atractiva():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
        )
    )
    keys = {item["key"] for item in result["componentes"]["items"]}
    assert result["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}
    assert "horario_10h" not in keys
    assert "horario_11h" not in keys
    assert "horario_12h" not in keys
    assert "horario_12h_plus" not in keys
    assert "jornada_mayor_12h" not in result["componentes"]["critical_combinations"]
    assert not any(item["key"].startswith("dormida_lv_entrada") for item in result["componentes"]["items"])
    assert any(item["key"] == "dormida_lv_salida_favorable" and item["amount"] == 4 for item in result["componentes"]["items"])
    assert result["score"] == 89


def test_24b_dormida_l_v_entrada_lunes_escala_correctamente_hasta_tope():
    at_6 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 6:00 AM",
            horario="Entrada: lunes 6:00 AM / Salida: viernes 4:00 PM",
        )
    )
    at_8 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 8:00 AM",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        )
    )
    at_9 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 9:00 AM",
            horario="Entrada: lunes 9:00 AM / Salida: viernes 4:00 PM",
        )
    )
    at_10 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 10:00 AM",
            horario="Entrada: lunes 10:00 AM / Salida: viernes 4:00 PM",
        )
    )
    at_11 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 11:00 AM",
            horario="Entrada: lunes 11:00 AM / Salida: viernes 4:00 PM",
        )
    )
    at_2pm = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 2:00 PM",
            horario="Entrada: lunes 2:00 PM / Salida: viernes 4:00 PM",
        )
    )
    at_7 = evaluate_solicitud_atractivo(
        _dormida_payload(
            sueldo="20000",
            pasaje_mode="aparte",
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            dormida_entrada="lunes 7:00 AM",
            horario="Entrada: lunes 7:00 AM / Salida: viernes 4:00 PM",
        )
    )
    assert _pre_salary_cap_score(at_6) < _pre_salary_cap_score(at_7) < _pre_salary_cap_score(at_8) < _pre_salary_cap_score(at_9)
    assert _pre_salary_cap_score(at_9) == _pre_salary_cap_score(at_10) == _pre_salary_cap_score(at_11) == _pre_salary_cap_score(at_2pm)
    assert any(item["key"] == "dormida_lv_entrada_muy_temprano" and item["amount"] == -4 for item in at_6["componentes"]["items"])
    assert any(item["key"] == "dormida_lv_entrada_temprano" and item["amount"] == -2 for item in at_7["componentes"]["items"])
    assert any(item["key"] == "dormida_lv_entrada_favorable_9" and item["amount"] == 1 for item in at_9["componentes"]["items"])
    assert any(item["key"] == "dormida_lv_entrada_favorable_10" and item["amount"] == 1 for item in at_10["componentes"]["items"])
    assert any(item["amount"] == 1 for item in at_11["componentes"]["items"] if str(item["key"]).startswith("dormida_lv_entrada"))
    assert at_6["score"] == 86.3
    assert at_7["score"] == 88.3
    assert at_8["score"] == 89
    assert at_9["score"] == at_10["score"] == at_11["score"] == at_2pm["score"] == 89.0
    assert _pre_salary_cap_score(at_10) == 91.25
    assert _pre_salary_cap_score(at_11) == 91.25
    assert _pre_salary_cap_score(at_2pm) == 91.25


def test_25_dormida_l_v_salida_2pm_es_mas_atractiva_que_4pm():
    normal = evaluate_solicitud_atractivo(_dormida_payload(dormida_salida="viernes 4:00 PM", horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM"))
    early = evaluate_solicitud_atractivo(_dormida_payload(dormida_salida="viernes 2:00 PM", horario="Entrada: lunes 8:00 AM / Salida: viernes 2:00 PM"))
    assert early["score"] > normal["score"]
    assert early["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}


def test_26_dormida_l_v_salida_7pm_recibe_penalizacion_moderada():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            dormida_entrada="lunes 9:00 AM",
            dormida_salida="viernes 7:00 PM",
            horario="Entrada: lunes 9:00 AM / Salida: viernes 7:00 PM",
        )
    )
    assert any(item["key"] == "dormida_lv_salida_tarde" and item["amount"] == -4 for item in result["componentes"]["items"])
    assert result["label"] in {LABEL_REGULAR, LABEL_ATRACTIVA}


def test_26b_dormida_l_s_entrada_lunes_sigue_la_misma_escala():
    at_7 = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 7:00 AM / Salida: sábado 12:00 PM",
            dormida_entrada="lunes 7:00 AM",
            dormida_salida="sábado 12:00 PM",
        )
    )
    at_8 = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM",
            dormida_entrada="lunes 8:00 AM",
            dormida_salida="sábado 12:00 PM",
        )
    )
    at_10 = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 10:00 AM / Salida: sábado 12:00 PM",
            dormida_entrada="lunes 10:00 AM",
            dormida_salida="sábado 12:00 PM",
        )
    )
    assert at_7["score"] < at_8["score"] < at_10["score"]
    assert any(item["key"] == "dormida_ls_entrada_temprano" and item["amount"] == -2 for item in at_7["componentes"]["items"])
    assert any(item["key"] == "dormida_ls_entrada_favorable_10" and item["amount"] == 2 for item in at_10["componentes"]["items"])


def test_27_dormida_l_s_sabado_12pm_es_razonable_y_atractiva():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM",
            dormida_salida="sábado 12:00 PM",
        )
    )
    assert any(item["key"] == "modalidad_cd_l_s" and item["amount"] == -2.5 for item in result["componentes"]["items"])
    assert any(item["key"] == "dormida_ls_salida_temprana" and item["amount"] == 4 for item in result["componentes"]["items"])
    assert result["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}


def test_28_dormida_l_s_sabado_3pm_es_menos_atractiva_que_12pm():
    noon = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM",
            dormida_salida="sábado 12:00 PM",
        )
    )
    late = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 lunes a sábado",
            horario="Entrada: lunes 8:00 AM / Salida: sábado 3:00 PM",
            dormida_salida="sábado 3:00 PM",
        )
    )
    assert late["score"] < noon["score"]
    assert any(item["key"] == "dormida_ls_salida_muy_tarde" and item["amount"] == -6 for item in late["componentes"]["items"])


def test_29_dormida_quincenal_no_se_vuelve_dificil_por_defecto():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 quincenal",
            horario="Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
            dormida_salida="segundo viernes 12:00 PM",
            sueldo="28000",
        )
    )
    assert any(item["key"] == "modalidad_cd_quincenal" and item["amount"] == -1.5 for item in result["componentes"]["items"])
    assert 85.0 <= result["score"] <= 86.0
    assert result["label"] == LABEL_ATRACTIVA


def test_30_dormida_quincenal_con_sueldo_alto_recupera_sin_superar_l_v_equivalente():
    quincenal = evaluate_solicitud_atractivo(
        _dormida_payload(
            modalidad_trabajo="Con dormida 💤 quincenal",
            horario="Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
            dormida_salida="segundo viernes 12:00 PM",
            sueldo="40000",
        )
    )
    semanal = evaluate_solicitud_atractivo(_dormida_payload(sueldo="40000"))
    assert _pre_quincenal_band_cap_score(quincenal) < _pre_quincenal_band_cap_score(semanal)
    assert quincenal["label"] in {LABEL_ATRACTIVA, LABEL_MUY_ATRACTIVA}


def test_31_dormida_l_v_domestica_exacta_queda_minimo_82_sin_penalizaciones_residuales():
    result = evaluate_solicitud_atractivo(
        _dormida_payload(
            funciones=["limpieza", "cocinar", "lavar"],
            tipo_lugar="casa",
            habitaciones="2",
            banos="2",
            adultos="2",
            ninos="0",
            edades_ninos="",
            envejeciente_tipo_cuidado="",
            envejeciente_responsabilidades=[],
            sueldo="20000",
            pasaje_mode="aparte",
        )
    )
    keys = {item["key"] for item in result["componentes"]["items"]}
    total_componentes = sum(float(item["amount"]) for item in result["componentes"]["items"])
    assert result["score"] >= 82
    assert result["componentes"]["score_before_salary_excellence_cap"] == float(result["componentes"]["base"]) + total_componentes
    assert result["score"] == 89.0
    assert result["componentes"]["salary_excellence_cap_applied"] is True
    assert "bonus_pasaje" in keys
    assert "func_limpieza" not in keys
    assert "func_cocinar" not in keys
    assert "func_lavar" not in keys
    assert "horario_10h" not in keys
    assert "horario_11h" not in keys
    assert "horario_12h" not in keys
    assert "horario_12h_plus" not in keys
    assert "combo_hogar_grande_limpieza" not in keys


def test_32_pasaje_aparte_suma_dos_puntos_en_dormida_l_v_domestica():
    base_case = _dormida_payload(
        funciones=["limpieza", "cocinar", "lavar"],
        tipo_lugar="casa",
        habitaciones="2",
        banos="2",
        adultos="2",
        ninos="0",
        edades_ninos="",
        sueldo="20000",
    )
    without_pasaje = evaluate_solicitud_atractivo(dict(base_case, pasaje_mode="incluido"))
    with_pasaje = evaluate_solicitud_atractivo(dict(base_case, pasaje_mode="aparte"))
    assert _pre_salary_cap_score(with_pasaje) == _pre_salary_cap_score(without_pasaje) + 2
    assert with_pasaje["componentes"]["salary_excellence_cap_applied"] is True


def test_33_fin_de_semana_tiene_escala_independiente_de_modalidad():
    result = evaluate_solicitud_atractivo(_weekend_payload())
    keys = {item["key"] for item in result["componentes"]["items"]}
    assert "modalidad_sd_fin_semana" in keys
    assert "modalidad_fin_semana" not in keys
    assert _component_amount(result, "modalidad_sd_fin_semana") == 1.0
    assert 84.0 <= result["score"] <= 89.0


def test_34_fin_de_semana_salida_diaria_calcula_duracion_y_penaliza_jornada_larga():
    result = evaluate_solicitud_atractivo(
        _weekend_payload(
            horario="Sábado y domingo, de 5:00 PM a 8:00 AM",
            horario_hora_entrada="5:00 PM",
            horario_hora_salida="8:00 AM",
        )
    )
    keys = {item["key"] for item in result["componentes"]["items"]}
    assert "horario_12h_plus" in keys
    assert "salida_tarde_6" in keys
    assert "salida_tarde_7" in keys
    assert "jornada_mayor_12h" in result["componentes"]["critical_combinations"]


def test_35_fin_de_semana_entrada_moderada_no_suma_bonos_excesivos():
    at_8 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="8:00 AM", sueldo=""))
    at_9 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="9:00 AM", sueldo=""))
    at_10 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="10:00 AM", sueldo=""))
    at_12 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="12:00 PM", sueldo=""))
    at_2 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="2:00 PM", sueldo=""))
    at_5 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="5:00 PM", sueldo=""))
    at_7 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_entrada="7:00 PM", sueldo=""))

    assert at_8["score"] == at_9["score"] == at_10["score"] == at_12["score"] == at_2["score"]
    assert at_2["score"] > at_5["score"]
    assert at_5["score"] == at_7["score"]
    assert _component_amount(at_8, "modalidad_sd_fin_semana") == 1.0
    assert _component_amount(at_5, "horario_12h_plus") == -10.0


def test_36_fin_de_semana_salida_mas_temprana_es_mejor_y_luego_empeora():
    at_8 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="8:00 AM", sueldo=""))
    at_9 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="9:00 AM", sueldo=""))
    at_10 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="10:00 AM", sueldo=""))
    at_11 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="11:00 AM", sueldo=""))
    at_12 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="12:00 PM", sueldo=""))
    at_1 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="1:00 PM", sueldo=""))
    at_2 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="2:00 PM", sueldo=""))
    at_3 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="3:00 PM", sueldo=""))
    at_4 = evaluate_solicitud_atractivo(_weekend_payload(horario_hora_salida="4:00 PM", sueldo=""))

    assert at_8["score"] < at_9["score"]
    assert at_9["score"] == at_10["score"] == at_11["score"] == at_12["score"] == at_1["score"] == at_2["score"] == at_3["score"] == at_4["score"]
    assert _component_amount(at_8, "horario_12h_plus") == -10.0
    assert not any(str(item["key"]).startswith("fin_semana_salida_") for item in at_9["componentes"]["items"])


def test_37_fin_de_semana_salida_diaria_no_usa_logica_de_dormida():
    salida_diaria = evaluate_solicitud_atractivo(
        _weekend_payload(
            horario="Sábado y domingo, de 2:00 PM a 9:00 AM",
            horario_hora_entrada="2:00 PM",
            horario_hora_salida="9:00 AM",
        )
    )
    dormida = evaluate_solicitud_atractivo(
        _payload(
            modalidad_trabajo="Con dormida fin de semana",
            horario="Entrada: sábado 2:00 PM / Salida: domingo 9:00 AM",
            horario_tipo="con_dormida",
            dias_trabajo="",
            horario_hora_entrada="",
            horario_hora_salida="",
            dormida_entrada="sábado 2:00 PM",
            dormida_salida="domingo 9:00 AM",
            sueldo="18000",
            funciones=["limpieza"],
        )
    )
    salida_keys = {item["key"] for item in salida_diaria["componentes"]["items"]}
    dormida_keys = {item["key"] for item in dormida["componentes"]["items"]}
    assert "modalidad_sd_fin_semana" in salida_keys
    assert "modalidad_fin_semana" in dormida_keys
    assert "modalidad_fin_semana" not in salida_keys
    assert "modalidad_sd_fin_semana" not in dormida_keys
    assert dormida["componentes"]["score_before_salary_excellence_cap"] <= 89.0
    assert salida_diaria["score"] != dormida["score"]


def test_38_mejor_horario_fin_de_semana_normal_no_supera_92():
    result = evaluate_solicitud_atractivo(
        _weekend_payload(
            horario="Sábado y domingo, de 5:00 PM a 8:00 AM",
            horario_hora_entrada="5:00 PM",
            horario_hora_salida="8:00 AM",
            sueldo="18000",
        )
    )
    assert result["score"] == 67.9
    assert _component_amount(result, "salario") == 11.12
    assert "jornada_mayor_12h" in result["componentes"]["critical_combinations"]


def test_39_salario_sube_de_forma_continua_en_pasos_de_500():
    scores = []
    bonuses = []
    for sueldo in ("20000", "20500", "21000", "21500", "22000", "22500", "23000"):
        result = evaluate_solicitud_atractivo(_payload(sueldo=sueldo))
        salary_item = next(item for item in result["componentes"]["items"] if item["key"] == "salario")
        scores.append(result["score"])
        bonuses.append(float(salary_item["amount"]))

    assert bonuses == sorted(bonuses)
    assert len(set(bonuses)) == len(bonuses)
    assert scores == sorted(scores)


def _case_5_5_large_home(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
        horario_hora_entrada="8:00 AM",
        horario_hora_salida="5:00 PM",
        tipo_lugar="casa",
        habitaciones="5",
        banos="5",
        pisos="1",
        adultos="4",
        ninos="2",
        edades_ninos="",
        observaciones="Los niños solo viven en la casa y no requieren cuidado.",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina", "salon_juegos", "terraza", "jardin", "estudio", "patio", "piscina", "marquesina"],
        pasaje_mode="aparte",
        sueldo="24000",
    )
    data.update(overrides)
    return data


def _case_5_5_low_occupancy(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 4:00 PM",
        horario_hora_entrada="8:00 AM",
        horario_hora_salida="4:00 PM",
        tipo_lugar="casa",
        habitaciones="5",
        banos="5",
        pisos="1",
        adultos="2",
        ninos="2",
        edades_ninos="",
        observaciones="Los niños solo viven en la casa y no requieren cuidado.",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina", "salon_juegos", "terraza", "jardin", "estudio", "patio", "piscina", "marquesina"],
        pasaje_mode="aparte",
        sueldo="21000",
    )
    data.update(overrides)
    return data


def test_40_cuatro_adultos_solos_penalizan_poco():
    result = evaluate_solicitud_atractivo(_payload(adultos="4", sueldo=""))
    adultos = [item for item in result["componentes"]["items"] if item["bucket"] == "adultos"]
    assert adultos == [
        {
            "key": "adultos_4",
            "amount": -1.0,
            "label": "Cuatro adultos en el hogar bajan un poco el atractivo.",
            "kind": "penalty",
            "bucket": "adultos",
        }
    ]


def test_41_adultos_5_y_6_penalizan_gradualmente():
    score_4 = evaluate_solicitud_atractivo(_payload(adultos="4", sueldo=""))
    score_5 = evaluate_solicitud_atractivo(_payload(adultos="5", sueldo=""))
    score_6 = evaluate_solicitud_atractivo(_payload(adultos="6", sueldo=""))

    item_5 = next(item for item in score_5["componentes"]["items"] if item["bucket"] == "adultos")
    item_6 = next(item for item in score_6["componentes"]["items"] if item["bucket"] == "adultos")

    assert item_5["key"] == "adultos_5"
    assert item_5["amount"] == -2
    assert item_6["key"] == "adultos_6"
    assert item_6["amount"] == -3
    assert score_4["score"] > score_5["score"] > score_6["score"]


def test_42_auditoria_caso_5_5_4_adultos_explica_score_actualizado():
    result = evaluate_solicitud_atractivo(_case_5_5_large_home())
    items = result["componentes"]["items"]

    assert result["componentes"]["score_sin_salario"] == 63.8
    assert result["score"] == 75.5
    assert [item["key"] for item in items] == [
        "adultos_4",
        "ocupacion_total",
        "hogar_carga_fisica",
        "hogar_areas_comunes",
        "combo_hogar_grande_adultos",
        "bonus_pasaje",
        "salario",
    ]
    assert next(item for item in items if item["key"] == "adultos_4")["amount"] == -1
    assert next(item for item in items if item["key"] == "ocupacion_total")["amount"] == -1.5
    assert next(item for item in items if item["key"] == "hogar_carga_fisica")["amount"] == -2.7
    assert next(item for item in items if item["key"] == "hogar_areas_comunes")["amount"] == -1.05
    assert next(item for item in items if item["key"] == "combo_hogar_grande_adultos")["amount"] == -1
    assert next(item for item in items if item["key"] == "bonus_pasaje")["amount"] == 2
    assert next(item for item in items if item["key"] == "salario")["amount"] == 11.76
    assert not any(item["key"] == "combo_adultos_limpieza_lavar" for item in items)


def test_43_ninos_sin_cuidado_real_no_afectan_casa_5_5():
    sin_ninos = evaluate_solicitud_atractivo(_case_5_5_large_home(ninos="0", observaciones=""))
    ninos_sin_carga = evaluate_solicitud_atractivo(_case_5_5_large_home())
    assert ninos_sin_carga["score"] < sin_ninos["score"]
    assert _component_amount(ninos_sin_carga, "ocupacion_total") == -1.5
    assert _component_amount(ninos_sin_carga, "combo_ninos_pequenos") == 0.0


def test_44_casa_5_5_no_activa_cap_critico_por_si_sola():
    result = evaluate_solicitud_atractivo(_case_5_5_large_home())
    assert result["componentes"]["critical_combinations"] == []
    salary_item = next(item for item in result["componentes"]["items"] if item["key"] == "salario")
    assert "carga relevante no crítica" not in salary_item["label"]


def test_45_salario_22k_24k_26k_sube_claro_y_sin_saltos_absurdos():
    score_22 = evaluate_solicitud_atractivo(_case_5_5_large_home(sueldo="22000"))
    score_24 = evaluate_solicitud_atractivo(_case_5_5_large_home(sueldo="24000"))
    score_26 = evaluate_solicitud_atractivo(_case_5_5_large_home(sueldo="26000"))

    assert 72.0 <= score_22["score"] <= 72.5
    assert 75.2 <= score_24["score"] <= 75.8
    assert 79.8 <= score_26["score"] <= 80.4
    assert score_22["score"] < score_24["score"] < score_26["score"]


def test_46_mismo_caso_dentro_de_rango_queda_en_banda_media():
    result = evaluate_solicitud_atractivo(_case_5_5_large_home(sueldo="20000"))
    assert 69.0 <= result["score"] <= 69.6


def test_47_mismo_caso_con_2_y_6_adultos_se_mueve_en_la_direccion_esperada():
    score_2 = evaluate_solicitud_atractivo(_case_5_5_large_home(adultos="2"))
    score_4 = evaluate_solicitud_atractivo(_case_5_5_large_home(adultos="4"))
    score_6 = evaluate_solicitud_atractivo(_case_5_5_large_home(adultos="6"))

    assert score_2["score"] > score_4["score"] > score_6["score"]


def test_47b_regresion_tabla_adultos_2_a_7_es_monotona_y_gradual():
    scores = [
        evaluate_solicitud_atractivo(_case_5_5_large_home(adultos=str(adultos)))["score"]
        for adultos in range(2, 8)
    ]

    assert scores == sorted(scores, reverse=True)
    drops = [round(scores[idx] - scores[idx + 1], 1) for idx in range(len(scores) - 1)]
    assert all(drop >= 0 for drop in drops)
    assert all(drop <= 5.0 for drop in drops)
    assert scores == [81.1, 79.6, 75.5, 73.0, 71.0, 69.0]


def test_48_nino_pequeno_con_cuidado_real_baja_claramente():
    base = evaluate_solicitud_atractivo(_case_5_5_large_home())
    con_nino = evaluate_solicitud_atractivo(
        _case_5_5_large_home(
            funciones=["ninos", "limpieza", "cocinar", "lavar"],
            edades_ninos="2 y 7",
            observaciones="Hay que cuidar activamente al niño de 2 años.",
        )
    )
    assert con_nino["score"] < base["score"] - 6


def test_49_envejeciente_encamado_baja_mucho_mas():
    base = evaluate_solicitud_atractivo(_case_5_5_large_home())
    encamado = evaluate_solicitud_atractivo(
        _case_5_5_large_home(
            funciones=["envejeciente", "limpieza", "cocinar", "lavar"],
            ninos="0",
            observaciones="",
            envejeciente_tipo_cuidado="encamado",
            envejeciente_responsabilidades=["higiene", "movilidad"],
        )
    )
    assert encamado["score"] < base["score"] - 20


def test_50_auditoria_caso_5_5_2_adultos_21k_explica_80_exacto():
    result = evaluate_solicitud_atractivo(_case_5_5_low_occupancy())
    items = result["componentes"]["items"]

    assert result["componentes"]["score_sin_salario"] == 67.1
    assert result["score"] == 75.9
    assert [item["key"] for item in items] == [
        "ocupacion_total",
        "hogar_carga_fisica",
        "hogar_areas_comunes",
        "hogar_baja_ocupacion",
        "bonus_pasaje",
        "salario",
    ]
    assert next(item for item in items if item["key"] == "ocupacion_total")["amount"] == -0.5
    assert next(item for item in items if item["key"] == "hogar_carga_fisica")["amount"] == -2.7
    assert next(item for item in items if item["key"] == "hogar_areas_comunes")["amount"] == -1.05
    assert next(item for item in items if item["key"] == "hogar_baja_ocupacion")["amount"] == 0.3
    assert next(item for item in items if item["key"] == "bonus_pasaje")["amount"] == 2
    assert next(item for item in items if item["key"] == "salario")["amount"] == 8.81
    assert _salary_relief(result) == 1.0


def test_51_casa_5_5_2_adultos_y_buen_sueldo_llega_a_80_82():
    result = evaluate_solicitud_atractivo(_case_5_5_low_occupancy())
    assert 75.5 <= result["score"] <= 76.2


def test_52_casa_5_5_con_4_y_6_adultos_baja_gradualmente():
    score_2 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(adultos="2"))
    score_4 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(adultos="4"))
    score_6 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(adultos="6"))

    assert score_2["score"] > score_4["score"] > score_6["score"]
    assert round(score_2["score"] - score_4["score"], 1) <= 5.2
    assert round(score_4["score"] - score_6["score"], 1) <= 5.0


def test_53_ninos_sin_cuidado_real_no_afectan_caso_5_5_2_adultos():
    sin_ninos = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(ninos="0", observaciones=""))
    ninos_sin_carga = evaluate_solicitud_atractivo(_case_5_5_low_occupancy())
    assert ninos_sin_carga["score"] < sin_ninos["score"]
    assert _component_amount(ninos_sin_carga, "ocupacion_total") == -0.5
    assert _component_amount(ninos_sin_carga, "combo_ninos_pequenos") == 0.0


def test_54_areas_normales_no_inflan_demasiado_la_carga():
    result = evaluate_solicitud_atractivo(
        _case_5_5_low_occupancy(
            areas_comunes=["sala", "comedor", "cocina", "marquesina"],
        )
    )
    assert 83.5 <= result["score"] <= 84.0
    assert _component_amount(result, "hogar_areas_comunes") == -0.1
    assert not any(item["key"] == "salario" and "carga relevante no crítica" in item["label"] for item in result["componentes"]["items"])


def test_55_areas_especiales_restan_de_forma_moderada_via_rango():
    normales = evaluate_solicitud_atractivo(
        _case_5_5_low_occupancy(areas_comunes=["sala", "comedor", "cocina", "marquesina"])
    )
    especiales = evaluate_solicitud_atractivo(
        _case_5_5_low_occupancy(areas_comunes=["sala", "comedor", "cocina", "marquesina", "piscina", "salon_juegos"])
    )
    assert especiales["componentes"]["salary_reference"]["reference_min"] == normales["componentes"]["salary_reference"]["reference_min"]
    assert round(_component_amount(especiales, "hogar_areas_comunes") - _component_amount(normales, "hogar_areas_comunes"), 2) == -0.25
    assert especiales["score"] <= normales["score"]
    assert round(normales["score"] - especiales["score"], 1) == 0.2


def test_55b_matriz_areas_comunes_suaviza_impacto_y_evita_doble_castigo_salarial():
    base_areas = ["sala", "comedor", "cocina"]
    all_real_areas = [
        "sala",
        "comedor",
        "cocina",
        "salon_juegos",
        "terraza",
        "jardin",
        "estudio",
        "patio",
        "piscina",
        "marquesina",
    ]
    base_payload = _dormida_payload(
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="2",
        adultos="3",
        ninos="2",
        edades_ninos="2 y 3 años",
        ayuda_cuidado_ninos="sin_ayuda",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        areas_comunes=base_areas,
        sueldo="20000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )

    def result_for(areas):
        return evaluate_solicitud_atractivo(dict(base_payload, areas_comunes=areas))

    base = result_for(base_areas)
    expected = {
        "sala": {"areas": base_areas, "area": 0.0, "salario": 2.56, "raw_delta": 0.0, "final_delta": 0.0, "ref": 20500},
        "comedor": {"areas": base_areas, "area": 0.0, "salario": 2.56, "raw_delta": 0.0, "final_delta": 0.0, "ref": 20500},
        "cocina": {"areas": base_areas, "area": 0.0, "salario": 2.56, "raw_delta": 0.0, "final_delta": 0.0, "ref": 20500},
        "salon_juegos": {"areas": base_areas + ["salon_juegos"], "area": -0.25, "salario": 2.56, "raw_delta": -0.25, "final_delta": -0.2, "ref": 20500},
        "terraza": {"areas": base_areas + ["terraza"], "area": -0.25, "salario": 2.56, "raw_delta": -0.25, "final_delta": -0.2, "ref": 20500},
        "jardin": {"areas": base_areas + ["jardin"], "area": -0.25, "salario": 2.56, "raw_delta": -0.25, "final_delta": -0.2, "ref": 20500},
        "estudio": {"areas": base_areas + ["estudio"], "area": -0.1, "salario": 2.56, "raw_delta": -0.1, "final_delta": -0.1, "ref": 20500},
        "patio": {"areas": base_areas + ["patio"], "area": -0.1, "salario": 2.56, "raw_delta": -0.1, "final_delta": -0.1, "ref": 20500},
        "piscina": {"areas": base_areas + ["piscina"], "area": 0.0, "salario": 2.56, "raw_delta": 0.0, "final_delta": 0.0, "ref": 20500},
        "marquesina": {"areas": base_areas + ["marquesina"], "area": -0.1, "salario": 2.56, "raw_delta": -0.1, "final_delta": -0.1, "ref": 20500},
        "otro": {"areas": base_areas + ["otro"], "area": -0.5, "salario": 2.56, "raw_delta": -0.5, "final_delta": -0.5, "ref": 20500},
        "todas_anteriores": {"areas": ["todas_anteriores"], "area": -1.05, "salario": 2.56, "raw_delta": -1.05, "final_delta": -1.0, "ref": 20500},
    }

    for name, spec in expected.items():
        result = result_for(spec["areas"])
        raw_delta = round(result["componentes"]["score_before_salary_excellence_cap"] - base["componentes"]["score_before_salary_excellence_cap"], 2)
        final_delta = round(result["score"] - base["score"], 1)
        assert _component_amount(result, "hogar_areas_comunes") == spec["area"], name
        assert _component_amount(result, "salario") == spec["salario"], name
        assert _component_amount(result, "bonus_solicitud_normal_atractiva") == 7.0, name
        assert _bucket_amount(result, "combinadas") == _bucket_amount(base, "combinadas"), name
        assert raw_delta == spec["raw_delta"], name
        assert final_delta == spec["final_delta"], name
        assert result["componentes"]["salary_reference"]["reference_min"] == spec["ref"], name
        if name not in {"todas_anteriores"}:
            assert raw_delta >= -0.5

    all_real = result_for(all_real_areas)
    todas = result_for(["todas_anteriores"])
    assert all_real["score"] == todas["score"]
    assert all_real["componentes"]["score_before_salary_excellence_cap"] == todas["componentes"]["score_before_salary_excellence_cap"]
    assert _component_amount(all_real, "hogar_areas_comunes") == _component_amount(todas, "hogar_areas_comunes") == -1.05

    terraza_piscina = result_for(base_areas + ["terraza", "piscina"])
    terraza = result_for(base_areas + ["terraza"])
    piscina_jardin = result_for(base_areas + ["piscina", "jardin"])
    jardin = result_for(base_areas + ["jardin"])
    piscina_salon = result_for(base_areas + ["piscina", "salon_juegos"])
    salon = result_for(base_areas + ["salon_juegos"])
    varias = result_for(base_areas + ["terraza", "jardin", "piscina", "salon_juegos"])
    varias_sin_piscina = result_for(base_areas + ["terraza", "jardin", "salon_juegos"])
    assert terraza_piscina["score"] == terraza["score"]
    assert piscina_jardin["score"] == jardin["score"]
    assert piscina_salon["score"] == salon["score"]
    assert varias["score"] == varias_sin_piscina["score"]
    assert round(base["score"] - terraza_piscina["score"], 1) == 0.2
    assert round(base["score"] - varias["score"], 1) == 0.7
    assert base["score"] >= terraza_piscina["score"] >= varias["score"] >= todas["score"]


def test_55c_areas_comunes_mantienen_impacto_suave_en_modalidades_principales():
    base_areas = ["sala", "comedor", "cocina"]
    several_areas = base_areas + ["terraza", "jardin", "piscina", "salon_juegos"]
    several_areas_without_pool = base_areas + ["terraza", "jardin", "salon_juegos"]
    base_payload = _dormida_payload(
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="2",
        adultos="3",
        ninos="2",
        edades_ninos="2 y 3 años",
        ayuda_cuidado_ninos="sin_ayuda",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        areas_comunes=base_areas,
        sueldo="20000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )
    modes = {
        "sd_l_v": {
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 4:00 PM",
            "horario_tipo": "salida_diaria",
            "dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "4:00 PM",
            "dormida_entrada": "",
            "dormida_salida": "",
        },
        "dormida_l_v": {},
        "dormida_l_s": {
            "modalidad_trabajo": "Con dormida 💤 lunes a sábado",
            "horario": "Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
            "dormida_salida": "sábado 1:00 PM",
        },
        "quincenal": {
            "modalidad_trabajo": "Con dormida 💤 quincenal",
            "horario": "Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
            "dormida_salida": "segundo viernes 12:00 PM",
        },
    }
    for overrides in modes.values():
        base = evaluate_solicitud_atractivo(dict(base_payload, **overrides, areas_comunes=base_areas))
        piscina = evaluate_solicitud_atractivo(dict(base_payload, **overrides, areas_comunes=base_areas + ["piscina"]))
        varias = evaluate_solicitud_atractivo(dict(base_payload, **overrides, areas_comunes=several_areas))
        varias_sin_piscina = evaluate_solicitud_atractivo(dict(base_payload, **overrides, areas_comunes=several_areas_without_pool))

        assert base["score"] == piscina["score"]
        assert varias["score"] == varias_sin_piscina["score"]
        assert 0.5 <= round(base["score"] - varias["score"], 1) <= 0.8
        assert base["score"] >= piscina["score"] >= varias["score"]


def test_56_no_hay_doble_penalizacion_por_tamano_areas_y_baja_ocupacion():
    result = evaluate_solicitud_atractivo(_case_5_5_low_occupancy())
    keys = [item["key"] for item in result["componentes"]["items"]]
    assert "combo_hogar_grande_adultos" not in keys
    assert "combo_adultos_limpieza_lavar" not in keys
    assert keys.count("hogar_carga_fisica") == 1
    assert not {"hogar_5", "hogar_5h_gradual", "hogar_banos_gradual"}.intersection(keys)


def test_57_subir_20k_21k_22k_mejora_de_forma_gradual():
    score_20 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(sueldo="20000"))
    score_21 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(sueldo="21000"))
    score_22 = evaluate_solicitud_atractivo(_case_5_5_low_occupancy(sueldo="22000"))

    assert 74.3 <= score_20["score"] <= 74.9
    assert 75.5 <= score_21["score"] <= 76.2
    assert 77.2 <= score_22["score"] <= 77.8
    assert score_20["score"] < score_21["score"] < score_22["score"]


def test_58_auditoria_5_5_baja_ocupacion_adultos_2_4_5_6_desglosa_salto_salarial():
    audited = {
        adultos: evaluate_solicitud_atractivo(_case_5_5_low_occupancy(adultos=str(adultos)))
        for adultos in (2, 4, 5, 6)
    }

    assert audited[2]["score"] == 75.9
    assert audited[4]["score"] == 70.8
    assert audited[5]["score"] == 68.3
    assert audited[6]["score"] == 66.3

    ref_2 = audited[2]["componentes"]["salary_reference"]
    ref_4 = audited[4]["componentes"]["salary_reference"]
    ref_5 = audited[5]["componentes"]["salary_reference"]
    ref_6 = audited[6]["componentes"]["salary_reference"]

    assert (ref_2["reference_min"], ref_2["reference_max"]) == (18500, 20500)
    assert (ref_4["reference_min"], ref_4["reference_max"]) == (19500, 21000)
    assert (ref_5["reference_min"], ref_5["reference_max"]) == (19500, 21000)
    assert (ref_6["reference_min"], ref_6["reference_max"]) == (19500, 21000)

    salario_4 = next(item for item in audited[4]["componentes"]["items"] if item["key"] == "salario")
    salario_5 = next(item for item in audited[5]["componentes"]["items"] if item["key"] == "salario")
    salario_6 = next(item for item in audited[6]["componentes"]["items"] if item["key"] == "salario")

    assert salario_4["amount"] == 7.0
    assert salario_5["amount"] == 7.0
    assert salario_6["amount"] == 7.0
    assert audited[4]["componentes"]["score_sin_salario"] == 63.8
    assert audited[5]["componentes"]["score_sin_salario"] == 61.3
    assert audited[6]["componentes"]["score_sin_salario"] == 59.3
    assert _salary_relief(audited[2]) == 1.0
    assert _salary_relief(audited[4]) is None
    assert _salary_relief(audited[5]) is None
    assert _salary_relief(audited[6]) is None


def test_58b_auditoria_3h_banos_2_25_3_desglosa_transicion_gradual():
    audited = {
        banos: evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos="2",
                pasaje_mode="no_incluido",
                detalles_servicio={"pasaje": {"mode": "no_incluido"}},
                sueldo="19500",
                habitaciones="3",
                banos=str(banos),
            )
        )
        for banos in (2, 2.5, 3)
    }

    def _amount(result, key):
        return next((item["amount"] for item in result["componentes"]["items"] if item["key"] == key), 0.0)

    assert audited[2]["componentes"]["base"] == 69
    assert audited[2]["componentes"]["score_sin_salario"] == 79.3
    assert audited[2]["score"] == 85.8
    assert _amount(audited[2], "bonus_solicitud_normal_atractiva") == 10.5
    assert _amount(audited[2], "hogar_3_3") == 0.0
    assert _amount(audited[2], "hogar_carga_fisica") == -0.2
    assert _amount(audited[2], "salario") == 6.5
    assert audited[2]["componentes"]["salary_reference"]["reference_min"] == 18000
    assert audited[2]["componentes"]["salary_reference"]["reference_max"] == 20000

    assert audited[2.5]["componentes"]["score_sin_salario"] == 78.7
    assert audited[2.5]["score"] == 85.2
    assert _amount(audited[2.5], "bonus_solicitud_normal_atractiva") == 10.0
    assert _amount(audited[2.5], "hogar_3_3") == 0.0
    assert _amount(audited[2.5], "hogar_carga_fisica") == -0.3
    assert _amount(audited[2.5], "salario") == 6.5
    assert audited[2.5]["componentes"]["salary_reference"]["reference_min"] == 18000
    assert audited[2.5]["componentes"]["salary_reference"]["reference_max"] == 20000

    assert audited[3]["componentes"]["score_sin_salario"] == 78.6
    assert audited[3]["score"] == 85.1
    assert _amount(audited[3], "bonus_solicitud_normal_atractiva") == 10.0
    assert _amount(audited[3], "hogar_3_3") == 0.0
    assert _amount(audited[3], "hogar_banos_gradual") == 0.0
    assert _amount(audited[3], "hogar_carga_fisica") == -0.4
    assert _amount(audited[3], "salario") == 6.5
    assert audited[3]["componentes"]["salary_reference"]["reference_min"] == 18000
    assert audited[3]["componentes"]["salary_reference"]["reference_max"] == 20000

    assert round(audited[2]["score"] - audited[2.5]["score"], 1) == 0.6
    assert round(audited[2.5]["score"] - audited[3]["score"], 1) == 0.1
    assert round(_amount(audited[2], "bonus_solicitud_normal_atractiva") - _amount(audited[2.5], "bonus_solicitud_normal_atractiva"), 1) == 0.5
    assert round(_amount(audited[2.5], "bonus_solicitud_normal_atractiva") - _amount(audited[3], "bonus_solicitud_normal_atractiva"), 1) == 0.0
    assert round(_amount(audited[2.5], "salario") - _amount(audited[3], "salario"), 1) == 0.0


def test_58f_calibracion_hogar_normal_3h_3b_3h_35b_y_pasaje():
    included_3_3 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="3",
            banos="3",
        )
    )
    included_3_35 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="3",
            banos="3.5",
        )
    )
    included_2_2 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="2",
            banos="2",
        )
    )
    included_4_4 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="4",
            banos="4",
        )
    )
    aparte_3_3 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
            sueldo="20000",
            habitaciones="3",
            banos="3",
        )
    )
    with_ninos_sin_cuidado = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="3",
            banos="3",
            ninos="2",
            nota_cliente="Los niños ya estudian casi todo el dia y no requieren cuidado real.",
        )
    )
    adults_3 = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="3",
            pasaje_mode="incluido",
            detalles_servicio={"pasaje": {"mode": "incluido"}},
            sueldo="20000",
            habitaciones="3",
            banos="3",
        )
    )

    assert 85.0 <= included_3_3["score"] <= 89.0
    assert 84.5 <= included_3_35["score"] <= 88.0
    assert included_3_3["score"] > included_3_35["score"]
    assert included_2_2["score"] > included_3_3["score"] > included_4_4["score"]
    assert _component_amount(included_3_3, "bonus_pasaje") == 0.0
    assert aparte_3_3["score"] > included_3_3["score"]
    assert with_ninos_sin_cuidado["score"] == included_3_3["score"] - 0.5
    assert _component_amount(with_ninos_sin_cuidado, "ocupacion_total") == -0.5
    assert _component_amount(with_ninos_sin_cuidado, "combo_ninos_pequenos") == 0.0
    assert adults_3["score"] >= 85.0


@pytest.mark.parametrize("banos", ["2", "3", "5"])
def test_58c_tabla_habitaciones_1_a_6_es_monotona_y_3h_ya_baja(banos):
    audited = {
        habitaciones: evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos="2",
                pasaje_mode="no_incluido",
                detalles_servicio={"pasaje": {"mode": "no_incluido"}},
                sueldo="19500",
                habitaciones=str(habitaciones),
                banos=banos,
            )
        )
        for habitaciones in range(1, 7)
    }

    scores = [audited[habitaciones]["score"] for habitaciones in range(1, 7)]
    bonus_normal = [
        _component_amount(audited[habitaciones], "bonus_solicitud_normal_atractiva")
        for habitaciones in range(1, 7)
    ]
    reliefs = [(_salary_relief(audited[habitaciones]) or 0.0) for habitaciones in range(1, 7)]

    assert scores == sorted(scores, reverse=True)
    assert bonus_normal == sorted(bonus_normal, reverse=True)
    assert reliefs == sorted(reliefs, reverse=True)
    assert audited[3]["score"] < audited[2]["score"]


@pytest.mark.parametrize("habitaciones", ["2", "3", "4", "5", "6"])
def test_58d_barrido_banos_permanece_monotono(habitaciones):
    bath_values = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6]
    results = [
        evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos="2",
                pasaje_mode="no_incluido",
                detalles_servicio={"pasaje": {"mode": "no_incluido"}},
                sueldo="19500",
                habitaciones=habitaciones,
                banos=str(banos),
            )
        )
        for banos in bath_values
    ]

    scores = [result["score"] for result in results]
    assert scores == sorted(scores, reverse=True)


def test_58e_4h_45b_permanece_por_encima_de_4h_5b():
    left = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="19500",
            habitaciones="4",
            banos="4.5",
        )
    )
    right = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="19500",
            habitaciones="4",
            banos="5",
        )
    )

    assert left["score"] >= right["score"]


@pytest.mark.parametrize(
    ("adultos", "pasaje_mode", "sueldo"),
    [
        ("2", "no_incluido", "19500"),
        ("2", "no_incluido", "23000"),
        ("2", "aparte", "19500"),
        ("2", "aparte", "23000"),
        ("4", "no_incluido", "19500"),
        ("4", "no_incluido", "23000"),
        ("4", "aparte", "19500"),
        ("4", "aparte", "23000"),
    ],
)
def test_59_monotonia_habitaciones_no_critica(adultos, pasaje_mode, sueldo):
    scores = [
        evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones=str(habitaciones),
                banos="3",
            )
        )["score"]
        for habitaciones in [1, 2, 3, 4, 5, 6]
    ]
    assert scores == sorted(scores, reverse=True)
    drops = [round(scores[idx] - scores[idx + 1], 1) for idx in range(len(scores) - 1)]
    assert all(drop <= 6.0 for drop in drops)


@pytest.mark.parametrize(
    ("adultos", "pasaje_mode", "sueldo"),
    [
        ("2", "no_incluido", "19500"),
        ("2", "no_incluido", "23000"),
        ("2", "aparte", "19500"),
        ("2", "aparte", "23000"),
        ("4", "no_incluido", "19500"),
        ("4", "no_incluido", "23000"),
        ("4", "aparte", "19500"),
        ("4", "aparte", "23000"),
    ],
)
def test_60_monotonia_banos_no_critica(adultos, pasaje_mode, sueldo):
    scores = [
        evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones="3",
                banos=str(banos),
            )
        )["score"]
        for banos in [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6]
    ]
    assert scores == sorted(scores, reverse=True)
    drops = [round(scores[idx] - scores[idx + 1], 1) for idx in range(len(scores) - 1)]
    assert all(drop <= 5.0 for drop in drops)


@pytest.mark.parametrize(
    ("adultos", "pasaje_mode", "sueldo"),
    [
        ("2", "no_incluido", "19500"),
        ("2", "no_incluido", "23000"),
        ("2", "aparte", "19500"),
        ("2", "aparte", "23000"),
        ("4", "no_incluido", "19500"),
        ("4", "no_incluido", "23000"),
        ("4", "aparte", "19500"),
        ("4", "aparte", "23000"),
    ],
)
def test_61_monotonia_matriz_combinada_no_critica(adultos, pasaje_mode, sueldo):
    chain = [(2, 2), (3, 2), (3, 3), (4, 3), (4, 4), (5, 4), (5, 5)]
    scores = [
        evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones=str(habitaciones),
                banos=str(banos),
            )
        )["score"]
        for habitaciones, banos in chain
    ]
    assert scores == sorted(scores, reverse=True)


def test_62_auditoria_transicion_4h3b_a_6h6b_desglosa_bonus_gradual():
    audited = {
        (habitaciones, banos): evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos="2",
                pasaje_mode="no_incluido",
                detalles_servicio={"pasaje": {"mode": "no_incluido"}},
                sueldo="19500",
                habitaciones=str(habitaciones),
                banos=str(banos),
            )
        )
        for habitaciones, banos in [(4, 3), (4, 4), (5, 4), (5, 5), (6, 6)]
    }

    expected = {
        (4, 3): {
            "score": 83.9,
            "bonus": 9.5,
            "hogar": -0.9,
            "baja_ocupacion": 0.0,
            "range": (18250, 20250),
            "salario": 6.25,
        },
        (4, 4): {
            "score": 82.9,
            "bonus": 9.0,
            "hogar": -1.4,
            "baja_ocupacion": 0.0,
            "range": (18250, 20250),
            "salario": 6.25,
        },
        (5, 4): {
            "score": 81.8,
            "bonus": 8.5,
            "hogar": -2.05,
            "baja_ocupacion": 0.3,
            "range": (18500, 20500),
            "salario": 6.0,
        },
        (5, 5): {
            "score": 80.6,
            "bonus": 8.0,
            "hogar": -2.7,
            "baja_ocupacion": 0.3,
            "range": (18500, 20500),
            "salario": 6.0,
        },
        (6, 6): {
            "score": 77.3,
            "bonus": 6.5,
            "hogar": -4.0,
            "baja_ocupacion": 0.3,
            "range": (18750, 21750),
            "salario": 5.5,
        },
    }

    obsolete_keys = {
        "hogar_3_3",
        "hogar_4_3",
        "hogar_4_4",
        "hogar_5",
        "hogar_6",
        "hogar_4h_gradual",
        "hogar_5h_gradual",
        "hogar_6h_gradual",
        "hogar_banos_gradual",
    }
    for size, spec in expected.items():
        result = audited[size]
        keys = {item["key"] for item in result["componentes"]["items"]}
        assert result["componentes"]["base"] == 69
        assert result["score"] == spec["score"]
        assert _component_amount(result, "bonus_solicitud_normal_atractiva") == spec["bonus"]
        assert _component_amount(result, "hogar_carga_fisica") == spec["hogar"]
        assert _component_amount(result, "hogar_baja_ocupacion") == spec["baja_ocupacion"]
        assert _salary_range(result) == spec["range"]
        assert _component_amount(result, "salario") == spec["salario"]
        assert result["componentes"]["salary_reference"].get("load_relief") is None
        assert not obsolete_keys.intersection(keys)


def test_62c_matriz_payload_real_vivienda_desglosa_carga_fisica_salario_bonus_y_combos():
    base_payload = _dormida_payload(
        tipo_lugar="casa",
        habitaciones="2",
        banos="2",
        pisos="1",
        adultos="2",
        ninos="2",
        edades_ninos="2 y 3 años",
        ayuda_cuidado_ninos="sin_ayuda",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        areas_comunes=["sala", "comedor", "cocina"],
        sueldo="20000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )
    sizes = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (3, 2), (2, 3), (4, 3), (3, 4), (5, 4), (4, 5)]
    audited = {
        size: evaluate_solicitud_atractivo(dict(base_payload, habitaciones=str(size[0]), banos=str(size[1])))
        for size in sizes
    }

    expected = {
        (2, 2): {"hogar": 0.0, "salario": 2.56, "bonus": 7.5, "ocupacion": -0.5, "combos": -3.88, "raw": 81.93, "final": 81.9, "ref": 20500},
        (3, 3): {"hogar": -0.4, "salario": 2.56, "bonus": 7.0, "ocupacion": -0.5, "combos": -3.88, "raw": 81.03, "final": 81.0, "ref": 20500},
        (4, 4): {"hogar": -1.4, "salario": 1.39, "bonus": 6.0, "ocupacion": -0.5, "combos": -3.88, "raw": 77.86, "final": 77.9, "ref": 20750},
        (5, 5): {"hogar": -2.7, "salario": 0.24, "bonus": 5.0, "ocupacion": -0.5, "combos": -3.88, "raw": 74.41, "final": 74.4, "ref": 21000},
        (6, 6): {"hogar": -4.0, "salario": -0.88, "bonus": 3.5, "ocupacion": -0.5, "combos": -3.88, "raw": 70.49, "final": 70.5, "ref": 21250},
        (3, 2): {"hogar": -0.2, "salario": 2.56, "bonus": 7.5, "ocupacion": -0.5, "combos": -3.88, "raw": 81.73, "final": 81.7, "ref": 20500},
        (2, 3): {"hogar": -0.2, "salario": 2.56, "bonus": 7.5, "ocupacion": -0.5, "combos": -3.88, "raw": 81.73, "final": 81.7, "ref": 20500},
        (4, 3): {"hogar": -0.9, "salario": 1.39, "bonus": 6.5, "ocupacion": -0.5, "combos": -3.88, "raw": 78.86, "final": 78.9, "ref": 20750},
        (3, 4): {"hogar": -0.9, "salario": 1.39, "bonus": 6.5, "ocupacion": -0.5, "combos": -3.88, "raw": 78.86, "final": 78.9, "ref": 20750},
        (5, 4): {"hogar": -2.05, "salario": 0.24, "bonus": 5.5, "ocupacion": -0.5, "combos": -3.88, "raw": 75.56, "final": 75.6, "ref": 21000},
        (4, 5): {"hogar": -2.05, "salario": 0.24, "bonus": 5.5, "ocupacion": -0.5, "combos": -3.88, "raw": 75.56, "final": 75.6, "ref": 21000},
    }

    for size, spec in expected.items():
        result = audited[size]
        assert _bucket_amount(result, "hogar") == spec["hogar"]
        assert _component_amount(result, "salario") == spec["salario"]
        assert _component_amount(result, "bonus_solicitud_normal_atractiva") == spec["bonus"]
        assert _bucket_amount(result, "ocupacion") == spec["ocupacion"]
        assert _bucket_amount(result, "combinadas") == spec["combos"]
        assert result["componentes"]["score_before_salary_excellence_cap"] == spec["raw"]
        assert result["score"] == spec["final"]
        assert result["componentes"]["salary_reference"]["reference_min"] == spec["ref"]

    diagonal = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
    scores = [audited[size]["score"] for size in diagonal]
    raw_scores = [audited[size]["componentes"]["score_before_salary_excellence_cap"] for size in diagonal]
    assert scores == sorted(scores, reverse=True)
    assert raw_scores == sorted(raw_scores, reverse=True)
    assert [round(scores[idx] - scores[idx + 1], 1) for idx in range(4)] == [0.9, 3.1, 3.5, 3.9]


def test_62d_vivienda_mantiene_progresion_por_modalidad_y_perfiles_de_carga():
    base_payload = _dormida_payload(
        tipo_lugar="casa",
        habitaciones="2",
        banos="2",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        ayuda_cuidado_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        areas_comunes=["sala", "comedor", "cocina"],
        sueldo="22000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
    )

    profiles = {
        "simple": {},
        "supervision": {"ninos": "2", "edades_ninos": "8 y 10 años", "funciones": ["limpieza", "cocinar", "lavar", "ninos"]},
        "un_nino_pequeno": {"ninos": "1", "edades_ninos": "2 años", "ayuda_cuidado_ninos": "sin_ayuda", "funciones": ["limpieza", "cocinar", "lavar", "ninos"]},
        "dos_pequenos_con_ayuda": {"ninos": "2", "edades_ninos": "2 y 3 años", "ayuda_cuidado_ninos": "con_ayuda", "funciones": ["limpieza", "cocinar", "lavar", "ninos"]},
        "dos_pequenos_sin_ayuda": {"ninos": "2", "edades_ninos": "2 y 3 años", "ayuda_cuidado_ninos": "sin_ayuda", "funciones": ["limpieza", "cocinar", "lavar", "ninos"]},
        "envejeciente": {"funciones": ["limpieza", "cocinar", "lavar", "envejeciente"], "envejeciente_tipo_cuidado": "independiente"},
        "encamado": {"funciones": ["limpieza", "cocinar", "lavar", "envejeciente"], "envejeciente_tipo_cuidado": "encamado", "envejeciente_responsabilidades": ["higiene", "movilidad"]},
    }

    for overrides in profiles.values():
        audited = [
            evaluate_solicitud_atractivo(dict(base_payload, **overrides, habitaciones=str(habitaciones), banos=str(banos)))["score"]
            for habitaciones, banos in [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        ]
        assert audited == sorted(audited, reverse=True)
        drops = [round(audited[idx] - audited[idx + 1], 1) for idx in range(4)]
        assert drops[1] <= 3.0
        assert drops[2] <= 3.5
        assert drops[3] <= 4.0


def test_62b_auditoria_piso_45_en_6h6b_depende_de_compensacion_salarial():
    audited = {
        "sin_sueldo": evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                sueldo="",
                habitaciones="6",
                banos="6",
            )
        ),
        "sueldo_bajo": evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                sueldo="18000",
                habitaciones="6",
                banos="6",
            )
        ),
        "sueldo_minimo": evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                sueldo="19000",
                habitaciones="6",
                banos="6",
            )
        ),
        "sueldo_en_rango": evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                sueldo="20000",
                habitaciones="6",
                banos="6",
            )
        ),
        "sueldo_sobre_rango": evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                sueldo="23000",
                habitaciones="6",
                banos="6",
            )
        ),
    }

    assert _component_amount(audited["sin_sueldo"], "bonus_solicitud_normal_atractiva") == 6.5
    assert _component_amount(audited["sin_sueldo"], "salario") == 0.0
    assert _salary_relief(audited["sin_sueldo"]) is None
    assert audited["sin_sueldo"]["score"] == 71.8

    assert _component_amount(audited["sueldo_bajo"], "bonus_solicitud_normal_atractiva") == 6.5
    assert _component_amount(audited["sueldo_bajo"], "salario") == 1.0
    assert _salary_relief(audited["sueldo_bajo"]) is None
    assert audited["sueldo_bajo"]["score"] == 72.8

    assert _component_amount(audited["sueldo_minimo"], "bonus_solicitud_normal_atractiva") == 6.5
    assert _component_amount(audited["sueldo_minimo"], "salario") == 5.17
    assert _salary_relief(audited["sueldo_minimo"]) is None
    assert audited["sueldo_minimo"]["score"] == 77.0

    assert _component_amount(audited["sueldo_en_rango"], "bonus_solicitud_normal_atractiva") == 6.5
    assert _component_amount(audited["sueldo_en_rango"], "salario") == 5.83
    assert _salary_relief(audited["sueldo_en_rango"]) is None
    assert audited["sueldo_en_rango"]["score"] == 77.6

    assert _component_amount(audited["sueldo_sobre_rango"], "bonus_solicitud_normal_atractiva") == 6.5
    assert _component_amount(audited["sueldo_sobre_rango"], "salario") == 8.92
    assert _salary_relief(audited["sueldo_sobre_rango"]) is None
    assert audited["sueldo_sobre_rango"]["score"] == 80.7

    assert audited["sin_sueldo"]["score"] < audited["sueldo_bajo"]["score"] < audited["sueldo_minimo"]["score"]
    assert audited["sueldo_minimo"]["score"] < audited["sueldo_en_rango"]["score"] < audited["sueldo_sobre_rango"]["score"]


@pytest.mark.parametrize(
    ("habitaciones_a", "banos_a", "habitaciones_b", "banos_b"),
    [
        (4, 3, 4, 4),
        (3, 3, 4, 4),
        (4, 4, 5, 5),
        (5, 5, 6, 6),
    ],
)
@pytest.mark.parametrize("adultos", ["2", "4"])
@pytest.mark.parametrize("pasaje_mode", ["no_incluido", "aparte"])
@pytest.mark.parametrize("sueldo", ["19500", "23000"])
def test_63_transiciones_grandes_siguen_monotonas_y_sin_saltos_irrazonables(
    habitaciones_a, banos_a, habitaciones_b, banos_b, adultos, pasaje_mode, sueldo
):
    left = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos=adultos,
            pasaje_mode=pasaje_mode,
            detalles_servicio={"pasaje": {"mode": pasaje_mode}},
            sueldo=sueldo,
            habitaciones=str(habitaciones_a),
            banos=str(banos_a),
        )
    )
    right = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos=adultos,
            pasaje_mode=pasaje_mode,
            detalles_servicio={"pasaje": {"mode": pasaje_mode}},
            sueldo=sueldo,
            habitaciones=str(habitaciones_b),
            banos=str(banos_b),
        )
    )

    assert left["score"] >= right["score"]
    assert round(left["score"] - right["score"], 1) <= 6.5


@pytest.mark.parametrize("adultos", ["2", "4"])
@pytest.mark.parametrize("pasaje_mode", ["no_incluido", "aparte"])
@pytest.mark.parametrize("sueldo", ["19500", "23000"])
def test_64_cadena_4h3b_a_6h6b_se_mantiene_ordenada_y_el_relief_no_invierte(adultos, pasaje_mode, sueldo):
    chain = [(4, 3), (4, 4), (5, 4), (5, 5), (6, 6)]
    results = [
        evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones=str(habitaciones),
                banos=str(banos),
            )
        )
        for habitaciones, banos in chain
    ]

    scores = [result["score"] for result in results]
    drops = [round(scores[idx] - scores[idx + 1], 1) for idx in range(len(scores) - 1)]

    assert scores == sorted(scores, reverse=True)
    assert all(drop <= 6.0 for drop in drops)


@pytest.mark.parametrize("adultos", ["2", "4"])
@pytest.mark.parametrize("pasaje_mode", ["no_incluido", "aparte"])
@pytest.mark.parametrize("sueldo", ["19500", "23000"])
def test_65_matriz_completa_habitaciones_y_banos_no_tiene_inversiones(adultos, pasaje_mode, sueldo):
    habitaciones_values = [1, 2, 3, 4, 5, 6]
    banos_values = [1, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6]
    audited = {
        (habitaciones, banos): evaluate_solicitud_atractivo(
            _noncritical_monotonic_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones=str(habitaciones),
                banos=str(banos),
            )
        )
        for habitaciones in habitaciones_values
        for banos in banos_values
    }

    for banos in banos_values:
        row_scores = [audited[(habitaciones, banos)]["score"] for habitaciones in habitaciones_values]
        row_bonus = [
            _component_amount(audited[(habitaciones, banos)], "bonus_solicitud_normal_atractiva")
            for habitaciones in habitaciones_values
        ]
        row_relief = [(_salary_relief(audited[(habitaciones, banos)]) or 0.0) for habitaciones in habitaciones_values]
        assert row_scores == sorted(row_scores, reverse=True)
        assert row_bonus == sorted(row_bonus, reverse=True)
        if adultos == "2":
            assert row_relief == sorted(row_relief, reverse=True)
            assert _pre_salary_cap_score(audited[(3, banos)]) < _pre_salary_cap_score(audited[(2, banos)])
        else:
            assert all(relief == 0.0 for relief in row_relief)

    for habitaciones in habitaciones_values:
        col_scores = [audited[(habitaciones, banos)]["score"] for banos in banos_values]
        col_bonus = [
            _component_amount(audited[(habitaciones, banos)], "bonus_solicitud_normal_atractiva")
            for banos in banos_values
        ]
        col_relief = [(_salary_relief(audited[(habitaciones, banos)]) or 0.0) for banos in banos_values]
        assert col_scores == sorted(col_scores, reverse=True)
        assert col_bonus == sorted(col_bonus, reverse=True)
        assert col_relief == sorted(col_relief, reverse=True)


def test_66_baja_ocupacion_no_invierte_frente_a_hogares_mas_pequenos_equivalentes():
    compact = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="19500",
            habitaciones="4",
            banos="4",
        )
    )
    low_occupancy_large = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="2",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="19500",
            habitaciones="5",
            banos="5",
        )
    )

    assert _component_amount(low_occupancy_large, "hogar_baja_ocupacion") == 0.3
    assert compact["score"] >= low_occupancy_large["score"]


def test_67_relief_salarial_no_convierte_tamano_extra_en_ventaja():
    moderate = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="4",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="23000",
            habitaciones="4",
            banos="4",
        )
    )
    larger = evaluate_solicitud_atractivo(
        _noncritical_monotonic_payload(
            adultos="4",
            pasaje_mode="no_incluido",
            detalles_servicio={"pasaje": {"mode": "no_incluido"}},
            sueldo="23000",
            habitaciones="5",
            banos="5",
        )
    )

    assert (_salary_relief(larger) or 0.0) <= (_salary_relief(moderate) or 0.0)
    assert moderate["score"] >= larger["score"]


def test_68_payload_ui_3h_2b_supera_4h_2b_y_explica_el_empate_previo():
    score_2 = evaluate_solicitud_atractivo(_ui_room_continuity_payload(habitaciones="2"))
    score_3 = evaluate_solicitud_atractivo(_ui_room_continuity_payload(habitaciones="3"))
    score_4 = evaluate_solicitud_atractivo(_ui_room_continuity_payload(habitaciones="4"))

    assert score_2["score"] == 75.2
    assert score_3["score"] == 74.5
    assert score_4["score"] == 73.8
    assert score_2["score"] > score_3["score"] > score_4["score"]
    assert _component_amount(score_2, "bonus_solicitud_normal_atractiva") == 0.0
    assert _component_amount(score_3, "bonus_solicitud_normal_atractiva") == 0.0
    assert _component_amount(score_4, "bonus_solicitud_normal_atractiva") == 0.0
    assert _component_amount(score_3, "hogar_carga_fisica") == -0.2
    assert _component_amount(score_4, "hogar_carga_fisica") == -0.7
    assert _salary_relief(score_3) is None
    assert _salary_relief(score_4) is None
    assert score_3["componentes"]["salary_reference"]["reference_min"] == 19000
    assert score_4["componentes"]["salary_reference"]["reference_min"] == 19250


@pytest.mark.parametrize("banos", ["2", "3", "5"])
@pytest.mark.parametrize("sueldo", ["20000", "23000"])
@pytest.mark.parametrize("pasaje_mode", ["no_incluido", "aparte"])
@pytest.mark.parametrize("adultos", ["2", "4"])
def test_69_habitaciones_1_a_6_permanece_estrictamente_monotono_en_payload_ui_variantes(
    adultos, pasaje_mode, sueldo, banos
):
    audited = {
        habitaciones: evaluate_solicitud_atractivo(
            _ui_room_continuity_payload(
                adultos=adultos,
                pasaje_mode=pasaje_mode,
                detalles_servicio={"pasaje": {"mode": pasaje_mode}},
                sueldo=sueldo,
                habitaciones=str(habitaciones),
                banos=banos,
            )
        )
        for habitaciones in [1, 2, 3, 4, 5, 6]
    }

    assert audited[1]["score"] >= audited[2]["score"]
    assert _pre_salary_cap_score(audited[2]) > _pre_salary_cap_score(audited[3])
    assert _pre_salary_cap_score(audited[3]) > _pre_salary_cap_score(audited[4])
    assert _pre_salary_cap_score(audited[4]) > _pre_salary_cap_score(audited[5])
    assert _pre_salary_cap_score(audited[5]) > _pre_salary_cap_score(audited[6])


def _dormida_l_s_calibracion_payload(**overrides):
    data = _dormida_payload(
        modalidad_trabajo="Con dormida 💤 lunes a sábado",
        horario="Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
        dormida_entrada="lunes 8:00 AM",
        dormida_salida="sábado 1:00 PM",
        tipo_lugar="casa",
        habitaciones="3",
        banos="3",
        pisos="1",
        adultos="2",
        ninos="0",
        edades_ninos="",
        funciones=["limpieza", "cocinar", "lavar"],
        sueldo="22000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
    )
    data.update(overrides)
    return data


def test_70_dormida_l_s_sabado_1pm_payload_real_queda_en_rango_objetivo():
    result = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload())

    assert 86.0 <= result["score"] <= 87.0
    assert result["componentes"]["score_sin_salario"] == 80.6
    assert _component_amount(result, "modalidad_cd_l_s") == -2.5
    assert _component_amount(result, "dormida_ls_salida_favorable") == 3.0
    assert _component_amount(result, "bonus_solicitud_normal_atractiva") == 9.55
    assert _component_amount(result, "bonus_pasaje") == 2.0
    assert _component_amount(result, "salario") == 6.0
    assert _salary_range(result) == (21000, 23000)
    assert _salary_relief(result) is None


def test_71_dormida_l_s_escala_salarial_objetivo_y_comparacion_l_v():
    scores = {
        sueldo: evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(sueldo=str(sueldo)))["score"]
        for sueldo in (20000, 21000, 22000, 23000, 24000)
    }
    l_v = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            modalidad_trabajo="Con dormida 💤 lunes a viernes",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            dormida_salida="viernes 4:00 PM",
            sueldo="22000",
        )
    )

    assert 83.5 <= scores[20000] <= 84.2
    assert 85.4 <= scores[21000] <= 86.0
    assert 86.3 <= scores[22000] <= 87.0
    assert 84.0 <= scores[23000] <= 88.0
    assert 86.0 <= scores[24000] <= 90.0
    assert scores[20000] < scores[21000] < scores[22000] < scores[23000] < scores[24000]
    assert 88.0 <= l_v["score"] <= 90.0
    assert 2.0 <= (l_v["score"] - scores[22000]) <= 6.0


def test_72_dormida_l_s_salida_sabado_12_1_2_y_3pm_es_monotona():
    at_12 = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(dormida_salida="sábado 12:00 PM"))
    at_1 = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(dormida_salida="sábado 1:00 PM"))
    at_2 = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(dormida_salida="sábado 2:00 PM"))
    at_3 = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(dormida_salida="sábado 3:00 PM"))

    assert at_12["score"] > at_1["score"] > at_2["score"] > at_3["score"]
    assert _component_amount(at_1, "dormida_ls_salida_favorable") == 3.0
    assert _component_amount(at_2, "dormida_ls_salida_2pm") == 1.0
    assert _component_amount(at_3, "bonus_solicitud_normal_atractiva") == 0.0


def test_73_dormida_l_s_cargas_bajan_sin_romper_casos_criticos():
    normal = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload())
    adultos_4 = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(adultos="4"))
    nino_pequeno = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            ninos="1",
            edades_ninos="3 años",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
        )
    )
    encamado = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            funciones=["limpieza", "cocinar", "lavar", "envejeciente"],
            envejeciente_tipo_cuidado="encamado",
        )
    )

    assert normal["score"] > nino_pequeno["score"] >= adultos_4["score"] > encamado["score"]
    assert normal["score"] - adultos_4["score"] >= 5.0
    assert _component_amount(adultos_4, "bonus_solicitud_normal_atractiva") == 7.05
    assert _component_amount(nino_pequeno, "combo_ninos_pequenos") == -2.5
    assert _component_amount(encamado, "combo_envejeciente") == -3.0


def test_74_ninos_sin_cuidado_cuentan_para_ocupacion_sin_penalizacion_de_cuidado():
    base = evaluate_solicitud_atractivo(_dormida_l_s_calibracion_payload(ninos="0"))
    residentes = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            ninos="3",
            edades_ninos="",
            nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
            observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
        )
    )

    assert residentes["score"] < base["score"]
    assert _component_amount(residentes, "ocupacion_total") == -1.0
    assert _component_amount(residentes, "combo_ninos_pequenos") == 0.0


def test_75_ocupacion_total_baja_gradualmente_sin_cuidado_infantil():
    audited = {
        (adultos, ninos): evaluate_solicitud_atractivo(
            _dormida_l_s_calibracion_payload(
                adultos=str(adultos),
                ninos=str(ninos),
                edades_ninos="",
                nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
                observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
            )
        )
        for adultos, ninos in [(2, 0), (2, 2), (2, 3), (4, 0), (4, 2), (4, 3), (5, 3)]
    }

    assert audited[(2, 0)]["score"] > audited[(2, 2)]["score"] > audited[(2, 3)]["score"]
    assert audited[(4, 0)]["score"] > audited[(4, 2)]["score"] > audited[(4, 3)]["score"] > audited[(5, 3)]["score"]
    assert audited[(2, 3)]["score"] > audited[(4, 3)]["score"]
    assert _component_amount(audited[(2, 2)], "ocupacion_total") == -0.5
    assert _component_amount(audited[(2, 3)], "ocupacion_total") == -1.0
    assert _component_amount(audited[(4, 2)], "ocupacion_total") == -1.5
    assert _component_amount(audited[(4, 3)], "ocupacion_total") == -2.0
    assert _component_amount(audited[(5, 3)], "ocupacion_total") == -2.0


def test_76_cuatro_adultos_tres_ninos_sin_cuidado_queda_en_rango_objetivo():
    result = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            adultos="4",
            ninos="3",
            edades_ninos="",
            nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
            observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
        )
    )

    assert 79.0 <= result["score"] <= 79.5
    assert result["componentes"]["score_sin_salario"] == 74.1
    assert _component_amount(result, "adultos_4") == -1.0
    assert _component_amount(result, "combo_adultos_limpieza_lavar") == -1.0
    assert _component_amount(result, "ocupacion_total") == -2.0
    assert _component_amount(result, "combo_ninos_pequenos") == 0.0


def test_77_nino_pequeno_con_cuidado_baja_mucho_mas_que_ocupacion_residente():
    sin_cuidado = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            adultos="4",
            ninos="3",
            edades_ninos="",
            nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
            observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
        )
    )
    con_cuidado = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            adultos="4",
            ninos="3",
            edades_ninos="2 y 7 años",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            nota_cliente="Hay que cuidar activamente al niño de 2 años.",
            observaciones="Hay que cuidar activamente al niño de 2 años.",
        )
    )

    assert con_cuidado["score"] <= sin_cuidado["score"] - 4.0
    assert _component_amount(con_cuidado, "combo_ninos_pequenos") == -2.84
    assert _component_amount(con_cuidado, "ocupacion_total") == -1.5


def test_78_dormida_l_v_tambien_aplica_ocupacion_sin_cambiar_de_forma_absurda():
    sin_ninos = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            modalidad_trabajo="Con dormida 💤 lunes a viernes",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            dormida_salida="viernes 4:00 PM",
            adultos="2",
            ninos="0",
        )
    )
    con_ninos = evaluate_solicitud_atractivo(
        _dormida_l_s_calibracion_payload(
            modalidad_trabajo="Con dormida 💤 lunes a viernes",
            horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            dormida_salida="viernes 4:00 PM",
            adultos="2",
            ninos="3",
            nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
            observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
        )
    )

    assert _pre_salary_cap_score(con_ninos) == _pre_salary_cap_score(sin_ninos) - 1.0
    assert con_ninos["score"] >= 87.0
    assert _component_amount(con_ninos, "ocupacion_total") == -1.0


def _dormida_l_s_casa_grande_transicion_payload(**overrides):
    data = _dormida_l_s_calibracion_payload(
        habitaciones="4",
        banos="4",
        adultos="2",
        ninos="2",
        edades_ninos="",
        nota_cliente="Los niños no requieren cuidado directo; solo viven en la casa.",
        observaciones="Los niños no requieren cuidado directo; solo viven en la casa.",
    )
    data.update(overrides)
    return data


def test_79_dormida_l_s_4h_4b_con_residentes_sin_cuidado_queda_en_rango_objetivo():
    result = evaluate_solicitud_atractivo(_dormida_l_s_casa_grande_transicion_payload())

    assert 84.5 <= result["score"] <= 85.2
    assert result["componentes"]["score_sin_salario"] == 79.1
    assert _component_amount(result, "modalidad_cd_l_s") == -2.5
    assert _component_amount(result, "dormida_ls_salida_favorable") == 3.0
    assert _component_amount(result, "ocupacion_total") == -0.5
    assert _component_amount(result, "hogar_carga_fisica") == -1.4
    assert _component_amount(result, "bonus_solicitud_normal_atractiva") == 9.55
    assert _component_amount(result, "bonus_pasaje") == 2.0
    assert _component_amount(result, "salario") == 5.75


def test_80_dormida_l_s_cadena_3h3b_a_5h5b_es_monotona_y_sin_salto_grande():
    chain = [(3, 3), (4, 3), (4, 4), (5, 4), (5, 5)]
    audited = {
        size: evaluate_solicitud_atractivo(
            _dormida_l_s_casa_grande_transicion_payload(
                habitaciones=str(size[0]),
                banos=str(size[1]),
            )
        )
        for size in chain
    }
    scores = [audited[size]["score"] for size in chain]
    drops = [round(scores[idx] - scores[idx + 1], 1) for idx in range(len(scores) - 1)]

    assert scores == [86.2, 84.9, 84.9, 83.4, 82.1]
    assert scores == sorted(scores, reverse=True)
    assert round(audited[(3, 3)]["score"] - audited[(4, 4)]["score"], 1) == 1.3
    assert all(drop <= 4.5 for drop in drops)
    assert audited[(5, 4)]["score"] >= audited[(5, 5)]["score"]


def test_80b_cocinar_y_lavar_no_aumentan_atractivo_con_mismo_payload():
    base = _dormida_l_s_calibracion_payload(
        habitaciones="4",
        banos="4",
        adultos="2",
        ninos="2",
        edades_ninos="2 y 7 años",
        ayuda_cuidado_ninos="con_ayuda",
        sueldo="21000",
    )
    matrix = {
        "limpieza": ["limpieza"],
        "limpieza_cocinar": ["limpieza", "cocinar"],
        "limpieza_lavar": ["limpieza", "lavar"],
        "limpieza_cocinar_lavar": ["limpieza", "cocinar", "lavar"],
        "limpieza_ninos": ["limpieza", "ninos"],
        "limpieza_cocinar_ninos": ["limpieza", "cocinar", "ninos"],
        "limpieza_lavar_ninos": ["limpieza", "lavar", "ninos"],
        "limpieza_cocinar_lavar_ninos": ["limpieza", "cocinar", "lavar", "ninos"],
    }

    def payload_for(funciones):
        data = dict(base)
        data["funciones"] = funciones
        if "ninos" not in funciones:
            data["ninos"] = "0"
            data["edades_ninos"] = ""
            data["ayuda_cuidado_ninos"] = ""
        return data

    audited = {name: evaluate_solicitud_atractivo(payload_for(funciones)) for name, funciones in matrix.items()}
    contexts = {
        name: atractivo_service.SolicitudAtractivoService._build_context(payload_for(funciones))
        for name, funciones in matrix.items()
    }

    raw = {name: _pre_salary_cap_score(result) for name, result in audited.items()}
    final = {name: result["score"] for name, result in audited.items()}
    domestic_core_load = {
        name: 0.5 * max(0, len(set(funciones).intersection({"limpieza", "cocinar", "lavar"})) - 1)
        for name, funciones in matrix.items()
    }

    assert raw["limpieza"] >= raw["limpieza_cocinar"]
    assert raw["limpieza"] >= raw["limpieza_lavar"]
    assert raw["limpieza_cocinar"] >= raw["limpieza_cocinar_lavar"]
    assert raw["limpieza_lavar"] >= raw["limpieza_cocinar_lavar"]
    assert final["limpieza"] >= final["limpieza_cocinar"]
    assert final["limpieza"] >= final["limpieza_lavar"]
    assert final["limpieza_cocinar"] >= final["limpieza_cocinar_lavar"]
    assert final["limpieza_lavar"] >= final["limpieza_cocinar_lavar"]

    assert raw["limpieza_ninos"] >= raw["limpieza_cocinar_ninos"]
    assert raw["limpieza_ninos"] >= raw["limpieza_lavar_ninos"]
    assert raw["limpieza_cocinar_ninos"] >= raw["limpieza_cocinar_lavar_ninos"]
    assert raw["limpieza_lavar_ninos"] >= raw["limpieza_cocinar_lavar_ninos"]
    assert final["limpieza_ninos"] >= final["limpieza_cocinar_ninos"]
    assert final["limpieza_ninos"] >= final["limpieza_lavar_ninos"]
    assert final["limpieza_cocinar_ninos"] >= final["limpieza_cocinar_lavar_ninos"]
    assert final["limpieza_lavar_ninos"] >= final["limpieza_cocinar_lavar_ninos"]

    assert domestic_core_load["limpieza"] < domestic_core_load["limpieza_cocinar"] < domestic_core_load["limpieza_cocinar_lavar"]
    assert domestic_core_load["limpieza_ninos"] < domestic_core_load["limpieza_cocinar_ninos"] < domestic_core_load["limpieza_cocinar_lavar_ninos"]
    assert contexts["limpieza_ninos"].child_care_load == contexts["limpieza_cocinar_ninos"].child_care_load
    assert contexts["limpieza_ninos"].child_care_load == contexts["limpieza_lavar_ninos"].child_care_load
    assert contexts["limpieza_ninos"].child_care_load == contexts["limpieza_cocinar_lavar_ninos"].child_care_load

    assert _component_amount(audited["limpieza_cocinar_ninos"], "salario") <= _component_amount(audited["limpieza_ninos"], "salario")
    assert _component_amount(audited["limpieza_lavar_ninos"], "salario") <= _component_amount(audited["limpieza_ninos"], "salario")
    assert _component_amount(audited["limpieza_cocinar_lavar_ninos"], "salario") <= _component_amount(audited["limpieza_cocinar_ninos"], "salario")
    assert _component_amount(audited["limpieza_cocinar_ninos"], "bonus_solicitud_normal_atractiva") <= _component_amount(audited["limpieza_ninos"], "bonus_solicitud_normal_atractiva")
    assert _component_amount(audited["limpieza_lavar_ninos"], "bonus_solicitud_normal_atractiva") <= _component_amount(audited["limpieza_ninos"], "bonus_solicitud_normal_atractiva")
    assert _component_amount(audited["limpieza_cocinar_lavar_ninos"], "bonus_solicitud_normal_atractiva") <= _component_amount(audited["limpieza_cocinar_ninos"], "bonus_solicitud_normal_atractiva")


def test_80c_domesticas_normales_l_v_payload_real_bajan_suave_con_ninos_y_sin_ninos():
    base = _dormida_l_s_calibracion_payload(
        modalidad_trabajo="Con dormida 💤 lunes a viernes",
        horario="Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
        dormida_salida="viernes 4:00 PM",
        tipo_lugar="apto",
        habitaciones="2",
        banos="2",
        adultos="2",
        ninos="2",
        edades_ninos="2 y 3 años",
        ayuda_cuidado_ninos="sin_ayuda",
        sueldo="20000",
    )
    matrix = {
        "limpieza": ["limpieza"],
        "limpieza_cocinar": ["limpieza", "cocinar"],
        "limpieza_lavar": ["limpieza", "lavar"],
        "limpieza_cocinar_lavar": ["limpieza", "cocinar", "lavar"],
        "limpieza_ninos": ["limpieza", "ninos"],
        "limpieza_cocinar_ninos": ["limpieza", "cocinar", "ninos"],
        "limpieza_lavar_ninos": ["limpieza", "lavar", "ninos"],
        "limpieza_cocinar_lavar_ninos": ["limpieza", "cocinar", "lavar", "ninos"],
    }

    def payload_for(funciones):
        data = dict(base)
        data["funciones"] = funciones
        if "ninos" not in funciones:
            data["ninos"] = "0"
            data["edades_ninos"] = ""
            data["ayuda_cuidado_ninos"] = ""
        return data

    audited = {name: evaluate_solicitud_atractivo(payload_for(funciones)) for name, funciones in matrix.items()}
    contexts = {
        name: atractivo_service.SolicitudAtractivoService._build_context(payload_for(funciones))
        for name, funciones in matrix.items()
    }
    raw = {name: _pre_salary_cap_score(result) for name, result in audited.items()}
    final = {name: result["score"] for name, result in audited.items()}

    assert raw["limpieza"] >= raw["limpieza_cocinar"] >= raw["limpieza_cocinar_lavar"]
    assert raw["limpieza"] >= raw["limpieza_lavar"] >= raw["limpieza_cocinar_lavar"]
    assert raw["limpieza"] - raw["limpieza_cocinar_lavar"] == 0.0
    assert final["limpieza"] - final["limpieza_cocinar_lavar"] == 0.0

    assert raw["limpieza_ninos"] >= raw["limpieza_cocinar_ninos"] >= raw["limpieza_cocinar_lavar_ninos"]
    assert raw["limpieza_ninos"] >= raw["limpieza_lavar_ninos"] >= raw["limpieza_cocinar_lavar_ninos"]
    assert round(raw["limpieza_ninos"] - raw["limpieza_cocinar_ninos"], 1) == 0.5
    assert round(raw["limpieza_ninos"] - raw["limpieza_lavar_ninos"], 1) == 0.5
    assert round(raw["limpieza_ninos"] - raw["limpieza_cocinar_lavar_ninos"], 1) == 1.0
    assert round(final["limpieza_ninos"] - final["limpieza_cocinar_lavar_ninos"], 1) == 1.0
    assert 84.0 <= final["limpieza_cocinar_lavar_ninos"] <= 85.0

    assert _component_amount(audited["limpieza_ninos"], "salario") == _component_amount(audited["limpieza_cocinar_lavar_ninos"], "salario")
    assert _component_amount(audited["limpieza_ninos"], "bonus_solicitud_normal_atractiva") == _component_amount(audited["limpieza_cocinar_lavar_ninos"], "bonus_solicitud_normal_atractiva")
    assert contexts["limpieza_ninos"].child_care_load == contexts["limpieza_cocinar_lavar_ninos"].child_care_load
    assert contexts["limpieza_ninos"].small_children == contexts["limpieza_cocinar_lavar_ninos"].small_children == 2
    assert contexts["limpieza_ninos"].supervision_count == contexts["limpieza_cocinar_lavar_ninos"].supervision_count == 0
    assert _component_amount(audited["limpieza_cocinar_ninos"], "combo_ninos_pequenos") - _component_amount(audited["limpieza_ninos"], "combo_ninos_pequenos") == -0.5
    assert _component_amount(audited["limpieza_cocinar_lavar_ninos"], "combo_ninos_pequenos") - _component_amount(audited["limpieza_ninos"], "combo_ninos_pequenos") == -1.0


def _nanny_focused_payload(**overrides):
    data = _payload(
        horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
        horario_hora_salida="5:00 PM",
        tipo_lugar="casa",
        habitaciones="3",
        banos="2",
        pisos="1",
        adultos="2",
        ninos="2",
        edades_ninos="2 y 3 años",
        funciones=["ninos", "cocinar", "lavar"],
        areas_comunes=["sala"],
        sueldo="18000",
        pasaje_mode="aparte",
        detalles_servicio={"pasaje": {"mode": "aparte"}},
        nota_cliente="",
        descripcion="",
        observaciones="",
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
    )
    data.update(overrides)
    return data


def test_81_ninera_con_cocinar_y_lavar_sin_limpieza_es_ninera_enfocada():
    result = evaluate_solicitud_atractivo(_nanny_focused_payload())

    assert 83.0 <= result["score"] <= 87.0
    assert result["score"] == 85.3
    assert result["componentes"]["score_sin_salario"] == 79.3
    assert _component_amount(result, "ocupacion_total") == -0.5
    assert _component_amount(result, "combo_ninos_pequenos") == 0.0
    assert _component_amount(result, "bonus_ninera_pura_atractiva") == 9.0
    assert _component_amount(result, "bonus_pasaje") == 2.0
    assert _component_amount(result, "salario") == 6.0
    assert result["componentes"]["salary_reference"]["reference_min"] == 17000
    assert result["componentes"]["salary_reference"]["reference_max"] == 19000


def test_82_ninera_enfocada_ordena_tareas_compatibles_y_limpieza_dispara_carga_real():
    audited = {
        "solo": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos"])),
        "cocinar": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos", "cocinar"])),
        "lavar": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos", "lavar"])),
        "cocinar_lavar": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos", "cocinar", "lavar"])),
        "limpieza": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos", "limpieza"])),
        "limpieza_cocinar": evaluate_solicitud_atractivo(_nanny_focused_payload(funciones=["ninos", "limpieza", "cocinar"])),
        "limpieza_cocinar_lavar": evaluate_solicitud_atractivo(
            _nanny_focused_payload(funciones=["ninos", "limpieza", "cocinar", "lavar"])
        ),
    }

    assert audited["solo"]["score"] == 86.3
    assert audited["cocinar"]["score"] == 85.8
    assert audited["lavar"]["score"] == 85.8
    assert audited["cocinar_lavar"]["score"] == 85.3
    assert audited["limpieza"]["score"] == 66.4
    assert audited["limpieza_cocinar"]["score"] == 65.2
    assert audited["limpieza_cocinar_lavar"]["score"] == 64.5
    assert audited["solo"]["score"] >= audited["cocinar"]["score"] >= audited["cocinar_lavar"]["score"]
    assert audited["solo"]["score"] >= audited["lavar"]["score"] >= audited["cocinar_lavar"]["score"]
    assert audited["cocinar_lavar"]["score"] > audited["limpieza"]["score"] > audited["limpieza_cocinar_lavar"]["score"]
    assert _component_amount(audited["cocinar_lavar"], "combo_ninos_pequenos") == 0.0
    assert _component_amount(audited["limpieza"], "combo_ninos_pequenos") == -2.88
    assert _component_amount(audited["limpieza_cocinar_lavar"], "combo_ninos_pequenos") == -4.85
    assert _component_amount(audited["cocinar_lavar"], "bonus_ninera_pura_atractiva") == 9.0
    assert _component_amount(audited["limpieza"], "bonus_ninera_pura_atractiva") == 0.0


@pytest.mark.parametrize(
    ("edades", "ninos", "expected_bonus"),
    [
        ("6 meses", "1", 8.0),
        ("2 años", "1", 9.0),
        ("3 años", "1", 10.0),
        ("2 y 3 años", "2", 9.0),
        ("6 años", "1", 11.0),
    ],
)
def test_83_ninera_enfocada_mantiene_sensibilidad_por_edad_sin_combo_fuerte(edades, ninos, expected_bonus):
    result = evaluate_solicitud_atractivo(
        _nanny_focused_payload(
            ninos=ninos,
            edades_ninos=edades,
            funciones=["ninos", "cocinar", "lavar"],
        )
    )

    assert _component_amount(result, "bonus_ninera_pura_atractiva") == expected_bonus
    assert _component_amount(result, "combo_ninos_pequenos") == 0.0
    assert result["score"] >= 85.0


def test_84_dormida_l_s_edad_incompleta_con_ayuda_supera_sin_ayuda_sin_borrar_carga():
    base = _dormida_l_s_calibracion_payload(
        adultos="3",
        ninos="2",
        edades_ninos="2 años",
        funciones=["limpieza", "cocinar", "lavar", "ninos"],
        sueldo="21000",
    )
    sin_ayuda = evaluate_solicitud_atractivo(dict(base, ayuda_cuidado_ninos="sin_ayuda"))
    con_ayuda = evaluate_solicitud_atractivo(dict(base, ayuda_cuidado_ninos="con_ayuda"))
    ctx_sin = atractivo_service.SolicitudAtractivoService._build_context(dict(base, ayuda_cuidado_ninos="sin_ayuda"))
    ctx_con = atractivo_service.SolicitudAtractivoService._build_context(dict(base, ayuda_cuidado_ninos="con_ayuda"))

    assert ctx_sin.child_count == 2
    assert ctx_sin.small_children == 1
    assert ctx_sin.unknown_child_count == 1
    assert ctx_con.effective_child_care_load < ctx_sin.effective_child_care_load
    assert _component_amount(con_ayuda, "ayuda_cuidado_ninos") > 0.0
    assert _component_amount(con_ayuda, "combo_ninos_pequenos") < 0.0
    assert con_ayuda["score"] > sin_ayuda["score"]
    assert 3.0 <= round(con_ayuda["score"] - sin_ayuda["score"], 1) <= 4.0


def test_85_dormida_l_s_ayuda_acerca_a_referencia_sin_ninos_sin_igualarla():
    base = _dormida_l_s_calibracion_payload(
        adultos="3",
        sueldo="21000",
    )

    def result_for(*, ninos, edades, ayuda="", cuidar=True):
        funciones = ["limpieza", "cocinar", "lavar"]
        if cuidar:
            funciones.append("ninos")
        return evaluate_solicitud_atractivo(
            dict(base, ninos=ninos, edades_ninos=edades, ayuda_cuidado_ninos=ayuda, funciones=funciones)
        )

    sin_ninos = result_for(ninos="0", edades="", cuidar=False)
    un_pequeno_con = result_for(ninos="1", edades="2 años", ayuda="con_ayuda")
    un_pequeno_sin = result_for(ninos="1", edades="2 años", ayuda="sin_ayuda")
    mixto_sin_funcion = result_for(ninos="2", edades="2 y 8 años", cuidar=False)
    mixto_con = result_for(ninos="2", edades="2 y 8 años", ayuda="con_ayuda")
    mixto_sin = result_for(ninos="2", edades="2 y 8 años", ayuda="sin_ayuda")
    dos_con = result_for(ninos="2", edades="2 y 3 años", ayuda="con_ayuda")
    dos_sin = result_for(ninos="2", edades="2 y 3 años", ayuda="sin_ayuda")
    mayores_con = result_for(ninos="2", edades="8 y 10 años", ayuda="con_ayuda")
    mayores_sin = result_for(ninos="2", edades="8 y 10 años", ayuda="sin_ayuda")

    assert sin_ninos["score"] >= mixto_sin_funcion["score"] > mixto_con["score"] > mixto_sin["score"]
    assert sin_ninos["score"] > un_pequeno_con["score"] > un_pequeno_sin["score"]
    assert sin_ninos["score"] > un_pequeno_con["score"] > mixto_con["score"] > dos_con["score"] > dos_sin["score"]
    assert mayores_con["score"] == mayores_sin["score"]
    assert 0.5 <= round(sin_ninos["score"] - un_pequeno_con["score"], 1) <= 1.0
    assert 0.7 <= round(sin_ninos["score"] - mixto_con["score"], 1) <= 1.5
    assert 1.0 <= round(sin_ninos["score"] - dos_con["score"], 1) <= 2.2
