from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from utils.child_age_parser import child_care_help_factor, parse_child_age_summary
from utils.sueldo_sugerido import analyze_salary_suggestion, classify_schedule, common_areas_load, parse_salary_amount


BASE_SCORE = 69
ATTRACTIVE_VERSION = "v8"

LABEL_MUY_ATRACTIVA = "Muy atractiva"
LABEL_ATRACTIVA = "Atractiva"
LABEL_REGULAR = "Regular"
LABEL_POCO = "Poco atractiva"
LABEL_DIFICIL = "Difícil"

HOUSEHOLD_FUNCTIONS = {"limpieza", "cocinar", "lavar", "planchar"}
ELDER_HEAVY_RESPONSIBILITIES = {"higiene", "pampers", "movilidad", "medicamentos"}
LOW_CHILD_LOAD_HINTS = (
    "supervision",
    "solo supervision",
    "solo pendiente",
    "estar pendiente",
    "solo estar pendiente",
    "ya estudian",
    "estudian casi todo el dia",
    "estudian casi todo el día",
    "independientes",
    "independiente",
    "van al colegio",
    "van a la escuela",
    "no necesitan muchos cuidados",
    "ya son grandes",
    "solo acompanamiento",
    "solo acompañamiento",
    "no requieren cuidados especiales",
    "no requiere cuidados especiales",
)
HIGH_CHILD_LOAD_HINTS = (
    "bebe",
    "bebé",
    "recien nacido",
    "recién nacido",
    "meses",
    "panales",
    "pañales",
    "biberon",
    "biberón",
    "estimulacion",
    "estimulación",
    "cargar",
    "dormir",
    "gatea",
    "preescolar",
)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_text(value: Any) -> str:
    txt = _norm(value)
    if not txt:
        return ""
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    raw = str(value).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return int(parsed)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except Exception:
        return default


def _round_score(value: float, digits: int = 1) -> float:
    scale = 10 ** int(digits)
    return math.floor((float(value) * scale) + 0.5) / scale


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, _round_score(value, 1)))


def _score_label(score: float) -> str:
    if score >= 90:
        return LABEL_MUY_ATRACTIVA
    if score >= 70:
        return LABEL_ATRACTIVA
    if score >= 60:
        return LABEL_REGULAR
    return LABEL_POCO


def _score_label_for_critical_cap(score: float) -> str:
    if score >= 85:
        return LABEL_MUY_ATRACTIVA
    if score >= 70:
        return LABEL_ATRACTIVA
    if score >= 55:
        return LABEL_REGULAR
    if score >= 35:
        return LABEL_POCO
    return LABEL_DIFICIL


def _label_rank(label: str) -> int:
    ordered = [
        LABEL_DIFICIL,
        LABEL_POCO,
        LABEL_REGULAR,
        LABEL_ATRACTIVA,
        LABEL_MUY_ATRACTIVA,
    ]
    try:
        return ordered.index(label)
    except ValueError:
        return 0


def _score_cap_for_next_label(label: str) -> int:
    if label == LABEL_DIFICIL:
        return 54
    if label == LABEL_POCO:
        return 69
    if label == LABEL_REGULAR:
        return 84
    if label == LABEL_ATRACTIVA:
        return 100
    return 100


def apply_salary_excellence_cap(
    raw_score: float,
    offered_salary: Any,
    suggested_min: Any,
    suggested_max: Any,
    *,
    has_reliable_suggestion: bool = True,
    mode_key: str = "",
) -> dict[str, Any]:
    offered = parse_salary_amount(offered_salary)
    ref_min = parse_salary_amount(suggested_min)
    ref_max = parse_salary_amount(suggested_max)
    cap_value: float | None = None
    ratio: float | None = None

    if not offered or not has_reliable_suggestion or not ref_min or not ref_max:
        cap_value = 89.0
    else:
        if ref_max < ref_min:
            ref_max = ref_min
        if _is_low_frequency_salida_diaria_mode(mode_key):
            if offered < ref_min:
                cap_value = None
            elif offered <= ref_max:
                cap_value = None
            else:
                ratio = (offered - ref_max) / ref_max if ref_max else None
                if ratio is not None:
                    if ratio <= 0.10:
                        cap_value = 92.0
                    elif ratio <= 0.30:
                        cap_value = 94.0
                    elif ratio <= 0.50:
                        cap_value = 96.0
                    else:
                        cap_value = 97.5
        elif offered <= ref_max:
            cap_value = 89.0
            if offered > ref_max:
                ratio = (offered - ref_max) / ref_max
        else:
            ratio = (offered - ref_max) / ref_max if ref_max else None
            if ((offered - ref_max) * 100) < (ref_max * 5):
                cap_value = 89.5

    capped_score = min(float(raw_score), cap_value) if cap_value is not None else float(raw_score)
    return {
        "score": capped_score,
        "applied": cap_value is not None and float(raw_score) > cap_value,
        "cap_value": cap_value,
        "salary_over_max_ratio": round(float(ratio), 4) if ratio is not None else None,
    }


def apply_quincenal_salary_band_cap(
    raw_score: float,
    ctx: "_Context",
    salary_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    if ctx.mode_key != "dormida_quincenal":
        return {"score": float(raw_score), "applied": False, "cap_value": None}
    salary_reference = salary_reference or {}
    offered = parse_salary_amount(salary_reference.get("offered"))
    ref_max = parse_salary_amount(salary_reference.get("reference_max"))
    if not offered or not ref_max:
        return {"score": float(raw_score), "applied": False, "cap_value": None}

    cap_value: float | None = None
    if offered <= ref_max:
        cap_value = 85.0
    elif offered <= (ref_max * 1.05):
        cap_value = 86.5
    elif offered <= (ref_max * 1.10):
        cap_value = 87.5
    elif offered <= (ref_max * 1.15):
        cap_value = 88.5
    elif offered <= (ref_max * 1.25):
        cap_value = 89.5
    elif offered <= (ref_max * 1.40):
        cap_value = 91.0

    capped_score = min(float(raw_score), cap_value) if cap_value is not None else float(raw_score)
    return {
        "score": capped_score,
        "applied": cap_value is not None and float(raw_score) > cap_value,
        "cap_value": cap_value,
    }


def _household_penalty_amount(habitaciones: int, banos: float) -> float:
    hab = max(0, int(habitaciones or 0))
    baths = max(0.0, float(banos or 0.0))
    if hab >= 6 or baths >= 6:
        return 7.0
    if hab >= 5 or baths >= 5:
        return 4.0
    if hab >= 4 and baths >= 4:
        return 2.0
    if (hab >= 4 and baths >= 3) or (hab >= 3 and baths >= 4):
        return 1.0
    return 0.0


def _round_to_half(value: float) -> float:
    return math.floor((float(value) * 2.0) + 0.5) / 2.0


def _physical_home_load(habitaciones: int, banos: float) -> float:
    hab = max(0, int(habitaciones or 0))
    baths = max(0.0, float(banos or 0.0))

    def dimension_load(value: float) -> float:
        if value <= 2.0:
            return 0.0
        if value <= 3.0:
            return (value - 2.0) * 0.2
        if value <= 4.0:
            return 0.2 + ((value - 3.0) * 0.5)
        if value <= 5.0:
            return 0.7 + ((value - 4.0) * 0.65)
        return 2.0 + max(0.0, value - 6.0) * 0.7

    return round(dimension_load(float(hab)) + dimension_load(baths), 2)


def _qualifies_compensated_large_household_floor(ctx: "_Context", data: dict[str, Any]) -> bool:
    if _household_penalty_amount(ctx.habitaciones, ctx.banos) < 7.0:
        return False
    offered = parse_salary_amount(data.get("sueldo"))
    if not offered:
        return False
    salary_payload = dict(data or {})
    if ctx.has_child_care_duty and not ctx.has_effective_child_care_load:
        salary_payload["funciones"] = [item for item in _as_list(salary_payload.get("funciones")) if _norm(item) != "ninos"]
        salary_payload["ninos"] = 0
        salary_payload["edades_ninos"] = ""
    salary_ref_payload = analyze_salary_suggestion(salary_payload)
    if not salary_ref_payload.get("can_suggest"):
        return False
    ref_min = int(salary_ref_payload.get("suggested_min") or 0)
    return ref_min > 0 and offered >= ref_min


def _normal_request_bonus_amount(ctx: "_Context", data: dict[str, Any]) -> float:
    if ctx.mode_key in {
        "salida_diaria_1_dia",
        "salida_diaria_2_dias",
        "salida_diaria_3_dias",
        "salida_diaria_4_dias",
        "salida_diaria_fin_semana",
        "salida_diaria_l_s",
    }:
        base_bonus = 11.0
    elif ctx.mode_key == "salida_diaria_l_v":
        base_bonus = 10.5
    elif ctx.mode_key == "dormida_l_v":
        base_bonus = 9.0
    elif ctx.mode_key == "dormida_l_s":
        base_bonus = 9.95
    elif ctx.mode_key == "dormida_quincenal":
        base_bonus = 8.5
    else:
        base_bonus = 13.0
    # Use one progressive physical-load taper so rooms and bathrooms do not
    # reduce the normal bonus through multiple independent cliffs.
    taper = _physical_home_load(ctx.habitaciones, ctx.banos)
    if ctx.mode_key == "dormida_l_s":
        bonus = max(0.0, round(float(base_bonus - taper), 2))
    else:
        bonus = max(0.0, _round_to_half(base_bonus - taper))
    if ctx.mode_key == "dormida_quincenal" and ctx.household_penalty_amount <= 4.0:
        bonus = max(6.5, bonus)
        if ctx.has_large_home:
            quincenal_home_bonus_cap = 6.5 if ctx.household_penalty_amount <= 2.0 else 4.5
            bonus = min(quincenal_home_bonus_cap, bonus)
    if (
        bonus > 0.0
        and _qualifies_compensated_large_household_floor(ctx, data)
    ):
        # Preserve a small residual bonus for still-normal requests so 5->6 does not
        # create a new cliff after removing the 4/4 cutoff.
        bonus = max(5.0, bonus)
    if ctx.has_child_care_duty:
        if ctx.mode_key == "dormida_quincenal" and not ctx.has_effective_child_care_load:
            child_taper = 0.5
        elif ctx.child_count <= 0:
            child_taper = 0.0
        elif ctx.supervision_only:
            child_taper = 0.0
        elif ctx.unknown_child_count > 0 and not ctx.has_effective_child_care_load:
            child_taper = 5.0
        else:
            child_taper = min(3.0, 0.5 + (float(ctx.effective_child_care_load or 0.0) * 0.75))
        if child_taper:
            bonus = max(0.0, _round_to_half(bonus - child_taper))
    if ctx.mode_key == "dormida_l_s" and ctx.adults >= 4:
        bonus = max(0.0, round(float(bonus - 2.5), 2))
    if ctx.mode_key == "dormida_l_s" and ctx.elder_type == "encamado":
        bonus = max(0.0, _round_to_half(bonus - 2.5))
    if ctx.mode_key == "dormida_l_s" and ctx.household_penalty_amount >= 2.0:
        bonus += 1.0
    if ctx.mode_key == "dormida_quincenal" and ctx.elder_type == "encamado":
        bonus = max(0.0, _round_to_half(bonus - 1.0))
    return bonus


def _parse_time_to_minutes(raw: Any) -> int | None:
    txt = str(raw or "").strip().lower()
    if not txt:
        return None
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", txt)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = _norm(match.group(3))
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour >= 24 or minute >= 60:
        return None
    return (hour * 60) + minute


def _parse_weekday(raw: Any) -> str:
    txt = _norm(raw)
    if not txt:
        return ""
    if "segundo viernes" in txt:
        return "segundo_viernes"
    if "lunes" in txt:
        return "lunes"
    if "martes" in txt:
        return "martes"
    if "miercoles" in txt or "miércoles" in txt:
        return "miercoles"
    if "jueves" in txt:
        return "jueves"
    if "viernes" in txt:
        return "viernes"
    if "sabado" in txt or "sábado" in txt:
        return "sabado"
    if "domingo" in txt:
        return "domingo"
    return ""


def _is_lunes_a_viernes_hint(raw: str) -> bool:
    return any(token in raw for token in ("lunes a viernes", "lun-vie", "l-v"))


def _is_lunes_a_sabado_hint(raw: str) -> bool:
    return any(token in raw for token in ("lunes a sabado", "lunes a sábado", "lun-sab", "l-s"))


def _is_fin_de_semana_hint(raw: str) -> bool:
    return any(token in raw for token in ("fin de semana", "sabado y domingo", "sábado y domingo", "viernes a lunes"))


def _salida_diaria_mode_from_schedule_key(schedule_key: str) -> str | None:
    return {
        "sd_1_dia": "salida_diaria_1_dia",
        "sd_2_dias": "salida_diaria_2_dias",
        "sd_3_dias": "salida_diaria_3_dias",
        "sd_4_dias": "salida_diaria_4_dias",
        "sd_l_v": "salida_diaria_l_v",
        "sd_l_s": "salida_diaria_l_s",
        "sd_fin_semana": "salida_diaria_fin_semana",
    }.get(str(schedule_key or ""))


def _is_salida_diaria_mode(mode_key: str) -> bool:
    return str(mode_key or "").startswith("salida_diaria_")


def _is_low_frequency_salida_diaria_mode(mode_key: str) -> bool:
    return mode_key in {
        "salida_diaria_1_dia",
        "salida_diaria_2_dias",
        "salida_diaria_3_dias",
        "salida_diaria_4_dias",
        "salida_diaria_fin_semana",
    }


def _extract_schedule_context(data: dict[str, Any]) -> dict[str, Any]:
    detalles = data.get("detalles_servicio") if isinstance(data.get("detalles_servicio"), dict) else {}
    modalidad = _norm(data.get("modalidad_trabajo"))
    horario = _norm(data.get("horario"))
    horario_tipo = _norm(data.get("horario_tipo") or detalles.get("horario_tipo"))
    dias_trabajo = _norm(data.get("dias_trabajo") or detalles.get("dias_trabajo"))
    dormida_entrada = data.get("dormida_entrada") or detalles.get("dormida_entrada")
    dormida_salida = data.get("dormida_salida") or detalles.get("dormida_salida")
    schedule_key, _ = classify_schedule(data)
    if not schedule_key:
        if "quincenal" in modalidad and "dormida" in modalidad:
            schedule_key = "cd_quincenal"
        elif "quincenal" in modalidad:
            schedule_key = "sd_quincenal"

    hora_entrada = data.get("horario_hora_entrada") or detalles.get("hora_entrada")
    hora_salida = data.get("horario_hora_salida") or detalles.get("hora_salida")
    combined_hint = " ".join(
        part
        for part in (
            modalidad,
            horario,
            dias_trabajo,
            _norm(dormida_entrada),
            _norm(dormida_salida),
        )
        if part
    )
    is_dormida = bool(
        horario_tipo == "con_dormida"
        or "dormida" in modalidad
        or dormida_entrada
        or dormida_salida
        or str(schedule_key or "").startswith("cd_")
    )
    is_salida_diaria = bool(
        horario_tipo == "salida_diaria"
        or "salida diaria" in modalidad
        or bool(dias_trabajo)
        or (hora_entrada and hora_salida)
        or str(schedule_key or "").startswith("sd_")
    )
    start_min = _parse_time_to_minutes(hora_entrada) if is_salida_diaria and not is_dormida else None
    end_min = _parse_time_to_minutes(hora_salida) if is_salida_diaria and not is_dormida else None
    hours = None
    if start_min is not None and end_min is not None:
        if end_min <= start_min:
            end_min += 24 * 60
        hours = (end_min - start_min) / 60.0

    dormida_entry_minutes = _parse_time_to_minutes(dormida_entrada)
    dormida_exit_minutes = _parse_time_to_minutes(dormida_salida)
    dormida_entry_day = _parse_weekday(dormida_entrada)
    dormida_exit_day = _parse_weekday(dormida_salida)

    if is_dormida:
        if "quincenal" in combined_hint or schedule_key == "cd_quincenal" or dormida_exit_day == "segundo_viernes":
            mode_key = "dormida_quincenal"
        elif _is_lunes_a_sabado_hint(combined_hint) or schedule_key == "cd_l_s" or dormida_exit_day == "sabado":
            mode_key = "dormida_l_s"
        elif _is_lunes_a_viernes_hint(combined_hint) or schedule_key == "cd_l_v" or dormida_exit_day == "viernes":
            mode_key = "dormida_l_v"
        elif _is_fin_de_semana_hint(combined_hint) or schedule_key == "cd_fin_semana":
            mode_key = "fin_de_semana"
        else:
            mode_key = "otro"
    elif is_salida_diaria:
        sd_mode = _salida_diaria_mode_from_schedule_key(str(schedule_key or ""))
        if sd_mode:
            mode_key = sd_mode
        elif _is_lunes_a_sabado_hint(combined_hint):
            mode_key = "salida_diaria_l_s"
        elif _is_lunes_a_viernes_hint(combined_hint):
            mode_key = "salida_diaria_l_v"
        elif _is_fin_de_semana_hint(combined_hint):
            mode_key = "salida_diaria_fin_semana"
        else:
            mode_key = "otro"
    else:
        mode_key = "otro"

    weekend_entry_minutes = None
    weekend_exit_minutes = None
    if mode_key == "fin_de_semana":
        weekend_entry_minutes = dormida_entry_minutes if dormida_entry_minutes is not None else start_min
        weekend_exit_minutes = dormida_exit_minutes if dormida_exit_minutes is not None else _parse_time_to_minutes(hora_salida)
        hours = None

    return {
        "schedule_key": schedule_key or "",
        "mode_key": mode_key or "",
        "hours": hours,
        "entry_minutes": start_min,
        "exit_minutes": end_min,
        "dormida_entry_minutes": dormida_entry_minutes,
        "dormida_exit_minutes": dormida_exit_minutes,
        "weekend_entry_minutes": weekend_entry_minutes,
        "weekend_exit_minutes": weekend_exit_minutes,
        "dormida_entry_day": dormida_entry_day,
        "dormida_exit_day": dormida_exit_day,
    }


def _top_motivos(items: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    ordered = sorted(
        [item for item in items if float(item.get("amount") or 0) != 0],
        key=lambda item: (abs(float(item.get("amount") or 0)), item.get("kind") != "penalty"),
        reverse=True,
    )
    return ordered[:limit]


@dataclass
class _Context:
    funciones: set[str]
    habitaciones: int
    banos: float
    adults: int
    small_children: int
    older_children: int
    baby_count: int
    toddler_count: int
    active_small_child_count: int
    light_small_child_count: int
    supervision_count: int
    child_care_load: float
    effective_child_care_load: float
    child_care_help_code: str
    child_care_help_factor: float
    child_care_help_warnings: tuple[str, ...]
    supervision_only: bool
    unknown_child_count: int
    child_count: int
    known_child_ages: bool
    youngest_small_child_age: int | None
    child_load_exempt: bool
    schedule_key: str
    mode_key: str
    hours: float | None
    entry_minutes: int | None
    exit_minutes: int | None
    dormida_entry_minutes: int | None
    dormida_exit_minutes: int | None
    weekend_entry_minutes: int | None
    weekend_exit_minutes: int | None
    dormida_entry_day: str
    dormida_exit_day: str
    elder_type: str
    elder_resp: set[str]
    solo_cuidado_ninos: bool
    nanny_focused: bool
    solo_envejeciente_independiente: bool
    heavy_household: bool
    has_large_home: bool
    compact_home: bool
    has_pasaje_help: bool
    tipo_lugar: str
    common_areas_load: float
    extra_areas_count: int
    all_areas_selected: bool
    household_penalty_amount: float
    has_critical_combo: bool
    has_child_care_duty: bool
    has_effective_child_care_load: bool
    has_quincenal: bool
    total_occupants: int


class SolicitudAtractivoService:
    version = ATTRACTIVE_VERSION

    @classmethod
    def evaluate(cls, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data or {})
        motivos: list[dict[str, Any]] = []
        componentes_items: list[dict[str, Any]] = []
        score = float(BASE_SCORE)

        def add_component(*, key: str, amount: float, label: str, kind: str, bucket: str) -> None:
            nonlocal score
            if not amount:
                return
            score += float(amount)
            row = {
                "key": key,
                "amount": round(float(amount), 2),
                "label": label,
                "kind": kind,
                "bucket": bucket,
            }
            componentes_items.append(row)
            motivos.append(row)

        ctx = cls._build_context(payload)

        cls._apply_modalidad(ctx, add_component)
        cls._apply_horario(ctx, add_component)
        cls._apply_funciones(ctx, add_component)
        cls._apply_adultos(ctx, add_component)
        cls._apply_ocupacion_total(ctx, add_component)
        cls._apply_envejeciente(ctx, add_component)
        cls._apply_hogar(payload, ctx, add_component)
        critical_combos = cls._apply_combinadas(payload, ctx, add_component)
        cls._apply_bonificaciones(payload, ctx, add_component)

        score_sin_salario = _clamp_score(score)
        salario_component = cls._compute_salario_component(payload, ctx, score_sin_salario, critical_combos)
        if salario_component:
            add_component(
                key="salario",
                amount=salario_component["applied_amount"],
                label=salario_component["label"],
                kind="bonus" if salario_component["applied_amount"] >= 0 else "penalty",
                bucket="salario",
            )
            score = float(BASE_SCORE) + sum(float(item["amount"]) for item in componentes_items)
            score = cls._apply_critical_label_cap(
                score=score,
                score_sin_salario=score_sin_salario,
                critical_combos=critical_combos,
                componentes_items=componentes_items,
            )

        salary_reference = salario_component.get("reference") if salario_component else None
        quincenal_salary_band_cap = apply_quincenal_salary_band_cap(score, ctx, salary_reference)
        score_before_quincenal_salary_band_cap = score
        score = float(quincenal_salary_band_cap["score"])
        salary_excellence_cap = apply_salary_excellence_cap(
            score,
            (salary_reference or {}).get("offered") or payload.get("sueldo"),
            (salary_reference or {}).get("reference_min"),
            (salary_reference or {}).get("reference_max"),
            has_reliable_suggestion=bool((salary_reference or {}).get("can_suggest")),
            mode_key=ctx.mode_key,
        )
        score_before_salary_excellence_cap = score
        score = float(salary_excellence_cap["score"])
        final_score = _clamp_score(score)
        final_label = _score_label(final_score)
        main_motivos = _top_motivos(motivos, limit=4)
        componentes = {
            "base": BASE_SCORE,
            "items": componentes_items,
            "score_sin_salario": score_sin_salario,
            "critical_combinations": critical_combos,
            "salary_reference": salary_reference,
            "score_before_quincenal_salary_band_cap": _round_score(score_before_quincenal_salary_band_cap, 2),
            "quincenal_salary_band_cap_applied": bool(quincenal_salary_band_cap["applied"]),
            "quincenal_salary_band_cap_value": quincenal_salary_band_cap["cap_value"],
            "score_before_salary_excellence_cap": _round_score(score_before_salary_excellence_cap, 2),
            "salary_excellence_cap_applied": bool(salary_excellence_cap["applied"]),
            "salary_excellence_cap_value": salary_excellence_cap["cap_value"],
            "salary_over_max_ratio": salary_excellence_cap["salary_over_max_ratio"],
        }
        return {
            "score": final_score,
            "label": final_label,
            "motivos": main_motivos,
            "componentes": componentes,
            "version": cls.version,
        }

    @classmethod
    def _build_context(cls, data: dict[str, Any]) -> _Context:
        funciones = {_norm(item) for item in _as_list(data.get("funciones")) if _norm(item)}
        adults = _to_int(data.get("adultos"), default=0)
        tipo_lugar = _norm(data.get("tipo_lugar"))
        child_text = " ".join(
            part
            for part in (
                data.get("nota_cliente"),
                data.get("descripcion"),
                data.get("observaciones"),
            )
            if str(part or "").strip()
        )
        child_count = max(_to_int(data.get("ninos"), default=0), 0)
        detalles = data.get("detalles_servicio") if isinstance(data.get("detalles_servicio"), dict) else {}
        ayuda_raw = data.get("ayuda_cuidado_ninos")
        if ayuda_raw in (None, ""):
            ayuda_raw = detalles.get("ayuda_cuidado_ninos")
        edades_summary = parse_child_age_summary(str(data.get("edades_ninos") or ""), declared_count=child_count or None)
        text_summary = parse_child_age_summary(child_text)
        child_text_norm = _norm_text(child_text)
        explicit_small_children = int(edades_summary.get("small_count") or 0)
        explicit_older_children = int(edades_summary.get("big_count") or 0) + int(edades_summary.get("teen_count") or 0)
        inferred_small_children = int(text_summary.get("small_count") or 0)
        inferred_older_children = int(text_summary.get("big_count") or 0) + int(text_summary.get("teen_count") or 0)
        explicit_intensity = {
            "baby_count": int(edades_summary.get("baby_count") or 0),
            "toddler_count": int(edades_summary.get("toddler_count") or 0),
            "active_small_child_count": int(edades_summary.get("active_small_child_count") or 0),
            "light_small_child_count": int(edades_summary.get("light_small_child_count") or 0),
            "supervision_count": int(edades_summary.get("supervision_count") or 0),
            "child_care_load": float(edades_summary.get("child_care_load") or 0.0),
            "unknown_child_count": int(edades_summary.get("unknown_count") or 0),
        }
        inferred_intensity = {
            "baby_count": int(text_summary.get("baby_count") or 0),
            "toddler_count": int(text_summary.get("toddler_count") or 0),
            "active_small_child_count": int(text_summary.get("active_small_child_count") or 0),
            "light_small_child_count": int(text_summary.get("light_small_child_count") or 0),
            "supervision_count": int(text_summary.get("supervision_count") or 0),
            "child_care_load": float(text_summary.get("child_care_load") or 0.0),
            "unknown_child_count": 0,
        }
        explicit_ages = [int(age) for age in (edades_summary.get("ages_years") or []) if 0 <= int(age) <= 17]
        inferred_ages = [int(age) for age in (text_summary.get("ages_years") or []) if 0 <= int(age) <= 17]
        low_child_load_hint = any(hint in child_text_norm for hint in LOW_CHILD_LOAD_HINTS)
        high_child_load_hint = any(hint in child_text_norm for hint in HIGH_CHILD_LOAD_HINTS) or bool(
            re.search(r"\b([2345])\s*anos?\b", child_text_norm)
        )
        known_child_ages = bool(explicit_ages)
        small_children = explicit_small_children
        older_children = explicit_older_children
        child_intensity = dict(explicit_intensity)
        chosen_ages = list(explicit_ages)
        if not known_child_ages:
            if low_child_load_hint and not high_child_load_hint:
                small_children = 0
                older_children = 0
                child_intensity = {
                    "baby_count": 0,
                    "toddler_count": 0,
                    "active_small_child_count": 0,
                    "light_small_child_count": 0,
                    "supervision_count": child_count,
                    "child_care_load": 0.0,
                    "unknown_child_count": 0,
                }
                chosen_ages = []
            else:
                small_children = inferred_small_children
                older_children = inferred_older_children
                child_intensity = dict(inferred_intensity)
                chosen_ages = list(inferred_ages)
                if not chosen_ages and child_count > 0 and not high_child_load_hint:
                    child_intensity["unknown_child_count"] = int(edades_summary.get("unknown_count") or child_count)
                if small_children == 0 and older_children == 0 and high_child_load_hint:
                    small_children = 1
                    child_intensity = {
                        "baby_count": 1,
                        "toddler_count": 0,
                        "active_small_child_count": 0,
                        "light_small_child_count": 0,
                        "supervision_count": 0,
                        "child_care_load": 1.0,
                        "unknown_child_count": 0,
                    }
                    chosen_ages = [0]
        supervision_only = bool(
            "ninos" in funciones
            and child_count > 0
            and small_children == 0
            and (child_intensity["supervision_count"] > 0 or older_children > 0 or low_child_load_hint)
        )
        help_info = child_care_help_factor(ayuda_raw, edades_summary)
        effective_child_care_load = round(float(child_intensity["child_care_load"] or 0.0) * float(help_info["factor"]), 3)
        youngest_small_child_age = min((age for age in chosen_ages if 0 <= age <= 5), default=None)
        child_load_exempt = (
            "ninos" in funciones
            and not known_child_ages
            and small_children == 0
            and low_child_load_hint
            and not high_child_load_hint
        )
        elder_type = _norm(data.get("envejeciente_tipo_cuidado"))
        elder_resp = {_norm(item) for item in _as_list(data.get("envejeciente_responsabilidades")) if _norm(item)}
        schedule = _extract_schedule_context(data)
        solo_cuidado_ninos = "ninos" in funciones and not funciones.intersection(HOUSEHOLD_FUNCTIONS | {"envejeciente"})
        nanny_focused = bool(
            "ninos" in funciones
            and "limpieza" not in funciones
            and "envejeciente" not in funciones
            and funciones.issubset({"ninos", "cocinar", "lavar"})
        )
        solo_env_ind = funciones == {"envejeciente"} and elder_type == "independiente"
        heavy_household = bool(funciones.intersection({"limpieza", "cocinar", "lavar", "planchar"}))
        habitaciones = _to_int(data.get("habitaciones"), default=0)
        banos = _to_float(data.get("banos"), default=0.0)
        has_large_home = (habitaciones >= 4 and banos >= 4) or habitaciones >= 5 or banos >= 5
        areas_load, normalized_areas = common_areas_load(data)
        all_areas_selected = "todas_anteriores" in {_norm(item) for item in _as_list(data.get("areas_comunes")) if _norm(item)}
        extra_areas_count = len([item for item in normalized_areas if item not in {"sala", "comedor", "cocina", "otro", "todas_anteriores"}])
        pasaje_mode = _norm(data.get("pasaje_mode"))
        if not pasaje_mode:
            pasaje_mode = _norm((detalles.get("pasaje") or {}).get("mode"))
        has_critical_combo = bool(
            (
                "ninos" in funciones
                and effective_child_care_load >= 1.5
                and {"limpieza", "cocinar", "lavar"}.issubset(funciones)
            )
            or (
                elder_type == "encamado"
                and {"limpieza", "cocinar", "lavar"}.issubset(funciones)
            )
            or (schedule["hours"] is not None and float(schedule["hours"]) > 12)
        )
        has_pasaje_help = bool(data.get("pasaje_aporte")) or pasaje_mode in {"aparte", "otro"}
        return _Context(
            funciones=funciones,
            habitaciones=habitaciones,
            banos=banos,
            adults=adults,
            small_children=small_children,
            older_children=older_children,
            baby_count=child_intensity["baby_count"],
            toddler_count=child_intensity["toddler_count"],
            active_small_child_count=child_intensity["active_small_child_count"],
            light_small_child_count=child_intensity["light_small_child_count"],
            supervision_count=child_intensity["supervision_count"],
            child_care_load=child_intensity["child_care_load"],
            effective_child_care_load=effective_child_care_load,
            child_care_help_code=str(help_info["code"]),
            child_care_help_factor=float(help_info["factor"]),
            child_care_help_warnings=tuple(help_info.get("warnings") or ()),
            supervision_only=supervision_only,
            unknown_child_count=child_intensity["unknown_child_count"],
            child_count=child_count,
            known_child_ages=known_child_ages,
            youngest_small_child_age=youngest_small_child_age,
            child_load_exempt=child_load_exempt,
            schedule_key=str(schedule["schedule_key"] or ""),
            mode_key=str(schedule["mode_key"] or ""),
            hours=schedule["hours"],
            entry_minutes=schedule["entry_minutes"],
            exit_minutes=schedule["exit_minutes"],
            dormida_entry_minutes=schedule["dormida_entry_minutes"],
            dormida_exit_minutes=schedule["dormida_exit_minutes"],
            weekend_entry_minutes=schedule["weekend_entry_minutes"],
            weekend_exit_minutes=schedule["weekend_exit_minutes"],
            dormida_entry_day=str(schedule["dormida_entry_day"] or ""),
            dormida_exit_day=str(schedule["dormida_exit_day"] or ""),
            elder_type=elder_type,
            elder_resp=elder_resp,
            solo_cuidado_ninos=solo_cuidado_ninos,
            nanny_focused=nanny_focused,
            solo_envejeciente_independiente=solo_env_ind,
            heavy_household=heavy_household,
            has_large_home=has_large_home,
            compact_home=habitaciones <= 3 and banos <= 2,
            has_pasaje_help=has_pasaje_help,
            tipo_lugar=tipo_lugar,
            common_areas_load=areas_load,
            extra_areas_count=extra_areas_count,
            all_areas_selected=all_areas_selected,
            household_penalty_amount=_household_penalty_amount(habitaciones, banos),
            has_critical_combo=has_critical_combo,
            has_child_care_duty="ninos" in funciones,
            has_effective_child_care_load="ninos" in funciones and small_children > 0,
            has_quincenal=str(schedule["mode_key"] or "") == "dormida_quincenal",
            total_occupants=max(0, adults) + max(0, child_count),
        )

    @staticmethod
    def _apply_modalidad(ctx: _Context, add_component) -> None:
        if ctx.mode_key == "salida_diaria_1_dia":
            add_component(
                key="modalidad_sd_1_dia",
                amount=2.5,
                label="Un día semanal en salida diaria es una frecuencia muy flexible.",
                kind="bonus",
                bucket="modalidad",
            )
        elif ctx.mode_key == "salida_diaria_2_dias":
            add_component(
                key="modalidad_sd_2_dias",
                amount=2.0,
                label="Dos días semanales mantienen alta flexibilidad para la candidata.",
                kind="bonus",
                bucket="modalidad",
            )
        elif ctx.mode_key == "salida_diaria_3_dias":
            add_component(
                key="modalidad_sd_3_dias",
                amount=1.0,
                label="Tres días semanales siguen siendo una frecuencia flexible.",
                kind="bonus",
                bucket="modalidad",
            )
        elif ctx.mode_key == "salida_diaria_fin_semana":
            add_component(
                key="modalidad_sd_fin_semana",
                amount=1.0,
                label="La salida diaria de fin de semana se evalúa como frecuencia corta sin dormida.",
                kind="bonus",
                bucket="modalidad",
            )
        elif ctx.mode_key == "salida_diaria_l_s":
            add_component(
                key="modalidad_sd_l_s",
                amount=-1.5,
                label="Lunes a sábado en salida diaria reduce ligeramente el atractivo frente a L-V.",
                kind="penalty",
                bucket="modalidad",
            )
        elif ctx.mode_key == "dormida_l_v":
            add_component(
                key="modalidad_cd_l_v",
                amount=1.25,
                label="La modalidad con dormida de lunes a viernes mejora ligeramente el atractivo.",
                kind="bonus",
                bucket="modalidad",
            )
        elif ctx.mode_key == "dormida_l_s":
            add_component(
                key="modalidad_cd_l_s",
                amount=-2.5,
                label="La modalidad con dormida de lunes a sábado está dentro de las opciones habituales del mercado.",
                kind="penalty",
                bucket="modalidad",
            )
        elif ctx.mode_key == "dormida_quincenal":
            add_component(
                key="modalidad_cd_quincenal",
                amount=-1.5,
                label="La salida quincenal reduce el atractivo.",
                kind="penalty",
                bucket="modalidad",
            )
        elif ctx.mode_key == "fin_de_semana":
            add_component(
                key="modalidad_fin_semana",
                amount=4.5,
                label="La modalidad de fin de semana se evalúa con una escala independiente.",
                kind="bonus",
                bucket="modalidad",
            )

    @staticmethod
    def _apply_horario(ctx: _Context, add_component) -> None:
        if ctx.mode_key == "fin_de_semana":
            if ctx.weekend_entry_minutes is not None:
                if ctx.dormida_entry_day == "viernes":
                    if ctx.weekend_entry_minutes < (16 * 60):
                        add_component(
                            key="fin_semana_entrada_viernes_temprana",
                            amount=-2,
                            label="La entrada del viernes antes de media tarde reduce el atractivo.",
                            kind="penalty",
                            bucket="horario",
                        )
                    elif ctx.weekend_entry_minutes <= (19 * 60):
                        add_component(
                            key="fin_semana_entrada_viernes_favorable",
                            amount=1.5,
                            label="La entrada del viernes está dentro de un horario favorable.",
                            kind="bonus",
                            bucket="horario",
                        )
                    else:
                        add_component(
                            key="fin_semana_entrada_viernes_tarde",
                            amount=1,
                            label="La entrada del viernes en la noche sigue siendo manejable.",
                            kind="bonus",
                            bucket="horario",
                        )
                elif ctx.dormida_entry_day == "sabado":
                    if ctx.weekend_entry_minutes <= (9 * 60):
                        add_component(
                            key="fin_semana_entrada_sabado_temprana",
                            amount=2.5,
                            label="La entrada del sábado temprano mejora el atractivo.",
                            kind="bonus",
                            bucket="horario",
                        )
                    elif ctx.weekend_entry_minutes <= (12 * 60):
                        add_component(
                            key="fin_semana_entrada_sabado_media_manana",
                            amount=2,
                            label="La entrada del sábado en la mañana es favorable.",
                            kind="bonus",
                            bucket="horario",
                        )
                    elif ctx.weekend_entry_minutes <= (14 * 60):
                        add_component(
                            key="fin_semana_entrada_sabado_mediodia",
                            amount=1.5,
                            label="La entrada del sábado al mediodía es manejable.",
                            kind="bonus",
                            bucket="horario",
                        )
                    else:
                        add_component(
                            key="fin_semana_entrada_sabado_tarde",
                            amount=0.5,
                            label="La entrada del sábado en la tarde aporta poco al atractivo.",
                            kind="bonus",
                            bucket="horario",
                        )
                elif ctx.weekend_entry_minutes <= (8 * 60):
                    add_component(
                        key="fin_semana_entrada_8am",
                        amount=2,
                        label="La entrada temprano en fin de semana es favorable.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_entry_minutes <= (9 * 60):
                    add_component(
                        key="fin_semana_entrada_9am",
                        amount=2,
                        label="La entrada en la mañana de fin de semana es favorable.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_entry_minutes <= (10 * 60):
                    add_component(
                        key="fin_semana_entrada_10am",
                        amount=1.5,
                        label="La entrada de media mañana en fin de semana es manejable.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_entry_minutes < (14 * 60):
                    add_component(
                        key="fin_semana_entrada_12pm",
                        amount=1,
                        label="La entrada al mediodía en fin de semana aporta algo al atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_entry_minutes < (17 * 60):
                    add_component(
                        key="fin_semana_entrada_2pm",
                        amount=0.5,
                        label="La entrada en la tarde de fin de semana aporta poco al atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )

            if ctx.weekend_exit_minutes is None:
                return
            if ctx.dormida_exit_day == "lunes":
                if (6 * 60) <= ctx.weekend_exit_minutes <= (8 * 60):
                    add_component(
                        key="fin_semana_salida_lunes_favorable",
                        amount=1.5,
                        label="La salida del lunes está dentro de un horario favorable.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_exit_minutes < (6 * 60):
                    add_component(
                        key="fin_semana_salida_lunes_muy_temprana",
                        amount=0.5,
                        label="La salida del lunes muy temprano es favorable, aunque menos práctica.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.weekend_exit_minutes <= (10 * 60):
                    add_component(
                        key="fin_semana_salida_lunes_media_manana",
                        amount=0.5,
                        label="La salida del lunes en la mañana sigue siendo manejable.",
                        kind="bonus",
                        bucket="horario",
                    )
                else:
                    add_component(
                        key="fin_semana_salida_lunes_tarde",
                        amount=-1,
                        label="La salida del lunes más tarde reduce ligeramente el atractivo.",
                        kind="penalty",
                        bucket="horario",
                    )
            elif ctx.dormida_exit_day == "domingo":
                if ctx.weekend_exit_minutes >= (18 * 60):
                    add_component(
                        key="fin_semana_salida_domingo_noche",
                        amount=-2,
                        label="La salida del domingo en la noche reduce el atractivo.",
                        kind="penalty",
                        bucket="horario",
                    )
                else:
                    add_component(
                        key="fin_semana_salida_domingo",
                        amount=-1,
                        label="La salida del domingo reduce un poco el atractivo de esta modalidad.",
                        kind="penalty",
                        bucket="horario",
                    )
            elif ctx.weekend_exit_minutes <= (8 * 60):
                add_component(
                    key="fin_semana_salida_8am",
                    amount=1.5,
                    label="La salida está dentro de un horario favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.weekend_exit_minutes <= (9 * 60):
                add_component(
                    key="fin_semana_salida_9am",
                    amount=1,
                    label="La salida temprano en fin de semana es favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.weekend_exit_minutes <= (10 * 60):
                add_component(
                    key="fin_semana_salida_10am",
                    amount=0.5,
                    label="La salida a media mañana en fin de semana sigue siendo manejable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.weekend_exit_minutes <= (11 * 60):
                return
            elif ctx.weekend_exit_minutes <= (12 * 60):
                return
            elif ctx.weekend_exit_minutes <= (13 * 60):
                add_component(
                    key="fin_semana_salida_1pm",
                    amount=-1,
                    label="Salir después del mediodía en fin de semana baja el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )
            elif ctx.weekend_exit_minutes <= (14 * 60):
                add_component(
                    key="fin_semana_salida_2pm",
                    amount=-1.5,
                    label="Salir a media tarde en fin de semana baja el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )
            elif ctx.weekend_exit_minutes <= (15 * 60):
                add_component(
                    key="fin_semana_salida_3pm",
                    amount=-1.5,
                    label="Salir avanzada la tarde en fin de semana reduce el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )
            else:
                add_component(
                    key="fin_semana_salida_4pm",
                    amount=-2,
                    label="Salir tarde en fin de semana reduce el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )
            return

        if _is_salida_diaria_mode(ctx.mode_key):
            hours = ctx.hours
            if hours is not None:
                if 9 < hours <= 10:
                    add_component(key="horario_10h", amount=-1.5, label="Más de 9 y hasta 10 horas reduce ligeramente el atractivo.", kind="penalty", bucket="horario")
                elif 10 < hours <= 11:
                    add_component(key="horario_11h", amount=-3.5, label="Más de 10 y hasta 11 horas reduce el atractivo de forma moderada.", kind="penalty", bucket="horario")
                elif 11 < hours <= 12:
                    add_component(key="horario_12h", amount=-6, label="Más de 11 y hasta 12 horas baja claramente el atractivo.", kind="penalty", bucket="horario")
                elif hours > 12:
                    add_component(key="horario_12h_plus", amount=-10, label="Más de 12 horas baja mucho el atractivo.", kind="penalty", bucket="horario")

            if ctx.exit_minutes is not None:
                if ctx.exit_minutes >= (19 * 60):
                    add_component(key="salida_tarde_6", amount=-1, label="Salida después de 6:00 PM reduce un poco el atractivo.", kind="penalty", bucket="horario")
                    add_component(key="salida_tarde_7", amount=-2, label="Salida después de 7:00 PM reduce el atractivo.", kind="penalty", bucket="horario")
                elif ctx.exit_minutes > (18 * 60):
                    add_component(key="salida_tarde_6", amount=-1, label="Salida después de 6:00 PM reduce un poco el atractivo.", kind="penalty", bucket="horario")
            return

        if ctx.mode_key == "dormida_l_v":
            if ctx.dormida_entry_minutes is not None:
                if ctx.dormida_entry_minutes <= (6 * 60):
                    add_component(
                        key="dormida_lv_entrada_muy_temprano",
                        amount=-4,
                        label="La entrada del lunes es demasiado temprana para esta modalidad.",
                        kind="penalty",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes <= (7 * 60):
                    add_component(
                        key="dormida_lv_entrada_temprano",
                        amount=-2,
                        label="La entrada del lunes es un poco temprana.",
                        kind="penalty",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (12 * 60):
                    add_component(
                        key="dormida_lv_entrada_muy_favorable",
                        amount=1,
                        label="La entrada más tarde del lunes mejora ligeramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (11 * 60):
                    add_component(
                        key="dormida_lv_entrada_favorable_11",
                        amount=1,
                        label="La entrada más tarde del lunes mejora ligeramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (10 * 60):
                    add_component(
                        key="dormida_lv_entrada_favorable_10",
                        amount=1,
                        label="La entrada más tarde del lunes mejora ligeramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (9 * 60):
                    add_component(
                        key="dormida_lv_entrada_favorable_9",
                        amount=1,
                        label="La entrada más tarde del lunes mejora ligeramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
            if ctx.dormida_exit_minutes is None:
                return
            if ctx.dormida_exit_minutes < (15 * 60):
                add_component(
                    key="dormida_lv_salida_temprana",
                    amount=6,
                    label="La salida del viernes es especialmente favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (17 * 60):
                add_component(
                    key="dormida_lv_salida_favorable",
                    amount=4,
                    label="La salida del viernes está dentro de un horario favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (18 * 60):
                add_component(
                    key="dormida_lv_salida_tarde_leve",
                    amount=1,
                    label="La salida del viernes sigue siendo manejable.",
                    kind="bonus",
                    bucket="horario",
                )
            else:
                add_component(
                    key="dormida_lv_salida_tarde",
                    amount=-4,
                    label="La salida del viernes es bastante tarde para esta modalidad.",
                    kind="penalty",
                    bucket="horario",
                )
            return

        if ctx.mode_key == "dormida_l_s":
            if ctx.dormida_entry_minutes is not None:
                if ctx.dormida_entry_minutes <= (6 * 60):
                    add_component(
                        key="dormida_ls_entrada_muy_temprano",
                        amount=-4,
                        label="La entrada del lunes es demasiado temprana para esta modalidad.",
                        kind="penalty",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes <= (7 * 60):
                    add_component(
                        key="dormida_ls_entrada_temprano",
                        amount=-2,
                        label="La entrada del lunes es un poco temprana.",
                        kind="penalty",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (12 * 60):
                    add_component(
                        key="dormida_ls_entrada_muy_favorable",
                        amount=3,
                        label="La entrada del lunes mejora claramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (11 * 60):
                    add_component(
                        key="dormida_ls_entrada_favorable_11",
                        amount=3,
                        label="La entrada del lunes mejora claramente el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (10 * 60):
                    add_component(
                        key="dormida_ls_entrada_favorable_10",
                        amount=2,
                        label="La entrada del lunes mejora el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
                elif ctx.dormida_entry_minutes >= (9 * 60):
                    add_component(
                        key="dormida_ls_entrada_favorable_9",
                        amount=1,
                        label="La entrada del lunes mejora un poco el atractivo.",
                        kind="bonus",
                        bucket="horario",
                    )
            if ctx.dormida_exit_minutes is None:
                return
            if ctx.dormida_exit_minutes <= (12 * 60):
                add_component(
                    key="dormida_ls_salida_temprana",
                    amount=4,
                    label="La salida del sábado es especialmente razonable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (13 * 60):
                add_component(
                    key="dormida_ls_salida_favorable",
                    amount=3,
                    label="La salida del sábado está dentro de un horario favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (14 * 60):
                add_component(
                    key="dormida_ls_salida_2pm",
                    amount=1,
                    label="La salida del sábado a las 2:00 PM sigue siendo manejable, aunque menos favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            else:
                add_component(
                    key="dormida_ls_salida_muy_tarde",
                    amount=-6,
                    label="La salida del sábado es demasiado tarde para esta modalidad.",
                    kind="penalty",
                    bucket="horario",
                )
            return

        if ctx.mode_key == "dormida_quincenal" and ctx.dormida_exit_minutes is not None:
            if ctx.dormida_exit_minutes <= (11 * 60):
                add_component(
                    key="dormida_quincenal_salida_11am",
                    amount=1.5,
                    label="La salida quincenal antes del mediodía mejora el atractivo.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (12 * 60):
                add_component(
                    key="dormida_quincenal_salida_12pm",
                    amount=1,
                    label="La salida quincenal al mediodía es favorable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (13 * 60):
                add_component(
                    key="dormida_quincenal_salida_1pm",
                    amount=0.5,
                    label="La salida quincenal a la 1:00 PM sigue siendo manejable.",
                    kind="bonus",
                    bucket="horario",
                )
            elif ctx.dormida_exit_minutes <= (14 * 60):
                add_component(
                    key="dormida_quincenal_salida_2pm",
                    amount=-1,
                    label="La salida quincenal a las 2:00 PM reduce un poco el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )
            else:
                add_component(
                    key="dormida_quincenal_salida_tarde",
                    amount=-1.5,
                    label="La salida quincenal después de las 2:00 PM reduce un poco más el atractivo.",
                    kind="penalty",
                    bucket="horario",
                )

    @staticmethod
    def _apply_funciones(ctx: _Context, add_component) -> None:
        if "planchar" in ctx.funciones:
            add_component(key="func_planchar", amount=-2, label="Planchar reduce un poco el atractivo.", kind="penalty", bucket="funciones")

    @staticmethod
    def _apply_adultos(ctx: _Context, add_component) -> None:
        if ctx.adults == 4:
            add_component(key="adultos_4", amount=-1, label="Cuatro adultos en el hogar bajan un poco el atractivo.", kind="penalty", bucket="adultos")
        elif ctx.adults == 5:
            add_component(key="adultos_5", amount=-2, label="Cinco adultos en el hogar bajan atractivo de forma moderada.", kind="penalty", bucket="adultos")
        elif ctx.adults == 6:
            add_component(key="adultos_6", amount=-3, label="Seis adultos en el hogar bajan atractivo.", kind="penalty", bucket="adultos")
        elif ctx.adults >= 7:
            add_component(key="adultos_7_plus", amount=-4, label="Siete o más adultos en el hogar bajan atractivo.", kind="penalty", bucket="adultos")

    @staticmethod
    def _apply_ocupacion_total(ctx: _Context, add_component) -> None:
        if ctx.child_count <= 0:
            return
        total = max(0, int(ctx.total_occupants or 0))
        if total <= 3:
            return
        if total == 4:
            amount = -0.5
        elif total == 5:
            amount = -1.0
        elif total == 6:
            amount = -1.5
        elif total == 7:
            amount = -2.0
        else:
            amount = -2.5
        if ctx.has_effective_child_care_load:
            amount = max(amount, -1.5)
        if ctx.adults >= 4:
            amount = max(amount, -2.0)
        add_component(
            key="ocupacion_total",
            amount=amount,
            label="La ocupación total del hogar aumenta la carga diaria sin implicar cuidado infantil.",
            kind="penalty",
            bucket="ocupacion",
        )

    @staticmethod
    def _apply_envejeciente(ctx: _Context, add_component) -> None:
        if "envejeciente" not in ctx.funciones:
            return
        if ctx.elder_type == "independiente":
            add_component(key="env_ind", amount=-6, label="Envejeciente independiente baja atractivo.", kind="penalty", bucket="envejeciente")
            return
        if ctx.elder_type == "encamado":
            if ctx.mode_key == "dormida_quincenal":
                env_amount = -2
            elif ctx.mode_key == "dormida_l_s":
                env_amount = -4
            else:
                env_amount = -16
            add_component(key="env_enc", amount=env_amount, label="Envejeciente encamado baja mucho el atractivo.", kind="penalty", bucket="envejeciente")
            if ctx.elder_resp.intersection(ELDER_HEAVY_RESPONSIBILITIES):
                heavy_elder_resp_count = len(ctx.elder_resp.intersection(ELDER_HEAVY_RESPONSIBILITIES))
                if ctx.mode_key == "dormida_quincenal":
                    extra_amount = -4 if heavy_elder_resp_count >= 2 else 0
                elif ctx.mode_key == "dormida_l_s":
                    extra_amount = -4 if heavy_elder_resp_count >= 2 else 0
                else:
                    extra_amount = -4
                add_component(
                    key="env_enc_extra",
                    amount=extra_amount,
                    label="Encamado con higiene, pampers, movilidad o medicación penaliza extra.",
                    kind="penalty",
                    bucket="envejeciente",
                )

    @staticmethod
    def _apply_hogar(data: dict[str, Any], ctx: _Context, add_component) -> None:
        hab = ctx.habitaciones
        banos = ctx.banos
        pisos = _norm(data.get("pisos") or data.get("cantidad_pisos"))
        is_single_floor = pisos in {"1", "1 piso", "uno", ""} or not pisos or pisos == "1.0"
        physical_load = _physical_home_load(hab, banos)
        if physical_load:
            add_component(
                key="hogar_carga_fisica",
                amount=-physical_load,
                label="La carga física de habitaciones y baños se aplica de forma progresiva.",
                kind="penalty",
                bucket="hogar",
            )
        if ctx.common_areas_load:
            add_component(
                key="hogar_areas_comunes",
                amount=-ctx.common_areas_load,
                label="Las áreas comunes adicionales suman una carga suave y progresiva.",
                kind="penalty",
                bucket="hogar",
            )
        if hab >= 5 or banos >= 5:
            low_occupancy_large_home = bool(
                is_single_floor
                and ctx.adults <= 3
                and not ctx.has_effective_child_care_load
                and ctx.elder_type != "encamado"
                and not ctx.has_critical_combo
                and ctx.hours is not None
                and ctx.hours <= 9
            )
            if low_occupancy_large_home:
                add_component(
                    key="hogar_baja_ocupacion",
                    amount=0.3,
                    label="La baja ocupación modera parte de la carga de una vivienda grande.",
                    kind="bonus",
                    bucket="hogar",
                )

        if pisos == "3+":
            add_component(key="hogar_3_pisos", amount=-4, label="Tres o más pisos bajan atractivo.", kind="penalty", bucket="hogar")

    @classmethod
    def _apply_combinadas(cls, data: dict[str, Any], ctx: _Context, add_component) -> list[str]:
        critical: list[str] = []
        has_limpieza = "limpieza" in ctx.funciones
        has_cocinar = "cocinar" in ctx.funciones
        has_lavar = "lavar" in ctx.funciones
        has_planchar = "planchar" in ctx.funciones

        if ctx.has_effective_child_care_load:
            load = max(0.0, float(ctx.effective_child_care_load or 0.0))
            child_penalty = 0.0
            if not has_limpieza:
                child_penalty = 0
            elif has_limpieza and ctx.mode_key == "dormida_l_v" and load < 1.5:
                age = ctx.youngest_small_child_age
                if age is None or age <= 0:
                    child_penalty = -1.5
                elif age <= 2:
                    child_penalty = -1.2
                elif age <= 4:
                    child_penalty = -1.0
                else:
                    child_penalty = -0.8
                domestic_core_load = float(int(has_cocinar) + int(has_lavar)) * 0.5
                child_penalty -= domestic_core_load
                child_penalty -= min(2.0, max(0.0, load - 1.0) * 2.5)
                child_penalty -= max(0.0, float(ctx.small_children - 1)) * 0.8
            elif has_limpieza and has_cocinar and has_lavar:
                child_penalty = -(3.5 + min(3.5, max(0.0, load - 0.9) * 3.0))
                if load >= 1.5:
                    critical.append("nino_pequeno_limpieza_cocinar_lavar")
            elif has_limpieza and (has_cocinar or has_lavar):
                child_penalty = -(3.0 + min(2.5, max(0.0, load - 0.9) * 2.5))
            elif has_limpieza:
                age = ctx.youngest_small_child_age
                if age is None or age <= 0:
                    child_penalty = -1.5
                elif age <= 2:
                    child_penalty = -1.2
                elif age <= 4:
                    child_penalty = -1.0
                else:
                    child_penalty = -0.8
                child_penalty -= min(2.0, max(0.0, load - 1.0) * 2.5)
                child_penalty -= max(0.0, float(ctx.small_children - 1)) * 0.8
            else:
                if load > 1.0:
                    child_penalty -= min(0.8, (load - 1.0) * 1.5)
            if ctx.mode_key == "dormida_quincenal" and child_penalty < 0:
                child_penalty += min(1.5, abs(child_penalty) * 0.5)
                if ctx.child_care_help_factor >= 1.0:
                    child_penalty *= 0.45
            if ctx.mode_key == "dormida_l_s" and child_penalty < 0:
                child_penalty += 1.0
            if ctx.child_care_help_factor < 1.0 and child_penalty < 0:
                retained_ratio = max(0.15, min(0.45, float(ctx.child_care_help_factor or 1.0)))
                child_penalty *= retained_ratio
            if (
                _is_salida_diaria_mode(ctx.mode_key)
                and ctx.child_care_help_factor < 1.0
                and ctx.small_children >= 2
                and child_penalty < 0
            ):
                child_penalty -= 2.0
            add_component(
                key="combo_ninos_pequenos",
                amount=round(float(child_penalty), 2),
                label=(
                    "La ayuda con los niños reduce parte de la carga de cuidado."
                    if ctx.child_care_help_factor < 1.0
                    else "Niños pequeños con la carga actual reducen atractivo."
                ),
                kind="penalty" if child_penalty < 0 else "bonus",
                bucket="combinadas",
            )
            if ctx.child_care_help_factor < 1.0 and has_limpieza:
                reduced_load = max(0.0, float(ctx.child_care_load or 0.0) - float(ctx.effective_child_care_load or 0.0))
                relief_cap = 2.0 if ctx.mode_key == "dormida_l_s" else 0.0
                if ctx.mode_key == "dormida_l_s" and ctx.has_large_home:
                    relief_cap = 1.2
                relief_base = (reduced_load * 2.8) + (0.5 if ctx.mode_key == "dormida_l_s" else 0.0)
                relief = min(relief_cap, relief_base) if ctx.child_care_help_code == "con_ayuda" else 0.0
                if relief:
                    add_component(
                        key="ayuda_cuidado_ninos",
                        amount=round(float(relief), 2),
                        label="La ayuda con los niños reduce parte de la carga de cuidado.",
                        kind="bonus",
                        bucket="combinadas",
                    )

        if ctx.elder_type == "encamado":
            elder_combo = 0
            if ctx.mode_key == "dormida_quincenal":
                heavy_elder_resp_count = len(ctx.elder_resp.intersection(ELDER_HEAVY_RESPONSIBILITIES))
                if has_limpieza and has_cocinar and has_lavar and has_planchar:
                    elder_combo = -5
                    critical.append("encamado_limpieza_cocinar_lavar")
                elif has_limpieza and has_cocinar and has_lavar:
                    elder_combo = -5 if heavy_elder_resp_count >= 2 else -2
                elif has_limpieza and has_cocinar:
                    elder_combo = -1.5
                elif has_limpieza:
                    elder_combo = -1
                if heavy_elder_resp_count >= 2:
                    critical.append("encamado_dependencia_intensa")
            elif ctx.mode_key == "dormida_l_s":
                heavy_elder_resp_count = len(ctx.elder_resp.intersection(ELDER_HEAVY_RESPONSIBILITIES))
                if has_limpieza and has_cocinar and has_lavar and has_planchar:
                    elder_combo = -6
                    critical.append("encamado_limpieza_cocinar_lavar")
                elif has_limpieza and has_cocinar and has_lavar:
                    elder_combo = -6 if heavy_elder_resp_count >= 2 else -3
                elif has_limpieza and has_cocinar:
                    elder_combo = -2
                elif has_limpieza:
                    elder_combo = -1.5
                if heavy_elder_resp_count >= 2:
                    critical.append("encamado_dependencia_intensa")
            else:
                if has_limpieza and has_cocinar and has_lavar and has_planchar:
                    elder_combo = -21
                elif has_limpieza and has_cocinar and has_lavar:
                    elder_combo = -18
                    critical.append("encamado_limpieza_cocinar_lavar")
                elif has_limpieza and has_cocinar:
                    elder_combo = -13
                elif has_limpieza:
                    elder_combo = -10
            add_component(
                key="combo_envejeciente",
                amount=elder_combo,
                label="Encamado combinado con funciones del hogar baja mucho el atractivo.",
                kind="penalty" if elder_combo < 0 else "bonus",
                bucket="combinadas",
            )

        hab = _to_int(data.get("habitaciones"), default=0)
        banos = _to_float(data.get("banos"), default=0.0)
        has_large_home_adults_combo = False
        if has_limpieza and hab >= 4 and banos >= 4 and ctx.adults >= 4:
            combo_amount = -1
            if ctx.adults >= 7:
                combo_amount = -4
            elif ctx.adults == 6:
                combo_amount = -3
            elif ctx.adults == 5:
                combo_amount = -2
            add_component(
                key="combo_hogar_grande_adultos",
                amount=combo_amount,
                label="Hogar grande con varios adultos aumenta la carga operativa.",
                kind="penalty",
                bucket="combinadas",
            )
            has_large_home_adults_combo = True
        if ctx.adults >= 4 and has_limpieza and has_lavar and not has_large_home_adults_combo:
            combo_amount = -1 if ctx.adults == 4 else (-2 if ctx.adults in {5, 6} else -3)
            add_component(
                key="combo_adultos_limpieza_lavar",
                amount=combo_amount,
                label="Varios adultos con limpieza y lavar aumentan la carga.",
                kind="penalty",
                bucket="combinadas",
            )
        if _is_salida_diaria_mode(ctx.mode_key) and ctx.has_large_home and ctx.hours is not None and ctx.hours > 10:
            add_component(
                key="combo_casa_grande_horas",
                amount=-4,
                label="Casa grande con jornada mayor de 10 horas penaliza extra.",
                kind="penalty",
                bucket="combinadas",
            )
        if _is_salida_diaria_mode(ctx.mode_key) and ctx.hours is not None and ctx.hours > 12:
            critical.append("jornada_mayor_12h")
        if ctx.mode_key == "salida_diaria_l_s" and ctx.hours is not None and ctx.hours > 10:
            add_component(
                key="combo_l_s_10h",
                amount=-8,
                label="Lunes a sábado con más de 10 horas penaliza extra.",
                kind="penalty",
                bucket="combinadas",
            )
        strong_house_load = has_limpieza and (has_cocinar or has_lavar or ctx.has_large_home or ctx.adults >= 4)
        if ctx.mode_key == "dormida_quincenal":
            quincenal_extra_strong_load = (
                strong_house_load
                and (
                    ctx.household_penalty_amount >= 7.0
                    or ctx.adults >= 5
                    or (ctx.has_effective_child_care_load and ctx.child_care_load >= 1.5 and (ctx.has_large_home or ctx.adults >= 4 or has_cocinar or has_lavar))
                )
            )
            if quincenal_extra_strong_load:
                add_component(
                    key="combo_quincenal_carga_fuerte",
                    amount=-3,
                    label="Quincenal con carga fuerte de hogar penaliza extra.",
                    kind="penalty",
                    bucket="combinadas",
                )
        return sorted(set(critical))

    @staticmethod
    def _apply_bonificaciones(data: dict[str, Any], ctx: _Context, add_component) -> None:
        nanny_pure_age_adjustment = 0
        if ctx.nanny_focused:
            age = ctx.youngest_small_child_age
            if age is None:
                if ctx.older_children > 0:
                    nanny_pure_age_adjustment = 2
            elif age <= 0:
                nanny_pure_age_adjustment = -1
            elif age <= 2:
                nanny_pure_age_adjustment = 0
            elif age <= 4:
                nanny_pure_age_adjustment = 1
            else:
                nanny_pure_age_adjustment = 2
        qualifies_large_home_normal_transition = bool(
            (
                (
                    _is_salida_diaria_mode(ctx.mode_key)
                    and ctx.hours is not None
                    and ctx.hours <= 9
                )
                or (
                    ctx.mode_key == "dormida_l_v"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (17 * 60)
                    and (
                        ctx.dormida_entry_minutes is None
                        or ctx.dormida_entry_minutes >= (8 * 60)
                    )
                )
                or (
                    ctx.mode_key == "dormida_l_s"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (14 * 60)
                    and (
                        ctx.dormida_entry_minutes is None
                        or ctx.dormida_entry_minutes >= (8 * 60)
                    )
                )
                or (
                    ctx.mode_key == "dormida_quincenal"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (15 * 60)
                    and (
                        ctx.dormida_entry_minutes is None
                        or ctx.dormida_entry_minutes >= (8 * 60)
                    )
                )
            )
            and ctx.extra_areas_count <= 3
            and not ctx.all_areas_selected
        )
        qualifies_normal_request = (
            (
                _is_salida_diaria_mode(ctx.mode_key)
                or ctx.mode_key in {"dormida_l_v", "dormida_l_s", "dormida_quincenal"}
            )
            and (
                not (ctx.habitaciones >= 4 and ctx.banos >= 4)
                or qualifies_large_home_normal_transition
                or (
                    ctx.mode_key == "dormida_quincenal"
                    and ctx.household_penalty_amount <= 4.0
                )
            )
            and (
                0 <= ctx.adults <= 3
                or (ctx.mode_key == "dormida_l_v" and ctx.adults <= 4)
                or (ctx.mode_key == "dormida_l_s" and ctx.adults <= 5)
                or (ctx.mode_key == "dormida_quincenal" and ctx.adults <= 4)
            )
            and not ctx.nanny_focused
            and not (
                ctx.has_child_care_duty
                and ctx.child_care_load >= 1.5
                and {"limpieza", "cocinar", "lavar"}.issubset(ctx.funciones)
            )
            and (
                ctx.elder_type != "encamado"
                or (
                    ctx.mode_key == "dormida_quincenal"
                    and ctx.adults <= 3
                    and not ctx.has_large_home
                    and not ctx.has_effective_child_care_load
                )
                or (
                    ctx.mode_key == "dormida_l_s"
                    and ctx.adults <= 3
                    and not ctx.has_large_home
                    and not ctx.has_effective_child_care_load
                )
            )
            and (
                (
                    _is_salida_diaria_mode(ctx.mode_key)
                    and ctx.hours is not None
                    and ctx.hours <= 9
                )
                or (
                    ctx.mode_key == "dormida_l_v"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (17 * 60)
                )
                or (
                    ctx.mode_key == "dormida_quincenal"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (15 * 60)
                )
                or (
                    ctx.mode_key == "dormida_l_s"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (14 * 60)
                    and (
                        ctx.dormida_entry_minutes is None
                        or ctx.dormida_entry_minutes >= (8 * 60)
                    )
                )
            )
        )
        if qualifies_normal_request:
            bonus_amount = _normal_request_bonus_amount(ctx, data)
            if bonus_amount > 0:
                add_component(
                    key="bonus_solicitud_normal_atractiva",
                    amount=bonus_amount,
                    label="La solicitud cae dentro del perfil normal y atractivo del mercado.",
                    kind="bonus",
                    bucket="bonificaciones",
                )
        qualifies_focused_nanny_request = (
            ctx.nanny_focused
            and not ctx.has_quincenal
            and ctx.elder_type != "encamado"
            and (
                (
                    ctx.mode_key == "salida_diaria_l_v"
                    and ctx.hours is not None
                    and ctx.hours <= 9
                )
                or (
                    ctx.mode_key == "dormida_l_v"
                    and ctx.dormida_exit_minutes is not None
                    and ctx.dormida_exit_minutes <= (17 * 60)
                )
            )
        )
        if qualifies_focused_nanny_request:
            if ctx.mode_key == "salida_diaria_l_v":
                base_bonus = 10
            elif ctx.mode_key == "dormida_l_v":
                base_bonus = 14.5
            else:
                base_bonus = 7
            child_task_adjustment = 0.0
            if not ctx.solo_cuidado_ninos:
                if "cocinar" in ctx.funciones:
                    child_task_adjustment -= 0.5
                if "lavar" in ctx.funciones:
                    child_task_adjustment -= 0.5
            bonus_amount = base_bonus + nanny_pure_age_adjustment + child_task_adjustment
            add_component(
                key="bonus_ninera_pura_atractiva",
                amount=bonus_amount,
                label="La solicitud es una niñera enfocada con horario razonable y buena carga base.",
                kind="bonus",
                bucket="bonificaciones",
            )
        elif ctx.nanny_focused and ctx.has_effective_child_care_load:
            add_component(
                key="bonus_cuidado_ninos",
                amount=4,
                label="Solo cuidado de bebé o niño sin carga del hogar suma atractivo.",
                kind="bonus",
                bucket="bonificaciones",
            )
        if ctx.solo_envejeciente_independiente:
            add_component(
                key="bonus_env_ind",
                amount=2,
                label="Solo envejeciente independiente sin limpieza suma atractivo.",
                kind="bonus",
                bucket="bonificaciones",
            )
        if (
            ctx.heavy_household
            and not qualifies_normal_request
            and not ctx.has_critical_combo
            and ctx.habitaciones <= 2
        ):
            add_component(
                key="bonus_hogar_compacto",
                amount=0.5,
                label="Un hogar muy compacto sigue siendo más manejable fuera del perfil normal.",
                kind="bonus",
                bucket="bonificaciones",
            )
        if ctx.tipo_lugar in {"apto", "apartamento", "penthouse"}:
            apt_bonus = 0
            if ctx.mode_key == "dormida_l_s":
                apt_bonus = 0
            elif (
                ctx.compact_home
                and ctx.adults <= 3
                and not ctx.has_critical_combo
                and ctx.extra_areas_count <= 3
                and not ctx.all_areas_selected
            ):
                apt_bonus = 3
            elif (
                not ctx.has_critical_combo
                and not ctx.all_areas_selected
                and not ctx.has_large_home
                and ctx.extra_areas_count <= 4
            ):
                apt_bonus = 1
            if apt_bonus:
                add_component(
                    key="bonus_apartamento",
                    amount=apt_bonus,
                    label="El apartamento suele ser más manejable que una casa equivalente.",
                    kind="bonus",
                    bucket="bonificaciones",
                )
        if ctx.has_pasaje_help:
            add_component(
                key="bonus_pasaje",
                amount=2,
                label="La ayuda con pasaje mejora un poco el atractivo.",
                kind="bonus",
                bucket="bonificaciones",
            )

    @classmethod
    def _compute_salario_component(
        cls,
        data: dict[str, Any],
        ctx: _Context,
        score_sin_salario: int,
        critical_combos: list[str],
    ) -> dict[str, Any] | None:
        offered = parse_salary_amount(data.get("sueldo"))
        if not offered:
            return None
        salary_payload = dict(data or {})
        if ctx.has_child_care_duty and not ctx.has_effective_child_care_load:
            salary_payload["funciones"] = [item for item in _as_list(salary_payload.get("funciones")) if _norm(item) != "ninos"]
            salary_payload["ninos"] = 0
            salary_payload["edades_ninos"] = ""
        elif (
            ctx.has_effective_child_care_load
            and ctx.child_care_load < 1.5
            and ctx.mode_key == "dormida_l_v"
            and "limpieza" in ctx.funciones
        ):
            salary_payload["funciones"] = [
                item
                for item in _as_list(salary_payload.get("funciones"))
                if _norm(item) not in {"cocinar", "lavar"}
            ]
        salary_ref_payload = analyze_salary_suggestion(salary_payload)
        can_suggest = bool(salary_ref_payload.get("can_suggest"))
        if can_suggest:
            ref_min = int(salary_ref_payload.get("suggested_min") or 0)
            ref_max = int(salary_ref_payload.get("suggested_max") or ref_min)
        else:
            ref_min = parse_salary_amount(data.get("sueldo")) or 0
            ref_max = ref_min
        if ref_min <= 0:
            return None
        if ref_max < ref_min:
            ref_max = ref_min
        def _interp(start_value: float, end_value: float, start_point: float, end_point: float, current: float) -> float:
            if end_point <= start_point:
                return start_value
            ratio = (current - start_point) / (end_point - start_point)
            ratio = max(0.0, min(1.0, ratio))
            return start_value + ((end_value - start_value) * ratio)

        if offered < ref_min:
            salary_position = "below_min"
            ratio_below_min = (offered / ref_min) if ref_min else 1.0
            if ratio_below_min >= 0.90:
                raw_amount = _interp(-5.0, 5.0, ref_min * 0.90, ref_min, offered)
                slightly_below_sd = False
                if _is_low_frequency_salida_diaria_mode(ctx.mode_key) and (ref_min - offered) <= 500:
                    raw_amount = max(raw_amount, 3.0)
                    slightly_below_sd = True
                if (
                    ctx.mode_key == "dormida_l_s"
                    and offered >= (ref_min - 1000)
                    and ctx.has_effective_child_care_load
                    and ctx.child_care_help_factor >= 1.0
                ):
                    floor_start = 4.45 if float(ctx.child_care_load or 0.0) <= 1.15 else 3.2
                    raw_amount = max(raw_amount, _interp(floor_start, 5.0, ref_min - 1000, ref_min, offered))
                elif ctx.mode_key == "dormida_l_s" and offered >= (ref_min - 1000):
                    raw_amount = max(raw_amount, 3.2)
                label = (
                    "El sueldo ofrecido queda ligeramente por debajo del mínimo sugerido para esta frecuencia."
                    if slightly_below_sd
                    else "El sueldo ofrecido está por debajo del mínimo sugerido."
                )
            else:
                raw_amount = -10.0
                label = "El sueldo ofrecido está claramente por debajo del mínimo sugerido."
        elif offered <= ref_max:
            if offered == ref_min:
                salary_position = "at_min"
                label = "El sueldo ofrecido está dentro del rango mínimo sugerido."
            elif offered == ref_max:
                salary_position = "at_max"
                label = "El sueldo ofrecido mejora el atractivo de la solicitud."
            else:
                salary_position = "above_min"
                label = "El sueldo ofrecido está por encima del mínimo sugerido."
            raw_amount = _interp(5.0, 7.0, ref_min, ref_max, offered)
        else:
            salary_position = "above_max"
            pct_above_max = ((offered - ref_max) / ref_max) if ref_max else 0.0
            if _is_low_frequency_salida_diaria_mode(ctx.mode_key):
                if pct_above_max <= 0.10:
                    raw_amount = _interp(7.0, 9.0, ref_max, ref_max * 1.10, offered)
                    label = "El sueldo ofrecido está hasta 10% por encima del máximo sugerido para esta frecuencia."
                elif pct_above_max <= 0.30:
                    raw_amount = _interp(9.0, 12.0, ref_max * 1.10, ref_max * 1.30, offered)
                    label = "El sueldo ofrecido está entre 10% y 30% por encima del máximo sugerido para esta frecuencia."
                elif pct_above_max <= 0.50:
                    raw_amount = _interp(12.0, 14.0, ref_max * 1.30, ref_max * 1.50, offered)
                    label = "El sueldo ofrecido está entre 30% y 50% por encima del máximo sugerido para esta frecuencia."
                else:
                    extra_ratio = min(1.0, max(0.0, (pct_above_max - 0.50) / 1.50))
                    raw_amount = 14.0 + (3.0 * math.sqrt(extra_ratio))
                    label = "El sueldo ofrecido está muy por encima del máximo sugerido para esta frecuencia, con rendimiento decreciente."
            elif pct_above_max >= 0.40:
                raw_amount = 22.0
                label = "El sueldo ofrecido está 40% o más por encima del máximo sugerido."
            elif pct_above_max >= 0.25:
                raw_amount = _interp(17.0, 22.0, ref_max * 1.25, ref_max * 1.40, offered)
                label = "El sueldo ofrecido está entre 25% y 39% por encima del máximo sugerido."
            elif pct_above_max >= 0.15:
                raw_amount = _interp(12.0, 17.0, ref_max * 1.15, ref_max * 1.25, offered)
                label = "El sueldo ofrecido está entre 15% y 24% por encima del máximo sugerido."
            else:
                raw_amount = _interp(7.0, 12.0, ref_max, ref_max * 1.15, offered)
                label = "El sueldo ofrecido está hasta 14% por encima del máximo sugerido."

        if ctx.mode_key == "fin_de_semana":
            if offered < ref_min:
                salary_position = "below_min"
                if offered >= (ref_min * 0.90):
                    raw_amount = _interp(-3.0, 4.0, ref_min * 0.90, ref_min, offered)
                    label = "El sueldo ofrecido está por debajo del mínimo sugerido para fin de semana."
                else:
                    raw_amount = -6.0
                    label = "El sueldo ofrecido está claramente por debajo del mínimo sugerido para fin de semana."
            elif offered <= ref_max:
                if offered == ref_min:
                    salary_position = "at_min"
                elif offered == ref_max:
                    salary_position = "at_max"
                else:
                    salary_position = "above_min"
                raw_amount = _interp(4.0, 6.0, ref_min, ref_max, offered)
                label = "El sueldo ofrecido está dentro del rango sugerido para fin de semana."
            else:
                salary_position = "above_max"
                pct_above_max = ((offered - ref_max) / ref_max) if ref_max else 0.0
                if pct_above_max >= 0.40:
                    raw_amount = _interp(16.0, 17.0, ref_max * 1.40, ref_max * 1.80, offered)
                    label = "El sueldo ofrecido está muy por encima del máximo sugerido para fin de semana, con rendimiento decreciente."
                elif pct_above_max >= 0.20:
                    raw_amount = _interp(12.0, 16.0, ref_max * 1.20, ref_max * 1.40, offered)
                    label = "El sueldo ofrecido está entre 20% y 39% por encima del máximo sugerido para fin de semana."
                elif pct_above_max >= 0.10:
                    raw_amount = _interp(8.0, 12.0, ref_max * 1.10, ref_max * 1.20, offered)
                    label = "El sueldo ofrecido está entre 10% y 19% por encima del máximo sugerido para fin de semana."
                else:
                    raw_amount = _interp(6.0, 8.0, ref_max, ref_max * 1.10, offered)
                    label = "El sueldo ofrecido está hasta 9% por encima del máximo sugerido para fin de semana."

        if ctx.mode_key == "dormida_quincenal" and offered > ref_max:
            salary_position = "above_max"
            pct_above_max = ((offered - ref_max) / ref_max) if ref_max else 0.0
            if pct_above_max <= 0.05:
                raw_amount = _interp(7.0, 8.0, ref_max, ref_max * 1.05, offered)
                label = "El sueldo ofrecido está hasta 5% por encima del máximo sugerido para quincenal."
            elif pct_above_max <= 0.15:
                raw_amount = _interp(8.0, 9.5, ref_max * 1.05, ref_max * 1.15, offered)
                label = "El sueldo ofrecido está entre 5% y 15% por encima del máximo sugerido para quincenal, con rendimiento gradual."
            elif pct_above_max <= 0.30:
                raw_amount = _interp(9.5, 11.0, ref_max * 1.15, ref_max * 1.30, offered)
                label = "El sueldo ofrecido está entre 15% y 30% por encima del máximo sugerido para quincenal, con rendimiento decreciente."
            else:
                raw_amount = _interp(11.0, 12.0, ref_max * 1.30, ref_max * 1.60, offered)
                label = "El sueldo ofrecido está muy por encima del máximo sugerido para quincenal, con rendimiento muy decreciente."

        if (
            salary_position == "below_min"
            and ctx.mode_key == "dormida_l_s"
            and ctx.has_effective_child_care_load
            and ctx.heavy_household
            and ctx.child_care_load <= 1.5
            and ctx.adults <= 3
            and offered >= 21000
            and (ref_min - offered) <= 1500
            and not critical_combos
        ):
            child_salary_floor = 2.5 if ctx.small_children == 1 else 1.0
            if raw_amount < child_salary_floor:
                raw_amount = child_salary_floor
                label = (
                    "El sueldo ofrecido queda apenas por debajo del mínimo sugerido; "
                    "la carga infantil es leve a moderada y no debe duplicar la penalización."
                )

        # Solicitudes con carga relevante, pero no crítica, pueden recuperar
        # parte del atractivo cuando la oferta compensa bien esa fricción.
        noncritical_load_markers = int(ctx.has_large_home) + int(ctx.adults >= 4) + int(ctx.extra_areas_count >= 5) + int("planchar" in ctx.funciones)
        low_occupancy_noncritical_load = bool(
            ctx.has_large_home
            and ctx.adults <= 3
            and not ctx.has_effective_child_care_load
            and ctx.elder_type != "encamado"
            and ctx.hours is not None
            and ctx.hours <= 9
            and not critical_combos
        )
        qualifies_noncritical_compensated_load_relief = bool(
            offered >= (ref_min * 0.95)
            and ctx.mode_key == "salida_diaria_l_v"
            and noncritical_load_markers >= 2
            and ctx.heavy_household
            and not ctx.has_child_care_duty
            and ctx.elder_type != "encamado"
            and not critical_combos
            and ctx.hours is not None
            and ctx.hours <= 9
            and score_sin_salario <= 68
        )
        load_relief = None
        if qualifies_noncritical_compensated_load_relief:
            upper_band_floor = ref_min + ((ref_max - ref_min) / 2.0)
            if low_occupancy_noncritical_load:
                if offered >= ref_max:
                    recovery_amount = 5.0
                elif offered >= ref_min:
                    recovery_amount = 4.0
                else:
                    recovery_amount = 3.0
            else:
                if offered >= ref_max:
                    recovery_amount = 8.0 if score_sin_salario <= 62 else (6.0 if score_sin_salario <= 65 else 4.0)
                elif offered >= upper_band_floor:
                    recovery_amount = 4.0 if score_sin_salario <= 62 else 2.0
                elif offered >= ref_min:
                    recovery_amount = 2.0 if score_sin_salario <= 62 else 1.0
                else:
                    recovery_amount = 0.0
            if ctx.has_large_home and ctx.household_penalty_amount > 0:
                # Salary relief can soften large-home friction, but should not
                # scale up with additional rooms/baths and turn extra size into
                # an advantage.
                recovery_cap = 1.0 if ctx.adults <= 2 else 0.0
                recovery_amount = min(recovery_amount, recovery_cap)
            if ctx.adults >= 5:
                recovery_amount = max(0.0, recovery_amount - float(ctx.adults - 3))
            if recovery_amount:
                load_relief = round(float(recovery_amount), 2)
                raw_amount += recovery_amount
                label = f"{label} La oferta compensa razonablemente una carga relevante no crítica."

        schedule_relief = None
        if (
            ctx.mode_key == "dormida_l_s"
            and offered > ref_max
            and ref_max > 0
            and not ctx.has_child_care_duty
            and ctx.elder_type != "encamado"
            and not critical_combos
            and ctx.dormida_entry_minutes is not None
            and ctx.dormida_entry_minutes >= (8 * 60)
            and ctx.dormida_exit_minutes is not None
            and ctx.dormida_exit_minutes <= (14 * 60)
        ):
            over_max_ratio = (offered - ref_max) / (ref_max * 0.05)
            relief_amount = min(0.7, max(0.0, over_max_ratio) * 0.7)
            if relief_amount:
                schedule_relief = round(float(relief_amount), 2)
                raw_amount += relief_amount
                label = f"{label} La oferta compensa un poco la fricción de lunes a sábado."

        if ctx.nanny_focused and raw_amount > 7.0:
            raw_amount = 7.0
            label = "El sueldo está dentro de un rango suficiente para una niñera enfocada."

        return {
            "raw_amount": raw_amount,
            "applied_amount": round(float(raw_amount), 2),
            "label": label,
            "reference": {
                "offered": offered,
                "reference_amount": ref_min,
                "reference_min": ref_min,
                "reference_max": ref_max,
                "can_suggest": can_suggest,
                "salary_position": salary_position,
                "offer_status": salary_ref_payload.get("offer_status"),
                "critical_combos": list(critical_combos),
                "load_relief": load_relief,
                "schedule_relief": schedule_relief,
            },
        }

    @classmethod
    def _apply_critical_label_cap(
        cls,
        *,
        score: float,
        score_sin_salario: float,
        critical_combos: list[str],
        componentes_items: list[dict[str, Any]],
    ) -> float:
        if not critical_combos:
            return score
        critical_set = set(critical_combos)
        should_cap = bool(
            critical_set.intersection(
                {
                    "encamado_limpieza_cocinar_lavar",
                    "encamado_dependencia_intensa",
                    "nino_pequeno_limpieza_cocinar_lavar",
                    "quincenal",
                    "jornada_mayor_12h",
                }
            )
            or len(critical_set) >= 2
        )
        if not should_cap:
            return score
        label_before = _score_label_for_critical_cap(score_sin_salario)
        label_after = _score_label_for_critical_cap(score)
        if "encamado_dependencia_intensa" in critical_set and score > 69:
            allowed_score = 69
            delta = float(allowed_score - score_sin_salario)
            if delta < 0:
                delta = 0.0
            for item in reversed(componentes_items):
                if item.get("bucket") == "salario":
                    item["amount"] = round(float(delta), 2)
                    item["label"] = "El salario ayuda, pero no puede tapar una dependencia intensa."
                    break
            return score_sin_salario + delta
        if _label_rank(label_after) <= _label_rank(label_before) + 1:
            return score
        allowed_score = _score_cap_for_next_label(label_before)
        delta = float(allowed_score - score_sin_salario)
        if delta < 0:
            delta = 0.0
        for item in reversed(componentes_items):
            if item.get("bucket") == "salario":
                item["amount"] = round(float(delta), 2)
                item["label"] = "El salario ayuda, pero no puede tapar una solicitud crítica."
                break
        return score_sin_salario + delta


def evaluate_solicitud_atractivo(data: dict[str, Any]) -> dict[str, Any]:
    return SolicitudAtractivoService.evaluate(data)


def apply_solicitud_atractivo_to_model(solicitud: Any, *, now: datetime | None = None) -> dict[str, Any]:
    detalles = getattr(solicitud, "detalles_servicio", None)
    detalles = detalles if isinstance(detalles, dict) else {}
    payload = {
        "modalidad_trabajo": getattr(solicitud, "modalidad_trabajo", None),
        "horario": getattr(solicitud, "horario", None),
        "horario_tipo": detalles.get("horario_tipo"),
        "dias_trabajo": detalles.get("dias_trabajo"),
        "horario_hora_entrada": detalles.get("hora_entrada"),
        "horario_hora_salida": detalles.get("hora_salida"),
        "dormida_entrada": detalles.get("dormida_entrada"),
        "dormida_salida": detalles.get("dormida_salida"),
        "tipo_lugar": getattr(solicitud, "tipo_lugar", None),
        "habitaciones": getattr(solicitud, "habitaciones", None),
        "banos": getattr(solicitud, "banos", None),
        "pisos": detalles.get("pisos"),
        "adultos": getattr(solicitud, "adultos", None),
        "ninos": getattr(solicitud, "ninos", None),
        "edades_ninos": getattr(solicitud, "edades_ninos", None),
        "nota_cliente": getattr(solicitud, "nota_cliente", None),
        "descripcion": getattr(solicitud, "descripcion", None),
        "sueldo": getattr(solicitud, "sueldo", None),
        "envejeciente_tipo_cuidado": getattr(solicitud, "envejeciente_tipo_cuidado", None),
        "envejeciente_responsabilidades": getattr(solicitud, "envejeciente_responsabilidades", None),
        "funciones": getattr(solicitud, "funciones", None),
        "areas_comunes": getattr(solicitud, "areas_comunes", None),
        "detalles_servicio": detalles,
    }
    result = evaluate_solicitud_atractivo(payload)
    if hasattr(solicitud, "atractivo_score"):
        solicitud.atractivo_score = int(result["score"])
    if hasattr(solicitud, "atractivo_label"):
        solicitud.atractivo_label = str(result["label"])
    if hasattr(solicitud, "atractivo_motivos"):
        solicitud.atractivo_motivos = list(result["motivos"] or [])
    if hasattr(solicitud, "atractivo_version"):
        solicitud.atractivo_version = str(result["version"])
    if hasattr(solicitud, "atractivo_calculated_at"):
        solicitud.atractivo_calculated_at = now or datetime.utcnow()
    return result
