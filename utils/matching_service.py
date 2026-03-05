# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import load_only

from models import Candidata
from utils.age_normalizer import parse_candidata_age_int, parse_solicitud_age_rules
from utils.compat_engine import compute_match, normalize_horarios_tokens
from utils.modality_normalizer import evaluate_modalidad_match
from utils.text_normalizer import infer_city, location_tokens, normalize_text, skill_tokens, tokens

DEFAULT_PREFILTER_LIMIT = 250
DEFAULT_TOP_K = 30
logger = logging.getLogger(__name__)
_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")


def _to_set(value: Any) -> Set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        out = set()
        for item in value:
            out |= tokens(item)
        return out
    return tokens(value)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _is_nonempty(value: Any) -> bool:
    return bool(_as_text(value))


def _parse_first_int(value: Any) -> Optional[int]:
    txt = normalize_text(value)
    if not txt:
        return None
    m = re.search(r"(\d{1,2})", txt)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _infer_pet_required(solicitud) -> bool:
    raw = normalize_text(getattr(solicitud, "mascota", None))
    if not raw:
        return False
    return raw not in {"no", "ninguna", "ninguno", "sin", "no tiene"}


def _score_level(score: int) -> str:
    if score >= 75:
        return "alta"
    if score >= 50:
        return "media"
    return "baja"


def _normalize_modalidad_tokens(value: Any) -> Set[str]:
    norm = normalize_text(value)
    out = tokens(norm)
    if not norm:
        return out

    if "dormida" in norm or "interna" in norm:
        out.add("dormida")
    if "medio tiempo" in norm or "medio_tiempo" in norm:
        out.add("medio_tiempo")
    if "fin de semana" in norm:
        out.add("fin_de_semana")
    if "noche" in norm:
        out.add("noche")
    if "tiempo completo" in norm or "completo" in norm:
        out.add("tiempo_completo")
    return out


def build_solicitud_profile(solicitud) -> Dict[str, Any]:
    city_text = _as_text(getattr(solicitud, "ciudad_sector", None))
    rutas_text = _as_text(getattr(solicitud, "rutas_cercanas", None))
    loc_text = " ".join(x for x in [city_text, rutas_text] if x).strip()

    funciones_values = list(getattr(solicitud, "funciones", None) or [])
    otro = _as_text(getattr(solicitud, "funciones_otro", None))
    if otro:
        funciones_values.append(otro)

    tipo_servicio = _as_text(getattr(solicitud, "tipo_servicio", None))
    detalles_servicio = getattr(solicitud, "detalles_servicio", None) or {}
    detalles_text = ""
    if isinstance(detalles_servicio, dict):
        detalles_text = normalize_text(str(detalles_servicio))

    edad_rules = parse_solicitud_age_rules(
        edad_requerida_list=list(getattr(solicitud, "edad_requerida", None) or []),
        otro_text=getattr(solicitud, "edad_otro", None) or getattr(solicitud, "edad_requerida_otro", None),
    )

    funciones_skill_tokens: Set[str] = set()
    for val in funciones_values:
        funciones_skill_tokens |= skill_tokens(val)

    tipo_servicio_norm = normalize_text(tipo_servicio)
    if "ninera" in tipo_servicio_norm or "nino" in tipo_servicio_norm:
        funciones_skill_tokens.add("cuidar_ninos")
    if "enfermera" in tipo_servicio_norm or "cuidadora" in tipo_servicio_norm:
        funciones_skill_tokens.add("enfermeria")
        funciones_skill_tokens.add("cuidar_envejecientes")

    if "nino" in detalles_text:
        funciones_skill_tokens.add("cuidar_ninos")
    if "envejec" in detalles_text or "ancian" in detalles_text:
        funciones_skill_tokens.add("cuidar_envejecientes")
    if "enfermer" in detalles_text:
        funciones_skill_tokens.add("enfermeria")

    return {
        "city": infer_city(city_text) or infer_city(loc_text),
        "city_tokens": location_tokens(city_text),
        "location_text": loc_text,
        "location_tokens": location_tokens(loc_text),
        "routes_tokens": location_tokens(rutas_text),
        "modalidad_text": _as_text(getattr(solicitud, "modalidad_trabajo", None)),
        "modalidad_tokens": _normalize_modalidad_tokens(getattr(solicitud, "modalidad_trabajo", None)),
        "horario_tokens": normalize_horarios_tokens(getattr(solicitud, "horario", None)),
        "funciones_tokens": funciones_skill_tokens,
        "tipo_servicio": normalize_text(tipo_servicio),
        "detalles_servicio_text": detalles_text,
        "experiencia_text": _as_text(getattr(solicitud, "experiencia", None)),
        "edad_rules": edad_rules,
        "pet_required": _infer_pet_required(solicitud),
    }


def _build_base_query(base_query=None):
    q = base_query or Candidata.query
    return q.options(
        load_only(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.numero_telefono,
            Candidata.cedula,
            Candidata.codigo,
            Candidata.estado,
            Candidata.direccion_completa,
            Candidata.rutas_cercanas,
            Candidata.modalidad_trabajo_preferida,
            Candidata.compat_disponibilidad_horario,
            Candidata.compat_disponibilidad_dias,
            Candidata.compat_fortalezas,
            Candidata.compat_limites_no_negociables,
            Candidata.compat_relacion_ninos,
            Candidata.sabe_planchar,
            Candidata.areas_experiencia,
            Candidata.anos_experiencia,
            Candidata.edad,
            Candidata.compat_test_candidata_json,
        )
    )


def _apply_city_filter(q, city: str):
    like = f"%{city}%"
    return q.filter(
        or_(
            Candidata.direccion_completa.ilike(like),
            Candidata.rutas_cercanas.ilike(like),
        )
    )


def candidate_query_prefilter(solicitud, base_query=None):
    """Fase A: prefiltro SQL por estado + ciudad opcional, con límite fijo 250."""
    t0 = perf_counter()
    profile = build_solicitud_profile(solicitud)
    city = profile.get("city")

    base = _build_base_query(base_query)

    q_primary = base.filter(Candidata.estado == "lista_para_trabajar")
    if city:
        q_primary = _apply_city_filter(q_primary, city)

    primary_rows = q_primary.order_by(Candidata.fila.desc()).limit(DEFAULT_PREFILTER_LIMIT).all()
    if len(primary_rows) >= 60:
        logger.info(
            "matching.prefilter pool_size=%s dt_ms=%s city_filter=%s states=lista_para_trabajar",
            len(primary_rows),
            int((perf_counter() - t0) * 1000),
            bool(city),
        )
        return primary_rows

    q_fallback = base.filter(Candidata.estado.in_(("lista_para_trabajar", "inscrita")))
    if city:
        q_fallback = _apply_city_filter(q_fallback, city)

    rows = q_fallback.order_by(Candidata.fila.desc()).limit(DEFAULT_PREFILTER_LIMIT).all()
    logger.info(
        "matching.prefilter pool_size=%s dt_ms=%s city_filter=%s states=lista_para_trabajar+inscrita",
        len(rows),
        int((perf_counter() - t0) * 1000),
        bool(city),
    )
    return rows


def _overlap_ratio(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    den = max(1, len(a))
    return inter / float(den)


def _location_component(sol_profile: Dict[str, Any], cand) -> tuple[int, Dict[str, str]]:
    sol_city = sol_profile.get("city")
    sol_city_tokens = set(sol_profile.get("city_tokens") or set())
    sol_routes_tokens = set(sol_profile.get("routes_tokens") or set())
    sol_loc_tokens = set(sol_profile.get("location_tokens") or set())

    cand_dir = _as_text(getattr(cand, "direccion_completa", None))
    cand_rutas = _as_text(getattr(cand, "rutas_cercanas", None))
    cand_text = f"{cand_dir} {cand_rutas}".strip()
    cand_city = infer_city(cand_text)
    cand_tokens = location_tokens(cand_text)

    route_overlap = sorted(sol_routes_tokens & cand_tokens)
    sector_overlap = sorted((sol_city_tokens or sol_loc_tokens) & cand_tokens)

    pts = 0
    if sol_city:
        if cand_city == sol_city:
            pts = 30 if sector_overlap else 20
    else:
        # sin ciudad limpia: rutas son señal primaria
        r_ratio = _overlap_ratio(sol_routes_tokens or sol_loc_tokens, cand_tokens)
        if r_ratio >= 0.6:
            pts = 26
        elif r_ratio >= 0.4:
            pts = 20
        elif r_ratio >= 0.2:
            pts = 12

    if route_overlap:
        pts += 10

    pts = max(0, min(40, pts))
    return pts, {
        "city_detectada": (
            f"Ciudad detectada: {sol_city.title()} ✅"
            if sol_city and cand_city == sol_city
            else (f"Ciudad detectada solicitud: {sol_city.title()}" if sol_city else "Ciudad no detectada")
        ),
        "tokens_match": (
            "Tokens coinciden: " + ", ".join(sector_overlap[:6])
            if sector_overlap
            else "Tokens sin coincidencia fuerte"
        ),
        "rutas_match": (
            "Rutas: " + " / ".join(route_overlap[:4])
            if route_overlap
            else "Rutas sin coincidencia fuerte"
        ),
    }


def _modalidad_component(sol_profile: Dict[str, Any], cand) -> Dict[str, Any]:
    return evaluate_modalidad_match(
        sol_profile.get("modalidad_text"),
        getattr(cand, "modalidad_trabajo_preferida", None),
        max_points=20,
    )


def _horario_component(sol_profile: Dict[str, Any], cand) -> tuple[int, str]:
    sol = set(sol_profile.get("horario_tokens") or set())
    cand_tokens = normalize_horarios_tokens(getattr(cand, "compat_disponibilidad_horario", None))

    if not sol or not cand_tokens:
        return 0, "Horario sin datos suficientes"

    if sol == cand_tokens or sol.issubset(cand_tokens):
        return 15, "Horario compatible (exacto)"

    inter = sol & cand_tokens
    if inter:
        return 8, "Horario compatible (solapa): " + ", ".join(sorted(inter))

    diurnos = {"8am-5pm", "9am-6pm", "10am-6pm"}
    if ("medio_tiempo" in sol and cand_tokens & diurnos) or ("medio_tiempo" in cand_tokens and sol & diurnos):
        return 8, "Horario compatible (medio tiempo/diurno)"

    return 0, "Horario sin coincidencia"


def _funciones_component(sol_profile: Dict[str, Any], cand) -> tuple[int, str, List[str], List[str], List[str], List[str]]:
    sol_funcs = set(sol_profile.get("funciones_tokens") or set())

    cand_funcs: Set[str] = set()
    cand_funcs |= skill_tokens(getattr(cand, "areas_experiencia", None))
    for val in (getattr(cand, "compat_fortalezas", None) or []):
        cand_funcs |= skill_tokens(val)
    if getattr(cand, "sabe_planchar", False):
        cand_funcs.add("planchar")

    overlap = sorted(sol_funcs & cand_funcs)
    n = len(overlap)
    if n >= 3:
        pts = 20
    elif n == 2:
        pts = 16
    elif n == 1:
        pts = 10
    else:
        pts = 0

    missing_notes: List[str] = []
    if "cuidar_ninos" in sol_funcs and "cuidar_ninos" not in cand_funcs:
        missing_notes.append("Sin experiencia declarada con niños")
    if "cuidar_envejecientes" in sol_funcs and "cuidar_envejecientes" not in cand_funcs:
        missing_notes.append("Sin experiencia declarada con envejecientes")
    if "enfermeria" in sol_funcs and "enfermeria" not in cand_funcs:
        missing_notes.append("Sin experiencia declarada en enfermería")

    if overlap:
        pretty = ", ".join(x.replace("_", " ").title() for x in overlap[:6])
        note = "Coincidencias por experiencia: " + pretty
    else:
        note = "Funciones sin coincidencia fuerte"

    return (
        pts,
        note,
        overlap,
        sorted(sol_funcs)[:10],
        sorted(cand_funcs)[:10],
        missing_notes,
    )


def _experience_component(sol_profile: Dict[str, Any], cand) -> tuple[int, str]:
    years = _parse_first_int(getattr(cand, "anos_experiencia", None))
    if years is not None and years >= 3:
        return 5, f"Experiencia: {years} anos"

    if _is_nonempty(sol_profile.get("experiencia_text")) and _is_nonempty(getattr(cand, "anos_experiencia", None)):
        return 3, "Experiencia declarada"

    return 0, "Experiencia sin datos concluyentes"


def _penalties(sol_profile: Dict[str, Any], cand) -> tuple[Dict[str, int], List[str]]:
    penalties = {"mascota": 0}
    reasons: List[str] = []

    if sol_profile.get("pet_required"):
        limits = {normalize_text(x) for x in (getattr(cand, "compat_limites_no_negociables", None) or [])}
        if "no mascotas" in limits or "no_mascotas" in limits:
            penalties["mascota"] = -8
            reasons.append("Mascotas: penalizacion por NO mascotas")

    return penalties, reasons


def _edad_component(sol_profile: Dict[str, Any], cand) -> tuple[int, Optional[bool], Optional[int], List[Any], str]:
    rules = list(sol_profile.get("edad_rules") or [])
    cand_age = parse_candidata_age_int(_as_text(getattr(cand, "edad", None)))

    if cand_age is None or not rules:
        return 0, None, cand_age, rules, "Edad no evaluable"

    matched = any(r.matches(cand_age) for r in rules)
    if matched:
        return 5, True, cand_age, rules, "Edad compatible con solicitud"
    return -5, False, cand_age, rules, "Edad fuera del rango solicitado"


def _bonus_from_test(solicitud, cand) -> tuple[int, str]:
    has_client = bool(getattr(solicitud, "compat_test_cliente_json", None))
    has_cand = bool(getattr(cand, "compat_test_candidata_json", None))
    if not (has_client and has_cand):
        return 0, "Bonus test: +0"

    try:
        test_score = int(compute_match(solicitud, cand).get("score") or 0)
        bonus = min(10, max(0, int(round(test_score / 10.0))))
        return bonus, f"Bonus test: +{bonus}"
    except Exception:
        return 0, "Bonus test: +0"


def _candidate_history_flags(solicitud, cand) -> tuple[bool, bool]:
    """
    Retorna (bloqueada_por_otro_cliente, rechazada_por_mismo_cliente).
    No afecta score; solo explica restricciones operativas de envío.
    """
    try:
        from models import Solicitud, SolicitudCandidata
    except Exception:
        return False, False

    if not getattr(solicitud, "id", None) or not getattr(cand, "fila", None):
        return False, False

    blocked = False
    rejected_same_client = False
    try:
        blocked_row = (
            SolicitudCandidata.query
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id == cand.fila,
                SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
                SolicitudCandidata.solicitud_id != solicitud.id,
                Solicitud.cliente_id != solicitud.cliente_id,
            )
            .first()
        )
        blocked = blocked_row is not None
    except Exception:
        blocked = False

    try:
        rejected_row = (
            SolicitudCandidata.query
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id == cand.fila,
                SolicitudCandidata.status == "descartada",
                Solicitud.cliente_id == solicitud.cliente_id,
            )
            .first()
        )
        rejected_same_client = rejected_row is not None
    except Exception:
        rejected_same_client = False

    return blocked, rejected_same_client


def _score_candidate(solicitud, cand) -> Dict[str, Any]:
    sol_profile = build_solicitud_profile(solicitud)

    ubicacion_pts, loc_info = _location_component(sol_profile, cand)
    modalidad_eval = _modalidad_component(sol_profile, cand)
    modalidad_pts = int(modalidad_eval["modalidad_pts"])
    modalidad_note = modalidad_eval["modalidad_reason"]
    horario_pts, horario_note = _horario_component(sol_profile, cand)
    (
        funciones_pts,
        funciones_note,
        skills_overlap,
        skills_solicitud_tokens,
        skills_candidata_tokens,
        missing_skill_notes,
    ) = _funciones_component(sol_profile, cand)
    experiencia_pts, experiencia_note = _experience_component(sol_profile, cand)
    edad_pts, edad_match, edad_candidate, edad_rules, edad_note = _edad_component(sol_profile, cand)

    penalties_dict, penalty_reasons = _penalties(sol_profile, cand)
    penalties_total = sum(penalties_dict.values())
    blocked_other_client, rejected_same_client = _candidate_history_flags(solicitud, cand)

    base_score = ubicacion_pts + modalidad_pts + horario_pts + funciones_pts + experiencia_pts + edad_pts
    operational_score = max(0, min(100, base_score + penalties_total))

    bonus_test, bonus_note = _bonus_from_test(solicitud, cand)
    final_score = max(0, min(100, operational_score + bonus_test))

    explain = {
        "city_detectada": loc_info["city_detectada"],
        "tokens_match": loc_info["tokens_match"],
        "rutas_match": loc_info["rutas_match"],
        "modalidad_match": modalidad_note,
        "horario_match": horario_note,
        "skills_match": funciones_note,
        "mascota_penalty": (
            "Mascotas: penalizacion por NO mascotas" if penalties_dict["mascota"] < 0 else "Sin penalizacion por mascotas"
        ),
        "test_bonus": bonus_note,
    }

    reasons = [
        explain["city_detectada"],
        explain["tokens_match"],
        explain["rutas_match"],
        explain["modalidad_match"],
        explain["horario_match"],
        explain["skills_match"],
        edad_note,
        explain["mascota_penalty"],
        explain["test_bonus"],
    ]
    if blocked_other_client:
        reasons.append("Bloqueada: ya fue enviada a otro cliente y no ha sido rechazada.")
    if rejected_same_client:
        reasons.append("Historial: esta candidata fue rechazada anteriormente por este cliente.")
    reasons.extend(missing_skill_notes)

    component_rows = [
        {"title": "Ubicacion", "score": ubicacion_pts, "notes": loc_info["tokens_match"]},
        {"title": "Modalidad", "score": modalidad_pts, "notes": modalidad_note},
        {"title": "Horario", "score": horario_pts, "notes": horario_note},
        {"title": "Funciones/Servicio", "score": funciones_pts, "notes": funciones_note},
        {"title": "Experiencia", "score": experiencia_pts, "notes": experiencia_note},
        {"title": "Edad", "score": edad_pts, "notes": edad_note},
        {"title": "Penalizacion mascotas", "score": penalties_dict["mascota"], "notes": explain["mascota_penalty"]},
        {"title": "Bonus test", "score": bonus_test, "notes": bonus_note},
    ]

    summary = f"Matching operativo (BD): {operational_score}%"
    risks = list(penalty_reasons)
    risks.extend(missing_skill_notes)

    breakdown_snapshot = {
        **explain,
        "ubicacion_pts": ubicacion_pts,
        "skills_match": skills_overlap,
        "skills_solicitud_tokens": skills_solicitud_tokens,
        "skills_candidata_tokens": skills_candidata_tokens,
        "funciones_pts": funciones_pts,
        "edad_candidate": edad_candidate,
        "edad_rules": [r.label() for r in edad_rules],
        "edad_match": edad_match,
        "edad_pts": edad_pts,
        "modalidad_pts": modalidad_pts,
        "components": {
            "ubicacion_pts": ubicacion_pts,
            "modalidad_pts": modalidad_pts,
            "horario_pts": horario_pts,
            "funciones_pts": funciones_pts,
            "experiencia_pts": experiencia_pts,
            "edad_pts": edad_pts,
            "penalties": penalties_dict,
            "bonus_test": bonus_test,
            "operational_score": operational_score,
            "final_score": final_score,
        },
        "component_rows": component_rows,
        "solicitud_modalidad_raw": modalidad_eval["solicitud_modalidad_raw"],
        "solicitud_modalidad_norm": modalidad_eval["solicitud_modalidad_norm"],
        "candidata_modalidad_raw": modalidad_eval["candidata_modalidad_raw"],
        "candidata_modalidad_norm": modalidad_eval["candidata_modalidad_norm"],
        "modalidad_match": modalidad_eval["modalidad_match"],
        "modalidad_reason": modalidad_eval["modalidad_reason"],
        "blocked_other_client": bool(blocked_other_client),
        "rejected_same_client": bool(rejected_same_client),
    }

    return {
        "candidate": cand,
        "score": final_score,
        "operational_score": operational_score,
        "bonus_test": bonus_test,
        "level": _score_level(final_score),
        "summary": summary,
        "risks": risks,
        "breakdown": component_rows,
        "reasons": reasons,
        "breakdown_snapshot": breakdown_snapshot,
    }


def rank_candidates(
    solicitud,
    *,
    top_k: int = DEFAULT_TOP_K,
    prefilter_limit: int = DEFAULT_PREFILTER_LIMIT,
) -> List[Dict[str, Any]]:
    """Fase B: ranking final basado en datos reales de BD (bonus test opcional)."""
    t0 = perf_counter()
    pool: Sequence[Candidata] = candidate_query_prefilter(solicitud)
    if prefilter_limit:
        pool = list(pool)[: max(1, min(int(prefilter_limit), DEFAULT_PREFILTER_LIMIT))]

    ranked = [_score_candidate(solicitud, cand) for cand in pool]
    ranked.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            int(item.get("operational_score") or 0),
            (item["candidate"].nombre_completo or "").lower(),
        ),
        reverse=True,
    )

    dt_ms = int((perf_counter() - t0) * 1000)
    logger.info("matching.rank pool_size=%s dt_ms=%s", len(pool), dt_ms)

    sliced = ranked[: max(1, int(top_k))]
    for row in sliced:
        row["meta"] = {
            "dt_ms": dt_ms,
            "pool_size": len(pool),
            "prefilter_limit": DEFAULT_PREFILTER_LIMIT,
        }
    return sliced
