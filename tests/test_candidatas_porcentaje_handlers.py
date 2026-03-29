# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login(client, usuario: str = "Owner", clave: str = "admin123"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_candidatas_porcentaje_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("candidatas_porcentaje") == "/candidatas_porcentaje"
            assert url_for("candidatas_routes.candidatas_porcentaje") == "/candidatas_porcentaje"

    assert flask_app.view_functions["candidatas_porcentaje"].__module__ == "core.handlers.candidatas_porcentaje_handlers"


def test_candidatas_porcentaje_redirects_to_login_without_session_user():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with client.session_transaction() as sess:
        sess.clear()

    resp = client.get("/candidatas_porcentaje", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_candidatas_porcentaje_render_and_query_contract():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    sentinel_pag = SimpleNamespace(items=[SimpleNamespace(fila=11)], page=2)
    captured = {}

    class _QueryStub:
        def __init__(self):
            self.per_page_seen = None
            self.page_seen = None
            self.error_out_seen = None

        def with_entities(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def paginate(self, page, per_page, error_out):
            self.page_seen = page
            self.per_page_seen = per_page
            self.error_out_seen = error_out
            return sentinel_pag

    q = _QueryStub()
    class _Field:
        def label(self, *_args, **_kwargs):
            return self

        def isnot(self, *_args, **_kwargs):
            return self

        def __gt__(self, _other):
            return self

        def asc(self):
            return self

        def nullslast(self):
            return self

    class _FakeCandidata:
        fila = _Field()
        codigo = _Field()
        nombre_completo = _Field()
        numero_telefono = _Field()
        modalidad_trabajo_preferida = _Field()
        inicio = _Field()
        fecha_de_pago = _Field()
        monto_total = _Field()
        porciento = _Field()
        query = q

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.candidatas_porcentaje_handlers.Candidata", new=_FakeCandidata), \
         patch("core.handlers.candidatas_porcentaje_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/candidatas_porcentaje?page=2", follow_redirects=False)

    assert resp.status_code == 200
    assert q.page_seen == 2
    assert q.per_page_seen == 50
    assert q.error_out_seen is False
    assert captured["template"] == "candidatas_porcentaje.html"
    assert captured["ctx"]["pagination"] is sentinel_pag
    assert captured["ctx"]["candidatas"] == sentinel_pag.items
