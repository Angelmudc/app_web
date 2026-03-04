# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.funciones_formatter import format_funciones

ENGINE_VERSION = "compat-engine-v2"

_LEVEL_ALTA_MIN = 75
_LEVEL_MEDIA_MIN = 50

HORARIO_OPTIONS = [
    ("8am-5pm", "8:00 AM a 5:00 PM"),
    ("9am-6pm", "9:00 AM a 6:00 PM"),
    ("10am-6pm", "10:00 AM a 6:00 PM"),
    ("medio_tiempo", "Medio tiempo"),
    ("fin_de_semana", "Fin de semana"),
    ("noche_solo", "Solo de noche"),
    ("dormida_l-v", "Dormida (Lunes a Viernes)"),
    ("dormida_l-s", "Dormida (Lunes a Sábado)"),
    ("salida_quincenal", "Salida quincenal (cada 15 días)"),
]
HORARIO_TOKENS = {k for k, _ in HORARIO_OPTIONS}


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _strip_accents(txt: str) -> str:
    value = unicodedata.normalize("NFKD", txt or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _canon_text(value: Any) -> str:
    txt = str(value or "").strip().lower()
    txt = _strip_accents(txt)
    txt = txt.replace("_", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _load_json_like(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _to_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            txt = str(item).strip()
            if txt:
                out.append(txt)
        return out
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return _to_list(parsed)
        except Exception:
            pass
        return [x.strip() for x in value.split(",") if x and x.strip()]
    txt = str(value).strip()
    return [txt] if txt else []


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _first_nonempty(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            val = data.get(key)
            if val not in (None, "", [], {}, ()):  # noqa: PLC1901
                return val
    return None


def _norm_ritmo(value: Any) -> Optional[str]:
    v = _canon_text(value)
    mapping = {
        "tranquilo": "tranquilo",
        "activo": "activo",
        "muy activo": "muy_activo",
        "muyactivo": "muy_activo",
    }
    return mapping.get(v)


def _norm_estilo(value: Any) -> Optional[str]:
    v = _canon_text(value)
    mapping = {
        "paso a paso": "necesita_instrucciones",
        "necesita instrucciones": "necesita_instrucciones",
        "necesita instruccion": "necesita_instrucciones",
        "prefiere iniciativa": "toma_iniciativa",
        "toma iniciativa": "toma_iniciativa",
        "necesita_instrucciones": "necesita_instrucciones",
        "toma_iniciativa": "toma_iniciativa",
    }
    return mapping.get(v)


def _norm_rel_ninos(value: Any) -> Optional[str]:
    v = _canon_text(value)
    mapping = {
        "comoda": "comoda",
        "comodas": "comoda",
        "neutral": "neutral",
        "prefiere evitar": "prefiere_evitar",
        "evitar": "prefiere_evitar",
        "prefiere_evitar": "prefiere_evitar",
    }
    return mapping.get(v)


def _norm_exp_level(value: Any) -> Optional[str]:
    v = _canon_text(value)
    mapping = {
        "basica": "basica",
        "intermedia": "intermedia",
        "alta": "alta",
    }
    return mapping.get(v)


def _norm_bool_mascota(value: Any) -> Optional[bool]:
    v = _canon_text(value)
    if v in {"si", "sí", "yes", "true", "1", "con mascota", "tiene mascota"}:
        return True
    if v in {"no", "false", "0", "sin mascota"}:
        return False
    return None


def _norm_limit(value: Any) -> Optional[str]:
    v = _canon_text(value)
    mapping = {
        "no cocinar": "no_cocinar",
        "no_cocinar": "no_cocinar",
        "no planchar": "no_planchar",
        "no_planchar": "no_planchar",
        "no dormir fuera": "no_dormir_fuera",
        "no_dormir_fuera": "no_dormir_fuera",
        "no trabajar fines de semana": "no_fines_de_semana",
        "no_fines_de_semana": "no_fines_de_semana",
        "no usar celular en horario": "sin_celular_en_horario",
        "sin celular en horario": "sin_celular_en_horario",
        "no mascotas": "no_mascotas",
        "no_mascotas": "no_mascotas",
    }
    return mapping.get(v, v.replace(" ", "_") if v else None)


def _sort_horario_tokens(tokens: set[str]) -> List[str]:
    order = {tok: idx for idx, (tok, _lbl) in enumerate(HORARIO_OPTIONS)}
    return sorted(tokens, key=lambda t: order.get(t, 999))


def normalize_horarios_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    direct_map = {
        # objetivo actual
        "8am-5pm": "8am-5pm",
        "9am-6pm": "9am-6pm",
        "10am-6pm": "10am-6pm",
        "medio_tiempo": "medio_tiempo",
        "fin_de_semana": "fin_de_semana",
        "noche_solo": "noche_solo",
        "dormida_l-v": "dormida_l-v",
        "dormida_l-s": "dormida_l-s",
        "salida_quincenal": "salida_quincenal",
        # retrocompatibilidad legacy requerida
        "manana": "8am-5pm",
        "mañana": "8am-5pm",
        "tarde": "9am-6pm",
        "noche": "noche_solo",
        "flexible": "medio_tiempo",
        "interna": "dormida_l-v",
        "fin de semana": "fin_de_semana",
        "findesemana": "fin_de_semana",
        "weekend": "fin_de_semana",
        # compat con cambios intermedios previos
        "7-11": "8am-5pm",
        "11-3": "9am-6pm",
        "3-7": "10am-6pm",
        "7-11pm": "noche_solo",
    }

    for part in _to_list(value):
        txt = _canon_text(part)
        if not txt:
            continue

        if txt in direct_map:
            tokens.add(direct_map[txt])
            continue

        if ("8:00" in txt and "5:00" in txt) or ("8" in txt and "5" in txt and ("am" in txt or "a.m" in txt or "a m" in txt)):
            tokens.add("8am-5pm")
            continue
        if ("9:00" in txt and "6:00" in txt) or ("9" in txt and "6" in txt):
            tokens.add("9am-6pm")
            continue
        if ("10:00" in txt and "6:00" in txt) or ("10" in txt and "6" in txt):
            tokens.add("10am-6pm")
            continue
        if "10:00" in txt and "7:00" in txt:
            tokens.add("10am-6pm")
            continue

        if "medio" in txt or "part" in txt:
            tokens.add("medio_tiempo")
            continue
        if "noche" in txt or "noct" in txt:
            tokens.add("noche_solo")
            continue
        if "fin de semana" in txt or "finde" in txt or "weekend" in txt:
            tokens.add("fin_de_semana")
            continue

        if "dormida" in txt or "interna" in txt:
            if "sabado" in txt or "sab" in txt or "lunes a sabado" in txt:
                tokens.add("dormida_l-s")
            else:
                tokens.add("dormida_l-v")
            continue

        if "quincenal" in txt or "15 dias" in txt or "cada 15" in txt:
            tokens.add("salida_quincenal")
            continue

    return {t for t in tokens if t in HORARIO_TOKENS}


def _extract_hours_from_text(text: str) -> List[int]:
    nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", text or "")]
    out = []
    for n in nums:
        if 0 <= n <= 24:
            out.append(n)
    return out


def _parse_cliente_horario_range(value: Any) -> Optional[tuple[int, int]]:
    txt_src = str(value or "").strip()
    txt = _canon_text(txt_src)
    if not txt:
        return None

    if "interna" in txt or "dormida" in txt:
        return (0, 24)
    if "noche" in txt:
        return (19, 23)

    if "8am-5pm" in txt:
        return (8, 17)
    if "9am-6pm" in txt:
        return (9, 18)
    if "10am-6pm" in txt:
        return (10, 18)

    # Casos comunes del formulario cliente legacy.
    if "8:00" in txt_src and "5:00" in txt_src:
        return (8, 17)
    if "9:00" in txt_src and "6:00" in txt_src:
        return (9, 18)
    if "10:00" in txt_src and "7:00" in txt_src:
        return (10, 19)

    hours = _extract_hours_from_text(txt)
    if len(hours) >= 2:
        start = max(0, min(23, hours[0]))
        end = max(0, min(24, hours[1]))
        if end <= start:
            end = min(24, end + 12)
        if end > start:
            return (start, end)
    return None


def _candidate_ranges(tokens: set[str]) -> List[tuple[int, int]]:
    slot_map = {
        "8am-5pm": (8, 17),
        "9am-6pm": (9, 18),
        "10am-6pm": (10, 18),
        "noche_solo": (19, 23),
    }
    ranges: List[tuple[int, int]] = []
    for t in tokens:
        rng = slot_map.get(t)
        if rng:
            ranges.append(rng)
    return ranges


def _hours_overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    left = max(a[0], b[0])
    right = min(a[1], b[1])
    return max(0, right - left)


def _normalize_funciones(values: Any) -> List[str]:
    text = format_funciones(values)
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x and x.strip()]


def _normalize_profile_lists(values: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in _to_list(values):
        key = _canon_text(raw)
        if not key:
            continue
        label = format_funciones([raw]) or raw
        label = (label or raw).strip()
        if not label:
            continue
        k = label.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(label)
    return out


def _pick_profile_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    profile = raw.get("profile")
    if isinstance(profile, dict):
        return profile
    return raw


def load_candidata_profile(c) -> Dict[str, Any]:
    raw = _load_json_like(getattr(c, "compat_test_candidata_json", None))
    data = _pick_profile_dict(raw)

    fortalezas = _first_nonempty(data, "fortalezas")
    if not fortalezas:
        fortalezas = getattr(c, "compat_habilidades_fuertes", None) or getattr(c, "compat_fortalezas", None)

    tareas_evitar = _first_nonempty(data, "tareas_evitar")
    if not tareas_evitar:
        tareas_evitar = getattr(c, "compat_habilidades_evitar", None) or getattr(c, "compat_tareas_evitar", None)

    limites = _first_nonempty(data, "limites_no_negociables")
    if not limites:
        limites = getattr(c, "compat_limites_no_negociables", None)

    disp_dias = _first_nonempty(data, "disponibilidad_dias")
    if not disp_dias:
        disp_dias = getattr(c, "compat_disponibilidad_dias", None)

    disp_horarios = _first_nonempty(data, "disponibilidad_horarios", "disponibilidad_horario")
    if not disp_horarios:
        disp_horarios = getattr(c, "compat_disponibilidad_horarios", None) or getattr(c, "compat_disponibilidad_horario", None)

    mascotas = _first_nonempty(data, "mascotas")
    if not mascotas:
        mascotas = getattr(c, "compat_mascotas", None)
        if mascotas in (None, "") and hasattr(c, "compat_mascotas_ok"):
            mascotas = "si" if bool(getattr(c, "compat_mascotas_ok")) else "no"

    puntualidad = _to_int(_first_nonempty(data, "puntualidad_1a5"), None)
    if puntualidad is None:
        puntualidad = _to_int(getattr(c, "compat_puntualidad_1a5", None), None)

    out = {
        "ritmo": _norm_ritmo(_first_nonempty(data, "ritmo", "ritmo_preferido") or getattr(c, "compat_ritmo_preferido", None)),
        "estilo": _norm_estilo(_first_nonempty(data, "estilo", "estilo_trabajo") or getattr(c, "compat_estilo_trabajo", None)),
        "comunicacion": _canon_text(_first_nonempty(data, "comunicacion") or getattr(c, "compat_comunicacion", None)),
        "relacion_ninos": _norm_rel_ninos(_first_nonempty(data, "relacion_ninos") or getattr(c, "compat_relacion_ninos", None)),
        "experiencia_nivel": _norm_exp_level(_first_nonempty(data, "experiencia_nivel") or getattr(c, "compat_experiencia_nivel", None)),
        "puntualidad_1a5": puntualidad,
        "fortalezas": _normalize_profile_lists(fortalezas),
        "tareas_evitar": _normalize_profile_lists(tareas_evitar),
        "limites_no_negociables": [_norm_limit(x) for x in _to_list(limites) if _norm_limit(x)],
        "disponibilidad_dias": [(_canon_text(x) or x) for x in _to_list(disp_dias)],
        "disponibilidad_horarios": _sort_horario_tokens(normalize_horarios_tokens(disp_horarios)),
        "mascotas": _norm_bool_mascota(mascotas),
        "nota": str(_first_nonempty(data, "nota", "observaciones") or getattr(c, "compat_observaciones", "") or "").strip(),
        "raw": raw,
        "version": str(raw.get("version") or "v1.0"),
        "timestamp": str(raw.get("timestamp") or ""),
    }
    return out


def load_cliente_profile(s) -> Dict[str, Any]:
    raw = _load_json_like(getattr(s, "compat_test_cliente_json", None))
    if not raw and hasattr(s, "compat_test_cliente"):
        raw = _load_json_like(getattr(s, "compat_test_cliente", None))
    data = _pick_profile_dict(raw)

    no_neg = [_norm_limit(x) for x in _to_list(_first_nonempty(data, "no_negociables")) if _norm_limit(x)]

    horario_tokens = normalize_horarios_tokens(_first_nonempty(data, "horario_tokens"))
    if not horario_tokens:
        horario_tokens = normalize_horarios_tokens(_first_nonempty(data, "horario_preferido"))
    if not horario_tokens:
        horario_tokens = normalize_horarios_tokens(getattr(s, "horario", None))

    out = {
        "ritmo_hogar": _norm_ritmo(_first_nonempty(data, "ritmo_hogar")),
        "direccion_trabajo": _norm_estilo(_first_nonempty(data, "direccion_trabajo", "estilo")),
        "comunicacion": _canon_text(_first_nonempty(data, "comunicacion")),
        "experiencia_deseada": _norm_exp_level(_first_nonempty(data, "experiencia_deseada")),
        "puntualidad_1a5": _to_int(_first_nonempty(data, "puntualidad_1a5"), None),
        "horario_preferido": str(_first_nonempty(data, "horario_preferido") or getattr(s, "horario", "") or "").strip(),
        "horario_tokens": _sort_horario_tokens(horario_tokens),
        "prioridades": _normalize_funciones(_first_nonempty(data, "prioridades")),
        "no_negociables": no_neg,
        "nota_cliente_test": str(_first_nonempty(data, "nota_cliente_test") or "").strip(),
        "ninos": _to_int(_first_nonempty(data, "ninos"), _to_int(getattr(s, "ninos", None), 0)) or 0,
        "mascota": _norm_bool_mascota(_first_nonempty(data, "mascota") or getattr(s, "mascota", None)),
        "funciones": _normalize_funciones(getattr(s, "funciones", None)),
        "raw": raw,
        "version": str(raw.get("version") or getattr(s, "compat_test_cliente_version", None) or "v1.0"),
        "timestamp": str(raw.get("timestamp") or getattr(s, "compat_test_cliente_at", "") or ""),
    }
    return out


def _compute_horario(cliente: Dict[str, Any], candidata: Dict[str, Any]) -> tuple[int, str, List[str]]:
    risks: List[str] = []
    cand_tokens = normalize_horarios_tokens(candidata.get("disponibilidad_horarios"))
    cli_tokens = normalize_horarios_tokens(cliente.get("horario_tokens"))
    if not cli_tokens:
        cli_tokens = normalize_horarios_tokens(cliente.get("horario_preferido"))
    cli_range = _parse_cliente_horario_range(cliente.get("horario_preferido"))

    if not cli_tokens and not cli_range and not cand_tokens:
        return 12, "Sin horario definido en ambos perfiles; se recomienda confirmar disponibilidad real.", risks
    if (not cli_tokens and not cli_range) or not cand_tokens:
        return 14, "Hay datos parciales de horario; conviene validar el bloque de horas en entrevista final.", risks

    needs_dormida = bool({"dormida_l-v", "dormida_l-s"} & cli_tokens) or (cli_range == (0, 24))
    if needs_dormida:
        if "dormida_l-s" in cand_tokens:
            return 25, "La candidata cumple con modalidad dormida de forma completa.", risks
        if "dormida_l-v" in cand_tokens:
            if "dormida_l-s" in cli_tokens:
                risks.append("La solicitud pide dormida L-S y la candidata reporta dormida L-V.")
                return 19, "Compatibilidad dormida parcial; validar cobertura de sábado.", risks
            return 23, "La candidata cumple modalidad dormida para jornada laboral semanal.", risks
        risks.append("La solicitud requiere modalidad dormida/interna y la candidata no la reporta.")
        return 8, "No hay alineación con modalidad dormida solicitada.", risks

    if "noche_solo" in cli_tokens:
        if "noche_solo" in cand_tokens:
            return 25, "Horario nocturno alineado entre solicitud y candidata.", risks
        risks.append("La solicitud exige horario nocturno y la candidata no lo declara.")
        return 7, "No hay alineación para jornada nocturna.", risks

    score = 12
    diurnos = {"8am-5pm", "9am-6pm", "10am-6pm"}
    cli_diurno = diurnos & cli_tokens
    cand_diurno = diurnos & cand_tokens

    if cli_diurno:
        if cli_diurno & cand_diurno:
            score = 25
        elif cand_diurno:
            score = 19
            risks.append("La candidata tiene horario diurno, pero no coincide exactamente con el solicitado.")
        elif "noche_solo" in cand_tokens:
            score = 7
            risks.append("La candidata reporta solo horario nocturno y la solicitud es diurna.")
        elif "medio_tiempo" in cand_tokens:
            score = 14
            risks.append("La candidata reporta medio tiempo y la solicitud requiere jornada diurna completa.")
        else:
            score = 11

    if "medio_tiempo" in cli_tokens:
        if "medio_tiempo" in cand_tokens:
            score = max(score, 23)
        elif cand_diurno:
            score = max(score, 16)
            risks.append("La solicitud pide medio tiempo y la candidata reporta disponibilidad diurna completa.")
        else:
            risks.append("No hay evidencia de disponibilidad en medio tiempo.")

    if "fin_de_semana" in cli_tokens:
        if "fin_de_semana" in cand_tokens:
            score = min(25, score + 2)
        else:
            score = max(0, score - 4)
            risks.append("La solicitud contempla fin de semana y la candidata no lo declaró.")

    if "salida_quincenal" in cli_tokens:
        if "salida_quincenal" in cand_tokens:
            score = min(25, score + 1)
        else:
            score = max(0, score - 3)
            risks.append("La solicitud contempla salida quincenal y la candidata no lo declaró.")

    ranges = _candidate_ranges(cand_tokens)
    if cli_range and ranges and score < 23:
        needed_hours = max(1, cli_range[1] - cli_range[0])
        overlap_hours = 0
        for rng in ranges:
            overlap_hours += _hours_overlap(cli_range, rng)
        overlap_hours = min(overlap_hours, needed_hours)
        ratio = overlap_hours / float(needed_hours)
        if ratio >= 0.75:
            score = max(score, 23)
        elif ratio >= 0.50:
            score = max(score, 19)
        elif ratio >= 0.25:
            score = max(score, 15)

    if score >= 23:
        return score, "Horario y disponibilidad alineados para la modalidad solicitada.", risks
    if score >= 17:
        return score, "Compatibilidad horaria media; conviene acordar condiciones antes de iniciar.", risks
    if score >= 12:
        risks.append("Puede existir ajuste operativo necesario en la jornada.")
        return score, "Compatibilidad horaria parcial; requiere validación adicional con ambas partes.", risks
    risks.append("Puede existir choque en horario o disponibilidad diaria.")
    return score, "El horario preferido del hogar no coincide con la disponibilidad declarada de la candidata.", risks


def _compute_ritmo(cliente: Dict[str, Any], candidata: Dict[str, Any]) -> tuple[int, str, List[str]]:
    risks: List[str] = []
    score = 12

    if cliente.get("ritmo_hogar") and candidata.get("ritmo"):
        if cliente["ritmo_hogar"] == candidata["ritmo"]:
            score += 7
        else:
            score += 1
            risks.append("Ritmo del hogar y ritmo preferido de la candidata no están totalmente alineados.")
    else:
        score += 3

    if cliente.get("direccion_trabajo") and candidata.get("estilo"):
        if cliente["direccion_trabajo"] == candidata["estilo"]:
            score += 6
        else:
            score += 1
            risks.append("Diferencia entre el estilo de supervisión del hogar y la forma de trabajo de la candidata.")
    else:
        score += 4

    score = max(0, min(25, score))
    note = "Ritmo y estilo con alineación operativa suficiente para trabajo estable." if score >= 20 else "Ritmo y estilo con alineación parcial; requiere expectativas claras desde el inicio."
    return score, note, risks


def _care_required(cliente: Dict[str, Any], keyword: str) -> bool:
    source = set(cliente.get("prioridades") or []) | set(cliente.get("funciones") or [])
    return any(keyword.lower() in (item or "").lower() for item in source)


def _compute_cuidados(cliente: Dict[str, Any], candidata: Dict[str, Any]) -> tuple[int, str, List[str]]:
    risks: List[str] = []
    necesita_ninos = int(cliente.get("ninos") or 0) > 0 or _care_required(cliente, "niños") or _care_required(cliente, "ninos")
    necesita_enve = _care_required(cliente, "envejecientes") or _care_required(cliente, "envejeciente")

    score = 20
    rel = candidata.get("relacion_ninos")

    if necesita_ninos:
        if rel == "comoda":
            score = 25
        elif rel == "neutral":
            score = 18
            risks.append("Hay niños en el hogar y la candidata se define neutral con esa dinámica.")
        elif rel == "prefiere_evitar":
            score = 4
            risks.append("La candidata prefiere evitar trabajo con niños y la solicitud sí lo requiere.")
        else:
            score = 12
            risks.append("No hay suficiente información sobre relación con niños en el perfil de la candidata.")

    if necesita_enve:
        evitar = {x.lower() for x in (candidata.get("tareas_evitar") or [])}
        if any("envejec" in x for x in evitar):
            score = max(0, score - 10)
            risks.append("La candidata reporta reserva para cuidado de envejecientes.")

    note = "Compatibilidad favorable para responsabilidades de cuidado del hogar." if score >= 18 else "Compatibilidad sensible en responsabilidades de cuidado; validar alcance del puesto."
    return score, note, risks


def _compute_limites(cliente: Dict[str, Any], candidata: Dict[str, Any]) -> tuple[int, str, List[str]]:
    risks: List[str] = []
    cli = set(cliente.get("no_negociables") or [])
    cand = set(candidata.get("limites_no_negociables") or [])

    if not cli and not cand:
        return 18, "No se declararon límites críticos en ambas partes.", risks

    choque = sorted(cli & cand)
    if choque:
        label_map = {
            "no_cocinar": "No cocinar",
            "no_planchar": "No planchar",
            "no_dormir_fuera": "No dormir fuera",
            "no_fines_de_semana": "No trabajar fines de semana",
            "sin_celular_en_horario": "Uso de celular en horario",
            "no_mascotas": "No mascotas",
        }
        choque_texto = ", ".join(label_map.get(x, x.replace("_", " ")) for x in choque)
        risks.append(f"Choque en límites no negociables: {choque_texto}.")
        return 3, "Se detectaron choques en condiciones no negociables del servicio.", risks

    return 25, "No se detectan conflictos en límites no negociables.", risks


def _compute_mascotas(cliente: Dict[str, Any], candidata: Dict[str, Any]) -> tuple[int, str, List[str]]:
    risks: List[str] = []
    tiene_mascota = cliente.get("mascota")
    cand_acepta = candidata.get("mascotas")
    cand_limites = set(candidata.get("limites_no_negociables") or [])

    if not tiene_mascota:
        return 22, "La solicitud no reporta mascotas como factor crítico.", risks

    if "no_mascotas" in cand_limites:
        risks.append("El hogar tiene mascota y la candidata declaró no trabajar con mascotas.")
        return 5, "Compatibilidad baja frente al criterio de mascotas.", risks

    if cand_acepta is True:
        return 25, "La candidata acepta mascotas y cumple con ese requisito del hogar.", risks
    if cand_acepta is False:
        risks.append("El hogar tiene mascota y la candidata reporta rechazo a mascotas.")
        return 5, "Compatibilidad baja frente al criterio de mascotas.", risks

    risks.append("No hay confirmación explícita sobre compatibilidad con mascotas.")
    return 12, "Compatibilidad parcial en criterio de mascotas por falta de confirmación.", risks


def _score_to_level(score: int) -> str:
    if score >= _LEVEL_ALTA_MIN:
        return "alta"
    if score >= _LEVEL_MEDIA_MIN:
        return "media"
    return "baja"


def _summary_for(score: int, level: str, breakdown: List[Dict[str, Any]], risks: List[str]) -> str:
    mejores = sorted(breakdown, key=lambda x: x.get("score", 0), reverse=True)[:2]
    areas = ", ".join(x.get("title", "") for x in mejores if x.get("title"))

    if level == "alta":
        return f"Compatibilidad alta ({score}/100). Buen encaje en {areas or 'los criterios principales'} con riesgos controlables."
    if level == "media":
        return f"Compatibilidad media ({score}/100). Hay base operativa positiva, pero conviene alinear expectativas en la entrevista final."
    if risks:
        return f"Compatibilidad baja ({score}/100). Se recomiendan ajustes antes de confirmar la asignación."
    return f"Compatibilidad baja ({score}/100). Hay información incompleta para recomendar una asignación segura."


def compute_match(s, c) -> Dict[str, Any]:
    cliente = load_cliente_profile(s)
    candidata = load_candidata_profile(c)

    breakdown: List[Dict[str, Any]] = []
    all_risks: List[str] = []

    h_score, h_note, h_r = _compute_horario(cliente, candidata)
    breakdown.append({"title": "Horario y disponibilidad", "score": h_score, "notes": h_note})
    all_risks.extend(h_r)

    r_score, r_note, r_r = _compute_ritmo(cliente, candidata)
    breakdown.append({"title": "Ritmo/estilo de trabajo", "score": r_score, "notes": r_note})
    all_risks.extend(r_r)

    c_score, c_note, c_r = _compute_cuidados(cliente, candidata)
    breakdown.append({"title": "Niños/envejecientes", "score": c_score, "notes": c_note})
    all_risks.extend(c_r)

    l_score, l_note, l_r = _compute_limites(cliente, candidata)
    breakdown.append({"title": "Límites no negociables", "score": l_score, "notes": l_note})
    all_risks.extend(l_r)

    m_score, m_note, m_r = _compute_mascotas(cliente, candidata)
    breakdown.append({"title": "Mascotas", "score": m_score, "notes": m_note})
    all_risks.extend(m_r)

    raw_total = sum(max(0, min(25, int(x.get("score", 0)))) for x in breakdown)
    score = int(round((raw_total / 125.0) * 100))
    level = _score_to_level(score)

    dedup_risks: List[str] = []
    seen = set()
    for risk in all_risks:
        txt = (risk or "").strip()
        if not txt:
            continue
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup_risks.append(txt)

    summary = _summary_for(score, level, breakdown, dedup_risks)

    return {
        "score": score,
        "level": level,
        "summary": summary,
        "risks": dedup_risks,
        "breakdown": breakdown,
        "meta": {
            "version": ENGINE_VERSION,
            "generated_at": _now_iso(),
            "cliente_test_version": cliente.get("version"),
            "cliente_test_timestamp": cliente.get("timestamp"),
            "candidata_test_version": candidata.get("version"),
            "candidata_test_timestamp": candidata.get("timestamp"),
        },
    }


def persist_result_to_solicitud(s, result: Dict[str, Any]) -> bool:
    try:
        from config_app import db  # import local para evitar ciclos

        score = _to_int((result or {}).get("score"), None)
        level = str((result or {}).get("level") or "").strip().lower()
        if level not in {"alta", "media", "baja"}:
            level = _score_to_level(score or 0) if score is not None else "baja"

        summary = str((result or {}).get("summary") or "").strip()
        risks = (result or {}).get("risks") or []
        if isinstance(risks, str):
            risks_txt = risks.strip()
        else:
            risks_txt = "; ".join(str(x).strip() for x in risks if str(x).strip())

        s.compat_calc_score = score
        s.compat_calc_level = level
        s.compat_calc_summary = summary or None
        s.compat_calc_risks = risks_txt or None
        s.compat_calc_at = datetime.utcnow()
        if hasattr(s, "fecha_ultima_modificacion"):
            s.fecha_ultima_modificacion = datetime.utcnow()

        db.session.commit()
        return True
    except Exception:
        try:
            from config_app import db
            db.session.rollback()
        except Exception:
            pass
        return False


def format_compat_result(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = result if isinstance(result, dict) else {}

    score = _to_int(base.get("score"), 0) or 0
    score = max(0, min(100, score))
    level = str(base.get("level") or _score_to_level(score)).strip().lower()
    if level not in {"alta", "media", "baja"}:
        level = _score_to_level(score)

    raw_breakdown = base.get("breakdown") or []
    formatted_breakdown: List[Dict[str, Any]] = []

    if isinstance(raw_breakdown, list):
        for item in raw_breakdown:
            if isinstance(item, dict):
                formatted_breakdown.append(
                    {
                        "title": str(item.get("title") or "Factor").strip(),
                        "score": max(0, min(25, _to_int(item.get("score"), 0) or 0)),
                        "notes": str(item.get("notes") or "").strip(),
                    }
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                formatted_breakdown.append(
                    {
                        "title": str(item[0] or "Factor").strip(),
                        "score": max(0, min(25, _to_int(item[1], 0) or 0)),
                        "notes": "",
                    }
                )

    if not formatted_breakdown:
        formatted_breakdown = [
            {"title": "Horario y disponibilidad", "score": 0, "notes": "Sin cálculo disponible."},
            {"title": "Ritmo/estilo de trabajo", "score": 0, "notes": "Sin cálculo disponible."},
            {"title": "Niños/envejecientes", "score": 0, "notes": "Sin cálculo disponible."},
            {"title": "Límites no negociables", "score": 0, "notes": "Sin cálculo disponible."},
            {"title": "Mascotas", "score": 0, "notes": "Sin cálculo disponible."},
        ]

    risks = base.get("risks") or []
    if isinstance(risks, str):
        risks = [x.strip() for x in risks.split(";") if x and x.strip()]

    summary = str(base.get("summary") or "").strip()
    if not summary:
        summary = f"Compatibilidad {level} ({score}/100)."

    meta = base.get("meta") if isinstance(base.get("meta"), dict) else {}

    return {
        "score": score,
        "level": level,
        "summary": summary,
        "risks": risks,
        "breakdown": formatted_breakdown,
        "meta": {
            "version": str(meta.get("version") or ENGINE_VERSION),
            "generated_at": str(meta.get("generated_at") or _now_iso()),
            "cliente_test_version": meta.get("cliente_test_version"),
            "cliente_test_timestamp": meta.get("cliente_test_timestamp"),
            "candidata_test_version": meta.get("candidata_test_version"),
            "candidata_test_timestamp": meta.get("candidata_test_timestamp"),
        },
    }
