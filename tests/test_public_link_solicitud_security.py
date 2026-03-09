# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes
from utils.robust_save import RobustSaveResult


class _FakeField:
    def __init__(self, data=None):
        self.data = data
        self.id = "fake"
        self.name = "fake"
        self.label = SimpleNamespace(text="fake")
        self.errors = []
        self.type = "HiddenField"
        self.choices = []

    def __call__(self, *args, **kwargs):
        return ""


class _FakePublicForm:
    def __init__(self, *, token="tok123", codigo="CL-001", nombre="Cliente Uno", email="cliente@example.com"):
        self.token = _FakeField(token)
        self.codigo_cliente = _FakeField(codigo)
        self.nombre_cliente = _FakeField(nombre)
        self.email_cliente = _FakeField(email)
        self.hp = _FakeField("")
        self.areas_comunes = _FakeField([])
        self.funciones = _FakeField([])
        self.edad_requerida = _FakeField([])
        self.dos_pisos = _FakeField(False)
        self.pasaje_aporte = _FakeField(False)

    def validate_on_submit(self):
        return True

    def hidden_tag(self):
        return ""

    def __iter__(self):
        return iter([])


def _dummy_cliente(*, codigo="CL-001", nombre="Cliente Uno", email="cliente@example.com"):
    return SimpleNamespace(
        id=7,
        codigo=codigo,
        nombre_completo=nombre,
        email=email,
        is_active=True,
        updated_at=datetime.utcnow(),
        total_solicitudes=0,
        fecha_ultima_solicitud=None,
        fecha_ultima_actividad=None,
    )


def test_public_link_valid_token_get_200_no_cache_and_no_sensitive_exposure():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._latest_solicitud_publica_cliente", return_value=None), \
         patch("clientes.routes.log_action") as log_mock:
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"
    assert resp.headers.get("Pragma") == "no-cache"
    assert resp.headers.get("Expires") == "0"

    html = resp.get_data(as_text=True)
    assert "Solicitud del cliente" in html
    assert "nota interna" not in html
    assert "809" not in html

    actions = [k.get("action_type") for _a, k in log_mock.call_args_list if isinstance(k, dict)]
    assert "PUBLIC_LINK_VIEW_OK" in actions


def test_public_link_invalid_token_returns_controlled_response():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("clientes.routes.log_action") as log_mock:
        resp = client.get("/clientes/solicitudes/publica/token-invalido")

    assert resp.status_code == 404
    assert "No fue posible abrir esta solicitud" in resp.get_data(as_text=True)

    actions = [k.get("action_type") for _a, k in log_mock.call_args_list if isinstance(k, dict)]
    assert "PUBLIC_LINK_VIEW_FAIL" in actions


def test_public_link_is_invalidated_when_sensitive_client_data_changes():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        c = _dummy_cliente(email="uno@example.com")
        token = clientes_routes.generar_token_publico_cliente(c)
        c.email = "rotado@example.com"
        with patch("clientes.routes.Cliente.query", SimpleNamespace(filter_by=lambda **kwargs: SimpleNamespace(first=lambda: c))):
            resolved, reason, _meta = clientes_routes._resolve_public_link_token(token)

    assert resolved is None
    assert reason == "fingerprint_mismatch"


def test_public_link_expired_returns_410_page():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("clientes.routes._resolve_public_link_token", return_value=(None, "expired", {})):
        resp = client.get("/clientes/solicitudes/publica/fake")

    assert resp.status_code == 410
    assert "Enlace expirado" in resp.get_data(as_text=True)


def test_public_link_token_of_one_client_cannot_be_used_with_other_identity():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente(codigo="CL-001", nombre="Cliente Uno", email="uno@example.com")
    fake_form = _FakePublicForm(token="tok123", codigo="CL-999", nombre="Cliente Dos", email="dos@example.com")

    with patch("clientes.routes.SolicitudPublicaForm", return_value=fake_form), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._latest_solicitud_publica_cliente", return_value=None):
        resp = client.post("/clientes/solicitudes/publica/tok123", data={"token": "tok123"})

    assert resp.status_code == 403
    assert "no coincide" in resp.get_data(as_text=True).lower()


def test_public_link_post_does_not_show_false_success_when_save_fails():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    fake_form = _FakePublicForm(token="tok123", codigo="CL-001", nombre="Cliente Uno", email="cliente@example.com")

    with patch("clientes.routes.SolicitudPublicaForm", return_value=fake_form), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._latest_solicitud_publica_cliente", return_value=None), \
         patch("clientes.routes.execute_robust_save", return_value=RobustSaveResult(ok=False, attempts=1, error_message="forced")):
        resp = client.post("/clientes/solicitudes/publica/tok123", data={"token": "tok123"}, follow_redirects=False)
        retry_get = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    assert "No se pudo enviar la solicitud en este momento" in resp.get_data(as_text=True)
    assert retry_get.status_code == 200


def test_public_link_successful_save_invalidates_token_and_second_access_is_blocked():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    fake_form = _FakePublicForm(token="tok123", codigo="CL-001", nombre="Cliente Uno", email="cliente@example.com")
    used_state = {"used": False}
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=88,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-A"),
    )

    def _usage_side_effect(_token_hash):
        if used_state["used"]:
            return used_row
        return None

    def _save_ok(*args, **kwargs):
        used_state["used"] = True
        return RobustSaveResult(ok=True, attempts=1, error_message="")

    with patch("clientes.routes.SolicitudPublicaForm", return_value=fake_form), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", side_effect=_usage_side_effect), \
         patch("clientes.routes._latest_solicitud_publica_cliente", return_value=None), \
         patch("clientes.routes.execute_robust_save", side_effect=_save_ok):
        resp = client.post("/clientes/solicitudes/publica/tok123", data={"token": "tok123"}, follow_redirects=False)
        second = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/publica/tok123" in (resp.location or "")
    assert second.status_code == 410
    assert "Este enlace ya fue usado correctamente" in second.get_data(as_text=True)


def test_public_link_renders_without_previous_solicitud():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._latest_solicitud_publica_cliente", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    assert "Sin solicitudes registradas" in resp.get_data(as_text=True)


def test_public_link_tampered_token_is_rejected():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/clientes/solicitudes/publica/tok123tampered")
    assert resp.status_code == 404
    assert "No fue posible abrir esta solicitud" in resp.get_data(as_text=True)


def test_public_link_used_is_blocked_with_controlled_response():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=91,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-B"),
    )
    with patch("clientes.routes._public_link_usage_by_hash", return_value=used_row):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 410
    html = resp.get_data(as_text=True)
    assert "Enlace ya utilizado" in html
    assert "CL-001-B" in html
