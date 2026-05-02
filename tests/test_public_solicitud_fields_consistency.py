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
        self.envejeciente_tipo_cuidado = _Field(None)
        self.envejeciente_responsabilidades = _Field([])
        self.envejeciente_solo_acompanamiento = _Field(False)
        self.envejeciente_nota = _Field("")
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
        self.detalles_servicio = _Field(None)

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
        detalles_servicio=None,
        envejeciente_tipo_cuidado=None,
        envejeciente_responsabilidades=None,
        envejeciente_solo_acompanamiento=False,
        envejeciente_nota=None,
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
        data={
            "banos": "2.5",
            "modalidad_grupo": "con_salida_diaria",
            "horario_dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
        },
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
    assert (s_nuevo.detalles_servicio or {}).get("cantidad_pisos") == "3+"
    assert (s_existente.detalles_servicio or {}).get("cantidad_pisos") == "3+"
    assert (s_nuevo.detalles_servicio or {}).get("horario_tipo") == "salida_diaria"
    assert (s_existente.detalles_servicio or {}).get("horario_tipo") == "salida_diaria"
    assert getattr(s_nuevo, "horario", "") == "Lunes a viernes, de 8:00 AM a 5:00 PM"
    assert getattr(s_existente, "horario", "") == "Lunes a viernes, de 8:00 AM a 5:00 PM"
    assert "Pisos reportados:" not in (s_nuevo.nota_cliente or "")
    assert "Pisos reportados:" not in (s_existente.nota_cliente or "")


def test_public_solicitud_mapping_accepts_habitaciones_y_banos_otro_values():
    form = _FakeSolicitudForm()
    form.habitaciones.data = 8
    form.banos.data = "6.5"
    s_nuevo = _new_solicitud_ns()
    s_existente = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()

    with flask_app.test_request_context(
        "/fake",
        method="POST",
        data={
            "banos": "6.5",
            "habitaciones": "8",
            "modalidad_grupo": "con_salida_diaria",
            "horario_dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
        },
    ):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_nuevo,
            form=form,
            public_pisos_value="2",
            public_pasaje_mode="incluido",
            public_pasaje_otro="",
            now_ref=now_ref,
        )
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_existente,
            form=form,
            public_pisos_value="2",
            public_pasaje_mode="incluido",
            public_pasaje_otro="",
            now_ref=now_ref,
        )

    assert s_nuevo.habitaciones == 8
    assert s_existente.habitaciones == 8
    assert s_nuevo.banos == 6.5
    assert s_existente.banos == 6.5


def test_public_solicitud_envejeciente_independiente_guarda_igual_en_ambos_flujos():
    form = _FakeSolicitudForm()
    form.funciones.data = ["envejeciente"]
    form.envejeciente_tipo_cuidado.data = "independiente"
    form.envejeciente_responsabilidades.data = []
    form.envejeciente_solo_acompanamiento.data = False
    form.envejeciente_nota.data = "Camina con apoyo"
    s_nuevo = _new_solicitud_ns()
    s_existente = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()
    with flask_app.test_request_context("/fake", method="POST", data={"horario": "L-V 8:00 a 17:00"}):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_nuevo, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_existente, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
    assert s_nuevo.envejeciente_tipo_cuidado == "independiente"
    assert s_existente.envejeciente_tipo_cuidado == "independiente"
    assert s_nuevo.envejeciente_responsabilidades is None
    assert s_existente.envejeciente_responsabilidades is None
    assert s_nuevo.envejeciente_solo_acompanamiento is False
    assert s_existente.envejeciente_solo_acompanamiento is False


def test_public_solicitud_envejeciente_encamado_responsabilidades_guarda_igual_en_ambos_flujos():
    form = _FakeSolicitudForm()
    form.funciones.data = ["envejeciente"]
    form.envejeciente_tipo_cuidado.data = "encamado"
    form.envejeciente_responsabilidades.data = ["pampers", "medicamentos"]
    form.envejeciente_solo_acompanamiento.data = False
    s_nuevo = _new_solicitud_ns()
    s_existente = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()
    with flask_app.test_request_context("/fake", method="POST", data={"horario": "L-V 8:00 a 17:00"}):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_nuevo, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_existente, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
    assert s_nuevo.envejeciente_tipo_cuidado == "encamado"
    assert s_existente.envejeciente_tipo_cuidado == "encamado"
    assert s_nuevo.envejeciente_responsabilidades == ["pampers", "medicamentos"]
    assert s_existente.envejeciente_responsabilidades == ["pampers", "medicamentos"]


def test_public_solicitud_envejeciente_encamado_solo_acompanamiento_guarda_igual_en_ambos_flujos():
    form = _FakeSolicitudForm()
    form.funciones.data = ["envejeciente"]
    form.envejeciente_tipo_cuidado.data = "encamado"
    form.envejeciente_responsabilidades.data = []
    form.envejeciente_solo_acompanamiento.data = True
    s_nuevo = _new_solicitud_ns()
    s_existente = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()
    with flask_app.test_request_context("/fake", method="POST", data={"horario": "L-V 8:00 a 17:00"}):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_nuevo, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s_existente, form=form, public_pisos_value="1", public_pasaje_mode="incluido", public_pasaje_otro="", now_ref=now_ref
        )
    assert s_nuevo.envejeciente_solo_acompanamiento is True
    assert s_existente.envejeciente_solo_acompanamiento is True


def test_money_sanitize_backend_strips_symbols_letters_and_emoji():
    assert clientes_routes._money_sanitize("16000") == "16000"
    assert clientes_routes._money_sanitize("RD$16000") == "16000"
    assert clientes_routes._money_sanitize("16,000🔥abc") == "16000"
    assert clientes_routes._money_sanitize("🔥abc") is None


def test_public_solicitud_mapping_saves_clean_salary_value():
    form = _FakeSolicitudForm()
    form.sueldo.data = "16,000🔥abc"
    s = _new_solicitud_ns()
    now_ref = clientes_routes.utc_now_naive()
    with flask_app.test_request_context(
        "/fake",
        method="POST",
        data={
            "banos": "2",
            "modalidad_grupo": "con_salida_diaria",
            "horario_dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
        },
    ):
        clientes_routes._apply_public_solicitud_fields(
            solicitud_obj=s,
            form=form,
            public_pisos_value="1",
            public_pasaje_mode="incluido",
            public_pasaje_otro="",
            now_ref=now_ref,
        )
    assert s.sueldo == "16000"
