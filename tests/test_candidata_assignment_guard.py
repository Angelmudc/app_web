# -*- coding: utf-8 -*-

from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch

from app import app as flask_app
from services.candidata_assignment_guard import (
    build_solicitud_payment_eligibility_map,
    validate_candidata_assignment_context,
)


class _FakeSortableCol:
    def desc(self):
        return self


class _FakeJoinQuery:
    def __init__(self, row=None, raise_on_first=False):
        self._row = row
        self._raise_on_first = raise_on_first

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        if self._raise_on_first:
            raise RuntimeError("db-error")
        return self._row


class _FakeSolicitudQuery:
    def __init__(self, row=None):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeExpr:
    def label(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def desc(self):
        return self

    def __eq__(self, _other):
        return self


def _patch_guard(*, sc_row=None, fallback_row=None, raise_on_first=False):
    return patch.multiple(
        "services.candidata_assignment_guard",
        db=SimpleNamespace(session=SimpleNamespace(query=lambda *a, **k: _FakeJoinQuery(sc_row, raise_on_first=raise_on_first))),
        Solicitud=SimpleNamespace(query=_FakeSolicitudQuery(fallback_row), candidata_id=object(), id=_FakeSortableCol()),
        _guard_logger_warning=lambda *a, **k: None,
        _guard_logger_exception=lambda *a, **k: None,
    )


class _FakeBatchQuery:
    def __init__(self, rows):
        self._rows = rows
        self.all_calls = 0

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def all(self):
        self.all_calls += 1
        return self._rows


@contextmanager
def _patch_batch_guard(*, active_rows=None, fallback_rows=None, warning_sink=None):
    active_query = _FakeBatchQuery(active_rows or [])
    fallback_query = _FakeBatchQuery(fallback_rows or [])
    sink = warning_sink if warning_sink is not None else []

    def _warn(*_args, **kwargs):
        sink.append(kwargs)

    with patch.multiple(
            "services.candidata_assignment_guard",
            db=SimpleNamespace(session=SimpleNamespace(query=lambda *a, **k: active_query)),
            Solicitud=SimpleNamespace(
                query=fallback_query,
                candidata_id=_FakeExpr(),
                id=_FakeExpr(),
                cliente_id=_FakeExpr(),
                estado=_FakeExpr(),
            ),
            SolicitudCandidata=SimpleNamespace(
                candidata_id=_FakeExpr(),
                solicitud_id=_FakeExpr(),
                status=_FakeExpr(),
            ),
            load_only=lambda *a, **k: None,
            _guard_logger_warning=_warn,
            _guard_logger_exception=lambda *a, **k: None,
        ):
        yield active_query, fallback_query, sink


def test_guard_sin_fila_en_solicitudes_candidatas():
    with _patch_guard(sc_row=None, fallback_row=None):
        res = validate_candidata_assignment_context(candidata_id=5)
    assert res.has_active_assignment is False
    assert res.can_mark_working is False
    assert res.can_charge is False
    assert res.reason_code == "no_active_assignment"


def test_guard_con_fila_valida_en_solicitudes_candidatas():
    solicitud = SimpleNamespace(id=10, cliente_id=20, estado="activa")
    with _patch_guard(sc_row=(SimpleNamespace(id=1), solicitud), fallback_row=None):
        res = validate_candidata_assignment_context(candidata_id=5)
    assert res.has_active_assignment is True
    assert res.can_mark_working is True
    assert res.can_charge is True
    assert res.matched_by == "solicitudes_candidatas"
    assert res.solicitud_id == 10
    assert res.cliente_id == 20


def test_guard_fallback_controlado_solicitud_candidata_id():
    fallback = SimpleNamespace(id=11, cliente_id=22, estado="espera_pago")
    with _patch_guard(sc_row=None, fallback_row=fallback):
        res = validate_candidata_assignment_context(candidata_id=9)
    assert res.has_active_assignment is True
    assert res.can_mark_working is True
    assert res.can_charge is True
    assert res.reason_code == "fallback_without_solicitud_candidata"
    assert res.matched_by == "solicitud_candidata_id_fallback"


def test_guard_reemplazo_permite_operacion_no_cobro():
    solicitud = SimpleNamespace(id=12, cliente_id=23, estado="reemplazo")
    with _patch_guard(sc_row=(SimpleNamespace(id=2), solicitud), fallback_row=None):
        res = validate_candidata_assignment_context(candidata_id=3)
    assert res.has_active_assignment is True
    assert res.can_mark_working is True
    assert res.can_charge is False


def test_guard_estados_operativos_requeridos():
    for estado, expected_charge in (("pendiente_servicio", False), ("espera_pago", True), ("pagada", True), ("finalizada", False), ("cerrada", False)):
        solicitud = SimpleNamespace(id=50, cliente_id=77, estado=estado)
        with _patch_guard(sc_row=(SimpleNamespace(id=6), solicitud), fallback_row=None):
            res = validate_candidata_assignment_context(candidata_id=2)
        assert res.can_charge is expected_charge


def test_guard_query_error_devuelve_validation_error():
    with _patch_guard(sc_row=None, fallback_row=None, raise_on_first=True):
        res = validate_candidata_assignment_context(candidata_id=4)
    assert res.reason_code == "validation_error"
    assert res.can_charge is False
    assert res.can_mark_working is False


def test_batch_guard_usa_cache_y_preserva_semantica():
    solicitudes = [
        SimpleNamespace(id=10, candidata_id=4, cliente_id=20, estado="activa"),
        SimpleNamespace(id=11, candidata_id=5, cliente_id=21, estado="espera_pago"),
        SimpleNamespace(id=12, candidata_id=0, cliente_id=22, estado="activa"),
        SimpleNamespace(id=13, candidata_id=6, cliente_id=23, estado="finalizada"),
    ]
    active_rows = [SimpleNamespace(id=10, candidata_id=4, cliente_id=20, estado="activa")]
    fallback_rows = [SimpleNamespace(id=11, candidata_id=5, cliente_id=21, estado="espera_pago")]
    warnings: list[dict] = []

    with flask_app.test_request_context("/admin/clientes/20"):
        with _patch_batch_guard(active_rows=active_rows, fallback_rows=fallback_rows, warning_sink=warnings):
            first = build_solicitud_payment_eligibility_map(solicitudes)
            second = build_solicitud_payment_eligibility_map(solicitudes)

    assert first[10].reason_code == "ok"
    assert first[10].can_charge is True
    assert first[11].reason_code == "fallback_without_solicitud_candidata"
    assert first[11].can_charge is True
    assert first[12].reason_code == "invalid_candidate_id"
    assert first[13].reason_code == "no_active_assignment"
    assert second[10].reason_code == first[10].reason_code
    assert second[11].reason_code == first[11].reason_code
    assert len(warnings) == 1
    assert warnings[0]["matched_by"] == "solicitud_candidata_id_fallback"
