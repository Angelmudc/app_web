from types import SimpleNamespace

import pytest

from app import app as flask_app
import admin.routes as admin_routes


EXPERIENCE_BY_SERVICE = {
    "DOMESTICA_LIMPIEZA": "Cuidado de envejeciente",
    "NINERA": "Niñera",
    "ENFERMERA": "Cuidadora de envejecientes",
    "CHOFER": "Chofer",
    "LIMPIEZA_ESTABLECIMIENTOS": "Limpieza",
    "CONSERJERIA_RESIDENCIAL": "Conserjería",
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
