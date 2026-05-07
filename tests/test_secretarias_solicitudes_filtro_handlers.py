# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app
from core.handlers import secretarias_solicitudes_handlers as handlers


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


class _Expr:
    def ilike(self, value):
        return ("ILIKE", value)

    def isnot(self, value):
        return ("ISNOT", value)

    def is_(self, value):
        return ("IS", value)

    def desc(self):
        return self

    def any(self, value):
        return ("ANY", value)

    def in_(self, values):
        return ("IN", values)

    def __eq__(self, other):
        return ("EQ", other)

    def __gt__(self, other):
        return ("GT", other)

    def __ge__(self, other):
        return ("GE", other)

    def __le__(self, other):
        return ("LE", other)


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

    def paginate(self, page, per_page, error_out):
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


def _fake_solicitud_model_legacy_sin_modalidad(query_obj):
    model = _fake_solicitud_model(query_obj)
    delattr(model, "modalidad")
    delattr(model, "tipo_modalidad")
    return model


def _patch_common(q, captured):
    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    return patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=_fake_solicitud_model(q)), \
        patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))), \
        patch("core.handlers.secretarias_solicitudes_handlers.load_only", side_effect=lambda *a: a), \
        patch("core.handlers.secretarias_solicitudes_handlers.or_", side_effect=lambda *a: ("OR", a)), \
        patch("core.handlers.secretarias_solicitudes_handlers.and_", side_effect=lambda *a: ("AND", a)), \
        patch("core.handlers.secretarias_solicitudes_handlers.cast", side_effect=lambda _expr, _typ: _Expr()), \
        patch("core.handlers.secretarias_solicitudes_handlers.func", new=SimpleNamespace(
            nullif=lambda a, b: ("NULLIF", a, b),
            length=lambda a: ("LENGTH", a),
            trim=lambda a: ("TRIM", a),
            array_to_string=lambda a, b: ("ARRAY_TO_STRING", a, b),
            replace=lambda a, b, c: ("REPLACE", a, b, c),
            lower=lambda a: _Expr(),
        )), \
        patch("core.handlers.secretarias_solicitudes_handlers.render_template", side_effect=_fake_render)


def test_secretarias_solicitudes_filtro_endpoint_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("secretarias_filtrar_solicitudes") == "/secretarias/solicitudes/filtro"
            assert url_for("procesos_routes.secretarias_filtrar_solicitudes") == "/secretarias/solicitudes/filtro"

    assert (
        flask_app.view_functions["secretarias_filtrar_solicitudes"].__module__
        == "core.handlers.secretarias_solicitudes_handlers"
    )


def test_estado_disponible_para_secretaria_no_incluye_cerradas_ni_espera_pago():
    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=SimpleNamespace(estado=_Expr())):
        expr = handlers._solicitud_disponible_para_secretaria_filter()
    txt = str(expr)
    assert "proceso" not in txt
    assert "activa" in txt
    assert "reemplazo" in txt
    assert "espera_pago" not in txt
    assert "pagada" not in txt
    assert "cancelada" not in txt


def test_secretarias_solicitudes_filtro_sin_filtros_no_devuelve_resultados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro", follow_redirects=False)

    assert resp.status_code == 200
    assert q.paginate_seen is None
    assert captured["ctx"]["items"] == []
    assert captured["ctx"]["empty_state_message"] == "Aplica filtros para ver resultados"


def test_secretarias_solicitudes_filtro_sueldo_rango_aplica_cast_y_limites():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(
        id=1, codigo_solicitud="SOL-1", ciudad_sector="Santiago", rutas_cercanas="K", modalidad_trabajo="Salida diaria",
        modalidad="", tipo_modalidad="", edad_requerida="", experiencia="", horario="", funciones=["limpieza"],
        funciones_otro="", adultos=1, ninos=None, edades_ninos="", mascota="", tipo_lugar="", habitaciones=1,
        banos=1.0, dos_pisos=False, areas_comunes=[], area_otro="", direccion="", sueldo="18000", pasaje_aporte=False,
        nota_cliente="", last_copiado_at=None, estado="activa", fecha_solicitud=None,
    )
    paginado = SimpleNamespace(items=[row], page=1, pages=1, total=1)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?sueldo_min=15000&sueldo_max=20000", follow_redirects=False)

    assert resp.status_code == 200
    assert q.paginate_seen == {"page": 1, "per_page": 20, "error_out": False}
    flat = str(q.filter_calls)
    assert "GE" in flat and "LE" in flat


def test_secretarias_solicitudes_filtro_funciones_multiselect_or():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?funciones=limpieza&funciones=cocinar", follow_redirects=False)

    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "ANY', 'limpieza'" in flat
    assert "ANY', 'cocinar'" in flat
    assert "OR" in flat


def test_secretarias_solicitudes_filtro_funciones_multiselect_acepta_array_brackets():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?funciones[]=limpieza&funciones[]=cocinar", follow_redirects=False)

    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "ANY', 'limpieza'" in flat
    assert "ANY', 'cocinar'" in flat


def test_secretarias_solicitudes_filtro_aplica_estado_disponible_como_filtro_base():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?pasaje=si", follow_redirects=False)

    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "IN" in flat
    assert "proceso" not in flat
    assert "activa" in flat
    assert "reemplazo" in flat


def test_secretarias_solicitudes_filtro_funcion_otro_activa_busqueda_por_funciones_otro():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[7], \
         patch("core.handlers.secretarias_solicitudes_handlers.func", new=SimpleNamespace(
             nullif=lambda a, b: ("NULLIF", a, b),
             length=lambda _a: _Expr(),
             trim=lambda a: ("TRIM", a),
             array_to_string=lambda a, b: ("ARRAY_TO_STRING", a, b),
             replace=lambda a, b, c: ("REPLACE", a, b, c),
             lower=lambda a: _Expr(),
         )):
        resp = client.get("/secretarias/solicitudes/filtro?funciones=otro", follow_redirects=False)

    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "ANY', 'otro'" in flat
    assert "ISNOT', None" in flat
    assert "GT', 0" in flat


def test_secretarias_solicitudes_filtro_async_devuelve_fragmento_y_preserva_querystring_en_paginacion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(
        id=1, codigo_solicitud="SOL-1", ciudad_sector="Santiago", rutas_cercanas="K", modalidad_trabajo="Salida diaria",
        modalidad="", tipo_modalidad="", edad_requerida="", experiencia="", horario="", funciones=["limpieza"],
        funciones_otro="", adultos=1, ninos=None, edades_ninos="", mascota="", tipo_lugar="", habitaciones=1,
        banos=1.0, dos_pisos=False, areas_comunes=[], area_otro="", direccion="", sueldo="18000", pasaje_aporte=False,
        nota_cliente="", last_copiado_at=None, estado="activa", fecha_solicitud=None,
    )
    paginado = SimpleNamespace(items=[row], page=2, pages=3, total=42)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get(
            "/secretarias/solicitudes/filtro?ciudad_sector=santiago&ruta=k&page=2",
            headers={"X-Requested-With": "XMLHttpRequest", "X-Admin-Async": "1"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert captured["template"] == "secretarias/_secretarias_solicitudes_results.html"
    assert captured["ctx"]["page"] == 2
    assert "ciudad_sector=santiago" in captured["ctx"]["page_links"][0]["url"]
    assert "ruta=k" in captured["ctx"]["page_links"][0]["url"]


def test_secretarias_solicitudes_filtro_fallback_html_completo_sin_headers_async():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?ciudad_sector=santiago", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "secretarias_solicitudes_buscar.html"


def test_secretarias_solicitudes_filtro_ui_funciones_incluye_codigos_esperados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get("/secretarias/solicitudes/filtro", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for code in ("limpieza", "cocinar", "lavar", "planchar", "ninos", "envejeciente", "otro"):
        assert f'value="{code}"' in html


def test_secretarias_solicitudes_filtro_tolera_modelo_legacy_sin_modalidad():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.secretarias_solicitudes_handlers.legacy_h.Solicitud", new=_fake_solicitud_model_legacy_sin_modalidad(q)), \
         patch("core.handlers.secretarias_solicitudes_handlers.db", new=SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: q))), \
         patch("core.handlers.secretarias_solicitudes_handlers.load_only", side_effect=lambda *a: a), \
         patch("core.handlers.secretarias_solicitudes_handlers.or_", side_effect=lambda *a: ("OR", a)), \
         patch("core.handlers.secretarias_solicitudes_handlers.and_", side_effect=lambda *a: ("AND", a)), \
         patch("core.handlers.secretarias_solicitudes_handlers.cast", side_effect=lambda _expr, _typ: _Expr()), \
         patch("core.handlers.secretarias_solicitudes_handlers.func", new=SimpleNamespace(
             nullif=lambda a, b: ("NULLIF", a, b),
             length=lambda a: ("LENGTH", a),
             trim=lambda a: ("TRIM", a),
             array_to_string=lambda a, b: ("ARRAY_TO_STRING", a, b),
             replace=lambda a, b, c: ("REPLACE", a, b, c),
             lower=lambda a: _Expr(),
         )), \
         patch("core.handlers.secretarias_solicitudes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/secretarias/solicitudes/filtro?pasaje=si", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "secretarias_solicitudes_buscar.html"


def test_secretarias_solicitudes_filtro_tipo_casa_solo_con_limpieza():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?tipo_casa=grande", follow_redirects=False)

    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "ANY', 'limpieza'" in flat
    assert "habitaciones" not in flat or "GE" in flat


def test_secretarias_solicitudes_filtro_modalidad_detecta_salida_y_dormida():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)

    q1 = _SearchQuery(paginado)
    c1 = {}
    p1 = _patch_common(q1, c1)
    with p1[0], p1[1], p1[2], p1[3], p1[4], p1[5], p1[6], p1[7]:
        resp1 = client.get("/secretarias/solicitudes/filtro?modalidad=salida_diaria", follow_redirects=False)
    assert resp1.status_code == 200
    assert "%salida diaria%" in str(q1.filter_calls)

    q2 = _SearchQuery(paginado)
    c2 = {}
    p2 = _patch_common(q2, c2)
    with p2[0], p2[1], p2[2], p2[3], p2[4], p2[5], p2[6], p2[7]:
        resp2 = client.get("/secretarias/solicitudes/filtro?modalidad=con_dormida", follow_redirects=False)
    assert resp2.status_code == 200
    flat2 = str(q2.filter_calls)
    assert "%con dormida%" in flat2 and "%dormida%" in flat2 and "%interna%" in flat2


def test_secretarias_solicitudes_filtro_paginacion_20_y_hook_copia():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(
        id=77, codigo_solicitud="SOL-077", ciudad_sector="Santiago", rutas_cercanas="A", modalidad_trabajo="Con dormida L-V",
        modalidad="", tipo_modalidad="", edad_requerida="", experiencia="", horario="", funciones=["limpieza"],
        funciones_otro="", adultos=1, ninos=None, edades_ninos="", mascota="", tipo_lugar="Casa", habitaciones=2,
        banos=2.0, dos_pisos=True, areas_comunes=[], area_otro="", direccion="", sueldo="19000", pasaje_aporte=True,
        nota_cliente="", last_copiado_at=None, estado="activa", fecha_solicitud=object(),
    )
    paginado = SimpleNamespace(items=[row], page=1, pages=1, total=1)
    q = _SearchQuery(paginado)
    captured = {}

    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
         patch("core.handlers.secretarias_solicitudes_handlers.format_rd_datetime", return_value="2026-05-05 08:00"):
        resp = client.get("/secretarias/solicitudes/filtro?ciudad_sector=santiago", follow_redirects=False)

    assert resp.status_code == 200
    assert q.paginate_seen == {"page": 1, "per_page": 20, "error_out": False}
    item = captured["ctx"]["items"][0]
    assert item["copy_action_endpoint"] == "secretarias_copiar_solicitud"
    assert "Disponible ( SOL-077 )" in item["order_text"]


def test_secretarias_solicitudes_filtro_edad_texto_parcial_aplica_ilike():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?edad_texto=mayor+de+35", follow_redirects=False)
    assert resp.status_code == 200
    assert q.paginate_seen == {"page": 1, "per_page": 20, "error_out": False}
    assert "%mayor de 35%" in str(q.filter_calls)


def test_secretarias_solicitudes_filtro_edad_rapida_aplica_patrones_equivalentes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?edad_rapida=30_40", follow_redirects=False)
    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "%30 a 40%" in flat
    assert "%30-40%" in flat


def test_secretarias_solicitudes_filtro_edad_combinada_con_ciudad():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get(
            "/secretarias/solicitudes/filtro?ciudad_sector=santiago&edad_texto=25+en+adelante",
            follow_redirects=False,
        )
    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "%santiago%" in flat
    assert "%25 en adelante%" in flat


def test_secretarias_solicitudes_filtro_edad_combinada_con_modalidad():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get(
            "/secretarias/solicitudes/filtro?modalidad=con_dormida&edad_rapida=45_plus",
            follow_redirects=False,
        )
    assert resp.status_code == 200
    flat = str(q.filter_calls)
    assert "%con dormida%" in flat
    assert "%45 en adelante%" in flat


def test_secretarias_solicitudes_filtro_sin_edad_se_mantiene_igual():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    paginado = SimpleNamespace(items=[], page=1, pages=1, total=0)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro", follow_redirects=False)
    assert resp.status_code == 200
    assert q.paginate_seen is None
    assert captured["ctx"]["empty_state_message"] == "Aplica filtros para ver resultados"


def test_secretarias_solicitudes_filtro_edad_null_vacio_no_rompe():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(
        id=88, codigo_solicitud="SOL-088", ciudad_sector="Santiago", rutas_cercanas="K", modalidad_trabajo="Salida diaria",
        modalidad="", tipo_modalidad="", edad_requerida=[], experiencia="", horario="", funciones=["limpieza"],
        funciones_otro="", adultos=1, ninos=None, edades_ninos="", mascota="", tipo_lugar="", habitaciones=1,
        banos=1.0, dos_pisos=False, areas_comunes=[], area_otro="", direccion="", sueldo="18000", pasaje_aporte=False,
        nota_cliente="", last_copiado_at=None, estado="activa", fecha_solicitud=None,
    )
    paginado = SimpleNamespace(items=[row], page=1, pages=1, total=1)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?edad_texto=30", follow_redirects=False)
    assert resp.status_code == 200
    assert captured["ctx"]["total"] == 1


def test_secretarias_solicitudes_filtro_edad_lista_no_rompe_texto_copiable():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    row = SimpleNamespace(
        id=89, codigo_solicitud="SOL-089", ciudad_sector="Santiago", rutas_cercanas="K", modalidad_trabajo="Con dormida",
        modalidad="", tipo_modalidad="", edad_requerida=["30 en adelante", "Mayor de 45"], experiencia="", horario="", funciones=["limpieza"],
        funciones_otro="", adultos=1, ninos=None, edades_ninos="", mascota="", tipo_lugar="", habitaciones=1,
        banos=1.0, dos_pisos=False, areas_comunes=[], area_otro="", direccion="", sueldo="18000", pasaje_aporte=False,
        nota_cliente="", last_copiado_at=None, estado="activa", fecha_solicitud=None,
    )
    paginado = SimpleNamespace(items=[row], page=1, pages=1, total=1)
    q = _SearchQuery(paginado)
    captured = {}
    patches = _patch_common(q, captured)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        resp = client.get("/secretarias/solicitudes/filtro?edad_texto=30", follow_redirects=False)
    assert resp.status_code == 200
    item = captured["ctx"]["items"][0]
    assert "Edad: 30 en adelante, Mayor de 45" in item["order_text"]
    assert item["copy_action_endpoint"] == "secretarias_copiar_solicitud"
