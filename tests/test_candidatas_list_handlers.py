# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app
from config_app import cache


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def asc(self):
        return self


class _Pagination:
    def __init__(self, items):
        self.items = items


class _ListQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.page_seen = None
        self.per_page_seen = None
        self.error_out_seen = None

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def paginate(self, page, per_page, error_out):
        self.page_seen = page
        self.per_page_seen = per_page
        self.error_out_seen = error_out
        return _Pagination(list(self.rows))


class _DbQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_seen = None

    def options(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        return list(self.rows)


def test_candidatas_list_endpoints_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("list_candidatas") == "/candidatas"
            assert url_for("candidatas_routes.list_candidatas") == "/candidatas"
            assert url_for("list_candidatas_db") == "/candidatas_db"
            assert url_for("candidatas_routes.list_candidatas_db") == "/candidatas_db"

    assert flask_app.view_functions["list_candidatas"].__module__ == "core.handlers.candidatas_list_handlers"
    assert flask_app.view_functions["list_candidatas_db"].__module__ == "core.handlers.candidatas_list_handlers"


def test_list_candidatas_render_filtros_paginacion_y_orden():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    rows = [SimpleNamespace(fila=2, nombre_completo="Lista")]
    query = _ListQuery(rows)
    fake_model = SimpleNamespace(query=query, estado=_Expr(), nombre_completo=_Expr())

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.candidatas_list_handlers.legacy_h.Candidata", new=fake_model), \
         patch("core.handlers.candidatas_list_handlers.legacy_h.candidatas_activas_filter", return_value=True), \
         patch("core.handlers.candidatas_list_handlers.apply_search_to_candidata_query", side_effect=lambda base, _q: base) as search_mock, \
         patch("core.handlers.candidatas_list_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/candidatas?q=ana&page=3&per_page=40", follow_redirects=False)

    assert resp.status_code == 200
    search_mock.assert_called_once()
    assert query.page_seen == 3
    assert query.per_page_seen == 40
    assert query.error_out_seen is False
    assert captured["template"] == "candidatas.html"
    assert captured["ctx"]["query"] == "ana"
    assert captured["ctx"]["candidatas"] == rows
    assert captured["ctx"]["page"] == 3
    assert captured["ctx"]["per_page"] == 40


def test_list_candidatas_db_json_contract():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)
    cache.clear()

    row = SimpleNamespace(
        fila=7,
        marca_temporal="2026-01-02",
        nombre_completo="Ana Demo",
        edad="31",
        numero_telefono="8090000000",
        direccion_completa="Santiago",
        modalidad_trabajo_preferida="Dormida",
        cedula="001-0000000-1",
        codigo="C-007",
        disponibilidad_inicio="inmediata",
        trabaja_con_ninos=True,
        trabaja_con_mascotas=False,
        puede_dormir_fuera=True,
        sueldo_esperado="30000",
        motivacion_trabajo="Necesita empleo",
    )
    query = _DbQuery([row])
    fake_model = SimpleNamespace(
        query=query,
        fila=_Expr(),
        marca_temporal=_Expr(),
        nombre_completo=_Expr(),
        edad=_Expr(),
        numero_telefono=_Expr(),
        direccion_completa=_Expr(),
        modalidad_trabajo_preferida=_Expr(),
        cedula=_Expr(),
        codigo=_Expr(),
        disponibilidad_inicio=_Expr(),
        trabaja_con_ninos=_Expr(),
        trabaja_con_mascotas=_Expr(),
        puede_dormir_fuera=_Expr(),
        sueldo_esperado=_Expr(),
        motivacion_trabajo=_Expr(),
    )

    with patch("core.handlers.candidatas_list_handlers.legacy_h.Candidata", new=fake_model), \
         patch("core.handlers.candidatas_list_handlers.load_only", side_effect=lambda *a: a), \
         patch("core.handlers.candidatas_list_handlers.legacy_h.iso_utc_z", return_value="2026-01-02T00:00:00Z"):
        resp = client.get("/candidatas_db", follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data["meta"]["max_rows"] == 1500
    assert data["meta"]["returned"] == 1
    assert query.limit_seen == 1500
    cand = data["candidatas"][0]
    assert cand["fila"] == 7
    assert cand["marca_temporal"] == "2026-01-02T00:00:00Z"
    assert cand["codigo"] == "C-007"
