from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata

PLAN_PRICES = {
    "basico": Decimal("3500.00"),
    "premium": Decimal("5000.00"),
    "vip": Decimal("8000.00"),
}

PLAN_LABELS = {
    "basico": "Básico",
    "premium": "Premium",
    "vip": "VIP",
}

DEFAULT_PLAN_PRICE = Decimal("3500.00")


def normalize_plan(plan: str | None) -> str:
    txt = str(plan or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-z]", "", txt)
    if txt.startswith("basico"):
        return "basico"
    if txt.startswith("premium"):
        return "premium"
    if txt.startswith("vip"):
        return "vip"
    return txt


def is_valid_plan(plan: str | None) -> bool:
    return normalize_plan(plan) in PLAN_PRICES


def get_plan_price(plan: str | None) -> Decimal:
    code = normalize_plan(plan)
    return PLAN_PRICES.get(code, DEFAULT_PLAN_PRICE)


def get_required_deposit(plan: str | None) -> Decimal:
    return (get_plan_price(plan) * Decimal("0.50")).quantize(Decimal("0.01"))


def get_plan_label(plan: str | None) -> str:
    code = normalize_plan(plan)
    return PLAN_LABELS.get(code, str(plan or "").strip())


def get_plan_choices() -> list[tuple[str, str]]:
    return [(code, PLAN_LABELS[code]) for code in PLAN_PRICES]


def format_money(value: Decimal | str | int | float | None) -> str:
    if value is None:
        amount = Decimal("0.00")
    elif isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip()
        if not text:
            amount = Decimal("0.00")
        else:
            cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,-")
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            try:
                amount = Decimal(cleaned)
            except (InvalidOperation, ValueError):
                amount = Decimal("0.00")
    return str(amount.quantize(Decimal("0.01")))
