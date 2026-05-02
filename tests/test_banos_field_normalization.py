# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from app import app as flask_app
import clientes.routes as clientes_routes
import admin.routes as admin_routes


def _assert_banos_apply(apply_fn, raw_value: str, expected: float):
    solicitud = SimpleNamespace(banos=None)
    form = SimpleNamespace(banos=SimpleNamespace(data=None))
    apply_fn(solicitud, form)
    assert solicitud.banos == expected


def _assert_client_banos(raw_value: str, expected: float):
    with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data={"banos": raw_value}):
        _assert_banos_apply(clientes_routes._apply_banos_from_request, raw_value, expected)


def _assert_admin_banos(raw_value: str, expected: float):
    with flask_app.test_request_context("/admin/clientes/1/solicitudes/nueva", method="POST", data={"banos": raw_value}):
        _assert_banos_apply(admin_routes._apply_banos_from_request, raw_value, expected)


def test_crear_desde_clientes_con_banos_1():
    _assert_client_banos("1", 1.0)


def test_crear_desde_clientes_con_banos_5():
    _assert_client_banos("5", 5.0)


def test_crear_desde_clientes_con_banos_5_5():
    _assert_client_banos("5.5", 5.5)


def test_editar_desde_clientes_conservando_1():
    _assert_client_banos("1", 1.0)


def test_editar_desde_clientes_conservando_5():
    _assert_client_banos("5", 5.0)


def test_editar_desde_clientes_conservando_5_5():
    _assert_client_banos("5.5", 5.5)


def test_editar_desde_clientes_conservando_otro_mayor_a_5_5():
    _assert_client_banos("6.5", 6.5)


def test_crear_desde_admin_con_banos_1():
    _assert_admin_banos("1", 1.0)


def test_crear_desde_admin_con_banos_5():
    _assert_admin_banos("5", 5.0)


def test_crear_desde_admin_con_banos_5_5():
    _assert_admin_banos("5.5", 5.5)


def test_editar_desde_admin_conservando_1():
    _assert_admin_banos("1", 1.0)


def test_editar_desde_admin_conservando_5():
    _assert_admin_banos("5", 5.0)


def test_editar_desde_admin_conservando_5_5():
    _assert_admin_banos("5.5", 5.5)


def test_editar_desde_admin_conservando_otro_mayor_a_5_5():
    _assert_admin_banos("6.5", 6.5)
