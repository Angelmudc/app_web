import json
import re
import subprocess
from pathlib import Path
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


def _official_message(kind, link):
    helper_path = Path(__file__).parents[1] / "static/js/core/client_public_form_message.js"
    function_name = (
        "buildClientPublicFormMessageExistente"
        if kind == "existing"
        else "buildClientPublicFormMessageNuevo"
    )
    script = (
        "global.window = {};\n"
        + helper_path.read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.{function_name}({json.dumps(link)})));\n"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


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
        codigo="CLI-042",
        nombre_completo="Ana Ejemplo",
        telefono="809-555-0101",
        email="ana@example.com",
        is_active=True,
    )
    class FakeColumn:
        def ilike(self, *_args, **_kwargs):
            return ("ilike",)

        def asc(self):
            return object()

        def __eq__(self, other):
            return ("eq", other)

    fake_column = FakeColumn()

    class FakeQuery:
        def filter(self, *expressions, **_kwargs):
            if any(expression == ("eq", "ana") for expression in expressions):
                return SimpleNamespace(first=lambda: None)
            return SimpleNamespace(
                order_by=lambda *_a, **_k: SimpleNamespace(
                    limit=lambda *_x, **_y: SimpleNamespace(all=lambda: [fake_client])
                )
            )

        def filter_by(self, **_kwargs):
            return SimpleNamespace(first=lambda: fake_client)

    monkeypatch.setattr(
        quick_routes,
        "Cliente",
        SimpleNamespace(
            query=FakeQuery(),
            codigo=fake_column,
            nombre_completo=fake_column,
            telefono=fake_column,
            email=fake_column,
        ),
    )
    monkeypatch.setattr(quick_routes, "func", SimpleNamespace(lower=lambda column: column))
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
    assert "Código: CLI-042" in html
    assert "809-555-0101" in html
    assert "notas_admin" not in html
    assert "historial" not in html.lower()
    response = client.post(f"/m/{SLUG}/existing/generate", data={"client_id": "42"})
    assert response.status_code == 200
    assert generated == [(42, {"created_by": "quick_form_sender:sender-test"})]
    assert "Compartir" in response.get_data(as_text=True)
    assert "Copiar mensaje" in response.get_data(as_text=True)
    assert "Mensaje copiado" in response.get_data(as_text=True)
    assert "navigator.share" in response.get_data(as_text=True)
    assert "text: message" in response.get_data(as_text=True)
    assert "client_public_form_message.js" in response.get_data(as_text=True)
    assert "buildClientPublicFormMessageExistente" in response.get_data(as_text=True)
    assert f'href="/m/{SLUG}/"' in response.get_data(as_text=True)
    assert "Volver al inicio" in response.get_data(as_text=True)
    assert "Generar otro" not in response.get_data(as_text=True)


def test_existing_search_prioritizes_exact_client_code(monkeypatch):
    _configure(monkeypatch)
    exact = SimpleNamespace(
        id=42,
        codigo="CLI-042",
        nombre_completo="Cliente Exacto",
        telefono="809-555-0042",
        email="exacto@example.com",
        is_active=True,
    )
    ambiguous = SimpleNamespace(
        id=43,
        codigo="CLI-042-A",
        nombre_completo="Cliente Ambiguo",
        telefono="809-555-0043",
        email="ambiguo@example.com",
        is_active=True,
    )
    class FakeColumn:
        def ilike(self, *_args, **_kwargs):
            return ("ilike",)

        def asc(self):
            return object()

        def __eq__(self, other):
            return ("eq", other)

    column = FakeColumn()

    class FakeQuery:
        def filter(self, *expressions, **_kwargs):
            if any(expression == ("eq", "cli-042") for expression in expressions):
                return SimpleNamespace(first=lambda: exact)
            return SimpleNamespace(
                order_by=lambda *_a, **_k: SimpleNamespace(
                    limit=lambda *_x, **_y: SimpleNamespace(all=lambda: [ambiguous, exact])
                )
            )

        def filter_by(self, **_kwargs):
            return SimpleNamespace(first=lambda: exact)

    monkeypatch.setattr(
        quick_routes,
        "Cliente",
        SimpleNamespace(
            query=FakeQuery(),
            codigo=column,
            nombre_completo=column,
            telefono=column,
            email=column,
        ),
    )
    monkeypatch.setattr(quick_routes, "func", SimpleNamespace(lower=lambda column: column))
    monkeypatch.setattr(quick_routes, "or_", lambda *_args: object())
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    html = client.get(f"/m/{SLUG}/existing?q=CLI-042").get_data(as_text=True)
    assert html.index("Cliente Exacto") < html.index("Cliente Ambiguo")
    assert "Código: CLI-042" in html


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
    html = response.get_data(as_text=True)
    assert "Cliente nuevo" in html
    assert "Copiar mensaje" in html
    assert "Mensaje copiado" in html
    assert "navigator.share" in html
    assert "text: message" in html
    assert "client_public_form_message.js" in html
    assert "buildClientPublicFormMessageNuevo" in html


def test_quick_sender_messages_equal_official_messages_exactly():
    existing_link = "https://example.test/solicitud/EXACT-EXISTING"
    new_link = "https://example.test/solicitud/EXACT-NEW"

    official_existing = _official_message("existing", existing_link)
    quick_existing = _official_message("existing", existing_link)
    official_new = _official_message("new", new_link)
    quick_new = _official_message("new", new_link)

    assert quick_existing == official_existing
    assert quick_new == official_new
    assert quick_existing.splitlines()[4] == existing_link
    assert quick_new.splitlines()[4] == new_link
    main_existing = Path(__file__).parents[1] / "templates/admin/cliente_link_publico_solicitud.html"
    main_new = Path(__file__).parents[1] / "templates/admin/cliente_nuevo_link_publico_solicitud.html"
    assert "buildClientPublicFormMessageExistente" in main_existing.read_text(encoding="utf-8")
    assert "buildClientPublicFormMessageNuevo" in main_new.read_text(encoding="utf-8")


def test_new_generation_from_home_stays_on_home_and_rotates_links(monkeypatch):
    _configure(monkeypatch)
    links = iter(
        (
            "https://example.test/solicitud/A",
            "https://example.test/solicitud/B",
            "https://example.test/solicitud/C",
        )
    )
    monkeypatch.setattr(
        quick_routes,
        "generar_link_publico_compartible_cliente_nuevo",
        lambda **_kwargs: next(links),
    )
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    home = client.get(f"/m/{SLUG}/")
    home_html = home.get_data(as_text=True)
    assert home.status_code == 200
    assert f'href="/m/{SLUG}/new"' not in home_html
    assert f'action="/m/{SLUG}/new/generate"' in home_html
    assert "Generar y copiar mensaje" in home_html
    assert "fetch(form.action" in home_html
    assert "event.preventDefault()" in home_html
    assert "Mensaje copiado" in home_html
    assert "Generando..." in home_html
    assert "setTimeout" in home_html
    assert "generateButton.disabled = true" in home_html
    assert "navigator.share" not in home_html
    assert "Formulario listo" not in home_html
    assert "Copiar de nuevo" not in home_html
    assert "Generar otro" not in home_html
    assert "newClientResult" not in home_html
    assert "No pudimos generar el formulario." in home_html

    generated_links = []
    for expected in ("A", "B", "C"):
        response = client.post(f"/m/{SLUG}/new/generate")
        assert response.status_code == 200
        assert response.headers.get("Location") is None
        link = response.get_json()["link"]
        generated_links.append(link)
        assert link == f"https://example.test/solicitud/{expected}"
    assert generated_links == [
        "https://example.test/solicitud/A",
        "https://example.test/solicitud/B",
        "https://example.test/solicitud/C",
    ]
    assert len(set(generated_links)) == 3


def test_new_generation_from_home_returns_controlled_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        quick_routes,
        "generar_link_publico_compartible_cliente_nuevo",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("generator unavailable")),
    )
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    response = client.post(f"/m/{SLUG}/new/generate")
    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": "No pudimos generar el formulario."}


def test_new_generation_uses_a_new_url_in_each_message(monkeypatch):
    _configure(monkeypatch)
    links = iter(("https://example.test/solicitud/N1", "https://example.test/solicitud/N2"))
    monkeypatch.setattr(
        quick_routes,
        "generar_link_publico_compartible_cliente_nuevo",
        lambda **_kwargs: next(links),
    )
    client = flask_app.test_client()
    client.post(f"/m/{SLUG}/login", data={"username": USERNAME, "password": PASSWORD})
    first = client.post(f"/m/{SLUG}/new").get_data(as_text=True)
    second = client.post(f"/m/{SLUG}/new").get_data(as_text=True)
    assert "https://example.test/solicitud/N1" in first
    assert "https://example.test/solicitud/N1" not in second
    assert "https://example.test/solicitud/N2" in second


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
            for _ in range(3):
                clientes_routes.generar_link_publico_compartible_cliente(fake_client)
        with patch.object(clientes_routes, "create_public_share_alias", side_effect=new_alias), patch.object(
            clientes_routes, "_public_share_external_url", side_effect=lambda code: f"https://example.test/solicitud/{code}"
        ):
            for _ in range(3):
                clientes_routes.generar_link_publico_compartible_cliente_nuevo()

    assert len(set(existing_tokens)) == 3
    assert len(set(new_tokens)) == 3
