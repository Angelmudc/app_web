# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def in_(self, *_args, **_kwargs):
        return self

    def is_(self, *_args, **_kwargs):
        return self

    def desc(self):
        return self

    def __lt__(self, _other):
        return self


class _JoinedLoad:
    def joinedload(self, *_args, **_kwargs):
        return self


class _GetQuery:
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


class _PostQuery:
    def __init__(self, row):
        self.row = row
        self.last_id = None

    def get_or_404(self, row_id):
        self.last_id = row_id
        return self.row


def test_secretarias_solicitudes_copiar_endpoint_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("secretarias_copiar_solicitudes") == "/secretarias/solicitudes/copiar"
            assert url_for("procesos_routes.secretarias_copiar_solicitudes") == "/secretarias/solicitudes/copiar"
            assert url_for("secretarias_copiar_solicitud", id=10) == "/secretarias/solicitudes/10/copiar"
            assert url_for("procesos_routes.secretarias_copiar_solicitud", id=10) == "/secretarias/solicitudes/10/copiar"

    assert (
        flask_app.view_functions["secretarias_copiar_solicitudes"].__module__
        == "core.handlers.secretarias_solicitudes_handlers"
    )
    assert (
        flask_app.view_functions["secretarias_copiar_solicitud"].__module__
        == "core.handlers.secretarias_solicitudes_handlers"
    )


def test_secretarias_solicitudes_copiar_render_y_salida_visible_no_regresion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    row = SimpleNamespace(
        id=7,
        codigo_solicitud="SOL-007",
        ciudad_sector="Santiago",
        rutas_cercanas="27",
        modalidad_trabajo="Con dormida L-V",
        modalidad="",
        tipo_modalidad="",
        edad_requerida="30-45",
        experiencia="Cuidado de ninos",
        horario="8am-5pm",
        funciones=["cocinar", "otro"],
        funciones_otro="Planchar",
        adultos=2,
        ninos="2",
        edades_ninos="5 y 8",
        mascota="Perro",
        tipo_lugar="Apartamento",
        habitaciones=3,
        banos=2.0,
        dos_pisos=True,
        areas_comunes=["Patio", "otro"],
        area_otro="Terraza",
        nota_cliente="Solo con referencias",
        sueldo="18000",
        pasaje_aporte=False,
    )
    get_q = _GetQuery([row])
    fake_solicitud = SimpleNamespace(
        query=get_q,
        reemplazos=_Expr(),
        estado=_Expr(),
        last_copiado_at=_Expr(),
        fecha_solicitud=_Expr(),
    )
    fake_reemplazo = SimpleNamespace(candidata_new=_Expr())

    class _Form:
        funciones = SimpleNamespace(choices=[("cocinar", "Cocinar")])

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=fake_solicitud), \
         patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Reemplazo", new=fake_reemplazo), \
         patch("core.handlers.secretarias_solicitudes_handlers.joinedload", side_effect=lambda *_a, **_k: _JoinedLoad()), \
         patch("core.handlers.secretarias_solicitudes_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.rd_today", return_value="2026-03-26"), \
         patch("core.handlers.secretarias_solicitudes_handlers.AdminSolicitudForm", new=_Form), \
         patch("core.handlers.secretarias_solicitudes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/secretarias/solicitudes/copiar", follow_redirects=False)

    assert resp.status_code == 200
    assert get_q.limit_seen == 500
    assert captured["template"] == "secretarias_solicitudes_copiar.html"
    assert captured["ctx"]["endpoint"] == "secretarias_copiar_solicitudes"
    assert captured["ctx"]["q"] == ""
    assert captured["ctx"]["q_enabled"] is False

    salida = captured["ctx"]["solicitudes"][0]["order_text"]
    assert "Disponible ( SOL-007 )" in salida
    assert "Con dormida 💤 L-V" in salida
    assert "Funciones: Cocinar, Planchar" in salida
    assert "Apartamento - 3 habitaciones, 2 baños, 2 pisos, Patio, Terraza" in salida
    assert "Hogar:" not in salida
    assert "Modalidad:" not in salida


def test_secretarias_copiar_solicitud_post_ok_redirect_y_ajax():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(codigo_solicitud="SOL-010", last_copiado_at=None)
    post_q = _PostQuery(row)
    fake_solicitud = SimpleNamespace(query=post_q)
    fake_session = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=fake_solicitud), \
         patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=fake_session)):
        resp = client.post("/secretarias/solicitudes/10/copiar", follow_redirects=False)
        ajax_resp = client.post(
            "/secretarias/solicitudes/10/copiar",
            headers={"X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )

    assert post_q.last_id == 10
    assert resp.status_code in (302, 303)
    assert "/secretarias/solicitudes/copiar" in (resp.headers.get("Location") or "")
    assert ajax_resp.status_code == 200
    assert ajax_resp.get_json()["ok"] is True
    assert ajax_resp.get_json()["id"] == 10
    assert ajax_resp.get_json()["codigo"] == "SOL-010"


def test_secretarias_copiar_solicitud_fallback_error_redirect_y_ajax_500():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(codigo_solicitud="SOL-011", last_copiado_at=None)
    post_q = _PostQuery(row)
    fake_solicitud = SimpleNamespace(query=post_q)
    state = {"rolled_back": False}

    def _raise_commit():
        raise RuntimeError("db fail")

    def _rollback():
        state["rolled_back"] = True

    fake_session = SimpleNamespace(commit=_raise_commit, rollback=_rollback)

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=fake_solicitud), \
         patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=fake_session)):
        resp = client.post("/secretarias/solicitudes/11/copiar", follow_redirects=False)
        ajax_resp = client.post(
            "/secretarias/solicitudes/11/copiar",
            headers={"X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False,
        )

    assert state["rolled_back"] is True
    assert resp.status_code in (302, 303)
    assert "/secretarias/solicitudes/copiar" in (resp.headers.get("Location") or "")
    assert ajax_resp.status_code == 500
    assert ajax_resp.get_json()["ok"] is False
    assert "No se pudo marcar como copiada" in ajax_resp.get_json()["error"]
