# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

RD_TZ = ZoneInfo("America/Santo_Domingo")
UTC_TZ = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC_TZ)


def utc_now_naive() -> datetime:
    # SQLAlchemy DateTime actual en el proyecto usa naive UTC.
    return utc_now().replace(tzinfo=None)


def now_rd() -> datetime:
    return utc_now().astimezone(RD_TZ)


def now_rd_naive() -> datetime:
    return now_rd().replace(tzinfo=None)


def rd_today() -> date:
    return now_rd().date()


def ensure_utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC_TZ)
    return value.astimezone(UTC_TZ)


def ensure_rd_aware(value: datetime | None, *, assume_utc_if_naive: bool = True) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        if assume_utc_if_naive:
            value = value.replace(tzinfo=UTC_TZ)
        else:
            value = value.replace(tzinfo=RD_TZ)
    return value.astimezone(RD_TZ)


def to_rd(value: datetime | None) -> datetime | None:
    return ensure_rd_aware(value, assume_utc_if_naive=True)


def to_utc_naive(value: datetime | None, *, assume_rd_if_naive: bool = True) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=RD_TZ if assume_rd_if_naive else UTC_TZ)
    return value.astimezone(UTC_TZ).replace(tzinfo=None)


def format_rd_datetime(value: datetime | date | None, fmt: str | None = None, empty: str = "-") -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime(fmt or "%Y-%m-%d")
    dt = to_rd(value)
    if dt is None:
        return empty
    return dt.strftime(fmt or "%Y-%m-%d %H:%M")


def iso_utc_z(value: datetime | None = None, *, seconds: bool = True) -> str:
    dt = ensure_utc_aware(value) or utc_now()
    if seconds:
        dt = dt.replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str | None) -> datetime | None:
    txt = (value or "").strip()
    if not txt:
        return None
    try:
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
        else:
            dt = dt.astimezone(UTC_TZ)
        return dt
    except Exception:
        return None


def utc_timestamp(value: datetime | None = None) -> float:
    dt = ensure_utc_aware(value) or utc_now()
    return dt.timestamp()


def rd_day_range_utc_naive(ref: date | datetime | None = None) -> tuple[datetime, datetime]:
    if ref is None:
        day = rd_today()
    elif isinstance(ref, datetime):
        day = ensure_rd_aware(ref, assume_utc_if_naive=True).date()
    else:
        day = ref

    start_rd = datetime.combine(day, time.min, tzinfo=RD_TZ)
    end_rd = datetime.combine(day, time.max, tzinfo=RD_TZ)
    start_utc_naive = start_rd.astimezone(UTC_TZ).replace(tzinfo=None)
    end_utc_naive = end_rd.astimezone(UTC_TZ).replace(tzinfo=None)
    return start_utc_naive, end_utc_naive
