# -*- coding: utf-8 -*-

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from flask import session

from app import app as flask_app
from core.services.cache_keys import _cache_key_with_role
from core.services.candidatas_shared import get_candidata_by_id
from core.services.date_utils import get_date_bounds, get_start_date, parse_date, parse_decimal


def test_parse_date_y_parse_decimal_contratos_basicos():
    assert parse_date("2026-03-27") == date(2026, 3, 27)
    assert parse_date("27-03-2026") is None

    assert parse_decimal("100.50") == Decimal("100.50")
    assert parse_decimal("100,50") == Decimal("100.50")
    assert parse_decimal("abc") is None


def test_get_date_bounds_y_get_start_date_con_periodos_soportados():
    with patch("core.services.date_utils.rd_today", return_value=date(2026, 3, 27)):
        assert get_date_bounds("day") == (date(2026, 3, 26), date(2026, 3, 27))
        assert get_date_bounds("week") == (date(2026, 3, 20), date(2026, 3, 27))
        assert get_date_bounds("month") == (date(2026, 2, 25), date(2026, 3, 27))
        assert get_date_bounds("date", "2026-03-01") == (date(2026, 3, 1), date(2026, 3, 1))
        assert get_date_bounds("all") == (None, None)
        assert get_start_date("week") == date(2026, 3, 20)


def test_cache_key_with_role_aisla_por_rol_y_querystring():
    with flask_app.test_request_context("/reporte_pagos?page=2"):
        session["role"] = "admin"
        assert _cache_key_with_role("reporte_pagos") == "reporte_pagos:admin:/reporte_pagos?page=2"

    with flask_app.test_request_context("/reporte_pagos"):
        assert _cache_key_with_role("reporte_pagos") == "reporte_pagos:anon:/reporte_pagos?"


def test_get_candidata_by_id_valida_input_y_consulta_por_pk():
    with patch("core.services.candidatas_shared.db.session.get", return_value="OBJ") as get_mock:
        assert get_candidata_by_id("22") == "OBJ"
        get_mock.assert_called_once()

    with patch("core.services.candidatas_shared.db.session.get") as get_mock:
        assert get_candidata_by_id("x22") is None
        assert get_candidata_by_id("") is None
        get_mock.assert_not_called()
