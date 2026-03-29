# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app
from config_app import cache


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def _login_admin(client):
    return client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)


class _Expr:
    def label(self, *_args, **_kwargs):
        return self

    def ilike(self, *_args, **_kwargs):
        return self

    def asc(self):
        return self

    def desc(self):
        return self

    def nullsfirst(self):
        return self

    def __eq__(self, _other):
        return self

    def __ge__(self, _other):
        return self

    def __le__(self, _other):
        return self

    def __lt__(self, _other):
        return self


class _FuncFake:
    def count(self, *_args, **_kwargs):
        return _Expr()

    def max(self, *_args, **_kwargs):
        return _Expr()

    def date_trunc(self, *_args, **_kwargs):
        return _Expr()

    def now(self):
        return "NOW"


class _AggQuery:
    def group_by(self, *_args, **_kwargs):
        return self

    def subquery(self):
        return SimpleNamespace(c=SimpleNamespace(cid=_Expr(), num_calls=_Expr(), last_call=_Expr()))


class _BaseQuery:
    def __init__(self, paginate_value):
        self.paginate_value = paginate_value
        self.paginate_calls = 0

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def paginate(self, *_args, **_kwargs):
        self.paginate_calls += 1
        return self.paginate_value


class _AllQuery:
    def __init__(self, rows):
        self.rows = rows

    def group_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)


class _CallsPeriodQuery:
    def __init__(self, rows):
        self.rows = rows
        self.limit_seen = None

    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        return list(self.rows)


def test_llamadas_endpoints_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("listado_llamadas_candidatas") == "/candidatas/llamadas"
            assert url_for("candidatas_routes.listado_llamadas_candidatas") == "/candidatas/llamadas"
            assert url_for("registrar_llamada_candidata", fila=9) == "/candidatas/9/llamar"
            assert url_for("candidatas_routes.registrar_llamada_candidata", fila=9) == "/candidatas/9/llamar"
            assert url_for("reporte_llamadas_candidatas") == "/candidatas/llamadas/reporte"
            assert url_for("candidatas_routes.reporte_llamadas_candidatas") == "/candidatas/llamadas/reporte"

    assert flask_app.view_functions["listado_llamadas_candidatas"].__module__ == "core.handlers.llamadas_candidatas_handlers"
    assert flask_app.view_functions["registrar_llamada_candidata"].__module__ == "core.handlers.llamadas_candidatas_handlers"
    assert flask_app.view_functions["reporte_llamadas_candidatas"].__module__ == "core.handlers.llamadas_candidatas_handlers"


def test_listado_llamadas_render_basico_contrato_intacto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    sentinel_pag = SimpleNamespace(items=[SimpleNamespace(fila=1)], page=2)
    base_q = _BaseQuery(sentinel_pag)
    captured = {}

    class _SessionStub:
        def __init__(self):
            self.calls = 0

        def query(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _AggQuery()
            return base_q

    fake_db = SimpleNamespace(session=_SessionStub())
    fake_candidata = SimpleNamespace(
        fila=_Expr(),
        nombre_completo=_Expr(),
        codigo=_Expr(),
        numero_telefono=_Expr(),
        marca_temporal=_Expr(),
        cedula=_Expr(),
        estado=_Expr(),
    )
    fake_llamada = SimpleNamespace(candidata_id=_Expr(), id=_Expr(), fecha_llamada=_Expr())

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.llamadas_candidatas_handlers.db", new=fake_db), \
         patch("core.handlers.llamadas_candidatas_handlers.Candidata", new=fake_candidata), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidata", new=fake_llamada), \
         patch("core.handlers.llamadas_candidatas_handlers.func", new=_FuncFake()), \
         patch("core.handlers.llamadas_candidatas_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.llamadas_candidatas_handlers.cast", side_effect=lambda *a, **k: _Expr()), \
         patch("core.handlers.llamadas_candidatas_handlers.get_date_bounds", return_value=(None, None)), \
         patch("core.handlers.llamadas_candidatas_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/candidatas/llamadas?page=2&period=all&q=ana", follow_redirects=False)

    assert resp.status_code == 200
    assert base_q.paginate_calls == 3
    assert captured["template"] == "llamadas_candidatas.html"
    assert captured["ctx"]["q"] == "ana"
    assert captured["ctx"]["period"] == "all"
    assert captured["ctx"]["en_proceso"] is sentinel_pag
    assert captured["ctx"]["en_inscripcion"] is sentinel_pag
    assert captured["ctx"]["lista_trabajar"] is sentinel_pag


def test_registrar_llamada_persistencia_y_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    added = {}
    fake_session = SimpleNamespace(
        add=lambda obj: added.setdefault("obj", obj),
        commit=lambda: None,
        rollback=lambda: None,
    )
    fake_db = SimpleNamespace(session=fake_session)
    candidata = SimpleNamespace(fila=15, nombre_completo="Ana Demo")
    fake_query = SimpleNamespace(get_or_404=lambda _fila: candidata)
    fake_candidata_model = SimpleNamespace(query=fake_query)
    fake_form = SimpleNamespace(
        validate_on_submit=lambda: True,
        duracion_minutos=SimpleNamespace(data=2),
        resultado=SimpleNamespace(data="contestada"),
        notas=SimpleNamespace(data="seguimiento"),
    )

    with patch("core.handlers.llamadas_candidatas_handlers.db", new=fake_db), \
         patch("core.handlers.llamadas_candidatas_handlers.Candidata", new=fake_candidata_model), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidataForm", return_value=fake_form), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidata", side_effect=lambda **kw: SimpleNamespace(**kw)), \
         patch("core.handlers.llamadas_candidatas_handlers.func", new=_FuncFake()), \
         patch("core.handlers.llamadas_candidatas_handlers.utc_now_naive", return_value="NOWUTC"):
        resp = client.post("/candidatas/15/llamar", data={"resultado": "contestada"}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert "/candidatas/llamadas" in (resp.headers.get("Location") or "")
    assert added["obj"].candidata_id == 15
    assert added["obj"].duracion_segundos == 120
    assert added["obj"].resultado == "contestada"
    assert added["obj"].notas == "seguimiento"


def test_registrar_llamada_fallback_rollback_y_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    rollback_calls = {"n": 0}

    def _rollback():
        rollback_calls["n"] += 1

    fake_session = SimpleNamespace(
        add=lambda _obj: None,
        commit=lambda: (_ for _ in ()).throw(RuntimeError("db fail")),
        rollback=_rollback,
    )
    fake_db = SimpleNamespace(session=fake_session)
    candidata = SimpleNamespace(fila=21, nombre_completo="Bea Demo")
    fake_query = SimpleNamespace(get_or_404=lambda _fila: candidata)
    fake_candidata_model = SimpleNamespace(query=fake_query)
    fake_form = SimpleNamespace(
        validate_on_submit=lambda: True,
        duracion_minutos=SimpleNamespace(data=1),
        resultado=SimpleNamespace(data="sin respuesta"),
        notas=SimpleNamespace(data=""),
    )

    with patch("core.handlers.llamadas_candidatas_handlers.db", new=fake_db), \
         patch("core.handlers.llamadas_candidatas_handlers.Candidata", new=fake_candidata_model), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidataForm", return_value=fake_form), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidata", side_effect=lambda **kw: SimpleNamespace(**kw)), \
         patch("core.handlers.llamadas_candidatas_handlers.func", new=_FuncFake()), \
         patch("core.handlers.llamadas_candidatas_handlers.utc_now_naive", return_value="NOWUTC"):
        resp = client.post("/candidatas/21/llamar", data={"resultado": "sin respuesta"}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert "/candidatas/llamadas" in (resp.headers.get("Location") or "")
    assert rollback_calls["n"] == 1


def test_reporte_llamadas_render_basico_y_metricas():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)
    cache.clear()

    sentinel_pag = SimpleNamespace(items=[SimpleNamespace(fila=7)], page=1)
    base_q = _BaseQuery(sentinel_pag)
    calls_period_q = _CallsPeriodQuery([SimpleNamespace(id=1)])
    captured = {}

    queries = [
        _AggQuery(),
        base_q,
        _AllQuery([SimpleNamespace(cnt=3), SimpleNamespace(cnt=1)]),
        calls_period_q,
        _AllQuery([SimpleNamespace(periodo="2026-03-01", cnt=1)]),
        _AllQuery([SimpleNamespace(periodo="2026-W12", cnt=2)]),
        _AllQuery([SimpleNamespace(periodo="2026-03", cnt=4)]),
    ]

    class _SessionStub:
        def __init__(self, queue):
            self.queue = list(queue)

        def query(self, *_args, **_kwargs):
            assert self.queue, "query() inesperado en reporte"
            return self.queue.pop(0)

    fake_db = SimpleNamespace(session=_SessionStub(queries))
    fake_candidata = SimpleNamespace(
        fila=_Expr(),
        nombre_completo=_Expr(),
        codigo=_Expr(),
        numero_telefono=_Expr(),
        marca_temporal=_Expr(),
        estado=_Expr(),
    )
    fake_llamada = SimpleNamespace(candidata_id=_Expr(), id=_Expr(), fecha_llamada=_Expr())

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.llamadas_candidatas_handlers.db", new=fake_db), \
         patch("core.handlers.llamadas_candidatas_handlers.Candidata", new=fake_candidata), \
         patch("core.handlers.llamadas_candidatas_handlers.LlamadaCandidata", new=fake_llamada), \
         patch("core.handlers.llamadas_candidatas_handlers.func", new=_FuncFake()), \
         patch("core.handlers.llamadas_candidatas_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.llamadas_candidatas_handlers.cast", side_effect=lambda *a, **k: _Expr()), \
         patch("core.handlers.llamadas_candidatas_handlers.get_start_date", return_value=None), \
         patch("core.handlers.llamadas_candidatas_handlers.rd_today", return_value="2026-03-26"), \
         patch("core.handlers.llamadas_candidatas_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/candidatas/llamadas/reporte?period=week&page=1", follow_redirects=False)

    assert resp.status_code == 200
    assert base_q.paginate_calls == 3
    assert calls_period_q.limit_seen == 2500
    assert captured["template"] == "reporte_llamadas.html"
    assert captured["ctx"]["period"] == "week"
    assert captured["ctx"]["promedio"] == 2.0
    assert captured["ctx"]["estancadas_en_proceso"] is sentinel_pag
    assert captured["ctx"]["estancadas_inscripcion"] is sentinel_pag
    assert captured["ctx"]["estancadas_lista"] is sentinel_pag
