# -*- coding: utf-8 -*-

from __future__ import annotations

from app import app as flask_app
from clientes.forms import SolicitudForm
from models import Solicitud


def _base_payload(plan: str | None = "premium") -> dict:
    data = {
        "ciudad_sector": "Santiago / Los Jardines",
        "modalidad_trabajo": "Con salida diaria - Lunes a Viernes",
        "edad_requerida": ["26-35"],
        "experiencia": "Experiencia en limpieza general",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "funciones": ["limpieza"],
        "tipo_lugar": "casa",
        "habitaciones": "2",
        "banos": "1",
        "areas_comunes": ["sala"],
        "adultos": "2",
        "ninos": "0",
        "sueldo": "18000",
        "modalidad_grupo": "con_salida_diaria",
        "modalidad_especifica": "sd_l_v",
        "horario_dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
    }
    if plan is not None:
        data["tipo_plan"] = plan
    return data


def test_cliente_form_guarda_tipo_plan_normalizado_y_no_toca_payment_cycle():
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data=_base_payload("Premium")):
        form = SolicitudForm()
        assert form.validate() is True

        solicitud = Solicitud()
        form.populate_obj(solicitud)

        assert solicitud.tipo_plan == "premium"
        assert solicitud.payment_cycle_plan is None
        assert solicitud.payment_cycle_precio_total is None
        assert solicitud.payment_cycle_abono_requerido is None
        assert solicitud.payment_cycle_opened_at is None
        assert solicitud.payment_cycle_closed_at is None
        assert solicitud.payment_cycle_motivo_apertura is None


def test_cliente_form_permite_crear_sin_tipo_plan():
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data=_base_payload(None)):
        form = SolicitudForm()
        assert form.validate() is True
        solicitud = Solicitud()
        form.populate_obj(solicitud)
        assert solicitud.tipo_plan in (None, "")


def test_cliente_form_rechaza_tipo_plan_invalido():
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data=_base_payload("oro")):
        form = SolicitudForm()
        assert form.validate() is False
        assert "Selecciona un plan válido: Básico, Premium o VIP." in form.tipo_plan.errors
