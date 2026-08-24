# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from config_app import db
from models import Candidata, StaffAuditLog
from services.payment_rules import format_money
from core.services.date_utils import parse_date
from utils.audit_entity import log_candidata_action
from utils.audit_logger import snapshot_model_fields
from utils.timezone import utc_now_naive


@dataclass
class CandidateFinanceResult:
    ok: bool
    message: str
    changes: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    status_code: int = 200
    error_code: str | None = None


FINANCE_AUDIT_ACTIONS = {
    "CANDIDATA_FINANCE_CONFIGURED",
    "CANDIDATA_FINANCE_PAYMENT_REGISTERED",
}


def _clean_text(value: Any, max_len: int | None = None) -> str:
    text = str(value or "").strip()
    if max_len is not None:
        text = text[:max_len]
    return text


def _parse_decimal_value(raw: Any) -> Decimal | None:
    if isinstance(raw, Decimal):
        return raw.quantize(Decimal("0.01"))
    text = _clean_text(raw)
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,-")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except Exception:
        return None


def _parse_date_value(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    text = _clean_text(raw)
    if not text:
        return None
    for parser in (parse_date,):
        try:
            parsed = parser(text)
        except Exception:
            parsed = None
        if parsed:
            return parsed
    return None


def _candidate_finance_base_due(candidata: Candidata) -> Decimal | None:
    if getattr(candidata, "monto_total", None) is None:
        return None
    try:
        base = Decimal(str(candidata.monto_total))
    except Exception:
        return None
    if base <= Decimal("0.00"):
        return None
    return (base * Decimal("0.25")).quantize(Decimal("0.01"))


def _candidate_finance_balance(candidata: Candidata) -> Decimal | None:
    if getattr(candidata, "monto_total", None) is None:
        return None
    if getattr(candidata, "porciento", None) is None:
        return None
    try:
        balance = Decimal(str(candidata.porciento))
    except Exception:
        return None
    return balance.quantize(Decimal("0.01"))


def _history_actor(log: StaffAuditLog) -> str:
    username = str(getattr(getattr(log, "actor_user", None), "username", "") or "").strip()
    if username:
        return username
    role = str(getattr(log, "actor_role", "") or "").strip()
    return role or "staff"


def _candidate_audit_entity_ids(candidata: Candidata) -> list[str]:
    ids: list[str] = []
    for raw in (getattr(candidata, "id", None), getattr(candidata, "fila", None)):
        text = str(raw or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids or ["0"]


def build_candidate_finance_snapshot(
    candidata: Candidata,
    *,
    audit_logs: list[StaffAuditLog] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    due = _candidate_finance_base_due(candidata)
    balance = _candidate_finance_balance(candidata)
    if due is None or balance is None:
        estado = "Sin cálculo de porciento"
        pagado = None
        pendiente = None
    else:
        pendiente = balance.quantize(Decimal("0.01"))
        pagado = max((due - balance).quantize(Decimal("0.01")), Decimal("0.00"))
        estado = "Pagado" if balance <= Decimal("0.00") else "Pendiente"

    logs = list(audit_logs or [])
    if not logs:
        try:
            entity_ids = _candidate_audit_entity_ids(candidata)
            logs = (
                StaffAuditLog.query.filter(
                    StaffAuditLog.entity_type.in_(["candidata", "Candidata"]),
                    StaffAuditLog.entity_id.in_(entity_ids),
                    StaffAuditLog.action_type.in_(tuple(FINANCE_AUDIT_ACTIONS)),
                )
                .order_by(StaffAuditLog.created_at.desc(), StaffAuditLog.id.desc())
                .limit(max(1, int(limit)))
                .all()
            )
        except Exception:
            logs = []

    logs = [
        log
        for log in logs
        if str(getattr(log, "action_type", "") or "").strip().upper() in FINANCE_AUDIT_ACTIONS
    ]

    items: list[dict[str, Any]] = []
    for log in logs[: max(1, int(limit))]:
        metadata = dict(getattr(log, "metadata_json", {}) or {})
        monto = metadata.get("monto") or metadata.get("monto_pagado") or ""
        fecha = metadata.get("fecha") or metadata.get("fecha_pago") or ""
        items.append(
            {
                "fecha": str(getattr(log, "created_at", None) or fecha or ""),
                "monto": format_money(monto) if monto else "",
                "metodo": _clean_text(metadata.get("metodo_pago") or metadata.get("metodo") or ""),
                "actor": _history_actor(log),
                "detalle": _clean_text(metadata.get("detalle") or metadata.get("nota") or metadata.get("calificacion") or getattr(log, "summary", "")),
            }
        )

    ultimo_pago = items[0]["fecha"] if items else ""

    return {
        "state": estado,
        "monto_total": format_money(getattr(candidata, "monto_total", None)) if getattr(candidata, "monto_total", None) is not None else "",
        "monto_total_value": getattr(candidata, "monto_total", None),
        "monto_base": format_money(due) if due is not None else "",
        "pagado": format_money(pagado) if pagado is not None else "",
        "pendiente": format_money(pendiente) if pendiente is not None else "",
        "saldo_actual": format_money(balance) if balance is not None else "",
        "ultimo_pago": ultimo_pago,
        "configurado": due is not None and balance is not None,
        "history": items,
    }


def configure_candidate_finance(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    actor: str,
    now_fn=utc_now_naive,
) -> CandidateFinanceResult:
    fecha_pago = _parse_date_value(data.get("fecha_pago") or data.get("fecha"))
    fecha_inicio = _parse_date_value(data.get("fecha_inicio") or data.get("inicio"))
    monto_total = _parse_decimal_value(data.get("monto_total"))

    errors: dict[str, str] = {}
    if fecha_pago is None:
        errors["fecha_pago"] = "Fecha inválida."
    if fecha_inicio is None:
        errors["fecha_inicio"] = "Fecha inválida."
    if monto_total is None:
        errors["monto_total"] = "Monto inválido."
    elif monto_total <= Decimal("0.00"):
        errors["monto_total"] = "El monto debe ser mayor que 0."
    if errors:
        return CandidateFinanceResult(False, "Datos incompletos o inválidos.", errors=errors, status_code=400, error_code="validation_error")

    due = (monto_total * Decimal("0.25")).quantize(Decimal("0.01"))
    before = snapshot_model_fields(candidata, ["monto_total", "porciento", "fecha_de_pago", "inicio"])

    candidata.monto_total = monto_total
    candidata.porciento = due
    candidata.fecha_de_pago = fecha_pago
    candidata.inicio = fecha_inicio

    log_candidata_action(
        action_type="CANDIDATA_FINANCE_CONFIGURED",
        candidata=candidata,
        summary=f"Configuró porciento para {candidata.fila}",
        metadata={
            "actor": actor,
            "monto_total": format_money(monto_total),
            "porciento": format_money(due),
            "fecha_pago": fecha_pago.isoformat(),
            "fecha_inicio": fecha_inicio.isoformat(),
            "now": now_fn().isoformat() if callable(now_fn) else "",
        },
        changes={
            "monto_total": {"from": before.get("monto_total"), "to": candidata.monto_total},
            "porciento": {"from": before.get("porciento"), "to": candidata.porciento},
            "fecha_de_pago": {"from": before.get("fecha_de_pago"), "to": candidata.fecha_de_pago},
            "inicio": {"from": before.get("inicio"), "to": candidata.inicio},
        },
        success=True,
    )

    return CandidateFinanceResult(
        True,
        "Porciento configurado correctamente.",
        changes={
            "monto_total": format_money(monto_total),
            "porciento": format_money(due),
            "fecha_pago": fecha_pago.isoformat(),
            "fecha_inicio": fecha_inicio.isoformat(),
        },
    )


def register_candidate_payment(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    actor: str,
    now_fn=utc_now_naive,
) -> CandidateFinanceResult:
    monto_pagado = _parse_decimal_value(data.get("monto_pagado") or data.get("monto"))
    calificacion = _clean_text(data.get("calificacion") or data.get("nota"), 200)
    metodo_pago = _clean_text(data.get("metodo_pago"), 50)
    if monto_pagado is None:
        return CandidateFinanceResult(False, "Monto inválido.", errors={"monto_pagado": "Monto inválido."}, status_code=400, error_code="validation_error")
    if monto_pagado <= Decimal("0.00"):
        return CandidateFinanceResult(False, "El monto debe ser mayor que 0.", errors={"monto_pagado": "El monto debe ser mayor que 0."}, status_code=400, error_code="validation_error")

    current_balance = _candidate_finance_balance(candidata)
    if current_balance is None:
        return CandidateFinanceResult(False, "No hay cálculo de porciento configurado.", errors={"porciento": "Sin cálculo de porciento."}, status_code=409, error_code="no_finance_config")

    if monto_pagado > current_balance:
        return CandidateFinanceResult(
            False,
            "El pago supera el saldo pendiente.",
            errors={"monto_pagado": "El pago supera el saldo pendiente."},
            status_code=400,
            error_code="overpayment",
        )

    before = snapshot_model_fields(candidata, ["porciento", "fecha_de_pago", "calificacion"])
    new_balance = (current_balance - monto_pagado).quantize(Decimal("0.01"))
    candidata.porciento = new_balance
    candidata.fecha_de_pago = now_fn().date() if callable(now_fn) else utc_now_naive().date()
    if calificacion:
        candidata.calificacion = calificacion

    log_candidata_action(
        action_type="CANDIDATA_FINANCE_PAYMENT_REGISTERED",
        candidata=candidata,
        summary=f"Registró pago de candidata {candidata.fila}",
        metadata={
            "actor": actor,
            "monto": format_money(monto_pagado),
            "saldo_anterior": format_money(current_balance),
            "saldo_nuevo": format_money(new_balance),
            "metodo_pago": metodo_pago,
            "calificacion": calificacion,
            "fecha_pago": candidata.fecha_de_pago.isoformat() if candidata.fecha_de_pago else "",
        },
        changes={
            "porciento": {"from": before.get("porciento"), "to": candidata.porciento},
            "fecha_de_pago": {"from": before.get("fecha_de_pago"), "to": candidata.fecha_de_pago},
            "calificacion": {"from": before.get("calificacion"), "to": candidata.calificacion},
        },
        success=True,
    )

    return CandidateFinanceResult(
        True,
        "Pago registrado correctamente.",
        changes={
            "monto_pagado": format_money(monto_pagado),
            "saldo_pendiente": format_money(new_balance),
            "estado": "Pagado" if new_balance <= Decimal("0.00") else "Pendiente",
        },
    )
