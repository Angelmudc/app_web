# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from core.services.search import (
    _prioritize_candidata_result,
    apply_search_to_candidata_query,
    normalize_query_text,
    search_candidatas_limited,
)


def test_normalize_query_text_conserva_semantica_actual():
    assert normalize_query_text("  María, Pérez. ") == "maria perez"
    assert normalize_query_text("A\nB\tC") == "a b c"
    assert normalize_query_text("") == ""


class _FakeQuery:
    def __init__(self):
        self.filter_calls = 0
        self.order_seen = None
        self.limit_seen = None

    def filter(self, *_args, **_kwargs):
        self.filter_calls += 1
        return self

    def options(self, *_args, **_kwargs):
        return self

    def order_by(self, expr):
        self.order_seen = expr
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        return [SimpleNamespace(fila=1), SimpleNamespace(fila=2)]


def test_apply_search_to_query_con_codigo_estricto_aplica_doble_filter():
    q = _FakeQuery()
    with patch("core.services.search.build_flexible_search_filters", return_value=(object(), [])):
        out = apply_search_to_candidata_query(q, "CAN-123456")
    assert out is q
    assert q.filter_calls == 2


def test_apply_search_to_query_sin_codigo_aplica_filter_or():
    q = _FakeQuery()
    with patch("core.services.search.build_flexible_search_filters", return_value=(None, [object(), object()])), \
         patch("core.services.search.or_", return_value="OR_EXPR"):
        out = apply_search_to_candidata_query(q, "ana")
    assert out is q
    assert q.filter_calls == 1


def test_search_candidatas_limited_respeta_limit_y_orden_id_desc():
    q = _FakeQuery()

    class _Expr:
        def desc(self):
            return "FILA_DESC"

        def asc(self):
            return "NOMBRE_ASC"

    fake_model = SimpleNamespace(fila=_Expr(), nombre_completo=_Expr())

    with patch("core.services.search.Candidata", new=fake_model), \
         patch("core.services.search.apply_search_to_candidata_query", side_effect=lambda base, _q: base), \
         patch("core.services.search.current_app", new=SimpleNamespace(logger=SimpleNamespace(info=lambda *_a, **_k: None))):
        rows = search_candidatas_limited("demo", limit=999, base_query=q, order_mode="id_desc", log_label="x")

    assert len(rows) == 2
    assert q.limit_seen == 500
    assert q.order_seen == "FILA_DESC"


def test_prioritize_candidata_result_mueve_objetivo_al_inicio():
    a = SimpleNamespace(fila=10)
    b = SimpleNamespace(fila=20)
    c = SimpleNamespace(fila=30)
    out = _prioritize_candidata_result([a, b, c], 30)
    assert [x.fila for x in out] == [30, 10, 20]
