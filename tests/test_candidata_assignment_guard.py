# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from services.candidata_assignment_guard import validate_candidata_assignment_context


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


def _patch_guard(*, sc_row=None, fallback_row=None, raise_on_first=False):
    return patch.multiple(
        "services.candidata_assignment_guard",
        db=SimpleNamespace(session=SimpleNamespace(query=lambda *a, **k: _FakeJoinQuery(sc_row, raise_on_first=raise_on_first))),
        Solicitud=SimpleNamespace(query=_FakeSolicitudQuery(fallback_row), candidata_id=object(), id=_FakeSortableCol()),
        _guard_logger_warning=lambda *a, **k: None,
        _guard_logger_exception=lambda *a, **k: None,
    )


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
