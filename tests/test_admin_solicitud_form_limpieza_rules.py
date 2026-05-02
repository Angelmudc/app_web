# -*- coding: utf-8 -*-

from __future__ import annotations

from werkzeug.datastructures import MultiDict

from app import app as flask_app
from admin.forms import AdminSolicitudForm


def _base_payload() -> MultiDict:
    return MultiDict(
        {
            "tipo_servicio": "DOMESTICA_LIMPIEZA",
            "ciudad_sector": "Santiago Centro",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Con salida diaria - Lunes a Viernes",
            "modalidad_grupo": "con_salida_diaria",
            "modalidad_especifica": "Salida diaria - lunes a viernes",
            "edad_requerida": "26-35",
            "experiencia": "Experiencia en limpieza y cocina",
            "horario": "L-V 8:00 a 17:00",
            "funciones": "cocinar",
            "adultos": "2",
            "sueldo": "18000",
            "pasaje_aporte": "0",
        }
    )


def _validate(formdata: MultiDict):
    with flask_app.test_request_context("/", method="POST", data=formdata):
        form = AdminSolicitudForm(formdata=formdata, meta={"csrf": False})
        return form, form.validate()


def test_admin_structure_fields_not_required_when_limpieza_not_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["cocinar"])
    payload["tipo_lugar"] = ""
    payload["habitaciones"] = ""
    payload["banos"] = ""

    form, ok = _validate(payload)

    assert ok is True
    assert form.tipo_lugar.errors == []
    assert form.habitaciones.errors == []
    assert form.banos.errors == []


def test_admin_structure_fields_required_when_limpieza_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["limpieza"])
    payload["tipo_lugar"] = ""
    payload["habitaciones"] = ""
    payload["banos"] = ""

    form, ok = _validate(payload)

    assert ok is False
    assert any("tipo de lugar" in e.lower() for e in form.tipo_lugar.errors)
    assert any("habitaciones" in e.lower() for e in form.habitaciones.errors)
    assert any("baños" in e.lower() or "banos" in e.lower() for e in form.banos.errors)


def test_admin_adultos_not_required_when_only_ninos_function():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos"])
    payload["adultos"] = ""

    form, ok = _validate(payload)

    assert ok is True
    assert form.adultos.errors == []


def test_admin_adultos_required_when_ninos_plus_household_function():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos", "cocinar"])
    payload["adultos"] = ""

    form, ok = _validate(payload)

    assert ok is False
    assert any("adult" in e.lower() for e in form.adultos.errors)


def test_admin_accepts_habitaciones_y_banos_otro_values():
    payload = _base_payload()
    payload.setlist("funciones", ["limpieza"])
    payload["tipo_lugar"] = "casa"
    payload["habitaciones"] = "8"
    payload["banos"] = "6.5"
    payload.setlist("areas_comunes", ["sala"])
    payload["adultos"] = "2"

    form, ok = _validate(payload)

    assert ok is True
    assert form.habitaciones.data == 8
    assert float(form.banos.data) == 6.5


def test_admin_envejeciente_sin_tipo_falla_si_funcion_esta_marcada():
    payload = _base_payload()
    payload.setlist("funciones", ["envejeciente"])
    form, ok = _validate(payload)
    assert ok is False
    assert form.envejeciente_tipo_cuidado.errors


def test_admin_envejeciente_encamado_sin_responsabilidades_ni_solo_falla():
    payload = _base_payload()
    payload.setlist("funciones", ["envejeciente"])
    payload["envejeciente_tipo_cuidado"] = "encamado"
    form, ok = _validate(payload)
    assert ok is False
    assert form.envejeciente_responsabilidades.errors


def test_admin_envejeciente_no_requerido_si_no_seleccionan_funcion():
    payload = _base_payload()
    payload.setlist("funciones", ["cocinar"])
    form, ok = _validate(payload)
    assert ok is True
    assert form.envejeciente_tipo_cuidado.errors == []


def test_admin_envejeciente_independiente_valido():
    payload = _base_payload()
    payload.setlist("funciones", ["envejeciente"])
    payload["envejeciente_tipo_cuidado"] = "independiente"
    form, ok = _validate(payload)
    assert ok is True, form.errors


def test_admin_envejeciente_encamado_con_responsabilidades_valido():
    payload = _base_payload()
    payload.setlist("funciones", ["envejeciente"])
    payload["envejeciente_tipo_cuidado"] = "encamado"
    payload.setlist("envejeciente_responsabilidades", ["pampers", "higiene"])
    form, ok = _validate(payload)
    assert ok is True, form.errors


def test_admin_envejeciente_encamado_solo_acompanamiento_valido():
    payload = _base_payload()
    payload.setlist("funciones", ["envejeciente"])
    payload["envejeciente_tipo_cuidado"] = "encamado"
    payload["envejeciente_solo_acompanamiento"] = "y"
    form, ok = _validate(payload)
    assert ok is True, form.errors
