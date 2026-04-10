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
