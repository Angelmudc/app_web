from __future__ import annotations

from collections import defaultdict
from typing import Any

from config_app import db
from models import EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta


INTERVIEW_REFERENCE_QUESTION_KEYS = frozenset(
    {
        "domestica.referencia_laboral",
        "domestica.referencia_familiar",
        "enfermera.referencia_laboral",
        "enfermera.referencia_familiar",
        "empleo_general.referencia_laboral",
        "empleo_general.referencia_familiar",
    }
)

INTERVIEW_REFERENCE_TYPES = ("laboral", "familiar")
INTERVIEW_REFERENCE_TYPE_LABELS = {
    "laboral": "Laboral",
    "familiar": "Familiar",
}

_INTERVIEW_REFERENCE_QUESTION_TO_TYPE = {
    "domestica.referencia_laboral": "laboral",
    "domestica.referencia_familiar": "familiar",
    "enfermera.referencia_laboral": "laboral",
    "enfermera.referencia_familiar": "familiar",
    "empleo_general.referencia_laboral": "laboral",
    "empleo_general.referencia_familiar": "familiar",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def interview_reference_type_for_question(pregunta: Any) -> str | None:
    clave = _clean_text(getattr(pregunta, "clave", "")).lower()
    return _INTERVIEW_REFERENCE_QUESTION_TO_TYPE.get(clave)


def interview_reference_type_for_clave(clave: Any) -> str | None:
    return _INTERVIEW_REFERENCE_QUESTION_TO_TYPE.get(_clean_text(clave).lower())


def _reference_display_text(datos_json: Any, fallback_text: Any = "") -> str:
    if isinstance(datos_json, dict):
        for key in ("texto", "respuesta", "value", "descripcion", "detalle", "comentario"):
            value = _clean_text(datos_json.get(key))
            if value:
                return value
        structured_parts = []
        for key in ("nombre", "telefono", "parentesco", "relacion", "validacion"):
            value = _clean_text(datos_json.get(key))
            if value:
                structured_parts.append(value)
        if structured_parts:
            return " — ".join(structured_parts)
    return _clean_text(fallback_text)


def _reference_label(tipo: str | None, pregunta: Any = None) -> str:
    if pregunta is not None:
        texto = _clean_text(getattr(pregunta, "texto", None))
        if texto:
            return texto
    tipo_clean = _clean_text(tipo).lower()
    return INTERVIEW_REFERENCE_TYPE_LABELS.get(tipo_clean, "Referencia en entrevista")


def interview_reference_payload(*, tipo: str, texto: str, datos_json: dict[str, Any] | None = None, source: str = "explicit", pregunta: Any = None) -> dict[str, Any]:
    clean_tipo = _clean_text(tipo).lower()
    clean_texto = _clean_text(texto)
    data = dict(datos_json or {})
    if clean_texto and not data.get("texto"):
        data["texto"] = clean_texto
    return {
        "tipo": clean_tipo,
        "label": _reference_label(clean_tipo, pregunta=pregunta),
        "respuesta": _reference_display_text(data, clean_texto),
        "texto": clean_texto,
        "datos_json": data,
        "source": source,
    }


def interview_reference_payload_from_row(row: EntrevistaReferencia) -> dict[str, Any]:
    return interview_reference_payload(
        tipo=getattr(row, "tipo", None),
        texto=getattr(row, "texto", None),
        datos_json=getattr(row, "datos_json", None) or {},
        source="explicit",
    )


def interview_reference_payload_from_respuesta(respuesta: EntrevistaRespuesta, pregunta: EntrevistaPregunta) -> dict[str, Any] | None:
    tipo = interview_reference_type_for_question(pregunta)
    if not tipo:
        return None
    texto = _clean_text(getattr(respuesta, "respuesta", None))
    if not texto:
        return None
    return interview_reference_payload(
        tipo=tipo,
        texto=texto,
        datos_json={
            "texto": texto,
            "question_id": int(getattr(pregunta, "id", 0) or 0),
            "question_clave": _clean_text(getattr(pregunta, "clave", None)),
            "question_text": _clean_text(getattr(pregunta, "texto", None)),
            "respuesta_id": int(getattr(respuesta, "id", 0) or 0),
        },
        source="respuesta",
        pregunta=pregunta,
    )


def sync_entrevista_referencias_from_answers(*, session, entrevista, preguntas, respuestas_payload: dict[int, str]) -> dict[str, dict[str, Any]]:
    entrevista_id = int(getattr(entrevista, "id", 0) or 0)
    if not entrevista_id:
        return {}

    desired: dict[str, dict[str, Any]] = {}
    for pregunta in preguntas or []:
        tipo = interview_reference_type_for_question(pregunta)
        if not tipo:
            continue
        texto = _clean_text(respuestas_payload.get(int(getattr(pregunta, "id", 0) or 0)))
        if not texto:
            continue
        desired[tipo] = interview_reference_payload(
            tipo=tipo,
            texto=texto,
            datos_json={
                "texto": texto,
                "question_id": int(getattr(pregunta, "id", 0) or 0),
                "question_clave": _clean_text(getattr(pregunta, "clave", None)),
                "question_text": _clean_text(getattr(pregunta, "texto", None)),
            },
            source="explicit",
            pregunta=pregunta,
        )

    existing_rows = {
        _clean_text(row.tipo).lower(): row
        for row in EntrevistaReferencia.query.filter_by(entrevista_id=entrevista_id).all()
    }

    for tipo in INTERVIEW_REFERENCE_TYPES:
        payload = desired.get(tipo)
        row = existing_rows.pop(tipo, None)
        if not payload:
            if row is not None:
                session.delete(row)
            continue
        if row is None:
            row = EntrevistaReferencia(entrevista_id=entrevista_id, tipo=tipo)
            session.add(row)
        row.tipo = tipo
        row.texto = payload["texto"] or None
        row.datos_json = payload["datos_json"] or {}

    for row in existing_rows.values():
        session.delete(row)

    return desired


def _collect_fallback_reference_map(entrevista_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    ids = [int(eid) for eid in entrevista_ids if int(eid or 0)]
    if not ids:
        return {}
    rows = (
        db.session.query(EntrevistaRespuesta, EntrevistaPregunta)
        .join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
        .filter(EntrevistaRespuesta.entrevista_id.in_(ids))
        .order_by(
            EntrevistaRespuesta.entrevista_id.asc(),
            EntrevistaPregunta.orden.asc(),
            EntrevistaPregunta.id.asc(),
            EntrevistaRespuesta.id.asc(),
        )
        .all()
    )
    refs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for respuesta, pregunta in rows:
        payload = interview_reference_payload_from_respuesta(respuesta, pregunta)
        if not payload:
            continue
        refs[int(respuesta.entrevista_id)].append(payload)
    return dict(refs)


def collect_entrevista_reference_map(entrevista_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    ids = [int(eid) for eid in entrevista_ids if int(eid or 0)]
    if not ids:
        return {}

    explicit_rows = (
        EntrevistaReferencia.query
        .filter(EntrevistaReferencia.entrevista_id.in_(ids))
        .order_by(
            EntrevistaReferencia.entrevista_id.asc(),
            EntrevistaReferencia.tipo.asc(),
            EntrevistaReferencia.id.asc(),
        )
        .all()
    )
    refs: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in explicit_rows:
        payload = interview_reference_payload_from_row(row)
        if payload["tipo"]:
            refs[int(row.entrevista_id)][payload["tipo"]] = payload

    fallback_map = _collect_fallback_reference_map(ids)
    for entrevista_id, items in fallback_map.items():
        current = refs[int(entrevista_id)]
        for item in items:
            if item["tipo"] and item["tipo"] not in current:
                current[item["tipo"]] = item

    ordered: dict[int, list[dict[str, Any]]] = {}
    for entrevista_id in ids:
        bucket = refs.get(int(entrevista_id), {})
        ordered[int(entrevista_id)] = [
            bucket[tipo]
            for tipo in INTERVIEW_REFERENCE_TYPES
            if tipo in bucket
        ]
    return ordered


def collect_entrevista_reference_items(entrevista: Any) -> list[dict[str, Any]]:
    entrevista_id = int(getattr(entrevista, "id", 0) or 0)
    if not entrevista_id:
        return []
    refs = collect_entrevista_reference_map([entrevista_id])
    return list(refs.get(entrevista_id, []))


def is_interview_reference_question(pregunta: Any) -> bool:
    clave = str(getattr(pregunta, "clave", "") or "").strip().lower()
    return clave in INTERVIEW_REFERENCE_QUESTION_KEYS
