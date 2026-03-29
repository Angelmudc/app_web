# -*- coding: utf-8 -*-

from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from core.services.db_retry import _retry_query


def _op_err():
    return OperationalError("SELECT 1", {}, Exception("db down"))


def test_retry_query_reintenta_y_retorna_valor_exitoso():
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _op_err()
        return "ok"

    with patch("core.services.db_retry.db.session.rollback") as rollback_mock:
        assert _retry_query(_fn, retries=2, swallow=False) == "ok"

    assert calls["n"] == 3
    assert rollback_mock.call_count == 2


def test_retry_query_swallow_true_retorna_none_tras_agotar_reintentos():
    def _fn():
        raise _op_err()

    with patch("core.services.db_retry.db.session.rollback") as rollback_mock:
        assert _retry_query(_fn, retries=1, swallow=True) is None

    assert rollback_mock.call_count == 2


def test_retry_query_swallow_false_propaga_ultimo_error():
    def _fn():
        raise _op_err()

    with patch("core.services.db_retry.db.session.rollback") as rollback_mock:
        with pytest.raises(OperationalError):
            _retry_query(_fn, retries=1, swallow=False)

    assert rollback_mock.call_count == 2


def test_retry_query_no_traga_errores_no_db():
    with patch("core.services.db_retry.db.session.rollback") as rollback_mock:
        with pytest.raises(ValueError):
            _retry_query(lambda: (_ for _ in ()).throw(ValueError("x")), retries=3, swallow=True)

    rollback_mock.assert_not_called()
