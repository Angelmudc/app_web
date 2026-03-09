# -*- coding: utf-8 -*-

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes
import clientes.routes as clientes_routes


def _cliente_solicitud_dummy():
    return SimpleNamespace(
        id=1,
        cliente_id=7,
        codigo_solicitud="CL-007-A",
        ciudad_sector="Santiago",
        rutas_cercanas="Ruta K",
        modalidad_trabajo="Dormida",
        horario="L-V",
        edad_requerida=["26–35"],
        edad_otro="",
        experiencia="Experiencia base",
        funciones=["limpieza"],
        funciones_otro="cuidado de plantas",
        tipo_lugar="casa",
        tipo_lugar_otro="",
        habitaciones=2,
        banos=1.0,
        dos_pisos=False,
        areas_comunes=["sala"],
        area_otro="balcon",
        adultos=2,
        ninos=1,
        edades_ninos="5",
        mascota="gato",
        sueldo="18000",
        pasaje_aporte=False,
        nota_cliente="",
    )


def _admin_solicitud_dummy():
    return SimpleNamespace(
        id=2,
        cliente_id=15,
        codigo_solicitud="CL-015-B",
        tipo_servicio="DOMESTICA_LIMPIEZA",
        ciudad_sector="Santiago",
        rutas_cercanas="Av. 27",
        modalidad_trabajo="Salida",
        horario="8-5",
        experiencia="Experiencia base",
        edad_requerida=["26–35"],
        funciones=["limpieza"],
        funciones_otro="compras",
        tipo_lugar="casa",
        habitaciones=3,
        banos=Decimal("1.5"),
        dos_pisos=False,
        areas_comunes=["sala"],
        area_otro="balcon",
        adultos=2,
        ninos=1,
        edades_ninos="6",
        mascota="perro",
        sueldo="22000",
        pasaje_aporte=False,
        nota_cliente="",
        detalles_servicio=None,
    )


def test_admin_areas_choices_include_otro_code():
    values = {v for v, _ in admin_routes.AREAS_COMUNES_CHOICES}
    assert "otro" in values


def test_client_edit_get_preloads_otro_fields_as_selected():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    captured = {}

    target = clientes_routes.editar_solicitud
    for _ in range(3):
        target = target.__wrapped__

    s = _cliente_solicitud_dummy()
    fake_user = SimpleNamespace(id=7)
    def _capture_render(*_a, **k):
        captured["ctx"] = k
        return "ok"

    with flask_app.test_request_context("/clientes/solicitudes/1/editar", method="GET"):
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "render_template", side_effect=_capture_render), \
             patch.object(clientes_routes, "Solicitud", SimpleNamespace(query=SimpleNamespace(filter_by=lambda **_k: SimpleNamespace(first_or_404=lambda: s)))):
            target(1)

    form = captured["ctx"]["form"]
    assert (form.modalidad_trabajo.data or "").strip() == "Dormida"
    assert "otro" in (form.funciones.data or [])
    assert (getattr(form, "funciones_otro").data or "").strip()
    assert "otro" in (form.areas_comunes.data or [])
    assert (getattr(form, "area_otro").data or "").strip()


def test_admin_edit_get_preloads_otro_fields_as_selected():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    captured = {}

    target = admin_routes.editar_solicitud_admin
    for _ in range(3):
        target = target.__wrapped__

    s = _admin_solicitud_dummy()
    def _capture_render(*_a, **k):
        captured["ctx"] = k
        return "ok"

    with flask_app.test_request_context("/admin/clientes/15/solicitudes/2/editar", method="GET"):
        with patch.object(admin_routes, "render_template", side_effect=_capture_render), \
             patch.object(admin_routes, "_populate_form_detalles_from_solicitud", return_value=None), \
             patch.object(admin_routes, "Solicitud", SimpleNamespace(query=SimpleNamespace(filter_by=lambda **_k: SimpleNamespace(first_or_404=lambda: s)))):
            target(15, 2)

    form = captured["ctx"]["form"]
    assert "otro" in (form.funciones.data or [])
    assert (getattr(form, "funciones_otro").data or "").strip()
    assert "otro" in (form.areas_comunes.data or [])
    assert (getattr(form, "area_otro").data or "").strip()


def test_shared_partial_has_independent_otro_wrappers_and_name_based_sync():
    path = "clientes/_solicitud_form_fields.html"
    content = flask_app.jinja_env.loader.get_source(flask_app.jinja_env, path)[0]

    assert "wrapper_id='wrap_edad_otro'" in content
    assert "wrapper_id='wrap_funciones_otro'" in content
    assert "wrapper_id='wrap_tipo_lugar_otro'" in content
    assert "wrapper_id='wrap_area_otro'" in content
    assert 'id="wrap_pasaje_otro"' in content
    assert "function getFieldsByName(name)" in content
    assert "function syncOtherFields(fromUserEvent)" in content
    assert "if (allRegularChecked()) setAll(false);" in content
    assert "allMasterControls[mi].checked = false;" in content
