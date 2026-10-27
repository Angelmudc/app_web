"""Equivalence and immutability tests for explicit service detail mapping."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from app import app as flask_app
from admin.forms import AdminSolicitudForm
import admin.routes as admin_routes
from services.service_details_mapping import build_service_details_from_form, combine_service_details
from services.service_catalog import SERVICE_CHOICES, merge_service_details


SCHEDULE = {
    "modalidad_grupo": "con_salida_diaria",
    "horario_dias_trabajo": "Lunes a viernes",
    "horario_hora_entrada": "8:00 AM",
    "horario_hora_salida": "5:00 PM",
    "horario_dormida_entrada": "",
    "horario_dormida_salida": "",
}

SERVICE_CASES = [
    pytest.param("DOMESTICA_LIMPIEZA", {"funciones": ["limpieza"], "ayuda_cuidado_ninos": ""}, id="domestica"),
    pytest.param("NINERA", {
        "ninera_cant_ninos": "2", "ninera_edades": "2 y 6 años",
        "ninera_tareas": ["supervision", "otro"], "ninera_tareas_otro": "Apoyar actividades",
        "ninera_responsabilidad_integral": "1", "ninera_responsable_principal": "no",
        "ninera_necesidad_especial_respuesta": "si", "ninera_condicion_especial": "Nota privada",
        "ninera_necesidad_especial_publicable": "Ayuda durante alimentación",
    }, id="ninera"),
    pytest.param("ENFERMERA", {
        "enf_a_quien_cuida": "Adulto mayor", "enf_condicion_principal": "Diabetes",
        "enf_cantidad_personas": "2",
        "enf_personas_cuidado": '[{"edad":78,"movilidad":"postrado","condiciones":["diabetes"],"necesidades":[]} ]',
        "enf_personas_cuidado_historicas": "[]", "enf_responsabilidad_por_persona": '{"aseo":2}',
        "enf_edad_persona": "78", "enf_edades_personas": "78 y 82",
        "enf_movilidad": "postrado", "enf_ayuda_higiene": "si", "enf_ayuda_alimentacion": "no",
        "enf_tipo_asistencia": "enfermeria", "enf_formacion_requerida": "Técnica acreditada",
        "enf_condiciones": ["diabetes", "otra"], "enf_condicion_otro": "Artritis",
        "enf_tareas": ["aseo", "otro", "administrar_inyecciones"],
        "enf_tareas_otro": "Apoyo prescrito",
        "enf_responsabilidad_principal": "si",
        "experiencia": "Enfermera",
    }, id="enfermera"),
    pytest.param("CHOFER", {
        "chofer_tipo_servicio": "traslado_empleados", "chofer_tipo_vehiculo": "carro",
        "chofer_vehiculo": "cliente", "chofer_tipo_vehiculo_otro": "se ignora",
        "chofer_rutas": ["dentro_ciudad", "larga_distancia"], "chofer_categoria_licencia": "categoria_02",
    }, id="chofer"),
    pytest.param("LIMPIEZA_ESTABLECIMIENTOS", {
        "office_tipo_establecimiento": "Oficina", "office_tamano": "Mediano", "office_niveles": "2",
        "office_banos": "3", "office_areas": "Recepción", "office_funciones": "Limpieza general",
        "office_frecuencia": "Diaria", "office_horario": "Fuera de horario de atención",
    }, id="limpieza_oficina"),
    pytest.param("CONSERJERIA_RESIDENCIAL", {
        "conserje_tipo_instalacion": "Residencial", "conserje_niveles": "3",
        "conserje_areas_comunes": "Lobby", "conserje_banos": "1", "conserje_escaleras": "1",
        "conserje_parqueos": "1", "conserje_areas_exteriores": "Jardín",
        "conserje_manejo_basura": "si", "conserje_mantenimiento": "si",
    }, id="conserje"),
]

OLD_SERVICE_DETAILS = {
    "DOMESTICA_LIMPIEZA": {},
    "NINERA": {
        "cantidad_ninos": 2, "edades_ninos": "4 y 7 años", "tareas": ["supervision"],
        "ninera_tareas_otro": "Preparar meriendas", "ninera_tareas_otro_revision_sha256": "sha-reviewed",
    },
    "ENFERMERA": {
        "a_quien_cuida": "Adulto mayor", "personas_cuidado_historicas": [{"indice": 0, "perfil": {"edad": 81}}],
    },
    "CHOFER": {"tipo_vehiculo": "carro", "licencia_requisitos": "Licencia vigente"},
    "LIMPIEZA_ESTABLECIMIENTOS": {"tipo_establecimiento": "Oficina", "tamano": "Mediano", "areas": "Recepción"},
    "CONSERJERIA_RESIDENCIAL": {"tipo_instalacion": "Residencial", "areas_comunes": "Lobby"},
}


@pytest.mark.parametrize(("service_code", "service_payload"), SERVICE_CASES)
def test_explicit_builder_matches_admin_adapter_for_each_service(service_code, service_payload):
    raw = MultiDict([("tipo_servicio", service_code), ("modalidad_trabajo", "Con salida diaria"),
                     ("horario", "Horario legado"), ("experiencia", service_payload.get("experiencia", "1-2 años"))])
    for key, value in SCHEDULE.items():
        raw.add(key, value)
    for key, value in service_payload.items():
        if isinstance(value, (list, tuple)):
            raw.setlist(key, list(value))
        else:
            raw.add(key, value)
    with flask_app.test_request_context("/admin/test-service-details", method="POST", data=raw.copy()):
        form = AdminSolicitudForm(formdata=raw, meta={"csrf": False})
        before = {name: deepcopy(field.data) for name, field in form._fields.items()}
        via_http_adapter = admin_routes._build_detalles_servicio_from_form(form)
        explicit = build_service_details_from_form(form, schedule_inputs=SCHEDULE)

    assert via_http_adapter == explicit
    assert explicit["tipo"] == service_code
    assert explicit["horario_tipo"] == "salida_diaria"
    assert explicit["dias_trabajo"] == "Lunes a viernes"
    assert {name: field.data for name, field in form._fields.items()} == before
    if service_code == "NINERA":
        assert explicit["tareas"] == ["supervision"]
        assert "ninera_tareas_otro_revision_sha256" not in explicit
        assert "necesidad_especial_revision_sha256" not in explicit
        assert explicit["condicion_especial"] == "Nota privada"
    elif service_code == "ENFERMERA":
        assert explicit["requiere_preparacion_profesional"] is True
        assert explicit["experiencia_requerida"] == "Enfermera"
        assert "responsabilidad_por_persona" not in explicit
        assert "tipo_inyeccion" not in explicit
        assert "responsabilidad_integral" not in explicit
    elif service_code == "CHOFER":
        assert explicit["finalidad"] == "traslado_empleados"
        assert "tipo_vehiculo_otro" not in explicit
        assert explicit["rutas"] == ["dentro_ciudad", "larga_distancia"]
        assert explicit["categoria_licencia_requerida"] == "categoria_02"
        assert "disponibilidad" not in explicit and "viajes_largos" not in explicit
    elif service_code == "LIMPIEZA_ESTABLECIMIENTOS":
        assert explicit["areas"] == ["Recepción"]
    elif service_code == "CONSERJERIA_RESIDENCIAL":
        assert explicit["tipo_instalacion"] == "Residencial"


def test_conserje_admin_mapping_writes_new_structured_contract_and_keeps_legacy_fields():
    from services.service_catalog import admin_details_from_form
    form = SimpleNamespace(
        conserje_tipo_residencial=SimpleNamespace(data="condominio_residencial"),
        conserje_tipo_residencial_otro=SimpleNamespace(data=""),
        conserje_niveles=SimpleNamespace(data=5), conserje_apartamentos=SimpleNamespace(data=36),
        conserje_ascensores=SimpleNamespace(data=2), conserje_escaleras=SimpleNamespace(data=3),
        conserje_cantidad_edificios=SimpleNamespace(data=3), conserje_cantidad_casas=SimpleNamespace(data=None),
        conserje_unidades=SimpleNamespace(data=None), conserje_tiene_ascensor=SimpleNamespace(data=""),
        conserje_parqueos_comunes=SimpleNamespace(data="si"), conserje_areas_recreativas=SimpleNamespace(data="si"),
        conserje_jardines_comunes=SimpleNamespace(data="si"), conserje_calles_internas=SimpleNamespace(data=""),
        conserje_banos=SimpleNamespace(data=1), conserje_estacionamiento=SimpleNamespace(data="si"),
        conserje_instalaciones=SimpleNamespace(data=["lobby_recepcion", "ascensores"]),
        conserje_otras_instalaciones=SimpleNamespace(data=""),
        conserje_responsabilidades=SimpleNamespace(data=["inspeccion_visual", "reportar_averias"]),
        conserje_responsabilidades_otro=SimpleNamespace(data=""),
        conserje_trabaja_con_conserjes=SimpleNamespace(data="si"),
        conserje_cantidad_conserjes=SimpleNamespace(data=2),
        conserje_tipo_instalacion=SimpleNamespace(data="Comercial"),
        conserje_areas_comunes=SimpleNamespace(data="Lobby histórico"),
        conserje_areas_exteriores=SimpleNamespace(data="Patio histórico"),
        conserje_manejo_basura=SimpleNamespace(data="Contenedores históricos"),
        conserje_mantenimiento=SimpleNamespace(data="Revisiones históricas"),
        conserje_parqueos=SimpleNamespace(data=8),
    )
    data = admin_details_from_form("CONSERJERIA_RESIDENCIAL", form)
    assert data["tipo_residencial"] == "condominio_residencial"
    assert data["apartamentos"] == 36 and data["estacionamiento"] == "si"
    assert data["cantidad_edificios"] == 3 and data["parqueos_comunes"] == "si"
    assert data["instalaciones"] == ["lobby_recepcion", "ascensores"]
    assert data["responsabilidades"] == ["inspeccion_visual", "reportar_averias"]
    assert data["cantidad_conserjes"] == 2
    assert data["tipo_instalacion"] == "Comercial"
    assert data["areas_comunes"] == "Lobby histórico"


def test_driver_responsibilities_are_optional_and_persist_when_provided():
    from services.service_catalog import public_details_from_form
    empty = public_details_from_form("CHOFER", MultiDict([("svc_driver_purpose", "personal_familiar"), ("svc_driver_vehicle", "carro"), ("svc_driver_license_category", "categoria_02")]))
    assert "responsabilidades" not in empty
    with_text = public_details_from_form("CHOFER", MultiDict([("svc_driver_purpose", "personal_familiar"), ("svc_driver_vehicle", "carro"), ("svc_driver_license_category", "categoria_02"), ("svc_driver_tasks", "Llevar el vehículo a mantenimiento")]))
    assert with_text["responsabilidades"] == "Llevar el vehículo a mantenimiento"
    assert not __import__("services.service_catalog", fromlist=["validate_service_details"]).validate_service_details("CHOFER", empty, strict_public=True)
def test_service_switch_merge_retains_previous_service_and_historical_json_fields():
    previous = {
        "tipo": "NINERA", "cantidad_ninos": 2,
        "necesidad_especial_respuesta": "conversar",
        "ninera_tareas_otro": "Preparar meriendas",
        "ninera_tareas_otro_revision_sha256": False,
    }
    next_details = {"tipo": "LIMPIEZA_ESTABLECIMIENTOS", "tamano": "Mediano", "areas": "Recepción"}
    saved = merge_service_details(previous, next_details, "LIMPIEZA_ESTABLECIMIENTOS")
    assert saved["tipo"] == "LIMPIEZA_ESTABLECIMIENTOS"
    assert saved["tamano"] == "Mediano"
    assert saved["_by_type"]["NINERA"]["cantidad_ninos"] == 2
    assert saved["_by_type"]["NINERA"]["ninera_tareas_otro"] == previous["ninera_tareas_otro"]
    assert previous["tipo"] == "NINERA"
    assert "tamano" not in previous


@pytest.mark.parametrize(("legacy", "canonical"), [
    ("LIMPIEZA_OFICINA", "LIMPIEZA_ESTABLECIMIENTOS"),
    ("CONSERJE", "CONSERJERIA_RESIDENCIAL"),
])
def test_admin_edit_form_reads_legacy_service_choice_without_making_it_a_new_choice(legacy, canonical):
    row = SimpleNamespace(tipo_servicio=legacy, experiencia="Histórico", detalles_servicio={"tipo": legacy})
    with flask_app.test_request_context("/admin/historic-edit"):
        form = AdminSolicitudForm(obj=row, meta={"csrf": False})
    assert form.tipo_servicio.data == legacy
    assert legacy in {value for value, _label in form.tipo_servicio.choices}
    assert canonical in {value for value, _label in SERVICE_CHOICES}
    assert legacy not in {value for value, _label in SERVICE_CHOICES}


def test_combine_service_details_is_equivalent_to_legacy_coordination_and_does_not_mutate_inputs():
    previous = {
        "tipo": "NINERA", "cantidad_ninos": 2,
        "ninera_tareas_otro": "Preparar meriendas",
        "ninera_tareas_otro_revision_sha256": "approved-digest",
        "ayuda_cuidado_ninos": "Apoyar en rutina de alimentación",
        "horario_tipo": "salida_diaria", "dias_trabajo": "Lunes a viernes",
        "_by_type": {"CHOFER": {"tipo_vehiculo": "carro", "historic_flag": True}},
    }
    updates = {"tipo": "LIMPIEZA_ESTABLECIMIENTOS", "tamano": "Mediano", "areas": "Recepción"}
    previous_before, updates_before = deepcopy(previous), deepcopy(updates)

    # Characterize the previous Admin coordination sequence as the oracle.
    legacy_updates = deepcopy(updates)
    if previous.get("ayuda_cuidado_ninos") and not legacy_updates.get("ayuda_cuidado_ninos"):
        legacy_updates["ayuda_cuidado_ninos"] = previous["ayuda_cuidado_ninos"]
    legacy_result = merge_service_details(deepcopy(previous), legacy_updates, "LIMPIEZA_ESTABLECIMIENTOS")

    actual = combine_service_details(previous, updates, "LIMPIEZA_ESTABLECIMIENTOS")
    assert actual == legacy_result
    assert previous == previous_before
    assert updates == updates_before
    assert actual["ayuda_cuidado_ninos"] == previous["ayuda_cuidado_ninos"]
    assert actual["dias_trabajo"] == "Lunes a viernes"
    assert actual["_by_type"]["NINERA"]["ninera_tareas_otro_revision_sha256"] == "approved-digest"
    assert actual["_by_type"]["CHOFER"]["historic_flag"] is True


@pytest.mark.parametrize("old_type", [code for code, _label in SERVICE_CHOICES])
@pytest.mark.parametrize("new_type", [code for code, _label in SERVICE_CHOICES])
def test_combine_preserves_details_and_approvals_across_service_type_changes(old_type, new_type):
    previous = {"tipo": old_type, **deepcopy(OLD_SERVICE_DETAILS[old_type]),
                "horario_tipo": "salida_diaria", "dias_trabajo": "Lunes a viernes",
                "_by_type": {"CHOFER": {"disponibilidad": "historical"}}}
    update = {"tipo": new_type, **deepcopy(OLD_SERVICE_DETAILS[new_type]),
              "frecuencia": "Diaria", "_by_type": {"UNTRUSTED": {"x": 1}}}
    previous_before, update_before = deepcopy(previous), deepcopy(update)

    result = combine_service_details(previous, update, new_type)
    assert result["tipo"] == new_type
    assert result["frecuencia"] == "Diaria"
    assert result["horario_tipo"] == "salida_diaria"
    assert result["dias_trabajo"] == "Lunes a viernes"
    # merge_service_details owns archive semantics; the combiner must retain
    # all registered old-service details, including text review fingerprints.
    if old_type == "NINERA" and old_type != new_type:
        assert result["_by_type"]["NINERA"]["ninera_tareas_otro_revision_sha256"] == "sha-reviewed"
    if old_type == new_type == "NINERA":
        assert result["ninera_tareas_otro_revision_sha256"] == "sha-reviewed"
    if old_type == "ENFERMERA" and old_type != new_type:
        assert result["_by_type"]["ENFERMERA"]["personas_cuidado_historicas"][0]["perfil"]["edad"] == 81
    assert "UNTRUSTED" not in (result.get("_by_type") or {})
    assert result["_by_type"]["CHOFER"]["disponibilidad"] == "historical"
    assert previous == previous_before
    assert update == update_before


def test_chofer_legacy_text_routes_are_archived_when_structured_routes_are_saved():
    previous = {"tipo": "CHOFER", "tipo_servicio": "Traslados", "tipo_vehiculo": "Carro",
                "rutas": "Centro y aeropuerto", "licencia_requisitos": "Licencia vigente", "disponibilidad": "Lun a vie"}
    saved = merge_service_details(previous, {"tipo": "CHOFER", "rutas": ["dentro_ciudad"], "categoria_licencia_requerida": "categoria_02"}, "CHOFER")
    assert saved["rutas"] == ["dentro_ciudad"]
    assert saved["rutas_legacy"] == "Centro y aeropuerto"
    assert saved["licencia_requisitos"] == "Licencia vigente"
    assert saved["disponibilidad"] == "Lun a vie"
    assert previous["rutas"] == "Centro y aeropuerto"


def test_service_switch_away_and_back_restores_review_approval_and_historical_values():
    original = {
        "tipo": "NINERA", "cantidad_ninos": 2, "edades_ninos": "3 y 7 años",
        "tareas": ["supervision"], "ninera_tareas_otro": "Acompañar tareas escolares",
        "ninera_tareas_otro_revision_sha256": "reviewed-fingerprint",
        "responsabilidad_integral": True,
        "horario_tipo": "salida_diaria", "dias_trabajo": "Lunes a viernes",
    }
    switched = combine_service_details(
        original,
        {"tipo": "CONSERJERIA_RESIDENCIAL", "tipo_instalacion": "Residencial", "areas_comunes": "Lobby"},
        "CONSERJERIA_RESIDENCIAL",
    )
    restored = combine_service_details(
        switched,
        {"tipo": "NINERA", "cantidad_ninos": 2},
        "NINERA",
    )
    assert restored["ninera_tareas_otro_revision_sha256"] == "reviewed-fingerprint"
    assert restored["ninera_tareas_otro"] == "Acompañar tareas escolares"
    assert restored["responsabilidad_integral"] is True
    assert restored["dias_trabajo"] == "Lunes a viernes"
    assert switched["tipo"] == "CONSERJERIA_RESIDENCIAL"
    assert original["tipo"] == "NINERA"
