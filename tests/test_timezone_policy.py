# -*- coding: utf-8 -*-
from datetime import date, datetime

from utils.timezone import (
    format_rd_datetime,
    iso_utc_z,
    parse_iso_utc,
    rd_today,
    to_rd,
    to_utc_naive,
)


def test_to_rd_converts_naive_utc_to_santo_domingo():
    dt_utc_naive = datetime(2026, 3, 9, 14, 30, 0)
    dt_rd = to_rd(dt_utc_naive)
    assert dt_rd is not None
    assert dt_rd.tzinfo is not None
    assert dt_rd.hour == 10
    assert dt_rd.minute == 30


def test_to_utc_naive_from_rd_naive_input():
    # 10:30 RD == 14:30 UTC
    rd_naive = datetime(2026, 3, 9, 10, 30, 0)
    utc_naive = to_utc_naive(rd_naive, assume_rd_if_naive=True)
    assert utc_naive == datetime(2026, 3, 9, 14, 30, 0)


def test_iso_utc_z_and_parse_iso_utc_roundtrip():
    base = datetime(2026, 3, 9, 15, 1, 22)
    raw = iso_utc_z(base)
    assert raw.endswith("Z")

    parsed = parse_iso_utc(raw)
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.hour == 15


def test_format_rd_datetime_handles_date_value():
    d = date(2026, 3, 9)
    assert format_rd_datetime(d, "%d/%m/%Y") == "09/03/2026"


def test_rd_today_returns_date_instance():
    assert isinstance(rd_today(), date)
