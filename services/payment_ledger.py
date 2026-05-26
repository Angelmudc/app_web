from __future__ import annotations

from decimal import Decimal

from flask import current_app

from config_app import db
from models import PagoSolicitud
from services.payment_rules import format_money, get_plan_price, get_required_deposit, normalize_plan
from utils.timezone import utc_now_naive


POSITIVE_TYPES = {"abono", "pago", "ajuste", "correccion"}
NEGATIVE_TYPES = {"devolucion"}


def _to_decimal(value) -> Decimal:
    return Decimal(format_money(value))


def _legacy_abono_value(solicitud) -> Decimal:
    raw = getattr(solicitud, "abono", None)
    if raw is None:
        return Decimal("0.00")
    return _to_decimal(raw)


def _cycle_plan(solicitud) -> str:
    return normalize_plan(getattr(solicitud, "payment_cycle_plan", None) or getattr(solicitud, "tipo_plan", None))


def _set_cycle_defaults(solicitud, *, cycle_num: int, motivo: str) -> None:
    plan_norm = _cycle_plan(solicitud)
    solicitud.payment_cycle_current = int(max(1, int(cycle_num)))
    solicitud.payment_cycle_plan = plan_norm
    solicitud.payment_cycle_precio_total = get_plan_price(plan_norm)
    solicitud.payment_cycle_abono_requerido = get_required_deposit(plan_norm)
    solicitud.payment_cycle_estado = "pendiente"
    solicitud.payment_cycle_opened_at = getattr(solicitud, "payment_cycle_opened_at", None) or utc_now_naive()
    solicitud.payment_cycle_closed_at = None
    solicitud.payment_cycle_motivo_apertura = (motivo or "").strip() or "auto_init"


def ensure_current_payment_cycle(solicitud, *, motivo: str = "auto_init") -> int:
    current = int(getattr(solicitud, "payment_cycle_current", 0) or 0)
    if current <= 0:
        current = 1
    if not getattr(solicitud, "payment_cycle_plan", None):
        _set_cycle_defaults(solicitud, cycle_num=current, motivo=motivo)
    elif getattr(solicitud, "payment_cycle_precio_total", None) is None:
        _set_cycle_defaults(solicitud, cycle_num=current, motivo=motivo)
    elif getattr(solicitud, "payment_cycle_abono_requerido", None) is None:
        _set_cycle_defaults(solicitud, cycle_num=current, motivo=motivo)
    elif not getattr(solicitud, "payment_cycle_estado", None):
        solicitud.payment_cycle_estado = "pendiente"
    return int(solicitud.payment_cycle_current or 1)


def get_current_payment_cycle(solicitud) -> dict:
    cycle_num = ensure_current_payment_cycle(solicitud)
    return {
        "numero_ciclo": cycle_num,
        "plan": _cycle_plan(solicitud),
        "precio_total_requerido": _to_decimal(getattr(solicitud, "payment_cycle_precio_total", None)),
        "abono_requerido": _to_decimal(getattr(solicitud, "payment_cycle_abono_requerido", None)),
        "estado_pago": str(getattr(solicitud, "payment_cycle_estado", None) or "pendiente").strip().lower(),
        "opened_at": getattr(solicitud, "payment_cycle_opened_at", None),
        "closed_at": getattr(solicitud, "payment_cycle_closed_at", None),
        "motivo_apertura": getattr(solicitud, "payment_cycle_motivo_apertura", None),
    }


def open_new_payment_cycle(solicitud, motivo: str, *, force: bool = False) -> dict:
    ensure_current_payment_cycle(solicitud)
    current_summary = get_payment_summary(solicitud)
    is_paid = Decimal(current_summary["saldo_pendiente"]) <= Decimal("0.00")
    if not force and not is_paid:
        return get_current_payment_cycle(solicitud)

    now_value = utc_now_naive()
    if getattr(solicitud, "payment_cycle_closed_at", None) is None:
        solicitud.payment_cycle_closed_at = now_value
    solicitud.payment_cycle_estado = "pagado" if is_paid else str(getattr(solicitud, "payment_cycle_estado", "pendiente") or "pendiente")
    next_cycle = int(getattr(solicitud, "payment_cycle_current", 1) or 1) + 1
    _set_cycle_defaults(solicitud, cycle_num=next_cycle, motivo=(motivo or "reactivacion"))
    solicitud.payment_cycle_opened_at = now_value
    return get_current_payment_cycle(solicitud)


def _iter_active_movimientos(solicitud_id: int, *, ciclo_numero: int | None = None):
    q = (
        PagoSolicitud.query
        .filter(PagoSolicitud.solicitud_id == int(solicitud_id), PagoSolicitud.anulado_at.is_(None))
    )
    if ciclo_numero is not None:
        q = q.filter(PagoSolicitud.ciclo_numero == int(ciclo_numero))
    return q.order_by(PagoSolicitud.created_at.asc(), PagoSolicitud.id.asc()).all()


def crear_pago_solicitud(
    *,
    solicitud_id: int,
    cliente_id: int,
    monto,
    tipo_pago: str,
    ciclo_numero: int | None = None,
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
        ciclo_numero=int(ciclo_numero or 1),
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


def calcular_total_pagado(solicitud_id: int, *, ciclo_numero: int | None = None) -> Decimal:
    total = Decimal("0.00")
    for mov in _iter_active_movimientos(solicitud_id, ciclo_numero=ciclo_numero):
        monto = _to_decimal(mov.monto)
        tipo = (mov.tipo_pago or "").strip().lower()
        if tipo in POSITIVE_TYPES and monto > Decimal("0.00"):
            total += monto
        elif tipo in NEGATIVE_TYPES and monto > Decimal("0.00"):
            total -= monto
    return total.quantize(Decimal("0.01"))


def calcular_total_abonado(solicitud_id: int, *, ciclo_numero: int | None = None) -> Decimal:
    total = Decimal("0.00")
    for mov in _iter_active_movimientos(solicitud_id, ciclo_numero=ciclo_numero):
        tipo = (mov.tipo_pago or "").strip().lower()
        if tipo != "abono":
            continue
        monto = _to_decimal(mov.monto)
        if monto > Decimal("0.00"):
            total += monto
    return total.quantize(Decimal("0.01"))


def calcular_total_pagado_cliente(cliente_id: int) -> Decimal:
    total = Decimal("0.00")
    movimientos = (
        PagoSolicitud.query
        .filter(PagoSolicitud.cliente_id == int(cliente_id), PagoSolicitud.anulado_at.is_(None))
        .all()
    )
    for mov in movimientos:
        monto = _to_decimal(mov.monto)
        if monto <= Decimal("0.00"):
            continue
        tipo = (mov.tipo_pago or "").strip().lower()
        if tipo in POSITIVE_TYPES:
            total += monto
        elif tipo in NEGATIVE_TYPES:
            total -= monto
    return total.quantize(Decimal("0.01"))


def calcular_saldo_pendiente(solicitud) -> Decimal:
    summary = get_payment_summary(solicitud)
    return Decimal(summary["saldo_pendiente"]).quantize(Decimal("0.01"))


def get_remaining_balance(solicitud) -> Decimal:
    return calcular_saldo_pendiente(solicitud)


def get_payment_summary(solicitud) -> dict[str, Decimal | str | int | bool]:
    cycle = get_current_payment_cycle(solicitud)
    solicitud_id = int(getattr(solicitud, "id", 0) or 0)
    cycle_num = int(cycle["numero_ciclo"])
    precio_plan = Decimal(cycle["precio_total_requerido"])
    abono_requerido = Decimal(cycle["abono_requerido"])
    total_pagado = calcular_total_pagado(solicitud_id, ciclo_numero=cycle_num)
    total_abonado = calcular_total_abonado(solicitud_id, ciclo_numero=cycle_num)
    legacy_abono = Decimal("0.00")
    legacy_abono_fallback = False
    if cycle_num == 1:
        legacy_abono = _legacy_abono_value(solicitud)
        if legacy_abono > Decimal("0.00"):
            legacy_abono_usable = min(legacy_abono, abono_requerido).quantize(Decimal("0.01"))
            if total_abonado < legacy_abono_usable:
                legacy_diff = (legacy_abono_usable - total_abonado).quantize(Decimal("0.01"))
                total_abonado = (total_abonado + legacy_diff).quantize(Decimal("0.01"))
                total_pagado = (total_pagado + legacy_diff).quantize(Decimal("0.01"))
                if total_pagado > precio_plan:
                    total_pagado = precio_plan
                legacy_abono_fallback = True
    saldo_pendiente = (precio_plan - total_pagado).quantize(Decimal("0.01"))
    if saldo_pendiente < Decimal("0.00"):
        saldo_pendiente = Decimal("0.00")
    abono_pagado = min(total_abonado, abono_requerido).quantize(Decimal("0.01"))

    return {
        "numero_ciclo": cycle_num,
        "precio_plan": precio_plan,
        "abono_requerido": abono_requerido,
        "abono_pagado": abono_pagado,
        "total_pagado": total_pagado,
        "total_abonado": total_abonado,
        "saldo_pendiente": saldo_pendiente,
        "saldo_restante": saldo_pendiente,
        "plan_norm": str(cycle["plan"]),
        "legacy_abono_fallback": legacy_abono_fallback,
        "legacy_abono": legacy_abono,
        "ciclo_estado": str(cycle["estado_pago"]),
    }


def sync_solicitud_payment_cache(solicitud) -> Decimal:
    summary = get_payment_summary(solicitud)
    total_pagado = Decimal(summary["total_pagado"])
    solicitud.monto_pagado = format_money(total_pagado)
    return total_pagado


def apply_payment_state_from_summary(solicitud, summary: dict[str, Decimal | str | int | bool] | None = None) -> str:
    summary_data = summary or get_payment_summary(solicitud)
    total_pagado = Decimal(summary_data["total_pagado"])
    precio_plan = Decimal(summary_data["precio_plan"])
    saldo_pendiente = Decimal(summary_data["saldo_pendiente"])
    estado_actual = (getattr(solicitud, "estado", "") or "").strip().lower()

    solicitud.monto_pagado = format_money(total_pagado)
    if saldo_pendiente <= Decimal("0.00") and precio_plan > Decimal("0.00"):
        solicitud.payment_cycle_estado = "pagado"
        if getattr(solicitud, "payment_cycle_closed_at", None) is None:
            solicitud.payment_cycle_closed_at = utc_now_naive()
        solicitud.estado = "pagada"
        return "pagada"

    if saldo_pendiente > Decimal("0.00"):
        solicitud.payment_cycle_estado = "parcial" if total_pagado > Decimal("0.00") else "pendiente"
        solicitud.payment_cycle_closed_at = None
        solicitud.estado = "espera_pago" if total_pagado > Decimal("0.00") else "proceso"
        return solicitud.estado

    solicitud.payment_cycle_estado = "pendiente"
    solicitud.payment_cycle_closed_at = None
    if estado_actual in {"proceso", "activa", "reemplazo", "cancelada", "espera_pago", "pagada"}:
        solicitud.estado = estado_actual
        return estado_actual
    solicitud.estado = "proceso"
    return "proceso"


def recalcular_estado_pago_solicitud(solicitud) -> str:
    return apply_payment_state_from_summary(solicitud)


def sync_cycle_plan_if_no_payments(solicitud, *, motivo: str = "plan_update") -> bool:
    summary = get_payment_summary(solicitud)
    if Decimal(summary["total_pagado"]) > Decimal("0.00"):
        return False
    plan_norm = normalize_plan(getattr(solicitud, "tipo_plan", None))
    solicitud.payment_cycle_plan = plan_norm
    solicitud.payment_cycle_precio_total = get_plan_price(plan_norm)
    solicitud.payment_cycle_abono_requerido = get_required_deposit(plan_norm)
    solicitud.payment_cycle_motivo_apertura = motivo
    return True


def ensure_reactivation_cycle(solicitud, *, motivo: str) -> bool:
    summary = get_payment_summary(solicitud)
    if Decimal(summary["saldo_pendiente"]) <= Decimal("0.00"):
        open_new_payment_cycle(solicitud, motivo=motivo, force=True)
        return True
    return False
