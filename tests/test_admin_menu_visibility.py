# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_owner_sees_create_user_link_and_can_open_route():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert b'href="/admin/usuarios"' in home.data
    assert "Usuarios y roles".encode("utf-8") in home.data
    assert "Registrar candidata".encode("utf-8") in home.data

    users_module = client.get("/admin/usuarios", follow_redirects=False)
    assert users_module.status_code == 200
    create_user = client.get("/admin/usuarios/nuevo", follow_redirects=False)
    assert create_user.status_code == 200


def test_admin_sees_register_candidate_link_and_can_open_route():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Registrar candidata".encode("utf-8") in home.data
    assert "Usuarios y roles".encode("utf-8") not in home.data

    reg_candidata = client.get("/registro_interno/", follow_redirects=False)
    assert reg_candidata.status_code == 200


def test_secretaria_does_not_see_admin_menu_links():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Karla", "9989").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Usuarios y roles".encode("utf-8") not in home.data
    assert "Registrar candidata".encode("utf-8") not in home.data

    # Seguridad existente: crear usuario es owner-only.
    denied = client.get("/admin/usuarios/nuevo", follow_redirects=False)
    assert denied.status_code == 403


def test_staff_roles_see_new_public_client_form_link_in_admin_clientes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998"), ("Karla", "9989")):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        resp = client.get("/home", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "/admin/solicitudes/nueva-publica/link" in html
        assert "Nueva solicitud pública (cliente nuevo)" in html
        link_page = client.get("/admin/solicitudes/nueva-publica/link", follow_redirects=False)
        assert link_page.status_code == 200
        link_html = link_page.get_data(as_text=True)
        assert "/solicitud/" in link_html
        assert "/clientes/solicitudes/nueva-publica/" not in link_html
        assert "Enlace legado (compatibilidad)" not in link_html
        assert 'id="linkPublicoNuevo"' in link_html
        assert "navigator.clipboard.writeText(value)" in link_html
        assert 'property="og:title"' in link_html
        assert 'property="og:description"' in link_html
        assert 'property="og:image"' in link_html
        assert 'name="twitter:card"' in link_html
        assert 'domestica-preview.png' in link_html
        assert "Este es el formulario de Doméstica del Cibao" in link_html
        assert "para registrar tu solicitud." in link_html
        assert "Ahí puedes colocar tus datos y lo que necesitas, para poder ayudarte mejor." in link_html
        assert "Cuando lo completes, envíame tu nombre y dime que ya terminaste." in link_html
        assert "Hola, gracias por comunicarte con Doméstica del Cibao A&D." not in link_html


def test_admin_existing_client_public_link_view_shows_only_short_url_and_copy_matches():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    fake_cliente = SimpleNamespace(id=99, nombre_completo="Cliente Demo", codigo="CL-0099")
    with flask_app.app_context():
        with patch.object(admin_routes.Cliente, "query", SimpleNamespace(get_or_404=lambda _cid: fake_cliente)), \
             patch("admin.routes.generar_link_publico_compartible_cliente", return_value="https://domestica.example.com/solicitud/ABCD2345"):
            resp = client.get("/admin/clientes/99/solicitudes/link-publico", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/solicitud/ABCD2345" in html
    assert "/clientes/solicitudes/publica/tok123" not in html
    assert "Enlace legado (compatibilidad)" not in html
    assert 'id="linkPublico"' in html
    assert "navigator.clipboard.writeText(value)" in html
    assert "Te comparto el formulario para registrar una nueva solicitud en Doméstica del Cibao A&amp;D." in html
    assert "Este enlace ya está asociado a tu perfil, por lo que solo necesitas completar los detalles del servicio que requieres." in html
    assert "Al finalizar, avísame para dar seguimiento a tu solicitud." in html
    assert "Hola, gracias por comunicarte con Doméstica del Cibao A&D." not in html


def test_admin_owner_json_link_publico_usa_public_base_url_y_no_localhost():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    old_public_base = flask_app.config.get("PUBLIC_BASE_URL")
    flask_app.config["PUBLIC_BASE_URL"] = "https://domestica.example.com"

    try:
        for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998")):
            client = flask_app.test_client()
            assert _login(client, usuario, clave).status_code in (302, 303)
            resp = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
            assert payload.get("ok") is True
            link = str(payload.get("link_publico") or "")
            assert link.startswith("https://domestica.example.com/")
            assert "localhost" not in link
            assert "/solicitud/" in link

        client_secretaria = flask_app.test_client()
        assert _login(client_secretaria, "Karla", "9989").status_code in (302, 303)
        denied = client_secretaria.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
        assert denied.status_code == 403
    finally:
        flask_app.config["PUBLIC_BASE_URL"] = old_public_base


def test_admin_link_json_rate_limit_blocks_excess_and_avoids_extra_token_generation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with flask_app.app_context():
        admin_routes.cache.clear()

    with patch(
        "admin.routes.generar_link_publico_compartible_cliente_nuevo",
        side_effect=[
            "https://domestica.example.com/solicitud/A1",
            "https://domestica.example.com/solicitud/A2",
            "https://domestica.example.com/solicitud/A3",
            "https://domestica.example.com/solicitud/A4",
        ],
    ) as gen_mock:
        r1 = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
        r2 = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
        r3 = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
        r4 = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert r4.status_code == 429
    payload = r4.get_json() or {}
    assert payload.get("ok") is False
    assert payload.get("error") == "rate_limited"
    assert "Has generado varios enlaces recientemente" in str(payload.get("message") or "")
    assert gen_mock.call_count == 3


def test_admin_and_owner_can_generate_inside_limit_and_secretaria_stays_forbidden():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        admin_routes.cache.clear()

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998")):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        resp = client.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
        assert resp.status_code == 200
        payload = resp.get_json() or {}
        assert payload.get("ok") is True
        assert "/solicitud/" in str(payload.get("link_publico") or "")

    client_secretaria = flask_app.test_client()
    assert _login(client_secretaria, "Karla", "9989").status_code in (302, 303)
    denied = client_secretaria.get("/admin/solicitudes/nueva-publica/link.json", follow_redirects=False)
    assert denied.status_code == 403
