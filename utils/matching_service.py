# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import load_only

from models import Candidata
from utils.compat_engine import (
    compute_match,
    load_cliente_profile,
    load_candidata_profile,
    normalize_horarios_tokens,
)

# Evita calcular sobre miles de registros cuando el prefiltro sigue amplio.
DEFAULT_PREFILTER_LIMIT = 700
DEFAULT_TOP_K = 30


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def build_solicitud_profile(solicitud) -> Dict[str, Any]:
    """Convierte la solicitud al perfil normalizado consumido por compat_engine."""
    return load_cliente_profile(solicitud)


def candidate_query_prefilter(solicitud, base_query=None):
    """
    Prefiltro SQL para reducir candidatas antes de compute_match.
    Solo aplica filtros suaves para no descartar buenas opciones por ruido de datos.
    """
    q = base_query or Candidata.query

    # Solo columnas necesarias para ranking/listado + compatibilidad.
    q = q.options(
        load_only(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.cedula,
            Candidata.numero_telefono,
            Candidata.modalidad_trabajo_preferida,
            Candidata.compat_disponibilidad_horario,
            Candidata.compat_test_candidata_json,
            Candidata.compat_fortalezas,
            Candidata.compat_limites_no_negociables,
            Candidata.compat_relacion_ninos,
            Candidata.compat_ritmo_preferido,
            Candidata.compat_estilo_trabajo,
            Candidata.estado,
        )
    )

    # Disponibles para sugerencia interna (sin tocar lógica actual de asignación).
    q = q.filter(Candidata.estado != "descalificada")

    horario_tokens = normalize_horarios_tokens(getattr(solicitud, "horario", None))
    modalidad_txt = _normalize_text(getattr(solicitud, "modalidad_trabajo", None))

    if "dormida_l-v" in horario_tokens or "dormida_l-s" in horario_tokens or "dormida" in modalidad_txt:
        q = q.filter(
            or_(
                Candidata.modalidad_trabajo_preferida.ilike("%dormida%"),
                Candidata.compat_disponibilidad_horario.ilike("%dormida%"),
            )
        )

    if "medio_tiempo" in horario_tokens or "medio" in modalidad_txt:
        q = q.filter(
            or_(
                Candidata.modalidad_trabajo_preferida.ilike("%medio%"),
                Candidata.compat_disponibilidad_horario.ilike("%medio_tiempo%"),
                Candidata.compat_disponibilidad_horario.ilike("%medio%"),
            )
        )

    if "noche_solo" in horario_tokens or "noche" in modalidad_txt:
        q = q.filter(
            or_(
                Candidata.compat_disponibilidad_horario.ilike("%noche_solo%"),
                Candidata.compat_disponibilidad_horario.ilike("%noche%"),
            )
        )

    if "fin_de_semana" in horario_tokens or "fin de semana" in modalidad_txt:
        q = q.filter(
            or_(
                Candidata.compat_disponibilidad_horario.ilike("%fin_de_semana%"),
                Candidata.compat_disponibilidad_horario.ilike("%fin%"),
            )
        )

    # Filtro general de horario para solicitudes diurnas/comunes.
    horario_like_filters = []
    token_to_like = {
        "8am-5pm": ["%8am-5pm%", "%8:00%", "%5:00%", "%manana%"],
        "9am-6pm": ["%9am-6pm%", "%9:00%", "%6:00%"],
        "10am-6pm": ["%10am-6pm%", "%10:00%", "%6:00%"],
        "salida_quincenal": ["%salida_quincenal%", "%quincenal%"],
    }
    for token in horario_tokens:
        for like_value in token_to_like.get(token, []):
            horario_like_filters.append(Candidata.compat_disponibilidad_horario.ilike(like_value))

    if horario_like_filters:
        q = q.filter(or_(*horario_like_filters))

    return q


def rank_candidates(
    solicitud,
    *,
    top_k: int = DEFAULT_TOP_K,
    prefilter_limit: int = DEFAULT_PREFILTER_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Ejecuta ranking final sobre candidatas prefiltradas.
    """
    _ = build_solicitud_profile(solicitud)

    candidates: Sequence[Candidata] = (
        candidate_query_prefilter(solicitud)
        .order_by(Candidata.fila.desc())
        .limit(prefilter_limit)
        .all()
    )

    ranked: List[Dict[str, Any]] = []
    for cand in candidates:
        # Flujo explícito solicitado: solicitud->profile / candidata->profile / compute_match.
        _ = load_candidata_profile(cand)
        result = compute_match(solicitud, cand)
        ranked.append(
            {
                "candidate": cand,
                "score": int(result.get("score") or 0),
                "level": str(result.get("level") or "baja"),
                "summary": str(result.get("summary") or "").strip(),
                "risks": list(result.get("risks") or []),
                "breakdown": list(result.get("breakdown") or []),
                "result": result,
            }
        )

    ranked.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            (item["candidate"].nombre_completo or "").lower(),
        ),
        reverse=True,
    )
    return ranked[: max(1, int(top_k))]
