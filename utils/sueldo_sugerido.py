from __future__ import annotations

import re
from typing import Any
import math


NO_SUGGESTION_MESSAGE = (
    "No tenemos suficiente informacion clara para sugerir un sueldo de forma responsable.\n\n"
    "Esta solicitud necesita revision de servicio al cliente para orientarte mejor "
    "segun el tipo de servicio, horario y responsabilidades."
)

SOFT_INCOMPLETE_MESSAGE = "Completa modalidad, horario y funciones para ver una sugerencia."
CHILDREN_INCOMPLETE_MESSAGE = "Completa la cantidad o edades de los niños para ver una sugerencia de sueldo."
ADULTS_INCOMPLETE_MESSAGE = "Completa la cantidad de adultos del hogar para ver una sugerencia de sueldo."
PLACE_INCOMPLETE_MESSAGE = "Selecciona si el servicio será en casa o apartamento para ver una sugerencia de sueldo."

BASE_SALARY_MAP = {
    "sd_1_dia": 5000,
    "sd_2_dias": 9500,
    "sd_3_dias": 12500,
    "sd_4_dias": 14500,
    "sd_l_v": 16000,
    "sd_l_s": 17000,
    "sd_fin_semana": 11000,
    "cd_l_v": 20000,
    "cd_l_s": 21000,
    "cd_quincenal": 25000,
    "cd_fin_semana": 14000,
}

SD_PROFILE_BASE = {
    1: {"ninera": 4500, "domestica": 5000, "envejeciente": 5500, "mixto": 6500, "mixto_alto": 7000},
    2: {"ninera": 8000, "domestica": 9000, "envejeciente": 10000, "mixto": 10000, "mixto_alto": 11000},
    3: {"ninera": 10500, "domestica": 12500, "envejeciente": 13500, "mixto": 14500, "mixto_alto": 15500},
    4: {"ninera": 12500, "domestica": 14500, "envejeciente": 16000, "mixto": 16000, "mixto_alto": 17000},
    5: {"ninera": 15000, "domestica": 18000, "envejeciente": 19000, "mixto": 20000, "mixto_alto": 21000},
}


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x or "").strip()]
    raw = str(v).strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _to_int(v: Any, default: int = 0) -> int:
    try:
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return default
        return int(n)
    except Exception:
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return default
        return n
    except Exception:
        return default


def parse_salary_amount(raw: Any) -> int | None:
    txt = str(raw or "").strip()
    if not txt:
        return None
    digits = "".join(ch for ch in txt if ch.isdigit())
    if not digits:
        return None
    try:
        amount = int(digits)
    except Exception:
        return None
    return amount if amount > 0 else None


def classify_schedule(data: dict[str, Any]) -> tuple[str | None, str]:
    modalidad = _norm(data.get("modalidad_trabajo"))
    horario = _norm(data.get("horario"))
    if not modalidad:
        return None, "incompleta"

    # Compatibilidad con formularios donde modalidad y horario llegan separados.
    # Ejemplo: modalidad="salida", horario="L-V 8:00am-5:00pm".
    expanded_modalidad = modalidad
    if modalidad in {"salida", "dormida"}:
        horario_hint = horario
        if "l-v" in horario_hint:
            horario_hint = horario_hint.replace("l-v", "lunes a viernes")
        if "l-s" in horario_hint:
            horario_hint = horario_hint.replace("l-s", "lunes a sabado")
        if "sabado y domingo" in horario_hint:
            horario_hint = horario_hint.replace("sabado y domingo", "fin de semana")
        expanded_modalidad = (
            f"salida diaria {horario_hint}" if modalidad == "salida"
            else f"con dormida {horario_hint}"
        ).strip()

    patterns = [
        (r"con\s+dormida.*lunes\s*a\s*viernes", "cd_l_v"),
        (r"con\s+dormida.*lunes\s*a\s*s[áa]bado", "cd_l_s"),
        (r"quincenal|salida\s+quincenal", "cd_quincenal"),
        (r"con\s+dormida.*fin\s*de\s*semana|con\s+dormida.*s[áa]bado\s*y\s*domingo", "cd_fin_semana"),
        (r"salida\s+diaria.*1\s*d[ií]a|salida\s+diaria.*1\s*d[ií]a\s*a\s*la\s*semana", "sd_1_dia"),
        (r"salida\s+diaria.*2\s*d[ií]as|salida\s+diaria.*2\s*d[ií]as\s*a\s*la\s*semana", "sd_2_dias"),
        (r"salida\s+diaria.*3\s*d[ií]as|salida\s+diaria.*3\s*d[ií]as\s*a\s*la\s*semana", "sd_3_dias"),
        (r"salida\s+diaria.*4\s*d[ií]as|salida\s+diaria.*4\s*d[ií]as\s*a\s*la\s*semana", "sd_4_dias"),
        (r"salida\s+diaria.*lunes\s*a\s*viernes", "sd_l_v"),
        (r"salida\s+diaria.*lunes\s*a\s*s[áa]bado", "sd_l_s"),
        (r"salida\s+diaria.*fin\s*de\s*semana|salida\s+diaria.*s[áa]bado\s*y\s*domingo|salida\s+diaria.*viernes\s*a\s*lunes", "sd_fin_semana"),
    ]
    for pattern, key in patterns:
        if re.search(pattern, expanded_modalidad):
            return key, "ok"
    if "otro" in modalidad:
        return None, "ambigua"
    return None, "ambigua"


def _service_profile(funciones: list[str]) -> str:
    has_ninos = "ninos" in funciones
    has_envejeciente = "envejeciente" in funciones
    has_limpieza = "limpieza" in funciones
    has_household_light = any(f in funciones for f in ("cocinar", "lavar", "planchar"))

    if has_ninos and has_envejeciente:
        return "mixto_alto"
    if has_envejeciente and has_limpieza:
        return "mixto_alto"
    if has_ninos and has_limpieza:
        return "mixto"
    if has_ninos:
        return "ninera"
    if has_envejeciente:
        return "envejeciente"
    # cocinar/lavar/planchar sin ninos ni envejeciente se mantiene como domestica.
    if has_household_light:
        return "domestica"
    return "domestica"


def _sd_days_from_schedule(schedule_key: str) -> int | None:
    if schedule_key == "sd_l_v":
        return 5
    if schedule_key == "sd_1_dia":
        return 1
    if schedule_key == "sd_2_dias":
        return 2
    if schedule_key == "sd_3_dias":
        return 3
    if schedule_key == "sd_4_dias":
        return 4
    return None


def _base_salary_for_schedule(schedule_key: str, funciones: list[str]) -> tuple[int, str]:
    # Con dormida usa base fija por modalidad (no por perfil).
    if schedule_key.startswith("cd_"):
        return BASE_SALARY_MAP[schedule_key], "con_dormida_fija"
    if schedule_key.startswith("sd_"):
        days = _sd_days_from_schedule(schedule_key)
        profile = _service_profile(funciones)
        if days in SD_PROFILE_BASE:
            return SD_PROFILE_BASE[days][profile], profile
    return BASE_SALARY_MAP[schedule_key], "general"


def classify_house_size(data: dict[str, Any], funciones: list[str]) -> tuple[int, list[str], str]:
    tipo_lugar = _norm(data.get("tipo_lugar"))
    hab = _to_int(data.get("habitaciones"), default=0)
    banos = _to_float(data.get("banos"), default=0.0)
    pisos = _norm(data.get("pisos") or data.get("cantidad_pisos"))
    areas = _as_list(data.get("areas_comunes"))
    has_all_areas = any(_norm(a) == "todas_anteriores" for a in areas)
    some_areas_count = len([a for a in areas if _norm(a) not in {"otro", "todas_anteriores"}])
    two_levels = _norm(data.get("dos_pisos")) in {"true", "1", "y"}
    ajustes = 0
    motivos: list[str] = []
    nivel = "normal"

    if "limpieza" not in funciones:
        return 0, motivos, nivel

    if tipo_lugar in {"casa", "apto"}:
        # Hogar grande: 3+ habitaciones y 3+ baños.
        is_large_home = hab >= 3 and banos >= 3
        if is_large_home:
            if tipo_lugar == "casa":
                ajustes += 1000
                nivel = "media"
                motivos.append("Casa grande por tamaño del hogar (3+ habitaciones y 3+ baños).")
            elif tipo_lugar == "apto":
                ajustes += 500
                nivel = "media"
                motivos.append("Apartamento grande por tamaño del hogar (3+ habitaciones y 3+ baños).")

        # Reglas adicionales de carga real (más allá de tipo/tamaño base).
        if pisos == "3+":
            ajustes += 1500
            nivel = "media"
            motivos.append("La vivienda tiene más de un nivel.")
        elif two_levels and hab >= 5:
            ajustes += 1000
            nivel = "media"
            motivos.append("La vivienda tiene más de un nivel.")

        if has_all_areas:
            ajustes += 2000
            nivel = "media"
            motivos.append("Incluye varias áreas comunes del hogar.")
        elif some_areas_count >= 4:
            ajustes += 1000
            motivos.append("Incluye varias áreas comunes del hogar.")
    return ajustes, motivos, nivel


def classify_child_load(data: dict[str, Any], funciones: list[str]) -> tuple[int, list[str], str]:
    ninos = _to_int(data.get("ninos"), default=0)
    edades = _norm(data.get("edades_ninos"))
    ajustes = 0
    motivos: list[str] = []
    nivel = "normal"
    if "ninos" not in funciones or ninos <= 0:
        return ajustes, motivos, nivel

    ages = [int(x) for x in re.findall(r"\b(\d{1,2})\b", edades)]
    small = len([a for a in ages if a <= 5])
    has_older_only = bool(ninos > 0 and ages and small == 0 and all(a >= 6 for a in ages))
    known_ages = bool(ages)
    if small == 0 and ninos > 0 and not ages:
        small = 1

    if has_older_only:
        motivos.append("Los niños indicados son mayores, por lo que el cuidado suele ser más de supervisión.")
        return 0, motivos, "normal"

    if small > 0:
        # Regla calibrada: RD$1,000 por cada niño de 5 años o menos.
        ajustes += 1000 * small
        nivel = "media" if small <= 2 else "alta"
        motivos.append("Los niños pequeños requieren mayor atención y responsabilidad.")
    return ajustes, motivos, nivel


def classify_elder_care(data: dict[str, Any], funciones: list[str]) -> tuple[int, list[str], str, list[str]]:
    if "envejeciente" not in funciones:
        return 0, [], "normal", []
    tipo = _norm(data.get("envejeciente_tipo_cuidado"))
    resp = _as_list(data.get("envejeciente_responsabilidades"))
    solo_acomp = _norm(data.get("envejeciente_solo_acompanamiento")) in {"1", "true", "y", "yes", "si"}
    ajustes = 0
    motivos: list[str] = []
    warnings: list[str] = []
    nivel = "normal"
    if tipo in {"independiente", "encamado"}:
        strong_resp = any(_norm(r) in {"pampers", "higiene", "comida", "medicamentos", "movilidad"} for r in resp)
        if solo_acomp or strong_resp or tipo == "encamado":
            ajustes = 1500
            nivel = "media"
        else:
            ajustes = 1000
            nivel = "media"
        motivos.append("Cuidado de envejeciente requiere atención y responsabilidad adicional.")
    return ajustes, motivos, nivel, warnings


def _horario_adjustments(data: dict[str, Any], schedule_key: str) -> tuple[int, list[str], list[str]]:
    if not schedule_key.startswith("sd_"):
        return 0, [], []
    detalles = data.get("detalles_servicio") if isinstance(data.get("detalles_servicio"), dict) else {}
    h_in = _norm(data.get("horario_hora_entrada") or (detalles or {}).get("hora_entrada"))
    h_out = _norm(data.get("horario_hora_salida") or (detalles or {}).get("hora_salida"))
    horario = _norm(data.get("horario"))
    motivos: list[str] = []
    warnings: list[str] = []
    adj = 0

    def _to_24h(txt: str) -> int | None:
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", txt)
        if not m:
            return None
        hour = int(m.group(1))
        ampm = _norm(m.group(3))
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return hour

    start = _to_24h(h_in or horario)
    end = _to_24h(h_out or horario[horario.rfind("a") + 1 :] if " a " in horario else "")
    if start is None or end is None:
        return adj, motivos, warnings
    if end <= start:
        end += 12
    hours = end - start
    if hours == 10:
        adj += 1000
        motivos.append("Jornada de 10 horas.")
    elif hours == 11:
        adj += 2000
        motivos.append("Jornada de 11 horas.")
    elif hours >= 12:
        adj += 3000
        motivos.append("Jornada de 12+ horas.")
        warnings.append("Horario extendido: solicitud mas exigente de lo normal.")

    if end > 18:
        adj += 500
        motivos.append("Salida despues de 6:00 PM.")
    if end > 19:
        adj += 1000
        motivos.append("Salida despues de 7:00 PM.")
        warnings.append("Horario de salida tarde puede dificultar cobertura.")
    return adj, motivos, warnings


def _offer_status(client_salary: int | None, suggested_min: int) -> str:
    if client_salary is None:
        return "sin_sueldo"
    if client_salary >= suggested_min:
        return "competitiva"
    gap = suggested_min - client_salary
    if gap <= 2000:
        return "baja"
    return "muy_baja"


def _format_rd(amount: int) -> str:
    return f"{int(amount):,}".replace(",", ",")


def _human_schedule_from_key(schedule_key: str) -> str:
    mapping = {
        "sd_1_dia": "salida diaria de 1 día a la semana",
        "sd_2_dias": "salida diaria de 2 días a la semana",
        "sd_3_dias": "salida diaria de 3 días a la semana",
        "sd_4_dias": "salida diaria de 4 días a la semana",
        "sd_l_v": "salida diaria de lunes a viernes",
        "sd_l_s": "salida diaria de lunes a sábado",
        "sd_fin_semana": "salida diaria de fin de semana",
        "cd_l_v": "con dormida de lunes a viernes",
        "cd_l_s": "con dormida de lunes a sábado",
        "cd_quincenal": "con dormida con salida quincenal",
        "cd_fin_semana": "con dormida de fin de semana",
    }
    return mapping.get(schedule_key, "modalidad y horario seleccionados")


def _reason_bullets(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("adjustments") or []
    labels = []
    for item in raw:
        if isinstance(item, dict):
            txt = str(item.get("label") or "").strip()
        else:
            txt = str(item or "").strip()
        if txt:
            labels.append(txt)
    out: list[str] = []
    seen: set[str] = set()
    for txt in labels:
        n = _norm(txt)
        bullet = ""
        if "incluye planchado" in n:
            bullet = "Incluye planchado dentro de las funciones del hogar."
        elif "casa muy grande" in n or "casa grande" in n or "casa mediana" in n or "apartamento amplio" in n:
            bullet = "El tamaño del hogar y sus espacios también influyen en la carga."
        elif "areas comunes" in n:
            bullet = "Hay varias áreas comunes que aumentan la carga diaria."
        elif "vivienda tiene mas de un nivel" in n or "vivienda tiene más de un nivel" in n:
            bullet = "La vivienda tiene más de un nivel."
        elif "jornada de 10 horas" in n or "jornada de 11 horas" in n or "jornada de 12+ horas" in n:
            bullet = "El horario es extendido."
        elif "salida despues de 6:00 pm" in n or "salida despues de 7:00 pm" in n:
            bullet = "La hora de salida es tarde."
        elif "nino pequeno" in n or "ninos pequenos" in n:
            bullet = "El cuidado de niños pequeños requiere más atención y responsabilidad."
        elif "ninos pequenos requieren mayor atencion" in n or "niños pequeños requieren mayor atención" in n:
            bullet = "Los niños pequeños requieren mayor atención y responsabilidad."
        elif "ninos mayores" in n:
            bullet = "Los niños indicados son mayores, por lo que el cuidado suele ser más de supervisión que de atención directa."
        elif "supervision" in n and "atencion directa" in n and "ninos" in n:
            bullet = "Los niños indicados son mayores, por lo que el cuidado suele ser más de supervisión que de atención directa."
        elif "cuidado suele ser mas de supervision" in n or "cuidado suele ser más de supervisión" in n:
            bullet = "Los niños indicados son mayores, por lo que el cuidado suele ser más de supervisión."
        elif "envejeciente encamado" in n:
            bullet = "Incluye cuidado de envejeciente encamado, que exige mayor responsabilidad."
        elif "envejeciente independiente" in n:
            bullet = "Incluye cuidado de envejeciente con acompañamiento o supervisión."
        elif "cuidado de envejeciente requiere atencion y responsabilidad adicional" in n or "cuidado de envejeciente requiere atención y responsabilidad adicional" in n:
            bullet = "Cuidado de envejeciente requiere atención y responsabilidad adicional."
        elif "4 o más adultos" in n or "4 o mas adultos" in n or "más de 4 adultos" in n or "mas de 4 adultos" in n:
            bullet = "Hay 4 o más adultos en el hogar, lo que aumenta la carga de limpieza y lavado. Se recomienda mejorar ligeramente la oferta."
        elif "6+ adultos" in n or "5+ adultos" in n:
            bullet = "Es un hogar con varios adultos, lo que incrementa tareas de apoyo."
        elif "modalidad clasificada" in n:
            raw_key = txt.split(":", 1)[1].strip(" .") if ":" in txt else ""
            bullet = f"Modalidad: {_human_schedule_from_key(raw_key)}."
        elif "perfil base de servicio" in n:
            if "ninera" in n:
                bullet = "El rango parte del servicio de niñera según la modalidad y días seleccionados."
            elif "cuidado de envejeciente" in n:
                bullet = "El rango parte de un servicio de cuidado de envejeciente según la modalidad y días seleccionados."
            elif "domestica y ninera" in n:
                bullet = "La solicitud combina cuidado de niños con tareas del hogar."
            else:
                bullet = "El rango parte del tipo de servicio y la modalidad seleccionada."
        elif "dias por semana considerados" in n:
            if ":" in txt:
                days = txt.split(":", 1)[1].strip(" .")
                bullet = f"Días de trabajo considerados: {days}."
        elif "perfil combinado: domestica + cuidado de envejeciente" in n:
            bullet = "La solicitud combina tareas del hogar con cuidado de envejeciente."
        if bullet and bullet not in seen:
            seen.add(bullet)
            out.append(bullet)
    if not out:
        out.append("Modalidad, horario y funciones seleccionadas.")
    return out[:5]


def _sanitize_client_text(text: str) -> str:
    out = str(text or "")
    # Eliminar cualquier remanente técnico visible para cliente.
    out = re.sub(r"\b(?:cd|sd)_[a-z0-9_]+\b", "modalidad seleccionada", out, flags=re.IGNORECASE)
    out = re.sub(r"modalidad\s+clasificada\s*:\s*[^.\n]+\.?", "Modalidad seleccionada.", out, flags=re.IGNORECASE)
    out = re.sub(r"\bclasificada\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"[ ]{2,}", " ", out)
    return out.strip()


def build_salary_message(payload: dict[str, Any]) -> str:
    if not payload.get("can_suggest"):
        return str(payload.get("reason_no_suggestion") or SOFT_INCOMPLETE_MESSAGE)
    min_s = int(payload.get("suggested_min") or 0)
    max_s = int(payload.get("suggested_max") or 0)
    status = payload.get("offer_status")
    load_level = _norm(payload.get("load_level"))
    reasons = list(payload.get("public_reasons") or [])
    if not reasons:
        reasons = _reason_bullets(payload)
    title = f"Rango sugerido: RD${_format_rd(min_s)} - RD${_format_rd(max_s)} mensual"
    intro = f"Para este tipo de solicitud, el sueldo suele estar entre RD${_format_rd(min_s)} y RD${_format_rd(max_s)} mensual + ayuda de pasaje."
    why_block = "¿Por qué este rango?\n" + "\n".join(f"- {r}" for r in reasons)

    if load_level in {"normal", "media"}:
        warning_msg = "Ofrecer menos puede dificultar encontrar una candidata disponible o adecuada."
    else:
        warning_msg = "Por el nivel de exigencia, ofrecer menos puede dificultar encontrar una candidata disponible o adecuada."

    status_msg = {
        "competitiva": "Tu oferta actual luce competitiva para esta solicitud.",
        "baja": "Tu oferta actual parece algo baja frente a la carga estimada.",
        "muy_baja": "Tu oferta actual luce bastante por debajo de lo que normalmente se requiere.",
        "sin_sueldo": "Aún no has indicado sueldo; este rango te sirve como referencia.",
    }.get(status, "")
    closing = (
        "Puedes ajustar el monto según tu presupuesto, pero este rango aumenta las probabilidades de encontrar personal.\n\n"
        "También recomendamos marcar la opción de ayuda para el pasaje, ya que mejora el atractivo de la oferta."
    )

    parts = [title, intro, why_block, warning_msg, closing]
    if status_msg:
        parts.insert(3, status_msg)
    return _sanitize_client_text("\n\n".join(parts))


def analyze_salary_suggestion(data: dict[str, Any]) -> dict[str, Any]:
    base_out = {
        "can_suggest": False,
        "reason_no_suggestion": "",
        "base_salary": None,
        "adjustments": [],
        "suggested_min": None,
        "suggested_max": None,
        "load_level": None,
        "warnings": [],
        "offer_status": "sin_sueldo",
    }
    funciones = [_norm(x) for x in _as_list(data.get("funciones"))]
    principal_funcs = {"ninos", "envejeciente", "limpieza", "cocinar", "lavar", "planchar"}
    has_principal_function = any(f in principal_funcs for f in funciones)
    tipo_lugar = _norm(data.get("tipo_lugar"))
    schedule_key, schedule_state = classify_schedule(data)
    horario = _norm(data.get("horario"))
    if schedule_state == "incompleta" or not has_principal_function:
        out = dict(base_out)
        out["reason_no_suggestion"] = SOFT_INCOMPLETE_MESSAGE
        out["message"] = SOFT_INCOMPLETE_MESSAGE
        return out

    if not tipo_lugar:
        out = dict(base_out)
        out["reason_no_suggestion"] = PLACE_INCOMPLETE_MESSAGE
        out["message"] = PLACE_INCOMPLETE_MESSAGE
        return out

    if (
        schedule_state == "ambigua"
        or tipo_lugar in {"oficina", "otro"}
        or "otro" in funciones
        or not horario
    ):
        out = dict(base_out)
        out["reason_no_suggestion"] = NO_SUGGESTION_MESSAGE
        out["message"] = NO_SUGGESTION_MESSAGE
        return out

    # Si incluye cuidado de niños, se requiere al menos cantidad o edades.
    if "ninos" in funciones:
        ninos_count = _to_int(data.get("ninos"), default=0)
        edades_txt = _norm(data.get("edades_ninos"))
        if ninos_count <= 0 and not edades_txt:
            out = dict(base_out)
            out["reason_no_suggestion"] = CHILDREN_INCOMPLETE_MESSAGE
            out["message"] = CHILDREN_INCOMPLETE_MESSAGE
            return out

    # Si hay funciones del hogar donde aplica adultos, exigir ese dato.
    # Adultos aplica principalmente cuando hay limpieza del hogar.
    if "limpieza" in funciones:
        adultos_raw = data.get("adultos")
        if adultos_raw is None or str(adultos_raw).strip() == "":
            out = dict(base_out)
            out["reason_no_suggestion"] = ADULTS_INCOMPLETE_MESSAGE
            out["message"] = ADULTS_INCOMPLETE_MESSAGE
            return out
    if not schedule_key or schedule_key not in BASE_SALARY_MAP:
        out = dict(base_out)
        out["reason_no_suggestion"] = NO_SUGGESTION_MESSAGE
        out["message"] = NO_SUGGESTION_MESSAGE
        return out

    base, profile = _base_salary_for_schedule(schedule_key, funciones)
    ajustes_total = 0
    motivos: list[str] = [f"Modalidad clasificada: {schedule_key}."]
    if schedule_key.startswith("sd_"):
        profile_txt = {
            "ninera": "ninera",
            "domestica": "domestica",
            "envejeciente": "cuidado de envejeciente",
            "mixto": "domestica y ninera",
            "mixto_alto": "cuidado mixto (ninos/envejeciente + hogar)",
            "general": "general",
        }.get(profile, "general")
        days = _sd_days_from_schedule(schedule_key)
        if days:
            motivos.append(f"Perfil base de servicio: {profile_txt}.")
            motivos.append(f"Dias por semana considerados: {days}.")
    if "envejeciente" in funciones and "limpieza" in funciones:
        motivos.append("Perfil combinado: domestica + cuidado de envejeciente.")
    warnings: list[str] = []
    levels = ["normal"]

    if "planchar" in funciones:
        ajustes_total += 1500
        motivos.append("Incluye planchado.")
        levels.append("media")

    house_adj, house_mot, house_lvl = classify_house_size(data, funciones)
    ajustes_total += house_adj
    motivos.extend(house_mot)
    levels.append(house_lvl)

    child_adj, child_mot, child_lvl = classify_child_load(data, funciones)
    ajustes_total += child_adj
    motivos.extend(child_mot)
    levels.append(child_lvl)
    if any("nino pequeno" in _norm(m) for m in child_mot) and "limpieza" in funciones:
        warnings.append("Ninos pequenos con limpieza general puede volver la solicitud mas exigente.")

    elder_adj, elder_mot, elder_lvl, elder_warn = classify_elder_care(data, funciones)
    ajustes_total += elder_adj
    motivos.extend(elder_mot)
    warnings.extend(elder_warn)
    levels.append(elder_lvl)

    adultos = _to_int(data.get("adultos"), default=0)
    if adultos >= 4 and ("limpieza" in funciones and "lavar" in funciones):
        ajustes_total += 1000
        motivos.append(
            "Hay 4 o más adultos en el hogar, lo que aumenta la carga de limpieza y lavado. Se recomienda mejorar ligeramente la oferta."
        )
        levels.append("media")

    h_adj, h_mot, h_warn = _horario_adjustments(data, schedule_key)
    ajustes_total += h_adj
    motivos.extend(h_mot)
    warnings.extend(h_warn)

    suggested_min = base + ajustes_total
    spread = 5000 if any(l == "muy_alta" for l in levels) else 3000 if any(l == "alta" for l in levels) else 2000
    suggested_max = suggested_min + spread

    # Cap moderado para salida diaria L-V cuando la unica carga fuerte es horario extendido.
    strong_non_schedule = (
        house_lvl in {"alta", "muy_alta"}
        or child_lvl in {"alta", "muy_alta"}
        or elder_lvl in {"alta", "muy_alta"}
        or adultos >= 5
        or {"limpieza", "cocinar", "lavar", "planchar"}.issubset(set(funciones))
        or ("planchar" in funciones and len(funciones) >= 3)
    )
    if schedule_key == "sd_l_v" and not strong_non_schedule and suggested_max > 21000:
        suggested_max = 21000
        if suggested_min > suggested_max:
            suggested_min = max(base, suggested_max - 1500)

    # Si solo hay ninos mayores (sin ninos pequenos), evitar picos exagerados.
    has_small_children_signal = any("nino pequeno" in _norm(m) or "ninos pequenos" in _norm(m) for m in child_mot)
    has_older_only_signal = any("mas de supervision que de atencion directa" in _norm(m) for m in child_mot)
    if schedule_key == "sd_l_v" and has_older_only_signal and not has_small_children_signal and suggested_max > 23000:
        suggested_max = 23000
        if suggested_min > suggested_max:
            suggested_min = max(base, suggested_max - 1500)
    # Nunca bajar por debajo de la base fija de la modalidad.
    if suggested_min < base:
        suggested_min = base
    if suggested_max < suggested_min:
        suggested_max = suggested_min

    client_salary = parse_salary_amount(data.get("sueldo"))
    status = _offer_status(client_salary, suggested_min)
    load_level = "muy_alta" if "muy_alta" in levels else "alta" if "alta" in levels else "media" if "media" in levels else "normal"

    public_reasons = _reason_bullets({"adjustments": [{"label": m, "amount": None} for m in motivos]})
    out = {
        "can_suggest": True,
        "reason_no_suggestion": "",
        "base_salary": base,
        "adjustments": [{"label": r, "amount": None} for r in public_reasons],
        "public_reasons": public_reasons,
        "internal_adjustments": [{"label": m, "amount": None} for m in motivos],
        "suggested_min": suggested_min,
        "suggested_max": suggested_max,
        "load_level": load_level,
        "warnings": warnings,
        "offer_status": status,
    }
    out["message"] = build_salary_message(out)
    return out
