# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for
from sqlalchemy.exc import OperationalError

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def __eq__(self, _other):
        return self

    def __ge__(self, _other):
        return self

    def __le__(self, _other):
        return self

    def desc(self):
        return self


class _FilterPaginationQuery:
    def __init__(self, paginado, count_values=None):
        self.paginado = paginado
        self.count_values = list(count_values or [])
        self.paginate_seen = None
        self.filter_calls = []

    def filter(self, *args, **kwargs):
        self.filter_calls.append((args, kwargs))
        return self

    def count(self):
        if self.count_values:
            return self.count_values.pop(0)
        return 0

    def order_by(self, *_args, **_kwargs):
        return self

    def paginate(self, page, per_page, error_out):  # noqa: ARG002
        self.paginate_seen = {"page": page, "per_page": per_page, "error_out": error_out}
        return self.paginado


def test_dashboard_procesos_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("dashboard_procesos") == "/dashboard_procesos"
            assert url_for("procesos_routes.dashboard_procesos") == "/dashboard_procesos"

    assert flask_app.view_functions["dashboard_procesos"].__module__ == "core.handlers.procesos_dashboard_handlers"


def test_dashboard_procesos_render_basico_filtros_y_paginacion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    paginado = SimpleNamespace(items=[SimpleNamespace(fila=1)], total=1, pages=1, page=1, per_page=50)
    q_filter = _FilterPaginationQuery(paginado, count_values=[10, 2])
    fake_candidata = SimpleNamespace(fecha_cambio_estado=_Expr(), estado=_Expr(), query=q_filter)
    fake_session = SimpleNamespace(
        query=lambda *_a, **_k: SimpleNamespace(group_by=lambda *_g, **_kw: SimpleNamespace(all=lambda: [("inscrita", 7)]))
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_dashboard_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_dashboard_handlers.db", new=SimpleNamespace(session=fake_session)), \
         patch("core.handlers.procesos_dashboard_handlers.rd_today", return_value="2026-03-26"), \
         patch("core.handlers.procesos_dashboard_handlers.cast", side_effect=lambda *a, **_k: _Expr()), \
         patch("core.handlers.procesos_dashboard_handlers.func", new=SimpleNamespace(count=lambda *_: _Expr())), \
         patch("core.handlers.procesos_dashboard_handlers.render_template", side_effect=_fake_render):
        resp = client.get(
            "/dashboard_procesos?estado=inscrita&desde=2026-03-01&hasta=2026-03-31&page=3&per_page=50",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert captured["template"] == "dashboard_procesos.html"
    assert captured["ctx"]["estado_filtro"] == "inscrita"
    assert captured["ctx"]["desde_str"] == "2026-03-01"
    assert captured["ctx"]["hasta_str"] == "2026-03-31"
    assert "trabajando" in captured["ctx"]["estados"]
    assert q_filter.paginate_seen == {"page": 3, "per_page": 50, "error_out": False}


def test_dashboard_procesos_fallback_db_mensaje_y_paginado_vacio():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    flashes = []
    fake_candidata = SimpleNamespace(query=SimpleNamespace(count=lambda: (_ for _ in ()).throw(OperationalError("x", {}, None))))

    def _fake_flash(msg, category="message"):
        flashes.append((category, msg))

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_dashboard_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_dashboard_handlers.flash", side_effect=_fake_flash), \
         patch("core.handlers.procesos_dashboard_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/dashboard_procesos", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "dashboard_procesos.html"
    assert captured["ctx"]["total"] == 0
    assert captured["ctx"]["entradas_hoy"] == 0
    assert captured["ctx"]["counts_por_estado"] == {}
    assert captured["ctx"]["candidatas"].items == []
    assert captured["ctx"]["candidatas"].pages == 0
    assert flashes and "No se pudo conectar a la base de datos" in flashes[0][1]
