# -*- coding: utf-8 -*-

from __future__ import annotations

from werkzeug.datastructures import MultiDict

from app import app as flask_app
from clientes.forms import SolicitudForm


def _base_payload() -> MultiDict:
    return MultiDict(
        {
            "ciudad_sector": "Santiago Centro",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Con salida diaria - Lunes a Viernes",
            "modalidad_grupo": "con_salida_diaria",
            "modalidad_especifica": "Salida diaria - lunes a viernes",
            "edad_requerida": "26-35",
            "experiencia": "Experiencia en limpieza y cocina",
            "horario": "L-V 8:00 a 17:00",
            "funciones": "limpieza",
            "tipo_lugar": "casa",
            "habitaciones": "3",
            "banos": "2",
            "adultos": "2",
            "areas_comunes": "sala",
            "sueldo": "18000",
        }
    )


def _validate(formdata: MultiDict):
    with flask_app.test_request_context("/", method="POST", data=formdata):
        form = SolicitudForm(formdata=formdata, meta={"csrf": False})
        return form, form.validate()


def test_ninos_not_required_when_cuidar_ninos_is_not_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["limpieza"])
    payload["ninos"] = ""
    payload["edades_ninos"] = ""

    form, ok = _validate(payload)

    assert ok is True
    assert form.ninos.errors == []
    assert form.edades_ninos.errors == []


def test_ninos_is_required_when_cuidar_ninos_is_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["limpieza", "ninos"])
    payload["ninos"] = ""
    payload["edades_ninos"] = ""

    form, ok = _validate(payload)

    assert ok is False
    assert any("cuántos niños" in e.lower() for e in form.ninos.errors)


def test_edades_ninos_is_required_when_cuidar_ninos_and_ninos_gt_zero():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos"])
    payload["ninos"] = "2"
    payload["edades_ninos"] = ""

    form, ok = _validate(payload)

    assert ok is False
    assert any("edades" in e.lower() for e in form.edades_ninos.errors)


def test_edades_ninos_not_required_when_cuidar_ninos_and_ninos_zero():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos"])
    payload["ninos"] = "0"
    payload["edades_ninos"] = ""

    form, ok = _validate(payload)

    assert ok is True
    assert form.edades_ninos.errors == []


def test_form_placeholders_remove_optional_copy_for_mascota_and_edades_ninos():
    with flask_app.app_context():
        form = SolicitudForm(meta={"csrf": False})

        assert "(opcional)" not in str(form.mascota.render_kw.get("placeholder", "")).lower()
        assert "(opcional)" not in str(form.edades_ninos.render_kw.get("placeholder", "")).lower()


def test_adultos_not_required_when_only_cuidar_ninos_is_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos"])
    payload["adultos"] = ""
    payload["ninos"] = "1"
    payload["edades_ninos"] = "4"

    form, ok = _validate(payload)

    assert ok is True
    assert form.adultos.errors == []


def test_adultos_required_when_cuidar_ninos_and_cocinar_are_selected():
    payload = _base_payload()
    payload.setlist("funciones", ["ninos", "cocinar"])
    payload["adultos"] = ""
    payload["ninos"] = "1"
    payload["edades_ninos"] = "4"

    form, ok = _validate(payload)

    assert ok is False
    assert any("adult" in e.lower() for e in form.adultos.errors)


def test_mascota_guidance_does_not_autofill_nota_cliente():
    payload = _base_payload()
    payload.setlist("funciones", ["limpieza"])
    payload["mascota"] = "Perro"
    payload["nota_cliente"] = ""

    form, ok = _validate(payload)

    assert ok is True
    assert (form.nota_cliente.data or "") == ""
