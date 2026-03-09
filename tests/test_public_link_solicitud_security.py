# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
import re
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


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html or "")
    return m.group(1) if m else ""


def test_public_link_valid_token_get_200_no_cache_and_no_sensitive_exposure():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes.log_action") as log_mock:
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"
    assert resp.headers.get("Pragma") == "no-cache"
    assert resp.headers.get("Expires") == "0"

    html = resp.get_data(as_text=True)
    assert "Formulario publico de solicitud" in html
    assert "Sin solicitudes registradas" not in html
    assert "Mis Solicitudes" not in html
    assert "/clientes/live/ping" not in html
    assert "modal_politicas" not in html
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
    assert "Este enlace no es valido o ha expirado" in resp.get_data(as_text=True)

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
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
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
         patch("clientes.routes.execute_robust_save", side_effect=_save_ok):
        resp = client.post("/clientes/solicitudes/publica/tok123", data={"token": "tok123"}, follow_redirects=False)
        second = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/publica/tok123" in (resp.location or "")
    assert second.status_code == 410
    assert "Este enlace ya fue utilizado" in second.get_data(as_text=True)


def test_public_link_successful_save_shows_professional_success_page_once():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    fake_form = _FakePublicForm(token="tok123", codigo="CL-001", nombre="Cliente Uno", email="cliente@example.com")
    used_state = {"used": False}
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=102,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-C"),
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
         patch("clientes.routes.execute_robust_save", side_effect=_save_ok):
        success = client.post("/clientes/solicitudes/publica/tok123", data={"token": "tok123"}, follow_redirects=True)
        later = client.get("/clientes/solicitudes/publica/tok123")

    assert success.status_code == 200
    assert "Tu solicitud fue enviada correctamente" in success.get_data(as_text=True)
    assert later.status_code == 410
    assert "Este enlace ya fue utilizado" in later.get_data(as_text=True)


def test_public_link_public_views_do_not_include_private_shell_or_live_ping():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=91,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-B"),
    )

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        form_resp = client.get("/clientes/solicitudes/publica/tok123")

    with patch("clientes.routes._public_link_usage_by_hash", return_value=used_row):
        used_resp = client.get("/clientes/solicitudes/publica/tok123")

    with patch("clientes.routes._resolve_public_link_token", return_value=(None, "expired", {})):
        invalid_resp = client.get("/clientes/solicitudes/publica/tok123")

    with client.session_transaction() as sess:
        sess["public_solicitud_success"] = {
            "token_hash": clientes_routes._public_link_token_hash_storage("tok123"),
            "solicitud_id": 91,
        }
    with patch("clientes.routes._public_link_usage_by_hash", return_value=used_row):
        success_resp = client.get("/clientes/solicitudes/publica/tok123?estado=enviado")

    for resp in (form_resp, used_resp, invalid_resp, success_resp):
        assert resp.status_code in (200, 410)
        html = resp.get_data(as_text=True)
        assert "/clientes/live/ping" not in html
        assert "Mis Solicitudes" not in html
        assert "Domésticas disponibles" not in html
        assert "modal_politicas" not in html


def test_public_link_tampered_token_is_rejected():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/clientes/solicitudes/publica/tok123tampered")
    assert resp.status_code == 404
    assert "Este enlace no es valido o ha expirado" in resp.get_data(as_text=True)


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
    assert "/clientes/live/ping" not in html


def test_public_link_post_with_incomplete_form_shows_validation_and_not_bad_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        get_resp = client.get("/clientes/solicitudes/publica/tok123")
        csrf_token = _extract_csrf(get_resp.get_data(as_text=True))
        post_resp = client.post(
            "/clientes/solicitudes/publica/tok123",
            data={
                "csrf_token": csrf_token,
                "token": "tok123",
            },
        )

    assert get_resp.status_code == 200
    assert csrf_token
    assert post_resp.status_code == 200
    html = post_resp.get_data(as_text=True)
    assert "Revisa los campos marcados para continuar con el envío." in html
    assert "The CSRF tokens do not match." not in html
    assert "Bad Request" not in html


def test_public_link_post_with_invalid_csrf_returns_controlled_public_message():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.post(
            "/clientes/solicitudes/publica/tok123",
            data={
                "csrf_token": "csrf-invalido",
                "token": "tok123",
            },
        )

    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert "La sesion del formulario expiro" in html
    assert "Recarga este enlace e intenta enviar nuevamente la solicitud." in html
    assert "Bad Request" not in html
    assert "The CSRF tokens do not match." not in html
