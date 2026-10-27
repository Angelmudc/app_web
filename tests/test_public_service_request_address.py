import pytest
from types import SimpleNamespace
from flask import render_template

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


def test_public_service_details_ignore_precise_address_even_if_submitted():
    details = public_details_from_form(
        "NINERA",
        PublicFormData({
            "svc_ninera_count": "2",
            "svc_ninera_ages": "2 y 6 años",
            "direccion_precisa": "Calle Ficticia 123, Residencial Sol, casa 4",
        }),
    )

    assert "direccion_precisa" not in details


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


def test_domestica_public_service_ignores_precise_address_even_if_submitted():
    details = public_details_from_form(
        "DOMESTICA_LIMPIEZA",
        PublicFormData({"direccion_precisa": "Calle Ficticia 10, casa 2"}),
    )

    assert "direccion_precisa" not in details


@pytest.mark.parametrize("form_type", [SolicitudPublicaForm, SolicitudClienteNuevoPublicaForm])
def test_public_form_does_not_require_precise_request_address(form_type):
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.test_request_context("/clientes/solicitudes/publica/test", method="POST", data={}):
        form = form_type()
        form.validate()

    assert not form.direccion_precisa.errors


@pytest.mark.parametrize("form_type", [SolicitudPublicaForm, SolicitudClienteNuevoPublicaForm])
def test_public_form_partial_does_not_render_precise_request_address(form_type):
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.test_request_context("/clientes/solicitudes/publica/test"):
        html = render_template(
            "clientes/_solicitud_form_fields.html",
            form=form_type(),
            service_form_public=True,
        )

    assert "Dirección precisa del servicio" not in html
    assert "Se guarda en esta solicitud para ubicar el servicio." not in html
    assert 'name="direccion_precisa"' not in html


def test_admin_form_partial_keeps_precise_request_address_editable():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.test_request_context("/admin/solicitud/editar"):
        html = render_template(
            "clientes/_solicitud_form_fields.html",
            form=AdminSolicitudForm(),
            service_form_public=False,
        )

    assert "Dirección precisa del servicio" in html
    assert 'name="direccion_precisa"' in html


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
