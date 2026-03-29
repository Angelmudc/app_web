# -*- coding: utf-8 -*-

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_admin(client):
    return client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)


class _Expr:
    def __eq__(self, _other):
        return self

    def is_(self, _other):
        return self

    def isnot(self, _other):
        return self

    def asc(self):
        return self

    def desc(self):
        return self


class _FuncRecorder:
    def __init__(self):
        self.calls = []

    def extract(self, part, _expr):
        self.calls.append(part)
        return _Expr()


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


class _FakeDataFrame:
    last_payload = None
    excel_called = False

    def __init__(self, payload):
        _FakeDataFrame.last_payload = payload

    def to_html(self, **_kwargs):
        return "<table>ok</table>"

    def to_excel(self, _writer, index=False, sheet_name="Reporte"):
        _FakeDataFrame.excel_called = bool((index is False) and (sheet_name == "Reporte"))


class _FakeExcelWriter:
    def __init__(self, output, engine=None):
        self.output = output
        self.engine = engine

    def __enter__(self):
        self.output.write(b"xlsx")
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_reporte_inscripciones_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("reporte_inscripciones") == "/reporte_inscripciones"
            assert url_for("procesos_routes.reporte_inscripciones") == "/reporte_inscripciones"

    assert flask_app.view_functions["reporte_inscripciones"].__module__ == "core.handlers.procesos_reportes_handlers"


def test_reporte_inscripciones_render_basico_paginado_y_filtros():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    captured = {}
    rows = [
        ("Ana", "Santo Domingo", "8090000001", "001", "C-001", "Web", True, 1200.0, date(2026, 1, 3)),
        ("Bea", "Santiago", "8090000002", "002", "C-002", "IG", True, 800.0, date(2026, 1, 2)),
        ("Carla", "La Vega", "8090000003", "003", "C-003", "TikTok", True, 600.0, date(2026, 1, 1)),
    ]
    query = _Query(rows)
    fake_db = SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: query))
    fake_candidata = SimpleNamespace(
        nombre_completo=_Expr(),
        direccion_completa=_Expr(),
        numero_telefono=_Expr(),
        cedula=_Expr(),
        codigo=_Expr(),
        medio_inscripcion=_Expr(),
        inscripcion=_Expr(),
        monto=_Expr(),
        fecha=_Expr(),
    )
    func_recorder = _FuncRecorder()

    def _fake_retry(callable_fn, retries=2, swallow=True):  # noqa: ARG001
        return callable_fn()

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_reportes_handlers.db", new=fake_db), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_reportes_handlers._retry_query", side_effect=_fake_retry), \
         patch("core.handlers.procesos_reportes_handlers.func", new=func_recorder), \
         patch("core.handlers.procesos_reportes_handlers.pd.DataFrame", side_effect=_FakeDataFrame), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
         patch("core.handlers.procesos_reportes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/reporte_inscripciones?mes=1&anio=2026&page=2&per_page=2&v=1", follow_redirects=False)

    assert resp.status_code == 200
    assert query.offset_seen == 2
    assert query.limit_seen == 2
    assert captured["template"] == "reporte_inscripciones.html"
    assert captured["ctx"]["mes"] == 1
    assert captured["ctx"]["anio"] == 2026
    assert captured["ctx"]["page"] == 2
    assert captured["ctx"]["per_page"] == 2
    assert captured["ctx"]["total"] == 3
    assert captured["ctx"]["total_pages"] == 2
    assert captured["ctx"]["reporte_html"] == "<table>ok</table>"
    assert func_recorder.calls.count("month") == 1
    assert func_recorder.calls.count("year") == 1
    assert _FakeDataFrame.last_payload[0]["Nombre"] == "Carla"


def test_reporte_inscripciones_descargar_excel_preserva_contrato():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    rows = [
        ("Ana", "Santo Domingo", "8090000001", "001", "C-001", "Web", True, 1200.0, date(2026, 1, 3)),
    ]
    query = _Query(rows)
    fake_db = SimpleNamespace(session=SimpleNamespace(query=lambda *_a, **_k: query))
    fake_candidata = SimpleNamespace(
        nombre_completo=_Expr(),
        direccion_completa=_Expr(),
        numero_telefono=_Expr(),
        cedula=_Expr(),
        codigo=_Expr(),
        medio_inscripcion=_Expr(),
        inscripcion=_Expr(),
        monto=_Expr(),
        fecha=_Expr(),
    )
    func_recorder = _FuncRecorder()
    send_kwargs = {}

    def _fake_retry(callable_fn, retries=2, swallow=True):  # noqa: ARG001
        return callable_fn()

    def _fake_send_file(output, **kwargs):
        send_kwargs.update(kwargs)
        send_kwargs["buffer_pos"] = output.tell()
        return "excel-ok"

    with patch("core.handlers.procesos_reportes_handlers.db", new=fake_db), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.Candidata", new=fake_candidata), \
         patch("core.handlers.procesos_reportes_handlers._retry_query", side_effect=_fake_retry), \
         patch("core.handlers.procesos_reportes_handlers.func", new=func_recorder), \
         patch("core.handlers.procesos_reportes_handlers.pd.DataFrame", side_effect=_FakeDataFrame), \
         patch("core.handlers.procesos_reportes_handlers.pd.ExcelWriter", side_effect=_FakeExcelWriter), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
         patch("core.handlers.procesos_reportes_handlers.send_file", side_effect=_fake_send_file):
        resp = client.get("/reporte_inscripciones?mes=1&anio=2026&descargar=1&v=2", follow_redirects=False)

    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "excel-ok"
    assert send_kwargs["as_attachment"] is True
    assert send_kwargs["download_name"] == "Reporte_Inscripciones_2026_01.xlsx"
    assert send_kwargs["mimetype"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert send_kwargs["buffer_pos"] == 0
    assert _FakeDataFrame.excel_called is True
    assert func_recorder.calls.count("month") == 1
    assert func_recorder.calls.count("year") == 1


def test_reporte_inscripciones_fallback_db_mantiene_mensaje_y_200():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.procesos_reportes_handlers._retry_query", return_value=None), \
         patch("core.handlers.procesos_reportes_handlers.legacy_h.rd_today", return_value=date(2026, 3, 27)), \
         patch("core.handlers.procesos_reportes_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/reporte_inscripciones?mes=1&anio=2026&v=3", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "reporte_inscripciones.html"
    assert captured["ctx"]["reporte_html"] == ""
    assert "No fue posible conectarse a la base de datos" in captured["ctx"]["mensaje"]


def test_reporte_inscripciones_parametro_mes_invalido_conserva_error():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    resp = client.get("/reporte_inscripciones?mes=13&anio=2026", follow_redirects=False)

    assert resp.status_code == 400
    assert "Parámetro 'mes' inválido." in resp.get_data(as_text=True)
