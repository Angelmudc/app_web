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

    def desc(self):
        return self

    def __eq__(self, _other):
        return self

    def __gt__(self, _other):
        return self

    def __ge__(self, _other):
        return self

    def __le__(self, _other):
        return self


class _SearchQuery:
    def __init__(self, paginado):
        self.paginado = paginado
        self.paginate_seen = None
        self.filter_calls = []
        self.execution_options_seen = None

    def options(self, *_args, **_kwargs):
        return self

    def execution_options(self, **kwargs):
        self.execution_options_seen = kwargs
        return self

    def filter(self, *args, **kwargs):
        self.filter_calls.append((args, kwargs))
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def paginate(self, page, per_page, error_out):  # noqa: ARG002
        self.paginate_seen = {"page": page, "per_page": per_page, "error_out": error_out}
        return self.paginado


def _fake_solicitud_model(query_obj):
    return SimpleNamespace(
        query=query_obj,
        id=_Expr(),
        fecha_solicitud=_Expr(),
        codigo_solicitud=_Expr(),
        ciudad_sector=_Expr(),
        rutas_cercanas=_Expr(),
        modalidad_trabajo=_Expr(),
        modalidad=_Expr(),
        tipo_modalidad=_Expr(),
        edad_requerida=_Expr(),
        experiencia=_Expr(),
        horario=_Expr(),
        funciones=_Expr(),
        funciones_otro=_Expr(),
        adultos=_Expr(),
        ninos=_Expr(),
        edades_ninos=_Expr(),
        mascota=_Expr(),
        tipo_lugar=_Expr(),
        habitaciones=_Expr(),
        banos=_Expr(),
        dos_pisos=_Expr(),
        areas_comunes=_Expr(),
        area_otro=_Expr(),
        direccion=_Expr(),
        sueldo=_Expr(),
        pasaje_aporte=_Expr(),
        nota_cliente=_Expr(),
        last_copiado_at=_Expr(),
        estado=_Expr(),
    )


def test_secretarias_solicitudes_buscar_endpoint_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("secretarias_buscar_solicitudes") == "/secretarias/solicitudes/buscar"
            assert url_for("procesos_routes.secretarias_buscar_solicitudes") == "/secretarias/solicitudes/buscar"

    assert (
        flask_app.view_functions["secretarias_buscar_solicitudes"].__module__
        == "core.handlers.secretarias_solicitudes_handlers"
    )


def test_secretarias_solicitudes_buscar_render_base_y_order_text_contrato_visible():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    row = SimpleNamespace(
        id=55,
        codigo_solicitud="SOL-055",
        ciudad_sector="Santiago",
        rutas_cercanas="K",
        modalidad_trabajo="Con dormida L-V",
        modalidad="",
        tipo_modalidad="",
        edad_requerida="25-45",
        experiencia="Limpieza",
        horario="8am-5pm",
        funciones=["limpiar", "otro"],
        funciones_otro="Planchar",
        adultos=2,
        ninos="1",
        edades_ninos="4",
        mascota="Gato",
        tipo_lugar="Casa",
        habitaciones=4,
        banos=2.0,
        dos_pisos=True,
        areas_comunes=["Patio", "otro"],
        area_otro="Terraza",
        direccion="X",
        sueldo="17000",
        pasaje_aporte=False,
        nota_cliente="Con experiencia real",
        last_copiado_at=None,
        estado="activa",
        fecha_solicitud=object(),
    )
    paginado = SimpleNamespace(items=[row], page=1, pages=1, total=1)
    q = _SearchQuery(paginado)

    class _Form:
        funciones = SimpleNamespace(choices=[("limpiar", "Limpiar")])

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=_fake_solicitud_model(q)), \
         patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))), \
         patch("core.handlers.secretarias_solicitudes_handlers.load_only", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.and_", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.func", new=SimpleNamespace(length=lambda *_: _Expr(), trim=lambda *_: _Expr())), \
         patch("core.handlers.secretarias_solicitudes_handlers.AdminSolicitudForm", new=_Form), \
         patch("core.handlers.secretarias_solicitudes_handlers.format_rd_datetime", return_value="2026-03-26 10:00"), \
         patch("core.handlers.secretarias_solicitudes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/secretarias/solicitudes/buscar", follow_redirects=False)

    assert resp.status_code == 200
    assert q.paginate_seen == {"page": 1, "per_page": 20, "error_out": False}
    assert q.execution_options_seen == {"stream_results": True}
    assert captured["template"] == "secretarias_solicitudes_buscar.html"
    assert captured["ctx"]["total"] == 1
    assert captured["ctx"]["items"][0]["codigo_solicitud"] == "SOL-055"
    assert captured["ctx"]["items"][0]["fecha_solicitud"] == "2026-03-26 10:00"
    order_text = captured["ctx"]["items"][0]["order_text"]
    assert "Disponible ( SOL-055 )" in order_text
    assert "Con dormida 💤 L-V" in order_text
    assert "Funciones: Limpiar, Planchar" in order_text
    assert "Casa - 4 habitaciones, 2 baños, 2 pisos, Patio, Terraza" in order_text
    assert "Modalidad:" not in order_text
    assert "Hogar:" not in order_text


def test_secretarias_solicitudes_buscar_filtros_parametros_y_paginacion_links():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    captured = {}
    row = SimpleNamespace(
        id=11,
        codigo_solicitud="SOL-011",
        ciudad_sector="Santo Domingo",
        rutas_cercanas="A",
        modalidad_trabajo="Salida diaria",
        modalidad="",
        tipo_modalidad="",
        edad_requerida="20-30",
        experiencia="General",
        horario="9-6",
        funciones=[],
        funciones_otro="",
        adultos=1,
        ninos=None,
        edades_ninos="",
        mascota="",
        tipo_lugar="",
        habitaciones=None,
        banos=None,
        dos_pisos=False,
        areas_comunes=[],
        area_otro="",
        direccion="",
        sueldo="15000",
        pasaje_aporte=True,
        nota_cliente="",
        last_copiado_at=object(),
        estado="reemplazo",
        fecha_solicitud=None,
    )
    paginado = SimpleNamespace(items=[row], page=2, pages=3, total=51)
    q = _SearchQuery(paginado)

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=_fake_solicitud_model(q)), \
         patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))), \
         patch("core.handlers.secretarias_solicitudes_handlers.load_only", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.or_", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.and_", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.func", new=SimpleNamespace(length=lambda *_: _Expr(), trim=lambda *_: _Expr())), \
         patch("core.handlers.secretarias_solicitudes_handlers.render_template", side_effect=_fake_render):
        resp = client.get(
            "/secretarias/solicitudes/buscar"
            "?q=santo&estado=activa&modalidad=dormida&mascota=si&con_ninos=no"
            "&desde=2026-03-01&hasta=2026-03-31&page=2&per_page=500",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert captured["template"] == "secretarias_solicitudes_buscar.html"
    assert q.paginate_seen == {"page": 2, "per_page": 100, "error_out": False}
    assert len(q.filter_calls) >= 5
    assert captured["ctx"]["q"] == "santo"
    assert captured["ctx"]["estado"] == "activa"
    assert captured["ctx"]["modalidad"] == "dormida"
    assert captured["ctx"]["mascota"] == "si"
    assert captured["ctx"]["con_ninos"] == "no"
    assert captured["ctx"]["desde"] == "2026-03-01"
    assert captured["ctx"]["hasta"] == "2026-03-31"
    assert captured["ctx"]["pages"] == 3
    assert captured["ctx"]["page"] == 2
    assert "page=1" in (captured["ctx"]["prev_url"] or "")
    assert "page=3" in (captured["ctx"]["next_url"] or "")
    assert "q=santo" in (captured["ctx"]["prev_url"] or "")
    assert captured["ctx"]["page_links"][1]["active"] is True
