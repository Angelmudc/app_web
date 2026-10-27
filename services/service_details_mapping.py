"""Explicit form-to-service-details mapping shared with the Admin editor.

The builder is a pure transformation: it receives the validated form and
explicit schedule controls, returns the same details payload as the legacy
Admin mapper, and does not mutate a request, ORM row, or database session.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from services.solicitud_edit_mapping import clean_list as _clean_list
from utils.experiencia_solicitud import experiencia_value_from_form
from utils.horario_mode import build_horario_from_form
from utils.solicitud_composition import normalized_funciones
from services.service_catalog import merge_service_details
from services.service_catalog import canonical_service_code


def build_service_details_from_form(
    form, *, schedule_inputs: Mapping[str, Any] | None = None
) -> dict | None:
    """Build the established JSONB payload from explicit validated inputs."""
    schedule_inputs = dict(schedule_inputs or {})
    raw_ts = getattr(form, 'tipo_servicio', None).data if hasattr(form, 'tipo_servicio') else None
    if not raw_ts:
        return None
    ts = canonical_service_code(raw_ts)

    detalles: dict = {
        "tipo": raw_ts  # preserve historical marker on an unrelated edit
    }
    precise_address = str(getattr(getattr(form, "direccion_precisa", None), "data", "") or "").strip()
    if precise_address:
        detalles["direccion_precisa"] = precise_address
    _horario_text, horario_payload, _horario_errors = build_horario_from_form(
        modalidad_group=schedule_inputs.get("modalidad_grupo"),
        modalidad_trabajo=(getattr(form, "modalidad_trabajo", None).data if hasattr(form, "modalidad_trabajo") else ""),
        dias_trabajo=schedule_inputs.get("horario_dias_trabajo"),
        hora_entrada=schedule_inputs.get("horario_hora_entrada"),
        hora_salida=schedule_inputs.get("horario_hora_salida"),
        dormida_entrada=schedule_inputs.get("horario_dormida_entrada"),
        dormida_salida=schedule_inputs.get("horario_dormida_salida"),
        horario_legacy=(getattr(form, "horario", None).data if hasattr(form, "horario") else ""),
    )
    detalles.update(horario_payload)

    # ─────────────────────────────
    # NIÑERA
    # ─────────────────────────────
    if ts == 'NINERA':
        cant_ninos = form.ninera_cant_ninos.data if hasattr(form, 'ninera_cant_ninos') else None
        edades = (form.ninera_edades.data or '').strip() if hasattr(form, 'ninera_edades') else ''
        tareas = _clean_list(form.ninera_tareas.data) if hasattr(form, 'ninera_tareas') else []
        tareas_otro = (form.ninera_tareas_otro.data or '').strip() if hasattr(form, 'ninera_tareas_otro') else ''
        condicion = (form.ninera_condicion_especial.data or '').strip() if hasattr(form, 'ninera_condicion_especial') else ''
        special_care = (form.ninera_necesidad_especial_publicable.data or '').strip() if hasattr(form, 'ninera_necesidad_especial_publicable') else ''
        special_need = form.ninera_necesidad_especial_respuesta.data or None
        usa_otro = ('otro' in tareas)
        tareas = [t for t in tareas if t != 'otro']
        if not usa_otro:
            tareas_otro = ''

        detalles.update({
            "cantidad_ninos": cant_ninos,
            "edades_ninos": edades or None,
            "tareas": _clean_list(tareas or []),
            # Clave específica para evitar cruces con ENFERMERA.
            "ninera_tareas_otro": tareas_otro or None,
            "necesidad_especial_publicable": special_care or None,
            "condicion_especial": condicion or None,
            "necesidad_especial_respuesta": special_need,
            "ninera_responsable_principal": form.ninera_responsable_principal.data or None,
            "responsabilidad_integral": bool(form.ninera_responsabilidad_integral.data),
        })

    # ─────────────────────────────
    # ENFERMERA / CUIDADORA
    # ─────────────────────────────
    elif ts == 'ENFERMERA':
        from services.service_catalog import parse_enfermera_personas, parse_enfermera_perfiles_historicos
        a_quien = (form.enf_a_quien_cuida.data or '').strip() if hasattr(form, 'enf_a_quien_cuida') else ''
        condicion = (form.enf_condicion_principal.data or '').strip() if hasattr(form, 'enf_condicion_principal') else ''
        condition_other = (form.enf_condicion_otro.data or '').strip() if hasattr(form, 'enf_condicion_otro') else ''
        conditions = _clean_list(form.enf_condiciones.data) if hasattr(form, 'enf_condiciones') else []
        tasks_other = (form.enf_tareas_otro.data or '').strip() if hasattr(form, 'enf_tareas_otro') else ''
        movilidad = form.enf_movilidad.data if hasattr(form, 'enf_movilidad') else ''
        assistance_type = form.enf_tipo_asistencia.data if hasattr(form, 'enf_tipo_asistencia') else ''
        requires_professional = assistance_type == 'enfermeria'
        formacion = (form.enf_formacion_requerida.data or '').strip() if hasattr(form, 'enf_formacion_requerida') else ''
        tareas = _clean_list(form.enf_tareas.data) if hasattr(form, 'enf_tareas') else []
        detalles.update({
            "a_quien_cuida": a_quien or None,
            "condicion_principal": condicion or None,
            "cantidad_personas": form.enf_cantidad_personas.data if hasattr(form, 'enf_cantidad_personas') else None,
            "personas_cuidado": parse_enfermera_personas(form.enf_personas_cuidado.data if hasattr(form, 'enf_personas_cuidado') else []),
            "personas_cuidado_historicas": parse_enfermera_perfiles_historicos(form.enf_personas_cuidado_historicas.data if hasattr(form, 'enf_personas_cuidado_historicas') else []),
            "edad_persona": form.enf_edad_persona.data if hasattr(form, 'enf_edad_persona') else None,
            "edades_personas": (form.enf_edades_personas.data or '').strip() if hasattr(form, 'enf_edades_personas') else None,
            "movilidad": movilidad or None,
            "ayuda_higiene": form.enf_ayuda_higiene.data or None,
            "ayuda_alimentacion": form.enf_ayuda_alimentacion.data or None,
            "tipo_asistencia": assistance_type or None,
            "condiciones": conditions or None,
            "condicion_otro": condition_other or None,
            "tareas": _clean_list(tareas or []),
            "responsabilidad_principal": form.enf_responsabilidad_principal.data or None,
            "requiere_preparacion_profesional": requires_professional,
            "formacion_requerida": formacion or None,
            "experiencia_requerida": experiencia_value_from_form(form) or None,
            "enf_tareas_otro": tasks_other or None,
        })

    # ─────────────────────────────
    # CHOFER
    # ─────────────────────────────
    elif ts == 'CHOFER':
        finalidad_chofer = (form.chofer_tipo_servicio.data or '').strip() if hasattr(form, 'chofer_tipo_servicio') else ''
        finalidad_otra = (form.chofer_tipo_servicio_otro.data or '').strip() if hasattr(form, 'chofer_tipo_servicio_otro') else ''
        responsabilidades = (form.chofer_responsabilidades.data or '').strip() if hasattr(form, 'chofer_responsabilidades') else ''
        vehiculo = form.chofer_vehiculo.data if hasattr(form, 'chofer_vehiculo') else ''
        tipo_vehiculo = form.chofer_tipo_vehiculo.data if hasattr(form, 'chofer_tipo_vehiculo') else ''
        tipo_vehiculo_otro = (form.chofer_tipo_vehiculo_otro.data or '').strip() if hasattr(form, 'chofer_tipo_vehiculo_otro') else ''
        rutas = list(form.chofer_rutas.data or []) if hasattr(form, 'chofer_rutas') else []
        rutas_otro = (form.chofer_rutas_otro.data or '').strip() if hasattr(form, 'chofer_rutas_otro') else ''
        categoria = form.chofer_categoria_licencia.data if hasattr(form, 'chofer_categoria_licencia') else ''
        if tipo_vehiculo != 'otro':
            tipo_vehiculo_otro = ''
        if 'otro' not in rutas:
            rutas_otro = ''
        detalles.update({
            "finalidad": finalidad_chofer or None,
            "finalidad_otro": finalidad_otra if finalidad_chofer == 'otro' else None,
            "responsabilidades": responsabilidades or None,
            "vehiculo": vehiculo or None,
            "tipo_vehiculo": tipo_vehiculo or None,
            "tipo_vehiculo_otro": tipo_vehiculo_otro or None,
            "rutas": rutas,
            "rutas_otro": rutas_otro or None,
            "categoria_licencia_requerida": categoria or None,
        })

    elif ts == 'LIMPIEZA_ESTABLECIMIENTOS':
        details = {
            "tipo_establecimiento": form.office_tipo_establecimiento.data,
            "tipo_establecimiento_otro": form.office_tipo_establecimiento_otro.data if hasattr(form, 'office_tipo_establecimiento_otro') else None,
            "tamano": form.office_tamano.data,
            "caracteristicas": {key: getattr(form, "office_" + key).data for key in ("despachos", "reuniones", "ventas", "probadores", "aulas", "administracion", "consultorios", "espera", "comedores", "cocinas", "niveles", "banos") if hasattr(form, "office_" + key) and getattr(form, "office_" + key).data is not None},
            "espacios_otro": form.office_espacios_otro.data if hasattr(form, 'office_espacios_otro') else None,
            "areas": _clean_list(form.office_areas.data) if hasattr(form, 'office_areas') and isinstance(form.office_areas.data, list) else [],
            "areas_legacy": form.office_areas_legacy.data if hasattr(form, 'office_areas_legacy') else None,
            "frecuencia": form.office_frecuencia.data if hasattr(form, 'office_frecuencia') else None,
            "horario_limpieza": form.office_horario.data if hasattr(form, 'office_horario') else None,
            "areas_otro": form.office_areas_otro.data if hasattr(form, 'office_areas_otro') else None,
            "funciones": _clean_list(form.office_funciones.data) if hasattr(form, 'office_funciones') else [],
            "funciones_otro": form.office_funciones_otro.data if hasattr(form, 'office_funciones_otro') else None,
        }
        detalles.update({k: v for k, v in details.items() if v not in (None, '', [], {})})

    elif ts == 'CONSERJERIA_RESIDENCIAL':
        details = {
            "tipo_residencial": form.conserje_tipo_residencial.data,
            "tipo_residencial_otro": form.conserje_tipo_residencial_otro.data,
            "tipo_instalacion": form.conserje_tipo_instalacion.data,
            "niveles": form.conserje_niveles.data,
            "apartamentos": form.conserje_apartamentos.data,
            "ascensores": form.conserje_ascensores.data,
            "cantidad_edificios": form.conserje_cantidad_edificios.data,
            "casas": form.conserje_cantidad_casas.data,
            "unidades": form.conserje_unidades.data,
            "tiene_ascensor": form.conserje_tiene_ascensor.data,
            "parqueos_comunes": form.conserje_parqueos_comunes.data,
            "areas_recreativas": form.conserje_areas_recreativas.data,
            "jardines_comunes": form.conserje_jardines_comunes.data,
            "calles_internas": form.conserje_calles_internas.data,
            "areas_comunes": form.conserje_areas_comunes.data,
            "banos": form.conserje_banos.data,
            "escaleras": form.conserje_escaleras.data,
            "parqueos": form.conserje_parqueos.data,
            "estacionamiento": form.conserje_estacionamiento.data,
            "areas_exteriores": form.conserje_areas_exteriores.data,
            "manejo_basura": form.conserje_manejo_basura.data,
            "mantenimiento_basico": form.conserje_mantenimiento.data,
            "instalaciones": form.conserje_instalaciones.data,
            "otras_instalaciones": form.conserje_otras_instalaciones.data,
            "responsabilidades": form.conserje_responsabilidades.data,
            "responsabilidades_otro": form.conserje_responsabilidades_otro.data,
            "trabaja_con_conserjes": form.conserje_trabaja_con_conserjes.data,
            "cantidad_conserjes": form.conserje_cantidad_conserjes.data,
        }
        detalles.update({k: v for k, v in details.items() if v not in (None, '', [], {})})

    # ─────────────────────────────
    # DOMÉSTICA DE LIMPIEZA
    # ─────────────────────────────
    elif ts == 'DOMESTICA_LIMPIEZA':
        # No metemos más cosas aquí porque ya usamos columnas normales (funciones, áreas, etc.)
        pass

    if hasattr(form, "ayuda_cuidado_ninos"):
        selected = normalized_funciones(getattr(getattr(form, "funciones", None), "data", None))
        ayuda = str(getattr(form.ayuda_cuidado_ninos, "data", "") or "").strip()
        if ayuda and "ninos" in selected:
            detalles["ayuda_cuidado_ninos"] = ayuda

    # Limpiar claves vacías
    clean = {
        k: v for k, v in detalles.items()
        if v not in (None, '', [], {})
    }
    return clean or None


def combine_service_details(
    previous: Any,
    updates: Any,
    service_type: str | None,
) -> dict[str, Any] | None:
    """Preserve existing childcare detail and delegate archival/merge rules.

    Inputs are copied because ``merge_service_details`` performs in-place
    compatibility updates for historical Enfermera profiles. No caller-owned
    details object is mutated by this coordination layer.
    """
    previous_copy = deepcopy(previous) if isinstance(previous, dict) else {}
    updates_copy = deepcopy(updates) if isinstance(updates, dict) else {}
    if previous_copy.get("ayuda_cuidado_ninos") and not updates_copy.get("ayuda_cuidado_ninos"):
        updates_copy["ayuda_cuidado_ninos"] = previous_copy["ayuda_cuidado_ninos"]
    return merge_service_details(previous_copy, updates_copy, service_type)
