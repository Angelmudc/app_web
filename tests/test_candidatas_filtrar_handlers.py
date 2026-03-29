# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def ilike(self, *_args, **_kwargs):
        return self

    def isnot(self, *_args, **_kwargs):
        return self

    def is_(self, *_args, **_kwargs):
        return self

    def asc(self):
        return self

    def __eq__(self, _other):
        return self


class _FilterQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_seen = None
        self.filter_calls = []

    def filter(self, *args, **kwargs):
        self.filter_calls.append((args, kwargs))
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        return list(self.rows)


def test_filtrar_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("filtrar") == "/filtrar"
            assert url_for("candidatas_routes.filtrar") == "/filtrar"

    assert flask_app.view_functions["filtrar"].__module__ == "core.handlers.candidatas_filtrar_handlers"


def test_filtrar_render_basico_sin_filtros():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    q = _FilterQuery([])
    fake_db = SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))
    fake_candidata = SimpleNamespace(
        nombre_completo=_Expr(),
        codigo=_Expr(),
        numero_telefono=_Expr(),
        direccion_completa=_Expr(),
        rutas_cercanas=_Expr(),
        cedula=_Expr(),
        modalidad_trabajo_preferida=_Expr(),
        anos_experiencia=_Expr(),
        estado=_Expr(),
        porciento=_Expr(),
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.candidatas_filtrar_handlers.db", new=fake_db), \
         patch("core.handlers.candidatas_filtrar_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.candidatas_filtrar_handlers.legacy_h.candidatas_activas_filter", return_value=True), \
         patch("core.handlers.candidatas_filtrar_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.candidatas_filtrar_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/filtrar", follow_redirects=False)

    assert resp.status_code == 200
    assert q.limit_seen == 500
    assert captured["template"] == "filtrar.html"
    assert captured["ctx"]["form_data"]["ciudad"] == ""
    assert captured["ctx"]["resultados"] == []
    assert captured["ctx"]["mensaje"] is None
    assert "trabajando" in captured["ctx"]["estados"]


def test_filtrar_aplica_params_y_normaliza_estado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    rows = [("Ana", "C-1", "809", "Santiago", "27", "001", "Dormida", "3 años o más", "lista_para_trabajar")]
    q = _FilterQuery(rows)
    fake_db = SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))
    fake_candidata = SimpleNamespace(
        nombre_completo=_Expr(),
        codigo=_Expr(),
        numero_telefono=_Expr(),
        direccion_completa=_Expr(),
        rutas_cercanas=_Expr(),
        cedula=_Expr(),
        modalidad_trabajo_preferida=_Expr(),
        anos_experiencia=_Expr(),
        areas_experiencia=_Expr(),
        estado=_Expr(),
        porciento=_Expr(),
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.candidatas_filtrar_handlers.db", new=fake_db), \
         patch("core.handlers.candidatas_filtrar_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.candidatas_filtrar_handlers.legacy_h.candidatas_activas_filter", return_value=True), \
         patch("core.handlers.candidatas_filtrar_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.candidatas_filtrar_handlers.render_template", side_effect=_fake_render):
        resp = client.post(
            "/filtrar",
            data={
                "ciudad": "Santiago",
                "rutas": "27",
                "modalidad": "Dormida",
                "experiencia_anos": "3 años o más",
                "areas_experiencia": "Limpieza",
                "estado": "lista para trabajar",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert q.limit_seen == 500
    assert captured["template"] == "filtrar.html"
    assert captured["ctx"]["form_data"]["estado"] == "lista para trabajar"
    assert captured["ctx"]["resultados"][0]["nombre"] == "Ana"
    assert captured["ctx"]["resultados"][0]["estado"] == "lista_para_trabajar"
