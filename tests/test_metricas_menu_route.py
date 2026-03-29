# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_metricas_endpoint_ok_for_admin_and_link_in_base():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    home_resp = client.get("/home", follow_redirects=False)
    assert home_resp.status_code == 200
    assert b'href="/admin/metricas"' in home_resp.data

    dashboard_resp = client.get("/admin/metricas", follow_redirects=False)
    assert dashboard_resp.status_code == 200
    assert "Panel de métricas".encode("utf-8") in dashboard_resp.data
    dashboard_html = dashboard_resp.get_data(as_text=True)
    assert 'id="metricasDashboardAsyncRegion"' in dashboard_html
    assert 'data-admin-async-form' in dashboard_html

    secretarias_resp = client.get("/admin/metricas/secretarias", follow_redirects=False)
    assert secretarias_resp.status_code == 200
    secretarias_html = secretarias_resp.get_data(as_text=True)
    assert 'id="metricasSecretariasAsyncRegion"' in secretarias_html
    assert 'data-admin-async-form' in secretarias_html

    solicitudes_resp = client.get("/admin/metricas/solicitudes", follow_redirects=False)
    assert solicitudes_resp.status_code == 200
    solicitudes_html = solicitudes_resp.get_data(as_text=True)
    assert 'id="metricasSolicitudesAsyncRegion"' in solicitudes_html
    assert 'data-admin-async-form' in solicitudes_html


def test_metricas_endpoint_forbidden_for_secretaria_and_no_menu_link():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Karla", "9989").status_code in (302, 303)

    home_resp = client.get("/home", follow_redirects=False)
    assert home_resp.status_code == 200
    assert b'href="/admin/metricas"' not in home_resp.data

    forbidden_resp = client.get("/admin/metricas", follow_redirects=False)
    assert forbidden_resp.status_code == 403
