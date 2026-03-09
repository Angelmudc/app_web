# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any


FIELD_LABELS: dict[str, str] = {
    "nombre_completo": "Nombre completo",
    "codigo": "Codigo",
    "estado": "Estado",
    "modalidad_trabajo": "Modalidad",
    "modalidad_trabajo_preferida": "Modalidad de trabajo",
    "horario": "Horario",
    "sueldo": "Sueldo",
    "nota_cliente": "Nota del cliente",
    "ciudad_sector": "Ciudad/sector",
    "candidata_id": "Candidata",
    "solicitud_id": "Solicitud",
    "cliente_id": "Cliente",
    "funciones": "Funciones",
    "edad": "Edad",
    "cedula": "Cedula",
    "cedula_norm_digits": "Cedula",
    "numero_telefono": "Telefono",
    "telefono": "Telefono",
    "telefono1": "Telefono",
    "phone": "Telefono",
    "phone_number": "Telefono",
    "whatsapp": "WhatsApp",
    "direccion_completa": "Direccion",
    "rutas_cercanas": "Rutas cercanas",
    "ruta_cercana_1": "Ruta cercana",
    "ruta_cercana_2": "Ruta cercana",
    "ruta_cercana_3": "Ruta cercana",
    "empleo_anterior": "Experiencia",
    "anos_experiencia": "Años de experiencia",
    "areas_experiencia": "Areas de experiencia",
    "contactos_referencias_laborales": "Referencias laborales",
    "referencias_laboral": "Referencias laborales",
    "referencias_laborales": "Referencias laborales",
    "referencias_familiares_detalle": "Referencias familiares",
    "referencias_familiares": "Referencias familiares",
    "acepta_porcentaje_sueldo": "¿Acepta porcentaje del sueldo?",
    "sabe_planchar": "¿Sabe planchar?",
}

SENSITIVE_FIELDS = {
    "cedula",
    "cedula_norm_digits",
    "numero_telefono",
    "telefono",
    "telefono1",
    "phone",
    "phone_number",
    "whatsapp",
}

GENERIC_SENTENCE_BY_FIELD = {
    "contactos_referencias_laborales": "Referencias laborales: modificadas",
    "referencias_laboral": "Referencias laborales: modificadas",
    "referencias_laborales": "Referencias laborales: modificadas",
    "referencias_familiares_detalle": "Referencias familiares: modificadas",
    "referencias_familiares": "Referencias familiares: modificadas",
    "direccion_completa": "Direccion: modificada",
    "empleo_anterior": "Experiencia: actualizada",
    "anos_experiencia": "Años de experiencia: actualizada",
    "areas_experiencia": "Areas de experiencia: actualizadas",
}


def _field_key(name: str | None) -> str:
    return (name or "").strip().lower()


def humanize_audit_field(name: str | None) -> str:
    key = _field_key(name)
    if not key:
        return "Campo"
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    txt = key.replace("_", " ").strip()
    return txt[:1].upper() + txt[1:] if txt else "Campo"


def humanize_audit_value(value: Any) -> str:
    if value is None:
        return "vacio"
    if isinstance(value, bool):
        return "Si" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        items = [humanize_audit_value(v) for v in value]
        txt = ", ".join([x for x in items if x])
        return txt or "vacio"
    txt = str(value).strip()
    return txt if txt else "vacio"


def _sentence_from_change(label: str, before: Any, after: Any) -> str:
    if before in (None, "", "vacio") and after not in (None, "", "vacio"):
        return f"{label}: agregado"
    if before not in (None, "", "vacio") and after in (None, "", "vacio"):
        return f"{label}: eliminado"
    return f"{label}: actualizado"


def humanize_change(field: str | None, before: Any, after: Any) -> dict[str, str | bool]:
    key = _field_key(field)
    label = humanize_audit_field(key)

    if key in SENSITIVE_FIELDS:
        return {
            "label": label,
            "from": "dato protegido",
            "to": "actualizado",
            "sentence": f"{label}: actualizada",
            "sensitive": True,
        }

    if key in GENERIC_SENTENCE_BY_FIELD:
        sentence = GENERIC_SENTENCE_BY_FIELD[key]
    else:
        sentence = _sentence_from_change(label, before, after)

    from_h = humanize_audit_value(before)
    to_h = humanize_audit_value(after)

    if len(from_h) > 90:
        from_h = "texto anterior"
    if len(to_h) > 90:
        to_h = "texto actualizado"

    return {
        "label": label,
        "from": from_h,
        "to": to_h,
        "sentence": sentence,
        "sensitive": False,
    }


def summarize_changed_fields(changes_human: list[dict[str, Any]] | None, max_items: int = 3) -> str:
    rows = changes_human or []
    labels = [str(r.get("label") or "").strip() for r in rows if str(r.get("label") or "").strip()]
    if not labels:
        return "cambios"
    labels = labels[:max(1, int(max_items))]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} y {labels[1]}"
    return f"{', '.join(labels[:-1])} y {labels[-1]}"
