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


def test_open_route_is_blocked_without_token():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/clientes/solicitudes/nueva-publica")
    assert resp.status_code == 404
    assert "requiere un enlace seguro".lower() in resp.get_data(as_text=True).lower()


def test_new_public_form_token_get_renders_personal_block_and_shared_request_body():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        resp = client.get("/clientes/solicitudes/nueva-publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Seccion 1 - Datos del cliente" in html
    assert "Nombre completo" in html
    assert "Correo electrónico / Gmail" in html
    assert "Número de teléfono" in html
    assert "Seccion 2 - Datos de la solicitud" in html
    assert "Ciudad / Sector" in html
    assert 'name="ciudad_sector"' in html
    assert "Requisitos y perfil" in html
    assert 'name="modalidad_grupo" value="con_dormida"' in html
    assert 'name="modalidad_grupo" value="con_salida_diaria"' in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:image"' in html
    assert 'name="twitter:card"' in html
    assert 'domestica-preview.png' in html


def test_new_public_post_keeps_cliente_city_sector_separate_from_solicitud_ciudad_sector():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    fake_form.ciudad_cliente.data = "Santiago"
    fake_form.sector_cliente.data = "Los Jardines"
    fake_form.ciudad_sector.data = "Puerto Plata / Centro"
    captured = {"cliente_ciudad": "", "cliente_sector": "", "solicitud_ciudad_sector": ""}

    def _capture_save(**kwargs):
        persist_fn = kwargs.get("persist_fn")
        freevars = list(getattr(persist_fn.__code__, "co_freevars", ()))
        cells = [cell.cell_contents for cell in (persist_fn.__closure__ or [])]
        env = dict(zip(freevars, cells))
        form = env.get("form")
        captured["cliente_ciudad"] = getattr(getattr(form, "ciudad_cliente", None), "data", "")
        captured["cliente_sector"] = getattr(getattr(form, "sector_cliente", None), "data", "")
        captured["solicitud_ciudad_sector"] = getattr(getattr(form, "ciudad_sector", None), "data", "")
        return RobustSaveResult(ok=False, attempts=1, error_message="forced")

    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", return_value=(None, "")), \
         patch("clientes.routes.execute_robust_save", side_effect=_capture_save):
        resp = client.post("/clientes/solicitudes/nueva-publica/tok123", data={"dummy": "1"}, follow_redirects=False)

    assert resp.status_code == 200
    assert captured["cliente_ciudad"] == "Santiago"
    assert captured["cliente_sector"] == "Los Jardines"
    assert captured["solicitud_ciudad_sector"] == "Puerto Plata / Centro"


def test_new_public_form_token_invalid_returns_controlled_response():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(False, "expired", {})):
        resp = client.get("/clientes/solicitudes/nueva-publica/tokX")

    assert resp.status_code == 410
    assert "Enlace expirado" in resp.get_data(as_text=True)


def test_next_cliente_codigo_publico_starts_from_2152():
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: [])):
            assert clientes_routes._next_cliente_codigo_publico() == "2,152"


def test_next_cliente_codigo_publico_continues_from_real_max_and_keeps_comma_format():
    rows = [("2,152",), ("2,153",), ("ABC-01",), ("1,999",)]
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: rows)):
            assert clientes_routes._next_cliente_codigo_publico() == "2,154"


def test_next_cliente_codigo_publico_respects_manual_higher_code_and_no_hole_fill():
    rows = [("2,150",), ("2,152",), ("2,500",)]
    with flask_app.app_context():
        with patch("clientes.routes.db.session.query", return_value=SimpleNamespace(all=lambda: rows)):
            assert clientes_routes._next_cliente_codigo_publico() == "2,501"


def test_new_public_form_token_post_success_invalidates_token_and_second_access_is_blocked():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    used_state = {"used": False}
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=88,
        used_at=None,
        solicitud=SimpleNamespace(codigo_solicitud="2,154-A"),
    )

    def _usage_side_effect(_token_hash):
        return used_row if used_state["used"] else None

    def _save_ok(*_args, **_kwargs):
        used_state["used"] = True
        return RobustSaveResult(ok=True, attempts=1, error_message="")

    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", side_effect=_usage_side_effect), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", return_value=(None, "")), \
         patch("clientes.routes.execute_robust_save", side_effect=_save_ok):
        resp = client.post("/clientes/solicitudes/nueva-publica/tok123", data={"dummy": "1"}, follow_redirects=False)
        second = client.get("/clientes/solicitudes/nueva-publica/tok123")

    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/nueva-publica/tok123" in (resp.location or "")
    assert second.status_code == 410
    assert "Este enlace ya fue utilizado" in second.get_data(as_text=True)


def test_new_public_success_page_shows_cliente_and_solicitud_confirmation_once():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    token = "tok123"
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=88,
        used_at=None,
        solicitud=SimpleNamespace(codigo_solicitud="2,154-A"),
    )

    with client.session_transaction() as sess:
        sess["public_new_solicitud_success"] = {
            "token_hash": clientes_routes._public_link_token_hash_storage(token),
            "cliente_nombre": "Cliente Nuevo",
            "cliente_codigo": "2,154",
            "solicitud_codigo": "2,154-A",
            "solicitud_id": 88,
        }

    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=used_row):
        success = client.get(f"/clientes/solicitudes/nueva-publica/{token}?estado=enviado")
        later = client.get(f"/clientes/solicitudes/nueva-publica/{token}")

    assert success.status_code == 200
    html = success.get_data(as_text=True)
    assert "Cliente Nuevo" in html
    assert "2,154-A" in html
    assert "Solicitud registrada correctamente." in html
    assert later.status_code == 410
    assert "Este enlace ya fue utilizado" in later.get_data(as_text=True)


def test_new_public_form_token_save_fail_keeps_token_valid():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", return_value=(None, "")), \
         patch("clientes.routes.execute_robust_save", return_value=RobustSaveResult(ok=False, attempts=1, error_message="forced")):
        post = client.post("/clientes/solicitudes/nueva-publica/tok123", data={"dummy": "1"}, follow_redirects=False)
        retry_get = client.get("/clientes/solicitudes/nueva-publica/tok123")

    assert post.status_code == 200
    assert "No se pudo enviar la solicitud en este momento" in post.get_data(as_text=True)
    assert retry_get.status_code == 200


def test_new_public_form_duplicate_email_or_phone_is_controlled():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    dup = SimpleNamespace(id=91, email="nuevo@example.com", telefono="809-123-4567")
    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", return_value=(dup, "email")):
        resp = client.post("/clientes/solicitudes/nueva-publica/tok123", data={"dummy": "1"}, follow_redirects=False)

    assert resp.status_code == 200
    assert "ya está registrado" in resp.get_data(as_text=True)


def test_existing_token_public_flow_still_available():
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
    assert "Formulario oficial de solicitud" in resp.get_data(as_text=True)


def test_new_public_short_route_renders_with_consistent_preview_metadata():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        resp = client.get("/clientes/n/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:image"' in html
    assert 'property="og:image:url"' in html
    assert 'property="og:image:secure_url"' in html
    assert 'property="og:image:type"' in html
    assert 'property="og:image:width"' in html
    assert 'property="og:image:height"' in html
    assert 'property="og:image:alt"' in html
    assert 'property="og:url"' in html
    assert 'name="twitter:card"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html
    assert 'name="twitter:image"' in html
    assert 'name="twitter:image:alt"' in html
    assert '/clientes/n/tok123' in html
    assert 'rel="canonical"' in html
    assert 'rel="image_src"' in html

    assert html.index('property="og:title"') < html.index("bootstrap@5.3.3")


def test_new_public_short_route_uses_configured_public_base_url_for_preview_metadata():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    old_base = flask_app.config.get("PUBLIC_BASE_URL")
    flask_app.config["PUBLIC_BASE_URL"] = "https://domestica.example.com"

    try:
        with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
             patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
             patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
            resp = client.get("/clientes/n/tok123")
    finally:
        flask_app.config["PUBLIC_BASE_URL"] = old_base

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'property="og:url" content="https://domestica.example.com/clientes/n/tok123"' in html
    assert 'rel="canonical" href="https://domestica.example.com/clientes/n/tok123"' in html
    assert 'property="og:image" content="https://domestica.example.com/static/img/domestica-preview.png?v=20260311"' in html


def test_new_public_short_route_success_redirect_stays_on_short_path():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_form = _FakeNewPublicForm()
    used_state = {"used": False}
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=88,
        used_at=None,
        solicitud=SimpleNamespace(codigo_solicitud="2,154-A"),
    )

    def _usage_side_effect(_token_hash):
        return used_row if used_state["used"] else None

    def _save_ok(*_args, **_kwargs):
        used_state["used"] = True
        return RobustSaveResult(ok=True, attempts=1, error_message="")

    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", side_effect=_usage_side_effect), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", return_value=(None, "")), \
         patch("clientes.routes.execute_robust_save", side_effect=_save_ok):
        resp = client.post("/clientes/n/tok123", data={"dummy": "1"}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert "/clientes/n/tok123" in (resp.location or "")


def test_share_continue_route_supports_new_client_flow_without_exposing_token_in_url():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    alias = SimpleNamespace(code="WXYZ5678JK", link_type="nuevo", token="tok123")
    with patch("clientes.routes.resolve_public_share_alias", return_value=alias), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        resp = client.get("/solicitud/WXYZ5678JK/continuar")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Seccion 1 - Datos del cliente" in html
    assert 'property="og:url" content="https://www.domesticadelcibao.com/solicitud/WXYZ5678JK"' in html
    assert 'rel="canonical" href="https://www.domesticadelcibao.com/solicitud/WXYZ5678JK"' in html
    assert "/clientes/n/tok123" not in html


def test_new_public_post_rerender_preserves_modalidad_otro_value_on_validation_error():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    modalidad_value = "Con dormida - Otro: Turno especial feriados"
    modalidad_norm = "Con dormida 💤 Turno especial feriados"

    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        post_resp = client.post(
            "/clientes/solicitudes/nueva-publica/tok123",
            data={
                "modalidad_trabajo": modalidad_value,
            },
        )

    assert post_resp.status_code == 200
    html = post_resp.get_data(as_text=True)
    assert modalidad_norm in html
    assert modalidad_value not in html
    assert 'id="modalidad_otro_text"' in html
