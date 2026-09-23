import re
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app as flask_app
import clientes.routes as clientes_routes
from quick_form_sender import routes as quick_routes
from quick_form_sender.routes import SESSION_MAX_AGE


SLUG = "mobile-access-test-9f4d"
USERNAME = "sender-test"
PASSWORD = "Sender#12345"


def _configure(monkeypatch, *, enabled=True):
    names = {
        "QUICK_FORM_SENDER_SLUG": SLUG if enabled else "",
        "QUICK_FORM_SENDER_USERNAME": USERNAME if enabled else "",
        "QUICK_FORM_SENDER_PASSWORD_HASH": generate_password_hash(PASSWORD, method="pbkdf2:sha256") if enabled else "",
        "QUICK_FORM_SENDER_SESSION_VERSION": "test-v1",
    }
    for key, value in names.items():
        monkeypatch.setenv(key, value)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["SESSION_COOKIE_SECURE"] = False
    quick_routes._LOCAL_LOGIN_FAILURES.clear()
    try:
        quick_routes.cache.clear()
    except Exception:
        pass


def test_missing_or_wrong_slug_is_404_and_unconfigured_access_is_disabled(monkeypatch):
    _configure(monkeypatch)
    client = flask_app.test_client()
    assert client.get(f"/m/wrong/{''}").status_code == 404
    monkeypatch.delenv("QUICK_FORM_SENDER_SLUG")
    assert client.get(f"/m/{SLUG}/").status_code == 404


def test_login_is_dedicated_persistent_and_does_not_grant_admin(monkeypatch):
    _configure(monkeypatch)
    client = flask_app.test_client()
    page = client.get(f"/m/{SLUG}/")
    assert page.status_code == 302
    assert f"/m/{SLUG}/login" in page.headers["Location"]

    bad = client.post(
        f"/m/{SLUG}/login",
        data={"username": USERNAME, "password": "wrong"},
    )
    assert bad.status_code == 200
    assert "No pudimos validar tus credenciales" in bad.get_data(as_text=True)
    assert "2FA" not in bad.get_data(as_text=True)

    good = client.post(
        f"/m/{SLUG}/login",
        data={"username": USERNAME, "password": PASSWORD},
    )
    assert good.status_code == 302
    cookie = good.headers["Set-Cookie"]
    assert "quick_form_sender=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert f"Max-Age={SESSION_MAX_AGE}" in cookie
    assert client.get(f"/m/{SLUG}/").status_code == 200
    assert client.get("/admin/").status_code in {302, 303, 401, 403}


def test_invalid_password_is_rate_limited_after_five_attempts(monkeypatch):
    _configure(monkeypatch)
    client = flask_app.test_client()
    responses = [
        client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": "wrong"})
        for _ in range(6)
    ]
    assert all(response.status_code == 200 for response in responses[:5])
    assert responses[-1].status_code == 200
    assert "No pudimos validar tus credenciales" in responses[-1].get_data(as_text=True)


def test_csrf_is_required_when_enabled(monkeypatch):
    _configure(monkeypatch)
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        client = flask_app.test_client()
        response = client.post(
            f"/m/{SLUG}/login",
            data={"username": USERNAME, "password": PASSWORD},
        )
        assert response.status_code in {400, 302}
        assert "quick_form_sender=" not in response.headers.get("Set-Cookie", "")
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


def test_existing_search_exposes_only_minimal_identity_and_reuses_generator(monkeypatch):
    _configure(monkeypatch)
    generated = []
    fake_client = SimpleNamespace(
        id=42,
        nombre_completo="Ana Ejemplo",
        telefono="809-555-0101",
        email="ana@example.com",
        is_active=True,
    )
    fake_column = SimpleNamespace(ilike=lambda *_args, **_kwargs: object(), asc=lambda: object())
    fake_query = SimpleNamespace(
        filter=lambda *_args, **_kwargs: SimpleNamespace(
            order_by=lambda *_a, **_k: SimpleNamespace(limit=lambda *_x, **_y: SimpleNamespace(all=lambda: [fake_client]))
        ),
        filter_by=lambda **_kwargs: SimpleNamespace(first=lambda: fake_client),
    )
    monkeypatch.setattr(
        quick_routes,
        "Cliente",
        SimpleNamespace(
            query=fake_query,
            nombre_completo=fake_column,
            telefono=fake_column,
            email=fake_column,
        ),
    )
    monkeypatch.setattr(quick_routes, "or_", lambda *_args: object())
    monkeypatch.setattr(
        quick_routes,
        "generar_link_publico_compartible_cliente",
        lambda client, **kwargs: generated.append((client.id, kwargs)) or "https://example.test/solicitud/A",
    )
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    response = client.get(f"/m/{SLUG}/existing?q=Ana")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Ana Ejemplo" in html
    assert "809-555-0101" in html
    assert "notas_admin" not in html
    assert "historial" not in html.lower()
    response = client.post(f"/m/{SLUG}/existing/generate", data={"client_id": "42"})
    assert response.status_code == 200
    assert generated == [(42, {"created_by": "quick_form_sender:sender-test"})]
    assert "Compartir" in response.get_data(as_text=True)


def test_new_generation_reuses_official_generator(monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(
        quick_routes,
        "generar_link_publico_compartible_cliente_nuevo",
        lambda **kwargs: calls.append(kwargs) or "https://example.test/solicitud/N",
    )
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    response = client.post(f"/m/{SLUG}/new")
    assert response.status_code == 200
    assert calls == [{"created_by": "quick_form_sender:sender-test"}]
    assert "Cliente nuevo" in response.get_data(as_text=True)


def test_session_version_and_logout_invalidate_access(monkeypatch):
    _configure(monkeypatch)
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    assert client.get(f"/m/{SLUG}/").status_code == 200
    monkeypatch.setenv("QUICK_FORM_SENDER_SESSION_VERSION", "test-v2")
    assert client.get(f"/m/{SLUG}/").status_code == 302
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    logout = client.post(f"/m/{SLUG}/logout")
    assert logout.status_code == 302
    assert re.search(r"quick_form_sender=;", logout.headers.get("Set-Cookie", ""))
    assert client.get(f"/m/{SLUG}/").status_code == 302


def test_official_token_generators_issue_distinct_tokens_on_each_generation(monkeypatch):
    _configure(monkeypatch)
    existing_tokens = []
    new_tokens = []
    fake_client = SimpleNamespace(id=42, codigo="C-42")

    def existing_alias(*, token, **_kwargs):
        existing_tokens.append(token)
        return SimpleNamespace(code=f"E{len(existing_tokens):07d}")

    def new_alias(*, token, **_kwargs):
        new_tokens.append(token)
        return SimpleNamespace(code=f"N{len(new_tokens):07d}")

    with flask_app.app_context():
        with patch.object(clientes_routes, "create_public_share_alias", side_effect=existing_alias), patch.object(
            clientes_routes, "_public_share_external_url", side_effect=lambda code: f"https://example.test/solicitud/{code}"
        ), patch.object(clientes_routes, "_cliente_public_link_fingerprint", return_value="fp"):
            clientes_routes.generar_link_publico_compartible_cliente(fake_client)
            clientes_routes.generar_link_publico_compartible_cliente(fake_client)
        with patch.object(clientes_routes, "create_public_share_alias", side_effect=new_alias), patch.object(
            clientes_routes, "_public_share_external_url", side_effect=lambda code: f"https://example.test/solicitud/{code}"
        ):
            clientes_routes.generar_link_publico_compartible_cliente_nuevo()
            clientes_routes.generar_link_publico_compartible_cliente_nuevo()

    assert len(set(existing_tokens)) == 2
    assert len(set(new_tokens)) == 2
