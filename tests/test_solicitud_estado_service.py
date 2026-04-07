# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from types import SimpleNamespace

from services.solicitud_estado import (
    days_in_state,
    priority_band_for_days,
    priority_message_for_solicitud,
    resolve_solicitud_estado_priority_anchor,
    set_solicitud_estado,
)


def test_set_solicitud_estado_transicion_real_actualiza_fechas_y_contadores():
    now = datetime(2026, 4, 7, 12, 0, 0)
    solicitud = SimpleNamespace(
        estado="proceso",
        estado_actual_desde=None,
        fecha_ultimo_estado=None,
        fecha_inicio_seguimiento=None,
        veces_activada=0,
        fecha_ultima_actividad=None,
        fecha_ultima_modificacion=None,
    )

    info = set_solicitud_estado(solicitud, "activa", now_dt=now)

    assert info["changed"] is True
    assert solicitud.estado == "activa"
    assert solicitud.estado_actual_desde == now
    assert solicitud.fecha_ultimo_estado == now
    assert solicitud.fecha_inicio_seguimiento == now
    assert solicitud.veces_activada == 1
    assert solicitud.fecha_ultima_actividad == now
    assert solicitud.fecha_ultima_modificacion == now


def test_set_solicitud_estado_mismo_estado_no_resetea_estado_actual_desde():
    start = datetime(2026, 4, 1, 9, 0, 0)
    now = datetime(2026, 4, 7, 12, 0, 0)
    solicitud = SimpleNamespace(
        estado="activa",
        estado_actual_desde=start,
        fecha_ultimo_estado=datetime(2026, 4, 1, 9, 0, 0),
        fecha_inicio_seguimiento=start,
        veces_activada=3,
        fecha_ultima_actividad=None,
        fecha_ultima_modificacion=None,
    )

    info = set_solicitud_estado(solicitud, "activa", now_dt=now)

    assert info["changed"] is False
    assert solicitud.estado_actual_desde == start
    assert solicitud.veces_activada == 3
    assert solicitud.fecha_ultima_actividad == now
    assert solicitud.fecha_ultima_modificacion == now


def test_resolve_anchor_reemplazo_prefiere_fecha_inicio_reemplazo_activo():
    now = datetime(2026, 4, 7, 12, 0, 0)
    repl_old = SimpleNamespace(fecha_inicio_reemplazo=now - timedelta(days=20), fecha_fin_reemplazo=now - timedelta(days=10))
    repl_active = SimpleNamespace(fecha_inicio_reemplazo=now - timedelta(days=6), fecha_fin_reemplazo=None)
    solicitud = SimpleNamespace(
        estado="reemplazo",
        estado_actual_desde=now - timedelta(days=2),
        fecha_ultima_modificacion=now - timedelta(days=1),
        fecha_solicitud=now - timedelta(days=30),
        reemplazos=[repl_old, repl_active],
    )

    anchor, source, estimated = resolve_solicitud_estado_priority_anchor(solicitud)

    assert anchor == repl_active.fecha_inicio_reemplazo
    assert source == "reemplazo.fecha_inicio_reemplazo"
    assert estimated is False
    assert days_in_state(anchor, now_dt=now) == 6


def test_priority_message_y_bandas_por_dias():
    assert priority_band_for_days(2) == "normal"
    assert priority_band_for_days(8) == "atencion"
    assert priority_band_for_days(12) == "urgente"
    assert priority_band_for_days(19) == "critica"

    assert priority_message_for_solicitud(estado="activa", days_in_current_state=3) == "Activa hace 3 días"
    assert priority_message_for_solicitud(estado="activa", days_in_current_state=12) == "Urgente: lleva 12 días activa"
    assert priority_message_for_solicitud(estado="reemplazo", days_in_current_state=22) == "Crítica: reemplazo abierto hace 22 días"


def test_reentrada_a_activa_reinicia_estado_actual_desde():
    t0 = datetime(2026, 4, 1, 9, 0, 0)
    t1 = datetime(2026, 4, 3, 9, 0, 0)
    t2 = datetime(2026, 4, 7, 9, 0, 0)
    solicitud = SimpleNamespace(
        estado="activa",
        estado_actual_desde=t0,
        fecha_ultimo_estado=t0,
        fecha_inicio_seguimiento=t0,
        veces_activada=1,
        fecha_ultima_actividad=t0,
        fecha_ultima_modificacion=t0,
    )

    info_to_pausa = set_solicitud_estado(solicitud, "espera_pago", now_dt=t1)
    info_back_to_activa = set_solicitud_estado(solicitud, "activa", now_dt=t2)

    assert info_to_pausa["changed"] is True
    assert info_back_to_activa["changed"] is True
    assert solicitud.estado == "activa"
    assert solicitud.estado_actual_desde == t2
    assert solicitud.fecha_inicio_seguimiento == t2
    assert solicitud.veces_activada == 2


def test_reentrada_a_reemplazo_reinicia_estado_actual_desde_y_anchor_usa_reemplazo_vigente():
    t0 = datetime(2026, 4, 1, 8, 0, 0)
    t1 = datetime(2026, 4, 2, 8, 0, 0)
    t2 = datetime(2026, 4, 5, 8, 0, 0)
    t3 = datetime(2026, 4, 6, 8, 0, 0)
    solicitud = SimpleNamespace(
        estado="activa",
        estado_actual_desde=t0,
        fecha_ultimo_estado=t0,
        fecha_inicio_seguimiento=t0,
        veces_activada=1,
        fecha_ultima_actividad=t0,
        fecha_ultima_modificacion=t0,
        fecha_solicitud=t0 - timedelta(days=10),
        reemplazos=[],
    )

    old_reemplazo = SimpleNamespace(fecha_inicio_reemplazo=t1, fecha_fin_reemplazo=t2)
    new_reemplazo = SimpleNamespace(fecha_inicio_reemplazo=t3, fecha_fin_reemplazo=None)

    set_solicitud_estado(solicitud, "reemplazo", now_dt=t1)
    assert solicitud.estado_actual_desde == t1
    solicitud.reemplazos = [old_reemplazo]

    set_solicitud_estado(solicitud, "activa", now_dt=t2)
    assert solicitud.estado_actual_desde == t2

    set_solicitud_estado(solicitud, "reemplazo", now_dt=t3)
    solicitud.reemplazos = [old_reemplazo, new_reemplazo]
    anchor, source, estimated = resolve_solicitud_estado_priority_anchor(solicitud)

    assert solicitud.estado_actual_desde == t3
    assert anchor == t3
    assert source == "reemplazo.fecha_inicio_reemplazo"
    assert estimated is False
