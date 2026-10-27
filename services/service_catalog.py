"""Canonical catalog and validation contracts for employment services.

Historical identifiers are read through one explicit compatibility map. New
public submissions use only the canonical codes exposed by ``SERVICE_CHOICES``.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any


SERVICES: tuple[dict[str, Any], ...] = (
    {
        "code": "DOMESTICA_LIMPIEZA", "label": "Doméstica", "title": "DOMÉSTICA",
        "description": "Apoyo para limpieza, cocina y tareas del hogar.",
        "sections": ("common", "household"), "legacy": True,
    },
    {
        "code": "NINERA", "label": "Niñera", "title": "NIÑERA",
        "description": "Cuidado de niños y apoyo en sus rutinas.",
        "sections": ("common", "children"), "legacy": True,
    },
    {
        "code": "ENFERMERA", "label": "Enfermera / Cuidadora", "title": "ENFERMERA",
        "description": "Acompañamiento y asistencia según la preparación requerida.",
        "sections": ("common", "care"), "legacy": True,
    },
    {
        "code": "CHOFER", "label": "Chofer", "title": "CHOFER",
        "description": "Traslados, vehículo y rutas generales.",
        "sections": ("common", "driving"), "legacy": True,
    },
    {
        "code": "LIMPIEZA_ESTABLECIMIENTOS", "label": "Limpieza de establecimientos", "title": "LIMPIEZA DE ESTABLECIMIENTOS",
        "description": "Limpieza y mantenimiento de oficinas y establecimientos.",
        "sections": ("common", "facility_cleaning"), "legacy": False,
    },
    {
        "code": "CONSERJERIA_RESIDENCIAL", "label": "Conserjería residencial", "title": "CONSERJERÍA RESIDENCIAL",
        "description": "Personal para el cuidado, limpieza y conservación de las áreas comunes de edificios y residenciales.",
        "sections": ("common", "facility_maintenance"), "legacy": False,
    },
)

OFFICE_ESTABLISHMENT_CHOICES = (("oficina", "Oficina o empresa"), ("tienda", "Tienda o local comercial"), ("educativo", "Centro educativo"), ("salud", "Consultorio o centro de salud"), ("restaurante", "Restaurante o cafetería"), ("edificio", "Edificio comercial"), ("otro", "Otro establecimiento"))
OFFICE_AREA_CHOICES = {
    "oficina": (("recepcion", "Recepción"), ("trabajo", "Despachos y áreas de trabajo"), ("reuniones", "Salas de reuniones"), ("banos", "Baños"), ("cocina", "Cocina o comedor del personal"), ("pasillos", "Pasillos y escaleras"), ("otras", "Otras áreas")),
    "tienda": (("ventas", "Área de ventas"), ("cajas", "Mostradores y cajas"), ("probadores", "Probadores"), ("almacen", "Almacén"), ("banos", "Baños"), ("entrada", "Entrada y escaparates"), ("otras", "Otras áreas")),
    "educativo": (("aulas", "Aulas"), ("administracion", "Oficinas administrativas"), ("banos", "Baños"), ("pasillos", "Pasillos y escaleras"), ("patio", "Patio y áreas recreativas"), ("comedor", "Comedor"), ("otras", "Otras áreas")),
    "salud": (("recepcion_espera", "Recepción y sala de espera"), ("consultorios", "Consultorios"), ("administracion", "Oficinas administrativas"), ("banos", "Baños"), ("pasillos", "Pasillos"), ("otras", "Otras áreas")),
    "restaurante": (("comedor", "Salón de comedor"), ("mobiliario", "Mesas y mobiliario"), ("banos", "Baños"), ("cocina", "Cocina"), ("almacen", "Área de almacenamiento"), ("entrada", "Entrada"), ("otras", "Otras áreas")),
    "edificio": (("recepcion", "Recepción"), ("pasillos", "Pasillos"), ("escaleras", "Escaleras"), ("ascensores", "Ascensores"), ("banos", "Baños comunes"), ("estacionamiento", "Áreas de estacionamiento"), ("otras", "Otras áreas")),
    "otro": (("recepcion", "Recepción"), ("trabajo", "Áreas de trabajo"), ("banos", "Baños"), ("pasillos", "Pasillos"), ("escaleras", "Escaleras"), ("almacen", "Almacén"), ("exteriores", "Áreas exteriores"), ("otras", "Otras áreas")),
}
OFFICE_TASK_CHOICES = (("pisos", "Barrer, trapear y limpiar pisos"), ("mobiliario", "Limpiar escritorios, mesas y mobiliario"), ("banos", "Limpiar y desinfectar baños"), ("basura", "Recoger basura y vaciar zafacones"), ("cristales", "Limpiar puertas, cristales y ventanas accesibles"), ("suministros", "Reponer papel higiénico, jabón y suministros"), ("organizar", "Organizar las áreas de trabajo"), ("cafe", "Preparar y servir café o bebidas"), ("visitantes", "Ofrecer agua o café a visitantes"), ("otras", "Otras funciones"))

SERVICE_BY_CODE = {item["code"]: item for item in SERVICES}
SERVICE_CHOICES = [(item["code"], item["label"]) for item in SERVICES]

# Read compatibility only. Never use these aliases as values for new requests.
SERVICE_CODE_ALIASES = {
    "LIMPIEZA_OFICINA": "LIMPIEZA_ESTABLECIMIENTOS",
    "CONSERJE": "CONSERJERIA_RESIDENCIAL",
}
SERVICE_CODES_BY_CANONICAL = {
    code: (code, *(alias for alias, target in SERVICE_CODE_ALIASES.items() if target == code))
    for code in SERVICE_BY_CODE
}


def canonical_service_code(code: str | None) -> str:
    """Return the current service identifier without mutating stored data."""
    raw = str(code or "").strip()
    return SERVICE_CODE_ALIASES.get(raw, raw)


def service_code_variants(code: str | None) -> tuple[str, ...]:
    """Canonical and historical stored values representing one service."""
    canonical = canonical_service_code(code)
    return SERVICE_CODES_BY_CANONICAL.get(canonical, (canonical,) if canonical else ())


def is_legacy_service_code(code: str | None) -> bool:
    raw = str(code or "").strip()
    return raw in SERVICE_CODE_ALIASES

# Stable data contracts for details_servicio. Existing keys remain readable.
DETAIL_CONTRACTS: dict[str, dict[str, Any]] = {
    "NINERA": {"required": ("cantidad_ninos", "edades_ninos"), "keys": ("cantidad_ninos", "edades_ninos", "tareas", "ninera_tareas_otro", "ninera_tareas_otro_revision_sha256", "necesidad_especial_publicable", "necesidad_especial_revision_sha256", "condicion_especial", "necesidad_especial_respuesta", "ninera_responsable_principal", "responsabilidad_integral")},
    "ENFERMERA": {"required": ("a_quien_cuida", "movilidad", "tareas"), "keys": ("a_quien_cuida", "cantidad_personas", "edad_persona", "edades_personas", "personas_cuidado", "personas_cuidado_historicas", "personas_cuidado_necesidades_historicas", "responsabilidad_por_persona", "movilidad", "ayuda_higiene", "ayuda_alimentacion", "tipo_asistencia", "condiciones", "condicion_otro", "condicion_otro_revision_sha256", "tareas", "responsabilidad_integral", "responsabilidad_principal", "tipo_inyeccion", "enf_tareas_otro", "enf_tareas_otro_revision_sha256", "requiere_preparacion_profesional", "experiencia_requerida", "formacion_requerida", "condicion_principal")},
    "CHOFER": {"required": ("finalidad", "tipo_vehiculo"), "keys": ("finalidad", "finalidad_otro", "tipo_servicio", "vehiculo", "tipo_vehiculo", "tipo_vehiculo_otro", "rutas", "rutas_legacy", "rutas_otro", "categoria_licencia_requerida", "responsabilidades", "viajes_largos", "licencia_requisitos", "licencia_detalle", "disponibilidad")},
    "LIMPIEZA_ESTABLECIMIENTOS": {"required": ("tipo_establecimiento",), "keys": ("tipo", "tipo_establecimiento", "tipo_establecimiento_otro", "tamano", "caracteristicas", "espacios_otro", "areas", "areas_otro", "areas_legacy", "funciones", "funciones_otro", "niveles", "banos", "frecuencia", "horario_limpieza", "trabaja_con_companeros", "cantidad_companeros_limpieza")},
    # Keep the legacy keys readable; new submissions use the residential schema below.
    "CONSERJERIA_RESIDENCIAL": {"required": ("tipo_residencial",), "keys": ("tipo", "tipo_residencial", "tipo_residencial_otro", "tipo_instalacion", "niveles", "apartamentos", "ascensores", "banos", "escaleras", "estacionamiento", "parqueos", "instalaciones", "otras_instalaciones", "areas_comunes", "areas_exteriores", "manejo_basura", "responsabilidades", "responsabilidades_otro", "mantenimiento_basico", "trabaja_con_conserjes", "cantidad_conserjes", "cantidad_edificios", "parqueos_comunes", "areas_recreativas", "jardines_comunes", "calles_internas", "casas", "unidades", "tiene_ascensor")},
}

CONSERJE_RESIDENTIAL_TYPES = (
    ("torre_apartamentos", "Torre de apartamentos"),
    ("edificio_apartamentos", "Edificio de apartamentos"),
    ("condominio_residencial", "Condominio residencial"),
    ("residencial_casas", "Residencial de casas"),
    ("otro", "Otro"),
)
CONSERJE_CHARACTERISTIC_FIELDS = {
    "niveles": {"key": "niveles", "name": "svc_conserje_levels", "admin": "conserje_niveles", "label": "Cantidad de niveles", "type": "number", "min": 1},
    "apartamentos": {"key": "apartamentos", "name": "svc_conserje_units", "admin": "conserje_apartamentos", "label": "Cantidad de apartamentos", "type": "number", "min": 1},
    "ascensores": {"key": "ascensores", "name": "svc_conserje_elevators", "admin": "conserje_ascensores", "label": "Cantidad de ascensores", "type": "number", "min": 0},
    "estacionamiento": {"key": "estacionamiento", "name": "svc_conserje_parking", "admin": "conserje_estacionamiento", "label": "¿Tiene parqueos?", "type": "yes_no"},
    "areas_recreativas": {"key": "areas_recreativas", "name": "svc_conserje_recreation", "admin": "conserje_areas_recreativas", "label": "¿Tiene áreas recreativas comunes?", "type": "yes_no"},
    "cantidad_edificios": {"key": "cantidad_edificios", "name": "svc_conserje_buildings", "admin": "conserje_cantidad_edificios", "label": "Cantidad de edificios o bloques", "type": "number", "min": 1},
    "parqueos_comunes": {"key": "parqueos_comunes", "name": "svc_conserje_common_parking", "admin": "conserje_parqueos_comunes", "label": "¿Tiene parqueos comunes?", "type": "yes_no"},
    "jardines_comunes": {"key": "jardines_comunes", "name": "svc_conserje_common_gardens", "admin": "conserje_jardines_comunes", "label": "¿Tiene jardines o áreas verdes comunes?", "type": "yes_no"},
    "casas": {"key": "casas", "name": "svc_conserje_houses", "admin": "conserje_cantidad_casas", "label": "Cantidad de casas", "type": "number", "min": 1},
    "calles_internas": {"key": "calles_internas", "name": "svc_conserje_internal_streets", "admin": "conserje_calles_internas", "label": "¿Tiene calles internas comunes?", "type": "yes_no"},
    "unidades": {"key": "unidades", "name": "svc_conserje_other_units", "admin": "conserje_unidades", "label": "Cantidad de unidades o propiedades", "type": "number", "min": 1},
    "tiene_ascensor": {"key": "tiene_ascensor", "name": "svc_conserje_has_elevator", "admin": "conserje_tiene_ascensor", "label": "¿Tiene ascensor?", "type": "yes_no"},
}
CONSERJE_INSTALLATION_CHOICES = (
    ("lobby_recepcion", "Lobby o recepción"), ("entrada_principal", "Entrada principal"),
    ("entradas_comunes", "Entradas comunes"), ("pasillos", "Pasillos compartidos"),
    ("calles_internas", "Calles internas"), ("aceras", "Aceras"),
    ("escaleras", "Escaleras comunes"), ("ascensores", "Ascensores"),
    ("parqueos", "Parqueos"), ("jardines", "Jardines y áreas verdes"),
    ("patio_exteriores", "Patio y áreas exteriores"), ("area_basura", "Área de basura"),
    ("salon_actividades", "Salón de actividades"), ("gimnasio_recreativas", "Gimnasio o áreas recreativas"),
    ("piscina_alrededores", "Piscina y alrededores"), ("parques_infantiles", "Parques o áreas infantiles"),
    ("gazebo", "Gazebo"), ("otras", "Otras instalaciones"),
)
CONSERJE_INSTALLATIONS_BY_TYPE = {
    "torre_apartamentos": ("lobby_recepcion", "pasillos", "ascensores", "escaleras", "parqueos", "area_basura", "gimnasio_recreativas", "piscina_alrededores", "salon_actividades", "jardines", "otras"),
    "edificio_apartamentos": ("entrada_principal", "pasillos", "escaleras", "ascensores", "parqueos", "area_basura", "patio_exteriores", "otras"),
    "condominio_residencial": ("entradas_comunes", "pasillos", "patio_exteriores", "parqueos", "jardines", "area_basura", "gimnasio_recreativas", "piscina_alrededores", "salon_actividades", "escaleras", "ascensores", "otras"),
    "residencial_casas": ("entrada_principal", "calles_internas", "aceras", "jardines", "parques_infantiles", "area_basura", "gazebo", "otras"),
    "otro": ("entrada_principal", "entradas_comunes", "lobby_recepcion", "pasillos", "calles_internas", "aceras", "escaleras", "ascensores", "parqueos", "jardines", "patio_exteriores", "area_basura", "salon_actividades", "gimnasio_recreativas", "piscina_alrededores", "parques_infantiles", "gazebo", "otras"),
}
CONSERJE_RESPONSIBILITY_CHOICES = (
    ("limpieza_areas_comunes", "Limpieza de áreas comunes"),
    ("traslado_basura", "Recogida y traslado de basura"),
    ("inspeccion_visual", "Inspección visual de instalaciones"),
    ("reportar_averias", "Reporte de averías"),
    ("organizar_areas", "Organización de espacios comunes"),
    ("apoyo_residentes", "Apoyo operativo básico a residentes"),
    ("riego_jardines", "Riego de jardines y plantas"),
    ("mantenimiento_basico", "Mantenimiento básico no especializado"),
    ("otras", "Otras responsabilidades"),
)
CONSERJE_GENERAL_RESPONSIBILITIES = ("limpieza_areas_comunes", "traslado_basura", "inspeccion_visual", "reportar_averias", "organizar_areas", "apoyo_residentes", "otras")
CONSERJE_RESPONSIBILITIES_BY_TYPE = {kind: (*CONSERJE_GENERAL_RESPONSIBILITIES, "riego_jardines") for kind in CONSERJE_INSTALLATIONS_BY_TYPE}
CONSERJE_INSTALLATION_REQUIREMENTS = {
    "ascensores": "elevator", "parqueos": "parking", "jardines": "gardens",
    "gimnasio_recreativas": "recreation", "salon_actividades": "recreation",
    "piscina_alrededores": "recreation", "parques_infantiles": "recreation",
    "calles_internas": "streets", "aceras": "streets", "gazebo": "recreation",
}
CONSERJE_RESIDENTIAL_PROFILES = {
    "torre_apartamentos": {"fields": ("niveles", "apartamentos", "ascensores", "estacionamiento", "areas_recreativas")},
    "edificio_apartamentos": {"fields": ("niveles", "apartamentos", "tiene_ascensor", "estacionamiento")},
    "condominio_residencial": {"fields": ("cantidad_edificios", "apartamentos", "parqueos_comunes", "areas_recreativas", "jardines_comunes"), "labels": {"apartamentos": "Cantidad total de apartamentos"}},
    "residencial_casas": {"fields": ("casas", "calles_internas", "jardines_comunes", "areas_recreativas")},
    "otro": {"fields": ("unidades",)},
}
CONSERJE_RESPONSIBILITY_CHOICES = (
    ("limpieza_areas_comunes", "Limpieza de áreas comunes"),
    ("traslado_basura", "Recolección y traslado de basura"),
    ("inspeccion_visual", "Inspección visual de instalaciones"),
    ("reportar_averias", "Reportar averías y desperfectos"),
    ("mantenimiento_basico", "Mantenimiento básico no especializado"),
    ("riego_jardines", "Riego de jardines y plantas"),
    ("organizar_areas", "Organización de áreas comunes"),
    ("apoyo_residentes", "Apoyo operativo básico a residentes"),
    ("otras", "Otras responsabilidades"),
)

CHOFER_LICENSE_CHOICES = (
    ("categoria_01", "Categoría 01 - Motocicletas"),
    ("categoria_02", "Categoría 02 - Vehículos livianos"),
    ("categoria_03", "Categoría 03 - Vehículos pesados"),
    ("categoria_04", "Categoría 04 - Vehículos pesados de mayor capacidad"),
    ("categoria_05", "Categoría 05 - Vehículos especiales"),
)
CHOFER_ROUTE_CHOICES = (
    ("dentro_ciudad", "Dentro de la ciudad"), ("entre_ciudades", "Entre ciudades"),
    ("viajes_ocasionales", "Viajes ocasionales"), ("rutas_fijas", "Rutas fijas"),
    ("larga_distancia", "Viajes de larga distancia"), ("otro", "Otro"),
)
CHOFER_PURPOSE_CHOICES = (
    ("personal_familiar", "Chofer personal o familiar"),
    ("traslado_empleados", "Traslado de empleados"),
    ("transporte_diligencias", "Transporte para diligencias"),
    ("otro", "Otro"),
)
CHOFER_VEHICLE_CHOICES = (("carro", "Carro"), ("yipeta", "Yipeta"), ("pickup", "Pickup"), ("minibus", "Minibús"), ("otro", "Otro"))

# These codes and labels are the shared backend contract used by the public
# form, Admin, validation, and publication copy.
NINERA_RESPONSIBILITY_CHOICES = (
    ("supervision", "Cuidado y supervisión de los niños"),
    ("banar_vestir", "Bañar y vestir a los niños"),
    ("preparar_comida_ninos", "Preparar alimentos exclusivamente para los niños"),
    ("alimentar", "Alimentar a los niños"),
    ("lavar_ropa_ninos", "Lavar la ropa de los niños"),
    ("planchar_ropa_ninos", "Planchar la ropa de los niños"),
    ("ordenar_dormitorios_ninos", "Limpiar y organizar los dormitorios de los niños"),
    ("ordenar_juegos_ninos", "Limpiar y organizar las áreas de juego"),
    ("llevar_colegio", "Llevar a los niños al colegio"),
    ("recoger_colegio", "Recoger a los niños del colegio"),
    ("tareas_escolares", "Ayudar con las tareas escolares"),
    ("acompanar_actividades", "Acompañar a actividades extracurriculares"),
    ("otro", "Otras responsabilidades"),
)
NINERA_INTEGRAL_RESPONSIBILITIES = frozenset({
    "supervision", "banar_vestir", "preparar_comida_ninos", "alimentar",
    "lavar_ropa_ninos", "ordenar_dormitorios_ninos", "ordenar_juegos_ninos",
})
NINERA_RESPONSIBILITY_GROUPS = (
    {"code": "daily_care", "label": "Cuidado diario", "responsibilities": ("supervision", "banar_vestir", "preparar_comida_ninos", "alimentar")},
    {"code": "children_clothes_spaces", "label": "Ropa y espacios de los niños", "responsibilities": ("lavar_ropa_ninos", "planchar_ropa_ninos", "ordenar_dormitorios_ninos", "ordenar_juegos_ninos")},
    {"code": "education_activities", "label": "Educación y actividades", "responsibilities": ("llevar_colegio", "recoger_colegio", "tareas_escolares", "acompanar_actividades", "otro")},
)
NINERA_SPECIAL_NEED_CHOICES = (("no", "No"), ("si", "Sí"))
NINERA_LEGACY_SPECIAL_NEED = ("conversar", "Prefirió conversarlo con la agencia (histórico)")
NINERA_EXPERIENCE_CHOICES = (("Niñera", "Niñera"), ("Enfermera", "Enfermera"))
NINERA_LEGACY_TASK_CHOICES = (
    ("jugar", "Actividad recreativa (histórico)"),
    ("tareas", "Apoyo escolar (histórico)"),
    ("dormir", "Rutina de sueño (histórico)"),
)

# ENFERMERA keeps its older JSON keys and codes readable while new requests use
# explicit, catalog-backed values and separate reviewed free-text fields.
ENFERMERA_EXPERIENCE_CHOICES = (
    ("Enfermera", "Enfermera · formación en enfermería"),
    ("Cuidadora de envejecientes", "Cuidadora de envejecientes · experiencia con adultos mayores"),
    ("Cuidadora de personas encamadas", "Cuidadora de personas encamadas · experiencia con movilidad muy limitada"),
)
ENFERMERA_MOBILITY_CHOICES = (
    ("autonomo", "Independiente"),
    ("parcial", "Necesita ayuda para movilizarse"),
    ("postrado", "Encamada"),
)
ENFERMERA_PERSON_CARE_NEEDS = (
    ("higiene", "Ayuda con higiene personal"),
    ("alimentacion", "Ayuda para alimentarse"),
    ("movilizacion", "Ayuda para movilizarse"),
    ("supervision", "Requiere supervisión individual"),
    ("medicacion", "Apoyo con medicamentos según indicación"),
    ("procedimientos", "Procedimientos de enfermería"),
)
ENFERMERA_ASSISTANCE_CHOICES = (
    ("acompanamiento", "Acompañamiento y supervisión general"),
    ("cotidiana", "Asistencia con actividades cotidianas"),
    ("enfermeria", "Cuidados que requieren personal de enfermería"),
)
ENFERMERA_CONDITION_CHOICES = (
    ("alzheimer_demencia", "Alzheimer o demencia"),
    ("parkinson", "Parkinson"),
    ("diabetes", "Diabetes"),
    ("hipertension", "Hipertensión arterial"),
    ("cardiaca", "Enfermedades cardíacas"),
    ("acv", "Secuelas de accidente cerebrovascular (ACV)"),
    ("artritis_artrosis", "Artritis o artrosis"),
    ("respiratorias", "Enfermedades respiratorias"),
    ("movilidad", "Problemas de movilidad"),
    ("otra", "Otra condición"),
    ("ninguna", "Ninguna de las anteriores"),
)


def parse_enfermera_personas(raw: Any) -> list[dict[str, Any]]:
    """Return only structured, non-identifying per-person care fields."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    allowed_mobility = {value for value, _ in ENFERMERA_MOBILITY_CHOICES}
    allowed_conditions = {value for value, _ in ENFERMERA_CONDITION_CHOICES}
    allowed_responsibilities = {value for value, _ in ENFERMERA_RESPONSIBILITY_CHOICES}
    people = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        raw_age = item.get("edad")
        if type(raw_age) is int:
            age = raw_age
        elif isinstance(raw_age, str) and re.fullmatch(r"\d{1,3}", raw_age.strip()):
            age = int(raw_age.strip())
        else:
            age = None
        mobility = str(item.get("movilidad") or "")
        conditions = item.get("condiciones") if isinstance(item.get("condiciones"), list) else []
        needs = item.get("necesidades") if isinstance(item.get("necesidades"), list) else []
        profile = {
            "edad": age if age is not None and 0 <= age <= 120 else None,
            "movilidad": mobility if mobility in allowed_mobility else "",
            "condiciones": list(dict.fromkeys(str(code) for code in conditions if str(code) in allowed_conditions)),
        }
        if "necesidades" in item:
            profile["necesidades"] = list(dict.fromkeys(str(code).strip()[:100] for code in needs if isinstance(code, str) and code.strip()))
        if "responsabilidades" in item:
            responsibilities = item.get("responsabilidades") if isinstance(item.get("responsabilidades"), list) else []
            profile["responsabilidades"] = list(dict.fromkeys(
                str(value) for value in responsibilities if isinstance(value, str) and value in allowed_responsibilities
            ))
        other = item.get("responsabilidades_otro")
        if isinstance(other, str) and other.strip():
            profile["responsabilidades_otro"] = other.strip()[:250]
        people.append(profile)
    return people


def enfermera_responsibility_lines(person: dict[str, Any]) -> list[str]:
    """Format only explicitly assigned per-person responsibilities for copies."""
    labels = dict(ENFERMERA_RESPONSIBILITY_CHOICES)
    selected = [labels[code] for code in person.get("responsabilidades", []) if code in labels and code != "otro"]
    if "otro" in person.get("responsabilidades", []) and str(person.get("responsabilidades_otro") or "").strip():
        selected.append(str(person["responsabilidades_otro"]).strip())
    return selected


def parse_enfermera_necesidades_historicas(raw: Any) -> list[dict[str, Any]]:
    """Preserve legacy per-person task selections for history only."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    by_index: dict[int, list[str]] = {}
    for entry in raw[:20]:
        if not isinstance(entry, dict):
            continue
        needs = entry.get("necesidades")
        if not isinstance(needs, list):
            continue
        index = max(0, min(19, int(entry.get("indice", 0)))) if str(entry.get("indice", 0)).isdigit() else 0
        by_index.setdefault(index, [])
        by_index[index].extend(str(code).strip()[:100] for code in needs if isinstance(code, str) and code.strip())
    return [{"indice": index, "necesidades": list(dict.fromkeys(needs))}
            for index, needs in sorted(by_index.items()) if needs]


def parse_enfermera_perfiles_historicos(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    by_index = {}
    for entry in raw[:20]:
        if not isinstance(entry, dict):
            continue
        profile = entry.get("perfil")
        if not isinstance(profile, dict):
            continue
        parsed = parse_enfermera_personas([profile])
        if parsed:
            index = entry.get("indice", 0)
            safe_index = max(0, min(19, int(index))) if str(index).isdigit() else 0
            by_index[safe_index] = {"indice": safe_index, "perfil": parsed[0]}
    return [by_index[index] for index in sorted(by_index)]


def parse_enfermera_responsabilidad_por_persona(raw: Any, count: Any = None, tasks: Any = None) -> dict[str, int]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    try:
        people_count = int(count or 0)
    except (TypeError, ValueError):
        people_count = 0
    valid_tasks = {code for code, _ in ENFERMERA_RESPONSIBILITY_CHOICES}
    selected = {str(item) for item in tasks or []}
    result = {}
    for code, index in raw.items():
        try:
            person_index = int(index)
        except (TypeError, ValueError):
            continue
        if code in valid_tasks and code in selected and 1 <= person_index <= people_count and people_count > 1:
            result[code] = person_index
    return result
ENFERMERA_RESPONSIBILITY_CHOICES = (
    ("acompanamiento", "Acompañar y supervisar a la persona"),
    ("aseo", "Bañar y realizar higiene personal"),
    ("cambiar_panales", "Cambiar pañales"),
    ("vestir", "Vestir a la persona"),
    ("alimentar", "Ayudar con la alimentación"),
    ("movilizar", "Movilizarla o ayudarla a levantarse"),
    ("cambiar_posicion", "Cambiarla de posición si está encamada"),
    ("recordar_medicamentos", "Recordar los horarios de medicamentos"),
    ("administrar_medicamentos", "Administrar medicamentos prescritos"),
    ("administrar_inyecciones", "Administrar medicamentos mediante inyecciones"),
    ("tomar_presion", "Tomar la presión arterial"),
    ("signos_vitales", "Controlar signos vitales"),
    ("curaciones", "Realizar curaciones"),
    ("consultas", "Acompañar a consultas médicas"),
    ("preparar_alimentos", "Preparar alimentos para la persona"),
    ("lavar_ropa", "Lavar su ropa"),
    ("organizar_habitacion", "Organizar su habitación"),
    ("conversacion", "Acompañamiento y conversación"),
    ("otro", "Otras responsabilidades"),
)
ENFERMERA_RESPONSIBILITY_GROUPS = (
    {"code": "cuidado_personal", "label": "Cuidado personal", "responsibilities": ("acompanamiento", "aseo", "cambiar_panales", "vestir", "alimentar", "movilizar", "cambiar_posicion")},
    {"code": "salud_asistencia", "label": "Salud y asistencia", "responsibilities": ("recordar_medicamentos", "administrar_medicamentos", "administrar_inyecciones", "tomar_presion", "signos_vitales", "curaciones", "consultas")},
    {"code": "otras_responsabilidades", "label": "Otras responsabilidades", "responsibilities": ("preparar_alimentos", "lavar_ropa", "organizar_habitacion", "conversacion", "otro")},
)
ENFERMERA_PROFESSIONAL_RESPONSIBILITIES = frozenset({
    "administrar_medicamentos", "administrar_inyecciones", "tomar_presion", "signos_vitales", "curaciones",
})
ENFERMERA_INJECTION_CHOICES = (
    ("subcutanea", "Subcutánea (por ejemplo, insulina)."),
    ("intramuscular", "Intramuscular."),
    ("otra_no_sabe", "Otra / No sabe especificar."),
)
ENFERMERA_LEGACY_TASK_CHOICES = (
    ("medicacion", "Administrar medicamentos (registro anterior)"),
    ("control", "Control de signos / chequeos básicos (registro anterior)"),
    ("otro_legado", "Otra tarea de cuidado (registro anterior)"),
)


def get_service(code: str | None) -> dict[str, Any] | None:
    return SERVICE_BY_CODE.get(canonical_service_code(code))


def service_label(code: str | None, *, legacy_default: str = "") -> str:
    raw = str(code or "").strip()
    if raw == "CONSERJE":
        return "Conserje (histórico)"
    if raw == "LIMPIEZA_OFICINA":
        return "Limpieza de oficina (histórico)"
    service = get_service(code)
    return str(service["label"]) if service else (legacy_default if not code else str(code))


def public_catalog() -> list[dict[str, Any]]:
    """Return JSON safe frontend configuration with no validation duplication."""
    result = [
        {key: deepcopy(value) if isinstance(value, tuple) else value
         for key, value in item.items()}
        for item in SERVICES
    ]
    for item in result:
        item["legacy_codes"] = list(service_code_variants(item["code"])[1:])
        if item["code"] == "NINERA":
            item["responsibilities"] = [{"code": code, "label": label} for code, label in NINERA_RESPONSIBILITY_CHOICES]
            item["integral_responsibilities"] = sorted(NINERA_INTEGRAL_RESPONSIBILITIES)
            item["experience_choices"] = [{"code": code, "label": label} for code, label in NINERA_EXPERIENCE_CHOICES]
            labels = dict(NINERA_RESPONSIBILITY_CHOICES)
            item["responsibility_groups"] = [
                {"code": group["code"], "label": group["label"],
                 "responsibilities": [{"code": code, "label": labels[code]} for code in group["responsibilities"]]}
                for group in NINERA_RESPONSIBILITY_GROUPS
            ]
            item["special_need_choices"] = [{"code": code, "label": label} for code, label in NINERA_SPECIAL_NEED_CHOICES]
        elif item["code"] == "ENFERMERA":
            item["experience_choices"] = [{"code": code, "label": label} for code, label in ENFERMERA_EXPERIENCE_CHOICES]
            item["mobility_choices"] = [{"code": code, "label": label} for code, label in ENFERMERA_MOBILITY_CHOICES]
            item["assistance_choices"] = [{"code": code, "label": label} for code, label in ENFERMERA_ASSISTANCE_CHOICES]
            item["condition_choices"] = [{"code": code, "label": label} for code, label in ENFERMERA_CONDITION_CHOICES]
            item["person_care_needs"] = [{"code": code, "label": label} for code, label in ENFERMERA_PERSON_CARE_NEEDS]
            item["responsibilities"] = [{"code": code, "label": label} for code, label in ENFERMERA_RESPONSIBILITY_CHOICES]
            labels = dict(ENFERMERA_RESPONSIBILITY_CHOICES)
            item["responsibility_groups"] = [
                {"code": group["code"], "label": group["label"],
                 "responsibilities": [{"code": code, "label": labels[code]} for code in group["responsibilities"]]}
                for group in ENFERMERA_RESPONSIBILITY_GROUPS
            ]
            item["professional_responsibilities"] = sorted(ENFERMERA_PROFESSIONAL_RESPONSIBILITIES)
        elif item["code"] == "LIMPIEZA_ESTABLECIMIENTOS":
            item["establishment_choices"] = [{"code": code, "label": label} for code, label in OFFICE_ESTABLISHMENT_CHOICES]
            item["area_choices"] = {kind: [{"code": code, "label": label} for code, label in options] for kind, options in OFFICE_AREA_CHOICES.items()}
            item["task_choices"] = [{"code": code, "label": label} for code, label in OFFICE_TASK_CHOICES]
        elif item["code"] == "CHOFER":
            item["purpose_choices"] = [{"code": code, "label": label} for code, label in CHOFER_PURPOSE_CHOICES]
            item["vehicle_choices"] = [{"code": code, "label": label} for code, label in CHOFER_VEHICLE_CHOICES]
            item["license_choices"] = [{"code": code, "label": label} for code, label in CHOFER_LICENSE_CHOICES]
            item["route_choices"] = [{"code": code, "label": label} for code, label in CHOFER_ROUTE_CHOICES]
        elif item["code"] == "CONSERJERIA_RESIDENCIAL":
            item["experience_choices"] = [{"code": "Conserjería", "label": "Conserjería"}]
            item["residential_types"] = [{"code": code, "label": label} for code, label in CONSERJE_RESIDENTIAL_TYPES]
            item["installation_choices"] = [{"code": code, "label": label} for code, label in CONSERJE_INSTALLATION_CHOICES]
            item["responsibility_choices"] = [{"code": code, "label": label} for code, label in CONSERJE_RESPONSIBILITY_CHOICES]
            labels = dict(CONSERJE_INSTALLATION_CHOICES)
            characteristic_types: dict[str, list[str]] = {}
            for kind, profile in CONSERJE_RESIDENTIAL_PROFILES.items():
                for field in profile["fields"]:
                    characteristic_types.setdefault(field, []).append(kind)
            item["characteristic_fields"] = [
                {**dict(CONSERJE_CHARACTERISTIC_FIELDS[field]), "types": ",".join(kinds)}
                for field, kinds in characteristic_types.items()
            ]
            item["residential_profiles"] = {
                kind: {
                    "fields": [
                        {**dict(CONSERJE_CHARACTERISTIC_FIELDS[field]), "label": profile.get("labels", {}).get(field, CONSERJE_CHARACTERISTIC_FIELDS[field]["label"])}
                        for field in profile["fields"]
                    ],
                    "installations": [
                        {"code": code, "label": labels[code], "requires": CONSERJE_INSTALLATION_REQUIREMENTS.get(code, "")}
                        for code in CONSERJE_INSTALLATIONS_BY_TYPE[kind]
                    ],
                    "responsibilities": [
                        {"code": code, "label": dict(CONSERJE_RESPONSIBILITY_CHOICES)[code]}
                        for code in CONSERJE_RESPONSIBILITIES_BY_TYPE[kind]
                    ],
                }
                for kind, profile in CONSERJE_RESIDENTIAL_PROFILES.items()
            }
            installation_types: dict[str, list[str]] = {}
            for kind, codes in CONSERJE_INSTALLATIONS_BY_TYPE.items():
                for code in codes:
                    installation_types.setdefault(code, []).append(kind)
            item["installation_options"] = [
                {"code": code, "label": label, "types": ",".join(installation_types.get(code, ())), "requires": CONSERJE_INSTALLATION_REQUIREMENTS.get(code, "") }
                for code, label in CONSERJE_INSTALLATION_CHOICES
            ]
            responsibility_types: dict[str, list[str]] = {}
            for kind, codes in CONSERJE_RESPONSIBILITIES_BY_TYPE.items():
                for code in codes:
                    responsibility_types.setdefault(code, []).append(kind)
            item["responsibility_options"] = [
                {"code": code, "label": label, "types": ",".join(responsibility_types.get(code, ())) }
                for code, label in CONSERJE_RESPONSIBILITY_CHOICES
            ]
    return result


def validate_service_details(code: str | None, details: Any, *, strict_public: bool = False) -> dict[str, str]:
    """Validate only details required by the selected type; legacy may be partial."""
    raw_code = str(code or "").strip()
    if strict_public and is_legacy_service_code(raw_code):
        return {"tipo_servicio": "Selecciona el identificador vigente del servicio."}
    code = canonical_service_code(raw_code)
    service = get_service(code)
    if not service or not isinstance(details, dict):
        return {}
    errors: dict[str, str] = {}
    new_nurse_schema = code == "ENFERMERA" and isinstance(details, dict) and (
        strict_public or any(details.get(key) not in (None, "", []) for key in (
            "cantidad_personas", "edad_persona", "edades_personas", "tipo_asistencia",
            "condiciones", "ayuda_higiene", "ayuda_alimentacion", "personas_cuidado",
        ))
    )
    required_keys = DETAIL_CONTRACTS.get(str(code), {}).get("required", ())
    # Existing Conserje requests use the former free-text schema and must
    # remain valid for viewing and administrative edits.
    if code == "CONSERJERIA_RESIDENCIAL" and not strict_public:
        required_keys = ()
    if new_nurse_schema:
        if strict_public or details.get("personas_cuidado"):
            required_keys = ("cantidad_personas", "tipo_asistencia", "personas_cuidado")
        else:
            required_keys = ("cantidad_personas", "movilidad", "tipo_asistencia", "condiciones")
    for key in required_keys:
        if code == "CHOFER" and key == "finalidad" and not strict_public and details.get("tipo_servicio"):
            continue
        value = details.get(key)
        if value is None or value == "" or value == []:
            errors[key] = "Completa este dato para continuar."
    if code == "NINERA" and isinstance(details.get("tareas"), list):
        # Admin must accept stored historical task codes when editing old
        # requests. The public normalizer below still admits only current codes.
        allowed = ({value for value, _label in NINERA_RESPONSIBILITY_CHOICES}
                   | {value for value, _label in NINERA_LEGACY_TASK_CHOICES})
        if any(str(value) not in allowed for value in details["tareas"]):
            errors["tareas"] = "Selecciona responsabilidades válidas para el cuidado infantil."
        if details.get("necesidad_especial_respuesta") not in (None, "", "no", "si", "conversar"):
            errors["necesidad_especial_respuesta"] = "Selecciona una opción válida."
        if details.get("necesidad_especial_respuesta") == "si" and not str(details.get("necesidad_especial_publicable") or "").strip():
            errors["necesidad_especial_publicable"] = "Describe los cuidados especiales que necesitará el niño."
        if len(str(details.get("necesidad_especial_publicable") or "")) > 500:
            errors["necesidad_especial_publicable"] = "La descripción de cuidados no puede superar 500 caracteres."
        if details.get("ninera_responsable_principal") not in (None, "", "si", "no"):
            errors["ninera_responsable_principal"] = "Selecciona una opción válida."
    if code == "ENFERMERA":
        if not new_nurse_schema and details.get("requiere_preparacion_profesional") and not str(details.get("formacion_requerida") or "").strip():
            errors["formacion_requerida"] = "Indica la preparación profesional requerida."
        new_schema = new_nurse_schema
        if new_schema:
            valid_tasks = {value for value, _label in ENFERMERA_RESPONSIBILITY_CHOICES} | {value for value, _label in ENFERMERA_LEGACY_TASK_CHOICES}
            valid_conditions = {value for value, _label in ENFERMERA_CONDITION_CHOICES}
            tasks = details.get("tareas") if isinstance(details.get("tareas"), list) else []
            conditions = details.get("condiciones") if isinstance(details.get("condiciones"), list) else []
            person_profiles = parse_enfermera_personas(details.get("personas_cuidado"))
            person_responsibilities_present = any("responsabilidades" in profile for profile in person_profiles)
            if person_profiles and person_responsibilities_present:
                allowed_person_tasks = {value for value, _label in ENFERMERA_RESPONSIBILITY_CHOICES}
                raw_profiles = details.get("personas_cuidado")
                if isinstance(raw_profiles, str):
                    try:
                        raw_profiles = json.loads(raw_profiles)
                    except (TypeError, ValueError):
                        raw_profiles = []
                if not isinstance(raw_profiles, list):
                    raw_profiles = []
                for index, (profile, raw_profile) in enumerate(zip(person_profiles, raw_profiles), start=1):
                    selected = profile.get("responsabilidades", [])
                    raw_selected = raw_profile.get("responsabilidades", []) if isinstance(raw_profile, dict) else []
                    if not isinstance(raw_selected, list) or any(str(value) not in allowed_person_tasks for value in raw_selected):
                        errors["personas_cuidado"] = f"Revisa las responsabilidades de la persona {index}."
                        break
                    if "otro" in selected and not str(profile.get("responsabilidades_otro") or "").strip():
                        errors["personas_cuidado"] = f"Describe las otras responsabilidades de la persona {index}."
                        break
                    raw_other = raw_profile.get("responsabilidades_otro") if isinstance(raw_profile, dict) else None
                    if raw_other is not None and (not isinstance(raw_other, str) or len(raw_other) > 250):
                        errors["personas_cuidado"] = f"Las otras responsabilidades de la persona {index} superan 250 caracteres."
                        break
            if any(str(value) not in valid_tasks for value in tasks):
                errors["tareas"] = "Selecciona responsabilidades válidas para ENFERMERA."
            attribution = details.get("responsabilidad_por_persona") or {}
            if attribution and not isinstance(attribution, dict):
                errors["responsabilidad_por_persona"] = "Revisa a qué persona aplica cada responsabilidad."
            elif isinstance(attribution, dict) and attribution != parse_enfermera_responsabilidad_por_persona(attribution, details.get("cantidad_personas"), tasks):
                errors["responsabilidad_por_persona"] = "Revisa a qué persona aplica cada responsabilidad."
            if not person_profiles and (not conditions or any(str(value) not in valid_conditions for value in conditions)):
                errors["condiciones"] = "Selecciona una condición o indica que no hay ninguna."
            if not person_profiles and "ninguna" in conditions and len(conditions) != 1:
                errors["condiciones"] = "Ninguna de las anteriores no se puede combinar con otras condiciones."
            if not person_profiles and "otra" in conditions and not str(details.get("condicion_otro") or "").strip():
                errors["condicion_otro"] = "Especifica la otra condición."
            if details.get("cantidad_personas") in (None, "", 0, "0"):
                errors["cantidad_personas"] = "Indica cuántas personas necesitan cuidados."
            else:
                try:
                    count = int(details.get("cantidad_personas"))
                    if count < 1 or count > 20:
                        errors["cantidad_personas"] = "Indica una cantidad entre 1 y 20 personas."
                    elif not person_profiles and count == 1 and details.get("edad_persona") in (None, ""):
                        errors["edad_persona"] = "Indica la edad de la persona."
                    elif not person_profiles and count > 1 and not str(details.get("edades_personas") or "").strip():
                        errors["edades_personas"] = "Indica las edades de las personas."
                    if not person_profiles and count == 1 and details.get("edad_persona") not in (None, ""):
                        try:
                            if not 0 <= int(details.get("edad_persona")) <= 120:
                                errors["edad_persona"] = "Indica una edad entre 0 y 120 años."
                        except (TypeError, ValueError):
                            errors["edad_persona"] = "Indica una edad válida."
                    if not person_profiles and count > 1 and details.get("edades_personas") and not re.fullmatch(r"[0-9\s,;yYaáñÑoOsS-]+", str(details["edades_personas"]).strip()):
                        errors["edades_personas"] = "Escribe solo las edades, separadas por coma o por la letra y."
                    if strict_public or details.get("personas_cuidado"):
                        people = parse_enfermera_personas(details.get("personas_cuidado"))
                        if len(people) != count:
                            errors["personas_cuidado"] = "Completa el perfil de cada persona."
                        else:
                            valid_mobility = {value for value, _ in ENFERMERA_MOBILITY_CHOICES}
                            valid_conditions = {value for value, _ in ENFERMERA_CONDITION_CHOICES}
                            for index, person in enumerate(people, start=1):
                                if person.get("edad") is None:
                                    errors["personas_cuidado"] = f"Indica una edad válida para la persona {index}."
                                    break
                                if person.get("movilidad") not in valid_mobility:
                                    errors["personas_cuidado"] = f"Selecciona la movilidad de la persona {index}."
                                    break
                                conditions_for_person = person.get("condiciones") or []
                                if not conditions_for_person or any(value not in valid_conditions for value in conditions_for_person):
                                    errors["personas_cuidado"] = f"Selecciona las condiciones de la persona {index}, o Ninguna."
                                    break
                                if "ninguna" in conditions_for_person and len(conditions_for_person) != 1:
                                    errors["personas_cuidado"] = f"Ninguna no se puede combinar para la persona {index}."
                                    break
                except (TypeError, ValueError):
                    errors["cantidad_personas"] = "Indica una cantidad válida de personas."
            mobility_values = {value for value, _label in ENFERMERA_MOBILITY_CHOICES}
            if not person_profiles and details.get("movilidad") not in mobility_values:
                errors["movilidad"] = "Selecciona el nivel de movilidad."
            if details.get("tipo_asistencia") not in {value for value, _label in ENFERMERA_ASSISTANCE_CHOICES}:
                errors["tipo_asistencia"] = "Selecciona el tipo de asistencia requerida."
            for key in (() if person_profiles else ("ayuda_higiene", "ayuda_alimentacion")):
                if details.get(key) not in {"si", "no"}:
                    errors[key] = "Selecciona Sí o No."
            if "otro" in tasks and not str(details.get("enf_tareas_otro") or "").strip():
                errors["enf_tareas_otro"] = "Describe las otras responsabilidades."
            if len(str(details.get("condicion_otro") or "")) > 250:
                errors["condicion_otro"] = "La descripción no puede superar 250 caracteres."
            if len(str(details.get("enf_tareas_otro") or "")) > 250:
                errors["enf_tareas_otro"] = "La descripción no puede superar 250 caracteres."
            professional_by_person = next((
                index for index, profile in enumerate(person_profiles, start=1)
                if set(profile.get("responsabilidades", [])).intersection(ENFERMERA_PROFESSIONAL_RESPONSIBILITIES)
            ), None)
            professional_task_selected = bool(set(tasks).intersection(ENFERMERA_PROFESSIONAL_RESPONSIBILITIES)) or professional_by_person is not None
            if professional_task_selected and details.get("tipo_asistencia") != "enfermeria":
                errors["tipo_asistencia"] = (
                    f"Las responsabilidades clínicas de la persona {professional_by_person} requieren seleccionar cuidados de enfermería."
                    if professional_by_person else "Las tareas clínicas requieren seleccionar cuidados de enfermería."
                )
            experience = details.get("experiencia_requerida")
            experience_codes = {value for value, _label in ENFERMERA_EXPERIENCE_CHOICES}
            if experience and experience not in experience_codes:
                # Older Admin records can still be reopened, but new public
                # requests must use one of the explicit current choices.
                if strict_public:
                    errors["experiencia_requerida"] = "Selecciona un tipo de experiencia válido para ENFERMERA."
            if (professional_task_selected or details.get("tipo_asistencia") == "enfermeria") and experience != "Enfermera":
                errors["experiencia_requerida"] = "Selecciona Enfermera para funciones que requieren formación profesional."
    if code == "CONSERJERIA_RESIDENCIAL":
        kind = details.get("tipo_residencial")
        valid_types = {value for value, _ in CONSERJE_RESIDENTIAL_TYPES}
        if strict_public and kind not in valid_types:
            errors["tipo_residencial"] = "Selecciona el tipo de residencial."
        other_kind = str(details.get("tipo_residencial_otro") or "").strip()
        if strict_public and kind == "otro" and not other_kind:
            errors["tipo_residencial_otro"] = "Especifica el tipo de residencial."
        elif len(other_kind) > 120:
            errors["tipo_residencial_otro"] = "La descripción no puede superar 120 caracteres."
        for key in ("niveles", "apartamentos", "ascensores", "escaleras", "banos", "cantidad_edificios", "casas", "unidades"):
            value = details.get(key)
            if value in (None, ""):
                continue
            if type(value) is not int or value < 0:
                errors[key] = "Indica un número entero no negativo."
        allowed_keys: set[str] = set()
        if strict_public and kind in CONSERJE_RESIDENTIAL_PROFILES:
            fields = CONSERJE_RESIDENTIAL_PROFILES[kind]["fields"]
            allowed_keys = {CONSERJE_CHARACTERISTIC_FIELDS[field]["key"] for field in fields}
            for field in fields:
                spec = CONSERJE_CHARACTERISTIC_FIELDS[field]
                value = details.get(spec["key"])
                if kind != "otro" and value in (None, ""):
                    errors[spec["key"]] = "Completa esta característica del residencial."
                if spec["type"] == "number" and value not in (None, ""):
                    minimum = spec.get("min", 0)
                    if type(value) is not int or value < minimum or value > 10000:
                        errors[spec["key"]] = "Indica una cantidad entera válida para este residencial."
                elif spec["type"] == "yes_no" and value not in (None, "", "si", "no"):
                    errors[spec["key"]] = "Selecciona Sí o No."
            for key in ("niveles", "apartamentos", "ascensores", "estacionamiento", "cantidad_edificios", "parqueos_comunes", "areas_recreativas", "jardines_comunes", "casas", "calles_internas", "unidades", "tiene_ascensor"):
                if key not in allowed_keys and details.get(key) not in (None, ""):
                    errors[key] = "Esta característica no corresponde al tipo de residencial seleccionado."
            for key in ("escaleras", "banos", "parqueos"):
                if details.get(key) not in (None, ""):
                    errors[key] = "Este dato solo se conserva en solicitudes históricas."
        for key in ("estacionamiento", "parqueos_comunes", "areas_recreativas", "jardines_comunes", "calles_internas", "tiene_ascensor"):
            value = details.get(key)
            if value not in (None, "", "si", "no"):
                errors[key] = "Selecciona Sí o No."
        installations = details.get("instalaciones") if isinstance(details.get("instalaciones"), list) else []
        valid_installations = {value for value, _ in CONSERJE_INSTALLATION_CHOICES}
        allowed_installations = set(CONSERJE_INSTALLATIONS_BY_TYPE.get(kind, ()))
        if strict_public and (not installations or any(value not in valid_installations or value not in allowed_installations for value in installations)):
            errors["instalaciones"] = "Selecciona las instalaciones que tendrá a su cargo."
        if strict_public:
            parking_key = "parqueos_comunes" if "parqueos_comunes" in allowed_keys else "estacionamiento"
            parking_exists = details.get(parking_key) == "si" if parking_key in allowed_keys else True
            elevator_exists = (
                type(details.get("ascensores")) is int and details["ascensores"] > 0 if "ascensores" in allowed_keys
                else details.get("tiene_ascensor") == "si" if "tiene_ascensor" in allowed_keys
                else True
            )
            recreation_exists = details.get("areas_recreativas") == "si" if "areas_recreativas" in allowed_keys else True
            gardens_exist = details.get("jardines_comunes") == "si" if "jardines_comunes" in allowed_keys else "jardines" in installations
            streets_exist = details.get("calles_internas") == "si" if "calles_internas" in allowed_keys else True
            incompatible = {
                "ascensores": not elevator_exists, "parqueos": not parking_exists,
                "jardines": details.get("jardines_comunes") == "no",
                "gimnasio_recreativas": not recreation_exists, "salon_actividades": not recreation_exists,
                "piscina_alrededores": not recreation_exists, "parques_infantiles": not recreation_exists,
                "calles_internas": not streets_exist, "aceras": not streets_exist,
                "gazebo": details.get("areas_recreativas") == "no",
            }
            if any(code in installations and incompatible.get(code, False) for code in installations):
                errors["instalaciones"] = "Hay instalaciones que contradicen las características indicadas."
        if "otras" in installations and not str(details.get("otras_instalaciones") or "").strip():
            errors["otras_instalaciones"] = "Especifica las otras instalaciones."
        elif len(str(details.get("otras_instalaciones") or "")) > 200:
            errors["otras_instalaciones"] = "La descripción no puede superar 200 caracteres."
        responsibilities = details.get("responsabilidades") if isinstance(details.get("responsabilidades"), list) else []
        valid_responsibilities = {value for value, _ in CONSERJE_RESPONSIBILITY_CHOICES}
        allowed_responsibilities = set(CONSERJE_RESPONSIBILITIES_BY_TYPE.get(kind, ()))
        if strict_public and (not responsibilities or any(value not in valid_responsibilities or value not in allowed_responsibilities for value in responsibilities)):
            errors["responsabilidades"] = "Selecciona las responsabilidades que tendrá."
        garden_work_allowed = details.get("jardines_comunes") == "si" or "jardines" in installations
        if strict_public and "riego_jardines" in responsibilities and not garden_work_allowed:
            errors["responsabilidades"] = "El riego solo aplica cuando hay jardines o áreas verdes."
        if "otras" in responsibilities and not str(details.get("responsabilidades_otro") or "").strip():
            errors["responsabilidades_otro"] = "Especifica las otras responsabilidades."
        elif len(str(details.get("responsabilidades_otro") or "")) > 250:
            errors["responsabilidades_otro"] = "La descripción no puede superar 250 caracteres."
        support = details.get("trabaja_con_conserjes")
        count = details.get("cantidad_conserjes")
        if strict_public and support not in {"si", "no"}:
            errors["trabaja_con_conserjes"] = "Indica si trabajará con otros conserjes."
        elif support not in (None, "", "si", "no"):
            errors["trabaja_con_conserjes"] = "Selecciona Sí o No."
        if support == "si" and (type(count) is not int or count < 1):
            errors["cantidad_conserjes"] = "Indica un número entero positivo de conserjes, sin contar a la persona contratada."
        elif support == "no" and count not in (None, ""):
            errors["cantidad_conserjes"] = "No indiques una cantidad si no trabajará con otros conserjes."
        elif count not in (None, "") and (type(count) is not int or count < 1):
            errors["cantidad_conserjes"] = "Indica un número entero positivo."
    if code == "CHOFER":
        purpose = details.get("finalidad")
        # Requests saved before the structured purpose field remain valid/editable.
        if not purpose and details.get("tipo_servicio"):
            purpose = str(details.get("tipo_servicio"))
        purpose_values = {value for value, _ in CHOFER_PURPOSE_CHOICES}
        if strict_public and purpose not in purpose_values:
            errors["finalidad"] = "Selecciona para qué necesitas el chofer."
        if purpose == "otro" and not str(details.get("finalidad_otro") or "").strip():
            errors["finalidad_otro"] = "Especifica para qué necesitas el chofer."
        elif len(str(details.get("finalidad_otro") or "")) > 120:
            errors["finalidad_otro"] = "La descripción no puede superar 120 caracteres."
        vehicle = details.get("tipo_vehiculo")
        legacy_vehicle_values = {"Carro": "carro", "Yipeta": "yipeta", "Pickup": "pickup", "Minibús": "minibus", "Camión ligero": "camion_ligero", "Otro": "otro"}
        if vehicle in legacy_vehicle_values:
            vehicle = legacy_vehicle_values[vehicle]
        if vehicle not in {value for value, _ in CHOFER_VEHICLE_CHOICES} and vehicle not in {"camion_ligero"}:
            errors["tipo_vehiculo"] = "Selecciona un tipo de vehículo válido."
        other_vehicle = str(details.get("tipo_vehiculo_otro") or "").strip()
        if vehicle == "otro" and not other_vehicle:
            errors["tipo_vehiculo_otro"] = "Especifica el tipo de vehículo."
        elif len(other_vehicle) > 100:
            errors["tipo_vehiculo_otro"] = "La descripción no puede superar 100 caracteres."
        license_category = details.get("categoria_licencia_requerida")
        license_codes = {value for value, _ in CHOFER_LICENSE_CHOICES}
        if strict_public and license_category not in license_codes:
            errors["categoria_licencia_requerida"] = "Selecciona la categoría de licencia requerida."
        elif license_category not in (None, "", *license_codes):
            errors["categoria_licencia_requerida"] = "Selecciona una categoría de licencia válida."
        routes = details.get("rutas")
        route_codes = {value for value, _ in CHOFER_ROUTE_CHOICES}
        if isinstance(routes, list) and any(value not in route_codes for value in routes):
            errors["rutas"] = "Selecciona rutas válidas."
        if isinstance(routes, list) and "otro" in routes and not str(details.get("rutas_otro") or "").strip():
            errors["rutas_otro"] = "Especifica los desplazamientos."
        # Solo se valida incompatibilidad inequívoca; pickup/camión/otro dependen del peso y configuración.
        incompatible = {"carro": {"categoria_01"}, "yipeta": {"categoria_01"},
                        "pickup": {"categoria_01"}, "minibus": {"categoria_01", "categoria_02", "categoria_05"},
                        "camion_ligero": {"categoria_01", "categoria_05"}}
        if license_category in incompatible.get(vehicle, set()):
            errors["categoria_licencia_requerida"] = "La categoría seleccionada no corresponde a este tipo de vehículo; revisa la clasificación oficial del INTRANT."
    if code == "LIMPIEZA_ESTABLECIMIENTOS":
        if any(k in details for k in ("nombre_establecimiento", "cantidad_empleados")):
            errors["__private_fields__"] = "No se admiten identificadores del establecimiento ni cantidad de empleados."
        kind = details.get("tipo_establecimiento")
        valid_types = {value for value, _ in OFFICE_ESTABLISHMENT_CHOICES}
        if strict_public and kind not in valid_types:
            errors["tipo_establecimiento"] = "Selecciona el tipo de establecimiento."
        other_type = str(details.get("tipo_establecimiento_otro") or "").strip()
        if strict_public and kind == "otro" and not other_type:
            errors["tipo_establecimiento_otro"] = "Especifica el tipo de establecimiento."
        elif len(other_type) > 120:
            errors["tipo_establecimiento_otro"] = "La descripción no puede superar 120 caracteres."
        if strict_public and kind == "otro" and not str(details.get("espacios_otro") or "").strip():
            errors["espacios_otro"] = "Describe brevemente los espacios que necesitan limpieza."
        areas = details.get("areas") if isinstance(details.get("areas"), list) else []
        funcs = details.get("funciones") if isinstance(details.get("funciones"), list) else []
        valid_areas = {value for value, _ in OFFICE_AREA_CHOICES.get(kind, ())}
        valid_tasks = {value for value, _ in OFFICE_TASK_CHOICES}
        if strict_public and (not areas or any(value not in valid_areas for value in areas)):
            errors["areas"] = "Selecciona las áreas que necesita limpiar."
        beverage_tasks = {"cafe", "visitantes"}
        if strict_public and kind not in {"oficina", "salud", "otro"} and any(value in beverage_tasks for value in funcs):
            errors["funciones"] = "Las funciones de bebidas no corresponden a este tipo de establecimiento."
        characteristics = details.get("caracteristicas") if isinstance(details.get("caracteristicas"), dict) else {}
        allowed_counts = {"oficina": {"despachos", "reuniones", "banos"}, "tienda": {"ventas", "probadores", "banos"}, "educativo": {"aulas", "administracion", "banos"}, "salud": {"consultorios", "espera", "banos"}, "restaurante": {"comedores", "cocinas", "banos"}, "edificio": {"niveles", "areas_comunes", "banos"}}
        if strict_public and any(key not in allowed_counts.get(kind, set()) for key in characteristics):
            errors["caracteristicas"] = "Las cantidades deben corresponder al tipo de establecimiento."
        for key, count in characteristics.items():
            try:
                if type(count) is not int or count < 0 or count > 500:
                    errors["caracteristicas"] = "Las cantidades deben ser números entre 0 y 500."
            except (TypeError, ValueError):
                errors["caracteristicas"] = "Las cantidades deben ser números entre 0 y 500."
        if strict_public and (not funcs or any(value not in valid_tasks for value in funcs)):
            errors["funciones"] = "Selecciona las funciones que realizará el personal."
        if strict_public and "otras" in areas and not str(details.get("areas_otro") or "").strip(): errors["areas_otro"] = "Especifica las otras áreas."
        if strict_public and "otras" in funcs and not str(details.get("funciones_otro") or "").strip(): errors["funciones_otro"] = "Especifica las otras funciones."
        if details.get("caracteristicas") is not None and not isinstance(details.get("caracteristicas"), dict): errors["caracteristicas"] = "Revisa las cantidades del establecimiento."
        support = details.get("trabaja_con_companeros")
        coworkers = details.get("cantidad_companeros_limpieza")
        if strict_public and support not in {"si", "no"}:
            errors["trabaja_con_companeros"] = "Indica si trabajará con otros empleados de limpieza."
        if support == "si":
            if type(coworkers) is not int or coworkers < 1:
                errors["cantidad_companeros_limpieza"] = "Indica un número entero positivo de compañeros de limpieza."
        elif support == "no" and coworkers not in (None, ""):
            errors["cantidad_companeros_limpieza"] = "No indiques una cantidad si no trabajará con otros empleados."
        if details.get("tamano") not in (None, "", "Pequeño", "Mediano", "Grande"): errors["tamano"] = "Selecciona un tamaño válido."
        if details.get("espacios_otro") and len(str(details["espacios_otro"])) > 200: errors["espacios_otro"] = "La descripción no puede superar 200 caracteres."
    return errors


def public_details_from_form(code: str | None, form: Any) -> dict[str, Any]:
    """Normalize the public form's service-specific controls to stored contracts."""
    code = canonical_service_code(code)
    get = form.get
    getlist = getattr(form, "getlist", None)
    if getlist is None:
        getlist = lambda key: ([get(key)] if get(key) not in (None, "") else [])
    if code == "NINERA":
        result = {"cantidad_ninos": get("svc_ninera_count"), "edades_ninos": get("svc_ninera_ages"),
                  "tareas": form.getlist("svc_ninera_responsibility"),
                  "ninera_tareas_otro": get("svc_ninera_other"),
                  "responsabilidad_integral": get("svc_ninera_integral") == "1",
                  "ninera_responsable_principal": get("svc_ninera_primary"),
                  "necesidad_especial_respuesta": get("svc_ninera_special_need"),
                  # This is a public care-task description, never a medical note.
                  # Public submissions cannot set the Admin review hash.
                  "necesidad_especial_publicable": get("svc_ninera_special_care")}
        result["tareas"] = [value for value in result["tareas"] if value in {code for code, _ in NINERA_RESPONSIBILITY_CHOICES}]
    elif code == "ENFERMERA":
        result = {"cantidad_personas": get("svc_care_count"), "edad_persona": get("svc_care_age_single"),
                  "edades_personas": get("svc_care_ages_multiple"), "movilidad": get("svc_care_mobility"),
                  "ayuda_higiene": get("svc_care_hygiene"), "ayuda_alimentacion": get("svc_care_feeding"),
                  "tipo_asistencia": get("svc_care_assistance"), "condiciones": form.getlist("svc_care_condition"),
                  "condicion_otro": get("svc_care_condition_other"),
                  "responsabilidad_principal": get("svc_care_primary"),
                  "requiere_preparacion_profesional": get("svc_care_assistance") == "enfermeria",
                  "experiencia_requerida": get("experiencia"),
                  "personas_cuidado": parse_enfermera_personas(get("svc_care_profiles")),
                  "personas_cuidado_historicas": parse_enfermera_perfiles_historicos(get("svc_care_profile_history")),
                  }
    elif code == "CHOFER":
        result = {"finalidad": get("svc_driver_purpose") or None, "finalidad_otro": get("svc_driver_purpose_other"), "tipo_vehiculo": get("svc_driver_vehicle"),
                  "tipo_vehiculo_otro": get("svc_driver_vehicle_other"),
                  "categoria_licencia_requerida": get("svc_driver_license_category"),
                  "rutas": form.getlist("svc_driver_route"), "rutas_otro": get("svc_driver_routes_other"),
                  "responsabilidades": get("svc_driver_tasks")}
    elif code == "LIMPIEZA_ESTABLECIMIENTOS":
        kind = get("svc_office_type")
        result = {"tipo_establecimiento": kind, "tipo_establecimiento_otro": get("svc_office_type_other") if kind == "otro" else None,
                  "tamano": get("svc_office_size"), "caracteristicas": {}, "espacios_otro": get("svc_office_spaces_other") if kind == "otro" else None,
                  "areas": getlist("svc_office_area"), "areas_otro": get("svc_office_areas_other"),
                  "funciones": getlist("svc_office_task"), "funciones_otro": get("svc_office_tasks_other"),
                  "trabaja_con_companeros": get("svc_office_support") or None}
        if result["trabaja_con_companeros"] == "si":
            raw_coworkers = get("svc_office_coworker_count")
            if raw_coworkers not in (None, ""):
                try:
                    result["cantidad_companeros_limpieza"] = int(raw_coworkers)
                except (TypeError, ValueError):
                    result["cantidad_companeros_limpieza"] = str(raw_coworkers).strip()
        for name in ("despachos", "reuniones", "banos", "ventas", "probadores", "aulas", "administracion", "consultorios", "espera", "comedores", "cocinas", "niveles", "areas_comunes"):
            raw = get("svc_office_" + name)
            if raw not in (None, ""):
                try: result["caracteristicas"][name] = int(raw)
                except (TypeError, ValueError): pass
        if "otras" not in result["areas"]: result["areas_otro"] = ""
        if "otras" not in result["funciones"]: result["funciones_otro"] = ""
    elif code == "CONSERJERIA_RESIDENCIAL":
        def nonnegative_integer(name):
            raw = get(name)
            if raw in (None, ""):
                return None
            return int(raw) if isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()) else raw
        result = {
            "tipo_residencial": get("svc_conserje_type"),
            "tipo_residencial_otro": get("svc_conserje_type_other"),
            "instalaciones": getlist("svc_conserje_installation"),
            "otras_instalaciones": get("svc_conserje_installation_other"),
            "responsabilidades": getlist("svc_conserje_responsibility"),
            "responsabilidades_otro": get("svc_conserje_responsibility_other"),
            "trabaja_con_conserjes": get("svc_conserje_support"),
            "cantidad_conserjes": nonnegative_integer("svc_conserje_coworker_count"),
        }
        for spec in CONSERJE_CHARACTERISTIC_FIELDS.values():
            raw = get(spec["name"])
            result[spec["key"]] = nonnegative_integer(spec["name"]) if spec["type"] == "number" else raw
        # Historical controls are accepted only by non-strict readers/admin edits.
        for key, name in (("escaleras", "svc_conserje_stairs"), ("banos", "svc_conserje_bathrooms"), ("parqueos", "svc_conserje_legacy_parking")):
            result[key] = nonnegative_integer(name)
    else:
        # The household service stores its general answers in Solicitud
        # columns, but common per-request JSON metadata (such as address)
        # still belongs in this service's details payload.
        result = {}
    # Every request owns its location. Keep it at the common JSONB level so
    # switching service type cannot overwrite another request's address.
    precise_address = str(get("direccion_precisa") or "").strip()
    if precise_address:
        result["direccion_precisa"] = precise_address
    # Never accept business names or headcount even if a client submits forged fields.
    return {key: value.strip() if isinstance(value, str) else value
            for key, value in result.items() if value not in (None, "", [])}


def admin_details_from_form(code: str | None, form: Any) -> dict[str, Any]:
    """Map Admin form controls to the same service detail contract as public."""
    code = canonical_service_code(code)
    def value(name: str, default: Any = None) -> Any:
        field = getattr(form, name, None)
        return getattr(field, "data", default) if field is not None else default

    if code == "NINERA":
        result = {"cantidad_ninos": value("ninera_cant_ninos"), "edades_ninos": value("ninera_edades"),
                  "tareas": value("ninera_tareas", []), "ninera_tareas_otro": value("ninera_tareas_otro"),
                  "condicion_especial": value("ninera_condicion_especial"),
                  "necesidad_especial_publicable": value("ninera_necesidad_especial_publicable"),
                  "necesidad_especial_respuesta": value("ninera_necesidad_especial_respuesta"),
                  "ninera_responsable_principal": value("ninera_responsable_principal"),
                  "responsabilidad_integral": bool(value("ninera_responsabilidad_integral", False))}
    elif code == "ENFERMERA":
        result = {"a_quien_cuida": value("enf_a_quien_cuida"), "cantidad_personas": value("enf_cantidad_personas"),
                  "edad_persona": value("enf_edad_persona"), "edades_personas": value("enf_edades_personas"),
                  "movilidad": value("enf_movilidad"), "ayuda_higiene": value("enf_ayuda_higiene"),
                  "ayuda_alimentacion": value("enf_ayuda_alimentacion"), "tipo_asistencia": value("enf_tipo_asistencia"),
                  "condiciones": value("enf_condiciones", []), "condicion_otro": value("enf_condicion_otro"),
                  "tareas": value("enf_tareas", []), "enf_tareas_otro": value("enf_tareas_otro"),
                  "responsabilidad_principal": value("enf_responsabilidad_principal"),
                  "requiere_preparacion_profesional": bool(value("enf_requiere_preparacion_profesional", False)),
                  "experiencia_requerida": value("experiencia"), "formacion_requerida": value("enf_formacion_requerida"),
                  "personas_cuidado": parse_enfermera_personas(value("enf_personas_cuidado")),
                  "personas_cuidado_historicas": parse_enfermera_perfiles_historicos(value("enf_personas_cuidado_historicas")),
                  "personas_cuidado_necesidades_historicas": parse_enfermera_necesidades_historicas(value("enf_personas_cuidado_necesidades_historicas")),
                  "experiencia_requerida": value("experiencia")}
    elif code == "CHOFER":
        result = {"finalidad": value("chofer_tipo_servicio"), "finalidad_otro": value("chofer_tipo_servicio_otro"), "responsabilidades": value("chofer_responsabilidades"), "tipo_vehiculo": value("chofer_tipo_vehiculo"),
                  "tipo_vehiculo_otro": value("chofer_tipo_vehiculo_otro"), "vehiculo": value("chofer_vehiculo"),
                  "rutas": value("chofer_rutas", []), "rutas_otro": value("chofer_rutas_otro"),
                  "categoria_licencia_requerida": value("chofer_categoria_licencia")}
    elif code == "LIMPIEZA_ESTABLECIMIENTOS":
        raw_areas = value("office_areas", [])
        result = {"tipo_establecimiento": value("office_tipo_establecimiento"), "tipo_establecimiento_otro": value("office_tipo_establecimiento_otro"),
                  "tamano": value("office_tamano"), "caracteristicas": {}, "espacios_otro": value("office_espacios_otro"),
                  "areas": raw_areas if isinstance(raw_areas, list) else [], "areas_legacy": value("office_areas_legacy") or (raw_areas if isinstance(raw_areas, str) else None), "areas_otro": value("office_areas_otro"),
                  "funciones": value("office_funciones", []), "funciones_otro": value("office_funciones_otro")}
        result["trabaja_con_companeros"] = value("office_trabaja_con_companeros") or None
        if result["trabaja_con_companeros"] == "si":
            result["cantidad_companeros_limpieza"] = value("office_cantidad_companeros_limpieza")
        if isinstance(value("office_caracteristicas"), str):
            try: result["caracteristicas"] = json.loads(value("office_caracteristicas"))
            except (TypeError, ValueError): result["caracteristicas"] = {}
        elif isinstance(value("office_caracteristicas"), dict): result["caracteristicas"] = value("office_caracteristicas")
        for characteristic in ("despachos", "reuniones", "ventas", "probadores", "aulas", "administracion", "consultorios", "espera", "comedores", "cocinas", "niveles", "banos"):
            field_value = value("office_" + characteristic)
            if field_value is not None: result["caracteristicas"][characteristic] = field_value
    elif code == "CONSERJERIA_RESIDENCIAL":
        result = {"tipo_residencial": value("conserje_tipo_residencial"), "tipo_residencial_otro": value("conserje_tipo_residencial_otro"),
                  "niveles": value("conserje_niveles"), "apartamentos": value("conserje_apartamentos"), "ascensores": value("conserje_ascensores"),
                  "cantidad_edificios": value("conserje_cantidad_edificios"), "casas": value("conserje_cantidad_casas"), "unidades": value("conserje_unidades"),
                  "tiene_ascensor": value("conserje_tiene_ascensor"), "parqueos_comunes": value("conserje_parqueos_comunes"),
                  "areas_recreativas": value("conserje_areas_recreativas"), "jardines_comunes": value("conserje_jardines_comunes"), "calles_internas": value("conserje_calles_internas"),
                  "escaleras": value("conserje_escaleras"), "banos": value("conserje_banos"), "estacionamiento": value("conserje_estacionamiento"),
                  "instalaciones": value("conserje_instalaciones", []), "otras_instalaciones": value("conserje_otras_instalaciones"),
                  "responsabilidades": value("conserje_responsabilidades", []), "responsabilidades_otro": value("conserje_responsabilidades_otro"),
                  "trabaja_con_conserjes": value("conserje_trabaja_con_conserjes") or None, "cantidad_conserjes": value("conserje_cantidad_conserjes"),
                  # These legacy values stay in the active JSON block when an Admin edits an old request.
                  "tipo_instalacion": value("conserje_tipo_instalacion"), "areas_comunes": value("conserje_areas_comunes"),
                  "areas_exteriores": value("conserje_areas_exteriores"), "manejo_basura": value("conserje_manejo_basura"),
                  "mantenimiento_basico": value("conserje_mantenimiento"), "parqueos": value("conserje_parqueos")}
    else:
        return {}
    return {key: item.strip() if isinstance(item, str) else item
            for key, item in result.items()
            if item not in (None, "", []) or (code == "CHOFER" and key == "rutas" and item == [])}


def merge_service_details(previous: Any, updates: Any, code: str | None) -> dict[str, Any] | None:
    """Keep flat active keys compatible and retain inactive service blocks by type."""
    merged = dict(previous) if isinstance(previous, dict) else {}
    old_code = str(merged.get("tipo") or "").strip()
    requested_code = str(code or "").strip()
    old_canonical = canonical_service_code(old_code)
    target_code = canonical_service_code(requested_code)
    # The old CONSERJE code predates the residential schema. Preserve its
    # exact payload on an unrelated edit; interpreting it as residential could
    # silently change the meaning of commercial facilities and duties.
    if requested_code == "CONSERJE" and old_code in {"", "CONSERJE"}:
        return merged or None
    by_type = dict(merged.get("_by_type") or {})
    if old_code and target_code and old_canonical != target_code:
        old_keys = set(DETAIL_CONTRACTS.get(old_canonical, {}).get("keys", ()))
        old_block = {key: merged[key] for key in old_keys if key in merged}
        if old_block:
            prior_block = dict(by_type.get(old_code) or {})
            prior_block.update(old_block)
            by_type[old_code] = prior_block
            for key in old_keys:
                merged.pop(key, None)
        target_block = {}
        for variant in service_code_variants(target_code):
            if isinstance(by_type.get(variant), dict):
                target_block.update(by_type[variant])
        merged.update(target_block)
    if target_code == "ENFERMERA" and isinstance(updates, dict):
        # Legacy per-person needs are retained as non-public historical data.
        # They are never reintroduced into the new form or copied publication.
        old_people = parse_enfermera_personas(merged.get("personas_cuidado"))
        new_people = parse_enfermera_personas(updates.get("personas_cuidado"))
        archive = parse_enfermera_perfiles_historicos(merged.get("personas_cuidado_historicas"))
        archive.extend(parse_enfermera_perfiles_historicos(updates.get("personas_cuidado_historicas")))
        needs_archive = parse_enfermera_necesidades_historicas(merged.get("personas_cuidado_necesidades_historicas"))
        needs_archive.extend(parse_enfermera_necesidades_historicas(updates.get("personas_cuidado_necesidades_historicas")))
        new_count = len(new_people)
        for index, person in enumerate(old_people):
            if index >= new_count:
                archive.append({"indice": index, "perfil": person})
            elif index < len(new_people) and person.get("necesidades") and not new_people[index].get("necesidades"):
                needs_archive.append({"indice": index, "necesidades": person["necesidades"]})
        archive = parse_enfermera_perfiles_historicos(archive)
        updates = dict(updates)
        if archive:
            updates["personas_cuidado_historicas"] = archive
        if needs_archive:
            updates["personas_cuidado_necesidades_historicas"] = parse_enfermera_necesidades_historicas(needs_archive)
    if target_code == "CHOFER" and isinstance(merged.get("rutas"), str) and isinstance(updates, dict) and isinstance(updates.get("rutas"), list):
        # Keep the old free-text value intact when an Admin form first saves the structured schema.
        merged["rutas_legacy"] = merged.get("rutas")
    if isinstance(updates, dict):
        merged.update({key: value for key, value in updates.items() if key != "_by_type"})
    if target_code:
        # An unrelated edit must not silently rewrite the historical marker.
        merged["tipo"] = old_code if old_code and old_canonical == target_code else target_code
    if by_type:
        merged["_by_type"] = by_type
    return merged or None


def service_details_for_type(details: Any, code: str | None) -> dict[str, Any]:
    """Read the active flat block or an archived block for an Admin type switch."""
    if not isinstance(details, dict):
        return {}
    selected = canonical_service_code(code)
    history = details.get("_by_type") if isinstance(details.get("_by_type"), dict) else {}
    result = {}
    for variant in service_code_variants(selected):
        if isinstance(history.get(variant), dict):
            result.update(history[variant])
    stored_type = str(details.get("tipo") or "").strip()
    same_stored_service = not stored_type or canonical_service_code(stored_type) == selected
    if same_stored_service:
        result.update({key: value for key, value in details.items() if key != "_by_type"})
    result["tipo"] = stored_type if stored_type and same_stored_service else selected
    return result
