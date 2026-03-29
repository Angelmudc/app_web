# -*- coding: utf-8 -*-

import io
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def ilike(self, *_args, **_kwargs):
        return self

    def asc(self):
        return self


class _SearchQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_seen = None

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_seen = n
        return self

    def all(self):
        return list(self.rows)


class _DummyCandidata:
    fila = _Expr()
    nombre_completo = _Expr()
    cedula = _Expr()
    estado = _Expr()
    codigo = _Expr()

    def __init__(self, fila=1):
        self.fila = fila
        self.nombre_completo = "Ana Demo"
        self.codigo = "C-001"
        self.cedula = "001-0000000-1"
        self.estado = "inscrita"
        self.foto_perfil = b"foto-old"
        self.perfil = b"perfil-old"
        self.cedula1 = b"ced1-old"
        self.cedula2 = b"ced2-old"


def test_finalizar_proceso_endpoints_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("finalizar_proceso_buscar") == "/finalizar_proceso/buscar"
            assert url_for("procesos_routes.finalizar_proceso_buscar") == "/finalizar_proceso/buscar"
            assert url_for("finalizar_proceso") == "/finalizar_proceso"
            assert url_for("procesos_routes.finalizar_proceso") == "/finalizar_proceso"

    assert flask_app.view_functions["finalizar_proceso_buscar"].__module__ == "core.handlers.finalizar_proceso_handlers"
    assert flask_app.view_functions["finalizar_proceso"].__module__ == "core.handlers.finalizar_proceso_handlers"


def test_finalizar_proceso_buscar_flujo_basico_render_contrato():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    rows = [SimpleNamespace(fila=10, nombre_completo="Ana", cedula="001", estado="inscrita", codigo="C-10")]
    search_q = _SearchQuery(rows)
    fake_candidata_model = SimpleNamespace(
        fila=_Expr(),
        nombre_completo=_Expr(),
        cedula=_Expr(),
        estado=_Expr(),
        codigo=_Expr(),
        query=search_q,
    )

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.finalizar_proceso_handlers.legacy_h.Candidata", new=fake_candidata_model), \
         patch("core.handlers.finalizar_proceso_handlers.load_only", side_effect=lambda *a: a), \
         patch("core.handlers.finalizar_proceso_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.finalizar_proceso_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/finalizar_proceso/buscar?q=ana", follow_redirects=False)

    assert resp.status_code == 200
    assert search_q.limit_seen == 300
    assert captured["template"] == "finalizar_proceso_buscar.html"
    assert captured["ctx"]["q"] == "ana"
    assert captured["ctx"]["resultados"] == rows


def test_finalizar_proceso_redirect_fallback_sin_fila():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get("/finalizar_proceso", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/finalizar_proceso/buscar" in (resp.headers.get("Location") or "")


def test_finalizar_proceso_no_regresion_archivos_vacios_no_sobrescribe():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata(fila=1)
    before_foto = cand.foto_perfil
    before_ced1 = cand.cedula1
    before_ced2 = cand.cedula2

    class _Query:
        def get(self, _fila):
            return cand

    with patch("core.handlers.finalizar_proceso_handlers.legacy_h.Candidata", new=SimpleNamespace(query=_Query())), \
         patch("core.handlers.finalizar_proceso_handlers.execute_robust_save") as save_mock:
        resp = client.post(
            "/finalizar_proceso?fila=1",
            data={
                "foto_perfil": (io.BytesIO(b""), "foto.jpg"),
                "cedula1": (io.BytesIO(b""), "ced1.jpg"),
                "cedula2": (io.BytesIO(b""), "ced2.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert b"no pueden estar vac" in resp.data.lower()
    save_mock.assert_not_called()
    assert cand.foto_perfil == before_foto
    assert cand.cedula1 == before_ced1
    assert cand.cedula2 == before_ced2


def test_finalizar_proceso_post_exitoso_redirect_y_persistencia():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata(fila=22)

    class _Query:
        def get(self, _fila):
            return cand

    def _fake_execute_robust_save(session, persist_fn, verify_fn):  # noqa: ARG001
        persist_fn(1)
        return SimpleNamespace(ok=True, attempts=1, error_message="")

    with patch("core.handlers.finalizar_proceso_handlers.legacy_h.Candidata", new=SimpleNamespace(query=_Query())), \
         patch("core.handlers.finalizar_proceso_handlers.legacy_h._cfg_grupos_empleo", return_value=["Interna"]), \
         patch("core.handlers.finalizar_proceso_handlers.legacy_h._save_grupos_empleo_safe", return_value=True), \
         patch("core.handlers.finalizar_proceso_handlers.execute_robust_save", side_effect=_fake_execute_robust_save), \
         patch("core.handlers.finalizar_proceso_handlers.log_candidata_action") as log_mock, \
         patch("core.handlers.finalizar_proceso_handlers.maybe_update_estado_por_completitud"):
        resp = client.post(
            "/finalizar_proceso?fila=22",
            data={
                "grupos_empleo": ["Interna"],
                "foto_perfil": (io.BytesIO(b"foto-nueva"), "foto.jpg"),
                "cedula1": (io.BytesIO(b"ced1-nueva"), "ced1.jpg"),
                "cedula2": (io.BytesIO(b"ced2-nueva"), "ced2.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert "/candidata/perfil" in (resp.headers.get("Location") or "")
    assert cand.foto_perfil == b"foto-nueva"
    assert cand.cedula1 == b"ced1-nueva"
    assert cand.cedula2 == b"ced2-nueva"
    log_mock.assert_called_once()
