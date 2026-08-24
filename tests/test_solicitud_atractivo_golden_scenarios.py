# -*- coding: utf-8 -*-

from services.solicitud_atractivo_service import SolicitudAtractivoService, evaluate_solicitud_atractivo
from utils.sueldo_sugerido import analyze_salary_suggestion


def _payload(mode, **overrides):
    variants = {
        "lv": (
            "Con dormida 💤 lunes a viernes",
            "Entrada: lunes 8:00 AM / Salida: viernes 4:00 PM",
            "lunes 8:00 AM",
            "viernes 4:00 PM",
        ),
        "weekend": (
            "Con dormida 💤 fin de semana",
            "Entrada: viernes 5:00 PM / Salida: lunes 8:00 AM",
            "viernes 5:00 PM",
            "lunes 8:00 AM",
        ),
        "weekend_less_favorable": (
            "Con dormida 💤 fin de semana",
            "Entrada: sábado 12:00 PM / Salida: lunes 2:00 PM",
            "sábado 12:00 PM",
            "lunes 2:00 PM",
        ),
        "ls": (
            "Con dormida 💤 lunes a sábado",
            "Entrada: lunes 8:00 AM / Salida: sábado 1:00 PM",
            "lunes 8:00 AM",
            "sábado 1:00 PM",
        ),
        "quincenal": (
            "Con dormida 💤 quincenal",
            "Entrada: lunes 8:00 AM / Salida: segundo viernes 12:00 PM",
            "lunes 8:00 AM",
            "segundo viernes 12:00 PM",
        ),
        "sd_lv": (
            "Salida diaria - lunes a viernes",
            "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_1_day": (
            "Salida diaria - 1 día a la semana",
            "Lunes, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_2_days": (
            "Salida diaria - 2 días a la semana",
            "Lunes y jueves, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_3_days": (
            "Salida diaria - 3 días a la semana",
            "Lunes, miércoles y viernes, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_4_days": (
            "Salida diaria - 4 días a la semana",
            "Lunes a jueves, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_ls": (
            "Salida diaria - lunes a sábado",
            "Lunes a sábado, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_weekend": (
            "Salida diaria - fin de semana",
            "Sábado y domingo, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
        "sd_other": (
            "Salida diaria otro",
            "Martes y jueves, de 8:00 AM a 5:00 PM",
            "",
            "",
        ),
    }
    modalidad, horario, entrada, salida = variants[mode]
    data = {
        "modalidad_trabajo": modalidad,
        "horario": horario,
        "horario_tipo": "salida_diaria" if mode.startswith("sd_") else "con_dormida",
        "dias_trabajo": {
            "sd_1_day": "Lunes",
            "sd_2_days": "Lunes y jueves",
            "sd_3_days": "Lunes, miércoles y viernes",
            "sd_4_days": "Lunes a jueves",
            "sd_lv": "Lunes a viernes",
            "sd_ls": "Lunes a sábado",
            "sd_weekend": "Sábado y domingo",
            "sd_other": "Martes y jueves",
        }.get(mode, ""),
        "horario_hora_entrada": "8:00 AM" if mode.startswith("sd_") else "",
        "horario_hora_salida": "5:00 PM" if mode.startswith("sd_") else "",
        "dormida_entrada": entrada,
        "dormida_salida": salida,
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
    ref = analyze_salary_suggestion(data)
    if "sueldo" not in overrides:
        data["sueldo"] = str(round((ref["suggested_min"] + ref["suggested_max"]) / 2))
    return data


GOLDEN_SCENARIOS = {
    "sd_1_day_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_1_day"),
        "range": (88.0, 90.0),
        "raw": 89.6,
        "components": {"modalidad_sd_1_dia": 2.5, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_1_day_salary_min": {
        "payload": _payload("sd_1_day", sueldo="5000"),
        "range": (88.0, 89.0),
        "raw": 88.6,
        "components": {"modalidad_sd_1_dia": 2.5, "bonus_solicitud_normal_atractiva": 10.5, "salario": 5.0},
    },
    "sd_1_day_salary_mid": {
        "payload": _payload("sd_1_day", sueldo="6000"),
        "range": (89.0, 90.0),
        "raw": 89.6,
        "components": {"modalidad_sd_1_dia": 2.5, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_1_day_salary_max": {
        "payload": _payload("sd_1_day", sueldo="7000"),
        "range": (90.0, 91.0),
        "raw": 90.6,
        "components": {"modalidad_sd_1_dia": 2.5, "bonus_solicitud_normal_atractiva": 10.5, "salario": 7.0},
    },
    "sd_1_day_salary_far_above_max": {
        "payload": _payload("sd_1_day", sueldo="20000"),
        "range": (97.0, 98.0),
        "raw": 100.45,
        "components": {"modalidad_sd_1_dia": 2.5, "bonus_solicitud_normal_atractiva": 10.5, "salario": 16.85},
    },
    "sd_2_days_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_2_days"),
        "range": (88.0, 90.0),
        "raw": 89.1,
        "components": {"modalidad_sd_2_dias": 2.0, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_3_days_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_3_days"),
        "range": (87.5, 89.0),
        "raw": 88.1,
        "components": {"modalidad_sd_3_dias": 1.0, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_4_days_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_4_days"),
        "range": (86.5, 88.0),
        "raw": 87.1,
        "components": {"bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_l_v_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_lv"),
        "range": (86.0, 87.0),
        "raw": 86.6,
        "components": {"bonus_solicitud_normal_atractiva": 10.0, "hogar_carga_fisica": -0.4, "salario": 6.0, "bonus_pasaje": 2.0},
    },
    "sd_l_s_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_ls"),
        "range": (85.0, 86.0),
        "raw": 85.6,
        "components": {"modalidad_sd_l_s": -1.5, "bonus_solicitud_normal_atractiva": 10.5, "hogar_carga_fisica": -0.4, "salario": 6.0, "bonus_pasaje": 2.0},
    },
    "sd_weekend_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_weekend"),
        "range": (87.5, 89.0),
        "raw": 88.1,
        "components": {"modalidad_sd_fin_semana": 1.0, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_other_two_days_normal_house_3h3b_salary_normal": {
        "payload": _payload("sd_other"),
        "range": (88.0, 90.0),
        "raw": 89.1,
        "components": {"modalidad_sd_2_dias": 2.0, "bonus_solicitud_normal_atractiva": 10.5, "salario": 6.0},
    },
    "sd_l_v_house_3h3b_child_2_8_help_salary_normal": {
        "payload": _payload("sd_lv", ninos="2", edades_ninos="2 y 8 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (84.0, 85.0),
        "raw": 84.62,
        "components": {"bonus_solicitud_normal_atractiva": 9.5, "combo_ninos_pequenos": -0.98, "salario": 6.0},
        "context": {"child_care_load": 1.012, "effective_child_care_load": 0.283, "small_children": 1, "supervision_count": 1, "child_care_help_code": "con_ayuda"},
    },
    "sd_l_v_house_3h3b_child_2_8_no_help_salary_normal": {
        "payload": _payload("sd_lv", ninos="2", edades_ninos="2 y 8 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (79.0, 80.5),
        "raw": 79.76,
        "components": {"bonus_solicitud_normal_atractiva": 8.5, "combo_ninos_pequenos": -3.84, "salario": 5.0},
        "context": {"child_care_load": 1.012, "effective_child_care_load": 1.012, "small_children": 1, "supervision_count": 1, "child_care_help_code": "sin_ayuda"},
    },
    "sd_l_s_house_3h3b_child_2_8_help_salary_normal": {
        "payload": _payload("sd_ls", ninos="2", edades_ninos="2 y 8 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (83.0, 84.0),
        "raw": 83.62,
        "components": {"modalidad_sd_l_s": -1.5, "bonus_solicitud_normal_atractiva": 10.0, "combo_ninos_pequenos": -0.98, "salario": 6.0},
        "context": {"child_care_load": 1.012, "effective_child_care_load": 0.283, "small_children": 1, "supervision_count": 1, "child_care_help_code": "con_ayuda"},
    },
    "sd_l_s_house_3h3b_child_2_8_no_help_salary_normal": {
        "payload": _payload("sd_ls", ninos="2", edades_ninos="2 y 8 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (79.0, 80.5),
        "raw": 79.76,
        "components": {"modalidad_sd_l_s": -1.5, "bonus_solicitud_normal_atractiva": 9.0, "combo_ninos_pequenos": -3.84, "salario": 6.0},
        "context": {"child_care_load": 1.012, "effective_child_care_load": 1.012, "small_children": 1, "supervision_count": 1, "child_care_help_code": "sin_ayuda"},
    },
    "lv_base": {
        "payload": _payload("lv"),
        "range": (88.0, 89.0),
        "raw": 90.5,
        "components": {"modalidad_cd_l_v": 1.25, "bonus_solicitud_normal_atractiva": 8.5, "salario": 6.0},
    },
    "lv_children_help": {
        "payload": _payload("lv", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (87.0, 88.5),
        "raw": 87.8,
        "components": {"modalidad_cd_l_v": 1.25},
    },
    "lv_children_no_help": {
        "payload": _payload("lv", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (84.0, 86.0),
        "raw": 85.12,
        "components": {"modalidad_cd_l_v": 1.25},
    },
    "lv_house_4_4": {
        "payload": _payload("lv", habitaciones="4", banos="4"),
        "range": (88.0, 89.0),
        "raw": 88.35,
        "components": {"modalidad_cd_l_v": 1.25},
    },
    "weekend_base": {
        "payload": _payload("weekend"),
        "range": (82.0, 84.0),
        "raw": 83.1,
        "components": {"modalidad_fin_semana": 4.5, "salario": 5.0},
    },
    "weekend_less_favorable": {
        "payload": _payload("weekend_less_favorable"),
        "range": (80.0, 82.0),
        "raw": 81.1,
    },
    "weekend_house_3h3b_child_2_8_help_salary_16000": {
        "payload": _payload(
            "weekend",
            adultos="3",
            ninos="2",
            edades_ninos="2 y 8 años",
            ayuda_cuidado_ninos="con_ayuda",
            funciones=["limpieza", "cocinar", "ninos"],
            sueldo="16000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        ),
        "range": (83.5, 84.5),
        "raw": 83.95,
        "components": {
            "modalidad_fin_semana": 4.5,
            "fin_semana_entrada_viernes_favorable": 1.5,
            "fin_semana_salida_lunes_favorable": 1.5,
            "hogar_carga_fisica": -0.4,
            "ocupacion_total": -1.0,
            "combo_ninos_pequenos": -0.84,
            "salario": 7.69,
            "bonus_pasaje": 2.0,
        },
        "context": {
            "child_care_load": 1.012,
            "effective_child_care_load": 0.283,
            "small_children": 1,
            "supervision_count": 1,
            "child_care_help_code": "con_ayuda",
        },
    },
    "weekend_house_3h3b_two_small_children_help_salary_20000": {
        "payload": _payload(
            "weekend",
            adultos="3",
            ninos="2",
            edades_ninos="2 y 3 años",
            ayuda_cuidado_ninos="con_ayuda",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            sueldo="20000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        ),
        "range": (90.0, 92.0),
        "raw": 91.0,
        "components": {
            "modalidad_fin_semana": 4.5,
            "fin_semana_entrada_viernes_favorable": 1.5,
            "fin_semana_salida_lunes_favorable": 1.5,
            "hogar_carga_fisica": -0.4,
            "ocupacion_total": -1.0,
            "combo_ninos_pequenos": -1.22,
            "salario": 15.12,
            "bonus_pasaje": 2.0,
        },
        "context": {
            "child_care_load": 1.35,
            "effective_child_care_load": 0.472,
            "small_children": 2,
            "supervision_count": 0,
            "child_care_help_code": "con_ayuda",
        },
    },
    "ls_base": {
        "payload": _payload("ls", tipo_lugar="apto", habitaciones="2", banos="2"),
        "range": (87.0, 88.0),
        "raw": 87.45,
        "components": {"bonus_solicitud_normal_atractiva": 9.95, "salario": 6.0},
    },
    "ls_children_help": {
        "payload": _payload("ls", tipo_lugar="apto", habitaciones="2", banos="2", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (86.5, 87.5),
        "raw": 87.12,
    },
    "ls_children_no_help": {
        "payload": _payload("ls", tipo_lugar="apto", habitaciones="2", banos="2", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"]),
        "range": (81.0, 82.0),
        "raw": 81.65,
    },
    "ls_3h3b_children_help": {
        "payload": _payload("ls", habitaciones="3", banos="3", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="21000"),
        "range": (83.5, 84.5),
        "raw": 84.04,
        "components": {"bonus_solicitud_normal_atractiva": 8.5, "combo_ninos_pequenos": -0.88, "ayuda_cuidado_ninos": 2.0, "salario": 3.82},
    },
    "ls_3h3b_children_no_help": {
        "payload": _payload("ls", habitaciones="3", banos="3", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="21000"),
        "range": (78.0, 79.0),
        "raw": 78.55,
        "components": {"bonus_solicitud_normal_atractiva": 8.0, "salario": 3.2},
    },
    "ls_house_3h3b_children_2_8_no_help_salary_21000": {
        "payload": _payload("ls", habitaciones="3", banos="3", adultos="3", ninos="2", edades_ninos="2 y 8 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="21000"),
        "range": (80.2, 80.7),
        "raw": 80.21,
        "components": {
            "modalidad_cd_l_s": -2.5,
            "bonus_solicitud_normal_atractiva": 8.5,
            "combo_ninos_pequenos": -2.84,
            "salario": 4.45,
        },
        "context": {
            "small_children": 1,
            "supervision_count": 1,
            "child_care_load": 1.012,
            "effective_child_care_load": 1.012,
            "child_care_help_code": "sin_ayuda",
        },
    },
    "ls_house_3h3b_children_2_3_with_help_salary_21000": {
        "payload": _payload("ls", habitaciones="3", banos="3", adultos="3", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="21000"),
        "range": (83.0, 84.0),
        "raw": 83.54,
        "components": {
            "bonus_solicitud_normal_atractiva": 8.5,
            "combo_ninos_pequenos": -0.88,
            "ayuda_cuidado_ninos": 2.0,
            "salario": 3.82,
        },
        "context": {
            "small_children": 2,
            "supervision_count": 0,
            "child_care_load": 1.35,
            "effective_child_care_load": 0.472,
            "child_care_help_code": "con_ayuda",
        },
    },
    "ls_4h4b_children_help": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="21000"),
        "range": (81.0, 82.0),
        "raw": 81.62,
        "components": {"combo_ninos_pequenos": -0.88, "ayuda_cuidado_ninos": 1.2},
        "components": {"bonus_solicitud_normal_atractiva": 8.5, "salario": 3.2},
    },
    "ls_no_children_equivalent": {
        "payload": _payload("ls", habitaciones="3", banos="3", sueldo="22000"),
        "range": (86.0, 87.0),
        "raw": 86.65,
        "context": {"small_children": 0, "supervision_count": 0},
        "forbidden_messages": ["reduce un poco el atractivo"],
    },
    "ls_children_8_10": {
        "payload": _payload("ls", habitaciones="3", banos="3", ninos="2", edades_ninos="8 y 10 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="22000"),
        "range": (85.5, 86.5),
        "raw": 86.15,
        "components": {"bonus_solicitud_normal_atractiva": 9.55, "combo_ninos_pequenos": 0.0, "salario": 6.0},
        "context": {"small_children": 0, "supervision_count": 2},
        "forbidden_messages": ["reduce un poco el atractivo", "Niños pequeños"],
    },
    "ls_children_2_10": {
        "payload": _payload("ls", habitaciones="3", banos="3", ninos="2", edades_ninos="2 y 10 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="22000"),
        "range": (81.5, 82.5),
        "raw": 81.99,
        "components": {"bonus_solicitud_normal_atractiva": 8.5, "combo_ninos_pequenos": -2.61, "salario": 5.5},
        "context": {"small_children": 1, "supervision_count": 1},
        "forbidden_messages": ["reduce un poco el atractivo"],
    },
    "ls_children_2_3": {
        "payload": _payload("ls", habitaciones="3", banos="3", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "cocinar", "lavar", "ninos"], sueldo="22000"),
        "range": (79.0, 80.5),
        "raw": 79.75,
        "components": {"bonus_solicitud_normal_atractiva": 8.0, "combo_ninos_pequenos": -3.85, "salario": 5.0},
        "context": {"small_children": 2, "supervision_count": 0},
        "forbidden_messages": ["reduce un poco el atractivo"],
    },
    "ls_house_4_4": {
        "payload": _payload("ls", habitaciones="4", banos="4"),
        "range": (85.0, 86.0),
        "raw": 85.65,
    },
    "ls_house_4h4b_no_children": {
        "payload": _payload("ls", habitaciones="4", banos="4", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (83.0, 84.0),
        "raw": 83.47,
        "components": {
            "bonus_solicitud_normal_atractiva": 9.55,
            "combo_ninos_pequenos": 0.0,
            "salario": 3.82,
        },
        "context": {
            "small_children": 0,
            "supervision_count": 0,
            "child_care_load": 0.0,
            "effective_child_care_load": 0.0,
            "child_care_help_code": "sin_ayuda",
        },
    },
    "ls_house_4h4b_children_8_10": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="8 y 10 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (82.5, 83.5),
        "raw": 82.97,
        "components": {
            "bonus_solicitud_normal_atractiva": 9.55,
            "combo_ninos_pequenos": 0.0,
            "salario": 3.82,
        },
        "context": {
            "small_children": 0,
            "supervision_count": 2,
            "child_care_load": 0.188,
            "effective_child_care_load": 0.188,
            "child_care_help_code": "sin_ayuda",
        },
        "forbidden_messages": ["Niños pequeños"],
    },
    "ls_house_4h4b_children_2_7_with_help": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="2 y 7 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (82.5, 83.2),
        "raw": 82.94,
        "components": {
            "bonus_solicitud_normal_atractiva": 9.0,
            "combo_ninos_pequenos": -0.06,
            "ayuda_cuidado_ninos": 1.2,
            "salario": 3.2,
        },
        "context": {
            "small_children": 1,
            "supervision_count": 1,
            "child_care_load": 1.012,
            "effective_child_care_load": 0.283,
            "child_care_help_code": "con_ayuda",
        },
    },
    "ls_house_4h4b_children_2_7_no_help": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="2 y 7 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (81.5, 82.5),
        "raw": 81.97,
        "components": {
            "bonus_solicitud_normal_atractiva": 8.5,
            "combo_ninos_pequenos": -0.23,
            "ayuda_cuidado_ninos": 0.0,
            "salario": 4.59,
        },
        "context": {
            "small_children": 1,
            "supervision_count": 1,
            "child_care_load": 1.012,
            "effective_child_care_load": 1.012,
            "child_care_help_code": "sin_ayuda",
        },
    },
    "ls_house_4h4b_children_2_3_with_help": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (81.8, 82.5),
        "raw": 82.15,
        "components": {
            "bonus_solicitud_normal_atractiva": 8.5,
            "combo_ninos_pequenos": -0.35,
            "ayuda_cuidado_ninos": 1.2,
            "salario": 3.2,
        },
        "context": {
            "small_children": 2,
            "supervision_count": 0,
            "child_care_load": 1.35,
            "effective_child_care_load": 0.472,
            "child_care_help_code": "con_ayuda",
        },
    },
    "ls_house_4h4b_children_2_3_no_help": {
        "payload": _payload("ls", habitaciones="4", banos="4", ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda", funciones=["limpieza", "ninos"], sueldo="21000"),
        "range": (78.5, 79.5),
        "raw": 79.37,
        "components": {
            "bonus_solicitud_normal_atractiva": 8.0,
            "combo_ninos_pequenos": -1.88,
            "ayuda_cuidado_ninos": 0.0,
            "salario": 3.65,
        },
        "context": {
            "small_children": 2,
            "supervision_count": 0,
            "child_care_load": 1.35,
            "effective_child_care_load": 1.35,
            "child_care_help_code": "sin_ayuda",
        },
    },
    "ls_4_adults": {
        "payload": _payload("ls", tipo_lugar="apto", habitaciones="2", banos="2", adultos="4"),
        "range": (82.5, 83.5),
        "raw": 82.95,
    },
    "ls_encamado": {
        "payload": _payload("ls", tipo_lugar="apto", habitaciones="2", banos="2", funciones=["limpieza", "cocinar", "lavar", "envejeciente"], envejeciente_tipo_cuidado="encamado"),
        "range": (77.5, 78.5),
        "raw": 78.0,
    },
    "quincenal_base": {
        "payload": _payload("quincenal"),
        "range": (83.0, 85.0),
        "raw": 84.1,
        "components": {"modalidad_cd_quincenal": -1.5, "quincenal_salary_band_cap_value": 85.0, "salario": 6.0},
    },
    "quincenal_apartment": {
        "payload": _payload("quincenal", tipo_lugar="apto", habitaciones="2", banos="2"),
        "range": (85.0, 85.0),
        "raw": 88.0,
    },
    "quincenal_children_help": {
        "payload": _payload("quincenal", funciones=["limpieza", "ninos"], ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="con_ayuda"),
        "range": (81.0, 83.0),
        "raw": 82.25,
    },
    "quincenal_children_no_help": {
        "payload": _payload("quincenal", funciones=["limpieza", "ninos"], ninos="2", edades_ninos="2 y 3 años", ayuda_cuidado_ninos="sin_ayuda"),
        "range": (80.0, 82.0),
        "raw": 81.45,
    },
    "quincenal_house_4_4": {
        "payload": _payload("quincenal", habitaciones="4", banos="4"),
        "range": (81.0, 83.0),
        "raw": 82.1,
    },
    "quincenal_encamado": {
        "payload": _payload("quincenal", funciones=["limpieza", "cocinar", "lavar", "envejeciente"], envejeciente_tipo_cuidado="encamado"),
        "range": (78.0, 80.0),
        "raw": 79.1,
    },
    "quincenal_critical": {
        "payload": _payload("quincenal", habitaciones="5", banos="5", adultos="4", funciones=["limpieza", "cocinar", "lavar", "ninos"], ninos="1", edades_ninos="2 años", ayuda_cuidado_ninos="sin_ayuda"),
        "range": (73.0, 74.0),
        "raw": 73.4,
        "components": {"combo_quincenal_carga_fuerte": 0.0},
    },
    "quincenal_house_4h4b_child_2_8_help_salary_25000": {
        "payload": _payload(
            "quincenal",
            horario="Entrada: lunes 7:30 AM / Salida: viernes 1:00 PM",
            dormida_entrada="lunes 7:30 AM",
            dormida_salida="viernes 1:00 PM",
            habitaciones="4",
            banos="4",
            adultos="3",
            ninos="2",
            edades_ninos="2 y 8 años",
            ayuda_cuidado_ninos="con_ayuda",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            sueldo="25000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        ),
        "range": (77.5, 78.5),
        "raw": 78.54,
        "components": {
            "modalidad_cd_quincenal": -1.5,
            "dormida_quincenal_salida_1pm": 0.5,
            "hogar_carga_fisica": -1.4,
            "ocupacion_total": -1.0,
            "combo_ninos_pequenos": -0.56,
            "salario": 5.5,
            "bonus_solicitud_normal_atractiva": 6.0,
            "bonus_pasaje": 2.0,
            "quincenal_salary_band_cap_value": 85.0,
        },
        "context": {
            "child_care_load": 1.012,
            "effective_child_care_load": 0.283,
            "small_children": 1,
            "supervision_count": 1,
            "child_care_help_code": "con_ayuda",
        },
    },
    "quincenal_house_4h4b_child_2_8_no_help_salary_25000": {
        "payload": _payload(
            "quincenal",
            horario="Entrada: lunes 7:30 AM / Salida: viernes 1:00 PM",
            dormida_entrada="lunes 7:30 AM",
            dormida_salida="viernes 1:00 PM",
            habitaciones="4",
            banos="4",
            adultos="3",
            ninos="2",
            edades_ninos="2 y 8 años",
            ayuda_cuidado_ninos="sin_ayuda",
            funciones=["limpieza", "cocinar", "lavar", "ninos"],
            sueldo="25000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        ),
        "range": (75.5, 76.5),
        "raw": 75.56,
        "components": {
            "modalidad_cd_quincenal": -1.5,
            "dormida_quincenal_salida_1pm": 0.5,
            "hogar_carga_fisica": -1.4,
            "ocupacion_total": -1.0,
            "combo_ninos_pequenos": -1.05,
            "salario": 4.01,
            "bonus_solicitud_normal_atractiva": 5.0,
            "bonus_pasaje": 2.0,
            "quincenal_salary_band_cap_value": 85.0,
        },
        "context": {
            "child_care_load": 1.012,
            "effective_child_care_load": 1.012,
            "small_children": 1,
            "supervision_count": 1,
            "child_care_help_code": "sin_ayuda",
        },
    },
    "quincenal_normal_house_3h3b_salary_base": {
        "payload": _payload(
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
            sueldo="24000",
            pasaje_mode="aparte",
            detalles_servicio={"pasaje": {"mode": "aparte"}},
        ),
        "range": (78.0, 81.0),
        "raw": 79.51,
        "components": {
            "modalidad_cd_quincenal": -1.5,
            "dormida_quincenal_salida_1pm": 0.5,
            "hogar_carga_fisica": -0.4,
            "ocupacion_total": -1.0,
            "combo_ninos_pequenos": -0.56,
            "salario": 3.97,
            "bonus_solicitud_normal_atractiva": 7.5,
            "bonus_pasaje": 2.0,
            "quincenal_salary_band_cap_value": 85.0,
        },
    },
}


def _raw_score(result):
    return float(result["componentes"].get("score_before_quincenal_salary_band_cap", result["componentes"]["score_before_salary_excellence_cap"]))


def _component(result, key):
    if key == "quincenal_salary_band_cap_value":
        return result["componentes"].get(key)
    return next((float(item["amount"]) for item in result["componentes"]["items"] if item["key"] == key), 0.0)


def _audit_table(results):
    rows = ["Escenario | Raw ref | Raw ahora | Delta | Final | Estado"]
    for name, expected in GOLDEN_SCENARIOS.items():
        result = results[name]
        raw_now = _raw_score(result)
        delta = round(raw_now - expected["raw"], 2)
        low, high = expected["range"]
        status = "OK" if abs(delta) <= 1.0 and low <= result["score"] <= high else "regresión"
        rows.append(f"{name} | {expected['raw']} | {raw_now} | {delta} | {result['score']} | {status}")
    return "\n".join(rows)


def _messages(result):
    return " ".join(
        [str(item.get("label") or "") for item in result["componentes"].get("items", [])]
        + [str(item.get("label") or "") for item in result.get("motivos", [])]
    )


def test_golden_scenarios_scores_raw_y_componentes_estables():
    results = {name: evaluate_solicitud_atractivo(spec["payload"]) for name, spec in GOLDEN_SCENARIOS.items()}
    failures = []
    for name, spec in GOLDEN_SCENARIOS.items():
        result = results[name]
        raw_now = _raw_score(result)
        low, high = spec["range"]
        if abs(raw_now - spec["raw"]) > 1.0:
            failures.append(f"{name}: raw {raw_now} movió más de 1.0 desde {spec['raw']}")
        if not (low <= result["score"] <= high):
            failures.append(f"{name}: final {result['score']} fuera de rango {low}-{high}")
        for key, expected_amount in spec.get("components", {}).items():
            actual = _component(result, key)
            if actual != expected_amount:
                failures.append(f"{name}: componente {key}={actual}, esperado {expected_amount}")
        if spec.get("context"):
            ctx = SolicitudAtractivoService._build_context(spec["payload"])
            for attr, expected_value in spec["context"].items():
                actual_value = getattr(ctx, attr)
                if actual_value != expected_value:
                    failures.append(f"{name}: contexto {attr}={actual_value}, esperado {expected_value}")
        messages = _messages(result)
        for forbidden in spec.get("forbidden_messages", []):
            if forbidden in messages:
                failures.append(f"{name}: mensaje prohibido presente: {forbidden}")

    assert not failures, "\n".join(failures + ["", _audit_table(results)])


def test_golden_scenarios_jerarquias_e_inversiones_sin_caps():
    results = {name: evaluate_solicitud_atractivo(spec["payload"]) for name, spec in GOLDEN_SCENARIOS.items()}
    raw = {name: _raw_score(result) for name, result in results.items()}

    assert raw["lv_base"] >= raw["ls_base"]
    assert results["ls_base"]["score"] > results["quincenal_base"]["score"]
    assert 81.0 <= raw["weekend_base"] <= 86.0
    assert raw["weekend_base"] > raw["weekend_less_favorable"]
    assert raw["weekend_house_3h3b_child_2_8_help_salary_16000"] > raw["weekend_base"]
    assert raw["weekend_house_3h3b_two_small_children_help_salary_20000"] > raw["weekend_base"]
    assert raw["weekend_house_3h3b_two_small_children_help_salary_20000"] < 98.0
    assert raw["ls_base"] > raw["ls_children_help"] > raw["ls_children_no_help"]
    assert raw["ls_no_children_equivalent"] >= raw["ls_children_8_10"] > raw["ls_children_2_10"] > raw["ls_children_2_3"]
    assert raw["ls_house_3h3b_children_2_3_with_help_salary_21000"] > raw["ls_children_2_10"] >= raw["ls_house_3h3b_children_2_8_no_help_salary_21000"]
    assert raw["ls_house_3h3b_children_2_3_with_help_salary_21000"] > raw["ls_house_3h3b_children_2_8_no_help_salary_21000"] > raw["ls_3h3b_children_no_help"]
    assert raw["ls_no_children_equivalent"] - raw["ls_children_8_10"] <= 1.5
    assert raw["ls_house_4h4b_no_children"] >= raw["ls_house_4h4b_children_8_10"]
    assert raw["ls_house_4h4b_children_8_10"] > raw["ls_house_4h4b_children_2_7_with_help"]
    assert raw["ls_house_4h4b_children_2_7_with_help"] > raw["ls_house_4h4b_children_2_7_no_help"]
    assert raw["ls_house_4h4b_children_2_7_with_help"] > raw["ls_house_4h4b_children_2_3_with_help"]
    assert raw["ls_house_4h4b_children_2_7_no_help"] > raw["ls_house_4h4b_children_2_3_no_help"]
    assert raw["ls_house_4h4b_children_2_3_with_help"] > raw["ls_house_4h4b_children_2_3_no_help"]
    assert raw["ls_base"] > raw["ls_house_4_4"]
    assert raw["ls_base"] > raw["ls_4_adults"]
    assert raw["ls_base"] > raw["ls_encamado"]
    assert raw["quincenal_apartment"] >= raw["quincenal_base"] > raw["quincenal_house_4_4"]
    assert raw["quincenal_children_help"] > raw["quincenal_children_no_help"]
    assert raw["quincenal_base"] > raw["quincenal_encamado"] > raw["quincenal_critical"]
    assert 77.5 <= raw["quincenal_house_4h4b_child_2_8_help_salary_25000"] <= 78.6
    assert 75.5 <= raw["quincenal_house_4h4b_child_2_8_no_help_salary_25000"] <= 76.5
    assert raw["quincenal_house_4h4b_child_2_8_help_salary_25000"] > raw["quincenal_house_4h4b_child_2_8_no_help_salary_25000"]
    assert 78.0 <= raw["quincenal_normal_house_3h3b_salary_base"] <= 81.0


def test_mensajes_de_modalidad_no_exponen_penalizacion_interna_salvo_quincenal():
    results = {
        mode: evaluate_solicitud_atractivo(_payload(mode))
        for mode in ("lv", "ls", "weekend", "quincenal")
    }

    assert "reduce el atractivo" not in _messages(results["lv"])
    assert "reduce un poco el atractivo" not in _messages(results["ls"])
    assert "reduce el atractivo" not in _messages(results["weekend"])
    assert "La salida quincenal reduce el atractivo." in _messages(results["quincenal"])
