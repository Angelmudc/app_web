"""Human-readable, lossless answer view for authorized Admin screens.

This module is deliberately separate from publication formatting: Admins need
to review private and historical answers that must never appear in copy text.
"""
from __future__ import annotations

import re
from typing import Any

from services import service_catalog as catalog


COMMON_ANSWER_FIELDS = (
    ("ciudad_sector", "Ciudad / sector"),
    ("rutas_cercanas", "Ruta cercana / referencia"),
    ("modalidad_trabajo", "Modalidad de trabajo"),
    ("horario", "Días y horario"),
    ("edad_requerida", "Edad requerida"),
    ("experiencia", "Experiencia requerida"),
    ("sueldo", "Sueldo ofrecido"),
    ("pasaje_aporte", "Pasaje"),
    ("funciones", "Responsabilidades seleccionadas"),
    ("funciones_otro", "Otra responsabilidad"),
    ("tipo_lugar", "Tipo de vivienda"),
    ("tipo_lugar_otro", "Otro tipo de vivienda"),
    ("habitaciones", "Habitaciones"),
    ("banos", "Baños"),
    ("dos_pisos", "Vivienda de dos niveles"),
    ("areas_comunes", "Áreas comunes"),
    ("area_otro", "Otra área común"),
    ("adultos", "Adultos en el hogar"),
    ("ninos", "Niños en el hogar"),
    ("edades_ninos", "Edades de los niños"),
    ("ayuda_cuidado_ninos", "Apoyo para el cuidado de los niños"),
    ("mascota", "Mascotas"),
    ("envejeciente_tipo_cuidado", "Tipo de cuidado de persona envejeciente"),
    ("envejeciente_responsabilidades", "Responsabilidades de cuidado"),
    ("envejeciente_solo_acompanamiento", "Solo acompañamiento"),
    ("envejeciente_nota", "Nota sobre el cuidado"),
    ("nota_cliente", "Observaciones del cliente"),
)

DETAIL_LABELS = {
    "tipo": "Servicio identificado en los detalles",
    "cantidad_ninos": "Cantidad de niños a cuidar",
    "edades_ninos": "Edades de los niños a cuidar",
    "tareas": "Responsabilidades seleccionadas",
    "ninera_tareas_otro": "Otra responsabilidad con los niños",
    "tareas_otro": "Otra responsabilidad (registro histórico)",
    "necesidad_especial_publicable": "Necesidad de cuidado descrita",
    "condicion_especial": "Nota privada sobre cuidados",
    "necesidad_especial_respuesta": "Necesidad de atención especial",
    "ninera_responsable_principal": "Responsable principal del cuidado",
    "responsabilidad_integral": "Responsabilidad integral del cuidado",
    "a_quien_cuida": "Persona que necesita cuidados",
    "cantidad_personas": "Cantidad de personas que necesitan cuidados",
    "edad_persona": "Edad de la persona",
    "edades_personas": "Edades de las personas",
    "personas_cuidado": "Perfiles individuales de cuidado",
    "personas_cuidado_historicas": "Perfiles históricos conservados",
    "personas_cuidado_necesidades_historicas": "Necesidades individuales históricas",
    "responsabilidad_por_persona": "Asignación histórica de responsabilidades por persona",
    "movilidad": "Movilidad",
    "ayuda_higiene": "Ayuda con higiene personal",
    "ayuda_alimentacion": "Ayuda para alimentarse",
    "tipo_asistencia": "Tipo de asistencia requerida",
    "condiciones": "Condiciones de salud indicadas",
    "condicion_otro": "Otra condición indicada",
    "condicion_principal": "Condición principal histórica",
    "responsabilidad_principal": "Responsable principal del cuidado",
    "tipo_inyeccion": "Tipo de inyección requerido",
    "enf_tareas_otro": "Otra tarea de cuidado",
    "requiere_preparacion_profesional": "Requiere preparación profesional",
    "experiencia_requerida": "Experiencia requerida para cuidados",
    "formacion_requerida": "Preparación profesional requerida",
    "finalidad": "Finalidad del servicio de chofer",
    "finalidad_otro": "Otra finalidad del servicio",
    "tipo_servicio": "Finalidad histórica del servicio de chofer",
    "responsabilidades": "Responsabilidades adicionales",
    "vehiculo": "Vehículo que utilizará",
    "tipo_vehiculo": "Tipo de vehículo",
    "tipo_vehiculo_otro": "Otro tipo de vehículo",
    "rutas": "Recorridos seleccionados",
    "rutas_legacy": "Recorridos históricos",
    "rutas_otro": "Otro recorrido",
    "categoria_licencia_requerida": "Categoría de licencia requerida",
    "licencia_requisitos": "Requisitos de licencia históricos",
    "licencia_detalle": "Detalle de licencia histórico",
    "disponibilidad": "Disponibilidad histórica",
    "viajes_largos": "Viajes largos históricos",
    "tipo_establecimiento": "Tipo de establecimiento",
    "tipo_establecimiento_otro": "Otro tipo de establecimiento",
    "tamano": "Tamaño del establecimiento",
    "caracteristicas": "Características y cantidades del establecimiento",
    "despachos": "Cantidad de despachos u oficinas",
    "reuniones": "Cantidad de salas de reuniones",
    "ventas": "Cantidad de áreas de venta",
    "probadores": "Cantidad de probadores",
    "aulas": "Cantidad de aulas",
    "administracion": "Cantidad de oficinas administrativas",
    "consultorios": "Cantidad de consultorios",
    "espera": "Cantidad de salas de espera",
    "comedores": "Cantidad de salones o áreas de comedor",
    "cocinas": "Cantidad de cocinas",
    "areas_comunes": "Cantidad de áreas comunes",
    "espacios_otro": "Otros espacios del establecimiento",
    "areas": "Áreas seleccionadas para limpieza",
    "areas_legacy": "Áreas históricas",
    "areas_otro": "Otra área de limpieza",
    "funciones": "Funciones de limpieza seleccionadas",
    "funciones_otro": "Otras funciones de limpieza",
    "niveles": "Cantidad de niveles",
    "banos": "Cantidad de baños",
    "frecuencia": "Frecuencia histórica de limpieza",
    "horario_limpieza": "Horario histórico de limpieza",
    "trabaja_con_companeros": "Trabajará con otros empleados de limpieza",
    "cantidad_companeros_limpieza": "Cantidad de compañeros de limpieza",
    "tipo_residencial": "Tipo de residencial",
    "tipo_residencial_otro": "Otro tipo de residencial",
    "tipo_instalacion": "Tipo de instalación (registro histórico)",
    "apartamentos": "Cantidad de apartamentos / unidades residenciales",
    "ascensores": "Cantidad de ascensores",
    "escaleras": "Cantidad de escaleras (histórico)",
    "estacionamiento": "Estacionamiento / parqueos",
    "parqueos": "Cantidad de parqueos (histórico)",
    "instalaciones": "Instalaciones a cargo",
    "otras_instalaciones": "Otras instalaciones",
    "areas_comunes": "Áreas comunes (registro histórico)",
    "areas_exteriores": "Áreas exteriores (registro histórico)",
    "manejo_basura": "Manejo de basura (registro histórico)",
    "mantenimiento_basico": "Mantenimiento básico (registro histórico)",
    "responsabilidades_otro": "Otras responsabilidades",
    "cantidad_edificios": "Cantidad de edificios o bloques",
    "parqueos_comunes": "Parqueos comunes",
    "areas_recreativas": "Áreas recreativas comunes",
    "jardines_comunes": "Jardines o áreas verdes comunes",
    "calles_internas": "Calles internas comunes",
    "casas": "Cantidad de casas",
    "unidades": "Cantidad de unidades o propiedades",
    "tiene_ascensor": "Tiene ascensor",
    "trabaja_con_conserjes": "Trabajará con otros conserjes",
    "cantidad_conserjes": "Cantidad de conserjes adicionales",
}

# Structured schedule metadata is already shown once in the common answers
# section as the customer's human-readable modality and schedule.
COMMON_SCHEDULE_DETAIL_KEYS = {
    "horario_tipo", "dias_trabajo", "hora_entrada", "hora_salida",
    "dormida_entrada", "dormida_salida", "modalidad_grupo",
    "modalidad_especifica", "modalidad_otro_text",
}


def _choice_labels() -> dict[str, dict[str, str]]:
    return {
        "tareas": dict(catalog.NINERA_RESPONSIBILITY_CHOICES + catalog.NINERA_LEGACY_TASK_CHOICES
                       + catalog.ENFERMERA_RESPONSIBILITY_CHOICES + catalog.ENFERMERA_LEGACY_TASK_CHOICES),
        "responsabilidades": dict(catalog.ENFERMERA_RESPONSIBILITY_CHOICES + catalog.CONSERJE_RESPONSIBILITY_CHOICES),
        "condiciones": dict(catalog.ENFERMERA_CONDITION_CHOICES),
        "necesidades": dict(catalog.ENFERMERA_PERSON_CARE_NEEDS),
        "movilidad": dict(catalog.ENFERMERA_MOBILITY_CHOICES),
        "tipo_asistencia": dict(catalog.ENFERMERA_ASSISTANCE_CHOICES),
        "tipo_inyeccion": dict(catalog.ENFERMERA_INJECTION_CHOICES),
        "finalidad": dict(catalog.CHOFER_PURPOSE_CHOICES),
        "tipo_servicio": dict(catalog.CHOFER_PURPOSE_CHOICES),
        "tipo_vehiculo": dict(catalog.CHOFER_VEHICLE_CHOICES),
        "vehiculo": {"cliente": "Vehículo del cliente", "empleado": "Vehículo propio del chofer"},
        "categoria_licencia_requerida": dict(catalog.CHOFER_LICENSE_CHOICES),
        "rutas": dict(catalog.CHOFER_ROUTE_CHOICES),
        "tipo_establecimiento": dict(catalog.OFFICE_ESTABLISHMENT_CHOICES),
        "areas": {code: label for choices in catalog.OFFICE_AREA_CHOICES.values() for code, label in choices},
        "funciones": dict(catalog.OFFICE_TASK_CHOICES),
        "tipo_residencial": dict(catalog.CONSERJE_RESIDENTIAL_TYPES),
        "instalaciones": dict(catalog.CONSERJE_INSTALLATION_CHOICES),
        "responsabilidades": dict(catalog.CONSERJE_RESPONSIBILITY_CHOICES),
    }


def _humanize(key: Any) -> str:
    text = str(key).replace("_", " ").strip()
    return re.sub(r"\s+", " ", text).capitalize() or "Respuesta"


def _display_value(value: Any, key: str = "", choices: dict[str, dict[str, str]] | None = None) -> str:
    choices = choices or _choice_labels()
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, dict):
        parts = []
        for child_key, child_value in value.items():
            label = DETAIL_LABELS.get(str(child_key), _humanize(child_key))
            rendered = _display_value(child_value, str(child_key), choices)
            if rendered:
                parts.append(f"{label}: {rendered}")
        return "\n".join(parts)
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(item, dict) for item in value):
            people = []
            for index, item in enumerate(value, 1):
                rendered = _display_value(item, key, choices)
                people.append(f"Persona {index}: {rendered}" if key in {"personas_cuidado", "personas_cuidado_historicas"} else rendered)
            return "\n".join(people)
        label_map = choices.get(key, {})
        return ", ".join(label_map.get(str(item), str(item)) for item in value if item not in (None, ""))
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return choices.get(key, {}).get(value, {"si": "Sí", "no": "No"}.get(value, value))
    return str(value)


def _answer_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for key, value in data.items():
        if str(key) in COMMON_SCHEDULE_DETAIL_KEYS:
            continue
        key_text = str(key).lower()
        if key == "_by_type" or key_text.endswith("sha256") or key_text.endswith("_revision") or str(key).startswith("_"):
            continue
        label = DETAIL_LABELS.get(str(key), _humanize(key))
        if key == "tipo":
            rendered = catalog.service_label(value)
        else:
            rendered = _display_value(value, str(key))
        if rendered:
            rows.append({"key": str(key), "label": label, "value": rendered})
    return rows


def build_admin_solicitud_answer_groups(solicitud: Any) -> list[dict[str, Any]]:
    """Return all stored customer answers, including service-specific history."""
    groups: list[dict[str, Any]] = []
    common = []
    for attr, label in COMMON_ANSWER_FIELDS:
        value = getattr(solicitud, attr, None)
        if attr == "pasaje_aporte":
            value = "Aparte" if value else "Incluido"
        rendered = _display_value(value, attr)
        if rendered:
            common.append({"key": attr, "label": label, "value": rendered})
    groups.append({"title": "Datos generales y condiciones del empleo", "rows": common})

    details = getattr(solicitud, "detalles_servicio", None)
    if not isinstance(details, dict):
        details = {}
    current_code = str(getattr(solicitud, "tipo_servicio", None) or details.get("tipo") or "").strip()
    current = _answer_rows(details)
    if current:
        groups.append({"title": f"Respuestas de {catalog.service_label(current_code)}", "rows": current})
    history = details.get("_by_type") if isinstance(details.get("_by_type"), dict) else {}
    for code, archived in history.items():
        if isinstance(archived, dict):
            rows = _answer_rows(archived)
            if rows:
                groups.append({"title": f"Respuestas históricas de {catalog.service_label(code)}", "rows": rows})
    return groups


def build_admin_preserved_answer_groups(solicitud: Any) -> list[dict[str, Any]]:
    """Return stored service answers not editable in the current type view."""
    details = getattr(solicitud, "detalles_servicio", None)
    if not isinstance(details, dict):
        return []
    groups = []
    current_code = catalog.canonical_service_code(
        getattr(solicitud, "tipo_servicio", None) or details.get("tipo")
    )
    editable_by_service = {
        "NINERA": {"tipo", "cantidad_ninos", "edades_ninos", "tareas", "ninera_tareas_otro", "tareas_otro", "necesidad_especial_publicable", "condicion_especial", "necesidad_especial_respuesta", "ninera_responsable_principal", "responsabilidad_integral"},
        "ENFERMERA": {"tipo", "a_quien_cuida", "cantidad_personas", "edad_persona", "edades_personas", "personas_cuidado", "personas_cuidado_historicas", "movilidad", "ayuda_higiene", "ayuda_alimentacion", "tipo_asistencia", "condiciones", "condicion_otro", "condicion_principal", "tareas", "responsabilidad_principal", "tipo_inyeccion", "enf_tareas_otro", "experiencia_requerida", "formacion_requerida", "experiencia"},
        "CHOFER": {"tipo", "finalidad", "finalidad_otro", "tipo_servicio", "responsabilidades", "vehiculo", "tipo_vehiculo", "tipo_vehiculo_otro", "rutas", "rutas_legacy", "rutas_otro", "categoria_licencia_requerida", "licencia_requisitos", "licencia_detalle"},
        "LIMPIEZA_ESTABLECIMIENTOS": {"tipo", "tipo_establecimiento", "tipo_establecimiento_otro", "tamano", "caracteristicas", "espacios_otro", "areas", "areas_legacy", "areas_otro", "funciones", "funciones_otro", "niveles", "banos", "frecuencia", "horario_limpieza", "trabaja_con_companeros", "cantidad_companeros_limpieza"},
        "CONSERJERIA_RESIDENCIAL": {"tipo", "tipo_residencial", "tipo_residencial_otro", "tipo_instalacion", "niveles", "apartamentos", "ascensores", "banos", "escaleras", "estacionamiento", "parqueos", "instalaciones", "otras_instalaciones", "areas_comunes", "areas_exteriores", "manejo_basura", "responsabilidades", "responsabilidades_otro", "mantenimiento_basico", "trabaja_con_conserjes", "cantidad_conserjes", "cantidad_edificios", "parqueos_comunes", "areas_recreativas", "jardines_comunes", "calles_internas", "casas", "unidades", "tiene_ascensor"},
    }
    active_rows = [
        row for row in _answer_rows(details)
        if row["key"] not in editable_by_service.get(current_code, set())
    ]
    if active_rows:
        groups.append({"title": "Respuestas históricas o campos no editables aquí", "rows": active_rows})
    history = details.get("_by_type") if isinstance(details.get("_by_type"), dict) else {}
    for code, archived in history.items():
        if isinstance(archived, dict):
            rows = _answer_rows(archived)
            if rows:
                groups.append({"title": f"Conservado: {catalog.service_label(code)}", "rows": rows})
    return groups
