from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from utils.timezone import rd_today


def parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except Exception:
        return None


def parse_decimal(s: str) -> Optional[Decimal]:
    try:
        return Decimal((s or "").replace(",", "."))
    except Exception:
        return None


def get_date_bounds(period: str, date_str: Optional[str] = None):
    """Devuelve (start_dt, end_dt)."""
    hoy = rd_today()
    if period == "day":
        return hoy - timedelta(days=1), hoy
    if period == "week":
        return hoy - timedelta(days=7), hoy
    if period == "month":
        return hoy - timedelta(days=30), hoy
    if period == "date" and date_str:
        d = date.fromisoformat(date_str)
        return d, d
    return None, None


def get_start_date(period: str, date_str: Optional[str] = None):
    start, _ = get_date_bounds(period, date_str)
    return start
