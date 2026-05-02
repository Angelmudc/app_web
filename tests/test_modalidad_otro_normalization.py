# -*- coding: utf-8 -*-

from app import app as flask_app
from admin.forms import AdminSolicitudForm
from clientes.forms import SolicitudForm, SolicitudPublicaForm, SolicitudClienteNuevoPublicaForm
from utils.modalidad import (
    canonicalize_modalidad_trabajo,
    split_modalidad_for_ui,
    should_preserve_existing_modalidad_on_edit,
)


def test_modalidad_otro_salida_diaria_no_duplica_prefijo():
    raw = "Salida diaria otro: salida diaria con lunes a viernes"
    assert canonicalize_modalidad_trabajo(raw) == "Salida diaria lunes a viernes"


def test_modalidad_otro_con_dormida_no_duplica_prefijo():
    raw = "Con dormida 💤 otro: con dormida quincenal"
    assert canonicalize_modalidad_trabajo(raw) == "Con dormida 💤 quincenal"


def test_modalidad_con_dormida_normaliza_con_emoji():
    raw = "Con dormida lunes a viernes"
    assert canonicalize_modalidad_trabajo(raw) == "Con dormida 💤 lunes a viernes"


def test_modalidad_viernes_a_lunes_se_convierte_a_fin_de_semana():
    raw = "Salida diaria - viernes a lunes"
    assert canonicalize_modalidad_trabajo(raw) == "Salida diaria - fin de semana"


def test_modalidad_normal_sin_otro_se_mantiene_compatible():
    raw = "Salida diaria - lunes a viernes"
    assert canonicalize_modalidad_trabajo(raw) == "Salida diaria - lunes a viernes"


def _assert_field_validator_normalizes(form_cls, value: str, expected: str):
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.test_request_context("/", method="POST"):
        form = form_cls(meta={"csrf": False})
        form.modalidad_trabajo.data = value
        form.validate_modalidad_trabajo(form.modalidad_trabajo)
        assert (form.modalidad_trabajo.data or "") == expected


def test_admin_form_normaliza_modalidad_otro():
    _assert_field_validator_normalizes(
        AdminSolicitudForm,
        "Salida diaria otro: salida diaria con lunes a viernes",
        "Salida diaria lunes a viernes",
    )


def test_cliente_interno_form_normaliza_modalidad_otro():
    _assert_field_validator_normalizes(
        SolicitudForm,
        "Con dormida 💤 otro: con dormida quincenal",
        "Con dormida 💤 quincenal",
    )


def test_publico_cliente_existente_form_normaliza_modalidad_otro():
    _assert_field_validator_normalizes(
        SolicitudPublicaForm,
        "Salida diaria otro: salida diaria con lunes a viernes",
        "Salida diaria lunes a viernes",
    )


def test_publico_cliente_nuevo_form_normaliza_modalidad_otro():
    _assert_field_validator_normalizes(
        SolicitudClienteNuevoPublicaForm,
        "Con dormida 💤 otro: con dormida quincenal",
        "Con dormida 💤 quincenal",
    )


def test_should_preserve_existing_modalidad_when_submitted_empty():
    assert should_preserve_existing_modalidad_on_edit(
        existing_value="Salida diaria - lunes a viernes",
        submitted_value="",
    )


def test_should_preserve_existing_modalidad_when_submitted_group_only_without_specific():
    assert should_preserve_existing_modalidad_on_edit(
        existing_value="Con dormida 💤 quincenal",
        submitted_value="Con dormida 💤",
        submitted_group="con_dormida",
        submitted_specific="",
        submitted_other="",
    )


def test_should_not_preserve_existing_modalidad_when_specific_is_submitted():
    assert not should_preserve_existing_modalidad_on_edit(
        existing_value="Con dormida 💤 quincenal",
        submitted_value="Con dormida 💤 lunes a viernes",
        submitted_group="con_dormida",
        submitted_specific="Con dormida 💤 lunes a viernes",
        submitted_other="",
    )


def test_split_modalidad_for_ui_precarga_con_dormida_quincenal():
    parsed = split_modalidad_for_ui("Con dormida 💤 quincenal")
    assert parsed == {
        "group": "con_dormida",
        "specific": "Con dormida 💤 quincenal",
        "other": "",
    }


def test_split_modalidad_for_ui_precarga_con_dormida_fin_semana():
    parsed = split_modalidad_for_ui("Con dormida 💤 fin de semana")
    assert parsed == {
        "group": "con_dormida",
        "specific": "Con dormida 💤 fin de semana",
        "other": "",
    }


def test_split_modalidad_for_ui_precarga_salida_diaria_lunes_sabado():
    parsed = split_modalidad_for_ui("Salida diaria - lunes a sábado")
    assert parsed == {
        "group": "con_salida_diaria",
        "specific": "Salida diaria - lunes a sábado",
        "other": "",
    }


def test_split_modalidad_for_ui_soporta_alias_normalizado():
    parsed = split_modalidad_for_ui("Salida diaria 1 dia a la semana")
    assert parsed == {
        "group": "con_salida_diaria",
        "specific": "Salida diaria - 1 día a la semana",
        "other": "",
    }


def test_split_modalidad_for_ui_soporta_salida_diaria_4_dias():
    parsed = split_modalidad_for_ui("Salida diaria - 4 días a la semana")
    assert parsed == {
        "group": "con_salida_diaria",
        "specific": "Salida diaria - 4 días a la semana",
        "other": "",
    }
