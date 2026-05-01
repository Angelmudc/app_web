# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from app import app as flask_app
import clientes.routes as clientes_routes


class _Field:
    def __init__(self, data=None, choices=None):
        self.data = data
        self.choices = choices or []


class _FakeSolicitudForm:
    def __init__(self):
        self.ciudad_sector = _Field("Santiago / Los Jardines")
        self.rutas_cercanas = _Field("Ruta K")
        self.modalidad_trabajo = _Field("Con dormida - Lunes a Viernes")
        self.horario = _Field("L-V 8:00 a 17:00")
        self.edad_requerida = _Field(["26-35", "otro"], choices=[("26-35", "26-35"), ("otro", "Otro")])
        self.edad_otro = _Field("40-45")
        self.experiencia = _Field("Cocina y limpieza")
        self.funciones = _Field(["limpieza", "otro"])
        self.funciones_otro = _Field("cuidar plantas")
        self.tipo_lugar = _Field("casa")
        self.tipo_lugar_otro = _Field("")
        self.habitaciones = _Field(3)
        self.banos = _Field("2.5")
        self.dos_pisos = _Field(True)
        self.areas_comunes = _Field(["sala", "otro"], choices=[("sala", "Sala"), ("otro", "Otro")])
        self.area_otro = _Field("balcon")
        self.adultos = _Field(2)
        self.ninos = _Field(1)
        self.edades_ninos = _Field("4")
        self.mascota = _Field("perro")
        self.sueldo = _Field("18,500")
        self.pasaje_aporte = _Field(False)
        self.nota_cliente = _Field("Necesito apoyo adicional")

    def populate_obj(self, obj):
        for name, value in self.__dict__.items():
            if isinstance(value, _Field):
                setattr(obj, name, value.data)


def _new_solicitud_ns():
    return SimpleNamespace(
        ciudad_sector="",
        rutas_cercanas="",
        modalidad_trabajo="",
        horario="",
        edad_requerida=[],
        experiencia="",
        funciones=[],
        funciones_otro=None,
        tipo_lugar="",
        habitaciones=0,
        banos=None,
        dos_pisos=False,
        areas_comunes=[],
        area_otro=None,
        adultos=0,
        ninos=0,
        edades_ninos="",
        mascota=None,
        sueldo=None,
        pasaje_aporte=False,
        nota_cliente="",
        fecha_ultima_modificacion=None,
    )


def test_public_solicitud_field_mapping_is_consistent_for_both_flows():
    form = _FakeSolicitudForm()
    s_nuevo = _new_solicitud_ns()
    s_existente = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()

    with flask_app.test_request_context(
        "/fake",
        method="POST",
        data={"banos": "2.5"},
    ):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_nuevo,
            form=form,
            public_pisos_value="3+",
            public_pasaje_mode="otro",
            public_pasaje_otro="aporte parcial",
            now_ref=now_ref,
        )
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_existente,
            form=form,
            public_pisos_value="3+",
            public_pasaje_mode="otro",
            public_pasaje_otro="aporte parcial",
            now_ref=now_ref,
        )

    comparable_fields = [
        "ciudad_sector",
        "rutas_cercanas",
        "modalidad_trabajo",
        "horario",
        "edad_requerida",
        "experiencia",
        "funciones",
        "funciones_otro",
        "tipo_lugar",
        "habitaciones",
        "banos",
        "dos_pisos",
        "areas_comunes",
        "area_otro",
        "adultos",
        "ninos",
        "edades_ninos",
        "mascota",
        "sueldo",
        "pasaje_aporte",
        "nota_cliente",
    ]

    for field in comparable_fields:
        assert getattr(s_nuevo, field) == getattr(s_existente, field)

    assert s_nuevo.fecha_ultima_modificacion == now_ref
    assert s_existente.fecha_ultima_modificacion == now_ref
