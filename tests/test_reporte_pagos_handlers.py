# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def __gt__(self, _other):
        return self

    def asc(self):
        return self


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.offset_seen = None
        self.limit_seen = None

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def count(self):
        return len(self.rows)

    def offset(self, n):
        self.offset_seen = n
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        if self.offset_seen is None or self.limit_seen is None:
            return list(self.rows)
        return list(self.rows)[self.offset_seen : self.offset_seen + self.limit_seen]


def test_reporte_pagos_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("reporte_pagos") == "/reporte_pagos"
            assert url_for("procesos_routes.reporte_pagos") == "/reporte_pagos"

    assert flask_app.view_functions["reporte_pagos"].__module__ == "core.handlers.procesos_reportes_handlers"


def test_reporte_pagos_render_basico_y_paginacion_visible():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    rows = [
        ("Ana", "001", "C-001", 1500.0),
        ("Bea", "002", None, 800.0),
        ("Carla", "003", "C-003", 2500.0),
    ]
    query = _Query(rows)
    fake_db = SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: query))
    fake_candidata = SimpleNamespace(
        nombre_completo=_Expr(),
        cedula=_Expr(),
        codigo=_Expr(),
        porciento=_Expr(),
    )

    def _fake_retry(callable_fn, retries=2, swallow=True):  # noqa: ARG001
        return callable_fn()

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_reportes_handlers.db", new=fake_db), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_reportes_handlers._retry_query", side_effect=_fake_retry), \
         patch("core.handlers.procesos_reportes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/reporte_pagos?page=2&per_page=2&v=1", follow_redirects=False)

    assert resp.status_code == 200
    assert query.offset_seen == 2
    assert query.limit_seen == 2
    assert captured["template"] == "reporte_pagos.html"
    assert captured["ctx"]["page"] == 2
    assert captured["ctx"]["per_page"] == 2
    assert captured["ctx"]["total"] == 3
    assert captured["ctx"]["total_pages"] == 2
    assert len(captured["ctx"]["pagos_pendientes"]) == 1
    assert captured["ctx"]["pagos_pendientes"][0]["nombre"] == "Carla"
    assert captured["ctx"]["pagos_pendientes"][0]["codigo"] == "C-003"


def test_reporte_pagos_fallback_db_mantiene_mensaje_y_200():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_reportes_handlers._retry_query", return_value=None), \
         patch("core.handlers.procesos_reportes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/reporte_pagos?v=2", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "reporte_pagos.html"
    assert captured["ctx"]["pagos_pendientes"] == []
    assert "No fue posible conectarse a la base de datos" in captured["ctx"]["mensaje"]
