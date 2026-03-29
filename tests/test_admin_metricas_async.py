# -*- coding: utf-8 -*-

from unittest.mock import patch

import admin.routes as admin_routes
from app import app as flask_app


def _login(client, usuario="Cruz", clave="8998"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def test_metricas_dashboard_get_async_devuelve_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    fake_payload = {
        "secretarias": {"items": [{"username": "Ana KPI"}]},
        "solicitudes": {"tiempo_promedio_colocacion_horas": 7.5},
    }
    with patch.object(admin_routes, "metrics_dashboard", return_value=fake_payload):
        resp = client.get("/admin/metricas?period=30d", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#metricasDashboardAsyncRegion"
    assert "/admin/metricas" in (data.get("redirect_url") or "")
    assert "period=30d" in (data.get("redirect_url") or "")
    html = data.get("replace_html") or ""
    assert "Panel de métricas" in html
    assert "7.5" in html


def test_metricas_secretarias_get_async_devuelve_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    fake_payload = {
        "items": [
            {
                "username": "Ana KPI",
                "colocaciones": 2,
                "entrevistas": 4,
                "ediciones": 5,
                "solicitudes": 3,
                "tasa_exito": 66.67,
            }
        ]
    }
    with patch.object(admin_routes, "metrics_secretarias", return_value=fake_payload):
        resp = client.get("/admin/metricas/secretarias?period=today", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#metricasSecretariasAsyncRegion"
    assert "/admin/metricas/secretarias" in (data.get("redirect_url") or "")
    assert "period=today" in (data.get("redirect_url") or "")
    html = data.get("replace_html") or ""
    assert "Métricas por secretaria" in html
    assert "Ana KPI" in html


def test_metricas_solicitudes_get_async_devuelve_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    fake_payload = {
        "tiempo_promedio_colocacion_horas": 12.0,
        "ratio_exito_fallo": {"exitos": 10, "fallos": 2},
        "pendientes_por_estado": {"proceso": 4, "activa": 6},
    }
    with patch.object(admin_routes, "metrics_solicitudes", return_value=fake_payload):
        resp = client.get("/admin/metricas/solicitudes?period=7d", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#metricasSolicitudesAsyncRegion"
    assert "/admin/metricas/solicitudes" in (data.get("redirect_url") or "")
    assert "period=7d" in (data.get("redirect_url") or "")
    html = data.get("replace_html") or ""
    assert "Métricas de solicitudes" in html
    assert "proceso" in html
