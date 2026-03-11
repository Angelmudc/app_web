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

    assert _login(client, "Owner", "8899").status_code in (302, 303)

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

    for usuario, clave in (("Owner", "8899"), ("Cruz", "8998"), ("Karla", "9989")):
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
        assert "/clientes/n/" in link_html
        assert "/clientes/solicitudes/nueva-publica/" not in link_html
        assert "Enlace legado (compatibilidad)" not in link_html
        assert 'id="linkPublicoNuevo"' in link_html
        assert "navigator.clipboard.writeText(text)" in link_html
        assert 'property="og:title"' in link_html
        assert 'property="og:description"' in link_html
        assert 'property="og:image"' in link_html
        assert 'name="twitter:card"' in link_html
        assert 'domestica-preview.png' in link_html


def test_admin_existing_client_public_link_view_shows_only_short_url_and_copy_matches():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "8899").status_code in (302, 303)

    fake_cliente = SimpleNamespace(id=99, nombre_completo="Cliente Demo", codigo="CL-0099")
    with flask_app.app_context():
        with patch.object(admin_routes.Cliente, "query", SimpleNamespace(get_or_404=lambda _cid: fake_cliente)), \
             patch("admin.routes.generar_token_publico_cliente", return_value="tok123"):
            resp = client.get("/admin/clientes/99/solicitudes/link-publico", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/clientes/f/tok123" in html
    assert "/clientes/solicitudes/publica/tok123" not in html
    assert "Enlace legado (compatibilidad)" not in html
    assert 'id="linkPublico"' in html
    assert "navigator.clipboard.writeText(text)" in html
