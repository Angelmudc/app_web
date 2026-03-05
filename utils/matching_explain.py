# -*- coding: utf-8 -*-

import re
from typing import Any


_STOPWORDS = {
    "tokens",
    "coinciden",
    "coincidencia",
    "coincidencias",
    "rutas",
    "ruta",
    "ciudad",
    "detectada",
    "sin",
    "datos",
    "compatible",
    "compatibles",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _truthy_match(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean_text(value).lower()
    if not text:
        return False
    if any(x in text for x in ("sin datos", "no evaluable", "incompatible", "no compatible")):
        return False
    return any(x in text for x in ("compatible", "coincide", "match", "ok", "true"))


def _token_hint(value: Any, max_tokens: int = 2) -> str:
    text = _clean_text(value).lower()
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9áéíóúñ]{2,24}", text)
    clean_tokens = []
    for tok in tokens:
        if tok in _STOPWORDS:
            continue
        if tok not in clean_tokens:
            clean_tokens.append(tok)
        if len(clean_tokens) >= max_tokens:
            break
    return ", ".join(clean_tokens)


def client_bullets_from_breakdown(breakdown: dict) -> list[str]:
    """
    Convierte breakdown_snapshot en bullets claros para el cliente (3-6 máximo)
    sin exponer datos sensibles.
    """
    bd = breakdown if isinstance(breakdown, dict) else {}
    bullets: list[str] = []

    city = _clean_text(bd.get("city_detectada"))
    token_hint = _token_hint(bd.get("tokens_match"), max_tokens=2)
    route_hint = _token_hint(bd.get("rutas_match"), max_tokens=2)
    if city or token_hint or route_hint:
        loc_parts = []
        if city:
            loc_parts.append(city)
        if token_hint:
            loc_parts.append(f"Sectores cercanos: {token_hint}")
        if route_hint:
            loc_parts.append(f"Rutas: {route_hint}")
        bullets.append("Ubicacion alineada con la solicitud: " + " | ".join(loc_parts[:2]))

    if _truthy_match(bd.get("modalidad_match")):
        bullets.append("La modalidad de trabajo es compatible con lo que solicitaste.")

    if _truthy_match(bd.get("horario_match")):
        bullets.append("El horario disponible coincide con tus necesidades.")

    skills = bd.get("skills_match")
    if isinstance(skills, list) and skills:
        overlap = [str(x).strip() for x in skills if str(x).strip()][:3]
        if overlap:
            bullets.append("Tiene experiencia en funciones clave: " + ", ".join(overlap) + ".")
    else:
        skills_text = _clean_text(skills)
        if skills_text:
            bullets.append("Experiencia y funciones compatibles: " + skills_text)

    if bool(bd.get("edad_match")):
        bullets.append("Cumple con el rango de edad que solicitaste.")

    mascotas = _clean_text(bd.get("mascota_penalty"))
    if mascotas:
        if "penalizacion" in mascotas.lower():
            bullets.append("Consideramos el criterio de mascotas al calcular la recomendacion.")
        elif "mascotas" in mascotas.lower():
            bullets.append("No hay conflictos relevantes con el criterio de mascotas.")

    if len(bullets) < 3:
        bullets.append("La recomendacion fue revisada por nuestro equipo de matching.")
    if len(bullets) < 3:
        bullets.append("El perfil cumple varios criterios operativos de la solicitud.")

    return bullets[:6]
