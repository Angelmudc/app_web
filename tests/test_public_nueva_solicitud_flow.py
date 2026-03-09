# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from utils.robust_save import RobustSaveResult
import clientes.routes as clientes_routes


class _FakeField:
    def __init__(self, data=None):
        self.data = data
        self.id = "fake"
        self.name = "fake"
        self.label = SimpleNamespace(text="fake")
        self.errors = []
        self.type = "StringField"
        self.choices = []

    def __call__(self, *args, **kwargs):
        return ""


class _FakeNewPublicForm:
    def __init__(self):
        self.hp = _FakeField("")
        self.nombre_completo = _FakeField("Cliente Nuevo")
        self.email_contacto = _FakeField("nuevo@example.com")
        self.telefono_contacto = _FakeField("809-123-4567")
        self.ciudad_cliente = _FakeField("Santiago")
        self.sector_cliente = _FakeField("Los Jardines")
        self.ciudad_sector = _FakeField("")
        self.rutas_cercanas = _FakeField("Ruta K")
        self.modalidad_trabajo = _FakeField("Dormida")
        self.horario = _FakeField("L-V 8:00 a 17:00")
        self.edad_requerida = _FakeField(["26-35"])
        self.edad_requerida.choices = [("26-35", "26-35")]
        self.edad_otro = _FakeField("")
        self.experiencia = _FakeField("Experiencia en limpieza")
        self.funciones = _FakeField(["limpieza"])
        self.funciones_otro = _FakeField("")
        self.tipo_lugar = _FakeField("casa")
        self.tipo_lugar_otro = _FakeField("")
        self.habitaciones = _FakeField(3)
        self.banos = _FakeField(2)
        self.dos_pisos = _FakeField(False)
        self.areas_comunes = _FakeField(["sala"])
        self.areas_comunes.choices = [("sala", "Sala")]
        self.area_otro = _FakeField("")
        self.adultos = _FakeField(2)
        self.ninos = _FakeField(0)
        self.edades_ninos = _FakeField("")
        self.mascota = _FakeField("")
        self.sueldo = _FakeField("18000")
        self.pasaje_aporte = _FakeField(False)
        self.nota_cliente = _FakeField("")

    def validate_on_submit(self):
        return True

    def hidden_tag(self):
        return ""

    def populate_obj(self, obj):
        return obj

    def __iter__(self):
        return iter([])


def test_new_public_form_get_renders_personal_block_and_shared_request_body():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/clientes/solicitudes/nueva-publica")
    assert resp.status_code == 200

    html = resp.get_data(as_text=True)
    assert "Información del cliente" in html
    assert "Nombre completo" in html
    assert "Correo electrónico / Gmail" in html
    assert "Número de teléfono" in html
    assert "Ciudad" in html
    assert "Sector" in html
    assert "Informacion del servicio" in html
    assert "Requisitos y perfil" in html


def test_new_public_form_invalid_post_stays_controlled_without_500():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.post("/clientes/solicitudes/nueva-publica", data={}, follow_redirects=False)
    assert resp.status_code == 200
    assert "Revisa los campos marcados en rojo." in resp.get_data(as_text=True)


def test_next_cliente_codigo_publico_starts_from_2152():
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: [])):
            assert clientes_routes._next_cliente_codigo_publico() == "2,152"


def test_next_cliente_codigo_publico_continues_from_real_max_and_keeps_comma_format():
    rows = [("2,152",), ("2,153",), ("ABC-01",), ("1,999",)]
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: rows)):
            assert clientes_routes._next_cliente_codigo_publico() == "2,154"


def test_next_cliente_codigo_publico_respects_manual_higher_code():
    rows = [("2,152",), ("2,153",), ("2,500",)]
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: rows)):
            assert clientes_routes._next_cliente_codigo_publico() == "2,501"


def test_next_cliente_codigo_publico_does_not_fill_holes_and_does_not_use_row_count():
    # Hay hueco en 2,151 y pocas filas, pero el siguiente debe usar el mayor real.
    rows = [("2,150",), ("2,152",), ("3,001",)]
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: rows)):
            assert clientes_routes._next_cliente_codigo_publico() == "3,002"


def test_new_public_form_post_success_redirects_to_professional_success_view():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    fake_query = SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: None))
    fake_cliente_model = SimpleNamespace(query=fake_query, email="email_col")

    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes.Cliente", fake_cliente_model), \
         patch("clientes.routes.execute_robust_save", return_value=RobustSaveResult(ok=True, attempts=2, error_message="")):
        resp = client.post("/clientes/solicitudes/nueva-publica", data={"dummy": "1"}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.location.endswith("/clientes/solicitudes/nueva-publica/exito")


def test_new_public_success_page_uses_session_payload_once():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with client.session_transaction() as sess:
        sess["public_new_solicitud_success"] = {
            "cliente_codigo": "2,154",
            "solicitud_codigo": "2,154-A",
            "solicitud_id": 88,
        }

    ok = client.get("/clientes/solicitudes/nueva-publica/exito")
    assert ok.status_code == 200
    html = ok.get_data(as_text=True)
    assert "2,154" in html
    assert "2,154-A" in html

    second = client.get("/clientes/solicitudes/nueva-publica/exito", follow_redirects=False)
    assert second.status_code in (302, 303)
    assert second.location.endswith("/clientes/solicitudes/nueva-publica")


def test_token_public_flow_still_available_after_new_route_addition():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = SimpleNamespace(
        id=7,
        codigo="CL-001",
        nombre_completo="Cliente Uno",
        email="cliente@example.com",
        is_active=True,
        updated_at=None,
        total_solicitudes=0,
        fecha_ultima_solicitud=None,
        fecha_ultima_actividad=None,
    )
    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    assert "Formulario publico de solicitud" in resp.get_data(as_text=True)
