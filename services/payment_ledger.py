from __future__ import annotations

from decimal import Decimal

from flask import current_app

from config_app import db
from models import PagoSolicitud
from services.payment_rules import format_money, get_plan_price, normalize_plan


POSITIVE_TYPES = {"abono", "pago", "ajuste", "correccion"}
NEGATIVE_TYPES = {"devolucion"}


def _to_decimal(value) -> Decimal:
    return Decimal(format_money(value))


def crear_pago_solicitud(
    *,
    solicitud_id: int,
    cliente_id: int,
    monto,
    tipo_pago: str,
    metodo_pago: str | None = None,
    referencia: str | None = None,
    nota: str | None = None,
    registrado_por_id: int | None = None,
    origen: str | None = None,
    origen_id: str | None = None,
) -> PagoSolicitud:
    payment = PagoSolicitud(
        solicitud_id=int(solicitud_id),
        cliente_id=int(cliente_id),
        monto=format_money(monto),
        tipo_pago=str(tipo_pago or "").strip().lower() or "pago",
        metodo_pago=(metodo_pago or "").strip() or None,
        referencia=(referencia or "").strip() or None,
        nota=(nota or "").strip() or None,
        registrado_por_id=(int(registrado_por_id) if registrado_por_id else None),
        origen=(origen or "").strip() or None,
        origen_id=(origen_id or "").strip() or None,
    )
    db.session.add(payment)
    db.session.flush()
    return payment


def _iter_active_movimientos(solicitud_id: int):
    return (
        PagoSolicitud.query
        .filter(PagoSolicitud.solicitud_id == int(solicitud_id), PagoSolicitud.anulado_at.is_(None))
        .order_by(PagoSolicitud.created_at.asc(), PagoSolicitud.id.asc())
        .all()
    )


def calcular_total_pagado(solicitud_id: int) -> Decimal:
    total = Decimal("0.00")
    for mov in _iter_active_movimientos(solicitud_id):
        monto = _to_decimal(mov.monto)
        tipo = (mov.tipo_pago or "").strip().lower()
        if tipo in POSITIVE_TYPES and monto > Decimal("0.00"):
            total += monto
        elif tipo in NEGATIVE_TYPES and monto > Decimal("0.00"):
            total -= monto
    return total.quantize(Decimal("0.01"))


def calcular_total_abonado(solicitud_id: int) -> Decimal:
    total = Decimal("0.00")
    for mov in _iter_active_movimientos(solicitud_id):
        tipo = (mov.tipo_pago or "").strip().lower()
        if tipo != "abono":
            continue
        monto = _to_decimal(mov.monto)
        if monto > Decimal("0.00"):
            total += monto
    return total.quantize(Decimal("0.01"))


def calcular_saldo_pendiente(solicitud) -> Decimal:
    precio_plan = get_plan_price(getattr(solicitud, "tipo_plan", None))
    plan_norm = normalize_plan(getattr(solicitud, "tipo_plan", None))
    if plan_norm not in {"basico", "premium", "vip"}:
        current_app.logger.warning(
            "payment_ledger plan_unknown solicitud_id=%s tipo_plan=%s fallback_price=%s",
            getattr(solicitud, "id", None),
            getattr(solicitud, "tipo_plan", None),
            str(precio_plan),
        )
    total_pagado = calcular_total_pagado(int(getattr(solicitud, "id", 0) or 0))
    saldo = (precio_plan - total_pagado).quantize(Decimal("0.01"))
    return saldo if saldo > Decimal("0.00") else Decimal("0.00")


def sync_solicitud_payment_cache(solicitud) -> Decimal:
    total_pagado = calcular_total_pagado(int(getattr(solicitud, "id", 0) or 0))
    solicitud.monto_pagado = format_money(total_pagado)
    return total_pagado


def recalcular_estado_pago_solicitud(solicitud) -> str:
    legacy_monto_pagado = _to_decimal(getattr(solicitud, "monto_pagado", None))
    total_pagado = sync_solicitud_payment_cache(solicitud)
    if total_pagado <= Decimal("0.00") and legacy_monto_pagado > Decimal("0.00"):
        total_pagado = legacy_monto_pagado
        solicitud.monto_pagado = format_money(total_pagado)
    precio_plan = get_plan_price(getattr(solicitud, "tipo_plan", None))
    estado_actual = (getattr(solicitud, "estado", "") or "").strip().lower()

    if total_pagado >= precio_plan and precio_plan > Decimal("0.00"):
        solicitud.estado = "pagada"
        return "pagada"
    if total_pagado > Decimal("0.00"):
        solicitud.estado = "espera_pago"
        return "espera_pago"

    # Si no hay pago, preservamos el estado operativo actual.
    if estado_actual in {"proceso", "activa", "reemplazo", "cancelada", "espera_pago", "pagada"}:
        solicitud.estado = estado_actual
        return estado_actual
    solicitud.estado = "proceso"
    return "proceso"
