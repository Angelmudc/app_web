import pytest
from types import SimpleNamespace

from app import app as flask_app
from admin.forms import AdminSolicitudForm
from admin.routes import _populate_form_detalles_from_solicitud
from clientes.forms import SolicitudClienteNuevoPublicaForm, SolicitudPublicaForm
from services.service_catalog import merge_service_details, public_details_from_form
from services.service_details_mapping import build_service_details_from_form, combine_service_details


class PublicFormData(dict):
    def getlist(self, name):
        value = self.get(name)
        return value if isinstance(value, list) else ([value] if value else [])


def test_public_service_details_store_precise_address_per_request():
    details = public_details_from_form(
        "NINERA",
        PublicFormData({
            "svc_ninera_count": "2",
            "svc_ninera_ages": "2 y 6 años",
            "direccion_precisa": "Calle Ficticia 123, Residencial Sol, casa 4",
        }),
    )

    assert details["direccion_precisa"] == "Calle Ficticia 123, Residencial Sol, casa 4"


def test_service_switch_preserves_precise_request_address():
    merged = merge_service_details(
        {"tipo": "NINERA", "cantidad_ninos": 2, "direccion_precisa": "Dirección anterior de esta solicitud"},
        {"tipo_establecimiento": "oficina"},
        "LIMPIEZA_ESTABLECIMIENTOS",
    )

    assert merged["direccion_precisa"] == "Dirección anterior de esta solicitud"


def test_public_service_details_do_not_invent_address_when_missing():
    details = public_details_from_form(
        "CHOFER",
        PublicFormData({"svc_driver_purpose": "traslado", "svc_driver_vehicle": "cliente"}),
    )

    assert "direccion_precisa" not in details


def test_domestica_service_keeps_common_address_in_json_details():
    details = public_details_from_form(
        "DOMESTICA_LIMPIEZA",
        PublicFormData({"direccion_precisa": "Calle Ficticia 10, casa 2"}),
    )

    assert details == {"direccion_precisa": "Calle Ficticia 10, casa 2"}


@pytest.mark.parametrize("form_type", [SolicitudPublicaForm, SolicitudClienteNuevoPublicaForm])
def test_public_form_rejects_missing_precise_request_address(form_type):
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.test_request_context("/clientes/solicitudes/publica/test", method="POST", data={}):
        form = form_type()
        form.validate()

    assert "Indica la dirección precisa" in form.direccion_precisa.errors[0]


def test_admin_edit_form_loads_the_request_specific_address():
    request_row = SimpleNamespace(
        tipo_servicio="NINERA",
        detalles_servicio={"tipo": "NINERA", "cantidad_ninos": "1", "direccion_precisa": "Calle Ficticia 10, casa 2"},
    )
    with flask_app.test_request_context("/admin/solicitud/editar"):
        form = AdminSolicitudForm()
        _populate_form_detalles_from_solicitud(form, request_row)

    assert form.direccion_precisa.data == "Calle Ficticia 10, casa 2"


def test_admin_service_edit_preserves_address_when_other_details_change():
    class Field:
        def __init__(self, data):
            self.data = data

    form = SimpleNamespace(
        tipo_servicio=Field("NINERA"),
        modalidad_trabajo=Field("Salida diaria"),
        horario=Field(""),
        direccion_precisa=Field(""),
        ninera_necesidad_especial_respuesta=Field(None),
        ninera_responsable_principal=Field(None),
        ninera_responsabilidad_integral=Field(False),
    )
    previous = {"tipo": "NINERA", "cantidad_ninos": "1", "direccion_precisa": "Calle Ficticia 10, casa 2"}
    updates = build_service_details_from_form(form, schedule_inputs={})

    saved = combine_service_details(previous, updates, "NINERA")

    assert saved["direccion_precisa"] == previous["direccion_precisa"]
