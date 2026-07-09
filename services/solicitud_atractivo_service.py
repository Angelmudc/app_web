from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from utils.child_age_parser import parse_child_age_summary
from utils.sueldo_sugerido import analyze_salary_suggestion, classify_schedule, parse_salary_amount


BASE_SCORE = 72
ATTRACTIVE_VERSION = "v1"

LABEL_MUY_ATRACTIVA = "Muy atractiva"
LABEL_ATRACTIVA = "Atractiva"
LABEL_REGULAR = "Regular"
LABEL_POCO = "Poco atractiva"
LABEL_DIFICIL = "Difícil"

HOUSEHOLD_FUNCTIONS = {"limpieza", "cocinar", "lavar", "planchar"}
ELDER_HEAVY_RESPONSIBILITIES = {"higiene", "pampers", "movilidad", "medicamentos"}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


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


def _clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))


def _score_label(score: int) -> str:
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


def _extract_schedule_context(data: dict[str, Any]) -> dict[str, Any]:
    detalles = data.get("detalles_servicio") if isinstance(data.get("detalles_servicio"), dict) else {}
    modalidad = _norm(data.get("modalidad_trabajo"))
    schedule_key, _ = classify_schedule(data)
    if not schedule_key:
        if "quincenal" in modalidad and "dormida" in modalidad:
            schedule_key = "cd_quincenal"
        elif "quincenal" in modalidad:
            schedule_key = "sd_quincenal"

    hora_entrada = data.get("horario_hora_entrada") or detalles.get("hora_entrada")
    hora_salida = data.get("horario_hora_salida") or detalles.get("hora_salida")
    start_min = _parse_time_to_minutes(hora_entrada)
    end_min = _parse_time_to_minutes(hora_salida)
    hours = None
    if start_min is not None and end_min is not None:
        if end_min <= start_min:
            end_min += 24 * 60
        hours = (end_min - start_min) / 60.0

    if schedule_key == "sd_l_v":
        mode_key = "sd_l_v"
    elif schedule_key == "cd_l_v":
        mode_key = "cd_l_v"
    elif schedule_key == "sd_l_s":
        mode_key = "sd_l_s"
    elif schedule_key == "cd_l_s":
        mode_key = "cd_l_s"
    elif schedule_key in {"sd_quincenal", "cd_quincenal"}:
        mode_key = schedule_key
    elif schedule_key in {"sd_fin_semana", "cd_fin_semana"}:
        mode_key = "fin_semana"
    else:
        mode_key = schedule_key or ""

    return {
        "schedule_key": schedule_key or "",
        "mode_key": mode_key or "",
        "hours": hours,
        "exit_minutes": end_min,
    }


def _top_motivos(items: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    ordered = sorted(
        [item for item in items if int(item.get("amount") or 0) != 0],
        key=lambda item: (abs(int(item.get("amount") or 0)), item.get("kind") != "penalty"),
        reverse=True,
    )
    return ordered[:limit]


@dataclass
class _Context:
    funciones: set[str]
    adults: int
    small_children: int
    older_children: int
    schedule_key: str
    mode_key: str
    hours: float | None
    exit_minutes: int | None
    elder_type: str
    elder_resp: set[str]
    solo_cuidado_ninos: bool
    solo_envejeciente_independiente: bool
    heavy_household: bool
    has_large_home: bool


class SolicitudAtractivoService:
    version = ATTRACTIVE_VERSION

    @classmethod
    def evaluate(cls, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data or {})
        motivos: list[dict[str, Any]] = []
        componentes_items: list[dict[str, Any]] = []
        score = BASE_SCORE

        def add_component(*, key: str, amount: int, label: str, kind: str, bucket: str) -> None:
            nonlocal score
            if not amount:
                return
            score += int(amount)
            row = {
                "key": key,
                "amount": int(amount),
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
        cls._apply_envejeciente(ctx, add_component)
        cls._apply_hogar(payload, ctx, add_component)
        critical_combos = cls._apply_combinadas(payload, ctx, add_component)
        cls._apply_bonificaciones(payload, ctx, add_component)

        score_sin_salario = _clamp_score(score)
        salario_component = cls._compute_salario_component(payload, score_sin_salario, critical_combos)
        if salario_component:
            add_component(
                key="salario",
                amount=salario_component["applied_amount"],
                label=salario_component["label"],
                kind="bonus" if salario_component["applied_amount"] >= 0 else "penalty",
                bucket="salario",
            )
            score = BASE_SCORE + sum(int(item["amount"]) for item in componentes_items)
            score = cls._apply_critical_label_cap(
                score=score,
                score_sin_salario=score_sin_salario,
                critical_combos=critical_combos,
                componentes_items=componentes_items,
            )

        final_score = _clamp_score(score)
        final_label = _score_label(final_score)
        main_motivos = _top_motivos(motivos, limit=4)
        componentes = {
            "base": BASE_SCORE,
            "items": componentes_items,
            "score_sin_salario": score_sin_salario,
            "critical_combinations": critical_combos,
            "salary_reference": salario_component.get("reference") if salario_component else None,
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
        child_summary = parse_child_age_summary(str(data.get("edades_ninos") or ""))
        small_children = int(child_summary.get("small_count") or 0)
        older_children = int(child_summary.get("big_count") or 0) + int(child_summary.get("teen_count") or 0)
        elder_type = _norm(data.get("envejeciente_tipo_cuidado"))
        elder_resp = {_norm(item) for item in _as_list(data.get("envejeciente_responsabilidades")) if _norm(item)}
        schedule = _extract_schedule_context(data)
        solo_cuidado_ninos = "ninos" in funciones and not funciones.intersection(HOUSEHOLD_FUNCTIONS | {"envejeciente"})
        solo_env_ind = funciones == {"envejeciente"} and elder_type == "independiente"
        heavy_household = bool(funciones.intersection({"limpieza", "cocinar", "lavar", "planchar"}))
        habitaciones = _to_int(data.get("habitaciones"), default=0)
        banos = _to_float(data.get("banos"), default=0.0)
        has_large_home = (habitaciones >= 4 and banos >= 4) or habitaciones >= 5 or banos >= 5
        return _Context(
            funciones=funciones,
            adults=adults,
            small_children=small_children,
            older_children=older_children,
            schedule_key=str(schedule["schedule_key"] or ""),
            mode_key=str(schedule["mode_key"] or ""),
            hours=schedule["hours"],
            exit_minutes=schedule["exit_minutes"],
            elder_type=elder_type,
            elder_resp=elder_resp,
            solo_cuidado_ninos=solo_cuidado_ninos,
            solo_envejeciente_independiente=solo_env_ind,
            heavy_household=heavy_household,
            has_large_home=has_large_home,
        )

    @staticmethod
    def _apply_modalidad(ctx: _Context, add_component) -> None:
        if ctx.mode_key == "sd_l_v":
            add_component(key="modalidad_sd_l_v", amount=6, label="Lunes a viernes suma atractivo.", kind="bonus", bucket="modalidad")
        elif ctx.mode_key == "cd_l_v":
            add_component(key="modalidad_cd_l_v", amount=3, label="Con dormida lunes a viernes mantiene buena atracción.", kind="bonus", bucket="modalidad")
        elif ctx.mode_key == "sd_l_s":
            add_component(key="modalidad_sd_l_s", amount=-8, label="Lunes a sábado baja atractivo.", kind="penalty", bucket="modalidad")
        elif ctx.mode_key == "cd_l_s":
            add_component(key="modalidad_cd_l_s", amount=-10, label="Con dormida lunes a sábado baja atractivo.", kind="penalty", bucket="modalidad")
        elif ctx.mode_key == "sd_quincenal":
            add_component(key="modalidad_sd_quincenal", amount=-18, label="Salida quincenal resta bastante atractivo.", kind="penalty", bucket="modalidad")
        elif ctx.mode_key == "cd_quincenal":
            add_component(key="modalidad_cd_quincenal", amount=-22, label="Con dormida con salida quincenal resta bastante atractivo.", kind="penalty", bucket="modalidad")
        elif ctx.mode_key == "fin_semana":
            add_component(key="modalidad_fin_semana", amount=-6, label="La modalidad de fin de semana reduce atractivo.", kind="penalty", bucket="modalidad")

    @staticmethod
    def _apply_horario(ctx: _Context, add_component) -> None:
        if not ctx.schedule_key.startswith("sd_"):
            return
        hours = ctx.hours
        if hours is not None:
            if hours <= 8:
                add_component(key="horario_8h", amount=3, label="Jornada de 8 horas o menos ayuda al atractivo.", kind="bonus", bucket="horario")
            elif 8 < hours <= 10:
                add_component(key="horario_10h", amount=-4, label="Más de 8 y hasta 10 horas baja atractivo.", kind="penalty", bucket="horario")
            elif 10 < hours <= 11:
                add_component(key="horario_11h", amount=-8, label="Más de 10 y hasta 11 horas baja atractivo.", kind="penalty", bucket="horario")
            elif hours > 11:
                add_component(key="horario_12h", amount=-12, label="Más de 11 horas baja atractivo.", kind="penalty", bucket="horario")

        if ctx.exit_minutes is not None:
            if ctx.exit_minutes >= (19 * 60):
                add_component(key="salida_tarde_6", amount=-2, label="Salida después de 6:00 PM penaliza.", kind="penalty", bucket="horario")
                add_component(key="salida_tarde_7", amount=-4, label="Salida después de 7:00 PM penaliza.", kind="penalty", bucket="horario")
            elif ctx.exit_minutes > (18 * 60):
                add_component(key="salida_tarde_6", amount=-2, label="Salida después de 6:00 PM penaliza.", kind="penalty", bucket="horario")

    @staticmethod
    def _apply_funciones(ctx: _Context, add_component) -> None:
        if "limpieza" in ctx.funciones:
            add_component(key="func_limpieza", amount=-6, label="Limpieza general baja atractivo.", kind="penalty", bucket="funciones")
        if "cocinar" in ctx.funciones:
            add_component(key="func_cocinar", amount=-4, label="Cocinar baja atractivo.", kind="penalty", bucket="funciones")
        if "lavar" in ctx.funciones:
            add_component(key="func_lavar", amount=-3, label="Lavar baja atractivo.", kind="penalty", bucket="funciones")
        if "planchar" in ctx.funciones:
            add_component(key="func_planchar", amount=-5, label="Planchar baja atractivo.", kind="penalty", bucket="funciones")

    @staticmethod
    def _apply_adultos(ctx: _Context, add_component) -> None:
        if ctx.adults == 4:
            add_component(key="adultos_4", amount=-4, label="Cuatro adultos en el hogar bajan atractivo.", kind="penalty", bucket="adultos")
        elif ctx.adults >= 5:
            add_component(key="adultos_5", amount=-7, label="Cinco o más adultos en el hogar bajan atractivo.", kind="penalty", bucket="adultos")

    @staticmethod
    def _apply_envejeciente(ctx: _Context, add_component) -> None:
        if "envejeciente" not in ctx.funciones:
            return
        if ctx.elder_type == "independiente":
            add_component(key="env_ind", amount=-6, label="Envejeciente independiente baja atractivo.", kind="penalty", bucket="envejeciente")
            return
        if ctx.elder_type == "encamado":
            add_component(key="env_enc", amount=-16, label="Envejeciente encamado baja mucho el atractivo.", kind="penalty", bucket="envejeciente")
            if ctx.elder_resp.intersection(ELDER_HEAVY_RESPONSIBILITIES):
                add_component(
                    key="env_enc_extra",
                    amount=-4,
                    label="Encamado con higiene, pampers, movilidad o medicación penaliza extra.",
                    kind="penalty",
                    bucket="envejeciente",
                )

    @staticmethod
    def _apply_hogar(data: dict[str, Any], ctx: _Context, add_component) -> None:
        hab = _to_int(data.get("habitaciones"), default=0)
        banos = _to_float(data.get("banos"), default=0.0)
        pisos = _norm(data.get("pisos") or data.get("cantidad_pisos"))
        if hab >= 5 or banos >= 5:
            add_component(key="hogar_5", amount=-10, label="Cinco o más habitaciones o baños bajan atractivo.", kind="penalty", bucket="hogar")
        elif hab >= 4 and banos >= 4:
            add_component(key="hogar_4_4", amount=-8, label="Casa de 4 habitaciones y 4 baños baja atractivo.", kind="penalty", bucket="hogar")
        elif hab >= 3 and banos >= 3:
            add_component(key="hogar_3_3", amount=-3, label="Tres habitaciones y tres baños restan atractivo.", kind="penalty", bucket="hogar")

        if pisos == "3+":
            add_component(key="hogar_3_pisos", amount=-4, label="Tres o más pisos bajan atractivo.", kind="penalty", bucket="hogar")

    @classmethod
    def _apply_combinadas(cls, data: dict[str, Any], ctx: _Context, add_component) -> list[str]:
        critical: list[str] = []
        has_limpieza = "limpieza" in ctx.funciones
        has_cocinar = "cocinar" in ctx.funciones
        has_lavar = "lavar" in ctx.funciones
        has_planchar = "planchar" in ctx.funciones

        if ctx.small_children > 0:
            child_penalty = 0
            if ctx.solo_cuidado_ninos:
                child_penalty = -1 if ctx.small_children == 1 else -2
            elif has_limpieza and has_cocinar and has_lavar:
                child_penalty = -15
                critical.append("nino_pequeno_limpieza_cocinar_lavar")
            elif has_limpieza and has_cocinar:
                child_penalty = -11
            elif has_limpieza:
                child_penalty = -8
            if child_penalty and ctx.small_children >= 2 and not ctx.solo_cuidado_ninos:
                child_penalty -= 2
            add_component(
                key="combo_ninos_pequenos",
                amount=child_penalty,
                label="Niños pequeños con la carga actual reducen atractivo.",
                kind="penalty" if child_penalty < 0 else "bonus",
                bucket="combinadas",
            )

        if ctx.elder_type == "encamado":
            elder_combo = 0
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
        if has_limpieza and hab >= 4 and banos >= 4:
            add_component(
                key="combo_hogar_grande_limpieza",
                amount=-6,
                label="Hogar 4/4 con limpieza agrega carga extra.",
                kind="penalty",
                bucket="combinadas",
            )
        if ctx.adults >= 4 and has_limpieza and has_lavar:
            add_component(
                key="combo_adultos_limpieza_lavar",
                amount=-4,
                label="Varios adultos con limpieza y lavar aumentan la carga.",
                kind="penalty",
                bucket="combinadas",
            )
        if ctx.has_large_home and ctx.hours is not None and ctx.hours > 10:
            add_component(
                key="combo_casa_grande_horas",
                amount=-4,
                label="Casa grande con jornada mayor de 10 horas penaliza extra.",
                kind="penalty",
                bucket="combinadas",
            )
        if ctx.mode_key == "sd_l_s" and ctx.hours is not None and ctx.hours > 10:
            add_component(
                key="combo_l_s_10h",
                amount=-6,
                label="Lunes a sábado con más de 10 horas penaliza extra.",
                kind="penalty",
                bucket="combinadas",
            )
        strong_house_load = has_limpieza and (has_cocinar or has_lavar or ctx.has_large_home or ctx.adults >= 4)
        if ctx.mode_key in {"sd_quincenal", "cd_quincenal"}:
            critical.append("quincenal")
            if strong_house_load:
                add_component(
                    key="combo_quincenal_carga_fuerte",
                    amount=-6,
                    label="Quincenal con carga fuerte de hogar penaliza extra.",
                    kind="penalty",
                    bucket="combinadas",
                )
        return sorted(set(critical))

    @staticmethod
    def _apply_bonificaciones(data: dict[str, Any], ctx: _Context, add_component) -> None:
        hab = _to_int(data.get("habitaciones"), default=0)
        banos = _to_float(data.get("banos"), default=0.0)
        if ctx.solo_cuidado_ninos:
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
        if ctx.heavy_household and hab <= 3 and banos <= 2:
            add_component(
                key="bonus_hogar_compacto",
                amount=2,
                label="Hogar compacto ayuda al atractivo.",
                kind="bonus",
                bucket="bonificaciones",
            )

    @classmethod
    def _compute_salario_component(
        cls,
        data: dict[str, Any],
        score_sin_salario: int,
        critical_combos: list[str],
    ) -> dict[str, Any] | None:
        offered = parse_salary_amount(data.get("sueldo"))
        if not offered:
            return None
        salary_ref_payload = analyze_salary_suggestion(data)
        if salary_ref_payload.get("can_suggest"):
            ref_min = int(salary_ref_payload.get("suggested_min") or 0)
            ref_max = int(salary_ref_payload.get("suggested_max") or ref_min)
            reference = int(round((ref_min + ref_max) / 2)) if ref_max else ref_min
        else:
            reference = parse_salary_amount(data.get("sueldo")) or 0
        if reference <= 0:
            return None
        ratio = (offered / reference) if reference else 1.0
        if ratio >= 1.15:
            raw_amount = 8
            label = "El sueldo ofrecido está 15% o más por encima del sugerido."
        elif ratio >= 1.05:
            raw_amount = 5
            label = "El sueldo ofrecido está por encima del sugerido."
        elif ratio >= 0.95:
            raw_amount = 2
            label = "El sueldo ofrecido está cerca del sugerido."
        elif ratio >= 0.85:
            raw_amount = 0
            label = "El sueldo ofrecido está dentro de un rango aceptable."
        else:
            raw_amount = -4
            label = "El sueldo ofrecido está por debajo del sugerido."

        applied_amount = raw_amount
        if applied_amount > 0 and score_sin_salario <= 30:
            applied_amount = min(applied_amount, 1)
        elif applied_amount > 0 and score_sin_salario <= 45:
            applied_amount = min(applied_amount, 3)

        return {
            "raw_amount": raw_amount,
            "applied_amount": applied_amount,
            "label": label,
            "reference": {
                "offered": offered,
                "reference_amount": reference,
                "ratio": round(ratio, 4),
                "critical_combos": list(critical_combos),
            },
        }

    @classmethod
    def _apply_critical_label_cap(
        cls,
        *,
        score: int,
        score_sin_salario: int,
        critical_combos: list[str],
        componentes_items: list[dict[str, Any]],
    ) -> int:
        if not critical_combos:
            return score
        label_before = _score_label(score_sin_salario)
        label_after = _score_label(score)
        if _label_rank(label_after) <= _label_rank(label_before) + 1:
            return score
        allowed_score = _score_cap_for_next_label(label_before)
        delta = allowed_score - score_sin_salario
        if delta < 0:
            delta = 0
        for item in reversed(componentes_items):
            if item.get("bucket") == "salario":
                item["amount"] = delta
                item["label"] = "El salario ayuda, pero no puede tapar una solicitud crítica."
                break
        return score_sin_salario + delta


def evaluate_solicitud_atractivo(data: dict[str, Any]) -> dict[str, Any]:
    return SolicitudAtractivoService.evaluate(data)


def apply_solicitud_atractivo_to_model(solicitud: Any, *, now: datetime | None = None) -> dict[str, Any]:
    payload = {
        "modalidad_trabajo": getattr(solicitud, "modalidad_trabajo", None),
        "horario": getattr(solicitud, "horario", None),
        "horario_hora_entrada": ((getattr(solicitud, "detalles_servicio", None) or {}) if isinstance(getattr(solicitud, "detalles_servicio", None), dict) else {}).get("hora_entrada"),
        "horario_hora_salida": ((getattr(solicitud, "detalles_servicio", None) or {}) if isinstance(getattr(solicitud, "detalles_servicio", None), dict) else {}).get("hora_salida"),
        "tipo_lugar": getattr(solicitud, "tipo_lugar", None),
        "habitaciones": getattr(solicitud, "habitaciones", None),
        "banos": getattr(solicitud, "banos", None),
        "pisos": ((getattr(solicitud, "detalles_servicio", None) or {}) if isinstance(getattr(solicitud, "detalles_servicio", None), dict) else {}).get("pisos"),
        "adultos": getattr(solicitud, "adultos", None),
        "ninos": getattr(solicitud, "ninos", None),
        "edades_ninos": getattr(solicitud, "edades_ninos", None),
        "sueldo": getattr(solicitud, "sueldo", None),
        "envejeciente_tipo_cuidado": getattr(solicitud, "envejeciente_tipo_cuidado", None),
        "envejeciente_responsabilidades": getattr(solicitud, "envejeciente_responsabilidades", None),
        "funciones": getattr(solicitud, "funciones", None),
        "areas_comunes": getattr(solicitud, "areas_comunes", None),
        "detalles_servicio": getattr(solicitud, "detalles_servicio", None),
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
