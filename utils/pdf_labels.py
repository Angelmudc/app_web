# -*- coding: utf-8 -*-

from __future__ import annotations

import re


_EXPLICIT_LABELS = {
    "tiene_hijos": "¿Tiene hijos?",
    "tienes_hijos": "¿Tiene hijos?",
    "numero_hijos": "¿Cuántos hijos tiene?",
    "edades_hijos": "Edades de los hijos",
    "quien_cuida": "¿Con quién deja a los niños?",
    "descripcion_personal": "Descripción personal",
    "fuerte": "Fortalezas",
    "razon_trabajo": "Motivo para trabajar",
    "labores_anteriores": "Experiencia y trabajos anteriores",
    "tiempo_ultimo_trabajo": "Tiempo en el último trabajo",
    "razon_salida": "Motivo de salida del último trabajo",
    "situacion_dificil": "¿Ha tenido situaciones difíciles?",
    "manejo_situacion": "¿Cómo manejó la situación?",
    "manejo_reclamo": "¿Cómo maneja un reclamo?",
    "uniforme": "Uso de uniforme",
    "dias_feriados": "Disponibilidad en días feriados",
    "revision_salida": "Revisión al salir",
    "colaboracion": "Trabajo en equipo y colaboración",
    "tipo_familia": "Tipo de familia",
    "cuidado_ninos": "Cuidado de niños",
    "sabes_cocinar": "¿Sabe cocinar?",
    "gusta_cocinar": "¿Le gusta cocinar?",
    "que_cocinas": "¿Qué cocina?",
    "postres": "Postres",
    "tareas_casa": "Tareas del hogar",
    "electrodomesticos": "Manejo de electrodomésticos",
    "planchar": "¿Sabe planchar?",
    "actividad_principal": "Actividad principal",
    "nivel_academico": "Nivel académico",
    "condiciones_salud": "Condiciones de salud",
    "alergico": "Alergias",
    "medicamentos": "Medicamentos",
    "seguro_medico": "Seguro médico",
    "pruebas_medicas": "Pruebas médicas",
    "vacunas_covid": "Vacunas COVID",
    "tomas_alcohol": "Consumo de alcohol",
    "fumas": "¿Fuma?",
    "tatuajes_piercings": "Tatuajes y piercings",
    "anos_experiencia": "Años de experiencia",
    "acepta_porcentaje_sueldo": "¿Acepta porcentaje del sueldo?",
    "modalidad_trabajo_preferida": "Modalidad de trabajo preferida",
    "compat_orden_detalle_nivel": "Nivel de orden y detalle",
    "orden_detalle_nivel": "Nivel de orden y detalle",
}

_WORD_REPLACEMENTS = {
    "anos": "años",
    "ninos": "niños",
    "nina": "niña",
    "ninas": "niñas",
}

_TECH_PREFIXES = (
    "compat_",
    "candidata_",
    "entrevista_",
    "pregunta_",
    "respuesta_",
    "campo_",
)

_DROP_PREFIX_TOKENS = {
    "compat",
    "candidata",
    "entrevista",
    "pregunta",
    "respuesta",
    "campo",
    "valor",
    "detalle",
}


def _looks_human_already(label: str) -> bool:
    if not label:
        return False
    return ("_" not in label) and ("." not in label)


def _normalize_key(raw: str) -> str:
    s = (raw or "").strip().strip(":")
    if "." in s:
        s = s.split(".", 1)[1]
    s = s.lower()
    for prefix in _TECH_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s.strip("_")


def _fallback_humanize(raw: str) -> str:
    s = (raw or "").strip().strip(":")
    if "." in s:
        s = s.split(".", 1)[1]
    for prefix in _TECH_PREFIXES:
        if s.lower().startswith(prefix):
            s = s[len(prefix):]

    s = re.sub(r"[_\.\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    words = [w for w in s.split(" ") if w]
    while words and words[0] in _DROP_PREFIX_TOKENS:
        words = words[1:]
    if not words:
        return ""

    normalized_words = [_WORD_REPLACEMENTS.get(w, w) for w in words]
    out = " ".join(normalized_words).strip()
    return out[:1].upper() + out[1:] if out else ""


def humanize_pdf_label(label_or_key: str) -> str:
    raw = (label_or_key or "").strip()
    if not raw:
        return ""
    if _looks_human_already(raw):
        return raw

    key = _normalize_key(raw)
    explicit = _EXPLICIT_LABELS.get(key)
    if explicit:
        return explicit

    fallback = _fallback_humanize(raw)
    return fallback or raw
