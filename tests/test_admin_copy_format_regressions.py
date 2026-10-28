from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from app import app as flask_app
import admin.routes as admin_routes
from services.service_catalog import public_details_from_form
from services.service_catalog import merge_service_details, service_details_for_type
from services.service_details_mapping import build_service_details_from_form


EXPERIENCE_BY_SERVICE = {
    "DOMESTICA_LIMPIEZA": "Cuidado de envejeciente",
    "NINERA": "Niñera",
    "ENFERMERA": "Cuidadora de envejecientes",
    "CHOFER": "Chofer",
    "LIMPIEZA_ESTABLECIMIENTOS": "Limpieza",
    "CONSERJERIA_RESIDENCIAL": "Conserjería",
}

VALID_EXPERIENCE_VALUES = {
    "DOMESTICA_LIMPIEZA": (
        "Doméstica completa", "Cocina", "Niñera", "Enfermera",
        "Cuidado de envejeciente", "Limpieza", "Conserjería",
    ),
    "NINERA": ("Niñera", "Enfermera"),
    "ENFERMERA": (
        "Enfermera", "Cuidadora de envejecientes", "Cuidadora de personas encamadas",
    ),
    "CHOFER": ("Chofer",),
    "LIMPIEZA_ESTABLECIMIENTOS": ("Limpieza",),
    "CONSERJERIA_RESIDENCIAL": ("Conserjería",),
}


def _request(service, experience):
    details = {"tipo": service}
    if service == "ENFERMERA" and experience:
        details["experiencia_requerida"] = experience
    return SimpleNamespace(
        id=10,
        codigo_solicitud="SOL-010",
        estado="activa",
        cliente_id=7,
        cliente=None,
        tipo_servicio=service,
        detalles_servicio=details,
        ciudad_sector="Santiago",
        rutas_cercanas="27 de Febrero",
        modalidad_trabajo="Salida diaria",
        edad_requerida=["25 en adelante"],
        experiencia=experience,
        horario="Lunes a viernes, 8:00 AM a 5:00 PM",
        horario_estructurado=None,
        funciones=["limpieza", "envejeciente"] if service == "DOMESTICA_LIMPIEZA" else [],
        funciones_otro="",
        tipo_lugar="Casa" if service == "DOMESTICA_LIMPIEZA" else "",
        habitaciones=3 if service == "DOMESTICA_LIMPIEZA" else 0,
        banos=2 if service == "DOMESTICA_LIMPIEZA" else 0,
        areas_comunes=[], area_otro="", dos_pisos=False,
        adultos=2 if service == "DOMESTICA_LIMPIEZA" else 0,
        ninos=2 if service == "DOMESTICA_LIMPIEZA" else 0,
        edades_ninos="4 y 14 años" if service == "DOMESTICA_LIMPIEZA" else "",
        mascota="", pasaje_aporte=False, sueldo="25000", nota_cliente="",
        envejeciente_tipo_cuidado="encamado" if service == "DOMESTICA_LIMPIEZA" else None,
        envejeciente_responsabilidades=["pampers", "higiene", "medicamentos"] if service == "DOMESTICA_LIMPIEZA" else [],
        envejeciente_solo_acompanamiento=False,
        envejeciente_nota="Nota privada sintética: usa cama clínica y requiere evaluación médica.",
    )


@pytest.mark.parametrize("service,experience", EXPERIENCE_BY_SERVICE.items())
def test_both_admin_generators_keep_selected_experience_before_schedule(service, experience):
    request = _request(service, experience)
    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        outputs = (
            admin_routes._admin_build_public_copy_text(request),
            admin_routes._admin_build_order_text_for_copiar(request),
        )

    for output in outputs:
        experience_line = f"Experiencia en: *{experience}*"
        assert experience_line in output
        assert output.index(experience_line) < output.index("Horario:")


@pytest.mark.parametrize("service", EXPERIENCE_BY_SERVICE)
def test_both_admin_generators_do_not_invent_missing_experience(service):
    request = _request(service, None)
    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        outputs = (
            admin_routes._admin_build_public_copy_text(request),
            admin_routes._admin_build_order_text_for_copiar(request),
        )

    for output in outputs:
        assert "Experiencia en:" not in output


@pytest.mark.parametrize(
    "service,experience",
    [(service, value) for service, values in VALID_EXPERIENCE_VALUES.items() for value in values],
)
def test_both_generators_keep_each_current_catalog_experience_value(service, experience):
    request = _request(service, experience)
    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        outputs = (
            admin_routes._admin_build_public_copy_text(request),
            admin_routes._admin_build_order_text_for_copiar(request),
        )

    for output in outputs:
        assert f"Experiencia en: *{experience}*" in output


def test_domestic_public_care_follows_functions_without_household_heading_or_private_note():
    request = _request("DOMESTICA_LIMPIEZA", "Cuidado de envejeciente")
    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        public_copy = admin_routes._admin_build_public_copy_text(request)
        internal_copy = admin_routes._admin_build_order_text_for_copiar(request)

    functions = "Funciones: Limpieza General, Cuidar envejecientes"
    care = "Envejeciente: encamado. Requiere: cambiar pampers, higiene personal, darle medicamentos."
    assert functions in public_copy
    assert care in public_copy
    assert public_copy.index(functions) < public_copy.index(care)
    assert "Composición del hogar" not in public_copy
    assert "Adultos: 2" in public_copy
    assert "Niños: 2 (4 y 14 años)" in public_copy
    assert "Nota privada sintética" not in public_copy
    assert "Composición del hogar" not in internal_copy
    assert "Adultos: 2" in internal_copy
    assert "Niños: 2 (4 y 14 años)" in internal_copy
    assert "Nota privada sintética: usa cama clínica y requiere evaluación médica." in internal_copy


def test_both_generators_use_explicit_custom_experience_from_current_service_details():
    custom = "Preparación de alimentos vegetarianos"
    request = _request("DOMESTICA_LIMPIEZA", custom)
    request.detalles_servicio["experiencia_requerida"] = custom

    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        outputs = (
            admin_routes._admin_build_public_copy_text(request),
            admin_routes._admin_build_order_text_for_copiar(request),
        )

    for output in outputs:
        experience_line = f"Experiencia en: *{custom}*"
        assert experience_line in output
        assert output.index(experience_line) < output.index("Horario:")


def test_public_details_mark_only_an_explicit_domestic_other_experience():
    explicit = public_details_from_form(
        "DOMESTICA_LIMPIEZA",
        MultiDict([("experiencia", "otro"), ("experiencia_otro", "Preparación de alimentos vegetarianos")]),
    )
    legacy_unattributed = public_details_from_form(
        "DOMESTICA_LIMPIEZA",
        MultiDict([("experiencia", "Preparación de alimentos vegetarianos")]),
    )

    assert explicit["experiencia_requerida"] == "Preparación de alimentos vegetarianos"
    assert "experiencia_requerida" not in legacy_unattributed


def test_admin_form_mapping_persists_explicit_domestic_other_experience():
    custom = "Preparación de alimentos vegetarianos"
    form = SimpleNamespace(
        tipo_servicio=SimpleNamespace(data="DOMESTICA_LIMPIEZA"),
        experiencia=SimpleNamespace(data="otro", raw_data=["otro"]),
        experiencia_otro=SimpleNamespace(data=custom),
    )

    details = build_service_details_from_form(form)

    assert details["experiencia_requerida"] == custom


def test_domestic_experience_provenance_is_archived_when_service_changes():
    custom = "Preparación de alimentos vegetarianos"
    merged = merge_service_details(
        {"tipo": "DOMESTICA_LIMPIEZA", "experiencia_requerida": custom},
        {"cantidad_ninos": 1, "edades_ninos": "5 años"},
        "NINERA",
    )

    assert "experiencia_requerida" not in merged
    assert service_details_for_type(merged, "DOMESTICA_LIMPIEZA")["experiencia_requerida"] == custom


def test_explicit_experience_marker_for_another_service_is_not_used():
    request = _request("DOMESTICA_LIMPIEZA", "Preparación de alimentos vegetarianos")
    request.detalles_servicio.update({"tipo": "NINERA", "experiencia_requerida": "Niñera"})

    with flask_app.app_context(), flask_app.test_request_context("/admin/solicitudes/copiar"):
        outputs = (
            admin_routes._admin_build_public_copy_text(request),
            admin_routes._admin_build_order_text_for_copiar(request),
        )

    for output in outputs:
        assert "Experiencia en:" not in output
