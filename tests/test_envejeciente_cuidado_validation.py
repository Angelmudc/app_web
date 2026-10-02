from werkzeug.datastructures import MultiDict
from flask import Flask

import pytest

from clientes.forms import SolicitudClienteNuevoPublicaForm, SolicitudForm, SolicitudPublicaForm
from admin.forms import AdminSolicitudForm


def _base_payload():
    return MultiDict(
        {
            "ciudad_sector": "Santiago / Centro",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "modalidad_grupo": "con_salida_diaria",
            "modalidad_especifica": "sd_l_v",
            "horario_dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
            "edad_requerida": "26-35",
            "experiencia": "Experiencia general",
            "funciones": "envejeciente",
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "1",
            "adultos": "1",
            "ninos": "0",
            "sueldo": "18000",
            "areas_comunes": "sala",
            "pasaje_mode": "incluido",
            "pasaje_aporte": "0",
        }
    )


def _mk_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_cliente_form_envejeciente_requiere_tipo():
    app = _mk_app()
    with app.test_request_context(method="POST", data=_base_payload()):
        form = SolicitudForm(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_tipo_cuidado.errors


def test_cliente_form_encamado_requiere_responsabilidad_o_solo():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "encamado")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_responsabilidades.errors


def test_cliente_form_encamado_con_una_responsabilidad_valido():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "encamado")
    data.add("envejeciente_responsabilidades", "higiene")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


def test_cliente_form_encamado_rechaza_responsabilidad_y_solo_acompanamiento():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "encamado")
    data.add("envejeciente_responsabilidades", "medicamentos")
    data.add("envejeciente_solo_acompanamiento", "y")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_responsabilidades.errors
        assert form.envejeciente_solo_acompanamiento.errors


def test_cliente_form_independiente_valido():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "independiente")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


def test_admin_form_encamado_con_solo_acompanamiento_valido():
    app = _mk_app()
    data = _base_payload()
    data.add("tipo_servicio", "DOMESTICA_LIMPIEZA")
    data.add("envejeciente_tipo_cuidado", "encamado")
    data.add("envejeciente_solo_acompanamiento", "y")
    with app.test_request_context(method="POST", data=data):
        form = AdminSolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


def test_cliente_form_no_exige_envejeciente_si_funcion_no_marcada():
    app = _mk_app()
    data = _base_payload()
    data.setlist("funciones", ["cocinar"])
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


@pytest.mark.parametrize(
    ("form_class", "extra"),
    [
        (SolicitudPublicaForm, {"token": "tok123", "codigo_cliente": "CLI-001", "nombre_cliente": "Cliente Prueba", "email_cliente": "cliente@example.com"}),
        (SolicitudClienteNuevoPublicaForm, {"nombre_completo": "Cliente Nuevo", "email_contacto": "nuevo@example.com", "telefono_contacto": "809-123-4567", "ciudad_cliente": "Santiago", "sector_cliente": "Centro"}),
    ],
)
def test_public_form_rejects_envejeciente_without_type(form_class, extra):
    data = _base_payload()
    data.update(extra)
    with _mk_app().test_request_context(method="POST", data=data):
        form = form_class(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_tipo_cuidado.errors


@pytest.mark.parametrize(
    ("form_class", "extra"),
    [
        (SolicitudPublicaForm, {"token": "tok123", "codigo_cliente": "CLI-001", "nombre_cliente": "Cliente Prueba", "email_cliente": "cliente@example.com"}),
        (SolicitudClienteNuevoPublicaForm, {"nombre_completo": "Cliente Nuevo", "email_contacto": "nuevo@example.com", "telefono_contacto": "809-123-4567", "ciudad_cliente": "Santiago", "sector_cliente": "Centro"}),
    ],
)
def test_both_public_forms_accept_direct_responsibility_or_solo_acompanamiento(form_class, extra):
    for selected in ({"envejeciente_responsabilidades": "higiene"}, {"envejeciente_solo_acompanamiento": "y"}):
        data = _base_payload()
        data.update(extra)
        data.update({"envejeciente_tipo_cuidado": "encamado", **selected})
        with _mk_app().test_request_context(method="POST", data=data):
            form = form_class(meta={"csrf": False})
            form.validate()
            assert form.envejeciente_responsabilidades.errors == []
            assert form.envejeciente_solo_acompanamiento.errors == []


@pytest.mark.parametrize(
    ("form_class", "extra"),
    [
        (SolicitudPublicaForm, {"token": "tok123", "codigo_cliente": "CLI-001", "nombre_cliente": "Cliente Prueba", "email_cliente": "cliente@example.com"}),
        (SolicitudClienteNuevoPublicaForm, {"nombre_completo": "Cliente Nuevo", "email_contacto": "nuevo@example.com", "telefono_contacto": "809-123-4567", "ciudad_cliente": "Santiago", "sector_cliente": "Centro"}),
    ],
)
def test_both_public_forms_reject_contradictory_elder_care_payload(form_class, extra):
    data = _base_payload()
    data.update(extra)
    data.update({
        "envejeciente_tipo_cuidado": "encamado",
        "envejeciente_responsabilidades": "medicamentos",
        "envejeciente_solo_acompanamiento": "y",
    })
    with _mk_app().test_request_context(method="POST", data=data):
        form = form_class(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_responsabilidades.errors
        assert form.envejeciente_solo_acompanamiento.errors
