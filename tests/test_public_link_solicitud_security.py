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
        self.modalidad_trabajo = _FakeField("Con salida diaria - Lunes a Viernes")
        self.dos_pisos = _FakeField(False)
        self.pasaje_aporte = _FakeField(False)
        self.nota_cliente = _FakeField("")

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
    assert "Formulario oficial de solicitud" in html
    assert "Formulario oficial" in html
    assert "Enlace oficial de uso único" in html
    assert "Gmail / Email" not in html
    assert "Sin solicitudes registradas" not in html
    assert "Mis Solicitudes" not in html
    assert "/clientes/live/ping" not in html
    assert "modal_politicas" not in html
    assert "nota interna" not in html
    assert "809" not in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:title" content="Doméstica del Cibao A&amp;D — Formulario de Solicitud"' in html
    assert 'property="og:description" content="Formulario oficial para completar su solicitud."' in html
    assert 'property="og:image"' in html
    assert 'property="og:image:url"' in html
    assert 'property="og:image:secure_url"' in html
    assert 'property="og:image:type"' in html
    assert 'property="og:image:width"' in html
    assert 'property="og:image:height"' in html
    assert 'property="og:image:alt"' in html
    assert 'name="twitter:card"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html
    assert 'name="twitter:title" content="Doméstica del Cibao A&amp;D — Formulario de Solicitud"' in html
    assert 'name="twitter:description" content="Formulario oficial para completar su solicitud."' in html
    assert 'name="twitter:image"' in html
    assert 'name="twitter:image:alt"' in html
    assert 'property="og:url"' in html
    assert '/clientes/f/tok123' in html
    assert 'rel="canonical"' in html
    assert 'rel="image_src"' in html
    assert 'domestica-preview.png' in html
    assert 'content="image/png"' in html

    # Metadatos críticos primero, antes de CSS externo pesado.
    assert html.index('property="og:title"') < html.index("bootstrap@5.3.3")

    actions = [k.get("action_type") for _a, k in log_mock.call_args_list if isinstance(k, dict)]
    assert "PUBLIC_LINK_VIEW_OK" in actions


def test_public_preview_image_static_asset_is_public_and_direct():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    resp = client.get("/static/img/domestica-preview.png", follow_redirects=False)
    assert resp.status_code == 200
    assert not resp.location


def test_public_link_metadata_uses_configured_public_base_url():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    old_base = flask_app.config.get("PUBLIC_BASE_URL")
    flask_app.config["PUBLIC_BASE_URL"] = "https://domestica.example.com"

    c = _dummy_cliente()
    try:
        with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})), \
             patch("clientes.routes._public_link_usage_by_hash", return_value=None):
            resp = client.get("/clientes/f/tok123")
    finally:
        flask_app.config["PUBLIC_BASE_URL"] = old_base

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'property="og:url" content="https://domestica.example.com/clientes/f/tok123"' in html
    assert 'rel="canonical" href="https://domestica.example.com/clientes/f/tok123"' in html
    assert 'property="og:image" content="https://domestica.example.com/static/img/domestica-preview.png?v=20260311"' in html


def test_public_link_form_has_expected_field_order_and_structure_grouping():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    idx_edad = html.find("Edad del personal")
    idx_exp = html.find("Tipo de experiencia requerida")
    idx_func = html.find("Funciones a realizar al personal")
    assert -1 not in (idx_edad, idx_exp, idx_func)
    assert idx_edad < idx_exp < idx_func

    idx_tl = html.find("Tipo de lugar")
    idx_hab = html.find("Habitaciones")
    idx_banos = html.find("Baños")
    idx_pisos = html.find("Cantidad de pisos")
    idx_areas = html.find("Áreas comunes")
    idx_adultos = html.find("Cantidad de adultos")
    idx_ninos = html.find("Cantidad de niños")
    idx_edades = html.find("Edades de los niños")
    idx_mascota = html.find("Mascota")
    assert -1 not in (idx_tl, idx_hab, idx_banos, idx_pisos, idx_areas, idx_adultos, idx_ninos, idx_edades, idx_mascota)
    assert idx_tl < idx_hab < idx_banos < idx_pisos < idx_areas < idx_adultos < idx_ninos < idx_edades < idx_mascota


def test_public_link_form_renders_pasaje_three_options_and_otro_input():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Pasaje incluido" in html
    assert "Pasaje aparte" in html
    assert "Otro" in html
    assert 'name="pasaje_otro_text"' in html


def test_public_link_form_renders_guided_modalidad_with_two_main_groups():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="modalidad_grupo" value="con_dormida"' in html
    assert 'name="modalidad_grupo" value="con_salida_diaria"' in html
    assert "Con dormida 💤" in html
    assert "Salida diaria" in html
    assert '"con_salida_diaria"' in html
    assert '"con_dormida"' in html
    assert '"cd_quincenal"' in html
    assert '"sd_l_v"' in html
    assert 'id="modalidad_otro_text"' in html


def test_public_link_form_ui_does_not_show_optional_word_for_mascota_or_edades_ninos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Mascota (opcional)" not in html
    assert "Edades de los niños (opcional)" not in html


def test_public_link_form_modalidad_options_keep_prefixes_for_each_group():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        resp = client.get("/clientes/solicitudes/publica/tok123")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '"cd_l_v"' in html
    assert '"cd_l_s"' in html
    assert '"cd_fin_semana"' in html
    assert '"sd_1_dia"' in html
    assert '"sd_2_dias"' in html
    assert '"sd_3_dias"' in html
    assert '"sd_fin_semana"' in html
    assert '"Salida Quincenal, sale viernes despu\\u00e9s del medio d\\u00eda"' in html
    assert '"Lunes a s\\u00e1bado, sale s\\u00e1bado despu\\u00e9s del medio d\\u00eda"' in html


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


def test_public_link_post_blocks_when_cliente_reaches_active_business_limit():
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    fake_form = _FakePublicForm(token="tok123", codigo=c.codigo, nombre=c.nombre_completo, email=c.email)
    with patch("clientes.routes.SolicitudPublicaForm", return_value=fake_form), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})), \
         patch("clientes.routes.enforce_business_limit", return_value=(False, 1)), \
         patch("clientes.routes.enforce_min_human_interval", return_value=(False, 3)), \
         patch("clientes.routes._cliente_active_solicitudes_count", return_value=99):
        resp = client.post("/clientes/solicitudes/publica/tok123", data={"dummy": "1"}, follow_redirects=False)

    assert resp.status_code == 429
    assert "demasiadas solicitudes activas".lower() in resp.get_data(as_text=True).lower()


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


def test_public_link_short_route_keeps_same_validation_and_security_behavior():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c = _dummy_cliente()
    def _resolve_side_effect(token):
        if token == "tok123":
            return (c, "", {"legacy_token": False})
        return (None, "invalid", {})

    with patch("clientes.routes._resolve_public_link_token", side_effect=_resolve_side_effect), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        ok = client.get("/clientes/f/tok123")
        invalid = client.get("/clientes/f/token-invalido")

    assert ok.status_code == 200
    ok_html = ok.get_data(as_text=True)
    assert "Formulario oficial de solicitud" in ok_html
    assert '/clientes/f/tok123' in ok_html
    assert invalid.status_code == 404


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
    success_html = success.get_data(as_text=True)
    assert "Cliente Uno" in success_html
    assert "CL-001-C" in success_html
    assert "la solicitud quedó registrada correctamente" in success_html
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

    assert success_resp.status_code in (302, 303)
    assert "/clientes/solicitudes/91/recomendaciones" in (success_resp.location or "")

    for resp in (form_resp, used_resp, invalid_resp):
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
    assert "https://wa.me/18094296892" in html
    assert "+1 809 429 6892" in html
    assert "/clientes/live/ping" not in html


def test_share_landing_route_is_corporate_and_does_not_expose_long_token():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    alias = SimpleNamespace(code="ABCD2345EF", link_type="existente", token="tok123")
    c = _dummy_cliente()
    with patch("clientes.routes.resolve_public_share_alias", return_value=alias), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})):
        resp = client.get("/solicitud/ABCD2345EF")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Formulario oficial de solicitud" in html
    assert "Enlace válido para este proceso" in html
    assert "/solicitud/ABCD2345EF/continuar" in html
    assert "tok123" not in html
    assert 'property="og:title"' in html
    assert 'property="og:url" content="https://www.domesticadelcibao.com/solicitud/ABCD2345EF"' in html


def test_share_continue_existing_route_uses_alias_canonical_url_and_existing_security():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    alias = SimpleNamespace(code="ABCD2345EF", link_type="existente", token="tok123")
    c = _dummy_cliente()
    with patch("clientes.routes.resolve_public_share_alias", return_value=alias), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {"legacy_token": False})):
        resp = client.get("/solicitud/ABCD2345EF/continuar")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Formulario oficial de solicitud" in html
    assert 'property="og:url" content="https://www.domesticadelcibao.com/solicitud/ABCD2345EF"' in html
    assert 'rel="canonical" href="https://www.domesticadelcibao.com/solicitud/ABCD2345EF"' in html


def test_share_continue_route_blocks_used_or_invalid_aliases():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    alias = SimpleNamespace(code="ABCD2345EF", link_type="existente", token="tok123")
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=91,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-B"),
    )

    with patch("clientes.routes.resolve_public_share_alias", return_value=alias), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=used_row):
        used_resp = client.get("/solicitud/ABCD2345EF/continuar")

    with patch("clientes.routes.resolve_public_share_alias", return_value=None):
        invalid_resp = client.get("/solicitud/ZZZZ")

    assert used_resp.status_code == 410
    assert "Este enlace ya fue utilizado" in used_resp.get_data(as_text=True)
    assert invalid_resp.status_code == 404


def test_share_continue_route_shows_success_once_after_submit_then_used_for_existing_flow():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    token = "tok123"
    alias = SimpleNamespace(code="ABCD2345EF", link_type="existente", token=token)
    used_row = SimpleNamespace(
        cliente_id=7,
        solicitud_id=91,
        used_at=datetime.utcnow(),
        solicitud=SimpleNamespace(codigo_solicitud="CL-001-B"),
    )

    with client.session_transaction() as sess:
        sess["public_solicitud_success"] = {
            "token_hash": clientes_routes._public_link_token_hash_storage(token),
            "cliente_nombre": "Cliente Uno",
            "solicitud_codigo": "CL-001-B",
            "solicitud_id": 91,
        }

    with patch("clientes.routes.resolve_public_share_alias", return_value=alias), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=used_row):
        success = client.get("/solicitud/ABCD2345EF/continuar?estado=enviado")
        later = client.get("/solicitud/ABCD2345EF/continuar")

    assert success.status_code in (302, 303)
    assert "/clientes/solicitudes/91/recomendaciones" in (success.location or "")
    assert later.status_code == 410
    assert "Este enlace ya fue utilizado" in later.get_data(as_text=True)


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


def test_public_link_post_rerender_preserves_modalidad_value_on_validation_error():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()
    c = _dummy_cliente()
    modalidad_value = "Con dormida - Otro: Turno especial feriados"
    modalidad_norm = "Con dormida 💤 Turno especial feriados"

    with patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None):
        get_resp = client.get("/clientes/solicitudes/publica/tok123")
        csrf_token = _extract_csrf(get_resp.get_data(as_text=True))
        post_resp = client.post(
            "/clientes/solicitudes/publica/tok123",
            data={
                "csrf_token": csrf_token,
                "token": "tok123",
                "modalidad_trabajo": modalidad_value,
            },
        )

    assert post_resp.status_code == 200
    html = post_resp.get_data(as_text=True)
    assert modalidad_norm in html
    assert modalidad_value not in html


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


def test_public_link_pasaje_otro_mode_maps_without_breaking_legacy_storage():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    c = _dummy_cliente()
    fake_form = _FakePublicForm(token="tok123", codigo="CL-001", nombre="Cliente Uno", email="")
    captured = {"mode": "", "text": "", "response_status": 0}

    def _robust_save_capture(**kwargs):
        persist_fn = kwargs.get("persist_fn")
        freevars = list(getattr(persist_fn.__code__, "co_freevars", ()))
        values = []
        if persist_fn.__closure__:
            values = [cell.cell_contents for cell in persist_fn.__closure__]
        env = dict(zip(freevars, values))
        captured["mode"] = env.get("pasaje_mode", "")
        captured["text"] = env.get("pasaje_otro_text", "")
        return RobustSaveResult(ok=False, attempts=1, error_message="forced")

    with patch("clientes.routes.SolicitudPublicaForm", return_value=fake_form), \
         patch("clientes.routes._resolve_public_link_token", return_value=(c, "", {})), \
         patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
         patch("clientes.routes.execute_robust_save", side_effect=_robust_save_capture):
        resp = client.post(
            "/clientes/solicitudes/publica/tok123",
            data={
                "token": "tok123",
                "pasaje_mode": "otro",
                "pasaje_otro_text": "pasaje los sabados",
                "pisos_selector": "2",
            },
        )
        captured["response_status"] = resp.status_code

    assert captured["response_status"] == 200
    assert captured["mode"] == "otro"
    assert captured["text"] == "pasaje los sabados"
